from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from apm_demo.common.contracts import ProviderBehavior, ProviderId


LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.4, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)


class ProviderMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.requests = Counter(
            "apm_provider_requests_total",
            "Payment requests received by a synthetic provider.",
            ("provider", "payment_method"),
            registry=self.registry,
        )
        self.responses = Counter(
            "apm_provider_responses_total",
            "Payment outcomes produced by a synthetic provider.",
            ("provider", "outcome"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "apm_provider_request_duration_seconds",
            "Synthetic provider processing duration.",
            ("provider", "payment_method"),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.health_checks = Counter(
            "apm_provider_health_checks_total",
            "Provider health checks by result.",
            ("provider", "result"),
            registry=self.registry,
        )
        self.health_duration = Histogram(
            "apm_provider_healthcheck_duration_seconds",
            "Synthetic provider health-check duration.",
            ("provider",),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.configured_latency = Gauge(
            "apm_provider_configured_latency_seconds",
            "Configured base latency for the synthetic provider.",
            ("provider",),
            registry=self.registry,
        )
        self.configured_timeout_rate = Gauge(
            "apm_provider_configured_timeout_ratio",
            "Configured timeout probability for the synthetic provider.",
            ("provider",),
            registry=self.registry,
        )

    def update_behavior(self, provider: ProviderId, behavior: ProviderBehavior) -> None:
        self.configured_latency.labels(provider=provider.value).set(
            behavior.base_latency_ms / 1_000
        )
        self.configured_timeout_rate.labels(provider=provider.value).set(
            behavior.timeout_rate
        )
