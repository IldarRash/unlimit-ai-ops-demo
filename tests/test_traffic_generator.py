import random

import httpx
import pytest
from prometheus_client import CollectorRegistry, generate_latest

from apm_demo.common.contracts import PROVIDER_BY_ID, ProviderId
from apm_demo.traffic_generator.config import GeneratorSettings
from apm_demo.traffic_generator.generator import TrafficGenerator
from apm_demo.traffic_generator.metrics import ClientMetrics


def settings() -> GeneratorSettings:
    return GeneratorSettings(
        provider_base_url="http://providers.test",
        request_timeout_seconds=0.1,
        healthcheck_timeout_seconds=0.1,
        random_seed=7,
    )


def metric_text(metrics: ClientMetrics) -> str:
    return generate_latest(metrics.registry).decode("utf-8")


@pytest.mark.asyncio
async def test_success_is_recorded_with_bounded_labels() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"outcome": "success"},
            request=request,
        )

    metrics = ClientMetrics(CollectorRegistry())
    client = httpx.AsyncClient(
        base_url="http://providers.test", transport=httpx.MockTransport(handler)
    )
    generator = TrafficGenerator(settings(), metrics, client=client, rng=random.Random(1))

    result = await generator.send_one(PROVIDER_BY_ID[ProviderId.ATLAS_PAY])
    output = metric_text(metrics)

    assert result.outcome == "success"
    assert 'provider="atlas-pay"' in output
    assert 'outcome="success"' in output
    assert "transaction_id" not in output
    assert "merchant-demo" not in output
    await client.aclose()


@pytest.mark.asyncio
async def test_client_timeout_is_recorded_when_provider_never_answers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider did not answer", request=request)

    metrics = ClientMetrics(CollectorRegistry())
    client = httpx.AsyncClient(
        base_url="http://providers.test", transport=httpx.MockTransport(handler)
    )
    generator = TrafficGenerator(settings(), metrics, client=client, rng=random.Random(2))

    result = await generator.send_one(PROVIDER_BY_ID[ProviderId.NOVA_BANK])
    output = metric_text(metrics)

    assert result.outcome == "timeout"
    assert result.status_code is None
    assert "apm_client_timeouts_total" in output
    assert 'provider="nova-bank"' in output
    await client.aclose()


@pytest.mark.asyncio
async def test_unhealthy_and_unresponsive_healthchecks_set_zero() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        raise httpx.ReadTimeout("health endpoint hung", request=request)

    metrics = ClientMetrics(CollectorRegistry())
    client = httpx.AsyncClient(
        base_url="http://providers.test", transport=httpx.MockTransport(handler)
    )
    generator = TrafficGenerator(settings(), metrics, client=client, rng=random.Random(3))

    assert not await generator.check_provider_health(ProviderId.ATLAS_PAY)
    assert not await generator.check_provider_health(ProviderId.ORBIT_WALLET)
    output = metric_text(metrics)

    assert 'apm_client_provider_health{provider="atlas-pay"} 0.0' in output
    assert 'apm_client_provider_health{provider="orbit-wallet"} 0.0' in output
    await client.aclose()


def test_each_provider_only_generates_supported_payment_methods() -> None:
    metrics = ClientMetrics(CollectorRegistry())
    generator = TrafficGenerator(settings(), metrics, rng=random.Random(4))

    for definition in PROVIDER_BY_ID.values():
        for _ in range(20):
            method, _ = generator.build_request(definition)
            assert method in definition.supported_methods
