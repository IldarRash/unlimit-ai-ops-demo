"""External adapters for metrics, persistence, and AI analysis."""

from apm_demo.incidents.infrastructure.metrics import (
    DeterministicMetricsSource,
    MetricsUnavailable,
    PrometheusMetricsSource,
)
from apm_demo.incidents.infrastructure.analysis import (
    AnalysisUnavailable,
    MockIncidentAnalyzer,
    OpenAIIncidentAnalyzer,
)
from apm_demo.incidents.infrastructure.repositories import (
    InMemoryAuditLog,
    InMemoryIncidentRepository,
)
from apm_demo.incidents.infrastructure.sqlite import (
    CatalogAmbiguityError,
    SQLiteIncidentStore,
)
from apm_demo.incidents.infrastructure.postgres import PostgresIncidentStore

__all__ = [
    "AnalysisUnavailable",
    "DeterministicMetricsSource",
    "MetricsUnavailable",
    "MockIncidentAnalyzer",
    "InMemoryAuditLog",
    "InMemoryIncidentRepository",
    "OpenAIIncidentAnalyzer",
    "PrometheusMetricsSource",
    "CatalogAmbiguityError",
    "PostgresIncidentStore",
    "SQLiteIncidentStore",
]
