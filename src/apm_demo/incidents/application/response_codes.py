from __future__ import annotations

from collections.abc import Iterable

from apm_demo.incidents.domain import (
    ProviderEvent,
    ResponseCodeDefinition,
    ResponseCodeInsight,
    ResponseInsightSource,
)


def catalog_response_insights(
    events: Iterable[ProviderEvent],
    definitions: Iterable[ResponseCodeDefinition],
) -> tuple[ResponseCodeInsight, ...]:
    event_tuple = tuple(events)
    grouped = _group_events(event_tuple)
    resolved = _definitions_by_code(event_tuple, definitions)
    return tuple(
        ResponseCodeInsight(
            response_code=code,
            name=definition.name,
            description=definition.description,
            source=ResponseInsightSource.CATALOG,
            evidence_refs=tuple(
                f"event:{event.event_id}" for event in grouped[code][:24]
            ),
            catalog_rule_id=f"{definition.definition_id}:v{definition.version}",
        )
        for code, definition in sorted(resolved.items())
    )


def unavailable_response_insights(
    events: Iterable[ProviderEvent],
    definitions: Iterable[ResponseCodeDefinition] = (),
) -> tuple[ResponseCodeInsight, ...]:
    event_tuple = tuple(events)
    grouped = _group_events(event_tuple)
    catalog = {
        item.response_code: item
        for item in catalog_response_insights(event_tuple, definitions)
    }
    return tuple(
        catalog.get(code)
        or ResponseCodeInsight(
            response_code=code,
            name="Uncatalogued provider response",
            description=(
                "No reviewed database catalog definition or validated model "
                "explanation is available for this response code."
            ),
            source=ResponseInsightSource.UNAVAILABLE,
            evidence_refs=tuple(
                f"event:{event.event_id}" for event in grouped[code][:24]
            ),
        )
        for code in sorted(grouped)
    )


def unresolved_response_codes(
    events: Iterable[ProviderEvent],
    definitions: Iterable[ResponseCodeDefinition] = (),
) -> tuple[str, ...]:
    event_tuple = tuple(events)
    resolved = _definitions_by_code(event_tuple, definitions)
    return tuple(
        code for code in sorted(_group_events(event_tuple)) if code not in resolved
    )


def _definitions_by_code(
    events: tuple[ProviderEvent, ...],
    definitions: Iterable[ResponseCodeDefinition],
) -> dict[str, ResponseCodeDefinition]:
    selected: dict[str, ResponseCodeDefinition] = {}
    for definition in definitions:
        if not any(definition.matches(event) for event in events):
            continue
        existing = selected.get(definition.response_code)
        if existing is None or definition.specificity > existing.specificity:
            selected[definition.response_code] = definition
            continue
        if definition.specificity == existing.specificity and (
            definition.definition_id != existing.definition_id
            or definition.version != existing.version
        ):
            raise ValueError(
                "multiple response-code definitions match at equal specificity"
            )
    return selected


def _group_events(events: Iterable[ProviderEvent]) -> dict[str, list[ProviderEvent]]:
    grouped: dict[str, list[ProviderEvent]] = {}
    for event in events:
        grouped.setdefault(event.response_code, []).append(event)
    return grouped
