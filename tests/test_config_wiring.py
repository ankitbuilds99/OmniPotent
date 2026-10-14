"""Settings must be wired: every declared knob is read, and reading it matters.

Two halves, from two lines of work, that guard the same failure from opposite
ends:

1. No dead settings. `Settings` is declared with `extra="ignore"`, which means an
   operator who exports a variable this model does not know about gets no error
   — and, symmetrically, a field this model declares but no code reads looks
   exactly like a working knob from the outside: you set it, nothing complains,
   nothing happens. That is the same class of defect as a hardcoded "analysis"
   result, just in the control plane, so it gets a test.

2. No inert settings. Each of the knobs asserted below existed in `Settings` but
   changed nothing when an operator moved it — the consumer still used its own
   literal, or the value never crossed a process boundary into the stdio
   subprocess that needed it. A test that only checks the setting's default
   would not have caught any of them, so these assert that *changing* the
   setting changes the behaviour.

Also pins the logging wiring: the COPILOT_LOG_* settings that
deploy/kubernetes/configmap.yaml and the Helm chart set were declared in
app/config.py and read by nobody, so JSON logging in Kubernetes happened only by
accident (configure_logging defaulted to json in production).
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

import pytest

from app import config as cfg
from app.config import Settings
from app.observability_core import configure_logging

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = ROOT / "app" / "config.py"


@pytest.fixture
def settings(monkeypatch):
    """Set env vars and hand back a reader for freshly-resolved settings."""

    def apply(**env: str):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        cfg.get_settings.cache_clear()
        return cfg.get_settings()

    cfg.get_settings.cache_clear()
    yield apply
    cfg.get_settings.cache_clear()


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

    Transitively: an accessor may read another accessor rather than a field
    (`oauth_allowlist_configured` reads `self.oauth_allowed_emails`, which is the
    property that parses `copilot_oauth_allowed_emails`). Stopping at one hop
    reported the underlying field as dead even though the chain that reads it
    starts from a real call site in app/.
    """
    tree = ast.parse(CONFIG_PY.read_text())
    settings_cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )

    accessors = {
        member.name: {
            node.attr
            for node in ast.walk(member)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        }
        for member in settings_cls.body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    # Seed with the accessors app/ actually calls, then follow self-references
    # until nothing new appears.
    reachable: set[str] = set()
    pending = [name for name in accessors if name in app_blob]
    while pending:
        current = pending.pop()
        for attr in accessors[current]:
            if attr in reachable:
                continue
            reachable.add(attr)
            if attr in accessors:
                pending.append(attr)
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
        #
        # The per-action safety knobs: parsed only by the get_* helpers that were
        # deleted with them. SAFETY_CONFIDENCE_THRESHOLD is a different setting
        # and is asserted present below.
        "safety_action_thresholds",
        "safety_max_blast_radius",
        "safety_protected_services",
        # Progressive delivery is ArgoCD's, configured in deploy/argocd/, not by
        # this process.
        "deployment_canary_steps",
        "deployment_max_error_rate",
        "deployment_max_latency_p99_ms",
        "deployment_min_success_rate",
        "deployment_health_check_interval_s",
        "deployment_rollback_on_failure",
        # These four groups had exactly one reader each, and that reader is a
        # module this branch deleted: app/cost_optimization/analyzer.py,
        # app/monitoring/ambient.py, app/llm_advanced/semantic_cache.py,
        # app/log_intelligence/clustering.py and app/similar_search/*. If one of
        # those modules comes back, its setting comes back with it.
        "cost_underutilized_threshold",
        "cost_idle_threshold",
        "cost_anomaly_threshold",
        "cost_budget_alert_threshold",
        "anomaly_z_threshold",
        "anomaly_trend_threshold",
        "anomaly_pattern_threshold",
        "cache_similarity_threshold_high",
        "cache_similarity_threshold_medium",
        "cache_similarity_threshold_low",
        "cache_max_entries_small",
        "cache_max_entries_medium",
        "cache_max_entries_large",
        "log_similarity_threshold",
        "similar_incident_threshold",
        # No caller: @cached/@cached_async is applied nowhere, and
        # governance.apply_retention() is never scheduled.
        "copilot_cache_ttl_default",
        "copilot_retention_days",
        # Only the Azure subscription id/region reach the multicloud MCP server;
        # the service-principal triple authenticated nothing.
        "azure_client_id",
        "azure_client_secret",
        "azure_tenant_id",
    ],
)
def test_unwired_settings_stay_deleted(gone):
    assert gone not in Settings.model_fields


