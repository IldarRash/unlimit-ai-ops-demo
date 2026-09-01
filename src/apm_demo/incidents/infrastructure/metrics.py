from __future__ import annotations

import asyncio
from collections.abc import Mapping
from math import isfinite

import httpx

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.domain import MetricSnapshot
from apm_demo.incidents.ports import MetricsUnavailable


class DeterministicMetricsSource:
    """Predictable source for local development and contract-level tests."""

    def __init__(self, snapshots: Mapping[ProviderId, MetricSnapshot]) -> None:
        self._snapshots = dict(snapshots)

    async def collect(
        self, provider: ProviderId, *, window_seconds: int
    ) -> MetricSnapshot:
        try:
            snapshot = self._snapshots[provider]
        except KeyError as error:
            raise MetricsUnavailable(f"no fixture for provider {provider.value}") from error
        return snapshot.model_copy(update={"window_seconds": window_seconds})


class PrometheusMetricsSource:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )
        self._owns_client = client is None

    async def collect(
        self, provider: ProviderId, *, window_seconds: int
    ) -> MetricSnapshot:
        if not 15 <= window_seconds <= 3_600:
            raise ValueError("window_seconds must be between 15 and 3600")

        label = provider.value
        window = f"{window_seconds}s"
        all_requests = f'apm_client_requests_total{{provider="{label}"}}'
        queries = {
            "total_requests": f"sum(increase({all_requests}[{window}]))",
            "request_rate_per_second": f"sum(rate({all_requests}[{window}]))",
            "success_rate": self._ratio_query(
                f'apm_client_requests_total{{provider="{label}",outcome="success"}}',
                all_requests,
                window,
            ),
            "error_rate": self._ratio_query(
                f'apm_client_requests_total{{provider="{label}",outcome=~"provider-error|transport-error"}}',
                all_requests,
                window,
            ),
            "timeout_rate": self._ratio_query(
                f'apm_client_requests_total{{provider="{label}",outcome="timeout"}}',
                all_requests,
                window,
            ),
            "p95_latency_ms": (
                "1000 * histogram_quantile(0.95, "
                "sum by (le) (rate(apm_client_request_duration_seconds_bucket"
                f'{{provider="{label}"}}[{window}])))'
            ),
            "health_up": f'min(apm_client_provider_health{{provider="{label}"}})',
        }
        values = await asyncio.gather(
            *(self._query(name, query) for name, query in queries.items())
        )

        result = dict(zip(queries, values, strict=True))
        return MetricSnapshot(
            provider=provider,
            window_seconds=window_seconds,
            total_requests=max(0, round(result["total_requests"])),
            request_rate_per_second=result["request_rate_per_second"],
            success_rate=result["success_rate"],
            error_rate=result["error_rate"],
            timeout_rate=result["timeout_rate"],
            p95_latency_ms=result["p95_latency_ms"],
            health_up=result["health_up"] >= 0.5,
        )

    @staticmethod
    def _ratio_query(numerator: str, denominator: str, window: str) -> str:
        return (
            f"sum(rate({numerator}[{window}])) / "
            f"clamp_min(sum(rate({denominator}[{window}])), 0.000001)"
        )

    async def _query(self, name: str, query: str) -> float:
        try:
            response = await self._client.get("/api/v1/query", params={"query": query})
            response.raise_for_status()
            payload = response.json()
            result = payload["data"]["result"]
            raw_value = result[0]["value"][1]
            value = float(raw_value)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise MetricsUnavailable(f"Prometheus query failed for {name}") from error
        if payload.get("status") != "success" or not isfinite(value):
            raise MetricsUnavailable(f"Prometheus returned invalid data for {name}")
        return value

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
