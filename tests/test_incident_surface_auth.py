from __future__ import annotations

import base64

import httpx
import pytest
from fastapi import Request

from apm_demo.incidents.api.app import _require_network, create_app
from apm_demo.incidents.api.auth import SurfaceAuth
from apm_demo.incidents.api.config import IncidentSettings


def basic_auth(username: str, password: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def test_enabled_operator_auth_rejects_missing_or_weak_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APM_INCIDENT_OPERATOR_AUTH_ENABLED", "true")
    monkeypatch.setenv("DEMO_AUTH_USER", "approved-tester")
    monkeypatch.setenv("DEMO_AUTH_PASSWORD", "short")

    with pytest.raises(ValueError, match="at least 12"):
        SurfaceAuth.from_environment()


def test_configured_metrics_token_rejects_short_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APM_INCIDENT_METRICS_TOKEN", "short")

    with pytest.raises(ValueError, match="at least 20"):
        SurfaceAuth.from_environment()


def test_railway_can_disable_unreliable_source_ip_guard() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/provider-events",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("incident-api", 8002),
            "client": ("203.0.113.10", 49152),
        }
    )

    _require_network(request, (), enforce=False)


@pytest.mark.asyncio
async def test_operator_auth_protects_surface_but_not_route_specific_integrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("APM_INCIDENT_OPERATOR_AUTH_ENABLED", "true")
    monkeypatch.setenv("DEMO_AUTH_USER", "approved-tester")
    monkeypatch.setenv("DEMO_AUTH_PASSWORD", "approved-password")
    monkeypatch.setenv("APM_INCIDENT_METRICS_TOKEN", "private-scrape-token")
    settings = IncidentSettings(database_path=str(tmp_path / "incidents.db"))
    app = create_app(settings)
    await app.state.container.initialize()

    integration_headers = {
        "Authorization": f"Bearer {settings.provider_event_token.get_secret_value()}"
    }
    event = {
        "event_id": "surface_auth_event",
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
            denied = await client.get("/api/v1/runtime")
            assert denied.status_code == 401
            assert denied.headers["www-authenticate"].startswith("Basic")
            assert (await client.get("/docs")).status_code == 401

            allowed = await client.get(
                "/api/v1/runtime", headers=basic_auth("approved-tester", "approved-password")
            )
            assert allowed.status_code == 200
            assert allowed.headers["cache-control"] == "no-store"
            assert allowed.headers["x-content-type-options"] == "nosniff"
            assert allowed.headers["x-frame-options"] == "DENY"
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/ready")).status_code == 200

            metrics_denied = await client.get("/metrics")
            assert metrics_denied.status_code == 401
            assert metrics_denied.headers["www-authenticate"] == "Bearer"
            assert (
                await client.get(
                    "/metrics", headers={"Authorization": "Bearer private-scrape-token"}
                )
            ).status_code == 200

            # This endpoint keeps its integration bearer token and must not
            # require the browser credential on top of it.
            accepted = await client.post(
                "/api/v1/provider-events", headers=integration_headers, json=event
            )
            assert accepted.status_code == 202

            # Catalog authorization similarly remains its dedicated token.
            catalog = await client.get(
                "/api/v1/catalog",
                headers={
                    "x-catalog-admin-token": settings.catalog_admin_token.get_secret_value()
                },
            )
            assert catalog.status_code == 200
    finally:
        await app.state.container.aclose()
