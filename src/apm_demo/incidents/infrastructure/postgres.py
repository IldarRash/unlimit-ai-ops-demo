from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

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
    utc_now,
)
from apm_demo.incidents.infrastructure.sqlite import CatalogAmbiguityError


_SCHEMA_VERSION = 3
_MIGRATION_LOCK = "apm_demo_incidents_schema"


class PostgresIncidentStore:
    """PostgreSQL implementation of all incident persistence ports.

    The pool is opened and its schema is initialized explicitly from the app
    lifespan, so a container can be built synchronously without connecting to
    the database. JSONB keeps the domain record as the source of truth while
    indexed columns preserve the query paths used by the API.
    """

    def __init__(self, database_url: str, *, max_pool_size: int = 5) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=max_pool_size,
            open=False,
            kwargs={"row_factory": tuple_row},
        )
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._pool.open()
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", (_MIGRATION_LOCK,)
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                for version in range(1, _SCHEMA_VERSION + 1):
                    migration = await connection.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = %s",
                        (version,),
                    )
                    if await migration.fetchone() is not None:
                        continue
                    if version == 1:
                        await self._create_schema(connection)
                    elif version == 2:
                        await self._migrate_legacy_analysis_provider(connection)
                    elif version == 3:
                        await self._create_external_signals(connection)
                    await connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (%s)",
                        (version,),
                    )
        self._initialized = True

    async def aclose(self) -> None:
        await self._pool.close()

    async def _create_schema(self, connection: AsyncConnection[Any]) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                alert_fingerprint TEXT,
                status TEXT NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS incidents_fingerprint_idx
            ON incidents(fingerprint, status)
            """,
            """
            CREATE INDEX IF NOT EXISTS incidents_alert_fingerprint_idx
            ON incidents(alert_fingerprint, last_seen_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS incident_audit (
                event_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                occurred_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS incident_audit_incident_idx
            ON incident_audit(incident_id, occurred_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS provider_events (
                event_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS provider_events_recent_idx
            ON provider_events(provider, observed_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS external_signals (
                signal_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS external_signals_recent_idx
            ON external_signals(provider, observed_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS known_error_rules (
                rule_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                provider TEXT NOT NULL,
                response_code TEXT NOT NULL,
                active BOOLEAN NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL,
                PRIMARY KEY(rule_id, version)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS known_error_rules_match_idx
            ON known_error_rules(provider, response_code, active)
            """,
            """
            CREATE TABLE IF NOT EXISTS catalog_audit (
                event_id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS catalog_audit_rule_idx
            ON catalog_audit(rule_id, occurred_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                delivery_key TEXT PRIMARY KEY,
                payload_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                incident_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                error TEXT,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS incident_feedback (
                feedback_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                created_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS incident_feedback_incident_idx
            ON incident_feedback(incident_id, created_at)
            """,
        )
        for statement in statements:
            await connection.execute(statement)

    @staticmethod
    async def _migrate_legacy_analysis_provider(
        connection: AsyncConnection[Any],
    ) -> None:
        await connection.execute(
            """
            UPDATE incidents
            SET payload_json = jsonb_set(
                jsonb_set(
                    jsonb_set(
                        payload_json,
                        '{analysis,generated_by}',
                        '"unavailable"'::jsonb
                    ),
                    '{analysis,classification}',
                    '"unavailable"'::jsonb
                ),
                '{analysis,model}',
                '"legacy-analysis-unavailable-v1"'::jsonb
            )
            WHERE payload_json #>> '{analysis,generated_by}' = 'mock'
            """
        )

    @staticmethod
    async def _create_external_signals(connection: AsyncConnection[Any]) -> None:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS external_signals (
                signal_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                payload_json JSONB NOT NULL
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS external_signals_recent_idx
            ON external_signals(provider, observed_at DESC)
            """
        )

    async def ping(self) -> bool:
        async with self._pool.connection() as connection:
            result = await connection.execute("SELECT 1")
            row = await result.fetchone()
            return row is not None and row[0] == 1

    async def get(self, incident_id: str) -> IncidentRecord | None:
        row = await self._fetchone(
            "SELECT payload_json FROM incidents WHERE incident_id = %s", (incident_id,)
        )
        return IncidentRecord.model_validate(row[0]) if row else None

    async def find_active_by_fingerprint(
        self, fingerprint: str
    ) -> IncidentRecord | None:
        row = await self._fetchone(
            """
            SELECT payload_json FROM incidents
            WHERE fingerprint = %s AND status <> %s
            ORDER BY last_seen_at DESC LIMIT 1
            """,
            (fingerprint, IncidentStatus.RESOLVED.value),
        )
        return IncidentRecord.model_validate(row[0]) if row else None

    async def find_latest_by_alert_fingerprint(
        self, alert_fingerprint: str
    ) -> IncidentRecord | None:
        row = await self._fetchone(
            """
            SELECT payload_json FROM incidents
            WHERE alert_fingerprint = %s
            ORDER BY last_seen_at DESC LIMIT 1
            """,
            (alert_fingerprint,),
        )
        return IncidentRecord.model_validate(row[0]) if row else None

    async def save(self, incident: IncidentRecord) -> IncidentRecord:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await self._upsert_incident(connection, incident)
        return incident

    async def save_with_audit(
        self, incident: IncidentRecord, event: IncidentAuditEvent
    ) -> IncidentRecord:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await self._upsert_incident(connection, incident)
                await self._append_audit(connection, event)
        return incident

    async def list_recent(self, *, limit: int = 50) -> tuple[IncidentRecord, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = await self._fetchall(
            "SELECT payload_json FROM incidents ORDER BY last_seen_at DESC LIMIT %s",
            (limit,),
        )
        return tuple(IncidentRecord.model_validate(row[0]) for row in rows)

    async def append(self, event: IncidentAuditEvent) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await self._append_audit(connection, event)

    async def list_for_incident(
        self, incident_id: str
    ) -> tuple[IncidentAuditEvent, ...]:
        rows = await self._fetchall(
            """
            SELECT payload_json FROM incident_audit
            WHERE incident_id = %s ORDER BY occurred_at
            """,
            (incident_id,),
        )
        return tuple(IncidentAuditEvent.model_validate(row[0]) for row in rows)

    async def append_event(self, event: ProviderEvent) -> ProviderEvent:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO provider_events(event_id, provider, observed_at, payload_json)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (
                        event.event_id,
                        event.provider.value,
                        event.observed_at,
                        self._json(event),
                    ),
                )
        return event

    async def list_recent_events(
        self, provider: ProviderId, *, limit: int = 20
    ) -> tuple[ProviderEvent, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = await self._fetchall(
            """
            SELECT payload_json FROM provider_events
            WHERE provider = %s ORDER BY observed_at DESC LIMIT %s
            """,
            (provider.value, limit),
        )
        return tuple(ProviderEvent.model_validate(row[0]) for row in rows)

    async def append_external_signal(self, signal: ExternalSignal) -> ExternalSignal:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO external_signals(signal_id, provider, observed_at, payload_json)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(signal_id) DO NOTHING
                    """,
                    (
                        signal.signal_id,
                        signal.provider.value,
                        signal.observed_at,
                        self._json(signal),
                    ),
                )
        row = await self._fetchone(
            "SELECT payload_json FROM external_signals WHERE signal_id = %s",
            (signal.signal_id,),
        )
        assert row is not None
        return ExternalSignal.model_validate(row[0])

    async def list_recent_external_signals(
        self, provider: ProviderId, *, limit: int = 12
    ) -> tuple[ExternalSignal, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = await self._fetchall(
            """
            SELECT payload_json FROM external_signals
            WHERE provider = %s ORDER BY observed_at DESC LIMIT %s
            """,
            (provider.value, limit),
        )
        return tuple(ExternalSignal.model_validate(row[0]) for row in rows)

    async def list_recent_events_for_provider(
        self, provider: ProviderId, *, limit: int = 20
    ) -> tuple[ProviderEvent, ...]:
        return await self.list_recent_events(provider, limit=limit)

    async def create_version(self, rule: KnownErrorRule) -> KnownErrorRule:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await self._lock_catalog_match(connection, rule)
                rows = await self._fetchall_on(
                    connection,
                    """
                    SELECT payload_json FROM known_error_rules
                    WHERE active = TRUE AND provider = %s AND response_code = %s
                    """,
                    (rule.provider.value, rule.response_code),
                )
                for row in rows:
                    existing = KnownErrorRule.model_validate(row[0])
                    if (
                        existing.rule_id != rule.rule_id
                        and existing.specificity == rule.specificity
                        and self._rules_overlap(existing, rule)
                    ):
                        raise CatalogAmbiguityError(
                            f"rule overlaps {existing.rule_id} at equal specificity"
                        )
                version_row = await self._fetchone_on(
                    connection,
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM known_error_rules WHERE rule_id = %s
                    """,
                    (rule.rule_id,),
                )
                assert version_row is not None
                stored = rule.model_copy(
                    update={"version": version_row[0], "created_at": utc_now()}
                )
                if stored.active:
                    await connection.execute(
                        "UPDATE known_error_rules SET active = FALSE WHERE rule_id = %s",
                        (stored.rule_id,),
                    )
                await connection.execute(
                    """
                    INSERT INTO known_error_rules(
                        rule_id, version, provider, response_code, active, created_at, payload_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        stored.rule_id,
                        stored.version,
                        stored.provider.value,
                        stored.response_code,
                        stored.active,
                        stored.created_at,
                        self._json(stored),
                    ),
                )
                await self._append_catalog_audit(
                    connection,
                    rule_id=stored.rule_id,
                    version=stored.version,
                    action=CatalogAuditAction.VERSION_CREATED,
                )
                return stored

    async def activate(self, rule_id: str, version: int) -> KnownErrorRule | None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                row = await self._fetchone_on(
                    connection,
                    """
                    SELECT payload_json FROM known_error_rules
                    WHERE rule_id = %s AND version = %s
                    """,
                    (rule_id, version),
                )
                if row is None:
                    return None
                stored = KnownErrorRule.model_validate(row[0]).model_copy(
                    update={"active": True}
                )
                await self._lock_catalog_match(connection, stored)
                other_rows = await self._fetchall_on(
                    connection,
                    """
                    SELECT payload_json FROM known_error_rules
                    WHERE active = TRUE AND provider = %s AND response_code = %s
                    AND rule_id <> %s
                    """,
                    (stored.provider.value, stored.response_code, stored.rule_id),
                )
                for other_row in other_rows:
                    other = KnownErrorRule.model_validate(other_row[0])
                    if (
                        other.specificity == stored.specificity
                        and self._rules_overlap(other, stored)
                    ):
                        raise CatalogAmbiguityError(
                            f"rule overlaps {other.rule_id} at equal specificity"
                        )
                await connection.execute(
                    "UPDATE known_error_rules SET active = FALSE WHERE rule_id = %s",
                    (rule_id,),
                )
                await connection.execute(
                    """
                    UPDATE known_error_rules SET active = TRUE, payload_json = %s
                    WHERE rule_id = %s AND version = %s
                    """,
                    (self._json(stored), rule_id, version),
                )
                await self._append_catalog_audit(
                    connection,
                    rule_id=rule_id,
                    version=version,
                    action=CatalogAuditAction.VERSION_ACTIVATED,
                )
                return stored

    async def deactivate(self, rule_id: str) -> bool:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                rows = await self._fetchall_on(
                    connection,
                    """
                    SELECT version, payload_json FROM known_error_rules
                    WHERE rule_id = %s AND active = TRUE FOR UPDATE
                    """,
                    (rule_id,),
                )
                if not rows:
                    return False
                for version, payload in rows:
                    stored = KnownErrorRule.model_validate(payload).model_copy(
                        update={"active": False}
                    )
                    await connection.execute(
                        """
                        UPDATE known_error_rules SET active = FALSE, payload_json = %s
                        WHERE rule_id = %s AND version = %s
                        """,
                        (self._json(stored), rule_id, version),
                    )
                await self._append_catalog_audit(
                    connection,
                    rule_id=rule_id,
                    version=None,
                    action=CatalogAuditAction.RULE_DEACTIVATED,
                )
                return True

    async def list_rules(
        self, *, include_inactive: bool = False
    ) -> tuple[KnownErrorRule, ...]:
        query = "SELECT active, payload_json FROM known_error_rules"
        if not include_inactive:
            query += " WHERE active = TRUE"
        query += " ORDER BY rule_id, version DESC"
        rows = await self._fetchall(query, ())
        return tuple(
            KnownErrorRule.model_validate(payload).model_copy(update={"active": active})
            for active, payload in rows
        )

    async def list_catalog_audit(
        self, *, limit: int = 100
    ) -> tuple[CatalogAuditEvent, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = await self._fetchall(
            """
            SELECT payload_json FROM catalog_audit
            ORDER BY occurred_at DESC LIMIT %s
            """,
            (limit,),
        )
        return tuple(CatalogAuditEvent.model_validate(row[0]) for row in rows)

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
        async with self._pool.connection() as connection:
            async with connection.transaction():
                inserted = await connection.execute(
                    """
                    INSERT INTO webhook_deliveries(
                        delivery_key, payload_digest, state, updated_at
                    ) VALUES (%s, %s, 'pending', %s)
                    ON CONFLICT(delivery_key) DO NOTHING
                    RETURNING delivery_key
                    """,
                    (delivery_key, payload_digest, now),
                )
                if await inserted.fetchone() is not None:
                    return True
                row = await self._fetchone_on(
                    connection,
                    """
                    SELECT payload_digest, state, updated_at FROM webhook_deliveries
                    WHERE delivery_key = %s FOR UPDATE
                    """,
                    (delivery_key,),
                )
                assert row is not None
                stored_digest, state, updated_at = row
                if stored_digest != payload_digest:
                    raise ValueError("delivery key reused with different payload")
                stale = updated_at < now - timedelta(seconds=60)
                if state == "failed" or (state == "pending" and stale):
                    await connection.execute(
                        """
                        UPDATE webhook_deliveries
                        SET state = 'pending', error = NULL, updated_at = %s
                        WHERE delivery_key = %s
                        """,
                        (now, delivery_key),
                    )
                    return True
                return False

    async def complete(
        self, delivery_key: str, incident_ids: tuple[str, ...]
    ) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE webhook_deliveries
                    SET state = 'completed', incident_ids_json = %s, updated_at = %s
                    WHERE delivery_key = %s
                    """,
                    (Jsonb(list(incident_ids)), utc_now(), delivery_key),
                )

    async def fail(self, delivery_key: str, reason: str) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE webhook_deliveries
                    SET state = 'failed', error = %s, updated_at = %s
                    WHERE delivery_key = %s
                    """,
                    (reason[:500], utc_now(), delivery_key),
                )

    async def append_feedback(self, feedback: IncidentFeedback) -> IncidentFeedback:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO incident_feedback(
                        feedback_id, incident_id, created_at, payload_json
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (
                        feedback.feedback_id,
                        feedback.incident_id,
                        feedback.created_at,
                        self._json(feedback),
                    ),
                )
        return feedback

    async def list_feedback(self, incident_id: str) -> tuple[IncidentFeedback, ...]:
        rows = await self._fetchall(
            """
            SELECT payload_json FROM incident_feedback
            WHERE incident_id = %s ORDER BY created_at
            """,
            (incident_id,),
        )
        return tuple(IncidentFeedback.model_validate(row[0]) for row in rows)

    async def _upsert_incident(
        self, connection: AsyncConnection[Any], incident: IncidentRecord
    ) -> None:
        await connection.execute(
            """
            INSERT INTO incidents(
                incident_id, fingerprint, alert_fingerprint, status, last_seen_at, payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(incident_id) DO UPDATE SET
                fingerprint = EXCLUDED.fingerprint,
                alert_fingerprint = EXCLUDED.alert_fingerprint,
                status = EXCLUDED.status,
                last_seen_at = EXCLUDED.last_seen_at,
                payload_json = EXCLUDED.payload_json
            """,
            (
                incident.incident_id,
                incident.fingerprint,
                incident.source_alert_fingerprint,
                incident.status.value,
                incident.last_seen_at,
                self._json(incident),
            ),
        )

    async def _append_audit(
        self, connection: AsyncConnection[Any], event: IncidentAuditEvent
    ) -> None:
        await connection.execute(
            """
            INSERT INTO incident_audit(event_id, incident_id, occurred_at, payload_json)
            VALUES (%s, %s, %s, %s)
            """,
            (event.event_id, event.incident_id, event.occurred_at, self._json(event)),
        )

    async def _append_catalog_audit(
        self,
        connection: AsyncConnection[Any],
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
        await connection.execute(
            """
            INSERT INTO catalog_audit(event_id, rule_id, occurred_at, payload_json)
            VALUES (%s, %s, %s, %s)
            """,
            (event.event_id, event.rule_id, event.occurred_at, self._json(event)),
        )

    async def _lock_catalog_match(
        self, connection: AsyncConnection[Any], rule: KnownErrorRule
    ) -> None:
        key = f"catalog:{rule.provider.value}:{rule.response_code}"
        await connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))

    @staticmethod
    def _rules_overlap(left: KnownErrorRule, right: KnownErrorRule) -> bool:
        if left.provider is not right.provider or left.response_code != right.response_code:
            return False
        return all(
            a is None or b is None or a == b
            for a, b in (
                (left.outcome, right.outcome),
                (left.payment_method, right.payment_method),
                (left.region, right.region),
            )
        )

    @staticmethod
    def _json(model: Any) -> Jsonb:
        return Jsonb(model.model_dump(mode="json"))

    async def _fetchone(
        self, query: str, parameters: tuple[Any, ...]
    ) -> tuple[Any, ...] | None:
        async with self._pool.connection() as connection:
            return await self._fetchone_on(connection, query, parameters)

    async def _fetchall(
        self, query: str, parameters: tuple[Any, ...]
    ) -> list[tuple[Any, ...]]:
        async with self._pool.connection() as connection:
            return await self._fetchall_on(connection, query, parameters)

    @staticmethod
    async def _fetchone_on(
        connection: AsyncConnection[Any], query: str, parameters: tuple[Any, ...]
    ) -> tuple[Any, ...] | None:
        result = await connection.execute(query, parameters)
        return await result.fetchone()

    @staticmethod
    async def _fetchall_on(
        connection: AsyncConnection[Any], query: str, parameters: tuple[Any, ...]
    ) -> list[tuple[Any, ...]]:
        result = await connection.execute(query, parameters)
        return await result.fetchall()
