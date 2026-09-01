from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apm_demo.incidents.domain import (
    AlertSignal,
    IncidentSeverity,
    MetricSnapshot,
    SignalType,
)


class DetectionThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_requests: int = Field(default=20, ge=1)
    warning_p95_latency_ms: float = Field(default=800, gt=0)
    critical_p95_latency_ms: float = Field(default=1_500, gt=0)
    warning_error_rate: float = Field(default=0.05, gt=0, le=1)
    critical_error_rate: float = Field(default=0.15, gt=0, le=1)
    warning_timeout_rate: float = Field(default=0.03, gt=0, le=1)
    critical_timeout_rate: float = Field(default=0.10, gt=0, le=1)

    @model_validator(mode="after")
    def validate_warning_before_critical(self) -> "DetectionThresholds":
        pairs = (
            (self.warning_p95_latency_ms, self.critical_p95_latency_ms, "latency"),
            (self.warning_error_rate, self.critical_error_rate, "error rate"),
            (self.warning_timeout_rate, self.critical_timeout_rate, "timeout rate"),
        )
        for warning, critical, label in pairs:
            if warning >= critical:
                raise ValueError(f"warning {label} must be below critical {label}")
        return self


class AnomalyDetector:
    def __init__(self, thresholds: DetectionThresholds | None = None) -> None:
        self.thresholds = thresholds or DetectionThresholds()

    def detect(self, snapshot: MetricSnapshot) -> tuple[AlertSignal, ...]:
        signals: list[AlertSignal] = []
        if not snapshot.health_up:
            signals.append(
                AlertSignal(
                    signal_type=SignalType.HEALTH,
                    severity=IncidentSeverity.CRITICAL,
                    actual_value=0,
                    threshold_value=1,
                    unit="boolean",
                    description=f"{snapshot.provider.value} health check is down",
                )
            )

        if snapshot.total_requests < self.thresholds.minimum_requests:
            return tuple(signals)

        latency_severity, latency_threshold = self._severity(
            snapshot.p95_latency_ms,
            self.thresholds.warning_p95_latency_ms,
            self.thresholds.critical_p95_latency_ms,
        )
        if latency_severity:
            signals.append(
                AlertSignal(
                    signal_type=SignalType.LATENCY,
                    severity=latency_severity,
                    actual_value=snapshot.p95_latency_ms,
                    threshold_value=latency_threshold,
                    unit="ms",
                    description=(
                        f"{snapshot.provider.value} p95 latency is "
                        f"{snapshot.p95_latency_ms:.0f} ms"
                    ),
                )
            )

        for signal_type, value, warning, critical, label in (
            (
                SignalType.ERROR_RATE,
                snapshot.error_rate,
                self.thresholds.warning_error_rate,
                self.thresholds.critical_error_rate,
                "error rate",
            ),
            (
                SignalType.TIMEOUT_RATE,
                snapshot.timeout_rate,
                self.thresholds.warning_timeout_rate,
                self.thresholds.critical_timeout_rate,
                "timeout rate",
            ),
        ):
            severity, threshold = self._severity(value, warning, critical)
            if severity:
                signals.append(
                    AlertSignal(
                        signal_type=signal_type,
                        severity=severity,
                        actual_value=value,
                        threshold_value=threshold,
                        unit="ratio",
                        description=(
                            f"{snapshot.provider.value} {label} is {value:.1%}"
                        ),
                    )
                )
        return tuple(signals)

    @staticmethod
    def _severity(
        value: float, warning: float, critical: float
    ) -> tuple[IncidentSeverity | None, float]:
        if value >= critical:
            return IncidentSeverity.CRITICAL, critical
        if value >= warning:
            return IncidentSeverity.WARNING, warning
        return None, warning


def incident_severity(signals: tuple[AlertSignal, ...]) -> IncidentSeverity:
    if any(signal.severity is IncidentSeverity.CRITICAL for signal in signals):
        return IncidentSeverity.CRITICAL
    if any(signal.severity is IncidentSeverity.WARNING for signal in signals):
        return IncidentSeverity.WARNING
    return IncidentSeverity.INFO


def incident_fingerprint(snapshot: MetricSnapshot, signals: tuple[AlertSignal, ...]) -> str:
    """Correlate repeated windows without encoding volatile metric values."""

    signal_key = ",".join(sorted(signal.signal_type.value for signal in signals))
    raw = f"v1:{snapshot.provider.value}:{signal_key}"
    return sha256(raw.encode("utf-8")).hexdigest()
