"""External adapters for metrics, persistence, and AI analysis."""

from apm_demo.incidents.infrastructure.metrics import (
    DeterministicMetricsSource,
    MetricsUnavailable,
    PrometheusMetricsSource,
)
from apm_demo.incidents.infrastructure.analysis import (
    AnalysisUnavailable,
    OpenAIIncidentAnalyzer,
)
from apm_demo.incidents.infrastructure.repositories import (
    InMemoryAuditLog,
    InMemoryExternalSignalRepository,
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
    "InMemoryAuditLog",
    "InMemoryExternalSignalRepository",
    "InMemoryIncidentRepository",
    "OpenAIIncidentAnalyzer",
    "PrometheusMetricsSource",
    "CatalogAmbiguityError",
    "PostgresIncidentStore",
    "SQLiteIncidentStore",
]
