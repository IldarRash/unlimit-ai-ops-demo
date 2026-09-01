from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.application.detection import (
    AnomalyDetector,
    incident_fingerprint,
    incident_severity,
)
from apm_demo.incidents.domain import IncidentSeverity, MetricSnapshot, SignalType


def snapshot(**updates: object) -> MetricSnapshot:
    values = {
        "provider": ProviderId.ATLAS_PAY,
        "window_seconds": 300,
        "total_requests": 120,
        "request_rate_per_second": 0.4,
        "success_rate": 0.8,
        "error_rate": 0.15,
        "timeout_rate": 0.05,
        "p95_latency_ms": 1_650,
        "health_up": True,
    }
    values.update(updates)
    return MetricSnapshot.model_validate(values)


def test_detector_emits_bounded_signals_and_highest_severity() -> None:
    signals = AnomalyDetector().detect(snapshot())

    assert {signal.signal_type for signal in signals} == {
        SignalType.LATENCY,
        SignalType.ERROR_RATE,
        SignalType.TIMEOUT_RATE,
    }
    assert incident_severity(signals) is IncidentSeverity.CRITICAL


def test_health_failure_bypasses_minimum_sample_guard() -> None:
    signals = AnomalyDetector().detect(
        snapshot(
            total_requests=0,
            request_rate_per_second=0,
            success_rate=0,
            error_rate=0,
            timeout_rate=0,
            p95_latency_ms=0,
            health_up=False,
        )
    )

    assert len(signals) == 1
    assert signals[0].signal_type is SignalType.HEALTH
    assert signals[0].severity is IncidentSeverity.CRITICAL


def test_fingerprint_is_stable_across_metric_value_changes() -> None:
    detector = AnomalyDetector()
    first = snapshot(p95_latency_ms=1_600)
    second = snapshot(p95_latency_ms=2_400)

    assert incident_fingerprint(first, detector.detect(first)) == incident_fingerprint(
        second, detector.detect(second)
    )
