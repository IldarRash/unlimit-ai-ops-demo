from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.infrastructure.metrics import (
    MetricsUnavailable,
    PrometheusMetricsSource,
)


@pytest.mark.asyncio
async def test_prometheus_source_normalizes_provider_metrics() -> None:
    values = {
        "histogram_quantile": "1650",
        "provider_health": "1",
        'outcome="success"': "0.80",
        "provider-error|transport-error": "0.15",
        'outcome="timeout"': "0.05",
        "sum(rate(": "0.4",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())["query"][0]
        if "sum by (payment_method, outcome)" in query:
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [
                            {
                                "metric": {
                                    "payment_method": "pix",
                                    "outcome": "success",
                                },
                                "value": [1, "72"],
                            },
                            {
                                "metric": {
                                    "payment_method": "pix",
                                    "outcome": "provider-error",
                                },
                                "value": [1, "18"],
                            },
                            {
                                "metric": {
                                    "payment_method": "pix",
                                    "outcome": "transport-error",
                                },
                                "value": [1, "2"],
                            },
                            {
                                "metric": {
                                    "payment_method": "ideal",
                                    "outcome": "success",
                                },
                                "value": [1, "24"],
                            },
                            {
                                "metric": {
                                    "payment_method": "ideal",
                                    "outcome": "timeout",
                                },
                                "value": [1, "6"],
                            },
                        ],
                    },
                },
            )
        value = next(value for marker, value in values.items() if marker in query)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": [{"value": [1, value]}]},
            },
        )

    client = httpx.AsyncClient(
        base_url="http://prometheus.test", transport=httpx.MockTransport(handler)
    )
    source = PrometheusMetricsSource("http://prometheus.test", client=client)

    snapshot = await source.collect(ProviderId.ATLAS_PAY, window_seconds=300)

    assert snapshot.total_requests == 122
    assert snapshot.outcome_counts is not None
    assert snapshot.outcome_counts.provider_error == 20
    assert snapshot.outcome_counts.timeout == 6
    assert snapshot.payment_method_breakdown[0].payment_method.value == "ideal"
    assert snapshot.payment_method_breakdown[1].counts.total_requests == 92
    assert snapshot.success_rate == 0.8
    assert snapshot.p95_latency_ms == 1_650
    assert snapshot.health_up is True
    await client.aclose()


@pytest.mark.asyncio
async def test_prometheus_source_rejects_empty_query_results() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": []}},
        )

    client = httpx.AsyncClient(
        base_url="http://prometheus.test", transport=httpx.MockTransport(handler)
    )
    source = PrometheusMetricsSource("http://prometheus.test", client=client)

    with pytest.raises(MetricsUnavailable, match="Prometheus query failed"):
        await source.collect(ProviderId.NOVA_BANK, window_seconds=300)
    await client.aclose()
