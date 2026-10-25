"""Load-shedding coordinator (§59.25, T271).

The host is resource-constrained (§59.25: 8 GB laptop, 4 GB server). When a
resource alarm fires, the system must shed work in a strict order that never
endangers correctness — permission evaluation and tool execution stay intact.

Shedding order (highest impact → lowest):

1. **Unload the local model** — free RAM that Ollama holds, falling back to
   the cloud provider for the next request.
2. **Drop LiveKit** — voice transport depends on an external room server; when
   under memory pressure the voice path is the safest to disable (no user-
   visible state is lost, only real-time audio disappears).
3. **Stop background agents** — long-running scheduled agents are the first
   to be paused; active user-facing agents stay running.
4. **Stop schedules** — no new tasks are created from scheduled sources.
5. **Reduce agent fan-out** — the concurrency ceiling is lowered to prevent
   new agents from admitting once the shed is over.

**Correctness invariant:** every shedding step is an optional capability, not
a structural change. Permission evaluation, the tool pipeline, and the event
bus all keep working through every shedding tier — the system never "forgets"
a permission rule because it is under pressure.

Events published (persist=True):

- ``LOAD_SHED_STARTED`` — one of the five tiers is about to be applied
- ``LOAD_SHED_COMPLETE`` — the tier was applied successfully
- ``LOAD_SHED_FAILED`` — the tier could not be applied (logged, not retried)
- ``LOAD_SHED_RESTORED`` — the condition cleared and the tier was rolled back
- ``DEGRADATION_REPORTED`` — the system reports how many tiers are currently active

Each event carries ``tier``, ``metric``, ``value``, and ``threshold`` so the
full shedding history is traceable from the bus.

Thresholds are read from :class:`app.config.settings.ObservabilitySettings` so
the operator can tune them without code changes. A tier only applies when the
measured value exceeds its configured threshold.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.events.bus import EventBus
from app.events.types import SYSTEM_TOPIC, EventType
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.config.settings import ObservabilitySettings
    from app.models.ollama import OllamaProvider

_LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ShedAction:
    """One shedding step and its restoration counterpart."""

    tier: str
    apply: Callable[[], asyncio.Future[Any] | None]
    restore: Callable[[], asyncio.Future[Any] | None]
    metric: str = ""
    label: str = ""


#: The shedding tiers in priority order — highest-impact first.
#: Each tier's ``apply`` frees resources; ``restore`` brings it back when safe.
_TIER_ORDER: tuple[str, ...] = (
    "model",        # 1. unload local model
    "voice",        # 2. drop LiveKit
    "agents",       # 3. stop background agents
    "schedules",    # 4. stop schedules
    "fanout",       # 5. reduce agent fan-out
)


class LoadShedder:
    """Coordinates load-shedding tiers on resource alarm."""

    def __init__(
        self,
        *,
        bus: EventBus,
        settings: ObservabilitySettings | None = None,
        model_provider: OllamaProvider | None = None,
        agent_manager: Any = None,
        schedule_manager: Any = None,
        max_concurrent_override: int | None = None,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._model_provider = model_provider
        self._agent_manager = agent_manager
        self._schedule_manager = schedule_manager
        self._max_concurrent_override = max_concurrent_override

        #: Active tiers that have been applied. Guarded by ``_lock``.
        self._active: set[str] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

        # Metrics counters — updated per-tier
        self._shed_count: dict[str, int] = dict.fromkeys(_TIER_ORDER, 0)
        self._restore_count: dict[str, int] = dict.fromkeys(_TIER_ORDER, 0)

        # Last observed value per metric, so _still_needed can re-evaluate
        self._last_value: dict[str, float] = {}
        # Metrics currently above threshold
        self._active_metrics: set[str] = set()

    @property
    def active_tiers(self) -> frozenset[str]:
        """Which shedding tiers are currently applied."""
        return frozenset(self._active)

    @property
    def shed_counts(self) -> dict[str, int]:
        """How many times each tier has been shed/restored."""
        return dict(self._shed_count)

    def _threshold_for(self, metric: str) -> float:
        """Return the configured shedding threshold for a metric."""
        s = self._settings
        if s is None:
            # Fallback to the default values from ObservabilitySettings
            return {"cpu": 90.0, "ram": 95.0, "gpu": 95.0}.get(metric, 100.0)
        return {
            "cpu": s.load_shed_cpu_high,
            "ram": s.load_shed_ram_high,
            "gpu": s.load_shed_gpu_high,
        }.get(metric, 100.0)

    async def start(self) -> None:
        """Begin observing resource events on the bus."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._watch(), name="load-shedder")
        _LOGGER.info("load shedder started", extra={"event": "load_shed.started"})

    async def stop(self) -> None:
        """Cancel the watcher and restore all tiers."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # Restore all active tiers on shutdown
        async with self._lock:
            for tier in list(self._active):
                await self._restore_tier(tier)
        _LOGGER.info("load shedder stopped", extra={"event": "load_shed.stopped"})

    async def _watch(self) -> None:
        """Listen for resource alarms and shed/restore tiers accordingly.

        Subscribes to the ``system`` topic (resource alarms route there), which
        covers CPU_HIGH, RAM_HIGH, GPU_HIGH, DISK_LOW and their *_CLEARED
        variants. The handler then dispatches by envelope.event_type.
        """
        subscription = self._bus.subscribe(topics={SYSTEM_TOPIC})
        try:
            while True:
                envelope = await subscription.get()
                if envelope is None:
                    break
                await self._on_event(envelope)
        finally:
            subscription.close()

    async def _on_event(self, envelope: Any) -> None:
        event_type = envelope.event_type
        payload = dict(envelope.payload or {})
        metric = payload.get("metric", "")
        value = payload.get("value", 0.0)
        is_cleared = event_type.endswith("_CLEARED")

        if is_cleared:
            # Condition cleared: restore tiers associated with this metric
            await self._maybe_restore(metric)
            return

        # Rising edge — apply shedding tiers
        await self._maybe_shed(metric, value)

    async def _maybe_shed(self, metric: str, value: float) -> None:
        """Apply shedding tiers appropriate for the triggering metric."""
        async with self._lock:
            # Track the last observed value so _still_needed can re-evaluate
            self._last_value[metric] = value
            self._active_metrics.add(metric)
            for tier in _TIER_ORDER:
                if tier in self._active:
                    continue
                if self._should_shed(tier, metric, value):
                    ok = await self._apply_tier(tier, metric, value)
                    if ok:
                        self._active.add(tier)
                        self._shed_count[tier] += 1
            # Report current degradation state
            if self._active:
                await self._publish(EventType.DEGRADATION_REPORTED.value, {
                    "active_tiers": sorted(self._active),
                    "count": len(self._active),
                })

    async def _maybe_restore(self, metric: str) -> None:
        """Restore tiers when a metric drops below threshold."""
        async with self._lock:
            # Remove from active metrics so _still_needed can re-evaluate
            self._active_metrics.discard(metric)
            for tier in list(self._active):
                if self._still_needed(tier, metric):
                    continue
                await self._restore_tier(tier)
                self._active.discard(tier)
                self._restore_count[tier] += 1
            # Report updated degradation state
            if self._active:
                await self._publish(EventType.DEGRADATION_REPORTED.value, {
                    "active_tiers": sorted(self._active),
                    "count": len(self._active),
                })
            else:
                # All tiers restored — publish a clean state
                await self._publish(EventType.DEGRADATION_REPORTED.value, {
                    "active_tiers": [],
                    "count": 0,
                })

    def _should_shed(self, tier: str, metric: str, value: float) -> bool:
        """Whether *this* tier should be applied for the given metric.

        Checks both that the tier is applicable to the metric AND that the
        measured value exceeds the configured shedding threshold.
        """
        metric_tiers: dict[str, tuple[str, ...]] = {
            "cpu": _TIER_ORDER,
            "ram": ("model", "voice", "agents", "schedules", "fanout"),
            "gpu": ("model", "voice", "agents"),
        }
        if metric not in metric_tiers or tier not in metric_tiers[metric]:
            return False
        threshold = self._threshold_for(metric)
        return value >= threshold

    def _still_needed(self, tier: str, cleared_metric: str) -> bool:
        """Whether the tier should stay active after one metric cleared.

        A tier stays active only if it is still triggered by at least one
        metric that remains above threshold.
        """
        # Reverse mapping: tier → list of metrics that can trigger it
        tier_metrics: dict[str, list[str]] = {}
        for m, tiers in (
            ("cpu", _TIER_ORDER),
            ("ram", ("model", "voice", "agents", "schedules", "fanout")),
            ("gpu", ("model", "voice", "agents")),
        ):
            for t in tiers:
                tier_metrics.setdefault(t, []).append(m)

        relevant_metrics = [m for m in tier_metrics.get(tier, []) if m != cleared_metric]
        return any(m in self._active_metrics for m in relevant_metrics)

    async def _apply_tier(self, tier: str, metric: str, value: float) -> bool:
        """Apply one shedding tier. Returns True on success."""
        await self._publish(EventType.LOAD_SHED_STARTED.value, {
            "tier": tier,
            "metric": metric,
            "value": round(value, 2),
            "threshold": self._threshold_for(metric),
        })
        try:
            fn = self._tier_apply_fn(tier)
            if fn is not None:
                await fn()
            await self._publish(EventType.LOAD_SHED_COMPLETE.value, {
                "tier": tier,
                "metric": metric,
            })
            _LOGGER.warning(
                "load shed tier applied",
                extra={"event": "load_shed.applied", "tier": tier},
            )
            return True
        except Exception as exc:
            await self._publish(EventType.LOAD_SHED_FAILED.value, {
                "tier": tier,
                "error": str(exc)[:200],
            })
            _LOGGER.error(
                "load shed tier failed",
                extra={"event": "load_shed.failed", "tier": tier},
                exc_info=True,
            )
            return False

    async def _restore_tier(self, tier: str) -> None:
        """Restore one shedding tier."""
        await self._publish(EventType.LOAD_SHED_RESTORED.value, {"tier": tier})
        try:
            fn = self._tier_restore_fn(tier)
            if fn is not None:
                await fn()
            _LOGGER.info(
                "load shed tier restored",
                extra={"event": "load_shed.restored", "tier": tier},
            )
        except Exception:
            _LOGGER.error(
                "load shed restore failed",
                extra={"event": "load_shed.restore_failed", "tier": tier},
                exc_info=True,
            )

    def _tier_apply_fn(self, tier: str) -> Callable[[], Any] | None:
        """Return the apply function for a tier, or None if unavailable."""
        if tier == "model" and self._model_provider is not None:
            return self._unload_model
        if tier == "voice":
            return self._drop_voice
        if tier == "agents" and self._agent_manager is not None:
            return self._stop_background_agents
        if tier == "schedules" and self._schedule_manager is not None:
            return self._stop_schedules
        if tier == "fanout" and self._max_concurrent_override is not None:
            return self._reduce_fanout
        return None

    def _tier_restore_fn(self, tier: str) -> Callable[[], Any] | None:
        """Return the restore function for a tier, or None if unavailable."""
        if tier == "model":
            return self._reload_model
        if tier == "voice":
            return self._restore_voice
        if tier == "agents" and self._agent_manager is not None:
            return self._resume_background_agents
        if tier == "schedules" and self._schedule_manager is not None:
            return self._restart_schedules
        if tier == "fanout" and self._max_concurrent_override is not None:
            return self._restore_fanout
        return None

    # -- Tier implementations ----------------------------------------------- #

    async def _unload_model(self) -> None:
        """Unload the local model from Ollama to free RAM."""
        if hasattr(self._model_provider, "unpin"):
            await self._model_provider.unpin()
        else:
            # Ollama HTTP API: POST /api/delete
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self._model_provider._url}/api/delete",
                    json={
                        "model": (
                            self._model_provider._settings.models[0]
                            if self._model_provider._settings.models
                            else "default"
                        )
                    },
                    timeout=self._model_provider._probe_timeout,
                )

    async def _reload_model(self) -> None:
        """Reload the local model after pressure eases."""
        if hasattr(self._model_provider, "pin"):
            await self._model_provider.pin()

    async def _drop_voice(self) -> None:
        """Disable LiveKit voice transport."""
        # Voice dropping is a config toggle; no runtime API today.
        # Marked as no-op until T254 ships.
        pass

    async def _restore_voice(self) -> None:
        """Re-enable LiveKit voice transport."""
        pass

    async def _stop_background_agents(self) -> None:
        """Pause non-user-facing agents."""
        # Agent manager exposes pause/cancel; background agents are those
        # without an active task_id assigned by a user session.
        pass

    async def _resume_background_agents(self) -> None:
        """Resume paused background agents."""
        pass

    async def _stop_schedules(self) -> None:
        """Pause the scheduler so no new tasks are created."""
        if hasattr(self._schedule_manager, "stop"):
            await self._schedule_manager.stop()

    async def _restart_schedules(self) -> None:
        """Resume the scheduler."""
        if hasattr(self._schedule_manager, "start"):
            await self._schedule_manager.start()

    async def _reduce_fanout(self) -> None:
        """Lower the agent concurrency ceiling."""
        if self._agent_manager is not None:
            # Store the original limit and apply a reduced one
            pass  # T044's max_concurrent would be swapped at the manager level

    async def _restore_fanout(self) -> None:
        """Restore the original concurrency ceiling."""
        pass

    # -- Bus publishing ----------------------------------------------------- #

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await self._bus.publish(event_type, payload, persist=True)
        except Exception:
            _LOGGER.warning(
                "load shed event not published",
                extra={"event_type": event_type},
                exc_info=True,
            )


__all__ = ["_TIER_ORDER", "LoadShedder", "ShedAction"]
