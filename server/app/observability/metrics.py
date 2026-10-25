"""Prometheus exposition for the health report (T022, spec §32).

``/metrics`` is a thin HTTP wrapper around this module. The route does no
arithmetic; it asks for a rendered body and returns it.

Three decisions worth stating.

**A dedicated registry, not the global default.** ``prometheus_client`` ships a
process-wide ``REGISTRY`` that any import in any module may write to. Two
processes in one test session, or one reload of the app factory, would collide on
metric names and raise ``Duplicated timeseries``. Owning the registry makes the
exporter's contents a property of this module and nothing else.

**Status is one-hot, not a magic number.** ``ultron_health_check_status`` carries
the literal status as a *label* and sets it to 1, so
``ultron_health_check_status{check="postgresql",status="failed"} 1`` is
self-describing. Collapsing four states onto 1 / 0.5 / 0 forces every dashboard
to hard-code the encoding, and adding a fifth state later would silently
reinterpret the existing ones.

**Rendering must not be able to fail the request.** ``observe`` only assigns
gauges that already exist, and ``render`` swallows exposition errors: a metrics
scrape that 500s is worse than one that is briefly empty, and health itself is
already reported by ``/health`` and ``/ready``.
"""

from __future__ import annotations

from typing import Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

from app.observability.health import HealthReport, HealthStatus

#: Prometheus text exposition format, used as the response Content-Type.
CONTENT_TYPE: Final[str] = CONTENT_TYPE_LATEST

#: Statuses rendered as labels. Ordering is severity-descending so the rendered
#: body is stable and diffable between scrapes.
_STATUSES: Final[tuple[HealthStatus, ...]] = (
    HealthStatus.FAILED,
    HealthStatus.DEGRADED,
    HealthStatus.OK,
    HealthStatus.SKIPPED,
)

#: The one registry every exporter in this module writes to. Process and GC
#: metrics come free and are genuinely useful for sizing a 4 GB VM (A§59.25);
#: they are registered here rather than via prometheus_client's global default
#: so two app factories in one test session cannot collide on a metric name.
_REGISTRY: Final[CollectorRegistry] = CollectorRegistry()
ProcessCollector(registry=_REGISTRY)
PlatformCollector(registry=_REGISTRY)
GCCollector(registry=_REGISTRY)


class HealthMetrics:
    """Gauges describing the most recent health report.

    Stateful on purpose: Prometheus pulls, so the current value has to be
    somewhere for the pull to find. :meth:`observe` is called on each scrape
    after a fresh report, which keeps the gauges aligned with the report the
    caller is also returning.
    """

    def __init__(self, registry: CollectorRegistry = _REGISTRY) -> None:
        self._registry = registry

        self._check_status = Gauge(
            "ultron_health_check_status",
            "One-hot status of a dependency health check.",
            ("check", "status"),
            registry=self._registry,
        )
        self._check_required = Gauge(
            "ultron_health_check_required",
            "Whether a check is required for readiness (1) or optional (0).",
            ("check",),
            registry=self._registry,
        )
        self._check_ready = Gauge(
            "ultron_health_check_ready",
            "Whether a check currently permits readiness (1) or blocks it (0).",
            ("check",),
            registry=self._registry,
        )
        self._check_latency = Gauge(
            "ultron_health_check_latency_ms",
            "Wall-clock duration of the last run of a check, in milliseconds.",
            ("check",),
            registry=self._registry,
        )
        self._ready = Gauge(
            "ultron_health_ready",
            "Whether the server is ready to accept traffic (1) or not (0).",
            registry=self._registry,
        )
        self._status_healthy = Gauge(
            "ultron_health_up",
            "Whether the report status is healthy or degraded rather than failed.",
            registry=self._registry,
        )
        self._report_duration = Gauge(
            "ultron_health_report_duration_ms",
            "Wall-clock duration of the last full health report, in milliseconds.",
            registry=self._registry,
        )
        self._startup_warnings = Gauge(
            "ultron_startup_warnings",
            "Number of configuration warnings raised at startup.",
            registry=self._registry,
        )

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    def observe(self, report: HealthReport, *, startup_warnings: int = 0) -> None:
        """Publish ``report`` into the gauges.

        ``startup_warnings`` is counted rather than described: the warnings are
        configuration values, not samples, so a gauge holding their text would
        leak secrets into a scrape. The text belongs in ``/health``.
        """
        for check in report.checks:
            for status in _STATUSES:
                self._check_status.labels(check.name, status.value).set(
                    1.0 if check.status is status else 0.0
                )
            self._check_required.labels(check.name).set(1.0 if check.required else 0.0)
            self._check_ready.labels(check.name).set(1.0 if check.ready else 0.0)
            self._check_latency.labels(check.name).set(check.latency_ms)
        self._ready.set(1.0 if report.ready else 0.0)
        self._status_healthy.set(1.0 if report.status.is_healthy else 0.0)
        self._report_duration.set(report.duration_ms)
        self._startup_warnings.set(float(startup_warnings))

    def render(self) -> bytes:
        """Render the current gauges in Prometheus text format."""
        return generate_latest(self._registry)


