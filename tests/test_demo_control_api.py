from __future__ import annotations

import httpx
import pytest

import apm_demo.incidents.api.app as app_module
from apm_demo.incidents.api.app import create_app
from apm_demo.incidents.api.config import IncidentSettings
from apm_demo.incidents.domain import (
    AnalysisProvider,
    CauseHypothesis,
    IncidentAnalysis,
    RemediationAction,
)


class FakeAnalyzer:
    async def analyze(self, evidence):  # type: ignore[no-untyped-def]
        return IncidentAnalysis(
            headline="test",
            summary="test",
            impact="test",
            probable_causes=("test",),
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="test",
                    why="test",
                    evidence_refs=("snapshot",),
                ),
            ),
            recommended_actions=(
                RemediationAction(priority=1, title="test", rationale="test"),
            ),
            confidence=0.5,
            generated_by=AnalysisProvider.OPENAI,
            model="fake-openai",
        )


class FakeUpstreamClient:
    calls: list[tuple[str, str, object]] = []

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):  # type: ignore[no-untyped-def]
        return False

    async def request(self, method: str, url: str, json=None):  # type: ignore[no-untyped-def]
        self.calls.append((method, url, json))
        request = httpx.Request(method, url)
        if url.endswith("/admin/generator"):
            return httpx.Response(
                200,
                request=request,
                json={"enabled": json.get("enabled", True) if json else True, "requests_per_second": json.get("requests_per_second", 4) if json else 4},
            )
        if url.endswith("/admin/providers"):
            return httpx.Response(200, request=request, json=[])
        if "/admin/scenarios/" in url:
            return httpx.Response(
                200,
                request=request,
                json={"scenario": url.rsplit("/", 1)[-1], "provider": json["provider"]},
            )
        return httpx.Response(404, request=request)


@pytest.mark.asyncio
async def test_operator_facade_controls_private_demo_services(monkeypatch, tmp_path) -> None:
    FakeUpstreamClient.calls.clear()
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(app_module.httpx, "AsyncClient", FakeUpstreamClient)
    app = create_app(
        IncidentSettings(database_path=str(tmp_path / "incidents.db")),
        analyzer=FakeAnalyzer(),
    )

    async with real_async_client(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        control = await client.get("/api/v1/demo/control")
        traffic = await client.patch(
            "/api/v1/demo/traffic",
            json={"enabled": True, "requests_per_second": 12},
        )
        scenario = await client.post(
            "/api/v1/demo/scenarios",
            json={"provider": "orbit-wallet", "scenario": "business-declines"},
        )
        runtime = await client.get("/api/v1/runtime")

    assert control.json()["generator"]["enabled"] is True
    assert traffic.json()["requests_per_second"] == 12
    assert scenario.json() == {
        "provider": "orbit-wallet",
        "scenario": "business-declines",
    }
    assert runtime.json()["analyzer_mode"] == "openai"
    assert runtime.json()["openai_requests_enabled"] is False
    assert runtime.json()["grafana_provider_dashboard_url"].startswith(
        "http://localhost:3000/d/"
    )
    assert any(call[0] == "PATCH" for call in FakeUpstreamClient.calls)
    assert any("/admin/scenarios/business-declines" in call[1] for call in FakeUpstreamClient.calls)
