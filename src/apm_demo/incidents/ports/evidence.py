from __future__ import annotations

from typing import Protocol

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.domain import MetricSnapshot


class MetricsUnavailable(RuntimeError):
    """Prometheus could not provide a complete, trustworthy evidence snapshot."""


class MetricsSource(Protocol):
    async def collect(
        self, provider: ProviderId, *, window_seconds: int
    ) -> MetricSnapshot: ...
