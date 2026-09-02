from __future__ import annotations

from datetime import timedelta

from apm_demo.common.contracts import PaymentOutcome
from apm_demo.incidents.domain import (
    EvidenceBundle,
    IncidentConclusion,
    PaymentMethodImpact,
    SignalType,
)


def affected_outcomes(evidence: EvidenceBundle) -> tuple[PaymentOutcome, ...]:
    selected: set[PaymentOutcome] = set()
    signal_types = {signal.signal_type for signal in evidence.signals}
    if SignalType.ERROR_RATE in signal_types:
        selected.add(PaymentOutcome.PROVIDER_ERROR)
    if SignalType.TIMEOUT_RATE in signal_types:
        selected.add(PaymentOutcome.TIMEOUT)
    if SignalType.DECLINE_RATE in signal_types:
        selected.update((PaymentOutcome.SOFT_DECLINE, PaymentOutcome.HARD_DECLINE))
    return tuple(outcome for outcome in PaymentOutcome if outcome in selected)


def build_conclusion(
    evidence: EvidenceBundle,
    *,
    statement: str,
    evidence_refs: tuple[str, ...],
) -> IncidentConclusion:
    snapshot = evidence.snapshot
    outcomes = affected_outcomes(evidence)
    counts = snapshot.outcome_counts
    if counts is None:
        estimated_share = _estimated_share(evidence, outcomes)
        affected_requests = round(snapshot.total_requests * estimated_share)
        affected_share = (
            affected_requests / snapshot.total_requests
            if snapshot.total_requests
            else None
        )
        method_impacts: tuple[PaymentMethodImpact, ...] = ()
        verification = "estimated" if snapshot.available else "unavailable"
    else:
        affected_requests = counts.count_for(outcomes)
        affected_share = (
            affected_requests / snapshot.total_requests
            if snapshot.total_requests
            else None
        )
        method_impacts = tuple(
            PaymentMethodImpact(
                payment_method=item.payment_method,
                affected_requests=item.counts.count_for(outcomes),
                total_requests=item.counts.total_requests,
                affected_share=(
                    item.counts.count_for(outcomes) / item.counts.total_requests
                    if item.counts.total_requests
                    else None
                ),
            )
            for item in snapshot.payment_method_breakdown
        )
        verification = "verified"

    return IncidentConclusion(
        statement=statement,
        window_started_at=snapshot.observed_at
        - timedelta(seconds=snapshot.window_seconds),
        window_ended_at=snapshot.observed_at,
        window_seconds=snapshot.window_seconds,
        affected_outcomes=outcomes,
        affected_requests=affected_requests,
        total_requests=snapshot.total_requests,
        affected_share=affected_share,
        payment_methods=method_impacts,
        evidence_refs=evidence_refs,
        verification=verification,
    )


def _estimated_share(
    evidence: EvidenceBundle, outcomes: tuple[PaymentOutcome, ...]
) -> float:
    snapshot = evidence.snapshot
    share = 0.0
    if PaymentOutcome.PROVIDER_ERROR in outcomes:
        share += snapshot.error_rate
    if PaymentOutcome.TIMEOUT in outcomes:
        share += snapshot.timeout_rate
    if (
        PaymentOutcome.SOFT_DECLINE in outcomes
        or PaymentOutcome.HARD_DECLINE in outcomes
    ):
        share += max(
            0.0,
            1 - snapshot.success_rate - snapshot.error_rate - snapshot.timeout_rate,
        )
    return min(1.0, share)
