from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class PipelineMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.deliveries = Counter(
            "incident_pipeline_deliveries_total",
            "Alertmanager delivery outcomes.",
            ("outcome",),
            registry=self.registry,
        )
        self.alerts = Counter(
            "incident_pipeline_alerts_total",
            "Alert processing outcomes.",
            ("status", "classification"),
            registry=self.registry,
        )
        self.rejections = Counter(
            "incident_pipeline_rejections_total",
            "Ingress rejections.",
            ("reason",),
            registry=self.registry,
        )
        self.processing_seconds = Histogram(
            "incident_pipeline_processing_seconds",
            "Webhook processing latency.",
            registry=self.registry,
        )
        self.provider_events = Counter(
            "incident_pipeline_provider_events_total",
            "Normalized provider events accepted.",
            ("provider", "outcome"),
            registry=self.registry,
        )
        self.feedback = Counter(
            "incident_pipeline_feedback_total",
            "Operator feedback by verdict.",
            ("verdict",),
            registry=self.registry,
        )
        self.sse_clients = Gauge(
            "incident_pipeline_sse_clients",
            "Connected incident SSE clients.",
            registry=self.registry,
        )
        self.llm_circuit_open = Gauge(
            "incident_pipeline_llm_circuit_open",
            "Whether the LLM circuit breaker is open.",
            registry=self.registry,
        )
