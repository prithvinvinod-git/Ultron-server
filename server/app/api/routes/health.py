"""Health, readiness, and metrics routes (T022, spec §32).

Three endpoints, and the difference between them is the whole point.

**``/health`` is liveness, and it stays ``200``.** It asks one question: is this
process up and answering? It is deliberately **dependency-free** -- it does not
consult PostgreSQL. A liveness probe that hit the database would restart a
perfectly healthy process every time PostgreSQL hiccuped, turning one
dependency's blip into an outage of every replica holding it. Configuration
warnings are *reported in the body* (``status`` drops to ``degraded``) rather
than failing the probe, because a restart cannot fix configuration: it would
only convert a diagnosable deployment into a crash loop. That is what makes
``Settings.startup_warnings``' promise ("observable through ``/health``")
actually true; before T022 those warnings were logged once at boot and then
invisible.

**``/ready`` is readiness, and it fails with ``503``.** It asks whether the
required dependencies are usable, which is what decides whether this instance
belongs in a load balancer's rotation. PostgreSQL is the only required check
(spec §23), so an unreachable Redis or Ollama degrades the report without
removing the instance.

**``/metrics`` is a Prometheus scrape**, gated by ``METRICS_ENABLED``.

None of these require a token. An orchestrator probing ``/ready`` cannot present
one, and a probe endpoint that needs credentials is a probe endpoint that gets
disabled. The trade is that all three are unauthenticated, so ``/metrics`` in
particular must be restricted at the network layer -- it is disabled by default
and belongs behind the reverse proxy rather than exposed publicly.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.dependencies import Container
from app.observability import metrics as metrics_module
from app.observability.health import HealthStatus

router = APIRouter(tags=["health"])


def _warnings(container: Container) -> list[str]:
    """Configuration problems worth showing on a probe.

    These are why ``Settings.startup_warnings`` produces warnings instead of
    refusing to boot. Reporting them on both probes is what keeps that promise:
    before T022 they were logged once at startup and then invisible.
    """
    return container.settings.startup_warnings()


@router.get(
    "/health",
    summary="Liveness probe",
    response_description="The process is up and answering.",
)
async def health(container: Container, response: Response) -> dict[str, object]:
    """Return liveness plus any configuration warnings.

    Deliberately dependency-free. ``HealthService.liveness`` answers without
    touching PostgreSQL, and this route keeps that property: a liveness probe
    that consulted the database would restart a perfectly healthy process every
    time PostgreSQL hiccuped, turning one dependency's blip into an outage of
    every replica holding it. Dependency detail is ``/ready``'s job.

    The HTTP status stays ``200`` however misconfigured the process is, because a
    restart cannot fix configuration -- it would only convert a diagnosable
    deployment into a crash loop. ``status`` drops to ``degraded`` instead, so
    the problem is visible to anything reading the body while the probe still
    reports the truth about liveness.
    """
    warnings = _warnings(container)
    body: dict[str, object] = dict(await container.health.liveness())
    if warnings:
        body["status"] = HealthStatus.DEGRADED.value
    body["warnings"] = warnings
    response.status_code = status.HTTP_200_OK
    return body


@router.get(
    "/ready",
    summary="Readiness probe",
    response_description="Whether required dependencies are usable.",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "At least one required dependency is unusable.",
        }
    },
)
async def ready(container: Container, response: Response) -> dict[str, object]:
    """Return ``503`` unless every required dependency is usable.

    Optional dependencies degrade without failing readiness: Redis down is a cold
    cache, and an unreachable model provider is a capability ULTRON is specified
    to survive (spec §33). Only PostgreSQL decides.
    """
    report = await container.health.readiness()
    response.status_code = (
        status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    body: dict[str, object] = dict(report.to_dict())
    body["warnings"] = _warnings(container)
    return body


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_description="Metrics in the Prometheus text exposition format.",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Metrics are disabled."}},
    response_class=Response,
)
async def metrics(container: Container, response: Response) -> Response:
    """Return the health-derived gauges, or ``404`` when metrics are disabled.

    Disabled by default. A scrape endpoint describes the deployment's internals
    -- dependency names, latency, version -- and returning ``404`` is clearer than
    an empty body, because an empty body reads as "healthy, nothing to report".
    """
    settings = container.settings.observability
    if not settings.metrics_enabled:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    exporter = metrics_module.get_metrics()
    report = await container.health.report()
    exporter.observe(report, startup_warnings=len(container.settings.startup_warnings()))
    return Response(content=exporter.render(), media_type=metrics_module.CONTENT_TYPE)


#: Named for the mount in ``app.main``.
__all__ = ["router"]
