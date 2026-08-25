"""The MCP transport must outlive the task that created the CopilotSession.

Regression test for the bricked-thread bug: `/chat/stream` builds its session
inside the SSE generator task, and sse-starlette cancels that task's anyio task
group as soon as the response finishes. The MCP stdio sessions used to be
entered on *that* task, so the cancellation tore their anyio cancel scopes down
and closed the stdio streams — while the session stayed in the API's pool. Turn
2 on the same thread_id then hit `anyio.ClosedResourceError` and the thread was
dead for good.

These tests start a real MCP server (the `repo` one, no credentials needed),
cancel the task that opened the session exactly the way sse-starlette does, and
then call a tool.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest

import app.config as cfg
import app.mcp.client as mcp_client
from app.session import CopilotSession


@pytest.fixture
def one_mcp_server(monkeypatch, tmp_path):
    """Load only the credential-free `repo` server, against a temp checkpoint DB."""
    monkeypatch.setattr(mcp_client, "_SERVERS", ("repo",))
    monkeypatch.setenv("COPILOT_CHECKPOINT_DB", str(tmp_path / "ck.sqlite"))
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


async def _open_in_cancelled_task(thread_id: str) -> CopilotSession:
    """Open a session inside an anyio task group child, then cancel the group —
    a faithful stand-in for sse-starlette finishing an EventSourceResponse."""
    holder: dict[str, CopilotSession] = {}

    async def opener() -> None:
        holder["session"] = await CopilotSession(thread_id=thread_id).__aenter__()
        await anyio.sleep(3600)  # stay alive like an in-flight SSE response

    async with anyio.create_task_group() as tg:
        tg.start_soon(opener)
        for _ in range(600):  # up to ~60s for the subprocess handshake
            if "session" in holder:
                break
            await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    assert "session" in holder, "session never came up"
    await asyncio.sleep(0.2)  # let any teardown that is going to happen happen
    return holder["session"]


async def test_tools_still_work_after_the_creating_task_is_cancelled(one_mcp_server):
    session = await _open_in_cancelled_task("mcp-lifetime")
    try:
        tools = {t.name: t for t in session._tools}
        assert "list_dir" in tools, f"repo MCP server did not load: {sorted(tools)}"
        # Before the fix this raised anyio.ClosedResourceError.
        result = await tools["list_dir"].ainvoke({})
        assert result
    finally:
        await session.__aexit__(None, None, None)


async def test_graph_is_usable_after_the_creating_task_is_cancelled(one_mcp_server):
    """The compiled graph (and its checkpointer connection) must survive too."""
    session = await _open_in_cancelled_task("mcp-lifetime-graph")
    try:
        snapshot = await session._graph.aget_state(session._config)
        assert snapshot is not None
        assert await session.pending_interrupt() is False
    finally:
        await session.__aexit__(None, None, None)


async def test_close_releases_the_mcp_transport(one_mcp_server):
    """__aexit__ must actually shut the subprocesses down (no leaked servers)."""
    session = await CopilotSession(thread_id="mcp-lifetime-close").__aenter__()
    tools = {t.name: t for t in session._tools}
    assert "list_dir" in tools
    await session.__aexit__(None, None, None)
    assert session._owner is None
    with pytest.raises(BaseException):  # transport is gone; any call must fail
        await tools["list_dir"].ainvoke({})
