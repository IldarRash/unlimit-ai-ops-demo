from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apm_demo.common.contracts import PaymentOutcome, ProviderId
from apm_demo.incidents.application.classification import IncidentClassifier
from apm_demo.incidents.application.detection import AnomalyDetector
from apm_demo.incidents.application.events import IncidentEventBus
from apm_demo.incidents.application.pipeline import AlertIncidentPipeline
from apm_demo.incidents.domain import (
    AlertDeliveryStatus,
    AlertmanagerAlert,
    AlertmanagerWebhook,
    AnalysisProvider,
    ClassificationKind,
    EvidenceBundle,
    KnownErrorRule,
    MetricSnapshot,
    ProviderEvent,
    RemediationAction,
    ResponseInsightSource,
    CauseHypothesis,
    IncidentAnalysis,
)
from apm_demo.incidents.infrastructure import (
    DeterministicMetricsSource,
    SQLiteIncidentStore,
)


START = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


class CountingAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, evidence: EvidenceBundle):  # type: ignore[no-untyped-def]
        self.calls += 1
        return IncidentAnalysis(
            headline="Unknown provider degradation",
            summary="Synthetic local analysis.",
            impact="Payments may fail.",
            probable_causes=("Unknown provider condition",),
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="Unknown provider condition",
                    why="The provider event is not present in the known-error catalog.",
                    evidence_refs=(f"event:{evidence.provider_events[0].event_id}",),
                ),
            ),
            recommended_actions=(
                RemediationAction(
                    priority=1,
                    title="Inspect normalized evidence",
                    rationale="Validate the unknown response before mitigation.",
                ),
            ),
            confidence=0.7,
            generated_by=AnalysisProvider.OPENAI,
            model="fake-openai",
        )


def snapshot() -> MetricSnapshot:
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


def webhook(
    status: AlertDeliveryStatus = AlertDeliveryStatus.FIRING,
    *,
    starts_at: datetime = START,
) -> AlertmanagerWebhook:
    return AlertmanagerWebhook(
        version="4",
        group_key='{}:{provider="atlas-pay"}',
        truncated_alerts=0,
        status=status,
        receiver="incident-intelligence",
        group_labels={"provider": "atlas-pay"},
        common_labels={"provider": "atlas-pay"},
        common_annotations={},
        external_url="http://alertmanager.test",
        alerts=(
            AlertmanagerAlert(
                status=status,
                labels={
                    "alertname": "ProviderErrorRateHigh",
                    "provider": "atlas-pay",
                    "severity": "critical",
                },
                annotations={"summary": "AtlasPay error rate is elevated"},
                starts_at=starts_at,
                ends_at=(START + timedelta(minutes=10))
                if status is AlertDeliveryStatus.RESOLVED
                else None,
                generator_url="http://prometheus.test/graph",
                fingerprint="alert-fingerprint-1",
            ),
        ),
    )


def pipeline(
    store: SQLiteIncidentStore, analyzer: CountingAnalyzer
) -> AlertIncidentPipeline:
    return AlertIncidentPipeline(
        metrics=DeterministicMetricsSource({ProviderId.ATLAS_PAY: snapshot()}),
        detector=AnomalyDetector(),
        classifier=IncidentClassifier(catalog=store, analyzer=analyzer),
        incidents=store,
        provider_events=store,
        external_signals=store,
        deliveries=store,
        events=IncidentEventBus(),
    )


def known_rule() -> KnownErrorRule:
    return KnownErrorRule(
        rule_id="atlas-upstream-error",
        provider=ProviderId.ATLAS_PAY,
        response_code="UPSTREAM_ERROR",
        response_name="Upstream processing error",
        response_description="AtlasPay could not complete upstream processing.",
        outcome=PaymentOutcome.PROVIDER_ERROR,
        headline="Known AtlasPay upstream error",
        summary="The provider returned a documented upstream error.",
        impact="AtlasPay attempts can fail.",
        probable_causes=("Documented provider degradation",),
        recommended_actions=(
            RemediationAction(
                priority=1,
                title="Open the provider runbook",
                rationale="Use the reviewed deterministic response.",
            ),
        ),
        confidence=0.99,
        runbook_url="https://example.invalid/runbook",
    )


@pytest.mark.asyncio
async def test_known_error_bypasses_llm_and_replay_survives_restart(tmp_path) -> None:
    database = str(tmp_path / "incidents.db")
    store = SQLiteIncidentStore(database)
    analyzer = CountingAnalyzer()
    await store.create_version(known_rule())
    await store.append_event(
        ProviderEvent(
            event_id="pev_known",
            provider=ProviderId.ATLAS_PAY,
            outcome=PaymentOutcome.PROVIDER_ERROR,
            response_code="UPSTREAM_ERROR",
            http_status=502,
            processing_time_ms=920,
        )
    )

    first = await pipeline(store, analyzer).ingest(webhook())
    incident = await store.get(first.incident_ids[0])

    assert incident is not None
    assert incident.analysis.generated_by is AnalysisProvider.CATALOG
    assert incident.analysis.classification is ClassificationKind.KNOWN
    assert incident.analysis.conclusion is not None
    assert incident.analysis.conclusion.evidence_refs == (
        "snapshot",
        "event:pev_known",
    )
    assert incident.analysis.response_code_insights[0].response_code == "UPSTREAM_ERROR"
    assert (
        incident.analysis.response_code_insights[0].source
        is ResponseInsightSource.CATALOG
    )
    assert analyzer.calls == 0

    restarted_store = SQLiteIncidentStore(database)
    replay = await pipeline(restarted_store, analyzer).ingest(webhook())
    assert replay.replayed is True
    assert len(await restarted_store.list_recent()) == 1
    assert analyzer.calls == 0


@pytest.mark.asyncio
async def test_unknown_error_is_analyzed_once_then_resolved_and_reopened(tmp_path) -> None:
    store = SQLiteIncidentStore(str(tmp_path / "incidents.db"))
    analyzer = CountingAnalyzer()
    await store.append_event(
        ProviderEvent(
            event_id="pev_unknown",
            provider=ProviderId.ATLAS_PAY,
            outcome=PaymentOutcome.PROVIDER_ERROR,
            response_code="NEW_PROVIDER_FAILURE",
            http_status=502,
            processing_time_ms=1_100,
        )
    )
    service = pipeline(store, analyzer)

    firing = await service.ingest(webhook())
    repeated = await service.ingest(webhook())
    assert repeated.replayed is True
    assert analyzer.calls == 1

    await service.ingest(webhook(AlertDeliveryStatus.RESOLVED))
    resolved = await store.get(firing.incident_ids[0])
    assert resolved is not None and resolved.status.value == "resolved"

    reopened_result = await service.ingest(
        webhook(starts_at=START + timedelta(hours=1))
    )
    reopened = await store.get(reopened_result.incident_ids[0])
    assert reopened is not None
    assert reopened.status.value == "open"
    assert reopened.occurrences == 2
    assert analyzer.calls == 2
