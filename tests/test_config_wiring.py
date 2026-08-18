"""Settings that were hardcoded literals must actually reach their consumers.

Each of these was a knob that existed in Settings but changed nothing when an
operator moved it — the consumer still used its own literal, or the value never
crossed a process boundary. A test that only checks the setting's default would
not have caught any of them, so these assert that *changing* the setting changes
the behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import config as cfg

ROOT = Path(__file__).resolve().parent.parent


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


# --- health: the aggregate deadline must not cap the per-check one ----------


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


# --- MCP: env vars must be forwarded into the stdio subprocess --------------


def test_github_mcp_server_receives_the_timeout_settings(settings, monkeypatch):
    """A stdio server inherits only a fixed allowlist, never os.environ.

    HTTP_TIMEOUT_GITHUB / HTTP_TIMEOUT_MCP therefore had to be placed in the
    server's own env dict; without that both knobs were inert no matter what the
    operator set.
    """
    from app.mcp import client as mcp_client

    settings(HTTP_TIMEOUT_GITHUB="7.5", HTTP_TIMEOUT_MCP="42")
    env = mcp_client._server_config()["github"]["env"]

    assert env["HTTP_TIMEOUT_GITHUB"] == "7.5"
    assert env["HTTP_TIMEOUT_MCP"] == "42.0"


def test_github_server_reads_those_env_names(settings, monkeypatch):
    """The names forwarded above must be the names the server actually reads."""
    from app.mcp.servers.github import server as gh

    monkeypatch.setenv("HTTP_TIMEOUT_MCP", "99")
    assert gh._mcp_timeout() == 99.0
    monkeypatch.setenv("HTTP_TIMEOUT_MCP", "not-a-number")
    assert gh._mcp_timeout() == 20.0  # malformed must not mean "no timeout"


# --- similar search: the auto-suggest bar must stay reachable ---------------


def test_auto_suggest_bar_tracks_the_configured_floor(settings):
    from app.similar_search import search as ss

    settings(SIMILAR_INCIDENT_THRESHOLD="0.5")
    assert ss._default_auto_suggest_threshold() == pytest.approx(0.6)


def test_auto_suggest_bar_never_reaches_an_unattainable_1_0(settings):
    """The call site tests `score > threshold` and scores cap at 1.0.

    A threshold of exactly 1.0 disables auto-suggest outright, which is the
    opposite of what an operator raising the floor to 0.95 intends.
    """
    from app.similar_search import search as ss

    settings(SIMILAR_INCIDENT_THRESHOLD="0.95")
    bar = ss._default_auto_suggest_threshold()
    assert bar < 1.0
    assert ss.SearchConfig().auto_suggest_threshold < 1.0


# --- remediation: the act-on-it bar is the safety threshold -----------------


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


# --- semantic cache: the process-wide instance must read the knobs ----------


def test_default_semantic_cache_is_built_from_settings(settings, monkeypatch):
    """get_semantic_cache() used to call SemanticCache() bare, ignoring CACHE_*."""
    from app.llm_advanced import semantic_cache as sc

    monkeypatch.setattr(sc, "_cache", None)
    settings(CACHE_SIMILARITY_THRESHOLD_MEDIUM="0.77", CACHE_MAX_ENTRIES_MEDIUM="123")

    cache = sc.get_semantic_cache()
    assert cache._similarity_threshold == pytest.approx(0.77)
    assert cache._max_entries == 123


# --- deployment manifests must match the uid the image builds --------------


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
