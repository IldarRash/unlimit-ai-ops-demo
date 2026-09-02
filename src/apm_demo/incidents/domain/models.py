from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from apm_demo.common.contracts import PaymentMethod, PaymentOutcome, ProviderId


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class SignalType(StrEnum):
    LATENCY = "latency"
    ERROR_RATE = "error-rate"
    TIMEOUT_RATE = "timeout-rate"
    DECLINE_RATE = "decline-rate"
    HEALTH = "health"


class AnalysisProvider(StrEnum):
    CATALOG = "catalog"
    OPENAI = "openai"
    UNAVAILABLE = "unavailable"


class ClassificationKind(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class AlertDeliveryStatus(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class FeedbackVerdict(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not-helpful"
    INCORRECT = "incorrect"


class CatalogAuditAction(StrEnum):
    VERSION_CREATED = "version-created"
    VERSION_ACTIVATED = "version-activated"
    RULE_DEACTIVATED = "rule-deactivated"


class AuditEventType(StrEnum):
    CREATED = "created"
    CORRELATED = "correlated"
    ESCALATED = "escalated"
    REOPENED = "reopened"
    RESOLVED = "resolved"
    DELIVERY_REPLAYED = "delivery-replayed"
    FEEDBACK_RECORDED = "feedback-recorded"
    STATUS_CHANGED = "status-changed"


class AlertmanagerAlert(BaseModel):
    """Validated subset of Alertmanager's webhook v4 alert object."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    status: AlertDeliveryStatus
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str = Field(default="", alias="generatorURL", max_length=2_048)
    fingerprint: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "AlertmanagerAlert":
        if self.starts_at.tzinfo is None:
            raise ValueError("startsAt must be timezone-aware")
        if self.ends_at is not None and self.ends_at.tzinfo is None:
            raise ValueError("endsAt must be timezone-aware")
        return self


class AlertmanagerWebhook(BaseModel):
    """Strict Alertmanager webhook envelope accepted by the ingress."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    version: Literal["4"]
    group_key: str = Field(alias="groupKey", min_length=1, max_length=1_024)
    truncated_alerts: int = Field(alias="truncatedAlerts", ge=0)
    status: AlertDeliveryStatus
    receiver: str = Field(min_length=1, max_length=256)
    group_labels: dict[str, str] = Field(alias="groupLabels", default_factory=dict)
    common_labels: dict[str, str] = Field(alias="commonLabels", default_factory=dict)
    common_annotations: dict[str, str] = Field(
        alias="commonAnnotations", default_factory=dict
    )
    external_url: str = Field(alias="externalURL", default="", max_length=2_048)
    alerts: tuple[AlertmanagerAlert, ...] = Field(min_length=1, max_length=100)


class ProviderEvent(BaseModel):
    """Allowlisted provider evidence. Raw transactions are never represented here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=80)
    provider: ProviderId
    observed_at: datetime = Field(default_factory=utc_now)
    outcome: PaymentOutcome
    response_code: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9_.-]+$")
    http_status: int | None = Field(default=None, ge=100, le=599)
    processing_time_ms: int = Field(ge=0, le=120_000)
    payment_method: PaymentMethod | None = None
    region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")

    @model_validator(mode="after")
    def validate_observed_at(self) -> "ProviderEvent":
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return self


class KnownErrorRule(BaseModel):
    """Versioned deterministic response for a known provider failure."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_-]+$")
    version: int = Field(default=1, ge=1)
    provider: ProviderId
    response_code: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9_.-]+$")
    outcome: PaymentOutcome | None = None
    payment_method: PaymentMethod | None = None
    region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    headline: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1_000)
    impact: str = Field(min_length=1, max_length=600)
    probable_causes: tuple[str, ...] = Field(min_length=1, max_length=5)
    recommended_actions: tuple["RemediationAction", ...] = Field(
        min_length=1, max_length=5
    )
    confidence: float = Field(ge=0, le=1)
    runbook_url: HttpUrl | None = None
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)

    def matches(self, event: ProviderEvent) -> bool:
        return (
            self.active
            and self.provider is event.provider
            and self.response_code == event.response_code
            and (self.outcome is None or self.outcome is event.outcome)
            and (
                self.payment_method is None
                or self.payment_method is event.payment_method
            )
            and (self.region is None or self.region == event.region)
        )

    @property
    def specificity(self) -> int:
        return sum(
            value is not None
            for value in (self.outcome, self.payment_method, self.region)
        )


class AlertEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_key: str = Field(min_length=1, max_length=1_024)
    fingerprint: str = Field(min_length=1, max_length=256)
    alert_name: str = Field(min_length=1, max_length=160)
    status: AlertDeliveryStatus
    severity: str | None = Field(default=None, max_length=32)
    truncated: bool = False


class MetricSnapshot(BaseModel):
    """Bounded, provider-level evidence collected for one analysis window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    available: bool = True
    collection_error: str | None = Field(default=None, max_length=120)
    window_seconds: int = Field(ge=15, le=3_600)
    observed_at: datetime = Field(default_factory=utc_now)
    total_requests: int = Field(ge=0)
    request_rate_per_second: float = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    error_rate: float = Field(ge=0, le=1)
    timeout_rate: float = Field(ge=0, le=1)
    p95_latency_ms: float = Field(ge=0)
    health_up: bool

    @model_validator(mode="after")
    def validate_outcome_rates(self) -> "MetricSnapshot":
        if self.success_rate + self.error_rate + self.timeout_rate > 1.000_001:
            raise ValueError(
                "success, error, and timeout rates cannot sum to more than 1"
            )
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.available and self.collection_error is not None:
            raise ValueError("available metrics cannot contain collection_error")
        if not self.available and not self.collection_error:
            raise ValueError("unavailable metrics require collection_error")
        return self


class AlertSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_type: SignalType
    severity: IncidentSeverity
    actual_value: float
    threshold_value: float
    unit: str = Field(min_length=1, max_length=24)
    description: str = Field(min_length=1, max_length=240)


class EvidenceBundle(BaseModel):
    """Only normalized evidence is passed to an LLM; raw payloads stay outside."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot: MetricSnapshot
    signals: tuple[AlertSignal, ...] = Field(min_length=1, max_length=8)
    alert: AlertEvidence | None = None
    provider_events: tuple[ProviderEvent, ...] = Field(default=(), max_length=20)
    source: str = Field(min_length=1, max_length=48)
    collected_at: datetime = Field(default_factory=utc_now)


class RemediationAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    priority: int = Field(ge=1, le=5)
    title: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=500)
    safe_to_automate: bool = False


class IncidentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    headline: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1_000)
    impact: str = Field(min_length=1, max_length=600)
    probable_causes: tuple[str, ...] = Field(min_length=1, max_length=5)
    # Empty is accepted only when loading historical stored records. New analyzers
    # and deterministic classifications always provide causal hypotheses.
    causes: tuple["CauseHypothesis", ...] = Field(default=(), max_length=5)
    recommended_actions: tuple[RemediationAction, ...] = Field(
        min_length=1, max_length=5
    )
    confidence: float = Field(ge=0, le=1)
    generated_by: AnalysisProvider
    model: str = Field(min_length=1, max_length=80)
    classification: ClassificationKind = ClassificationKind.UNKNOWN
    catalog_rule_id: str | None = Field(default=None, max_length=80)
    runbook_url: HttpUrl | None = None
    prompt_version: str | None = Field(default=None, max_length=40)
    request_id: str | None = Field(default=None, max_length=160)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    generated_at: datetime = Field(default_factory=utc_now)


class CauseHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal["business", "technical"]
    title: str = Field(min_length=1, max_length=160)
    why: str = Field(min_length=1, max_length=600)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=24)


class IncidentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1, max_length=80)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: ProviderId
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.OPEN
    evidence: EvidenceBundle
    analysis: IncidentAnalysis
    source_alert_fingerprint: str | None = Field(default=None, max_length=256)
    alert_group_key: str | None = Field(default=None, max_length=1_024)
    occurrences: int = Field(default=1, ge=1)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timeline(self) -> "IncidentRecord":
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at cannot be before first_seen_at")
        return self


class IncidentFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_id: str = Field(min_length=1, max_length=80)
    incident_id: str = Field(min_length=1, max_length=80)
    verdict: FeedbackVerdict
    note: str | None = Field(default=None, max_length=1_000)
    created_at: datetime = Field(default_factory=utc_now)


class CatalogAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=80)
    rule_id: str = Field(min_length=1, max_length=80)
    version: int | None = Field(default=None, ge=1)
    action: CatalogAuditAction
    occurred_at: datetime = Field(default_factory=utc_now)


class AlertIngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delivery_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted: int = Field(ge=0)
    replayed: bool = False
    incident_ids: tuple[str, ...] = ()
    truncated: bool = False


class IncidentAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=80)
    incident_id: str = Field(min_length=1, max_length=80)
    event_type: AuditEventType
    occurred_at: datetime = Field(default_factory=utc_now)
    details: dict[str, str] = Field(default_factory=dict)
