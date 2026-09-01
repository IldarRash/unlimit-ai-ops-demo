from __future__ import annotations

from dataclasses import dataclass

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.api.config import AnalyzerMode, IncidentSettings, MetricsMode
from apm_demo.incidents.application.detection import AnomalyDetector, DetectionThresholds
from apm_demo.incidents.application.classification import IncidentClassifier
from apm_demo.incidents.application.events import IncidentEventBus
from apm_demo.incidents.application.pipeline import AlertIncidentPipeline
from apm_demo.incidents.application.service import (
    AnalyzeProviderIncident,
    IncidentLifecycle,
)
from apm_demo.common.contracts import PaymentOutcome
from apm_demo.incidents.domain import (
    KnownErrorRule,
    MetricSnapshot,
    RemediationAction,
)
from apm_demo.incidents.infrastructure import (
    DeterministicMetricsSource,
    MockIncidentAnalyzer,
    OpenAIIncidentAnalyzer,
    PrometheusMetricsSource,
    PostgresIncidentStore,
    SQLiteIncidentStore,
)
from apm_demo.incidents.ports.repositories import (
    AuditLog,
    FeedbackRepository,
    IncidentRepository,
    KnownErrorCatalog,
    ProviderEventRepository,
)

from apm_demo.incidents.infrastructure.observability import PipelineMetrics


@dataclass
class IncidentContainer:
    settings: IncidentSettings
    analyze_incident: AnalyzeProviderIncident
    alert_pipeline: AlertIncidentPipeline
    lifecycle: IncidentLifecycle
    incidents: IncidentRepository
    audit_log: AuditLog
    provider_events: ProviderEventRepository
    catalog: KnownErrorCatalog
    feedback: FeedbackRepository
    event_bus: IncidentEventBus
    pipeline_metrics: PipelineMetrics
    analyzer: object
    closeables: tuple[object, ...]

    async def initialize(self) -> None:
        initialize = getattr(self.incidents, "initialize", None)
        if initialize is not None:
            await initialize()
        if not await self.catalog.list_rules(include_inactive=True):
            await self.catalog.create_version(_default_known_error_rule())

    async def aclose(self) -> None:
        for resource in self.closeables:
            close = getattr(resource, "aclose", None)
            if close:
                await close()


def build_container(settings: IncidentSettings) -> IncidentContainer:
    database_url = settings.database_url_value()
    store: SQLiteIncidentStore | PostgresIncidentStore
    store = (
        PostgresIncidentStore(database_url)
        if database_url is not None
        else SQLiteIncidentStore(settings.database_path)
    )
    event_bus = IncidentEventBus()
    pipeline_metrics = PipelineMetrics()
    detector = AnomalyDetector(
        DetectionThresholds(
            minimum_requests=settings.minimum_requests,
            warning_p95_latency_ms=settings.warning_p95_latency_ms,
            critical_p95_latency_ms=settings.critical_p95_latency_ms,
            warning_error_rate=settings.warning_error_rate,
            critical_error_rate=settings.critical_error_rate,
            warning_timeout_rate=settings.warning_timeout_rate,
            critical_timeout_rate=settings.critical_timeout_rate,
        )
    )
    if settings.metrics_mode is MetricsMode.PROMETHEUS:
        metrics = PrometheusMetricsSource(
            settings.prometheus_url,
            timeout_seconds=settings.request_timeout_seconds,
        )
        metric_closeables: tuple[object, ...] = (metrics,)
    else:
        metrics = DeterministicMetricsSource(_demo_snapshots())
        metric_closeables = ()

    if settings.analyzer_mode is AnalyzerMode.OPENAI:
        assert settings.openai_api_key is not None
        analyzer = OpenAIIncidentAnalyzer(
            settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            timeout_seconds=settings.request_timeout_seconds,
            failure_threshold=settings.llm_failure_threshold,
            circuit_reset_seconds=settings.llm_circuit_reset_seconds,
        )
        analyzer_closeables: tuple[object, ...] = (analyzer,)
    else:
        analyzer = MockIncidentAnalyzer()
        analyzer_closeables = ()

    service = AnalyzeProviderIncident(
        metrics=metrics,
        detector=detector,
        analyzer=analyzer,
        incidents=store,
        audit_log=store,
    )
    classifier = IncidentClassifier(catalog=store, analyzer=analyzer)
    alert_pipeline = AlertIncidentPipeline(
        metrics=metrics,
        detector=detector,
        classifier=classifier,
        incidents=store,
        provider_events=store,
        deliveries=store,
        events=event_bus,
        event_limit=settings.provider_event_limit,
        window_seconds=settings.analysis_window_seconds,
    )
    return IncidentContainer(
        settings=settings,
        analyze_incident=service,
        alert_pipeline=alert_pipeline,
        lifecycle=IncidentLifecycle(incidents=store, audit_log=store),
        incidents=store,
        audit_log=store,
        provider_events=store,
        catalog=store,
        feedback=store,
        event_bus=event_bus,
        pipeline_metrics=pipeline_metrics,
        analyzer=analyzer,
        closeables=(store,) + metric_closeables + analyzer_closeables,
    )


def _demo_snapshots() -> dict[ProviderId, MetricSnapshot]:
    base = {
        "window_seconds": 300,
        "total_requests": 180,
        "request_rate_per_second": 0.6,
    }
    return {
        ProviderId.ATLAS_PAY: MetricSnapshot(
            provider=ProviderId.ATLAS_PAY,
            success_rate=0.78,
            error_rate=0.16,
            timeout_rate=0.06,
            p95_latency_ms=1_840,
            health_up=True,
            **base,
        ),
        ProviderId.NOVA_BANK: MetricSnapshot(
            provider=ProviderId.NOVA_BANK,
            success_rate=0.985,
            error_rate=0.01,
            timeout_rate=0.005,
            p95_latency_ms=240,
            health_up=True,
            **base,
        ),
        ProviderId.ORBIT_WALLET: MetricSnapshot(
            provider=ProviderId.ORBIT_WALLET,
            success_rate=0.89,
            error_rate=0.04,
            timeout_rate=0.07,
            p95_latency_ms=920,
            health_up=False,
            **base,
        ),
    }


def _default_known_error_rule() -> KnownErrorRule:
    return KnownErrorRule(
        rule_id="atlas-upstream-error",
        provider=ProviderId.ATLAS_PAY,
        response_code="UPSTREAM_ERROR",
        outcome=PaymentOutcome.PROVIDER_ERROR,
        headline="AtlasPay upstream processing error",
        summary=(
            "AtlasPay returned the known UPSTREAM_ERROR response. "
            "Use the provider incident runbook; no LLM analysis is required."
        ),
        impact="AtlasPay payment attempts can fail until the upstream condition clears.",
        probable_causes=("Known AtlasPay upstream processing degradation",),
        recommended_actions=(
            RemediationAction(
                priority=1,
                title="Follow the AtlasPay upstream error runbook",
                rationale="Validate provider status and routing exposure before any mitigation.",
                safe_to_automate=False,
            ),
        ),
        confidence=0.98,
        runbook_url="https://example.invalid/runbooks/atlas-upstream-error",
    )
