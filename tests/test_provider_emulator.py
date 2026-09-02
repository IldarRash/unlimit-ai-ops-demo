from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from apm_demo.common.contracts import HealthMode, ProviderId, ScenarioName
from apm_demo.incidents.application.detection import AnomalyDetector
from apm_demo.incidents.domain import MetricSnapshot, SignalType
from apm_demo.provider_emulator.app import create_app
from apm_demo.provider_emulator.metrics import ProviderMetrics
from apm_demo.provider_emulator.state import BASELINE_BEHAVIORS, ProviderRuntime


class FixedRandom:
    def __init__(self, values: Iterator[float]) -> None:
        self._values = values

    def random(self) -> float:
        return next(self._values)

    def uniform(self, start: float, end: float) -> float:
        return (start + end) / 2


def payment_payload() -> dict[str, str]:
    return {
        "transaction_id": "tx-1001",
        "merchant": "merchant-demo-01",
        "payment_method": "pix",
        "region": "BR",
    }


def build_client(random_values: list[float]) -> tuple[TestClient, AsyncMock]:
    sleep = AsyncMock(return_value=None)
    app = create_app(
        runtime=ProviderRuntime(),
        metrics=ProviderMetrics(CollectorRegistry()),
        rng=FixedRandom(iter(random_values)),  # type: ignore[arg-type]
        sleep=sleep,
    )
    return TestClient(app), sleep


def test_successful_payment_uses_configured_provider() -> None:
    client, sleep = build_client([0.9, 0.1])

    response = client.post("/providers/atlas-pay/payments", json=payment_payload())

    assert response.status_code == 200
    assert response.json()["outcome"] == "success"
    assert response.json()["provider"] == "atlas-pay"
    sleep.assert_awaited_once()


def test_timeout_behavior_is_observable_as_gateway_timeout() -> None:
    client, sleep = build_client([0.0])

    response = client.post("/providers/nova-bank/payments", json=payment_payload())

    assert response.status_code == 504
    assert response.json()["outcome"] == "timeout"
    assert sleep.await_args.args[0] == 5.0


def test_healthcheck_can_become_unhealthy() -> None:
    client, _ = build_client([0.9])
    patch = client.patch(
        "/admin/providers/orbit-wallet/behavior",
        json={"health_mode": HealthMode.UNHEALTHY.value},
    )
    assert patch.status_code == 200

    health = client.get("/providers/orbit-wallet/health")

    assert health.status_code == 503
    assert health.json()["status"] == "unhealthy"


def test_invalid_partial_distribution_is_rejected() -> None:
    client, _ = build_client([0.9])

    response = client.patch(
        "/admin/providers/atlas-pay/behavior",
        json={"success_rate": 0.5},
    )

    assert response.status_code == 422
    assert "must sum to 1.0" in str(response.json())


def test_baseline_operational_failure_rates_stay_below_alert_threshold() -> None:
    for behavior in BASELINE_BEHAVIORS.values():
        assert behavior.success_rate >= 0.90
        assert behavior.provider_error_rate + behavior.timeout_rate < 0.05


@pytest.mark.parametrize(
    ("scenario", "field", "expected"),
    [
        ("slow-provider", "base_latency_ms", 1_600),
        ("provider-errors", "provider_error_rate", 0.45),
        ("business-declines", "soft_decline_rate", 0.38),
        ("unknown-provider-error", "provider_error_code", "UNMAPPED_PROVIDER_FAILURE"),
        ("provider-timeout", "timeout_rate", 0.65),
        ("healthcheck-down", "health_mode", "unhealthy"),
        ("healthcheck-timeout", "health_mode", "timeout"),
    ],
)
def test_scenarios_change_selected_provider(
    scenario: str, field: str, expected: object
) -> None:
    client, _ = build_client([0.9])

    response = client.post(
        f"/admin/scenarios/{scenario}", json={"provider": "atlas-pay"}
    )

    assert response.status_code == 200
    assert response.json()["behavior"][field] == expected


def test_scenario_and_baseline_state_are_visible_in_metrics() -> None:
    client, _ = build_client([0.9])

    client.post(
        "/admin/scenarios/business-declines", json={"provider": "orbit-wallet"}
    )
    metrics = client.get("/metrics").text

    assert 'apm_demo_active_scenario{provider="atlas-pay",scenario="normal"} 1.0' in metrics
    assert (
        'apm_demo_active_scenario{provider="orbit-wallet",scenario="business-declines"} 1.0'
        in metrics
    )
    assert (
        'apm_demo_scenario_applied_total{provider="orbit-wallet",scenario="business-declines"} 1.0'
        in metrics
    )
    assert 'apm_provider_configured_error_ratio{provider="orbit-wallet"} 0.02' in metrics


