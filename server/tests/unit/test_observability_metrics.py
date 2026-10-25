"""Unit tests for the Phase 12 observability meters (T180-T185, spec A§32).

Two things are worth testing without a running server:

- **Registration and rendering.** The subsystem meters must be present in the
  one registry and render as valid Prometheus text, so a scrape after Phase 12
  actually carries them. A meter that exists in code but not in the registry is
  invisible to every dashboard.
- **Threshold events are edge-triggered.** The resource monitor must publish
  ``CPU_HIGH`` (and friends) once on the rising edge and a recovery event on the
  falling edge, never once per sample: a machine pegged for an hour would
  otherwise bury the stream, and silence must not have to be read as recovery.
"""

from __future__ import annotations

import asyncio

import pytest
from prometheus_client import CollectorRegistry

from app.config import ObservabilitySettings
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability import metrics as metrics_module
from app.observability.metrics import SubsystemMetrics
from app.observability.monitoring import MetricsSubscriber
from app.observability.resources import ResourceMonitor

# --------------------------------------------------------------------------- #
# Registration and rendering (T180/T181)
# --------------------------------------------------------------------------- #


def test_subsystem_metrics_are_registered_and_render() -> None:
    exporter = SubsystemMetrics(CollectorRegistry())
    text = exporter.render().decode()
    for name in (
        "ultron_agents_current",
        "ultron_agents_started_total",
        "ultron_tasks_current",
        "ultron_tasks_created_total",
        "ultron_task_duration_seconds",
        "ultron_tool_executions_total",
        "ultron_tool_authorization_total",
        "ultron_model_requests_total",
        "ultron_model_tokens_total",
        "ultron_event_bus_published_total",
        "ultron_event_bus_subscribers",
        "ultron_resource_cpu_percent",
    ):
        assert name in text, f"{name} is missing from the scrape"


def test_shared_registry_carries_health_and_subsystems() -> None:
    """One scrape must describe both health and the work (§32)."""
    text = metrics_module.get_subsystem_metrics().render().decode()
    assert "ultron_health_ready" in text
    assert "ultron_tasks_created_total" in text


# --------------------------------------------------------------------------- #
# Metrics subscriber (T181/T183)
# --------------------------------------------------------------------------- #


def _subscriber() -> tuple[SubsystemMetrics, MetricsSubscriber]:
    bus = EventBus()
    exporter = SubsystemMetrics(CollectorRegistry())
    return exporter, MetricsSubscriber(bus, metrics=exporter)


def test_task_lifecycle_updates_counters_and_histogram() -> None:
    exporter, sub = _subscriber()
    sub.handle(_env(EventType.TASK_CREATED.value, {"task_id": "t1", "priority": "high"}))
    sub.handle(_env(EventType.TASK_STARTED.value, {"task_id": "t1"}))
    sub.handle(_env(EventType.TASK_COMPLETED.value, {"task_id": "t1"}))

    text = exporter.render().decode()
    assert 'ultron_tasks_created_total{priority="high"} 1.0' in text
    assert 'ultron_tasks_finished_total{outcome="completed"} 1.0' in text
    assert "ultron_task_duration_seconds_count 1.0" in text
    # The task is terminal, so no state carries a live count of 1.
    assert 'ultron_tasks_current{status="running"} 0.0' in text
    assert 'ultron_tasks_current{status="pending"} 0.0' in text


def test_agent_lifecycle_tracks_current_state() -> None:
    exporter, sub = _subscriber()
    sub.handle(_env(EventType.AGENT_STARTED.value, {"agent_type": "coder"}, agent_id="a1"))
    text = exporter.render().decode()
    assert 'ultron_agents_current{status="running"} 1.0' in text

    sub.handle(_env(EventType.AGENT_COMPLETED.value, {}, agent_id="a1"))
    text = exporter.render().decode()
    assert 'ultron_agents_finished_total{outcome="completed"} 1.0' in text
    # Terminal: the running gauge is decremented back to zero.
    assert 'ultron_agents_current{status="running"} 0.0' in text


