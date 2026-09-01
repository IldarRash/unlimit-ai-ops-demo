from __future__ import annotations

import pytest

from apm_demo.incidents.api.config import IncidentSettings


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