def test_scenarios_are_reproducible_instead_of_stacking_previous_degradation() -> None:
    client, _ = build_client([0.9])
    client.post("/admin/scenarios/slow-provider", json={"provider": "atlas-pay"})

    response = client.post(
        "/admin/scenarios/provider-errors", json={"provider": "atlas-pay"}
    )

    assert response.json()["behavior"]["base_latency_ms"] == (
        BASELINE_BEHAVIORS[ProviderId.ATLAS_PAY].base_latency_ms
    )
    assert response.json()["behavior"]["provider_error_rate"] == 0.45


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_signal", "forbidden_signals"),
    [
        (ScenarioName.NORMAL, None, frozenset(SignalType)),
        (ScenarioName.RECOVER, None, frozenset(SignalType)),
        (
            ScenarioName.SLOW_PROVIDER,
            SignalType.LATENCY,
            frozenset(
                {
                    SignalType.ERROR_RATE,
                    SignalType.TIMEOUT_RATE,
                    SignalType.DECLINE_RATE,
                    SignalType.HEALTH,
                }
            ),
        ),
        (
            ScenarioName.PROVIDER_ERRORS,
            SignalType.ERROR_RATE,
            frozenset(
                {
                    SignalType.TIMEOUT_RATE,
                    SignalType.DECLINE_RATE,
                    SignalType.HEALTH,
                }
            ),
        ),
        (
            ScenarioName.UNKNOWN_PROVIDER_ERROR,
            SignalType.ERROR_RATE,
            frozenset(
                {
                    SignalType.TIMEOUT_RATE,
                    SignalType.DECLINE_RATE,
                    SignalType.HEALTH,
                }
            ),
        ),
        (
            ScenarioName.BUSINESS_DECLINES,
            SignalType.DECLINE_RATE,
            frozenset(
                {
                    SignalType.ERROR_RATE,
                    SignalType.TIMEOUT_RATE,
                    SignalType.HEALTH,
                }
            ),
        ),
        (
            ScenarioName.PROVIDER_TIMEOUT,
            SignalType.TIMEOUT_RATE,
            frozenset(
                {
                    SignalType.ERROR_RATE,
                    SignalType.DECLINE_RATE,
                    SignalType.HEALTH,
                }
            ),
        ),
        (
            ScenarioName.HEALTHCHECK_DOWN,
            SignalType.HEALTH,
            frozenset(
                {
                    SignalType.ERROR_RATE,
                    SignalType.TIMEOUT_RATE,
                    SignalType.DECLINE_RATE,
                }
            ),
        ),
        (
            ScenarioName.HEALTHCHECK_TIMEOUT,
            SignalType.HEALTH,
            frozenset(
                {
                    SignalType.ERROR_RATE,
                    SignalType.TIMEOUT_RATE,
                    SignalType.DECLINE_RATE,
                }
            ),
        ),
    ],
)
async def test_each_scenario_produces_its_intended_incident_signal(
    scenario: ScenarioName,
    expected_signal: SignalType | None,
    forbidden_signals: frozenset[SignalType],
) -> None:
    behavior = await ProviderRuntime().apply_scenario(
        scenario, ProviderId.ATLAS_PAY
    )
    returning_share = 1 - behavior.timeout_rate
    snapshot = MetricSnapshot(
        provider=ProviderId.ATLAS_PAY,
        window_seconds=300,
        total_requests=1_000,
        request_rate_per_second=20,
        success_rate=returning_share * behavior.success_rate,
        error_rate=returning_share * behavior.provider_error_rate,
        timeout_rate=behavior.timeout_rate,
        p95_latency_ms=behavior.base_latency_ms + behavior.jitter_ms,
        health_up=behavior.health_mode is HealthMode.HEALTHY,
    )

    detected = {
        signal.signal_type for signal in AnomalyDetector().detect(snapshot)
    }

    if expected_signal is None:
        assert detected == set()
    else:
        assert expected_signal in detected
    assert detected.isdisjoint(forbidden_signals)
