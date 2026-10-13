"""Regression coverage for partial-evidence investigation behavior."""

import json

import pytest


def test_tool_failure_is_structured_and_sanitized():
    from app.graph.builder import _tool_failure_message
    from app.tool_trust.gateway import ToolGatewayCode, ToolGatewayError

    error = ToolGatewayError(
        ToolGatewayCode.TOOL_FAILURE,
        tool_name="get_metric",
        provider="datadog",
    )
    payload = json.loads(_tool_failure_message(error))
    assert payload == {
        "status": "unavailable",
        "provider": "datadog",
        "tool": "get_metric",
        "reason": "tool_failure",
    }


def test_prometheus_metric_not_found_does_not_raise():
    from app.mcp.servers.logs_metrics import server

    result = server.get_metric("service-that-does-not-exist", "restart_count")
    assert result["status"] == "unavailable"
    assert result["provider"] == "prometheus"
    assert result["reason"] == "metric_not_found"


def test_datadog_metric_unavailable_does_not_raise(monkeypatch):
    from app.mcp.servers.datadog import server

    monkeypatch.setattr(server, "OFFLINE", True)
    monkeypatch.setattr(server, "_gate", lambda: {"error": "not configured"})
    result = server.get_metric("crashloop-demo", "bad query")
    assert result["status"] == "unavailable"
    assert result["provider"] == "datadog"
    assert result["reason"] == "not_configured"


def test_kubernetes_missing_pod_is_a_result_not_an_exception(monkeypatch):
    from app.mcp.servers.kubernetes import server

    monkeypatch.setattr(server, "OFFLINE", True)
    monkeypatch.setattr(server, "_gate", lambda: None)
    monkeypatch.setattr(server, "_get_pods", lambda: [{"name": "other", "status": "Running"}])
    result = server.describe_pod("crashloop-demo", "omnipotent-demo")
    assert "not found" in result["error"]
    assert result["available"] == ["other"]
