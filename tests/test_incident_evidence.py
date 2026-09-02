from datetime import datetime, timedelta, timezone

from apm_demo.common.contracts import PaymentOutcome, ProviderId
from apm_demo.incidents.application.detection import AnomalyDetector
from apm_demo.incidents.application.evidence import select_incident_provider_events
from apm_demo.incidents.domain import MetricSnapshot, ProviderEvent


OBSERVED_AT = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)


def snapshot(**updates: object) -> MetricSnapshot:
    values = {
        "provider": ProviderId.ATLAS_PAY,
        "observed_at": OBSERVED_AT,
        "window_seconds": 300,
        "total_requests": 100,
        "request_rate_per_second": 1,
        "success_rate": 0.8,
        "error_rate": 0.2,
        "timeout_rate": 0,
        "p95_latency_ms": 200,
        "health_up": True,
    }
    values.update(updates)
    return MetricSnapshot.model_validate(values)


def event(
    event_id: str,
    outcome: PaymentOutcome,
    *,
    age_seconds: int = 30,
) -> ProviderEvent:
    return ProviderEvent(
        event_id=event_id,
        provider=ProviderId.ATLAS_PAY,
        observed_at=OBSERVED_AT - timedelta(seconds=age_seconds),
        outcome=outcome,
        response_code=event_id.upper(),
        http_status=502,
        processing_time_ms=100,
    )


def test_response_evidence_matches_signal_and_metric_window() -> None:
    current = snapshot()
    signals = AnomalyDetector().detect(current)

    selected = select_incident_provider_events(
        (
            event("relevant", PaymentOutcome.PROVIDER_ERROR),
            event("business_background", PaymentOutcome.SOFT_DECLINE),
            event("outside_window", PaymentOutcome.PROVIDER_ERROR, age_seconds=301),
        ),
        snapshot=current,
        signals=signals,
        collected_at=OBSERVED_AT,
        limit=20,
    )

    assert tuple(item.event_id for item in selected) == ("relevant",)


def test_health_incident_does_not_claim_payment_response_codes_as_its_cause() -> None:
    current = snapshot(
        success_rate=0.99,
        error_rate=0.01,
        health_up=False,
    )

    selected = select_incident_provider_events(
        (event("unrelated_error", PaymentOutcome.PROVIDER_ERROR),),
        snapshot=current,
        signals=AnomalyDetector().detect(current),
        collected_at=OBSERVED_AT,
        limit=20,
    )

    assert selected == ()
