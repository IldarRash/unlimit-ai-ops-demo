from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from apm_demo.common.contracts import PaymentMethod, PaymentOutcome, ProviderId
from apm_demo.incidents.domain import (
    IncidentConclusion,
    MetricSnapshot,
    OutcomeCounts,
    PaymentMethodBreakdown,
)


def test_metric_snapshot_accepts_bounded_provider_evidence() -> None:
    snapshot = MetricSnapshot(
        provider=ProviderId.ATLAS_PAY,
        window_seconds=300,
        observed_at=datetime.now(timezone.utc),
        total_requests=120,
        request_rate_per_second=0.4,
        success_rate=0.8,
        error_rate=0.15,
        timeout_rate=0.05,
        p95_latency_ms=1_650,
        health_up=True,
    )

    assert snapshot.provider is ProviderId.ATLAS_PAY
    assert snapshot.p95_latency_ms == 1_650


def test_metric_snapshot_rejects_impossible_outcome_distribution() -> None:
    with pytest.raises(ValidationError, match="cannot sum to more than 1"):
        MetricSnapshot(
            provider=ProviderId.ATLAS_PAY,
            window_seconds=300,
            total_requests=120,
            request_rate_per_second=0.4,
            success_rate=0.8,
            error_rate=0.2,
            timeout_rate=0.1,
            p95_latency_ms=1_650,
            health_up=True,
        )


def test_metric_snapshot_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        MetricSnapshot(
            provider=ProviderId.ATLAS_PAY,
            window_seconds=300,
            observed_at=datetime(2026, 1, 1),
            total_requests=1,
            request_rate_per_second=0.1,
            success_rate=1,
            error_rate=0,
            timeout_rate=0,
            p95_latency_ms=100,
            health_up=True,
        )


def test_metric_snapshot_validates_payment_method_counts() -> None:
    with pytest.raises(
        ValidationError,
        match="payment method counts must equal provider outcome counts",
    ):
        MetricSnapshot(
            provider=ProviderId.ATLAS_PAY,
            window_seconds=300,
            total_requests=10,
            outcome_counts=OutcomeCounts(success=8, provider_error=2),
            payment_method_breakdown=(
                PaymentMethodBreakdown(
                    payment_method=PaymentMethod.PIX,
                    counts=OutcomeCounts(success=8, provider_error=1),
                ),
            ),
            request_rate_per_second=0.03,
            success_rate=0.8,
            error_rate=0.2,
            timeout_rate=0,
            p95_latency_ms=200,
            health_up=True,
        )


def test_incident_conclusion_rejects_unverified_numeric_share() -> None:
    ended_at = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="affected share must match"):
        IncidentConclusion(
            statement="Provider failures are elevated.",
            window_started_at=ended_at - timedelta(minutes=5),
            window_ended_at=ended_at,
            window_seconds=300,
            affected_outcomes=(PaymentOutcome.PROVIDER_ERROR,),
            affected_requests=20,
            total_requests=100,
            affected_share=0.5,
            evidence_refs=("snapshot",),
            verification="verified",
        )
