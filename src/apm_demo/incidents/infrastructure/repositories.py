from __future__ import annotations

import asyncio

from apm_demo.incidents.domain import (
    IncidentAuditEvent,
    IncidentRecord,
    IncidentStatus,
)


class InMemoryIncidentRepository:
    """Process-local adapter; replaceable through the repository port."""

    def __init__(self) -> None:
        self._records: dict[str, IncidentRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, incident_id: str) -> IncidentRecord | None:
        async with self._lock:
            record = self._records.get(incident_id)
            return record.model_copy(deep=True) if record else None

    async def find_active_by_fingerprint(
        self, fingerprint: str
    ) -> IncidentRecord | None:
        async with self._lock:
            for record in self._records.values():
                if (
                    record.fingerprint == fingerprint
                    and record.status is not IncidentStatus.RESOLVED
                ):
                    return record.model_copy(deep=True)
        return None

    async def find_latest_by_alert_fingerprint(
        self, alert_fingerprint: str
    ) -> IncidentRecord | None:
        async with self._lock:
            matches = [
                record
                for record in self._records.values()
                if record.source_alert_fingerprint == alert_fingerprint
            ]
            if not matches:
                return None
            latest = max(matches, key=lambda item: item.last_seen_at)
            return latest.model_copy(deep=True)

    async def save(self, incident: IncidentRecord) -> IncidentRecord:
        async with self._lock:
            stored = incident.model_copy(deep=True)
            self._records[stored.incident_id] = stored
            return stored.model_copy(deep=True)

    async def save_with_audit(
        self, incident: IncidentRecord, event: IncidentAuditEvent
    ) -> IncidentRecord:
        async with self._lock:
            stored = incident.model_copy(deep=True)
            self._records[stored.incident_id] = stored
        return stored.model_copy(deep=True)

    async def list_recent(self, *, limit: int = 50) -> tuple[IncidentRecord, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        async with self._lock:
            records = sorted(
                self._records.values(),
                key=lambda item: item.last_seen_at,
                reverse=True,
            )[:limit]
            return tuple(record.model_copy(deep=True) for record in records)


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._events: list[IncidentAuditEvent] = []
        self._lock = asyncio.Lock()

    async def append(self, event: IncidentAuditEvent) -> None:
        async with self._lock:
            self._events.append(event.model_copy(deep=True))

    async def list_for_incident(
        self, incident_id: str
    ) -> tuple[IncidentAuditEvent, ...]:
        async with self._lock:
            return tuple(
                event.model_copy(deep=True)
                for event in self._events
                if event.incident_id == incident_id
            )
