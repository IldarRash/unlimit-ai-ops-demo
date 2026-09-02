from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from apm_demo.common.contracts import HealthMode, ProviderBehavior, ProviderId, ScenarioName


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
        self.configured_error_rate = Gauge(
            "apm_provider_configured_error_ratio",
            "Configured technical provider-error probability.",
            ("provider",),
            registry=self.registry,
        )
        self.configured_health_mode = Gauge(
            "apm_provider_configured_health_mode",
            "One-hot configured provider health-check mode.",
            ("provider", "mode"),
            registry=self.registry,
        )
        self.scenarios_applied = Counter(
            "apm_demo_scenario_applied_total",
            "Operator-applied demo scenarios.",
            ("provider", "scenario"),
            registry=self.registry,
        )
        self.active_scenario = Gauge(
            "apm_demo_active_scenario",
            "One-hot active demo scenario for each provider.",
            ("provider", "scenario"),
            registry=self.registry,
        )

    def update_behavior(self, provider: ProviderId, behavior: ProviderBehavior) -> None:
        self.configured_latency.labels(provider=provider.value).set(
            behavior.base_latency_ms / 1_000
        )
        self.configured_timeout_rate.labels(provider=provider.value).set(
            behavior.timeout_rate
        )
        self.configured_error_rate.labels(provider=provider.value).set(
            behavior.provider_error_rate
        )
        for mode in HealthMode:
            self.configured_health_mode.labels(
                provider=provider.value, mode=mode.value
            ).set(1 if behavior.health_mode is mode else 0)

    def set_active_scenario(
        self, provider: ProviderId, scenario: ScenarioName
    ) -> None:
        for candidate in ScenarioName:
            self.active_scenario.labels(
                provider=provider.value, scenario=candidate.value
            ).set(1 if candidate is scenario else 0)

    def record_scenario(
        self, provider: ProviderId, scenario: ScenarioName
    ) -> None:
        self.scenarios_applied.labels(
            provider=provider.value, scenario=scenario.value
        ).inc()
        self.set_active_scenario(provider, scenario)
