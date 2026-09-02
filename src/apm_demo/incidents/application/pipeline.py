from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.application.classification import IncidentClassifier
from apm_demo.incidents.application.detection import AnomalyDetector, incident_severity
from apm_demo.incidents.application.events import IncidentEventBus
from apm_demo.incidents.domain import (
    AlertDeliveryStatus,
    AlertEvidence,
    AlertIngestResult,
    AlertSignal,
    AlertmanagerAlert,
    AlertmanagerWebhook,
    AuditEventType,
    EvidenceBundle,
    IncidentAuditEvent,
    IncidentAnalysis,
    IncidentRecord,
    IncidentSeverity,
    IncidentStatus,
    MetricSnapshot,
    SignalType,
    utc_now,
)
from apm_demo.incidents.ports import (
    DeliveryLedger,
    ExternalSignalRepository,
    IncidentRepository,
    MetricsSource,
    MetricsUnavailable,
    ProviderEventRepository,
)


class InvalidAlert(ValueError):
    pass


class AlertIncidentPipeline:
    def __init__(
        self,
        *,
        metrics: MetricsSource,
        detector: AnomalyDetector,
        classifier: IncidentClassifier,
        incidents: IncidentRepository,
        provider_events: ProviderEventRepository,
        external_signals: ExternalSignalRepository,
        deliveries: DeliveryLedger,
        events: IncidentEventBus,
        event_limit: int = 20,
        external_signal_limit: int = 12,
        window_seconds: int = 300,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._metrics = metrics
        self._detector = detector
        self._classifier = classifier
        self._incidents = incidents
        self._provider_events = provider_events
        self._external_signals = external_signals
        self._deliveries = deliveries
        self._events = events
        self._event_limit = event_limit
        self._external_signal_limit = external_signal_limit
        self._window_seconds = window_seconds
        self._now = now

    async def ingest(self, webhook: AlertmanagerWebhook) -> AlertIngestResult:
        providers = tuple(self._provider_for(alert, webhook) for alert in webhook.alerts)
        canonical = json.dumps(
            webhook.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        delivery_key = payload_digest
        if not await self._deliveries.claim(delivery_key, payload_digest):
            return AlertIngestResult(
                delivery_key=delivery_key,
                accepted=0,
                replayed=True,
                truncated=webhook.truncated_alerts > 0,
            )

        incident_ids: list[str] = []
        try:
            for alert, provider in zip(webhook.alerts, providers, strict=True):
                incident = await self._process_alert(webhook, alert, provider)
                if incident is not None:
                    incident_ids.append(incident.incident_id)
            await self._deliveries.complete(delivery_key, tuple(incident_ids))
        except Exception as error:
            await self._deliveries.fail(delivery_key, type(error).__name__)
            raise
        return AlertIngestResult(
            delivery_key=delivery_key,
            accepted=len(webhook.alerts),
            incident_ids=tuple(incident_ids),
            truncated=webhook.truncated_alerts > 0,
        )

    @staticmethod
    def _provider_for(
        alert: AlertmanagerAlert, webhook: AlertmanagerWebhook
    ) -> ProviderId:
        raw_provider = (
            alert.labels.get("provider")
            or webhook.common_labels.get("provider")
            or webhook.group_labels.get("provider")
        )
        try:
            return ProviderId(raw_provider)
        except (TypeError, ValueError) as error:
            raise InvalidAlert(
                "every alert must contain a supported provider label"
            ) from error

    async def _process_alert(
        self,
        webhook: AlertmanagerWebhook,
        alert: AlertmanagerAlert,
        provider: ProviderId,
    ) -> IncidentRecord | None:
        existing = await self._incidents.find_latest_by_alert_fingerprint(
            alert.fingerprint
        )
        if alert.status is AlertDeliveryStatus.RESOLVED:
            return await self._resolve(existing)

        try:
            snapshot = await self._metrics.collect(
                provider, window_seconds=self._window_seconds
            )
            evidence_source = type(self._metrics).__name__
        except MetricsUnavailable:
            snapshot = MetricSnapshot(
                provider=provider,
                available=False,
                collection_error="metrics-source-unavailable",
                window_seconds=self._window_seconds,
                total_requests=0,
                request_rate_per_second=0,
                success_rate=0,
                error_rate=0,
                timeout_rate=0,
                p95_latency_ms=0,
                health_up=True,
            )
            evidence_source = "metrics-unavailable"
        signals = self._detector.detect(snapshot) or (self._signal_from_alert(alert),)
        provider_events = await self._provider_events.list_recent_events(
            provider, limit=self._event_limit
        )
        external_signals = await self._external_signals.list_recent_external_signals(
            provider, limit=self._external_signal_limit
        )
        evidence = EvidenceBundle(
            snapshot=snapshot,
            signals=signals,
            alert=AlertEvidence(
                group_key=webhook.group_key,
                fingerprint=alert.fingerprint,
                alert_name=alert.labels.get("alertname", "ProviderDegradation"),
                status=alert.status,
                severity=alert.labels.get("severity"),
                truncated=webhook.truncated_alerts > 0,
            ),
            provider_events=provider_events,
            external_signals=external_signals,
            source=evidence_source,
            collected_at=self._now(),
        )
        severity = self._severity(alert, signals)
        if existing is None:
            classification = await self._classifier.classify(evidence)
            return await self._create(
                webhook, alert, provider, evidence, classification.analysis, severity
            )
        if (
            existing.status is not IncidentStatus.RESOLVED
            and self._severity_rank(severity) <= self._severity_rank(existing.severity)
        ):
            return await self._update_existing(
                existing, evidence, existing.analysis, severity
            )
        classification = await self._classifier.classify(evidence)
        return await self._update_existing(
            existing, evidence, classification.analysis, severity
        )

    async def _create(
        self,
        webhook: AlertmanagerWebhook,
        alert: AlertmanagerAlert,
        provider: ProviderId,
        evidence: EvidenceBundle,
        analysis: IncidentAnalysis,
        severity: IncidentSeverity,
    ) -> IncidentRecord:
        observed_at = self._now()
        fingerprint = hashlib.sha256(
            f"alertmanager:{alert.fingerprint}".encode("utf-8")
        ).hexdigest()
        incident = IncidentRecord(
            incident_id=f"inc_{uuid4().hex}",
            fingerprint=fingerprint,
            provider=provider,
            severity=severity,
            evidence=evidence,
            analysis=analysis,
            source_alert_fingerprint=alert.fingerprint,
            alert_group_key=webhook.group_key,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )
        stored = await self._store_with_audit(
            incident,
            AuditEventType.CREATED,
            {
                "classification": analysis.classification.value,
                "provider": provider.value,
                "severity": severity.value,
            },
        )
        await self._events.publish(stored)
        return stored

    async def _update_existing(
        self,
        existing: IncidentRecord,
        evidence: EvidenceBundle,
        analysis: IncidentAnalysis,
        severity: IncidentSeverity,
    ) -> IncidentRecord:
        was_resolved = existing.status is IncidentStatus.RESOLVED
        escalated = self._severity_rank(severity) > self._severity_rank(existing.severity)
        updated = existing.model_copy(
            update={
                "status": IncidentStatus.OPEN,
                "severity": severity,
                "evidence": evidence,
                "analysis": analysis if was_resolved or escalated else existing.analysis,
                "occurrences": existing.occurrences + 1,
                "last_seen_at": self._now(),
                "resolved_at": None,
            }
        )
        event_type = (
            AuditEventType.REOPENED
            if was_resolved
            else AuditEventType.ESCALATED
            if escalated
            else AuditEventType.CORRELATED
        )
        stored = await self._store_with_audit(
            updated,
            event_type,
            {
                "classification": updated.analysis.classification.value,
                "occurrences": str(updated.occurrences),
                "severity": severity.value,
            },
        )
        await self._events.publish(stored)
        return stored

    async def _resolve(self, existing: IncidentRecord | None) -> IncidentRecord | None:
        if existing is None or existing.status is IncidentStatus.RESOLVED:
            return existing
        now = self._now()
        resolved = existing.model_copy(
            update={
                "status": IncidentStatus.RESOLVED,
                "last_seen_at": now,
                "resolved_at": now,
            }
        )
        stored = await self._store_with_audit(
            resolved,
            AuditEventType.RESOLVED,
            {"from": existing.status.value, "to": IncidentStatus.RESOLVED.value},
        )
        await self._events.publish(stored)
        return stored

    async def _store_with_audit(
        self,
        incident: IncidentRecord,
        event_type: AuditEventType,
        details: dict[str, str],
    ) -> IncidentRecord:
        event = IncidentAuditEvent(
            event_id=f"evt_{uuid4().hex}",
            incident_id=incident.incident_id,
            event_type=event_type,
            occurred_at=self._now(),
            details=details,
        )
        return await self._incidents.save_with_audit(incident, event)

    @staticmethod
    def _severity(
        alert: AlertmanagerAlert, signals: tuple[AlertSignal, ...]
    ) -> IncidentSeverity:
        raw = alert.labels.get("severity", "").lower()
        aliases = {
            "info": IncidentSeverity.INFO,
            "warning": IncidentSeverity.WARNING,
            "warn": IncidentSeverity.WARNING,
            "critical": IncidentSeverity.CRITICAL,
            "page": IncidentSeverity.CRITICAL,
        }
        return aliases.get(raw, incident_severity(signals))

    @staticmethod
    def _signal_from_alert(alert: AlertmanagerAlert) -> AlertSignal:
        name = alert.labels.get("alertname", "provider-error").lower()
        if "latency" in name or "p95" in name:
            signal_type, unit = SignalType.LATENCY, "ms"
        elif "timeout" in name:
            signal_type, unit = SignalType.TIMEOUT_RATE, "ratio"
        elif "health" in name or "down" in name:
            signal_type, unit = SignalType.HEALTH, "boolean"
        else:
            signal_type, unit = SignalType.ERROR_RATE, "ratio"
        severity = (
            IncidentSeverity.CRITICAL
            if alert.labels.get("severity", "").lower() in {"critical", "page"}
            else IncidentSeverity.WARNING
        )
        return AlertSignal(
            signal_type=signal_type,
            severity=severity,
            actual_value=0 if signal_type is SignalType.HEALTH else 1,
            threshold_value=1 if signal_type is SignalType.HEALTH else 0,
            unit=unit,
            description=alert.annotations.get(
                "summary", alert.labels.get("alertname", "Alertmanager signal")
            )[:240],
        )

    @staticmethod
    def _severity_rank(severity: IncidentSeverity) -> int:
        return {
            IncidentSeverity.INFO: 0,
            IncidentSeverity.WARNING: 1,
            IncidentSeverity.CRITICAL: 2,
        }[severity]
