"""Every declared setting must be read by something.

`Settings` is declared with `extra="ignore"`, which means an operator who exports
a variable this model does not know about gets no error — and, symmetrically, a
field this model declares but no code reads looks exactly like a working knob
from the outside: you set it, nothing complains, nothing happens. That is the
same class of defect as a hardcoded "analysis" result, just in the control plane,
so it gets a test.

Also pins the logging wiring: the COPILOT_LOG_* settings that
deploy/kubernetes/configmap.yaml and the Helm chart set were declared in
app/config.py and read by nobody, so JSON logging in Kubernetes happened only by
accident (configure_logging defaulted to json in production).
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

from app.config import Settings
from app.observability_core import configure_logging

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = ROOT / "app" / "config.py"


# --------------------------------------------------------------------------- #
# no dead settings
# --------------------------------------------------------------------------- #


def _app_sources_excluding_config() -> str:
    parts = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        if path == CONFIG_PY:
            continue
        parts.append(path.read_text(errors="ignore"))
    return "\n".join(parts)


def _fields_reachable_via_accessors(app_blob: str) -> set[str]:
    """Fields read indirectly, through a `Settings` property/method that app/ calls.

    e.g. nothing says `settings.cors_origins`, but `settings.allowed_origins` is
    used by the CORS middleware and reads `self.cors_origins`.
    """
    tree = ast.parse(CONFIG_PY.read_text())
    settings_cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )

    reachable: set[str] = set()
    for member in settings_cls.body:
        if not isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if member.name not in app_blob:
            continue  # the accessor itself has no caller
        for node in ast.walk(member):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "self":
                    reachable.add(node.attr)
    return reachable


def test_no_setting_is_declared_without_a_reader():
    app_blob = _app_sources_excluding_config()
    reachable = _fields_reachable_via_accessors(app_blob)

    dead = sorted(
        name
        for name in Settings.model_fields
        if name not in app_blob and name not in reachable
    )
    assert not dead, (
        "app/config.py declares settings that nothing in app/ reads. Because "
        'Settings is extra="ignore", setting one of these produces no error and '
        "no effect — an operator cannot tell it from a working knob. Wire it to "
        f"the code that should honour it, or delete it: {dead}"
    )


@pytest.mark.parametrize(
    "gone",
    [
        # Deleted because nothing read them; listed individually so re-adding a
        # field without a reader trips this test rather than sliding back in.
        "safety_confidence_threshold",
        "safety_action_thresholds",
        "safety_max_blast_radius",
        "safety_protected_services",
        "deployment_canary_steps",
        "deployment_max_error_rate",
        "cost_idle_threshold",
        "anomaly_trend_threshold",
        "cache_similarity_threshold_high",
        "log_similarity_threshold",
        "similar_incident_threshold",
        "copilot_cache_ttl_default",
        "copilot_retention_days",
        "redis_max_connections",
        "http_timeout_mcp",
        "http_timeout_health",
        "azure_client_id",
    ],
)
def test_unwired_settings_stay_deleted(gone):
    assert gone not in Settings.model_fields


def test_dead_settings_helpers_are_gone():
    for helper in (
        "get_action_confidence_thresholds",
        "get_max_blast_radius",
        "get_protected_services",
        "get_canary_steps",
    ):
        assert not hasattr(Settings, helper), (
            f"Settings.{helper} parses a setting that no longer exists and had no callers"
        )


def test_github_timeout_reaches_the_github_mcp_subprocess():
    """The GitHub MCP server is a subprocess reading env, not this Settings model.

    HTTP_TIMEOUT_GITHUB therefore only takes effect if app/mcp/client.py forwards
    it; without the forward, a value in .env was read by nobody.
    """
    from app.mcp import client

    env = client._server_config()["github"]["env"]
    assert env["HTTP_TIMEOUT_GITHUB"] == str(Settings().http_timeout_github)


# --------------------------------------------------------------------------- #
# logging is configured from the COPILOT_LOG_* settings
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def _reconfigure(monkeypatch, **env):
    from app import config

    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    try:
        configure_logging()
    finally:
        config.get_settings.cache_clear()


def test_copilot_log_level_sets_the_root_level(monkeypatch):
    _reconfigure(monkeypatch, COPILOT_LOG_LEVEL="warning")
    assert logging.getLogger().level == logging.WARNING


def test_copilot_log_format_selects_the_json_formatter(monkeypatch):
    _reconfigure(monkeypatch, COPILOT_LOG_FORMAT="json", COPILOT_ENV="development")
    handler = logging.getLogger().handlers[0]
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    handler.filter(record)
    assert json.loads(handler.format(record))["msg"] == "hello"


def test_copilot_log_format_text_overrides_the_production_default(monkeypatch):
    _reconfigure(
        monkeypatch,
        COPILOT_LOG_FORMAT="text",
        COPILOT_ENV="production",
        COPILOT_API_TOKEN="t",
    )
    handler = logging.getLogger().handlers[0]
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    handler.filter(record)
    formatted = handler.format(record)
    with pytest.raises(json.JSONDecodeError):
        json.loads(formatted)
    assert "hello" in formatted


def test_copilot_log_file_adds_a_file_sink_and_creates_its_directory(
    monkeypatch, tmp_path
):
    target = tmp_path / "nested" / "dir" / "copilot.log"
    _reconfigure(monkeypatch, COPILOT_LOG_FILE=str(target), COPILOT_LOG_FORMAT="text")

    logging.getLogger("omnipotent.test").warning("to the file")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert target.is_file(), "COPILOT_LOG_FILE did not create the log file"
    assert "to the file" in target.read_text()
