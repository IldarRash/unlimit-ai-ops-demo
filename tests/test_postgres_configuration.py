from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from apm_demo.incidents.api.config import IncidentSettings


ROOT = Path(__file__).resolve().parents[1]


def test_database_url_is_optional_secret_setting() -> None:
    settings = IncidentSettings(database_url="postgresql://user:password@db/app")

    assert settings.database_url_value() == "postgresql://user:password@db/app"
    assert "password" not in str(settings.database_url)


def test_blank_database_url_keeps_sqlite_fallback() -> None:
    assert IncidentSettings(database_url="  ").database_url_value() is None


def test_database_url_accepts_railway_standard_environment_name(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@db/app")

    assert IncidentSettings().database_url_value() == "postgresql://user:password@db/app"


def test_container_selects_postgres_only_when_database_url_is_configured() -> None:
    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    from apm_demo.incidents.api.container import build_container
    from apm_demo.incidents.infrastructure import PostgresIncidentStore, SQLiteIncidentStore

    sqlite = build_container(IncidentSettings(database_path="test-incidents.db"))
    postgres = build_container(
        IncidentSettings(database_url="postgresql://user:password@db/app")
    )

    assert isinstance(sqlite.incidents, SQLiteIncidentStore)
    assert isinstance(postgres.incidents, PostgresIncidentStore)


def test_compose_runs_incident_api_against_postgres_secret() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["postgres"]["image"] == "postgres:17-alpine"
    incident = compose["services"]["incident-api"]
    assert incident["environment"]["DATABASE_HOST"] == "postgres"
    assert incident["environment"]["DATABASE_PASSWORD_FILE"] == (
        "/run/secrets/postgres_password"
    )
    assert "postgres_password" in incident["secrets"]
    assert incident["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "/ready" in incident["healthcheck"]["test"][-1]
    assert "incident-data" not in compose["volumes"]
