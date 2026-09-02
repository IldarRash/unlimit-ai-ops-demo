from __future__ import annotations

import sqlite3

import httpx
import pytest

from apm_demo.incidents.api.app import create_app
from apm_demo.incidents.api.config import IncidentSettings


EVENT = {
    "event_id": "pev_api_known",
    "provider": "atlas-pay",
    "outcome": "provider-error",
    "response_code": "UPSTREAM_ERROR",
    "http_status": 502,
    "processing_time_ms": 950,
    "payment_method": "pix",
    "region": "BR",
}

ALERT = {
    "version": "4",
    "groupKey": '{}:{provider="atlas-pay"}',
    "truncatedAlerts": 0,
    "status": "firing",
    "receiver": "incident-intelligence",
    "groupLabels": {"provider": "atlas-pay"},
    "commonLabels": {"provider": "atlas-pay"},
    "commonAnnotations": {},
    "externalURL": "http://alertmanager.test",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "ProviderErrorRateHigh",
                "provider": "atlas-pay",
                "severity": "critical",
            },
            "annotations": {"summary": "Synthetic provider degradation"},
            "startsAt": "2026-08-01T12:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus.test/graph",
            "fingerprint": "api-alert-fingerprint",
        }
    ],
}


@pytest.mark.asyncio
async def test_ingress_auth_known_path_and_allowlist_redaction(tmp_path) -> None:
    database = tmp_path / "incidents.db"
    settings = IncidentSettings(database_path=str(database))
    app = create_app(settings)
    await app.state.container.initialize()
    event_headers = {
        "Authorization": (
            f"Bearer {settings.provider_event_token.get_secret_value()}"
        )
    }
    alert_headers = {
        "Authorization": f"Bearer {settings.alertmanager_token.get_secret_value()}"
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unauthorized_event = await client.post("/api/v1/provider-events", json=EVENT)
        assert unauthorized_event.status_code == 401

        forbidden_shape = await client.post(
            "/api/v1/provider-events",
            headers=event_headers,
            json={**EVENT, "transaction_id": "must-not-enter-the-pipeline"},
        )
        assert forbidden_shape.status_code == 422

        accepted_event = await client.post(
            "/api/v1/provider-events", headers=event_headers, json=EVENT
        )
        assert accepted_event.status_code == 202

        unauthorized_alert = await client.post(
            "/api/v1/integrations/alertmanager", json=ALERT
        )
        assert unauthorized_alert.status_code == 401

        accepted_alert = await client.post(
            "/api/v1/integrations/alertmanager",
            headers=alert_headers,
            json=ALERT,
        )
        assert accepted_alert.status_code == 200
        incident_id = accepted_alert.json()["incident_ids"][0]
        incident = await client.get(f"/api/v1/incidents/{incident_id}")
        assert incident.json()["analysis"]["generated_by"] == "catalog"

    await app.state.container.aclose()
    with sqlite3.connect(database) as connection:
        serialized_events = " ".join(
            row[0] for row in connection.execute("SELECT payload_json FROM provider_events")
        )
    assert "transaction_id" not in serialized_events
    assert "must-not-enter-the-pipeline" not in serialized_events
