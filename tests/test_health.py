"""Tests for health check system."""

from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock

from app.health import (
    HealthChecker,
    HealthStatus,
    ComponentHealth,
    SystemHealth,
    check_database,
    check_redis,
    check_llm,
    get_health_checker,
)


class TestHealthChecker:
    """Tests for HealthChecker class."""

    def test_register_check(self):
        """Should register health checks."""
        checker = HealthChecker()

        async def my_check() -> ComponentHealth:
            return ComponentHealth(name="test", status=HealthStatus.HEALTHY)

        checker.register("test", my_check)
        assert "test" in checker._checks

    @pytest.mark.asyncio
    async def test_check_component_success(self):
        """Should return healthy for successful check."""
        checker = HealthChecker()

        async def healthy_check() -> ComponentHealth:
            return ComponentHealth(name="test", status=HealthStatus.HEALTHY, message="OK")

        checker.register("test", healthy_check)
        result = await checker.check_component("test")

        assert result.status == HealthStatus.HEALTHY
        assert result.message == "OK"

    @pytest.mark.asyncio
    async def test_check_component_failure(self):
        """Should return unhealthy for failed check."""
        checker = HealthChecker()

        async def failing_check() -> ComponentHealth:
            raise Exception("Connection failed")

        checker.register("failing", failing_check)
        result = await checker.check_component("failing")

        assert result.status == HealthStatus.UNHEALTHY
        assert "Connection failed" in result.message

    @pytest.mark.asyncio
    async def test_check_component_timeout(self):
        """Should return unhealthy on timeout."""
        import asyncio

        checker = HealthChecker()

        async def slow_check() -> ComponentHealth:
            await asyncio.sleep(10)
            return ComponentHealth(name="slow", status=HealthStatus.HEALTHY)

        checker.register("slow", slow_check)
        result = await checker.check_component("slow")

        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.message.lower()

    @pytest.mark.asyncio
    async def test_check_all_aggregation(self):
        """Should aggregate component statuses correctly."""
        checker = HealthChecker()

        async def healthy() -> ComponentHealth:
            return ComponentHealth(name="healthy", status=HealthStatus.HEALTHY)

        async def degraded() -> ComponentHealth:
            return ComponentHealth(name="degraded", status=HealthStatus.DEGRADED)

        checker.register("healthy", healthy)
        checker.register("degraded", degraded)

        result = await checker.check_all()

        assert result.status == HealthStatus.DEGRADED
        assert len(result.components) == 2

    @pytest.mark.asyncio
    async def test_liveness_always_true(self):
        """Liveness should always return true unless process is dead."""
        checker = HealthChecker()
        alive, details = await checker.liveness()

        assert alive is True
        assert details["status"] == "alive"

    @pytest.mark.asyncio
    async def test_readiness_depends_on_components(self):
        """Readiness should depend on component health."""
        checker = HealthChecker()

        async def unhealthy() -> ComponentHealth:
            return ComponentHealth(name="db", status=HealthStatus.UNHEALTHY)

        checker.register("db", unhealthy)

        ready, _ = await checker.readiness()
        assert ready is False

    @pytest.mark.asyncio
    async def test_startup_probe(self):
        """Startup probe should reflect startup state."""
        checker = HealthChecker()

        started, _ = await checker.startup()
        assert started is False

        checker.mark_startup_complete()

        started, _ = await checker.startup()
        assert started is True

    def test_system_health_to_dict(self):
        """SystemHealth should serialize to dict."""
        health = SystemHealth(
            status=HealthStatus.HEALTHY,
            version="1.0.0",
            uptime_seconds=123.45,
            components={
                "db": ComponentHealth(name="db", status=HealthStatus.HEALTHY, latency_ms=5.0),
            },
        )

        data = health.to_dict()

        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert "db" in data["components"]
        assert data["components"]["db"]["status"] == "healthy"


class TestComponentHealthChecks:
    """Tests for individual component health checks."""

    @pytest.mark.asyncio
    async def test_check_database_healthy(self):
        """Database check should return healthy when connected."""
        from datetime import datetime
        from app.database.engine import DatabaseHealth

        mock_health = DatabaseHealth(
            healthy=True,
            latency_ms=5.0,
            connection_pool_size=10,
            connection_pool_checked_out=2,
            last_check=datetime.now(),
        )

        with patch("app.database.check_db_health", new_callable=AsyncMock) as mock:
            mock.return_value = mock_health
            result = await check_database()

            assert result.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_redis_not_configured(self):
        """Redis check should return healthy when not configured."""
        with patch("app.config.get_settings") as mock:
            mock.return_value.copilot_redis_url = ""
            result = await check_redis()

            assert result.status == HealthStatus.HEALTHY
            assert "not configured" in result.message.lower()

    @pytest.mark.asyncio
    async def test_check_llm_multi_tenant(self):
        """LLM check should pass in multi-tenant mode."""
        with patch("app.config.get_settings") as mock:
            mock.return_value.copilot_multi_tenant = True
            result = await check_llm()

            assert result.status == HealthStatus.HEALTHY
            assert "multi-tenant" in result.message.lower()

    @pytest.mark.asyncio
    async def test_check_llm_no_key(self):
        """LLM check should fail without API key."""
        with patch("app.config.get_settings") as mock_settings:
            mock_settings.return_value.copilot_multi_tenant = False
            with patch("app.llm.active_api_key") as mock_key:
                mock_key.return_value = ""
                result = await check_llm()

                assert result.status == HealthStatus.UNHEALTHY


class TestHealthCheckerSingleton:
    """Tests for singleton health checker."""

    def test_get_health_checker_singleton(self):
        """Should return same instance."""
        checker1 = get_health_checker()
        checker2 = get_health_checker()

        assert checker1 is checker2
