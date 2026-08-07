"""Tests for MCP health monitoring module."""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.health import (
    MCPServerStatus,
    MCPServerHealth,
    MCPHealthSummary,
    MCPHealthMonitor,
    get_health_monitor,
)


class TestMCPServerStatus:
    """Tests for MCPServerStatus enum."""

    def test_status_values(self):
        """Test all status enum values exist."""
        assert MCPServerStatus.HEALTHY.value == "healthy"
        assert MCPServerStatus.DEGRADED.value == "degraded"
        assert MCPServerStatus.UNHEALTHY.value == "unhealthy"
        assert MCPServerStatus.UNKNOWN.value == "unknown"
        assert MCPServerStatus.STOPPED.value == "stopped"


class TestMCPServerHealth:
    """Tests for MCPServerHealth dataclass."""

    def test_health_creation(self):
        """Test creating a health object."""
        health = MCPServerHealth(
            name="datadog",
            status=MCPServerStatus.HEALTHY,
            pid=12345,
            uptime_seconds=3600.0,
        )
        assert health.name == "datadog"
        assert health.status == MCPServerStatus.HEALTHY
        assert health.pid == 12345
        assert health.uptime_seconds == 3600.0

    def test_health_to_dict(self):
        """Test converting health to dict."""
        health = MCPServerHealth(
            name="github",
            status=MCPServerStatus.HEALTHY,
            tool_calls_total=100,
            tool_errors_total=5,
        )
        data = health.to_dict()
        assert data["name"] == "github"
        assert data["status"] == "healthy"
        assert data["tool_calls_total"] == 100
        assert data["error_rate"] == 5.0  # 5/100 * 100

    def test_health_error_rate_zero_calls(self):
        """Test error rate when no calls made."""
        health = MCPServerHealth(
            name="test",
            status=MCPServerStatus.UNKNOWN,
            tool_calls_total=0,
            tool_errors_total=0,
        )
        data = health.to_dict()
        assert data["error_rate"] == 0.0


class TestMCPHealthSummary:
    """Tests for MCPHealthSummary dataclass."""

    def test_summary_creation(self):
        """Test creating a health summary."""
        servers = [
            MCPServerHealth(name="s1", status=MCPServerStatus.HEALTHY),
            MCPServerHealth(name="s2", status=MCPServerStatus.DEGRADED),
        ]
        summary = MCPHealthSummary(
            overall_status=MCPServerStatus.DEGRADED,
            servers=servers,
            healthy_count=1,
            degraded_count=1,
            unhealthy_count=0,
            total_tool_calls=50,
            total_errors=2,
        )
        assert summary.overall_status == MCPServerStatus.DEGRADED
        assert len(summary.servers) == 2
        assert summary.healthy_count == 1

    def test_summary_to_dict(self):
        """Test converting summary to dict."""
        summary = MCPHealthSummary(
            overall_status=MCPServerStatus.HEALTHY,
            servers=[],
            healthy_count=3,
            degraded_count=0,
            unhealthy_count=0,
            total_tool_calls=100,
            total_errors=1,
        )
        data = summary.to_dict()
        assert data["overall_status"] == "healthy"
        assert data["healthy_count"] == 3
        assert data["error_rate"] == 1.0


class TestMCPHealthMonitor:
    """Tests for MCPHealthMonitor class."""

    @pytest.fixture
    def monitor(self):
        """Create a test monitor instance."""
        return MCPHealthMonitor()

    def test_monitor_initialization(self, monitor):
        """Test monitor initializes correctly."""
        assert monitor._error_rate_degraded == 0.10
        assert monitor._error_rate_unhealthy == 0.50
        assert monitor._latency_degraded_ms == 5000.0

    def test_record_tool_call_success(self, monitor):
        """Test recording a successful tool call."""
        monitor.record_tool_call(
            server_name="datadog",
            tool_name="search_logs",
            latency_ms=150.0,
            success=True,
        )
        stats = monitor._stats.get("datadog")
        assert stats is not None
        assert stats.tool_calls == 1
        assert stats.errors == 0
        assert 150.0 in stats.latency_samples

    def test_record_tool_call_failure(self, monitor):
        """Test recording a failed tool call."""
        monitor.record_tool_call(
            server_name="github",
            tool_name="get_pr",
            latency_ms=500.0,
            success=False,
            error="API timeout",
        )
        stats = monitor._stats.get("github")
        assert stats.tool_calls == 1
        assert stats.errors == 1
        assert stats.last_error == "API timeout"

    def test_latency_sample_limit(self, monitor):
        """Test that latency samples are limited to 100."""
        for i in range(150):
            monitor.record_tool_call(
                server_name="test",
                tool_name="tool",
                latency_ms=float(i),
                success=True,
            )
        stats = monitor._stats.get("test")
        assert len(stats.latency_samples) == 100
        # Should keep the last 100 samples
        assert stats.latency_samples[0] == 50.0

    @pytest.mark.asyncio
    async def test_check_server_stopped(self, monitor):
        """Test checking a stopped server."""
        health = await monitor.check_server("nonexistent")
        assert health.status == MCPServerStatus.STOPPED

    @pytest.mark.asyncio
    async def test_check_all_empty(self, monitor):
        """Test checking all servers when none registered."""
        summary = await monitor.check_all()
        assert summary.overall_status == MCPServerStatus.UNKNOWN
        assert len(summary.servers) == 0


class TestGetHealthMonitor:
    """Tests for get_health_monitor singleton."""

    def test_get_health_monitor_singleton(self):
        """Test that get_health_monitor returns singleton."""
        monitor1 = get_health_monitor()
        monitor2 = get_health_monitor()
        assert monitor1 is monitor2

    def test_get_health_monitor_type(self):
        """Test that get_health_monitor returns correct type."""
        monitor = get_health_monitor()
        assert isinstance(monitor, MCPHealthMonitor)
