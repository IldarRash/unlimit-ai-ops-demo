from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeneratorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APM_",
        env_file=".env",
        extra="ignore",
    )

    provider_base_url: str = "http://provider-emulator:8000"
    requests_per_second: float = Field(default=4.0, gt=0, le=100.0)
    request_timeout_seconds: float = Field(default=2.0, gt=0, le=30.0)
    healthcheck_interval_seconds: float = Field(default=5.0, gt=0, le=300.0)
    healthcheck_timeout_seconds: float = Field(default=1.0, gt=0, le=30.0)
    max_in_flight: int = Field(default=32, ge=1, le=1_000)
    generator_enabled: bool = True
    random_seed: int | None = None
    incident_api_url: str | None = None
    provider_event_token: SecretStr | None = None
    provider_event_token_file: str | None = None

    def provider_event_token_value(self) -> str | None:
        if self.provider_event_token_file:
            return Path(self.provider_event_token_file).read_text(encoding="utf-8").strip()
        return self.provider_event_token.get_secret_value().strip() if self.provider_event_token else None
