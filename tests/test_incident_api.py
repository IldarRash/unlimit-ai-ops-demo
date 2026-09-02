import httpx
import pytest

from apm_demo.incidents.api.app import create_app
from apm_demo.incidents.api.config import IncidentSettings, MetricsMode
from apm_demo.incidents.domain import (
    AnalysisProvider,
    CauseHypothesis,
    IncidentAnalysis,
    RemediationAction,
)


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, evidence):  # type: ignore[no-untyped-def]
        self.calls += 1
        return IncidentAnalysis(
            headline="Provider degradation",
            summary="Synthetic test analysis.",
            impact="Payments may fail.",
            probable_causes=("Synthetic provider degradation",),
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="Synthetic provider degradation",
                    why="The normalized metrics exceeded configured thresholds.",
                    evidence_refs=("snapshot",),
                ),
            ),
            recommended_actions=(
                RemediationAction(
                    priority=1,
                    title="Inspect provider metrics",
                    rationale="Confirm the condition before mitigation.",
                ),
            ),
            confidence=0.8,
            generated_by=AnalysisProvider.OPENAI,
            model="fake-openai",
        )


@pytest.mark.asyncio
async def test_incident_api_analyzes_lists_and_updates_incident(tmp_path) -> None:
    app = create_app(
        IncidentSettings(
            metrics_mode=MetricsMode.DEMO,
            database_path=str(tmp_path / "incidents.db"),
        ),
        analyzer=FakeAnalyzer(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/incidents/analyze", json={"provider": "atlas-pay"}
        )
        assert response.status_code == 200
        incident = response.json()["incident"]
        incident_id = incident["incident_id"]
        assert response.json()["detected"] is True
        assert incident["analysis"]["generated_by"] == "openai"

        listing = await client.get("/api/v1/incidents")
        assert [item["incident_id"] for item in listing.json()] == [incident_id]

        status = await client.patch(
            f"/api/v1/incidents/{incident_id}/status",
            json={"status": "acknowledged"},
        )
        assert status.status_code == 200
        assert status.json()["status"] == "acknowledged"

        audit = await client.get(f"/api/v1/incidents/{incident_id}/audit")
        assert [item["event_type"] for item in audit.json()] == [
            "created",
            "status-changed",
        ]
        assert response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_incident_api_returns_no_incident_for_healthy_provider(tmp_path) -> None:
    app = create_app(
        IncidentSettings(database_path=str(tmp_path / "incidents.db")),
        analyzer=FakeAnalyzer(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/incidents/analyze", json={"provider": "nova-bank"}
        )

    assert response.status_code == 200
    assert response.json() == {"detected": False, "incident": None}


@pytest.mark.asyncio
async def test_incident_console_is_served_by_the_api(tmp_path) -> None:
    app = create_app(
        IncidentSettings(database_path=str(tmp_path / "incidents.db")),
        analyzer=FakeAnalyzer(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/")
        stylesheet = await client.get("/assets/styles.css")

    assert page.status_code == 200
    assert "Sentinel" in page.text
    assert stylesheet.status_code == 200
    assert "--color-primary" in stylesheet.text


@pytest.mark.asyncio
async def test_manual_analysis_uses_known_error_catalog_before_openai(tmp_path) -> None:
    analyzer = FakeAnalyzer()
    settings = IncidentSettings(database_path=str(tmp_path / "incidents.db"))
    app = create_app(settings, analyzer=analyzer)
    await app.state.container.initialize()
    headers = {
        "Authorization": f"Bearer {settings.provider_event_token.get_secret_value()}"
    }
    event = {
        "event_id": "manual_known_event",
        "provider": "atlas-pay",
        "outcome": "provider-error",
        "response_code": "UPSTREAM_ERROR",
        "http_status": 502,
        "processing_time_ms": 900,
        "payment_method": "pix",
        "region": "BR",
    }

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.post("/api/v1/provider-events", headers=headers, json=event)
            ).status_code == 202
            response = await client.post(
                "/api/v1/incidents/analyze", json={"provider": "atlas-pay"}
            )
        assert response.status_code == 200
        assert response.json()["incident"]["analysis"]["generated_by"] == "catalog"
        assert analyzer.calls == 0
    finally:
        await app.state.container.aclose()
