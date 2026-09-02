from __future__ import annotations

from dataclasses import dataclass

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.api.config import IncidentSettings, MetricsMode
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
    ResponseCodeDefinition,
)
from apm_demo.incidents.infrastructure import (
    DeterministicMetricsSource,
    OpenAIIncidentAnalyzer,
    PrometheusMetricsSource,
    PostgresIncidentStore,
    SQLiteIncidentStore,
)
from apm_demo.incidents.ports.repositories import (
    AuditLog,
    ExternalSignalRepository,
    FeedbackRepository,
    IncidentRepository,
    KnownErrorCatalog,
    ProviderEventRepository,
    ResponseCodeCatalog,
)
from apm_demo.incidents.ports.analysis import IncidentAnalyzer

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
    external_signals: ExternalSignalRepository
    catalog: KnownErrorCatalog
    response_catalog: ResponseCodeCatalog
    feedback: FeedbackRepository
    event_bus: IncidentEventBus
    pipeline_metrics: PipelineMetrics
    analyzer: object
    closeables: tuple[object, ...]

    async def initialize(self) -> None:
        initialize = getattr(self.incidents, "initialize", None)
        if initialize is not None:
            await initialize()
        rules = await self.catalog.list_rules(include_inactive=True)
        default_rule = _default_known_error_rule()
        if not any(
            rule.rule_id == default_rule.rule_id
            and rule.version >= default_rule.version
            for rule in rules
        ):
            await self.catalog.create_version(default_rule)
        if not await self.response_catalog.list_response_code_definitions(
            include_inactive=True
        ):
            for definition in _default_response_code_definitions():
                await self.response_catalog.create_response_code_version(definition)

    async def aclose(self) -> None:
        for resource in self.closeables:
            close = getattr(resource, "aclose", None)
            if close:
                await close()


def build_container(
    settings: IncidentSettings, *, analyzer: IncidentAnalyzer | None = None
) -> IncidentContainer:
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
            warning_decline_rate=settings.warning_decline_rate,
            critical_decline_rate=settings.critical_decline_rate,
        )
    )
    if settings.metrics_mode is MetricsMode.PROMETHEUS:
        prometheus_auth = settings.prometheus_auth()
        metrics = PrometheusMetricsSource(
            settings.prometheus_url,
            timeout_seconds=settings.request_timeout_seconds,
            username=prometheus_auth[0] if prometheus_auth else None,
            password=prometheus_auth[1] if prometheus_auth else None,
        )
        metric_closeables: tuple[object, ...] = (metrics,)
    else:
        metrics = DeterministicMetricsSource(_demo_snapshots())
        metric_closeables = ()

    if analyzer is None:
        analyzer = OpenAIIncidentAnalyzer(
            settings.openai_api_key_value(),
            model=settings.openai_model,
            requests_enabled=settings.openai_requests_enabled,
            timeout_seconds=settings.llm_timeout_seconds,
            failure_threshold=settings.llm_failure_threshold,
            circuit_reset_seconds=settings.llm_circuit_reset_seconds,
        )
        analyzer_closeables: tuple[object, ...] = (analyzer,)
    else:
        analyzer_closeables = ()

    classifier = IncidentClassifier(
        catalog=store, response_catalog=store, analyzer=analyzer
    )
    service = AnalyzeProviderIncident(
        metrics=metrics,
        detector=detector,
        classifier=classifier,
        incidents=store,
        audit_log=store,
        provider_events=store,
        external_signals=store,
        event_limit=settings.provider_event_limit,
        external_signal_limit=settings.external_signal_limit,
    )
    alert_pipeline = AlertIncidentPipeline(
        metrics=metrics,
        detector=detector,
        classifier=classifier,
        incidents=store,
        provider_events=store,
        external_signals=store,
        deliveries=store,
        events=event_bus,
        event_limit=settings.provider_event_limit,
        external_signal_limit=settings.external_signal_limit,
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
        external_signals=store,
        catalog=store,
        response_catalog=store,
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
        version=2,
        provider=ProviderId.ATLAS_PAY,
        response_code="UPSTREAM_ERROR",
        outcome=PaymentOutcome.PROVIDER_ERROR,
        headline="AtlasPay upstream processing error",
        summary=(
            "AtlasPay returned the known UPSTREAM_ERROR response. "
            "The response matches the reviewed upstream processing rule."
        ),
        impact="AtlasPay payment attempts can fail until the upstream condition clears.",
        operator_decision=(
            "Action required: verify AtlasPay status and the affected routing exposure."
        ),
        probable_causes=("Known AtlasPay upstream processing degradation",),
        recommended_actions=(
            RemediationAction(
                priority=1,
                title="Check AtlasPay status and routing exposure",
                rationale="Validate provider status and routing exposure before any mitigation.",
                safe_to_automate=False,
            ),
        ),
        confidence=0.98,
    )


def _default_response_code_definitions() -> tuple[ResponseCodeDefinition, ...]:
    return (
        ResponseCodeDefinition(
            definition_id="approved",
            response_code="APPROVED",
            name="Approved payment",
            description=(
                "The provider accepted the payment attempt and returned a "
                "successful response."
            ),
        ),
        ResponseCodeDefinition(
            definition_id="do-not-honor",
            response_code="DO_NOT_HONOR",
            name="Issuer declined",
            description=(
                "The payment was declined without a more specific issuer reason; "
                "operator action should focus on aggregate patterns rather than "
                "retrying an individual payment."
            ),
        ),
        ResponseCodeDefinition(
            definition_id="invalid-account",
            response_code="INVALID_ACCOUNT",
            name="Invalid account details",
            description=(
                "The provider reports that the submitted account or payment "
                "credentials are not valid for processing."
            ),
        ),
        ResponseCodeDefinition(
            definition_id="provider-timeout",
            response_code="PROVIDER_TIMEOUT",
            name="Provider timeout",
            description=(
                "The provider did not return a payment response before the "
                "configured client timeout."
            ),
        ),
        ResponseCodeDefinition(
            definition_id="transport-error",
            response_code="TRANSPORT_ERROR",
            name="Transport failure",
            description=(
                "The client could not complete the network exchange with the provider."
            ),
        ),
        ResponseCodeDefinition(
            definition_id="atlas-upstream-error",
            provider=ProviderId.ATLAS_PAY,
            response_code="UPSTREAM_ERROR",
            name="Upstream processing error",
            description=(
                "AtlasPay could not complete processing because its upstream payment "
                "system returned a provider-side failure."
            ),
        ),
    )
