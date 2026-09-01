import pytest
from pydantic import ValidationError

from apm_demo.common.contracts import (
    CLIENT_METRIC_LABELS,
    HealthMode,
    PaymentOutcome,
    ProviderBehavior,
    ProviderBehaviorPatch,
    ProviderId,
    select_non_timeout_outcome,
)


def test_response_probabilities_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        ProviderBehavior(
            success_rate=0.5,
            soft_decline_rate=0.1,
            hard_decline_rate=0.1,
            provider_error_rate=0.1,
        )


def test_partial_patch_is_revalidated_as_complete_behavior() -> None:
    original = ProviderBehavior()
    patch = ProviderBehaviorPatch(
        health_mode=HealthMode.TIMEOUT,
        timeout_rate=0.25,
        base_latency_ms=1_250,
    )

    updated = patch.apply_to(original)

    assert updated.health_mode is HealthMode.TIMEOUT
    assert updated.timeout_rate == 0.25
    assert updated.base_latency_ms == 1_250
    assert updated.success_rate == original.success_rate


@pytest.mark.parametrize(
    ("random_value", "expected"),
    [
        (0.00, PaymentOutcome.SUCCESS),
        (0.939, PaymentOutcome.SUCCESS),
        (0.945, PaymentOutcome.SOFT_DECLINE),
        (0.975, PaymentOutcome.HARD_DECLINE),
        (0.999, PaymentOutcome.PROVIDER_ERROR),
    ],
)
def test_non_timeout_outcome_selection(
    random_value: float, expected: PaymentOutcome
) -> None:
    assert select_non_timeout_outcome(ProviderBehavior(), random_value) is expected


def test_prometheus_labels_exclude_unbounded_identifiers() -> None:
    assert CLIENT_METRIC_LABELS == ("provider", "payment_method", "outcome")
    assert "transaction_id" not in CLIENT_METRIC_LABELS
    assert "merchant" not in CLIENT_METRIC_LABELS
    assert ProviderId.ATLAS_PAY.value == "atlas-pay"