@pytest.mark.parametrize(
    ("field", "reader"),
    [
        # The mirror image of the test above, and the reason this list is not
        # empty: these were candidates for deletion as "nothing reads them", and
        # then something was made to read them. Deleting one now would silently
        # re-hardcode its consumer's literal, which is the failure this whole
        # module exists to catch — so the reader is named here.
        ("http_timeout_health", "app/health.py"),
        ("http_timeout_mcp", "app/mcp/client.py -> the github stdio server"),
        ("redis_min_connections", "app/redis_pool.py"),
        ("redis_max_connections", "app/redis_pool.py"),
        ("redis_connection_timeout", "app/redis_pool.py"),
        ("redis_socket_timeout", "app/redis_pool.py"),
        (
            "safety_confidence_threshold",
            "app/coordinator/handlers/remediation.py + app/verification/consistency_checker.py",
        ),
    ],
)
def test_wired_settings_stay_declared(field, reader):
    assert field in Settings.model_fields, (
        f"{field} is read by {reader}; deleting it re-hardcodes that literal"
    )


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


# --------------------------------------------------------------------------- #
# MCP: env vars must be forwarded into the stdio subprocess
# --------------------------------------------------------------------------- #


def test_github_mcp_server_receives_the_timeout_settings(settings):
    """A stdio server inherits only a fixed allowlist, never os.environ.

    The GitHub MCP server is a subprocess reading env, not this Settings model,
    so HTTP_TIMEOUT_GITHUB / HTTP_TIMEOUT_MCP had to be placed in the server's
    own env dict; without that both knobs were inert no matter what the operator
    set, and a value in .env was read by nobody.
    """
    from app.mcp import client as mcp_client

    # Defaults: whatever Settings resolves is what the subprocess is told.
    env = mcp_client._server_config()["github"]["env"]
    assert env["HTTP_TIMEOUT_GITHUB"] == str(cfg.get_settings().http_timeout_github)
    assert env["HTTP_TIMEOUT_MCP"] == str(cfg.get_settings().http_timeout_mcp)

    # ...and moving the setting moves the forwarded value.
    settings(HTTP_TIMEOUT_GITHUB="7.5", HTTP_TIMEOUT_MCP="42")
    env = mcp_client._server_config()["github"]["env"]
    assert env["HTTP_TIMEOUT_GITHUB"] == "7.5"
    assert env["HTTP_TIMEOUT_MCP"] == "42.0"


def test_github_server_reads_those_env_names(monkeypatch):
    """The names forwarded above must be the names the server actually reads."""
    from app.mcp.servers.github import server as gh

    monkeypatch.setenv("HTTP_TIMEOUT_MCP", "99")
    assert gh._mcp_timeout() == 99.0
    monkeypatch.setenv("HTTP_TIMEOUT_MCP", "not-a-number")
    assert gh._mcp_timeout() == 20.0  # malformed must not mean "no timeout"


# --------------------------------------------------------------------------- #
# health: the aggregate deadline must not cap the per-check one
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_check_all_deadline_follows_http_timeout_health(settings):
    """A fixed 10s aggregate silently capped HTTP_TIMEOUT_HEALTH above 10."""
    from app.health import HealthChecker

    captured: list[float] = []

    async def spy(coro, timeout):
        captured.append(timeout)
        return await coro

    checker = HealthChecker()

    async def ok():
        from app.health import ComponentHealth, HealthStatus

        return ComponentHealth(name="c", status=HealthStatus.HEALTHY)

    checker.register("c", ok)

    settings(HTTP_TIMEOUT_HEALTH="30")
    import app.health as health_mod

    orig = health_mod.asyncio.wait_for
    health_mod.asyncio.wait_for = spy
    try:
        await checker.check_all()
    finally:
        health_mod.asyncio.wait_for = orig

    # The outer (aggregate) deadline is the largest; it must exceed the raised
    # per-check value rather than sit at the old hardcoded 10.
    assert max(captured) == pytest.approx(60.0)
    assert max(captured) > 30.0


@pytest.mark.asyncio
async def test_check_all_still_honours_an_explicit_argument(settings):
    from app.health import ComponentHealth, HealthChecker, HealthStatus

    checker = HealthChecker()

    async def ok():
        return ComponentHealth(name="c", status=HealthStatus.HEALTHY)

    checker.register("c", ok)
    result = await checker.check_all(timeout=1.0)
    assert result.components["c"].status is HealthStatus.HEALTHY


