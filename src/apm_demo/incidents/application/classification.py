from __future__ import annotations

from dataclasses import dataclass

from apm_demo.incidents.application.conclusion import build_conclusion
from apm_demo.incidents.application.response_codes import unavailable_response_insights
from apm_demo.incidents.domain import (
    AnalysisProvider,
    CauseHypothesis,
    ClassificationKind,
    EvidenceBundle,
    IncidentAnalysis,
    KnownErrorRule,
    OperatorDisposition,
    ProviderEvent,
    RemediationAction,
    ResponseCodeDefinition,
)
from apm_demo.incidents.ports import (
    AnalysisUnavailable,
    IncidentAnalyzer,
    KnownErrorCatalog,
    ResponseCodeCatalog,
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
        self,
        *,
        catalog: KnownErrorCatalog,
        response_catalog: ResponseCodeCatalog,
        analyzer: IncidentAnalyzer,
    ) -> None:
        self._catalog = catalog
        self._response_catalog = response_catalog
        self._analyzer = analyzer

    async def classify(self, evidence: EvidenceBundle) -> ClassificationResult:
        try:
            definitions = await self._response_catalog.resolve_response_codes(
                evidence.provider_events
            )
            matched: tuple[ProviderEvent, KnownErrorRule] | None = None
            for event in evidence.provider_events:
                rule = await self._catalog.match(event)
                if rule is not None and matched is None:
                    matched = (event, rule)
            if matched is not None:
                event, rule = matched
                return ClassificationResult(
                    kind=ClassificationKind.KNOWN,
                    analysis=self._catalog_analysis(
                        rule, event, evidence, definitions
                    ),
                    matched_event=event,
                    matched_rule=rule,
                )
        except ValueError as error:
            return ClassificationResult(
                kind=ClassificationKind.UNAVAILABLE,
                analysis=self._unavailable_analysis(
                    f"Incident catalog is ambiguous: {error}", evidence, ()
                ),
            )

        try:
            analysis = await self._analyzer.analyze(
                evidence, response_code_definitions=definitions
            )
        except AnalysisUnavailable:
            return ClassificationResult(
                kind=ClassificationKind.UNAVAILABLE,
                analysis=self._unavailable_analysis(
                    "No reviewed incident rule matched, and a complete incident assessment could not be produced.",
                    evidence,
                    definitions,
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
        rule: KnownErrorRule,
        event: ProviderEvent,
        evidence: EvidenceBundle,
        definitions: tuple[ResponseCodeDefinition, ...],
    ) -> IncidentAnalysis:
        evidence_refs = ("snapshot", f"event:{event.event_id}")
        return IncidentAnalysis(
            headline=rule.headline,
            summary=rule.summary,
            impact=rule.impact,
            operator_disposition=rule.operator_disposition,
            operator_decision=rule.operator_decision,
            probable_causes=rule.probable_causes,
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="Cataloged provider response",
                    why="The normalized provider event matched the active known-error rule.",
                    evidence_refs=(f"event:{event.event_id}",),
                ),
            ),
            conclusion=build_conclusion(
                evidence,
                statement=rule.summary,
                evidence_refs=evidence_refs,
            ),
            response_code_insights=unavailable_response_insights(
                evidence.provider_events, definitions
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
    def _unavailable_analysis(
        reason: str,
        evidence: EvidenceBundle,
        definitions: tuple[ResponseCodeDefinition, ...],
    ) -> IncidentAnalysis:
        return IncidentAnalysis(
            headline="Manual review required",
            summary=reason,
            impact="Measured signals indicate a possible provider impact, but the cause is not yet classified.",
            operator_disposition=OperatorDisposition.MANUAL_REVIEW,
            operator_decision=(
                "Review the measured impact, recent provider responses, and provider status "
                "before changing routing or retry behaviour."
            ),
            probable_causes=("Unclassified provider or integration condition",),
            causes=(
                CauseHypothesis(
                    category="technical",
                    title="Cause not yet classified",
                    why=(
                        "The measured signals crossed an incident threshold, but no reviewed "
                        "rule produced a confirmed explanation."
                    ),
                    evidence_refs=("snapshot",),
                ),
            ),
            conclusion=build_conclusion(
                evidence,
                statement=reason,
                evidence_refs=("snapshot",),
            ),
            response_code_insights=unavailable_response_insights(
                evidence.provider_events, definitions
            ),
            recommended_actions=(
                RemediationAction(
                    priority=1,
                    title="Check provider status and recent changes",
                    rationale=(
                        "Confirm whether the provider or an internal deployment explains the "
                        "measured degradation before mitigation."
                    ),
                    safe_to_automate=False,
                ),
            ),
            confidence=0,
            generated_by=AnalysisProvider.UNAVAILABLE,
            model="analysis-unavailable-v1",
            classification=ClassificationKind.UNAVAILABLE,
        )