#: Meters for the agent/task/tool/model/bus subsystems (T180/T181/T183).
#:
#: These are *separate* from the health gauges because they answer a different
#: question: health says "is the deployment usable", these say "what is the
#: work doing". Keeping them in one registry means a single ``/metrics`` scrape
#: carries both, which is the point of a single scrape endpoint.
#:
#: Counters are cumulative and derive from the event stream (a subscriber in
#: :mod:`app.observability.monitoring` feeds them), so instrumentation lives in
#: one place rather than in every manager. Gauges that describe *current* state
#: (agents/tasks by status) are maintained by the same subscriber as it sees the
#: lifecycle transitions, which is the only writer and therefore cannot drift.
class SubsystemMetrics:
    """Prometheus meters for agents, tasks, tools, models, the bus and host."""

    def __init__(self, registry: CollectorRegistry = _REGISTRY) -> None:
        self._registry = registry

        # -- Agents ---------------------------------------------------------
        self.agents_current = Gauge(
            "ultron_agents_current",
            "Agents currently in each lifecycle state.",
            ("status",),
            registry=registry,
        )
        self.agents_started = Counter(
            "ultron_agents_started",
            "Agents that began running, by agent type.",
            ("agent_type",),
            registry=registry,
        )
        self.agents_finished = Counter(
            "ultron_agents_finished",
            "Agents that reached a terminal state, by outcome.",
            ("outcome",),
            registry=registry,
        )

        # -- Tasks ----------------------------------------------------------
        self.tasks_current = Gauge(
            "ultron_tasks_current",
            "Tasks currently in each lifecycle state.",
            ("status",),
            registry=registry,
        )
        self.tasks_created = Counter(
            "ultron_tasks_created",
            "Tasks created.",
            ("priority",),
            registry=registry,
        )
        self.tasks_finished = Counter(
            "ultron_tasks_finished",
            "Tasks that reached a terminal state, by outcome.",
            ("outcome",),
            registry=registry,
        )
        self.task_duration = Histogram(
            "ultron_task_duration_seconds",
            "Wall-clock duration of a task from start to terminal state.",
            buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200),
            registry=registry,
        )

        # -- Tools ----------------------------------------------------------
        self.tool_executions = Counter(
            "ultron_tool_executions",
            "Tool executions by tool name and outcome.",
            ("tool", "outcome"),
            registry=registry,
        )
        self.tool_duration = Histogram(
            "ultron_tool_duration_seconds",
            "Wall-clock duration of a tool execution.",
            ("tool",),
            buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 300),
            registry=registry,
        )
        # §59.24: tool authorization events, correlated with §15 audit rows.
        self.tool_authorization = Counter(
            "ultron_tool_authorization",
            "Tool authorization evaluations by level and decision.",
            ("tool", "level", "decision"),
            registry=registry,
        )

        # -- Models ---------------------------------------------------------
        self.model_requests = Counter(
            "ultron_model_requests",
            "Model requests by model and provider.",
            ("model", "provider"),
            registry=registry,
        )
        self.model_tokens = Counter(
            "ultron_model_tokens",
            "Model tokens consumed by model and kind (prompt/completion).",
            ("model", "kind"),
            registry=registry,
        )
        self.model_latency = Histogram(
            "ultron_model_latency_seconds",
            "Wall-clock latency of a model call, by model.",
            ("model",),
            buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
            registry=registry,
        )

        # -- Event bus ------------------------------------------------------
        self.bus_published = Counter(
            "ultron_event_bus_published",
            "Events published, by event type.",
            ("event_type",),
            registry=registry,
        )
        self.bus_subscribers = Gauge(
            "ultron_event_bus_subscribers",
            "Active subscribers on the in-process event bus.",
            registry=registry,
        )

        # -- Host resources -------------------------------------------------
        self.resource_cpu = Gauge(
            "ultron_resource_cpu_percent",
            "Host CPU utilisation as a percentage.",
            registry=registry,
        )
        self.resource_ram = Gauge(
            "ultron_resource_ram_percent",
            "Host RAM utilisation as a percentage.",
            registry=registry,
        )
        self.resource_disk = Gauge(
            "ultron_resource_disk_percent",
            "Host disk utilisation as a percentage (low is bad).",
            registry=registry,
        )
        self.resource_gpu = Gauge(
            "ultron_resource_gpu_percent",
            "GPU utilisation as a percentage, when a GPU is present.",
            registry=registry,
        )

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    def render(self) -> bytes:
        """Render the shared registry in Prometheus text format."""
        return generate_latest(self._registry)


#: Process-wide instances. Module-level because the meters must outlive a single
#: request; a scrape is a read of retained state, not a fresh computation.
_METRICS: Final[HealthMetrics] = HealthMetrics()
_SUBSYSTEMS: Final[SubsystemMetrics] = SubsystemMetrics()


def get_metrics() -> HealthMetrics:
    """Return the shared health exporter."""
    return _METRICS


def get_subsystem_metrics() -> SubsystemMetrics:
    """Return the shared subsystem exporter (T180/T181/T183)."""
    return _SUBSYSTEMS
