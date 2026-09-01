from __future__ import annotations

from enum import StrEnum
from math import isclose
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderId(StrEnum):
    ATLAS_PAY = "atlas-pay"
    NOVA_BANK = "nova-bank"
    ORBIT_WALLET = "orbit-wallet"


class PaymentMethod(StrEnum):
    PIX = "pix"
    IDEAL = "ideal"
    KLARNA = "klarna"
    LOCAL_WALLET = "local-wallet"


class PaymentOutcome(StrEnum):
    SUCCESS = "success"
    SOFT_DECLINE = "soft-decline"
    HARD_DECLINE = "hard-decline"
    PROVIDER_ERROR = "provider-error"
    TIMEOUT = "timeout"


class HealthMode(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    TIMEOUT = "timeout"


class ScenarioName(StrEnum):
    NORMAL = "normal"
    SLOW_PROVIDER = "slow-provider"
    PROVIDER_ERRORS = "provider-errors"
    PROVIDER_TIMEOUT = "provider-timeout"
    HEALTHCHECK_DOWN = "healthcheck-down"
    HEALTHCHECK_TIMEOUT = "healthcheck-timeout"
    RECOVER = "recover"


Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class ProviderBehavior(BaseModel):
    """Runtime behavior of one synthetic payment provider.

    Timeout probability is evaluated first. The remaining response probabilities
    describe the distribution only for requests that do return a response.
    """

    model_config = ConfigDict(extra="forbid")

    base_latency_ms: int = Field(default=150, ge=0, le=60_000)
    jitter_ms: int = Field(default=50, ge=0, le=30_000)
    timeout_rate: Probability = 0.01
    timeout_delay_ms: int = Field(default=5_000, ge=1, le=120_000)
    health_mode: HealthMode = HealthMode.HEALTHY
    health_latency_ms: int = Field(default=25, ge=0, le=60_000)
    success_rate: Probability = 0.94
    soft_decline_rate: Probability = 0.03
    hard_decline_rate: Probability = 0.01
    provider_error_rate: Probability = 0.02

    @model_validator(mode="after")
    def validate_response_distribution(self) -> "ProviderBehavior":
        total = (
            self.success_rate
            + self.soft_decline_rate
            + self.hard_decline_rate
            + self.provider_error_rate
        )
        if not isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(
                "non-timeout response probabilities must sum to 1.0; "
                f"received {total:.6f}"
            )
        return self


class ProviderBehaviorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_latency_ms: int | None = Field(default=None, ge=0, le=60_000)
    jitter_ms: int | None = Field(default=None, ge=0, le=30_000)
    timeout_rate: Probability | None = None
    timeout_delay_ms: int | None = Field(default=None, ge=1, le=120_000)
    health_mode: HealthMode | None = None
    health_latency_ms: int | None = Field(default=None, ge=0, le=60_000)
    success_rate: Probability | None = None
    soft_decline_rate: Probability | None = None
    hard_decline_rate: Probability | None = None
    provider_error_rate: Probability | None = None

    def apply_to(self, behavior: ProviderBehavior) -> ProviderBehavior:
        return ProviderBehavior.model_validate(
            behavior.model_copy(update=self.model_dump(exclude_none=True)).model_dump()
        )


class ProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    display_name: str
    supported_methods: tuple[PaymentMethod, ...]
    traffic_weight: Probability


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1, max_length=80)
    merchant: str = Field(min_length=1, max_length=80)
    payment_method: PaymentMethod
    region: str = Field(pattern=r"^[A-Z]{2}$")


class PaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    provider: ProviderId
    outcome: PaymentOutcome
    response_code: str
    processing_time_ms: int = Field(ge=0)


class ProviderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    definition: ProviderDefinition
    behavior: ProviderBehavior


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId = ProviderId.ATLAS_PAY


class ScenarioApplied(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioName
    provider: ProviderId
    behavior: ProviderBehavior


PROVIDER_DEFINITIONS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        provider=ProviderId.ATLAS_PAY,
        display_name="AtlasPay",
        supported_methods=(PaymentMethod.PIX, PaymentMethod.IDEAL),
        traffic_weight=0.50,
    ),
    ProviderDefinition(
        provider=ProviderId.NOVA_BANK,
        display_name="NovaBank",
        supported_methods=(PaymentMethod.IDEAL, PaymentMethod.KLARNA),
        traffic_weight=0.30,
    ),
    ProviderDefinition(
        provider=ProviderId.ORBIT_WALLET,
        display_name="OrbitWallet",
        supported_methods=(PaymentMethod.PIX, PaymentMethod.LOCAL_WALLET),
        traffic_weight=0.20,
    ),
)

PROVIDER_BY_ID = {definition.provider: definition for definition in PROVIDER_DEFINITIONS}

# Every label has a deliberately bounded vocabulary. Transaction and merchant IDs
# are never Prometheus labels because they would create unbounded cardinality.
CLIENT_METRIC_LABELS = ("provider", "payment_method", "outcome")
PROVIDER_METRIC_LABELS = ("provider", "outcome")


def select_non_timeout_outcome(
    behavior: ProviderBehavior, random_value: float
) -> PaymentOutcome:
    """Select a returning response from a stable [0, 1) random value."""

    boundaries = (
        (behavior.success_rate, PaymentOutcome.SUCCESS),
        (
            behavior.success_rate + behavior.soft_decline_rate,
            PaymentOutcome.SOFT_DECLINE,
        ),
        (
            behavior.success_rate
            + behavior.soft_decline_rate
            + behavior.hard_decline_rate,
            PaymentOutcome.HARD_DECLINE,
        ),
    )
    for boundary, outcome in boundaries:
        if random_value < boundary:
            return outcome
    return PaymentOutcome.PROVIDER_ERROR
