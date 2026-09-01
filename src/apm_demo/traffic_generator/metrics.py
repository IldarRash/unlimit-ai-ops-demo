from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from apm_demo.provider_emulator.metrics import LATENCY_BUCKETS


class ClientMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.requests = Counter(
            "apm_client_requests_total",
            "End-to-end payment attempts observed by the traffic generator.",
            ("provider", "payment_method", "outcome"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "apm_client_request_duration_seconds",
            "End-to-end payment request duration observed by the client.",
            ("provider", "payment_method"),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.timeouts = Counter(
            "apm_client_timeouts_total",
            "Client-side payment request timeouts.",
            ("provider", "payment_method"),
            registry=self.registry,
        )
        self.transport_errors = Counter(
            "apm_client_transport_errors_total",
            "Client-side transport errors excluding timeouts.",
            ("provider", "payment_method"),
            registry=self.registry,
        )
        self.provider_health = Gauge(
            "apm_client_provider_health",
            "Latest provider health observed by the traffic generator (1 healthy, 0 unhealthy).",
            ("provider",),
            registry=self.registry,
        )
        self.health_duration = Histogram(
            "apm_client_provider_healthcheck_duration_seconds",
            "Provider health-check duration observed by the client.",
            ("provider",),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.generator_enabled = Gauge(
            "apm_generator_enabled",
            "Whether continuous traffic generation is enabled.",
            registry=self.registry,
        )
        self.target_rps = Gauge(
            "apm_generator_target_requests_per_second",
            "Configured target request rate.",
            registry=self.registry,
        )
