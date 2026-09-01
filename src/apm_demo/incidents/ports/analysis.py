from __future__ import annotations

from typing import Protocol

from apm_demo.incidents.domain import EvidenceBundle, IncidentAnalysis


class AnalysisUnavailable(RuntimeError):
    """The configured analysis provider did not produce a valid result."""


class IncidentAnalyzer(Protocol):
    async def analyze(self, evidence: EvidenceBundle) -> IncidentAnalysis: ...
