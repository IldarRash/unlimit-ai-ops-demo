from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.domain import (
    CatalogAuditAction,
    CatalogAuditEvent,
    ExternalSignal,
    IncidentAuditEvent,
    IncidentFeedback,
    IncidentRecord,
    IncidentStatus,
    KnownErrorRule,
    ProviderEvent,
    ResponseCodeDefinition,
    utc_now,
)


T = TypeVar("T")


class CatalogAmbiguityError(ValueError):
    """Two active rules could win with equal specificity for the same event."""


class SQLiteIncidentStore:
    """Single-instance durable store with WAL and short explicit transactions."""

    def __init__(self, database_path: str) -> None:
        path = Path(database_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._write_lock = asyncio.Lock()
        self._run(self._initialize)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            return operation(connection)

    async def _read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._run, operation)

    async def _write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        async with self._write_lock:
            for attempt in range(3):
                try:
                    return await asyncio.to_thread(self._run, operation)
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error).lower() or attempt == 2:
                        raise
                    await asyncio.sleep(0.05 * (2**attempt))
        raise RuntimeError("unreachable SQLite retry state")

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                alert_fingerprint TEXT,
                status TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS incidents_fingerprint_idx
                ON incidents(fingerprint, status);
            CREATE INDEX IF NOT EXISTS incidents_alert_fingerprint_idx
                ON incidents(alert_fingerprint, last_seen_at DESC);

            CREATE TABLE IF NOT EXISTS incident_audit (
                event_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(incident_id) REFERENCES incidents(incident_id)
            );
            CREATE INDEX IF NOT EXISTS incident_audit_incident_idx
                ON incident_audit(incident_id, occurred_at);

            CREATE TABLE IF NOT EXISTS provider_events (
                event_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS provider_events_recent_idx
                ON provider_events(provider, observed_at DESC);

            CREATE TABLE IF NOT EXISTS external_signals (
                signal_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS external_signals_recent_idx
                ON external_signals(provider, observed_at DESC);

            CREATE TABLE IF NOT EXISTS known_error_rules (
                rule_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                provider TEXT NOT NULL,
                response_code TEXT NOT NULL,
                active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(rule_id, version)
            );
            CREATE INDEX IF NOT EXISTS known_error_rules_match_idx
                ON known_error_rules(provider, response_code, active);

            CREATE TABLE IF NOT EXISTS response_code_definitions (
                definition_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                provider TEXT,
                response_code TEXT NOT NULL,
                active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(definition_id, version)
            );
            CREATE INDEX IF NOT EXISTS response_code_definitions_match_idx
                ON response_code_definitions(response_code, provider, active);

            CREATE TABLE IF NOT EXISTS catalog_audit (
                event_id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS catalog_audit_rule_idx
                ON catalog_audit(rule_id, occurred_at);

            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                delivery_key TEXT PRIMARY KEY,
                payload_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                incident_ids_json TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_feedback (
                feedback_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(incident_id) REFERENCES incidents(incident_id)
            );
            CREATE INDEX IF NOT EXISTS incident_feedback_incident_idx
                ON incident_feedback(incident_id, created_at);
            """
        )
        legacy_rows = connection.execute(
            "SELECT incident_id, payload_json FROM incidents"
        ).fetchall()
        for incident_id, raw_payload in legacy_rows:
            payload = json.loads(raw_payload)
            analysis = payload.get("analysis", {})
            if analysis.get("generated_by") != "mock":
                continue
            analysis["generated_by"] = "unavailable"
            analysis["classification"] = "unavailable"
            analysis["model"] = "legacy-analysis-unavailable-v1"
            connection.execute(
                "UPDATE incidents SET payload_json = ? WHERE incident_id = ?",
                (json.dumps(payload, separators=(",", ":")), incident_id),
            )

    async def ping(self) -> bool:
        return await self._read(
            lambda connection: connection.execute("SELECT 1").fetchone()[0] == 1
        )

    async def get(self, incident_id: str) -> IncidentRecord | None:
        def operation(connection: sqlite3.Connection) -> IncidentRecord | None:
            row = connection.execute(
                "SELECT payload_json FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            return IncidentRecord.model_validate_json(row[0]) if row else None

        return await self._read(operation)

    async def find_active_by_fingerprint(
        self, fingerprint: str
    ) -> IncidentRecord | None:
        def operation(connection: sqlite3.Connection) -> IncidentRecord | None:
            row = connection.execute(
                """
                SELECT payload_json FROM incidents
                WHERE fingerprint = ? AND status != ?
                ORDER BY last_seen_at DESC LIMIT 1
                """,
                (fingerprint, IncidentStatus.RESOLVED.value),
            ).fetchone()
            return IncidentRecord.model_validate_json(row[0]) if row else None

        return await self._read(operation)

    async def find_latest_by_alert_fingerprint(
        self, alert_fingerprint: str
    ) -> IncidentRecord | None:
        def operation(connection: sqlite3.Connection) -> IncidentRecord | None:
            row = connection.execute(
                """
                SELECT payload_json FROM incidents
                WHERE alert_fingerprint = ?
                ORDER BY last_seen_at DESC LIMIT 1
                """,
                (alert_fingerprint,),
            ).fetchone()
            return IncidentRecord.model_validate_json(row[0]) if row else None

        return await self._read(operation)

    @staticmethod
    def _upsert_incident(
        connection: sqlite3.Connection, incident: IncidentRecord
    ) -> None:
        connection.execute(
            """
            INSERT INTO incidents(
                incident_id, fingerprint, alert_fingerprint, status,
                last_seen_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(incident_id) DO UPDATE SET
                fingerprint = excluded.fingerprint,
                alert_fingerprint = excluded.alert_fingerprint,
                status = excluded.status,
                last_seen_at = excluded.last_seen_at,
                payload_json = excluded.payload_json
            """,
            (
                incident.incident_id,
                incident.fingerprint,
                incident.source_alert_fingerprint,
                incident.status.value,
                incident.last_seen_at.isoformat(),
                incident.model_dump_json(),
            ),
        )

    async def save(self, incident: IncidentRecord) -> IncidentRecord:
        def operation(connection: sqlite3.Connection) -> IncidentRecord:
            self._upsert_incident(connection, incident)
            return incident

        return await self._write(operation)

    async def save_with_audit(
        self, incident: IncidentRecord, event: IncidentAuditEvent
    ) -> IncidentRecord:
        def operation(connection: sqlite3.Connection) -> IncidentRecord:
            self._upsert_incident(connection, incident)
            connection.execute(
                """
                INSERT INTO incident_audit(event_id, incident_id, occurred_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.incident_id,
                    event.occurred_at.isoformat(),
                    event.model_dump_json(),
                ),
            )
            return incident

        return await self._write(operation)

    async def list_recent(self, *, limit: int = 50) -> tuple[IncidentRecord, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")

        def operation(connection: sqlite3.Connection) -> tuple[IncidentRecord, ...]:
            rows = connection.execute(
                "SELECT payload_json FROM incidents ORDER BY last_seen_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(IncidentRecord.model_validate_json(row[0]) for row in rows)

        return await self._read(operation)

    async def append(self, event: IncidentAuditEvent) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO incident_audit(event_id, incident_id, occurred_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.incident_id,
                    event.occurred_at.isoformat(),
                    event.model_dump_json(),
                ),
            )

        await self._write(operation)

    async def list_for_incident(
        self, incident_id: str
    ) -> tuple[IncidentAuditEvent, ...]:
        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[IncidentAuditEvent, ...]:
            rows = connection.execute(
                """
                SELECT payload_json FROM incident_audit
                WHERE incident_id = ? ORDER BY occurred_at
                """,
                (incident_id,),
            ).fetchall()
            return tuple(IncidentAuditEvent.model_validate_json(row[0]) for row in rows)

        return await self._read(operation)

    async def append_event(self, event: ProviderEvent) -> ProviderEvent:
        def operation(connection: sqlite3.Connection) -> ProviderEvent:
            connection.execute(
                """
                INSERT OR IGNORE INTO provider_events(
                    event_id, provider, observed_at, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.provider.value,
                    event.observed_at.isoformat(),
                    event.model_dump_json(),
                ),
            )
            return event

        return await self._write(operation)

    async def list_recent_events(
        self, provider: ProviderId, *, limit: int = 20
    ) -> tuple[ProviderEvent, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        def operation(connection: sqlite3.Connection) -> tuple[ProviderEvent, ...]:
            rows = connection.execute(
                """
                SELECT payload_json FROM provider_events
                WHERE provider = ? ORDER BY observed_at DESC LIMIT ?
                """,
                (provider.value, limit),
            ).fetchall()
            return tuple(ProviderEvent.model_validate_json(row[0]) for row in rows)

        return await self._read(operation)

    async def append_external_signal(self, signal: ExternalSignal) -> ExternalSignal:
        def operation(connection: sqlite3.Connection) -> ExternalSignal:
            connection.execute(
                """
                INSERT OR IGNORE INTO external_signals(
                    signal_id, provider, observed_at, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.provider.value,
                    signal.observed_at.isoformat(),
                    signal.model_dump_json(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM external_signals WHERE signal_id = ?",
                (signal.signal_id,),
            ).fetchone()
            assert row is not None
            return ExternalSignal.model_validate_json(row[0])

        return await self._write(operation)

    async def list_recent_external_signals(
        self, provider: ProviderId, *, limit: int = 12
    ) -> tuple[ExternalSignal, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        def operation(connection: sqlite3.Connection) -> tuple[ExternalSignal, ...]:
            rows = connection.execute(
                """
                SELECT payload_json FROM external_signals
                WHERE provider = ? ORDER BY observed_at DESC LIMIT ?
                """,
                (provider.value, limit),
            ).fetchall()
            return tuple(ExternalSignal.model_validate_json(row[0]) for row in rows)

        return await self._read(operation)

    # ProviderEventRepository aliases keep the port vocabulary concise.
    async def list_recent_events_for_provider(
        self, provider: ProviderId, *, limit: int = 20
    ) -> tuple[ProviderEvent, ...]:
        return await self.list_recent_events(provider, limit=limit)

    async def create_response_code_version(
        self, definition: ResponseCodeDefinition
    ) -> ResponseCodeDefinition:
        def operation(connection: sqlite3.Connection) -> ResponseCodeDefinition:
            duplicate = connection.execute(
                """
                SELECT definition_id FROM response_code_definitions
                WHERE active = 1 AND response_code = ? AND provider IS ?
                  AND definition_id != ?
                LIMIT 1
                """,
                (
                    definition.response_code,
                    definition.provider.value if definition.provider else None,
                    definition.definition_id,
                ),
            ).fetchone()
            if duplicate is not None:
                raise CatalogAmbiguityError(
                    f"response code definition overlaps {duplicate[0]}"
                )
            next_version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM response_code_definitions WHERE definition_id = ?
                """,
                (definition.definition_id,),
            ).fetchone()[0]
            stored = definition.model_copy(
                update={"version": next_version, "created_at": utc_now()}
            )
            if stored.active:
                connection.execute(
                    "UPDATE response_code_definitions SET active = 0 WHERE definition_id = ?",
                    (stored.definition_id,),
                )
            connection.execute(
                """
                INSERT INTO response_code_definitions(
                    definition_id, version, provider, response_code, active,
                    created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.definition_id,
                    stored.version,
                    stored.provider.value if stored.provider else None,
                    stored.response_code,
                    int(stored.active),
                    stored.created_at.isoformat(),
                    stored.model_dump_json(),
                ),
            )
            return stored

        return await self._write(operation)

    async def list_response_code_definitions(
        self, *, include_inactive: bool = False
    ) -> tuple[ResponseCodeDefinition, ...]:
        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[ResponseCodeDefinition, ...]:
            where = "" if include_inactive else "WHERE active = 1"
            rows = connection.execute(
                f"""
                SELECT active, payload_json FROM response_code_definitions {where}
                ORDER BY response_code, provider, version DESC
                """
            ).fetchall()
            return tuple(
                ResponseCodeDefinition.model_validate_json(row["payload_json"]).model_copy(
                    update={"active": bool(row["active"])}
                )
                for row in rows
            )

        return await self._read(operation)

    async def resolve_response_codes(
        self, events: tuple[ProviderEvent, ...]
    ) -> tuple[ResponseCodeDefinition, ...]:
        definitions = await self.list_response_code_definitions()
        return tuple(
            definition
            for definition in definitions
            if any(definition.matches(event) for event in events)
        )

    @staticmethod
    def _rules_overlap(left: KnownErrorRule, right: KnownErrorRule) -> bool:
        if left.provider is not right.provider or left.response_code != right.response_code:
            return False
        pairs = (
            (left.outcome, right.outcome),
            (left.payment_method, right.payment_method),
            (left.region, right.region),
        )
        return all(a is None or b is None or a == b for a, b in pairs)

    async def create_version(self, rule: KnownErrorRule) -> KnownErrorRule:
        def operation(connection: sqlite3.Connection) -> KnownErrorRule:
            rows = connection.execute(
                """
                SELECT payload_json FROM known_error_rules
                WHERE active = 1 AND provider = ? AND response_code = ?
                """,
                (rule.provider.value, rule.response_code),
            ).fetchall()
            active_rules = [KnownErrorRule.model_validate_json(row[0]) for row in rows]
            for existing in active_rules:
                if (
                    existing.rule_id != rule.rule_id
                    and existing.specificity == rule.specificity
                    and self._rules_overlap(existing, rule)
                ):
                    raise CatalogAmbiguityError(
                        f"rule overlaps {existing.rule_id} at equal specificity"
                    )
            next_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM known_error_rules WHERE rule_id = ?",
                (rule.rule_id,),
            ).fetchone()[0]
            stored = rule.model_copy(
                update={"version": next_version, "created_at": utc_now()}
            )
            if stored.active:
                connection.execute(
                    "UPDATE known_error_rules SET active = 0 WHERE rule_id = ?",
                    (stored.rule_id,),
                )
            connection.execute(
                """
                INSERT INTO known_error_rules(
                    rule_id, version, provider, response_code, active,
                    created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.rule_id,
                    stored.version,
                    stored.provider.value,
                    stored.response_code,
                    int(stored.active),
                    stored.created_at.isoformat(),
                    stored.model_dump_json(),
                ),
            )
            self._append_catalog_audit(
                connection,
                rule_id=stored.rule_id,
                version=stored.version,
                action=CatalogAuditAction.VERSION_CREATED,
            )
            return stored

        return await self._write(operation)

    async def activate(self, rule_id: str, version: int) -> KnownErrorRule | None:
        def operation(connection: sqlite3.Connection) -> KnownErrorRule | None:
            row = connection.execute(
                """
                SELECT payload_json FROM known_error_rules
                WHERE rule_id = ? AND version = ?
                """,
                (rule_id, version),
            ).fetchone()
            if row is None:
                return None
            stored = KnownErrorRule.model_validate_json(row[0]).model_copy(
                update={"active": True}
            )
            other_rows = connection.execute(
                """
                SELECT payload_json FROM known_error_rules
                WHERE active = 1 AND provider = ? AND response_code = ? AND rule_id != ?
                """,
                (stored.provider.value, stored.response_code, stored.rule_id),
            ).fetchall()
            for other_row in other_rows:
                other = KnownErrorRule.model_validate_json(other_row[0])
                if (
                    other.specificity == stored.specificity
                    and self._rules_overlap(other, stored)
                ):
                    raise CatalogAmbiguityError(
                        f"rule overlaps {other.rule_id} at equal specificity"
                    )
            connection.execute(
                "UPDATE known_error_rules SET active = 0 WHERE rule_id = ?",
                (rule_id,),
            )
            connection.execute(
                """
                UPDATE known_error_rules SET active = 1, payload_json = ?
                WHERE rule_id = ? AND version = ?
                """,
                (stored.model_dump_json(), rule_id, version),
            )
            self._append_catalog_audit(
                connection,
                rule_id=rule_id,
                version=version,
                action=CatalogAuditAction.VERSION_ACTIVATED,
            )
            return stored

        return await self._write(operation)

    async def deactivate(self, rule_id: str) -> bool:
        def operation(connection: sqlite3.Connection) -> bool:
            rows = connection.execute(
                "SELECT version, payload_json FROM known_error_rules WHERE rule_id = ? AND active = 1",
                (rule_id,),
            ).fetchall()
            if not rows:
                return False
            for row in rows:
                stored = KnownErrorRule.model_validate_json(row["payload_json"]).model_copy(
                    update={"active": False}
                )
                connection.execute(
                    """
                    UPDATE known_error_rules SET active = 0, payload_json = ?
                    WHERE rule_id = ? AND version = ?
                    """,
                    (stored.model_dump_json(), rule_id, row["version"]),
                )
            self._append_catalog_audit(
                connection,
                rule_id=rule_id,
                version=None,
                action=CatalogAuditAction.RULE_DEACTIVATED,
            )
            return True

        return await self._write(operation)

    async def list_rules(
        self, *, include_inactive: bool = False
    ) -> tuple[KnownErrorRule, ...]:
        def operation(connection: sqlite3.Connection) -> tuple[KnownErrorRule, ...]:
            where = "" if include_inactive else "WHERE active = 1"
            rows = connection.execute(
                f"""
                SELECT active, payload_json FROM known_error_rules {where}
                ORDER BY rule_id, version DESC
                """
            ).fetchall()
            return tuple(
                KnownErrorRule.model_validate_json(row["payload_json"]).model_copy(
                    update={"active": bool(row["active"])}
                )
                for row in rows
            )

        return await self._read(operation)

    @staticmethod
    def _append_catalog_audit(
        connection: sqlite3.Connection,
        *,
        rule_id: str,
        version: int | None,
        action: CatalogAuditAction,
    ) -> None:
        event = CatalogAuditEvent(
            event_id=f"cat_{uuid4().hex}",
            rule_id=rule_id,
            version=version,
            action=action,
        )
        connection.execute(
            """
            INSERT INTO catalog_audit(event_id, rule_id, occurred_at, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.rule_id,
                event.occurred_at.isoformat(),
                event.model_dump_json(),
            ),
        )

    async def list_catalog_audit(
        self, *, limit: int = 100
    ) -> tuple[CatalogAuditEvent, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        def operation(connection: sqlite3.Connection) -> tuple[CatalogAuditEvent, ...]:
            rows = connection.execute(
                """
                SELECT payload_json FROM catalog_audit
                ORDER BY occurred_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(CatalogAuditEvent.model_validate_json(row[0]) for row in rows)

        return await self._read(operation)

    async def match(self, event: ProviderEvent) -> KnownErrorRule | None:
        rules = await self.list_rules()
        matches = [rule for rule in rules if rule.matches(event)]
        if not matches:
            return None
        best_specificity = max(rule.specificity for rule in matches)
        winners = [rule for rule in matches if rule.specificity == best_specificity]
        if len(winners) > 1:
            raise CatalogAmbiguityError("multiple catalog rules match at equal specificity")
        return winners[0]

    async def claim(self, delivery_key: str, payload_digest: str) -> bool:
        now = utc_now()

        def operation(connection: sqlite3.Connection) -> bool:
            row = connection.execute(
                """
                SELECT payload_digest, state, updated_at FROM webhook_deliveries
                WHERE delivery_key = ?
                """,
                (delivery_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO webhook_deliveries(
                        delivery_key, payload_digest, state, updated_at
                    ) VALUES (?, ?, 'pending', ?)
                    """,
                    (delivery_key, payload_digest, now.isoformat()),
                )
                return True
            if row["payload_digest"] != payload_digest:
                raise ValueError("delivery key reused with different payload")
            updated_at = datetime.fromisoformat(row["updated_at"])
            stale = updated_at < now - timedelta(seconds=60)
            if row["state"] == "failed" or (row["state"] == "pending" and stale):
                connection.execute(
                    """
                    UPDATE webhook_deliveries
                    SET state = 'pending', error = NULL, updated_at = ?
                    WHERE delivery_key = ?
                    """,
                    (now.isoformat(), delivery_key),
                )
                return True
            return False

        return await self._write(operation)

    async def complete(
        self, delivery_key: str, incident_ids: tuple[str, ...]
    ) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET state = 'completed', incident_ids_json = ?, updated_at = ?
                WHERE delivery_key = ?
                """,
                (json.dumps(incident_ids), utc_now().isoformat(), delivery_key),
            )

        await self._write(operation)

    async def fail(self, delivery_key: str, reason: str) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET state = 'failed', error = ?, updated_at = ?
                WHERE delivery_key = ?
                """,
                (reason[:500], utc_now().isoformat(), delivery_key),
            )

        await self._write(operation)

    async def append_feedback(
        self, feedback: IncidentFeedback
    ) -> IncidentFeedback:
        def operation(connection: sqlite3.Connection) -> IncidentFeedback:
            connection.execute(
                """
                INSERT INTO incident_feedback(
                    feedback_id, incident_id, created_at, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    feedback.feedback_id,
                    feedback.incident_id,
                    feedback.created_at.isoformat(),
                    feedback.model_dump_json(),
                ),
            )
            return feedback

        return await self._write(operation)

    async def list_feedback(
        self, incident_id: str
    ) -> tuple[IncidentFeedback, ...]:
        def operation(connection: sqlite3.Connection) -> tuple[IncidentFeedback, ...]:
            rows = connection.execute(
                """
                SELECT payload_json FROM incident_feedback
                WHERE incident_id = ? ORDER BY created_at
                """,
                (incident_id,),
            ).fetchall()
            return tuple(IncidentFeedback.model_validate_json(row[0]) for row in rows)

        return await self._read(operation)
