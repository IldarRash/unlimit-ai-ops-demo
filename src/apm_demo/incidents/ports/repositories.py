from __future__ import annotations

from typing import Protocol

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.domain import (
    IncidentAuditEvent,
    IncidentFeedback,
    IncidentRecord,
    KnownErrorRule,
    ProviderEvent,
)


class IncidentRepository(Protocol):
    async def get(self, incident_id: str) -> IncidentRecord | None: ...

    async def find_active_by_fingerprint(
        self, fingerprint: str
    ) -> IncidentRecord | None: ...

    async def find_latest_by_alert_fingerprint(
        self, alert_fingerprint: str
    ) -> IncidentRecord | None: ...

    async def save(self, incident: IncidentRecord) -> IncidentRecord: ...

    async def save_with_audit(
        self, incident: IncidentRecord, event: IncidentAuditEvent
    ) -> IncidentRecord: ...

    async def list_recent(self, *, limit: int = 50) -> tuple[IncidentRecord, ...]: ...


class AuditLog(Protocol):
    async def append(self, event: IncidentAuditEvent) -> None: ...

    async def list_for_incident(
        self, incident_id: str
    ) -> tuple[IncidentAuditEvent, ...]: ...


class ProviderEventRepository(Protocol):
    async def append_event(self, event: ProviderEvent) -> ProviderEvent: ...

    async def list_recent_events(
        self, provider: ProviderId, *, limit: int = 20
    ) -> tuple[ProviderEvent, ...]: ...


class KnownErrorCatalog(Protocol):
    async def create_version(self, rule: KnownErrorRule) -> KnownErrorRule: ...

    async def activate(self, rule_id: str, version: int) -> KnownErrorRule | None: ...

    async def deactivate(self, rule_id: str) -> bool: ...

    async def list_rules(self, *, include_inactive: bool = False) -> tuple[KnownErrorRule, ...]: ...

    async def match(self, event: ProviderEvent) -> KnownErrorRule | None: ...


class DeliveryLedger(Protocol):
    async def claim(self, delivery_key: str, payload_digest: str) -> bool: ...

    async def complete(self, delivery_key: str, incident_ids: tuple[str, ...]) -> None: ...

    async def fail(self, delivery_key: str, reason: str) -> None: ...


class FeedbackRepository(Protocol):
    async def append_feedback(self, feedback: IncidentFeedback) -> IncidentFeedback: ...

    async def list_feedback(self, incident_id: str) -> tuple[IncidentFeedback, ...]: ...
