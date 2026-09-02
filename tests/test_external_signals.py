from __future__ import annotations

import httpx
import pytest

from apm_demo.incidents.api.app import create_app
from apm_demo.incidents.api.config import IncidentSettings
from apm_demo.incidents.domain import (
    AnalysisProvider,
    CauseHypothesis,
    EvidenceBundle,
    IncidentAnalysis,
    RemediationAction,
)


class CapturingAnalyzer:
    def __init__(self) -> None:
        self.evidence: EvidenceBundle | None = None

    async def analyze(self, evidence: EvidenceBundle) -> IncidentAnalysis:
        self.evidence = evidence
        source = f"external:{evidence.external_signals[0].signal_id}"
        return IncidentAnalysis(
            headline="External signal correlates with provider degradation",
            summary="A sanitized provider-status signal supports the metric anomaly.",
            impact="AtlasPay payments may fail while the provider reports degradation.",
            probable_causes=("Provider-reported service degradation",),
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="Provider-reported degradation",
                    why="The external status signal and payment metrics overlap in time.",
                    evidence_refs=(source, "snapshot"),
                ),
            ),
            recommended_actions=(
                RemediationAction(
                    priority=1,
                    title="Confirm the provider status update",
                    rationale="Validate the external signal before changing routing.",
                ),
            ),
            confidence=0.82,
            generated_by=AnalysisProvider.OPENAI,
            model="local-eval-analyzer",
        )


def signal_payload() -> dict[str, object]:
    return {
        "signal_id": "status_atlas_001",
        "provider": "atlas-pay",
        "signal_type": "provider-status",
        "observed_at": "2026-09-02T09:00:00Z",
        "title": "AtlasPay reports elevated payment errors",
        "summary": "The provider status page reports degraded payment processing.",
        "source_ref": "status.atlas.example/incidents/001",
        "severity": "critical",
        "confidence": 0.95,
        "reported_count": 1,
        "region": "BR",
        "contains_customer_data": False,
    }


@pytest.mark.asyncio
async def test_authenticated_external_signal_is_idempotent_and_enters_evidence(
    tmp_path,
) -> None:
    settings = IncidentSettings(
        database_path=str(tmp_path / "incidents.db"),
        enforce_ingress_networks=False,
    )
    analyzer = CapturingAnalyzer()
    app = create_app(settings, analyzer=analyzer)
    await app.state.container.initialize()
    headers = {
        "Authorization": f"Bearer {settings.external_signal_token.get_secret_value()}"
    }

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.post("/api/v1/external-signals", json=signal_payload())
            ).status_code == 401

            accepted = await client.post(
                "/api/v1/external-signals", headers=headers, json=signal_payload()
            )
            assert accepted.status_code == 202

            changed_duplicate = signal_payload() | {"title": "Ignored duplicate"}
            duplicate = await client.post(
                "/api/v1/external-signals", headers=headers, json=changed_duplicate
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["title"] == signal_payload()["title"]

            listing = await client.get(
                "/api/v1/external-signals?provider=atlas-pay", headers=headers
            )
            assert listing.status_code == 200
            assert [item["signal_id"] for item in listing.json()] == ["status_atlas_001"]

            analyzed = await client.post(
                "/api/v1/incidents/analyze", json={"provider": "atlas-pay"}
            )
            assert analyzed.status_code == 200
            assert analyzed.json()["incident"]["evidence"]["external_signals"][0][
                "signal_id"
            ] == "status_atlas_001"
            assert analyzer.evidence is not None
            assert analyzer.evidence.external_signals[0].signal_id == "status_atlas_001"
    finally:
        await app.state.container.aclose()


@pytest.mark.asyncio
async def test_external_signal_rejects_customer_data_flag(tmp_path) -> None:
    settings = IncidentSettings(
        database_path=str(tmp_path / "incidents.db"),
        enforce_ingress_networks=False,
    )
    app = create_app(settings, analyzer=CapturingAnalyzer())
    await app.state.container.initialize()
    headers = {
        "Authorization": f"Bearer {settings.external_signal_token.get_secret_value()}"
    }
    payload = signal_payload() | {"contains_customer_data": True}

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/external-signals", headers=headers, json=payload
            )
        assert response.status_code == 422
    finally:
        await app.state.container.aclose()
