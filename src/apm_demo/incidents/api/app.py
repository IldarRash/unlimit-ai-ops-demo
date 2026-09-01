from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg import OperationalError as PostgresOperationalError
from psycopg_pool import PoolTimeout

from apm_demo.incidents.api.auth import (
    SurfaceAuth,
    is_integration_or_catalog_path,
    is_operator_authorized,
)
from apm_demo.incidents.api.config import IncidentSettings
from apm_demo.incidents.api.container import IncidentContainer, build_container
from apm_demo.incidents.api.logging import configure_logging
from apm_demo.incidents.api.schemas import (
    AnalyzeIncidentRequest,
    AnalyzeIncidentResponse,
    IncidentFeedbackRequest,
    UpdateIncidentStatusRequest,
)
from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.application.pipeline import InvalidAlert
from apm_demo.incidents.domain import (
    AlertIngestResult,
    AlertmanagerWebhook,
    CatalogAuditEvent,
    ClassificationKind,
    IncidentAuditEvent,
    IncidentFeedback,
    IncidentRecord,
    IncidentStatus,
    KnownErrorRule,
    ProviderEvent,
)
from apm_demo.incidents.infrastructure import (
    AnalysisUnavailable,
    CatalogAmbiguityError,
    MetricsUnavailable,
)


