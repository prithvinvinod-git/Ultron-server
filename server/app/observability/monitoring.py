"""Agent/task/tool/model metrics derived from the event bus (T181/T183, spec A§32).

The subsystem meters in :mod:`app.observability.metrics` are *fed* here rather
than at each call site. That is the whole design: every producer already
publishes the lifecycle facts (``TASK_CREATED``, ``AGENT_STARTED``,
``TOOL_COMPLETED``, ``MODEL_SELECTED``) onto the one bus (A§19), so a single
subscriber can turn that stream into counters and histograms without any
manager importing Prometheus or knowing it is being measured. Instrumentation
that lives in the managers would have to be re-added to every new manager and
would drift; a subscriber sees them all by construction.

**Current-state gauges are derived from observed transitions, not queried from
the database.** A scrape must not open a session or run a query -- it is a pull
that can happen every 15 seconds, and coupling it to the database would make
``/metrics`` fail exactly when the database is the thing under suspicion. The
subscriber therefore keeps the live ``{id: status}`` maps itself, updating them
from the same events that drive the counters. The maps are bounded by the
number of agents/tasks this process has seen; a long-lived process would grow
them without bound, so a terminal state removes the entry.

**It never lets a metrics bug break the system.** :meth:`handle` swallows and
logs any exception: a malformed payload should cost a sample, not the task that
published it. Metrics are observability, never control flow.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any

from app.events.bus import EventBus, EventEnvelope, Subscription
from app.events.types import EventType
from app.observability.logging import get_logger
from app.observability.metrics import SubsystemMetrics, get_subsystem_metrics

_LOGGER = get_logger(__name__)

#: Agent states that mean an agent is no longer occupying a slot.
_AGENT_TERMINAL: frozenset[str] = frozenset({"completed", "failed", "cancelled", "timeout"})

#: Task states that mean a task is finished.
_TASK_TERMINAL: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


class MetricsSubscriber:
    """Turn bus events into agent/task/tool/model/bus metrics.

    ``metrics`` is injectable so a test can pass a fresh
    :class:`SubsystemMetrics` bound to its own registry and assert on labels
    without touching the process-wide exporter.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        metrics: SubsystemMetrics | None = None,
        name: str = "metrics-subscriber",
    ) -> None:
        self._bus = bus
        self._metrics = metrics or get_subsystem_metrics()
        self._name = name
        self._subscription: Subscription | None = None
        self._worker: asyncio.Task[None] | None = None
        self._processed = 0

        # Live state, updated from events so gauges never need the database.
        self._agent_state: dict[str, str] = {}
        self._task_state: dict[str, str] = {}
        # Start times keyed by id for duration histograms.
        self._task_started: dict[str, float] = {}

    @property
    def processed(self) -> int:
        """How many events this subscriber has turned into samples."""
        return self._processed

    def snapshot(self) -> dict[str, int]:
        """Counters for a health check or the observability phase (T186)."""
        return {
            "processed": self._processed,
            "agents_tracked": len(self._agent_state),
            "tasks_tracked": len(self._task_state),
        }

    async def start(self) -> None:
        """Begin consuming the bus."""
        if self._worker is not None:
            return
        self._subscription = self._bus.subscribe()
        self._refresh_bus_subscribers()
        self._worker = asyncio.create_task(self._run(), name=self._name)
        _LOGGER.info("metrics subscriber started", extra={"event": "metrics.subscriber.started"})

    async def stop(self) -> None:
        """Stop the worker and close the subscription."""
        if self._subscription is not None:
            with contextlib.suppress(Exception):
                self._subscription.close()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._subscription = None
        _LOGGER.info("metrics subscriber stopped", extra={"event": "metrics.subscriber.stopped"})

    async def _run(self) -> None:
        subscription = self._subscription
        if subscription is None:
            return
        async for envelope in subscription:
            self.handle(envelope)

    def handle(self, envelope: EventEnvelope) -> None:
        """Record one event. Never raises: a bad sample must not break a task."""
        try:
            self._handle(envelope)
        except Exception:  # observability must not be load-bearing
            _LOGGER.warning(
                "metrics subscriber failed to record an event",
                extra={"event": "metrics.record_failed", "event_type": envelope.event_type},
                exc_info=True,
            )
            return
        self._processed += 1

    def _handle(self, envelope: EventEnvelope) -> None:
        event_type = envelope.event_type
        payload: Mapping[str, Any] = envelope.payload or {}

        # Bus counters describe the bus itself; they are updated for every event.
        if event_type not in {"stream.lagged", "stream.connected"}:
            self._metrics.bus_published.labels(event_type).inc()
        self._refresh_bus_subscribers()

        if event_type == EventType.TASK_CREATED.value:
            self._on_task_created(envelope, payload)
        elif event_type == EventType.TASK_STARTED.value:
            self._on_task_started(envelope, payload)
        elif event_type in {EventType.TASK_COMPLETED.value, EventType.TASK_FAILED.value}:
            self._on_task_terminal(event_type, envelope, payload)
        elif event_type == EventType.AGENT_STARTED.value:
            self._on_agent_started(envelope, payload)
        elif event_type in {
            EventType.AGENT_COMPLETED.value,
            EventType.AGENT_FAILED.value,
        }:
            self._on_agent_terminal(event_type, envelope)
        elif event_type in {EventType.TOOL_COMPLETED.value, EventType.TOOL_FAILED.value}:
            self._on_tool_outcome(event_type, payload)
        elif event_type in {
            EventType.PERMISSION_GRANTED.value,
            EventType.PERMISSION_DENIED.value,
        }:
            self._on_permission_outcome(event_type, payload)
        elif event_type in {"MODEL_SELECTED", "MODEL_REQUEST"}:
            self._on_model_request(payload)
        elif event_type in {"MODEL_FAILED", "MODEL_RESPONSE"}:
            self._on_model_response(event_type, payload)

    # -- Tasks ----------------------------------------------------------- #

    def _on_task_created(self, envelope: EventEnvelope, payload: Mapping[str, Any]) -> None:
        task_id = self._task_id(envelope, payload)
        priority = str(payload.get("priority") or "normal")
        self._metrics.tasks_created.labels(priority).inc()
        self._set_task_state(task_id, str(payload.get("status") or "pending"))

    def _on_task_started(self, envelope: EventEnvelope, payload: Mapping[str, Any]) -> None:
        task_id = self._task_id(envelope, payload)
        self._set_task_state(task_id, "running")
        self._task_started[task_id] = _now()

    def _on_task_terminal(
        self, event_type: str, envelope: EventEnvelope, payload: Mapping[str, Any]
    ) -> None:
        task_id = self._task_id(envelope, payload)
        outcome = "completed" if event_type == EventType.TASK_COMPLETED.value else "failed"
        self._metrics.tasks_finished.labels(outcome).inc()
        started = self._task_started.pop(task_id, None)
        if started is not None:
            self._metrics.task_duration.observe(max(0.0, _now() - started))
        self._set_task_state(task_id, outcome)

    def _task_id(self, envelope: EventEnvelope, payload: Mapping[str, Any]) -> str:
        raw = payload.get("task_id") or envelope.task_id
        return str(raw) if raw is not None else f"anon-{envelope.id}"

    def _set_task_state(self, task_id: str, state: str) -> None:
        previous = self._task_state.get(task_id)
        if previous == state:
            return
        if previous is not None:
            self._metrics.tasks_current.labels(previous).dec()
        if state in _TASK_TERMINAL:
            # Terminal tasks leave no current sample behind.
            self._task_state.pop(task_id, None)
            return
        self._task_state[task_id] = state
        self._metrics.tasks_current.labels(state).inc()

    # -- Agents ---------------------------------------------------------- #

    def _on_agent_started(self, envelope: EventEnvelope, payload: Mapping[str, Any]) -> None:
        agent_type = str(payload.get("agent_type") or payload.get("type") or "unknown")
        self._metrics.agents_started.labels(agent_type).inc()
        agent_id = self._agent_id(envelope, payload)
        self._set_agent_state(agent_id, "running")

    def _on_agent_terminal(self, event_type: str, envelope: EventEnvelope) -> None:
        outcome = "completed" if event_type == EventType.AGENT_COMPLETED.value else "failed"
        self._metrics.agents_finished.labels(outcome).inc()
        agent_id = self._agent_id(envelope, {})
        self._set_agent_state(agent_id, outcome)

    def _agent_id(self, envelope: EventEnvelope, payload: Mapping[str, Any]) -> str:
        raw = payload.get("agent_id") or envelope.agent_id
        return str(raw) if raw is not None else f"anon-{envelope.id}"

    def _set_agent_state(self, agent_id: str, state: str) -> None:
        previous = self._agent_state.get(agent_id)
        if previous == state:
            return
        if previous is not None:
            self._metrics.agents_current.labels(previous).dec()
        if state in _AGENT_TERMINAL:
            self._agent_state.pop(agent_id, None)
            return
        self._agent_state[agent_id] = state
        self._metrics.agents_current.labels(state).inc()

    # -- Tools ----------------------------------------------------------- #

    def _on_tool_outcome(self, event_type: str, payload: Mapping[str, Any]) -> None:
        tool = str(payload.get("tool") or "unknown")
        outcome = "completed" if event_type == EventType.TOOL_COMPLETED.value else "failed"
        self._metrics.tool_executions.labels(tool, outcome).inc()
        duration_ms = payload.get("duration_ms")
        if isinstance(duration_ms, int | float):
            self._metrics.tool_duration.labels(tool).observe(max(0.0, float(duration_ms) / 1000.0))

    # -- Permissions (§59.24) ------------------------------------------- #

    def _on_permission_outcome(self, event_type: str, payload: Mapping[str, Any]) -> None:
        tool = str(payload.get("tool") or "unknown")
        level = str(payload.get("level") or "unknown")
        decision = "granted" if event_type == EventType.PERMISSION_GRANTED.value else "denied"
        self._metrics.tool_authorization.labels(tool, level, decision).inc()

    # -- Models ---------------------------------------------------------- #

    def _on_model_request(self, payload: Mapping[str, Any]) -> None:
        model = str(payload.get("model") or "unknown")
        provider = str(payload.get("provider") or "unknown")
        self._metrics.model_requests.labels(model, provider).inc()
        prompt_tokens = payload.get("prompt_tokens")
        if isinstance(prompt_tokens, int | float):
            self._metrics.model_tokens.labels(model, "prompt").inc(float(prompt_tokens))

    def _on_model_response(self, event_type: str, payload: Mapping[str, Any]) -> None:
        model = str(payload.get("model") or "unknown")
        latency_ms = payload.get("latency_ms")
        if isinstance(latency_ms, int | float):
            self._metrics.model_latency.labels(model).observe(max(0.0, float(latency_ms) / 1000.0))
        completion_tokens = payload.get("completion_tokens")
        if isinstance(completion_tokens, int | float):
            self._metrics.model_tokens.labels(model, "completion").inc(float(completion_tokens))

    # -- Bus ------------------------------------------------------------- #

    def _refresh_bus_subscribers(self) -> None:
        self._metrics.bus_subscribers.set(float(self._bus.subscriber_count))


def _now() -> float:
    """Monotonic seconds; durations must not be affected by a clock change."""
    import time

    return time.monotonic()


__all__ = ["MetricsSubscriber"]
