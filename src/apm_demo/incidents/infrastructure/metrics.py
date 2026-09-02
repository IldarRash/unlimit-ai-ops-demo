from __future__ import annotations

import asyncio
from collections.abc import Mapping
from math import isfinite

import httpx

from apm_demo.common.contracts import PaymentMethod, PaymentOutcome, ProviderId
from apm_demo.incidents.domain import (
    MetricSnapshot,
    OutcomeCounts,
    PaymentMethodBreakdown,
)
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
        values_and_breakdown = await asyncio.gather(
            *(self._query(name, query) for name, query in queries.items()),
            self._query_breakdown(all_requests, window),
        )

        result = dict(zip(queries, values_and_breakdown[:-1], strict=True))
        outcome_counts, method_breakdown = values_and_breakdown[-1]
        return MetricSnapshot(
            provider=provider,
            window_seconds=window_seconds,
            total_requests=outcome_counts.total_requests,
            outcome_counts=outcome_counts,
            payment_method_breakdown=method_breakdown,
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

    async def _query_breakdown(
        self, all_requests: str, window: str
    ) -> tuple[OutcomeCounts, tuple[PaymentMethodBreakdown, ...]]:
        query = (
            "sum by (payment_method, outcome) "
            f"(increase({all_requests}[{window}]))"
        )
        try:
            response = await self._client.get("/api/v1/query", params={"query": query})
            response.raise_for_status()
            payload = response.json()
            result = payload["data"]["result"]
            if not isinstance(result, list) or not result:
                raise ValueError("empty breakdown")
            by_method: dict[PaymentMethod, dict[PaymentOutcome, int]] = {}
            for item in result:
                metric = item["metric"]
                method = PaymentMethod(metric["payment_method"])
                raw_outcome = metric["outcome"]
                outcome = (
                    PaymentOutcome.PROVIDER_ERROR
                    if raw_outcome == "transport-error"
                    else PaymentOutcome(raw_outcome)
                )
                value = float(item["value"][1])
                if not isfinite(value):
                    raise ValueError("non-finite breakdown value")
                method_counts = by_method.setdefault(method, {})
                method_counts[outcome] = method_counts.get(outcome, 0) + max(
                    0, round(value)
                )
        except (
            httpx.HTTPError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise MetricsUnavailable(
                "Prometheus query failed for payment_method_breakdown"
            ) from error
        if payload.get("status") != "success":
            raise MetricsUnavailable(
                "Prometheus returned invalid data for payment_method_breakdown"
            )

        breakdown = tuple(
            PaymentMethodBreakdown(
                payment_method=method,
                counts=self._outcome_counts(values),
            )
            for method, values in sorted(by_method.items(), key=lambda item: item[0].value)
        )
        provider_counts = self._outcome_counts(
            {
                outcome: sum(item.counts.count_for((outcome,)) for item in breakdown)
                for outcome in PaymentOutcome
            }
        )
        return provider_counts, breakdown

    @staticmethod
    def _outcome_counts(values: Mapping[PaymentOutcome, int]) -> OutcomeCounts:
        return OutcomeCounts(
            success=values.get(PaymentOutcome.SUCCESS, 0),
            soft_decline=values.get(PaymentOutcome.SOFT_DECLINE, 0),
            hard_decline=values.get(PaymentOutcome.HARD_DECLINE, 0),
            provider_error=values.get(PaymentOutcome.PROVIDER_ERROR, 0),
            timeout=values.get(PaymentOutcome.TIMEOUT, 0),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
