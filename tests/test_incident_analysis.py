from __future__ import annotations

import json

import httpx
import pytest

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.api.config import IncidentSettings
from apm_demo.incidents.application.detection import AnomalyDetector
from apm_demo.incidents.domain import (
    AnalysisProvider,
    CauseHypothesis,
    IncidentAnalysis,
    EvidenceBundle,
    ExternalSignal,
    ExternalSignalType,
    MetricSnapshot,
    RemediationAction,
)
from apm_demo.incidents.infrastructure.analysis import (
    AnalysisUnavailable,
    OpenAIIncidentAnalyzer,
)


def test_incident_settings_use_a_dedicated_llm_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-that-is-long-enough")
    settings = IncidentSettings(_env_file=None)

    assert settings.request_timeout_seconds == 5
    assert settings.llm_timeout_seconds == 30


def evidence() -> EvidenceBundle:
    snapshot = MetricSnapshot(
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
    return EvidenceBundle(
        snapshot=snapshot,
        signals=AnomalyDetector().detect(snapshot),
        source="test",
    )


@pytest.mark.asyncio
async def test_openai_adapter_requests_strict_non_stored_structured_output() -> None:
    captured: dict[str, object] = {}
    output = {
        "headline": "AtlasPay latency degradation",
        "summary": "Latency and error signals exceed their configured thresholds.",
        "impact": "Some AtlasPay payments may complete slowly or fail.",
        "probable_causes": ["Provider processing degradation"],
        "causes": [{"category": "technical", "title": "Provider processing degradation", "why": "Error signal exceeded threshold.", "evidence_refs": ["snapshot", "signal:error-rate"]}],
        "recommended_actions": [
            {
                "priority": 1,
                "title": "Inspect provider status",
                "rationale": "Confirm the provider condition before changing routing.",
            }
        ],
        "confidence": 0.83,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps(output)})

    client = httpx.AsyncClient(
        base_url="https://api.openai.test/v1", transport=httpx.MockTransport(handler)
    )
    analyzer = OpenAIIncidentAnalyzer(
        "sk-test-abcdefghijklmnopqrstuvwxyz",
        model="test-model",
        requests_enabled=True,
        client=client,
        max_attempts=1,
    )

    result = await analyzer.analyze(evidence())

    assert captured["store"] is False
    model_input = json.loads(captured["input"])  # type: ignore[arg-type]
    expected_refs = [
        "signal:error-rate",
        "signal:latency",
        "signal:timeout-rate",
        "snapshot",
    ]
    assert model_input["allowed_evidence_refs"] == expected_refs
    assert captured["text"]["format"]["type"] == "json_schema"  # type: ignore[index]
    assert captured["text"]["format"]["strict"] is True  # type: ignore[index]
    schema = captured["text"]["format"]["schema"]  # type: ignore[index]
    assert schema["$defs"]["_ProposedCause"]["properties"]["evidence_refs"][  # type: ignore[index]
        "items"
    ]["enum"] == expected_refs
    assert result.generated_by is AnalysisProvider.OPENAI
    assert result.causes[0].category == "technical"
    assert result.recommended_actions[0].safe_to_automate is False
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_rejects_unreferenced_evidence() -> None:
    output = {"headline": "x", "summary": "x", "impact": "x", "probable_causes": ["x"], "causes": [{"category": "technical", "title": "x", "why": "x", "evidence_refs": ["event:not-present"]}], "recommended_actions": [{"priority": 1, "title": "x", "rationale": "x"}], "confidence": 0.1}
    client = httpx.AsyncClient(base_url="https://api.openai.test/v1", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output_text": json.dumps(output)})))
    analyzer = OpenAIIncidentAnalyzer("sk-test-abcdefghijklmnopqrstuvwxyz", model="test-model", requests_enabled=True, client=client, max_attempts=1)
    with pytest.raises(AnalysisUnavailable):
        await analyzer.analyze(evidence())
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_accepts_supplied_external_signal_reference() -> None:
    supplied = evidence().model_copy(
        update={
            "external_signals": (
                ExternalSignal(
                    signal_id="status_001",
                    provider=ProviderId.ATLAS_PAY,
                    signal_type=ExternalSignalType.PROVIDER_STATUS,
                    title="Provider status degradation",
                    summary="Sanitized provider status reports elevated errors.",
                    source_ref="status.example/incidents/001",
                ),
            )
        }
    )
    output = {
        "headline": "Correlated provider degradation",
        "summary": "Metrics and the provider status signal align.",
        "impact": "Some payments may fail.",
        "probable_causes": ["Provider degradation"],
        "causes": [
            {
                "category": "technical",
                "title": "Provider degradation",
                "why": "The supplied status signal corroborates the metrics.",
                "evidence_refs": ["external:status_001", "snapshot"],
            }
        ],
        "recommended_actions": [
            {"priority": 1, "title": "Verify status", "rationale": "Confirm before mitigation."}
        ],
        "confidence": 0.8,
    }
    client = httpx.AsyncClient(
        base_url="https://api.openai.test/v1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"output_text": json.dumps(output)})
        ),
    )
    analyzer = OpenAIIncidentAnalyzer(
        "sk-test-abcdefghijklmnopqrstuvwxyz",
        model="test-model",
        requests_enabled=True,
        client=client,
        max_attempts=1,
    )

    result = await analyzer.analyze(supplied)

    assert result.causes[0].evidence_refs[0] == "external:status_001"
    await client.aclose()


def test_openai_adapter_rejects_placeholder_key() -> None:
    with pytest.raises(ValueError, match="usable OpenAI API key"):
        OpenAIIncidentAnalyzer(
            "replace_with_your_openai_api_key",
            model="test-model",
        )


@pytest.mark.asyncio
async def test_openai_runtime_gate_blocks_network_requests() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="https://api.openai.test/v1", transport=httpx.MockTransport(handler)
    )
    analyzer = OpenAIIncidentAnalyzer(
        "sk-test-abcdefghijklmnopqrstuvwxyz",
        model="test-model",
        client=client,
    )

    with pytest.raises(AnalysisUnavailable, match="runtime gate"):
        await analyzer.analyze(evidence())

    assert calls == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_rejects_invalid_model_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output_text": "{}"})

    client = httpx.AsyncClient(
        base_url="https://api.openai.test/v1", transport=httpx.MockTransport(handler)
    )
    analyzer = OpenAIIncidentAnalyzer(
        "sk-test-abcdefghijklmnopqrstuvwxyz",
        model="test-model",
        requests_enabled=True,
        client=client,
        max_attempts=1,
    )

    with pytest.raises(AnalysisUnavailable, match="OpenAI analysis failed"):
        await analyzer.analyze(evidence())
    await client.aclose()