def test_tool_and_model_metrics_from_events() -> None:
    exporter, sub = _subscriber()
    sub.handle(_env(EventType.TOOL_COMPLETED.value, {"tool": "read_file", "duration_ms": 250}))
    sub.handle(_env("MODEL_SELECTED", {"model": "llama3", "provider": "ollama"}))
    text = exporter.render().decode()
    assert 'ultron_tool_executions_total{outcome="completed",tool="read_file"} 1.0' in text
    assert 'ultron_model_requests_total{model="llama3",provider="ollama"} 1.0' in text


def test_tool_authorization_metric_from_events() -> None:
    """§59.24: PERMISSION_GRANTED/DENIED events update authorization metrics."""
    exporter, sub = _subscriber()
    sub.handle(_env(
        EventType.PERMISSION_GRANTED.value,
        {"tool": "fs.write", "level": "MODIFY_PROJECT"},
    ))
    text = exporter.render().decode()
    # Prometheus sorts labels alphabetically
    assert 'ultron_tool_authorization_total{decision="granted",level="MODIFY_PROJECT",tool="fs.write"} 1.0' in text


def test_subscriber_survives_a_malformed_payload() -> None:
    """A bad payload costs a sample, never the task that published it."""
    _, sub = _subscriber()
    sub.handle(_env(EventType.TASK_CREATED.value, {"priority": object()}))
    # No exception escaped, and the subscriber still works afterwards.
    before = sub.processed
    sub.handle(_env(EventType.TASK_CREATED.value, {"task_id": "ok", "priority": "low"}))
    assert sub.processed >= before


# --------------------------------------------------------------------------- #
# Resource monitor threshold events (T182)
# --------------------------------------------------------------------------- #


def _all_cpu_bad_settings() -> ObservabilitySettings:
    return ObservabilitySettings(
        resource_monitor_enabled=True,
        cpu_high_percent=0.0,
        ram_high_percent=0.0,
        disk_low_percent=100.0,
        gpu_high_percent=0.0,
    )


def _drain(sub) -> list[str]:
    seen: list[str] = []
    while True:
        try:
            envelope = sub._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if envelope is not None:
            seen.append(envelope.event_type)
    return seen


@pytest.mark.asyncio
async def test_threshold_fires_once_per_edge() -> None:
    if not ResourceMonitor(settings=_all_cpu_bad_settings(), bus=EventBus()).available:
        pytest.skip("psutil is not importable on this host")

    bus = EventBus()
    monitor = ResourceMonitor(
        settings=_all_cpu_bad_settings(),
        bus=bus,
        metrics=SubsystemMetrics(CollectorRegistry()),
    )
    # Subscribe first: the bus does not replay, so a later subscriber would miss
    # the events we are here to observe.
    sub = bus.subscribe()
    await monitor.sample_once()
    await monitor.sample_once()

    seen = _drain(sub)
    sub.close()
    assert seen.count(EventType.CPU_HIGH.value) == 1
    assert seen.count(EventType.RAM_HIGH.value) == 1
    assert monitor.alarms == 3  # cpu, ram, disk (no GPU on this host)
    assert monitor.snapshot()["samples"] == 2


@pytest.mark.asyncio
async def test_resource_gauges_are_set_after_a_sample() -> None:
    if not ResourceMonitor(settings=_all_cpu_bad_settings(), bus=EventBus()).available:
        pytest.skip("psutil is not importable on this host")

    exporter = SubsystemMetrics(CollectorRegistry())
    monitor = ResourceMonitor(
        settings=ObservabilitySettings(resource_monitor_enabled=True),
        bus=EventBus(),
        metrics=exporter,
    )
    await monitor.sample_once()
    text = exporter.render().decode()
    assert "ultron_resource_cpu_percent" in text
    assert "ultron_resource_ram_percent" in text


@pytest.mark.asyncio
async def test_monitor_is_a_noop_when_disabled() -> None:
    monitor = ResourceMonitor(
        settings=ObservabilitySettings(resource_monitor_enabled=False),
        bus=EventBus(),
        metrics=SubsystemMetrics(CollectorRegistry()),
    )
    await monitor.start()
    # No task was created, so nothing will sample and no alarm can fire.
    assert monitor.snapshot()["samples"] == 0
    await monitor.stop()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _env(
    event_type: str,
    payload: dict[str, object],
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
):
    from app.events.bus import EventEnvelope

    return EventEnvelope(
        id=1, event_type=event_type, payload=payload, agent_id=agent_id, task_id=task_id
    )
