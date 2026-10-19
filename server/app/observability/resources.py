"""Host resource monitor: CPU/RAM/disk/GPU sampling and threshold alarms (T182, spec A§32).

The thresholds and the sampling interval already existed in
``ObservabilitySettings`` (``cpu_high_percent``, ``ram_high_percent``,
``disk_low_percent``, ``gpu_high_percent``, ``resource_monitor_interval``) and
the alarm event types already existed in :class:`~app.events.types.EventType`
(``CPU_HIGH``, ``RAM_HIGH``, ``DISK_LOW``, ``GPU_HIGH``); nothing emitted them.
This is the missing producer: a poll loop that samples the host and publishes the
canonical events when a threshold is crossed.

**Alarms are edge-triggered, not level-triggered.** A machine over 85% CPU for an
hour would otherwise publish ``CPU_HIGH`` every 30 seconds and bury the event
stream. The monitor remembers which alarms are currently active and only
publishes on the rising edge; it publishes a recovery event when the condition
clears, so a subscriber can see both the onset and the end rather than inferring
the end from silence -- which A§60.4 explicitly warns against.

**GPU may be absent, and that is not a failure.** A headless server has no GPU;
``pynvml`` may not be installed. In that case the GPU gauge is simply never set
and no ``GPU_HIGH`` can fire. The absence is honest: a zero would claim the GPU
is idle, which is a different fact from "there is no GPU".

**psutil is optional at runtime.** It is a core dependency, but a stripped
deployment must still boot: if the import fails the monitor logs once and never
starts, rather than taking the process down over an observability feature.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger
from app.observability.metrics import SubsystemMetrics, get_subsystem_metrics

if TYPE_CHECKING:
    from app.config.settings import ObservabilitySettings

_LOGGER = get_logger(__name__)

try:  # pragma: no cover - import guard, exercised by the "no psutil" path
    import psutil
except Exception:  # any import failure means "no resource monitor"
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class Threshold:
    """One alarm: which event fires, and the comparison that fires it."""

    event: EventType
    #: True for a "high is bad" metric (CPU, RAM, GPU); False for "low is bad"
    #: (free disk below a floor).
    high_is_bad: bool
    metric: str


_THRESHOLDS: tuple[Threshold, ...] = (
    Threshold(EventType.CPU_HIGH, True, "cpu"),
    Threshold(EventType.RAM_HIGH, True, "ram"),
    Threshold(EventType.DISK_LOW, False, "disk"),
    Threshold(EventType.GPU_HIGH, True, "gpu"),
)


class ResourceMonitor:
    """Sample host resources and publish threshold-crossing events (T182)."""

    def __init__(
        self,
        *,
        settings: ObservabilitySettings,
        bus: EventBus,
        metrics: SubsystemMetrics | None = None,
        name: str = "resource-monitor",
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._metrics = metrics or get_subsystem_metrics()
        self._name = name
        self._task: asyncio.Task[None] | None = None
        #: Which alarms are currently active, so only the rising edge publishes.
        self._active: set[EventType] = set()
        self._alarms = 0
        self._samples = 0

    @property
    def available(self) -> bool:
        """Whether the host can actually be sampled on this deployment."""
        return psutil is not None

    @property
    def alarms(self) -> int:
        """How many threshold alarms have fired since start."""
        return self._alarms

    def snapshot(self) -> dict[str, int | bool]:
        """Counters for a health check or the observability phase (T186)."""
        return {
            "available": self.available,
            "samples": self._samples,
            "alarms": self._alarms,
            "active": len(self._active),
        }

    async def start(self) -> None:
        """Begin the poll loop, unless sampling is impossible or disabled."""
        if self._task is not None:
            return
        if not self._settings.resource_monitor_enabled:
            _LOGGER.info(
                "resource monitor disabled",
                extra={"event": "resources.disabled"},
            )
            return
        if not self.available:
            _LOGGER.warning(
                "resource monitor unavailable: psutil is not importable",
                extra={"event": "resources.unavailable"},
            )
            return
        self._task = asyncio.create_task(self._run(), name=self._name)
        _LOGGER.info(
            "resource monitor started",
            extra={
                "event": "resources.started",
                "interval": self._settings.resource_monitor_interval,
            },
        )

    async def stop(self) -> None:
        """Stop the poll loop."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        _LOGGER.info("resource monitor stopped", extra={"event": "resources.stopped"})

    async def _run(self) -> None:
        interval = max(1, self._settings.resource_monitor_interval)
        while True:
            try:
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # a bad sample must not kill the loop
                _LOGGER.warning(
                    "resource sample failed",
                    extra={"event": "resources.sample_failed"},
                    exc_info=True,
                )
            await asyncio.sleep(interval)

    async def sample_once(self) -> dict[str, float | None]:
        """Sample the host once, update gauges and fire any threshold edges.

        Returns the readings so a test can assert on them without scraping.
        """
        readings: dict[str, float | None] = {
            "cpu": float(psutil.cpu_percent(interval=None)),
            "ram": float(psutil.virtual_memory().percent),
            "disk": self._disk_percent(),
            "gpu": self._gpu_percent(),
        }
        self._samples += 1

        self._metrics.resource_cpu.set(readings["cpu"] or 0.0)
        self._metrics.resource_ram.set(readings["ram"] or 0.0)
        if readings["disk"] is not None:
            self._metrics.resource_disk.set(readings["disk"])
        if readings["gpu"] is not None:
            self._metrics.resource_gpu.set(readings["gpu"])

        for threshold in _THRESHOLDS:
            await self._evaluate(threshold, readings[threshold.metric])
        return readings

    async def _evaluate(self, threshold: Threshold, value: float | None) -> None:
        if value is None:
            return
        limit = self._limit_for(threshold)
        crossed = value >= limit if threshold.high_is_bad else value <= limit
        if crossed and threshold.event not in self._active:
            self._active.add(threshold.event)
            self._alarms += 1
            await self._bus.publish(
                threshold.event.value,
                {
                    "metric": threshold.metric,
                    "value": round(value, 2),
                    "threshold": limit,
                    "direction": "high" if threshold.high_is_bad else "low",
                },
            )
            _LOGGER.warning(
                "resource threshold crossed",
                extra={
                    "event": "resources.threshold",
                    "metric": threshold.metric,
                    "value": value,
                    "threshold": limit,
                },
            )
        elif not crossed and threshold.event in self._active:
            self._active.discard(threshold.event)
            await self._bus.publish(
                f"{threshold.event.value}_CLEARED",
                {
                    "metric": threshold.metric,
                    "value": round(value, 2),
                    "threshold": limit,
                },
            )

    def _limit_for(self, threshold: Threshold) -> float:
        settings = self._settings
        return {
            "cpu": settings.cpu_high_percent,
            "ram": settings.ram_high_percent,
            "disk": settings.disk_low_percent,
            "gpu": settings.gpu_high_percent,
        }[threshold.metric]

    @staticmethod
    def _disk_percent() -> float:
        """Utilisation of the filesystem holding the current directory."""
        usage = psutil.disk_usage(".")
        return float(usage.percent)

    @staticmethod
    def _gpu_percent() -> float | None:
        """GPU utilisation, or ``None`` when there is no GPU to read.

        ``pynvml`` is not a dependency, so this tries the import and reports
        absence rather than fabricating a zero.
        """
        try:  # pragma: no cover - hardware dependent
            import pynvml  # type: ignore[import-not-found]
        except Exception:
            return None
        try:  # pragma: no cover - hardware dependent
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util: Any = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return float(util.gpu)
        except Exception:
            return None


__all__ = ["ResourceMonitor", "Threshold"]
