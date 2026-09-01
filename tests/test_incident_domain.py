from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.domain import MetricSnapshot


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
