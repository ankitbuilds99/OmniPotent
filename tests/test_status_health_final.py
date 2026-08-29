"""The two halves of the MCP health work that never reached a user-visible surface.

Both are cases of a bound that existed in the comments but not in the code.

  * The status page synced only the components the health check *reported on*.
    Once idle MCP servers correctly stopped appearing in `summary.servers` —
    nothing has a session open, so nothing has measured them — the rows for them
    kept whatever they were last written as, with no path back. Rows set to
    `major_outage` by the older behaviour therefore showed a permanent outage on
    the public page for servers that were merely idle. This deployment's own
    database still holds 21 of them.
  * `_bounded_error_label` claimed to keep the `error` Prometheus label to a
    small set while actually admitting any 48 characters of `[A-Za-z0-9_.-]` —
    which is also the shape of a UUID, a release tag and a request id. The
    registry never evicts a series, so each distinct value was permanent.
"""

from __future__ import annotations

import pytest

from app.observability import metrics
from app.status_page import store as sp_store
from app.status_page.models import ComponentStatus


# --------------------------------------------------------------------------- #
# The status page: an idle server is unmeasured, not down.
# --------------------------------------------------------------------------- #
class _Health:
    def __init__(self, name: str, status: object) -> None:
        self.name = name
        self.status = status


class _Summary:
    def __init__(self, servers: list[_Health]) -> None:
        self.servers = servers


@pytest.fixture
def unmeasured_is_distinguishable(monkeypatch):
    """`_collect_mcp_statuses` has to say which of two things happened."""

    def _monitor(servers, raises=False):
        class _M:
            async def check_all(self):
                if raises:
                    raise RuntimeError("no transport")
                return _Summary(servers)

        return lambda: _M()

    return _monitor


async def test_an_idle_server_is_reported_as_unmeasured_not_as_an_outage(
    monkeypatch, unmeasured_is_distinguishable
):
    """The check ran and returned no servers. That is a measurement — every
    server is idle — so a row left at `major_outage` by the older behaviour has
    to come back, and `degraded` is the value this codebase already uses for
    "nothing has measured this" (`_map_mcp_status` maps MCPServerStatus.UNKNOWN
    to it, and a component row is created with it)."""
    monkeypatch.setattr(
        "app.mcp.health.get_health_monitor", unmeasured_is_distinguishable([])
    )
    measured = await sp_store._collect_mcp_statuses()

    # Empty dict, NOT None: the difference is the whole fix.
    assert measured == {}
    assert measured is not None


async def test_a_failed_check_is_not_a_measurement(
    monkeypatch, unmeasured_is_distinguishable
):
    """If the check itself raises, nothing was measured and every row must keep
    what it had. Returning `{}` here — which is what it used to do — would be
    indistinguishable from "every server is idle" and would rewrite every
    component off the back of a failure to look."""
    monkeypatch.setattr(
        "app.mcp.health.get_health_monitor",
        unmeasured_is_distinguishable([], raises=True),
    )
    assert await sp_store._collect_mcp_statuses() is None


async def test_a_reported_server_still_maps_to_its_measured_status(
    monkeypatch, unmeasured_is_distinguishable
):
    """The healing must not swallow a real outage: a server the check *did*
    report on keeps the status it was measured at."""
    from app.mcp.health import MCPServerStatus

    monkeypatch.setattr(
        "app.mcp.health.get_health_monitor",
        unmeasured_is_distinguishable(
            [
                _Health("argocd", MCPServerStatus.UNHEALTHY),
                _Health("alerting", MCPServerStatus.HEALTHY),
            ]
        ),
    )
    measured = await sp_store._collect_mcp_statuses()

    assert measured["mcp_argocd"] == ("argocd", ComponentStatus.MAJOR_OUTAGE)
    assert measured["mcp_alerting"] == ("alerting", ComponentStatus.OPERATIONAL)


