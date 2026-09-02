from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from apm_demo.common.contracts import PaymentOutcome
from apm_demo.incidents.domain import (
    AlertSignal,
    MetricSnapshot,
    ProviderEvent,
    SignalType,
)


_OUTCOMES_BY_SIGNAL: dict[SignalType, frozenset[PaymentOutcome]] = {
    SignalType.ERROR_RATE: frozenset({PaymentOutcome.PROVIDER_ERROR}),
    SignalType.TIMEOUT_RATE: frozenset({PaymentOutcome.TIMEOUT}),
    SignalType.DECLINE_RATE: frozenset(
        {PaymentOutcome.SOFT_DECLINE, PaymentOutcome.HARD_DECLINE}
    ),
}


def select_incident_provider_events(
    events: Iterable[ProviderEvent],
    *,
    snapshot: MetricSnapshot,
    signals: tuple[AlertSignal, ...],
    collected_at: datetime,
    limit: int,
) -> tuple[ProviderEvent, ...]:
    """Keep only response events that can support a detected incident signal."""

    relevant_outcomes = frozenset().union(
        *(
            _OUTCOMES_BY_SIGNAL.get(signal.signal_type, frozenset())
            for signal in signals
        )
    )
    if not relevant_outcomes:
        return ()

    window_start = collected_at - timedelta(seconds=snapshot.window_seconds)
    relevant = (
        event
        for event in events
        if window_start <= event.observed_at <= collected_at
        and event.outcome in relevant_outcomes
    )
    return tuple(
        sorted(relevant, key=lambda event: event.observed_at, reverse=True)[:limit]
    )