logger = logging.getLogger("apm_demo.incidents.api")
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def create_app(settings: IncidentSettings | None = None) -> FastAPI:
    configure_logging()
    resolved_settings = settings or IncidentSettings()
    surface_auth = SurfaceAuth.from_environment()
    container = build_container(resolved_settings)
    storage_backend = (
        "postgresql" if resolved_settings.database_url_value() else "sqlite-wal"
    )
    trusted_networks = tuple(
        ipaddress.ip_network(item.strip())
        for item in resolved_settings.trusted_ingress_networks.split(",")
        if item.strip()
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await container.initialize()
        yield
        await container.aclose()

    app = FastAPI(
        title="Incident Intelligence API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.container = container
    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

    @app.middleware("http")
    async def surface_access_control(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path == "/metrics":
            if not surface_auth.metrics_token_matches(
                request.headers.get("authorization", "")
            ):
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "invalid scrape credential"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        elif (
            surface_auth.operator_auth_enabled
            and path not in {"/health", "/ready"}
            and not is_integration_or_catalog_path(path)
            and not is_operator_authorized(request, surface_auth)
        ):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "operator authentication required"},
                headers={"WWW-Authenticate": 'Basic realm="Incident Intelligence"'},
            )
        return await call_next(request)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid4().hex
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={"request_id": request_id},
            )
            raise
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["cache-control"] = "no-store"
        return response

    @app.exception_handler(MetricsUnavailable)
    async def metrics_unavailable(_: Request, error: MetricsUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.exception_handler(AnalysisUnavailable)
    async def analysis_unavailable(_: Request, error: AnalysisUnavailable) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @app.exception_handler(CatalogAmbiguityError)
    async def catalog_ambiguity(
        _: Request, error: CatalogAmbiguityError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(PostgresOperationalError)
    @app.exception_handler(sqlite3.OperationalError)
    async def storage_unavailable(
        _: Request, __: Exception
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": "incident storage unavailable"}
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": resolved_settings.service_name}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if not await container.incidents.ping():
            raise HTTPException(status_code=503, detail="incident store unavailable")
        return {"status": "ready", "storage": storage_backend}

    @app.get("/metrics", include_in_schema=False)
    async def pipeline_prometheus_metrics() -> Response:
        analyzer = container.analyzer
        container.pipeline_metrics.llm_circuit_open.set(
            1 if getattr(analyzer, "circuit_open", False) else 0
        )
        return Response(
            content=generate_latest(container.pipeline_metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.get("/api/v1/runtime")
    async def runtime() -> dict[str, str]:
        return {
            "metrics_mode": resolved_settings.metrics_mode.value,
            "analyzer_mode": resolved_settings.analyzer_mode.value,
            "model": (
                resolved_settings.openai_model
                if resolved_settings.analyzer_mode.value == "openai"
                else "deterministic-incident-analyzer-v1"
            ),
            "storage": storage_backend,
        }

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @app.post(
        "/api/v1/incidents/analyze",
        response_model=AnalyzeIncidentResponse,
    )
    async def analyze(payload: AnalyzeIncidentRequest) -> AnalyzeIncidentResponse:
        incident = await container.analyze_incident.execute(
            payload.provider,
            window_seconds=(
                payload.window_seconds or resolved_settings.analysis_window_seconds
            ),
        )
        return AnalyzeIncidentResponse(detected=incident is not None, incident=incident)

    @app.post(
        "/api/v1/integrations/alertmanager",
        response_model=AlertIngestResult,
    )
    async def ingest_alertmanager(
        payload: AlertmanagerWebhook, request: Request
    ) -> AlertIngestResult:
        _require_network(
            request,
            trusted_networks,
            enforce=resolved_settings.enforce_ingress_networks,
        )
        _require_bearer(
            request, resolved_settings.alertmanager_token_value()
        )
        started = perf_counter()
        try:
            result = await container.alert_pipeline.ingest(payload)
        except InvalidAlert as error:
            container.pipeline_metrics.rejections.labels(reason="invalid-alert").inc()
            raise HTTPException(status_code=422, detail=str(error)) from error
        except MetricsUnavailable:
            container.pipeline_metrics.deliveries.labels(outcome="metrics-unavailable").inc()
            raise
        finally:
            container.pipeline_metrics.processing_seconds.observe(
                perf_counter() - started
            )
        outcome = "replayed" if result.replayed else "accepted"
        container.pipeline_metrics.deliveries.labels(outcome=outcome).inc()
        for incident_id in result.incident_ids:
            incident = await container.incidents.get(incident_id)
            if incident is not None:
                container.pipeline_metrics.alerts.labels(
                    status=incident.status.value,
                    classification=incident.analysis.classification.value,
                ).inc()
        return result

    @app.post(
        "/api/v1/provider-events",
        response_model=ProviderEvent,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def ingest_provider_event(
        event: ProviderEvent, request: Request
    ) -> ProviderEvent:
        _require_network(
            request,
            trusted_networks,
            enforce=resolved_settings.enforce_ingress_networks,
        )
        _require_bearer(
            request, resolved_settings.provider_event_token_value()
        )
        stored = await container.provider_events.append_event(event)
        container.pipeline_metrics.provider_events.labels(
            provider=stored.provider.value, outcome=stored.outcome.value
        ).inc()
        return stored

    @app.get("/api/v1/incidents", response_model=list[IncidentRecord])
    async def list_incidents(
        limit: int = Query(default=50, ge=1, le=200),
        provider: ProviderId | None = None,
        incident_status: IncidentStatus | None = Query(default=None, alias="status"),
        classification: ClassificationKind | None = None,
    ) -> tuple[IncidentRecord, ...]:
        incidents = await container.incidents.list_recent(limit=200)
        filtered = tuple(
            incident
            for incident in incidents
            if (provider is None or incident.provider is provider)
            and (incident_status is None or incident.status is incident_status)
            and (
                classification is None
                or incident.analysis.classification is classification
            )
        )
        return filtered[:limit]

    @app.get("/api/v1/incidents/{incident_id}", response_model=IncidentRecord)
    async def get_incident(incident_id: str) -> IncidentRecord:
        incident = await container.incidents.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return incident

    @app.get(
        "/api/v1/incidents/{incident_id}/audit",
        response_model=list[IncidentAuditEvent],
    )
    async def get_audit(incident_id: str) -> tuple[IncidentAuditEvent, ...]:
        if await container.incidents.get(incident_id) is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return await container.audit_log.list_for_incident(incident_id)

    @app.patch("/api/v1/incidents/{incident_id}/status", response_model=IncidentRecord)
    async def update_status(
        incident_id: str, payload: UpdateIncidentStatusRequest
    ) -> IncidentRecord:
        incident = await container.lifecycle.set_status(incident_id, payload.status)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        await container.event_bus.publish(incident)
        return incident

    @app.post(
        "/api/v1/incidents/{incident_id}/feedback",
        response_model=IncidentFeedback,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_feedback(
        incident_id: str, payload: IncidentFeedbackRequest
    ) -> IncidentFeedback:
        if await container.incidents.get(incident_id) is None:
            raise HTTPException(status_code=404, detail="incident not found")
        feedback = IncidentFeedback(
            feedback_id=f"fb_{uuid4().hex}",
            incident_id=incident_id,
            verdict=payload.verdict,
            note=payload.note,
        )
        stored = await container.feedback.append_feedback(feedback)
        container.pipeline_metrics.feedback.labels(verdict=stored.verdict.value).inc()
        return stored

    @app.get(
        "/api/v1/incidents/{incident_id}/feedback",
        response_model=list[IncidentFeedback],
    )
    async def list_feedback(incident_id: str) -> tuple[IncidentFeedback, ...]:
        if await container.incidents.get(incident_id) is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return await container.feedback.list_feedback(incident_id)

    @app.get("/api/v1/stream/incidents", include_in_schema=False)
    async def incident_stream(
        provider: ProviderId | None = None,
        incident_status: IncidentStatus | None = Query(default=None, alias="status"),
        classification: ClassificationKind | None = None,
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            container.pipeline_metrics.sse_clients.inc()
            try:
                yield "event: ready\ndata: {}\n\n"
                async for incident in container.event_bus.subscribe():
                    if provider is not None and incident.provider is not provider:
                        continue
                    if incident_status is not None and incident.status is not incident_status:
                        continue
                    if (
                        classification is not None
                        and incident.analysis.classification is not classification
                    ):
                        continue
                    yield f"event: incident\ndata: {incident.model_dump_json()}\n\n"
            except asyncio.CancelledError:
                raise
            finally:
                container.pipeline_metrics.sse_clients.dec()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/catalog", response_model=list[KnownErrorRule])
    async def list_catalog(
        request: Request, include_inactive: bool = False
    ) -> tuple[KnownErrorRule, ...]:
        _require_admin(request, resolved_settings.catalog_admin_token_value())
        return await container.catalog.list_rules(include_inactive=include_inactive)

    @app.get("/api/v1/catalog/audit", response_model=list[CatalogAuditEvent])
    async def list_catalog_audit(
        request: Request, limit: int = Query(default=100, ge=1, le=500)
    ) -> tuple[CatalogAuditEvent, ...]:
        _require_admin(request, resolved_settings.catalog_admin_token_value())
        return await container.catalog.list_catalog_audit(limit=limit)

    @app.post(
        "/api/v1/catalog",
        response_model=KnownErrorRule,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_catalog_rule(
        rule: KnownErrorRule, request: Request
    ) -> KnownErrorRule:
        _require_admin(request, resolved_settings.catalog_admin_token_value())
        return await container.catalog.create_version(rule)

    @app.put("/api/v1/catalog/{rule_id}", response_model=KnownErrorRule)
    async def update_catalog_rule(
        rule_id: str, rule: KnownErrorRule, request: Request
    ) -> KnownErrorRule:
        _require_admin(request, resolved_settings.catalog_admin_token_value())
        if rule.rule_id != rule_id:
            raise HTTPException(status_code=422, detail="rule_id must match path")
        return await container.catalog.create_version(rule)

    @app.post(
        "/api/v1/catalog/{rule_id}/versions/{version}/activate",
        response_model=KnownErrorRule,
    )
    async def activate_catalog_rule(
        rule_id: str, version: int, request: Request
    ) -> KnownErrorRule:
        _require_admin(request, resolved_settings.catalog_admin_token_value())
        rule = await container.catalog.activate(rule_id, version)
        if rule is None:
            raise HTTPException(status_code=404, detail="catalog rule version not found")
        return rule

    @app.delete(
        "/api/v1/catalog/{rule_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    async def deactivate_catalog_rule(rule_id: str, request: Request) -> Response:
        _require_admin(request, resolved_settings.catalog_admin_token_value())
        if not await container.catalog.deactivate(rule_id):
            raise HTTPException(status_code=404, detail="active catalog rule not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


def _require_network(
    request: Request,
    trusted_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
    *,
    enforce: bool = True,
) -> None:
    if not enforce:
        return
    if request.client is None:
        raise HTTPException(status_code=403, detail="ingress source is unavailable")
    try:
        client_ip = ipaddress.ip_address(request.client.host)
    except ValueError as error:
        raise HTTPException(status_code=403, detail="ingress source is invalid") from error
    if not any(client_ip in network for network in trusted_networks):
        raise HTTPException(status_code=403, detail="ingress source is not trusted")


def _require_bearer(request: Request, expected: str) -> None:
    authorization = request.headers.get("authorization", "")
    scheme, _, candidate = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(candidate, expected):
        raise HTTPException(
            status_code=401,
            detail="invalid integration credential",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _require_admin(request: Request, expected: str) -> None:
    candidate = request.headers.get("x-catalog-admin-token", "")
    if not secrets.compare_digest(candidate, expected):
        raise HTTPException(status_code=401, detail="invalid catalog admin credential")


app = create_app()