# --------------------------------------------------------------------------- #
# remediation: the act-on-it bar is the safety threshold
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fix_branch_uses_the_safety_confidence_threshold(settings):
    """Raising the safety threshold must also raise the bar for routing to a fix.

    It was a literal 0.8 here, so the router and the safety gates disagreed about
    what counts as confident enough to change production.
    """
    from app.coordinator.handlers.remediation import create_remediation_handlers

    decide = create_remediation_handlers()["decision_gate"]
    inputs = {"dep_hypothesize": {"hypotheses": [{"confidence": 0.85, "cause": "x"}]}}

    settings(SAFETY_CONFIDENCE_THRESHOLD="0.80")
    assert (await decide(None, inputs))["decision"] == "fix"

    settings(SAFETY_CONFIDENCE_THRESHOLD="0.90")
    assert (await decide(None, inputs))["decision"] == "investigate_more"


@pytest.mark.asyncio
async def test_investigate_bar_can_never_exceed_the_fix_bar(settings):
    """Lowering the safety threshold below 0.5 must not strand a whole band."""
    from app.coordinator.handlers.remediation import create_remediation_handlers

    decide = create_remediation_handlers()["decision_gate"]

    settings(SAFETY_CONFIDENCE_THRESHOLD="0.30")
    # 0.35 clears the (lowered) fix bar, so it routes to fix rather than falling
    # into an investigate band that now sits above it.
    result = await decide(None, {"dep_hypothesize": {"hypotheses": [{"confidence": 0.35}]}})
    assert result["decision"] == "fix"
    # ...and something below it still escalates rather than vanishing.
    low = await decide(None, {"dep_hypothesize": {"hypotheses": [{"confidence": 0.1}]}})
    assert low["decision"] == "escalate"


# Two more settings-wiring tests used to live here and were removed with the
# code they covered, not because the wiring stopped mattering:
#   * the SIMILAR_INCIDENT_THRESHOLD auto-suggest floor (app/similar_search/*)
#   * the CACHE_* defaults of get_semantic_cache() (app/llm_advanced/semantic_cache.py)
# Both modules were deleted in this branch as unimported. If either is restored,
# restore its setting in app/config.py and its test here together — the setting
# is asserted deleted by test_unwired_settings_stay_deleted above.


# --------------------------------------------------------------------------- #
# deployment manifests must match the uid the image builds
# --------------------------------------------------------------------------- #


def _dockerfile_uid() -> int:
    text = (ROOT / "Dockerfile").read_text()
    match = re.search(r"useradd[^\n]*--uid\s+(\d+)", text)
    assert match, "Dockerfile no longer creates its user with an explicit --uid"
    return int(match.group(1))


@pytest.mark.parametrize(
    "manifest",
    [
        "deploy/kubernetes/deployment.yaml",
        "deploy/kubernetes/base/migration-job.yaml",
        "deploy/helm/copilot/values.yaml",
        "deploy/helm/devops-copilot/values.yaml",
        "deploy/helm/values/staging.yaml",
    ],
)
def test_manifests_run_as_the_uid_that_owns_app(manifest):
    """runAsUser must match the uid the Dockerfile chowns /app to.

    These all said 1000 while the image builds uid 10001. The process then owned
    nothing in the image, could not write COPILOT_STATE_DIR, and every SQLite
    store failed to open — a hard startup failure on every k8s deployment.
    """
    uid = _dockerfile_uid()
    text = (ROOT / manifest).read_text()
    found = re.findall(r"^\s*(?:runAsUser|runAsGroup|fsGroup):\s*(\d+)\s*$", text, re.M)
    assert found, f"{manifest} declares no runAsUser/fsGroup"
    mismatched = sorted({v for v in found if int(v) != uid})
    assert not mismatched, (
        f"{manifest} runs as uid {mismatched} but the image builds uid {uid}; "
        "the state directory would be unwritable"
    )


def test_helm_state_dir_matches_the_persistence_mount_point():
    """COPILOT_STATE_DIR must be where the volume is actually mounted."""
    import yaml

    values = yaml.safe_load((ROOT / "deploy/helm/copilot/values.yaml").read_text())
    assert values["config"]["COPILOT_STATE_DIR"] == values["persistence"]["mountPath"]


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
        COPILOT_SECRET_KEY="s",
        COPILOT_CHECKPOINT_DB="postgresql://localhost/test",
        COPILOT_REDIS_URL="redis://localhost/0",
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
