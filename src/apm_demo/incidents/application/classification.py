from __future__ import annotations

from dataclasses import dataclass

from apm_demo.incidents.domain import (
    AnalysisProvider,
    CauseHypothesis,
    ClassificationKind,
    EvidenceBundle,
    IncidentAnalysis,
    KnownErrorRule,
    ProviderEvent,
    RemediationAction,
)
from apm_demo.incidents.ports import (
    AnalysisUnavailable,
    IncidentAnalyzer,
    KnownErrorCatalog,
)


@dataclass(frozen=True)
class ClassificationResult:
    kind: ClassificationKind
    analysis: IncidentAnalysis
    matched_event: ProviderEvent | None = None
    matched_rule: KnownErrorRule | None = None


class IncidentClassifier:
    """Routes known provider failures around the LLM and fails unknowns closed."""

    def __init__(
        self, *, catalog: KnownErrorCatalog, analyzer: IncidentAnalyzer
    ) -> None:
        self._catalog = catalog
        self._analyzer = analyzer

    async def classify(self, evidence: EvidenceBundle) -> ClassificationResult:
        try:
            for event in evidence.provider_events:
                rule = await self._catalog.match(event)
                if rule is not None:
                    return ClassificationResult(
                        kind=ClassificationKind.KNOWN,
                        analysis=self._catalog_analysis(rule, event),
                        matched_event=event,
                        matched_rule=rule,
                    )
        except ValueError as error:
            return ClassificationResult(
                kind=ClassificationKind.UNAVAILABLE,
                analysis=self._unavailable_analysis(
                    f"Known-error catalog is ambiguous: {error}"
                ),
            )

        try:
            analysis = await self._analyzer.analyze(evidence)
        except AnalysisUnavailable:
            return ClassificationResult(
                kind=ClassificationKind.UNAVAILABLE,
                analysis=self._unavailable_analysis(
                    "Automated analysis is temporarily unavailable. Inspect evidence and runbook manually."
                ),
            )
        return ClassificationResult(
            kind=ClassificationKind.UNKNOWN,
            analysis=analysis.model_copy(
                update={"classification": ClassificationKind.UNKNOWN}
            ),
        )

    @staticmethod
    def _catalog_analysis(
        rule: KnownErrorRule, event: ProviderEvent
    ) -> IncidentAnalysis:
        return IncidentAnalysis(
            headline=rule.headline,
            summary=rule.summary,
            impact=rule.impact,
            probable_causes=rule.probable_causes,
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="Cataloged provider response",
                    why="The normalized provider event matched the active known-error rule.",
                    evidence_refs=(f"event:{event.event_id}",),
                ),
            ),
            recommended_actions=rule.recommended_actions,
            confidence=rule.confidence,
            generated_by=AnalysisProvider.CATALOG,
            model=f"known-error-catalog-v{rule.version}",
            classification=ClassificationKind.KNOWN,
            catalog_rule_id=rule.rule_id,
            runbook_url=rule.runbook_url,
        )

    @staticmethod
    def _unavailable_analysis(reason: str) -> IncidentAnalysis:
        return IncidentAnalysis(
            headline="Incident analysis unavailable",
            summary=reason,
            impact="Provider impact must be assessed from the attached metrics and events.",
            probable_causes=("Insufficient or temporarily unavailable analysis context",),
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="Automated analysis unavailable",
                    why=reason,
                    evidence_refs=("snapshot",),
                ),
            ),
            recommended_actions=(
                RemediationAction(
                    priority=1,
                    title="Review incident evidence manually",
                    rationale="No automated mitigation is permitted when analysis is unavailable.",
                    safe_to_automate=False,
                ),
            ),
            confidence=0,
            generated_by=AnalysisProvider.UNAVAILABLE,
            model="analysis-unavailable-v1",
            classification=ClassificationKind.UNAVAILABLE,
        )
