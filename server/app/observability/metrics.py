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
    Gauge,
    GCCollector,
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


class HealthMetrics:
    """Gauges describing the most recent health report.

    Stateful on purpose: Prometheus pulls, so the current value has to be
    somewhere for the pull to find. :meth:`observe` is called on each scrape
    after a fresh report, which keeps the gauges aligned with the report the
    caller is also returning.
    """

    def __init__(self) -> None:
        self._registry = CollectorRegistry()
        # Process and GC metrics come free and are genuinely useful for sizing a
        # 4 GB VM (§59.25); they are registered explicitly rather than via the
        # global default registry.
        ProcessCollector(registry=self._registry)
        PlatformCollector(registry=self._registry)
        GCCollector(registry=self._registry)

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


#: Process-wide instance. Module-level because the gauges must outlive a single
#: request; a scrape is a read of retained state, not a fresh computation.
_METRICS: Final[HealthMetrics] = HealthMetrics()


def get_metrics() -> HealthMetrics:
    """Return the shared exporter."""
    return _METRICS
