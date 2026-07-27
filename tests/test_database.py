"""Tests for database layer."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")


class TestDatabaseEngine:
    """Tests for database engine configuration."""

    def test_get_database_url_default(self):
        """Default URL should be SQLite."""
        from app.database.engine import _get_database_url

        with patch("app.database.engine.get_settings") as mock:
            mock.return_value.copilot_database_url = ""
            url = _get_database_url()
            assert "sqlite" in url
            assert "aiosqlite" in url

    def test_get_database_url_postgres(self):
        """PostgreSQL URL should be converted to asyncpg."""
        from app.database.engine import _get_database_url

        with patch("app.database.engine.get_settings") as mock:
            mock.return_value.copilot_database_url = "postgresql://user:pass@host:5432/db"
            url = _get_database_url()
            assert "postgresql+asyncpg://" in url

    def test_get_database_url_postgres_short(self):
        """Short postgres:// URL should be converted."""
        from app.database.engine import _get_database_url

        with patch("app.database.engine.get_settings") as mock:
            mock.return_value.copilot_database_url = "postgres://user:pass@host/db"
            url = _get_database_url()
            assert "postgresql+asyncpg://" in url


class TestDatabaseHealth:
    """Tests for database health checks."""

    @pytest.mark.asyncio
    async def test_check_db_health_success(self):
        """Health check should return healthy on success."""
        from app.database.engine import DatabaseHealth

        health = DatabaseHealth(
            healthy=True,
            latency_ms=5.0,
            connection_pool_size=10,
            connection_pool_checked_out=2,
            last_check=MagicMock(),
        )

        assert health.healthy is True
        assert health.latency_ms == 5.0
        assert health.error is None

    @pytest.mark.asyncio
    async def test_check_db_health_failure(self):
        """Health check should return unhealthy on failure."""
        from app.database.engine import DatabaseHealth

        health = DatabaseHealth(
            healthy=False,
            latency_ms=100.0,
            connection_pool_size=0,
            connection_pool_checked_out=0,
            last_check=MagicMock(),
            error="Connection refused",
        )

        assert health.healthy is False
        assert health.error == "Connection refused"


class TestDatabaseModels:
    """Tests for SQLAlchemy models."""

    def test_organization_model(self):
        """Organization model should have required fields."""
        from app.database.models import Organization

        assert hasattr(Organization, "id")
        assert hasattr(Organization, "slug")
        assert hasattr(Organization, "name")
        assert hasattr(Organization, "plan")

    def test_api_key_model(self):
        """APIKey model should have required fields."""
        from app.database.models import APIKey

        assert hasattr(APIKey, "id")
        assert hasattr(APIKey, "org_id")
        assert hasattr(APIKey, "key_hash")
        assert hasattr(APIKey, "role")

    def test_investigation_model(self):
        """Investigation model should have required fields."""
        from app.database.models import Investigation

        assert hasattr(Investigation, "id")
        assert hasattr(Investigation, "org_id")
        assert hasattr(Investigation, "thread_id")
        assert hasattr(Investigation, "status")
        assert hasattr(Investigation, "report")

    def test_audit_log_model(self):
        """AuditLog model should have hash chain fields."""
        from app.database.models import AuditLog

        assert hasattr(AuditLog, "prev_hash")
        assert hasattr(AuditLog, "entry_hash")
        assert hasattr(AuditLog, "event_type")
