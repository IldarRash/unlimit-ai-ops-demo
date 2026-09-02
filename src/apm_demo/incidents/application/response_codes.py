from __future__ import annotations

from collections.abc import Iterable

from apm_demo.incidents.domain import (
    KnownErrorRule,
    ProviderEvent,
    ResponseCodeInsight,
    ResponseInsightSource,
)


BUILTIN_RESPONSE_GLOSSARY: dict[str, tuple[str, str]] = {
    "APPROVED": (
        "Approved payment",
        "The provider accepted the payment attempt and returned a successful response.",
    ),
    "DO_NOT_HONOR": (
        "Issuer declined",
        "The payment was declined without a more specific issuer reason; operator action should focus on aggregate patterns rather than retrying an individual payment.",
    ),
    "INVALID_ACCOUNT": (
        "Invalid account details",
        "The provider reports that the submitted account or payment credentials are not valid for processing.",
    ),
    "PROVIDER_TIMEOUT": (
        "Provider timeout",
        "The provider did not return a payment response before the configured client timeout.",
    ),
    "TRANSPORT_ERROR": (
        "Transport failure",
        "The client could not complete the network exchange with the provider.",
    ),
}


def builtin_response_insights(
    events: Iterable[ProviderEvent],
) -> tuple[ResponseCodeInsight, ...]:
    event_tuple = tuple(events)
    grouped = _group_events(event_tuple)
    return tuple(
        ResponseCodeInsight(
            response_code=code,
            name=BUILTIN_RESPONSE_GLOSSARY[code][0],
            description=BUILTIN_RESPONSE_GLOSSARY[code][1],
            source=ResponseInsightSource.CATALOG,
            evidence_refs=tuple(
                f"event:{event.event_id}" for event in grouped[code][:24]
            ),
            catalog_rule_id="builtin-response-glossary-v1",
        )
        for code in sorted(grouped)
        if code in BUILTIN_RESPONSE_GLOSSARY
    )


def catalog_response_insight(
    rule: KnownErrorRule, events: Iterable[ProviderEvent]
) -> ResponseCodeInsight:
    matching = tuple(event for event in events if rule.matches(event))
    if not matching:
        raise ValueError("catalog response insight requires a matching event")
    return ResponseCodeInsight(
        response_code=rule.response_code,
        name=rule.response_name or rule.headline,
        description=rule.response_description or rule.summary,
        source=ResponseInsightSource.CATALOG,
        evidence_refs=tuple(f"event:{event.event_id}" for event in matching[:24]),
        catalog_rule_id=rule.rule_id,
    )


def unavailable_response_insights(
    events: Iterable[ProviderEvent],
) -> tuple[ResponseCodeInsight, ...]:
    event_tuple = tuple(events)
    grouped = _group_events(event_tuple)
    builtin = {
        item.response_code: item for item in builtin_response_insights(event_tuple)
    }
    return tuple(
        builtin.get(code)
        or ResponseCodeInsight(
            response_code=code,
            name="Uncatalogued provider response",
            description=(
                "No reviewed catalog definition or validated model explanation is "
                "available for this response code."
            ),
            source=ResponseInsightSource.UNAVAILABLE,
            evidence_refs=tuple(
                f"event:{event.event_id}" for event in grouped[code][:24]
            ),
        )
        for code in sorted(grouped)
    )


def unresolved_response_codes(events: Iterable[ProviderEvent]) -> tuple[str, ...]:
    return tuple(
        code
        for code in sorted(_group_events(events))
        if code not in BUILTIN_RESPONSE_GLOSSARY
    )


def _group_events(events: Iterable[ProviderEvent]) -> dict[str, list[ProviderEvent]]:
    grouped: dict[str, list[ProviderEvent]] = {}
    for event in events:
        grouped.setdefault(event.response_code, []).append(event)
    return grouped
