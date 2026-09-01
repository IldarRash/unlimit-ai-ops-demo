import pytest

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.application.detection import AnomalyDetector
from apm_demo.incidents.application.service import AnalyzeProviderIncident
from apm_demo.incidents.domain import AuditEventType, MetricSnapshot
from apm_demo.incidents.infrastructure import (
    DeterministicMetricsSource,
    InMemoryAuditLog,
    InMemoryIncidentRepository,
    MockIncidentAnalyzer,
)


def degraded_snapshot() -> MetricSnapshot:
    return MetricSnapshot(
        provider=ProviderId.ATLAS_PAY,
        window_seconds=300,
        total_requests=120,
        request_rate_per_second=0.4,
        success_rate=0.8,
        error_rate=0.15,
        timeout_rate=0.05,
        p95_latency_ms=1_650,
        health_up=True,
    )


@pytest.mark.asyncio
async def test_service_correlates_repeated_incident_and_records_audit() -> None:
    repository = InMemoryIncidentRepository()
    audit_log = InMemoryAuditLog()
    service = AnalyzeProviderIncident(
        metrics=DeterministicMetricsSource(
            {ProviderId.ATLAS_PAY: degraded_snapshot()}
        ),
        detector=AnomalyDetector(),
        analyzer=MockIncidentAnalyzer(),
        incidents=repository,
        audit_log=audit_log,
    )

    first = await service.execute(ProviderId.ATLAS_PAY)
    second = await service.execute(ProviderId.ATLAS_PAY)

    assert first is not None
    assert second is not None
    assert second.incident_id == first.incident_id
    assert second.occurrences == 2
    assert len(await repository.list_recent()) == 1
    assert [event.event_type for event in await audit_log.list_for_incident(first.incident_id)] == [
        AuditEventType.CREATED,
        AuditEventType.CORRELATED,
    ]


@pytest.mark.asyncio
async def test_service_skips_analysis_when_no_signal_is_detected() -> None:
    healthy = degraded_snapshot().model_copy(
        update={
            "success_rate": 0.99,
            "error_rate": 0.005,
            "timeout_rate": 0.005,
            "p95_latency_ms": 180,
        }
    )
    repository = InMemoryIncidentRepository()
    service = AnalyzeProviderIncident(
        metrics=DeterministicMetricsSource({ProviderId.ATLAS_PAY: healthy}),
        detector=AnomalyDetector(),
        analyzer=MockIncidentAnalyzer(),
        incidents=repository,
        audit_log=InMemoryAuditLog(),
    )

    assert await service.execute(ProviderId.ATLAS_PAY) is None
    assert await repository.list_recent() == ()
