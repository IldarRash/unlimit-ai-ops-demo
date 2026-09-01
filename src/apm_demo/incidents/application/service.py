from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.application.detection import (
    AnomalyDetector,
    incident_fingerprint,
    incident_severity,
)
from apm_demo.incidents.domain import (
    AuditEventType,
    EvidenceBundle,
    IncidentAuditEvent,
    IncidentRecord,
    IncidentSeverity,
    IncidentStatus,
    utc_now,
)
from apm_demo.incidents.ports import (
    AuditLog,
    IncidentAnalyzer,
    IncidentRepository,
    MetricsSource,
)


class AnalyzeProviderIncident:
    def __init__(
        self,
        *,
        metrics: MetricsSource,
        detector: AnomalyDetector,
        analyzer: IncidentAnalyzer,
        incidents: IncidentRepository,
        audit_log: AuditLog,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._metrics = metrics
        self._detector = detector
        self._analyzer = analyzer
        self._incidents = incidents
        self._audit_log = audit_log
        self._now = now

    async def execute(
        self, provider: ProviderId, *, window_seconds: int = 300
    ) -> IncidentRecord | None:
        snapshot = await self._metrics.collect(
            provider, window_seconds=window_seconds
        )
        signals = self._detector.detect(snapshot)
        if not signals:
            return None

        evidence = EvidenceBundle(
            snapshot=snapshot,
            signals=signals,
            source=type(self._metrics).__name__,
            collected_at=self._now(),
        )
        fingerprint = incident_fingerprint(snapshot, signals)
        severity = incident_severity(signals)
        existing = await self._incidents.find_active_by_fingerprint(fingerprint)
        if existing:
            return await self._correlate(existing, evidence, severity)

        analysis = await self._analyzer.analyze(evidence)
        observed_at = self._now()
        incident = IncidentRecord(
            incident_id=f"inc_{uuid4().hex}",
            fingerprint=fingerprint,
            provider=provider,
            severity=severity,
            evidence=evidence,
            analysis=analysis,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )
        stored = await self._incidents.save(incident)
        await self._audit(
            stored.incident_id,
            AuditEventType.CREATED,
            {"severity": severity.value, "provider": provider.value},
        )
        return stored

    async def _correlate(
        self,
        existing: IncidentRecord,
        evidence: EvidenceBundle,
        severity: IncidentSeverity,
    ) -> IncidentRecord:
        escalated = self._severity_rank(severity) > self._severity_rank(existing.severity)
        analysis = (
            await self._analyzer.analyze(evidence) if escalated else existing.analysis
        )
        updated = existing.model_copy(
            update={
                "severity": severity,
                "evidence": evidence,
                "analysis": analysis,
                "occurrences": existing.occurrences + 1,
                "last_seen_at": self._now(),
            }
        )
        stored = await self._incidents.save(updated)
        await self._audit(
            stored.incident_id,
            AuditEventType.ESCALATED if escalated else AuditEventType.CORRELATED,
            {"severity": severity.value, "occurrences": str(stored.occurrences)},
        )
        return stored

    async def _audit(
        self, incident_id: str, event_type: AuditEventType, details: dict[str, str]
    ) -> None:
        await self._audit_log.append(
            IncidentAuditEvent(
                event_id=f"evt_{uuid4().hex}",
                incident_id=incident_id,
                event_type=event_type,
                occurred_at=self._now(),
                details=details,
            )
        )

    @staticmethod
    def _severity_rank(severity: IncidentSeverity) -> int:
        return {
            IncidentSeverity.INFO: 0,
            IncidentSeverity.WARNING: 1,
            IncidentSeverity.CRITICAL: 2,
        }[severity]


class IncidentLifecycle:
    def __init__(
        self,
        *,
        incidents: IncidentRepository,
        audit_log: AuditLog,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._incidents = incidents
        self._audit_log = audit_log
        self._now = now

    async def set_status(
        self, incident_id: str, status: IncidentStatus
    ) -> IncidentRecord | None:
        incident = await self._incidents.get(incident_id)
        if incident is None:
            return None
        if incident.status is status:
            return incident
        updated = incident.model_copy(
            update={"status": status, "last_seen_at": self._now()}
        )
        stored = await self._incidents.save(updated)
        await self._audit_log.append(
            IncidentAuditEvent(
                event_id=f"evt_{uuid4().hex}",
                incident_id=incident_id,
                event_type=AuditEventType.STATUS_CHANGED,
                occurred_at=self._now(),
                details={"from": incident.status.value, "to": status.value},
            )
        )
        return stored
