"""Installing an agent means something now.

`AgentManager.install()` used to accept any ID in the catalogue and answer
`{"status": "success", "message": "Installed Prometheus Metrics v1.0.0"}`. It
downloaded nothing, verified nothing and wired nothing: `app/mcp/client.py`
builds its server list from a hard-coded tuple, so the "installed" agent
contributed zero tools. The investigating agent relayed the success to the
operator and then called `query_prometheus`, which does not exist.

These tests pin the three halves of the fix: an entry with no implementation is
refused, an entry whose module actually resolves installs and shows up in the
MCP server config, and the state file lives outside the source tree so an
install survives the next image build.
"""

from __future__ import annotations

import json

import pytest

import app.config as cfg
from app import policy
from app.marketplace import manager as mgr
from app.marketplace import registry as reg


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A manager pointed at a scratch state file, with a clean registry."""
    monkeypatch.setenv(
        "COPILOT_MARKETPLACE_STATE_PATH", str(tmp_path / "state" / "installed.json")
    )
    cfg.get_settings.cache_clear()
    reg.reset_registry()
    mgr.reset_manager()
    yield tmp_path / "state" / "installed.json"
    cfg.get_settings.cache_clear()
    reg.reset_registry()
    mgr.reset_manager()


def _real_entry(agent_id: str = "docs-helper", **kw) -> reg.AgentEntry:
    """A catalogue entry backed by a module that genuinely exists here."""
    entry = reg.AgentEntry(
        id=agent_id,
        name="Docs Helper",
        description="Ships with this deployment",
        version="2.1.0",
        author="OmniPotent",
        category="docs",
        module="app.mcp.servers.docs.server",
        **kw,
    )
    reg.get_registry().register(entry)
    return entry


# --------------------------------------------------------------------------- #
# Availability — the check that did not exist
# --------------------------------------------------------------------------- #
class TestAvailability:
    def test_an_entry_without_a_module_is_unavailable(self, store):
        assert reg.get_registry().get_agent("prometheus-metrics").is_available() is False

    def test_an_entry_naming_a_missing_module_is_unavailable(self, store):
        entry = _real_entry("ghost", )
        entry.module = "app.mcp.servers.definitely_not_here.server"
        assert entry.is_available() is False

    def test_an_entry_naming_a_present_module_is_available(self, store):
        assert _real_entry().is_available() is True

    def test_every_builtin_listing_is_honest_about_it(self, store):
        """All ten ship no implementation; the listing has to say so."""
        agents = reg.get_registry().list_agents()
        assert agents, "the catalogue should not be empty"
        assert all(a.to_dict()["available"] is False for a in agents)

    def test_available_only_filters_the_catalogue(self, store):
        _real_entry()
        listed = reg.get_registry().list_agents(available_only=True)
        assert [a.id for a in listed] == ["docs-helper"]


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #
class TestInstall:
    def test_installing_an_agent_with_no_implementation_is_refused(self, store):
        """The bug: this answered "success" for an agent that does not exist."""
        result = mgr.get_manager().install("prometheus-metrics")

        assert result["status"] == "unavailable"
        assert "cannot be installed" in result["message"]
        assert mgr.get_manager().is_installed("prometheus-metrics") is False
        assert not store.exists(), "nothing should have been recorded"

    def test_the_refusal_names_where_to_get_it(self, store):
        result = mgr.get_manager().install("prometheus-metrics")
        assert result["source"] == "https://github.com/omnipotent/agent-prometheus"

    def test_installing_an_available_agent_succeeds(self, store):
        _real_entry()
        result = mgr.get_manager().install("docs-helper", {"DOCS_ROOT": "/srv/docs"})

        assert result["status"] == "success"
        assert result["agent"]["version"] == "2.1.0"
        assert result["agent"]["module"] == "app.mcp.servers.docs.server"
        assert result["warnings"] == []
        assert mgr.get_manager().is_installed("docs-helper") is True

    def test_unknown_agent_is_an_error_not_an_install(self, store):
        result = mgr.get_manager().install("no-such-agent")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_installing_twice_is_refused(self, store):
        _real_entry()
        mgr.get_manager().install("docs-helper")
        assert mgr.get_manager().install("docs-helper")["status"] == "error"

    def test_missing_env_vars_are_warned_about_not_hidden(self, store, monkeypatch):
        monkeypatch.delenv("DOCS_TOKEN", raising=False)
        _real_entry(env_vars=["DOCS_TOKEN"])

        result = mgr.get_manager().install("docs-helper")

        assert result["status"] == "success"
        assert "DOCS_TOKEN" in result["warnings"][0]
        assert "will fail" in result["warnings"][0]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
class TestPersistence:
    def test_state_lives_outside_the_source_tree(self, store):
        """It was app/marketplace/installed.json — erased by every image build."""
        default = cfg.Settings(copilot_marketplace_state_path="").marketplace_state_path
        assert "app/marketplace" not in str(default)
        assert default.parent.name == "data"

    def test_an_install_survives_a_restart(self, store):
        _real_entry()
        mgr.get_manager().install("docs-helper", {"DOCS_ROOT": "/srv/docs"})

        mgr.reset_manager()  # as a process restart would
        _real_entry()

        installed = mgr.get_manager().get_installed("docs-helper")
        assert installed is not None
        assert installed.config == {"DOCS_ROOT": "/srv/docs"}

    def test_a_failed_write_reports_failure_instead_of_success(self, store, monkeypatch):
        """A read-only rootfs used to swallow the OSError and still say success."""
        _real_entry()

        def boom(self):
            raise OSError("Read-only file system")

        monkeypatch.setattr(mgr.AgentManager, "_save", boom)

        result = mgr.get_manager().install("docs-helper")

        assert result["status"] == "error"
        assert "Could not persist" in result["message"]
        assert mgr.get_manager().is_installed("docs-helper") is False

    def test_a_corrupt_state_file_does_not_crash_the_manager(self, store):
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{ not json")
        assert mgr.AgentManager().list_installed() == []


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
class TestLifecycle:
    def test_uninstall_round_trip(self, store):
        _real_entry()
        m = mgr.get_manager()
        m.install("docs-helper")

        assert m.uninstall("docs-helper")["status"] == "success"
        assert m.is_installed("docs-helper") is False
        assert json.loads(store.read_text())["agents"] == []

    def test_uninstalling_something_not_installed_is_an_error(self, store):
        assert mgr.get_manager().uninstall("docs-helper")["status"] == "error"

    def test_disable_then_enable(self, store):
        _real_entry()
        m = mgr.get_manager()
        m.install("docs-helper")

        assert m.disable("docs-helper")["status"] == "success"
        assert m.list_installed(enabled_only=True) == []

        assert m.enable("docs-helper")["status"] == "success"
        assert [a.id for a in m.list_installed(enabled_only=True)] == ["docs-helper"]

    def test_configure_merges_and_persists(self, store):
        _real_entry()
        m = mgr.get_manager()
        m.install("docs-helper", {"A": "1"})

        result = m.configure("docs-helper", {"B": "2"})

        assert result["config"] == {"A": "1", "B": "2"}
        saved = json.loads(store.read_text())["agents"][0]
        assert saved["config"] == {"A": "1", "B": "2"}

    def test_agent_info_reports_availability(self, store):
        info = mgr.get_manager().get_agent_info("prometheus-metrics")
        assert info["is_available"] is False
        assert info["is_installed"] is False


# --------------------------------------------------------------------------- #
# The wiring — an install has to change what the LLM can call
# --------------------------------------------------------------------------- #
class TestServerWiring:
    def test_an_enabled_install_becomes_a_server_config(self, store):
        _real_entry(env_vars=["DOCS_TOKEN"])
        mgr.get_manager().install("docs-helper", {"DOCS_ROOT": "/srv/docs"})

        configs = mgr.installed_server_configs("/usr/bin/python3")

        assert list(configs) == ["marketplace:docs-helper"]
        entry = configs["marketplace:docs-helper"]
        assert entry["command"] == "/usr/bin/python3"
        assert entry["args"] == ["-m", "app.mcp.servers.docs.server"]
        assert entry["transport"] == "stdio"
        assert entry["env"]["DOCS_ROOT"] == "/srv/docs"
        assert "DOCS_TOKEN" in entry["env"]

    def test_a_disabled_install_is_not_launched(self, store):
        _real_entry()
        mgr.get_manager().install("docs-helper")
        mgr.get_manager().disable("docs-helper")

        assert mgr.installed_server_configs("python3") == {}

    def test_an_install_whose_module_vanished_is_skipped(self, store):
        _real_entry()
        m = mgr.get_manager()
        m.install("docs-helper")
        m.get_installed("docs-helper").module = "app.mcp.servers.gone.server"

        assert mgr.installed_server_configs("python3") == {}

    def test_the_client_merges_installed_agents_into_its_server_map(self, store):
        from app.mcp import client

        _real_entry()
        before = client._server_config()
        assert "marketplace:docs-helper" not in before

        mgr.get_manager().install("docs-helper")
        after = client._server_config()

        assert "marketplace:docs-helper" in after
        # ...and the built-ins are all still there.
        assert set(before) <= set(after)

    def test_every_builtin_server_has_a_config_entry(self, store):
        """load_mcp_tools iterates the config; a name in _SERVERS with no entry
        used to raise KeyError into a bare except and vanish."""
        from app.mcp import client

        assert set(client._SERVERS) <= set(client._server_config())


# --------------------------------------------------------------------------- #
# Policy — these tools mutate the deployment, not a monitored system
# --------------------------------------------------------------------------- #
class TestPolicy:
    @pytest.mark.parametrize(
        "tool", ["install_agent", "uninstall_agent", "configure_agent"]
    )
    def test_deployment_changing_tools_need_approval(self, tool):
        assert policy.requires_approval(tool) is True
        assert tool in policy.APPROVE_TOOLS

    @pytest.mark.parametrize("tool", ["enable_agent", "disable_agent"])
    def test_reversible_tools_are_audited_not_gated(self, tool):
        decision = policy.classify(tool)
        assert decision["decision"] == "notify"
        assert decision["write"] is True

    def test_the_approval_card_names_the_agent(self):
        preview = policy.describe_action("install_agent", {"agent_id": "prometheus-metrics"})
        assert "prometheus-metrics" in preview
