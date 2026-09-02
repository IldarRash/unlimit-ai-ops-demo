from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MetricsMode(StrEnum):
    DEMO = "demo"
    PROMETHEUS = "prometheus"


class IncidentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APM_INCIDENT_",
        extra="ignore",
    )

    service_name: str = "incident-intelligence"
    metrics_mode: MetricsMode = MetricsMode.DEMO
    prometheus_url: str = "http://localhost:9090"
    traffic_generator_url: str = "http://traffic-generator:8001"
    provider_emulator_url: str = "http://provider-emulator:8000"
    grafana_public_url: str = "http://localhost:3000"
    analysis_window_seconds: int = Field(default=300, ge=15, le=3_600)
    request_timeout_seconds: float = Field(default=5, gt=0, le=60)
    database_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "APM_INCIDENT_DATABASE_URL"),
    )
    database_path: str = "data/incidents.db"
    alertmanager_token: SecretStr = SecretStr("dev-alertmanager-token-change-me")
    alertmanager_token_file: str | None = None
    provider_event_token: SecretStr = SecretStr("dev-provider-event-token-change-me")
    provider_event_token_file: str | None = None
    catalog_admin_token: SecretStr = SecretStr("dev-catalog-admin-token-change-me")
    catalog_admin_token_file: str | None = None
    enforce_ingress_networks: bool = True
    trusted_ingress_networks: str = "127.0.0.1/32,::1/128"
    provider_event_limit: int = Field(default=20, ge=1, le=100)
    minimum_requests: int = Field(default=20, ge=1)
    warning_p95_latency_ms: float = Field(default=800, gt=0)
    critical_p95_latency_ms: float = Field(default=1_500, gt=0)
    warning_error_rate: float = Field(default=0.05, gt=0, le=1)
    critical_error_rate: float = Field(default=0.15, gt=0, le=1)
    warning_timeout_rate: float = Field(default=0.03, gt=0, le=1)
    critical_timeout_rate: float = Field(default=0.10, gt=0, le=1)
    warning_decline_rate: float = Field(default=0.10, gt=0, le=1)
    critical_decline_rate: float = Field(default=0.25, gt=0, le=1)
    llm_failure_threshold: int = Field(default=3, ge=1, le=10)
    llm_circuit_reset_seconds: float = Field(default=30, ge=1, le=600)
    openai_model: str = "gpt-5.4-mini"
    openai_requests_enabled: bool = False
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "APM_INCIDENT_OPENAI_API_KEY"),
    )

    @model_validator(mode="after")
    def validate_configuration(self) -> "IncidentSettings":
        value = self.openai_api_key.get_secret_value().strip() if self.openai_api_key else ""
        if len(value) < 20 or value == "replace_with_your_openai_api_key":
            raise ValueError("OPENAI_API_KEY must be configured")
        for name, secret in (
            ("alertmanager_token", self.alertmanager_token),
            ("provider_event_token", self.provider_event_token),
            ("catalog_admin_token", self.catalog_admin_token),
        ):
            if len(secret.get_secret_value().strip()) < 20:
                raise ValueError(f"{name} must contain at least 20 characters")
        for warning, critical, label in (
            (self.warning_p95_latency_ms, self.critical_p95_latency_ms, "latency"),
            (self.warning_error_rate, self.critical_error_rate, "error rate"),
            (self.warning_timeout_rate, self.critical_timeout_rate, "timeout rate"),
            (self.warning_decline_rate, self.critical_decline_rate, "decline rate"),
        ):
            if warning >= critical:
                raise ValueError(f"warning {label} must be below critical {label}")
        return self

    def openai_api_key_value(self) -> str:
        assert self.openai_api_key is not None
        return self.openai_api_key.get_secret_value().strip()

    def alertmanager_token_value(self) -> str:
        return self._secret_value(self.alertmanager_token, self.alertmanager_token_file)

    def provider_event_token_value(self) -> str:
        return self._secret_value(self.provider_event_token, self.provider_event_token_file)

    def catalog_admin_token_value(self) -> str:
        return self._secret_value(self.catalog_admin_token, self.catalog_admin_token_file)

    def database_url_value(self) -> str | None:
        """Return a configured PostgreSQL URL without exposing it in settings dumps."""
        if self.database_url is None:
            return None
        value = self.database_url.get_secret_value().strip()
        return value or None

    @staticmethod
    def _secret_value(secret: SecretStr, secret_file: str | None) -> str:
        value = (
            Path(secret_file).read_text(encoding="utf-8").strip()
            if secret_file
            else secret.get_secret_value().strip()
        )
        if len(value) < 20:
            raise ValueError("integration secrets must contain at least 20 characters")
        return value