async def test_a_stuck_outage_row_heals_once_the_server_goes_idle(
    tmp_path, monkeypatch, unmeasured_is_distinguishable
):
    """The fix itself, against a real database rather than the collector alone.

    Three syncs in the order that produced the 21 stuck rows: a server is
    measured unhealthy, then goes idle so the check stops reporting it, then the
    check fails outright. Only the middle one may rewrite the row, and before
    this fix none of them did — the row was written `major_outage` once and no
    subsequent sync ever touched it again.
    """
    from sqlalchemy import select

    from app.database import engine as db_engine
    from app.database.models import Base, StatusComponent
    from app.mcp.health import MCPServerStatus

    monkeypatch.setenv(
        "COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/status.db"
    )
    import app.config as cfg

    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None

    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def status_of(component_id: str) -> str | None:
        async with db_engine.get_session() as session:
            return (
                await session.execute(
                    select(StatusComponent.status).where(
                        StatusComponent.component_id == component_id
                    )
                )
            ).scalar_one_or_none()

    def reporting(servers, raises=False):
        monkeypatch.setattr(
            "app.mcp.health.get_health_monitor",
            unmeasured_is_distinguishable(servers, raises=raises),
        )

    reporting([_Health("argocd", MCPServerStatus.UNHEALTHY)])
    await sp_store._sync_from_health_checks()
    assert await status_of("mcp_argocd") == ComponentStatus.MAJOR_OUTAGE.value

    # No session open, so the server is absent from `summary.servers`. It is not
    # down; nobody looked. This is the transition that used to leave the public
    # page showing a permanent outage.
    reporting([])
    await sp_store._sync_from_health_checks()
    assert await status_of("mcp_argocd") == ComponentStatus.DEGRADED.value

    # And a check that raises is not a measurement of anything: the row keeps
    # what it last had rather than being healed off the back of a failure to look.
    reporting([_Health("argocd", MCPServerStatus.UNHEALTHY)])
    await sp_store._sync_from_health_checks()
    reporting([], raises=True)
    await sp_store._sync_from_health_checks()
    assert await status_of("mcp_argocd") == ComponentStatus.MAJOR_OUTAGE.value

    db_engine._engine = None
    db_engine._session_factory = None
    cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# The metric label: a genuinely closed set.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    [
        # Each of these passes the old `[A-Za-z0-9_.-]` shape check and would
        # have become its own permanent time series.
        "550e8400-e29b-41d4-a716-446655440000",
        "release-2026.08",
        "req_9f2c41ab",
        "feature-branch-retry-logic",
        "pod-checkout-7d9f8b6c4d-x2n4k",
    ],
)
def test_a_value_shaped_like_an_identifier_is_not_admitted(value: str) -> None:
    """The bound is on the *set*, not on the characters. Anything joined out of
    request data carries a separator; a classified kind and an exception class
    name do not."""
    assert metrics._bounded_error_label(value) == "unclassified"


def test_the_classified_kinds_still_reach_prometheus_verbatim() -> None:
    """Alert rules group by these, so bucketing them would break the rules this
    whole path exists to feed."""
    from app.mcp import client as mcp_client

    for kind in mcp_client._ERROR_KINDS:
        assert metrics._bounded_error_label(kind) == kind


def test_the_admitted_kinds_match_the_classifier_that_produces_them() -> None:
    """`metrics` cannot import `app.mcp.client` at module scope, so the kind
    list is duplicated there. If the two ever drift, every real MCP failure
    silently buckets as "unclassified" and the alert rules go quiet — which is
    the kind of failure that looks like good news."""
    from app.mcp import client as mcp_client

    assert metrics._ERROR_LABEL_KINDS == frozenset(mcp_client._ERROR_KINDS)


def test_an_exception_class_name_is_still_admitted() -> None:
    """Documented as accepted by `record_mcp_tool_call`, and drawn from the
    finite set of classes in the program rather than from request data."""
    for name in ("ConnectionTimeout", "TimeoutError", "McpError", "OSError"):
        assert metrics._bounded_error_label(name) == name


def test_a_message_is_bucketed_rather_than_dropped() -> None:
    """The failure still has to be counted; only the detail goes."""
    assert (
        metrics._bounded_error_label("ToolException: /etc/passwd escapes the sandbox")
        == "unclassified"
    )
    # Absent is its own answer, and not the same as unclassified: the caller
    # said nothing rather than saying something unusable.
    assert metrics._bounded_error_label(None) == "unknown"
    assert metrics._bounded_error_label("") == "unknown"
