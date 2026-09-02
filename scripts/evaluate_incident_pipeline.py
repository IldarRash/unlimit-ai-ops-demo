from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from statistics import mean
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apm_demo.common.contracts import PaymentOutcome, ProviderId
from apm_demo.incidents.application.classification import IncidentClassifier
from apm_demo.incidents.application.detection import AnomalyDetector, incident_severity
from apm_demo.incidents.domain import (
    AnalysisProvider,
    CauseHypothesis,
    EvidenceBundle,
    ExternalSignal,
    IncidentAnalysis,
    IncidentSeverity,
    KnownErrorRule,
    MetricSnapshot,
    ProviderEvent,
    RemediationAction,
)


DEFAULT_GOLDEN_SET = ROOT / "tests" / "fixtures" / "golden_incidents.json"


class StaticCatalog:
    def __init__(self, rule: KnownErrorRule) -> None:
        self.rule = rule

    async def match(self, event: ProviderEvent) -> KnownErrorRule | None:
        return self.rule if self.rule.matches(event) else None


class OfflineAnalyzer:
    """Deterministic evaluator stub; it never performs network requests."""

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, evidence: EvidenceBundle) -> IncidentAnalysis:
        self.calls += 1
        business = any(signal.signal_type.value == "decline-rate" for signal in evidence.signals)
        category = "business" if business else "technical"
        if evidence.external_signals:
            evidence_ref = f"external:{evidence.external_signals[0].signal_id}"
        elif evidence.provider_events:
            evidence_ref = f"event:{evidence.provider_events[0].event_id}"
        else:
            evidence_ref = f"signal:{evidence.signals[0].signal_type.value}"
        return IncidentAnalysis(
            headline="Offline golden-set analysis",
            summary="Deterministic fixture output used to evaluate routing and contracts.",
            impact="This output is not a model-quality claim.",
            probable_causes=(f"{category.title()} incident hypothesis",),
            causes=(
                CauseHypothesis(
                    category=category,
                    title=f"{category.title()} incident hypothesis",
                    why="The fixture analyzer follows the expected signal class.",
                    evidence_refs=(evidence_ref,),
                ),
            ),
            recommended_actions=(
                RemediationAction(
                    priority=1,
                    title="Review normalized evidence",
                    rationale="Human verification remains required before mitigation.",
                ),
            ),
            confidence=0.8,
            generated_by=AnalysisProvider.OPENAI,
            model="offline-golden-fixture",
        )


def known_rule() -> KnownErrorRule:
    return KnownErrorRule(
        rule_id="atlas-upstream-error",
        provider=ProviderId.ATLAS_PAY,
        response_code="UPSTREAM_ERROR",
        outcome=PaymentOutcome.PROVIDER_ERROR,
        headline="Known AtlasPay upstream error",
        summary="The provider returned a documented upstream error.",
        impact="AtlasPay attempts can fail.",
        probable_causes=("Documented provider degradation",),
        recommended_actions=(
            RemediationAction(
                priority=1,
                title="Open the provider runbook",
                rationale="Use the reviewed deterministic response.",
            ),
        ),
        confidence=0.99,
    )


async def evaluate(cases: list[dict[str, object]]) -> dict[str, object]:
    detector = AnomalyDetector()
    analyzer = OfflineAnalyzer()
    classifier = IncidentClassifier(catalog=StaticCatalog(known_rule()), analyzer=analyzer)
    results: list[dict[str, object]] = []
    latencies_ms: list[float] = []

    for case in cases:
        started = perf_counter()
        snapshot = MetricSnapshot.model_validate(case["snapshot"])
        provider_events = tuple(
            ProviderEvent.model_validate(item) for item in case.get("provider_events", [])
        )
        external_signals = tuple(
            ExternalSignal.model_validate(item) for item in case.get("external_signals", [])
        )
        signals = detector.detect(snapshot)
        actual_signal_set = sorted(signal.signal_type.value for signal in signals)
        expected_signal_set = sorted(case["expected_signals"])
        actual_severity = (
            incident_severity(signals).value if signals else IncidentSeverity.INFO.value
        )
        route = "none"
        cause_category = None
        if signals:
            classified = await classifier.classify(
                EvidenceBundle(
                    snapshot=snapshot,
                    signals=signals,
                    provider_events=provider_events,
                    external_signals=external_signals,
                    source="offline-golden-set",
                )
            )
            route = classified.analysis.generated_by.value
            cause_category = classified.analysis.causes[0].category
        latency_ms = (perf_counter() - started) * 1000
        latencies_ms.append(latency_ms)
        checks = {
            "signals": actual_signal_set == expected_signal_set,
            "severity": actual_severity == case["expected_severity"],
            "route": route == case["expected_route"],
            "cause_category": cause_category == case["expected_cause_category"],
        }
        results.append(
            {
                "id": case["id"],
                "passed": all(checks.values()),
                "checks": checks,
                "actual": {
                    "signals": actual_signal_set,
                    "severity": actual_severity,
                    "route": route,
                    "cause_category": cause_category,
                },
            }
        )

    passed = sum(1 for result in results if result["passed"])
    sorted_latency = sorted(latencies_ms)
    p95_index = max(0, min(len(sorted_latency) - 1, int(0.95 * len(sorted_latency))))
    return {
        "scope": "offline deterministic routing and schema evaluation; no OpenAI request",
        "cases": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0,
        "actual_offline_analyzer_calls": analyzer.calls,
        "expected_openai_routes": sum(1 for case in cases if case["expected_route"] == "openai"),
        "offline_latency_ms": {
            "mean": round(mean(latencies_ms), 3),
            "p95": round(sorted_latency[p95_index], 3),
        },
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the incident pipeline against a golden set.")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = json.loads(args.golden_set.read_text(encoding="utf-8"))
    report = asyncio.run(evaluate(cases))
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
