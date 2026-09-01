import httpx
import pytest

from apm_demo.incidents.api.app import create_app
from apm_demo.incidents.api.config import AnalyzerMode, IncidentSettings, MetricsMode


@pytest.mark.asyncio
async def test_incident_api_analyzes_lists_and_updates_incident(tmp_path) -> None:
    app = create_app(
        IncidentSettings(
            metrics_mode=MetricsMode.DEMO,
            analyzer_mode=AnalyzerMode.MOCK,
            database_path=str(tmp_path / "incidents.db"),
        )
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
        assert incident["analysis"]["generated_by"] == "mock"

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
    app = create_app(IncidentSettings(database_path=str(tmp_path / "incidents.db")))
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
    app = create_app(IncidentSettings(database_path=str(tmp_path / "incidents.db")))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/")
        stylesheet = await client.get("/assets/styles.css")

    assert page.status_code == 200
    assert "Sentinel" in page.text
    assert stylesheet.status_code == 200
    assert "--color-primary" in stylesheet.text
