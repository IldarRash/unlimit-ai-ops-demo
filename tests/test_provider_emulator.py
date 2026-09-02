from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from apm_demo.common.contracts import HealthMode
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
        assert behavior.provider_error_rate + behavior.timeout_rate < 0.05


@pytest.mark.parametrize(
    ("scenario", "field", "expected"),
    [
        ("slow-provider", "base_latency_ms", 1_600),
        ("provider-errors", "provider_error_rate", 0.45),
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
