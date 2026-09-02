from __future__ import annotations

import pytest

from apm_demo.common.contracts import PaymentOutcome, ProviderId
from apm_demo.incidents.application.response_codes import (
    catalog_response_insights,
    unresolved_response_codes,
)
from apm_demo.incidents.domain import ProviderEvent, ResponseCodeDefinition
from apm_demo.incidents.infrastructure import CatalogAmbiguityError, SQLiteIncidentStore


def event(provider: ProviderId = ProviderId.ATLAS_PAY) -> ProviderEvent:
    return ProviderEvent(
        event_id=f"evt-{provider.value}",
        provider=provider,
        outcome=PaymentOutcome.PROVIDER_ERROR,
        response_code="UPSTREAM_ERROR",
        http_status=502,
        processing_time_ms=120,
    )


def test_provider_definition_overrides_global_definition() -> None:
    events = (event(),)
    definitions = (
        ResponseCodeDefinition(
            definition_id="global-upstream",
            response_code="UPSTREAM_ERROR",
            name="Global upstream error",
            description="Generic meaning.",
        ),
        ResponseCodeDefinition(
            definition_id="atlas-upstream",
            provider=ProviderId.ATLAS_PAY,
            response_code="UPSTREAM_ERROR",
            name="AtlasPay upstream error",
            description="AtlasPay-specific meaning.",
        ),
    )

    insights = catalog_response_insights(events, definitions)

    assert insights[0].name == "AtlasPay upstream error"
    assert unresolved_response_codes(events, definitions) == ()


@pytest.mark.asyncio
async def test_sqlite_persists_versioned_response_code_definitions(tmp_path) -> None:
    store = SQLiteIncidentStore(str(tmp_path / "incidents.db"))
    first = await store.create_response_code_version(
        ResponseCodeDefinition(
            definition_id="timeout",
            response_code="PROVIDER_TIMEOUT",
            name="Provider timeout",
            description="First reviewed definition.",
        )
    )
    second = await store.create_response_code_version(
        ResponseCodeDefinition(
            definition_id="timeout",
            response_code="PROVIDER_TIMEOUT",
            name="Provider timeout",
            description="Updated reviewed definition.",
        )
    )

    all_versions = await store.list_response_code_definitions(include_inactive=True)
    active = await store.list_response_code_definitions()

    assert (first.version, second.version) == (1, 2)
    assert len(all_versions) == 2
    assert len(active) == 1
    assert active[0].description == "Updated reviewed definition."


@pytest.mark.asyncio
async def test_sqlite_rejects_ambiguous_active_definition(tmp_path) -> None:
    store = SQLiteIncidentStore(str(tmp_path / "incidents.db"))
    await store.create_response_code_version(
        ResponseCodeDefinition(
            definition_id="first",
            response_code="DO_NOT_HONOR",
            name="Issuer decline",
            description="Reviewed definition.",
        )
    )

    with pytest.raises(CatalogAmbiguityError):
        await store.create_response_code_version(
            ResponseCodeDefinition(
                definition_id="second",
                response_code="DO_NOT_HONOR",
                name="Another issuer decline",
                description="Conflicting definition.",
            )
        )
