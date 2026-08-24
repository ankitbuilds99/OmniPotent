"""Tests for WebSocket endpoint."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def mock_settings():
    """Mock settings with WebSocket enabled."""
    with patch("app.api.websocket.get_settings") as mock:
        settings = MagicMock()
        settings.copilot_websocket_enabled = True
        settings.copilot_websocket_ping_interval = 30
        mock.return_value = settings
        yield settings


@pytest.fixture
def mock_settings_disabled():
    """Mock settings with WebSocket disabled."""
    with patch("app.api.websocket.get_settings") as mock:
        settings = MagicMock()
        settings.copilot_websocket_enabled = False
        settings.copilot_websocket_ping_interval = 30
        mock.return_value = settings
        yield settings


def test_parse_message_valid():
    """Test parsing valid WebSocket messages."""
    from app.api.websocket import parse_message

    # Chat message
    msg = parse_message('{"type": "chat", "thread_id": "test-123", "message": "Hello"}')
    assert msg is not None
    assert msg.type == "chat"
    assert msg.thread_id == "test-123"
    assert msg.message == "Hello"

    # Approval message
    msg = parse_message('{"type": "approval", "thread_id": "test-123", "approved": true, "reason": "LGTM"}')
    assert msg is not None
    assert msg.type == "approval"
    assert msg.approved is True
    assert msg.reason == "LGTM"

    # Ping message
    msg = parse_message('{"type": "ping"}')
    assert msg is not None
    assert msg.type == "ping"


def test_parse_message_invalid():
    """Test parsing invalid WebSocket messages."""
    from app.api.websocket import parse_message

    # Invalid JSON
    assert parse_message("not json") is None

    # Missing type
    msg = parse_message('{"thread_id": "test"}')
    assert msg is not None
    assert msg.type == ""

    # Empty string
    assert parse_message("") is None


def test_connection_manager_connect_disconnect():
    """Test ConnectionManager connect/disconnect."""
    import asyncio
    from app.api.websocket import ConnectionManager

    manager = ConnectionManager()

    # Create mock websocket
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.client_state = MagicMock()

    async def test():
        # Connect
        conn = await manager.connect(ws, "thread-1", "session-1")
        assert conn.thread_id == "thread-1"
        assert conn.session_id == "session-1"
        assert manager.connection_count("thread-1") == 1
        assert manager.connection_count() == 1

        # Disconnect
        await manager.disconnect(conn)
        assert manager.connection_count("thread-1") == 0

    asyncio.run(test())


def test_connection_manager_broadcast():
    """Test ConnectionManager broadcasting."""
    import asyncio
    from app.api.websocket import ConnectionManager
    from unittest.mock import MagicMock
    from starlette.websockets import WebSocketState

    manager = ConnectionManager()

    # Create mock websockets
    ws1 = MagicMock()
    ws1.accept = AsyncMock()
    ws1.send_json = AsyncMock()
    ws1.client_state = WebSocketState.CONNECTED

    ws2 = MagicMock()
    ws2.accept = AsyncMock()
    ws2.send_json = AsyncMock()
    ws2.client_state = WebSocketState.CONNECTED

    async def test():
        # Connect two clients to same thread
        await manager.connect(ws1, "thread-1")
        await manager.connect(ws2, "thread-1")

        # Broadcast
        sent = await manager.broadcast("thread-1", {"type": "trace", "line": "test"})
        assert sent == 2
        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()

        # Broadcast to non-existent thread
        sent = await manager.broadcast("thread-nonexistent", {"type": "test"})
        assert sent == 0

    asyncio.run(test())


def test_connection_stats():
    """Test get_connection_stats."""
    from app.api.websocket import get_connection_stats

    with patch("app.api.websocket.get_settings") as mock_settings:
        settings = MagicMock()
        settings.copilot_websocket_enabled = True
        mock_settings.return_value = settings

        stats = get_connection_stats()
        assert "total_connections" in stats
        assert "enabled" in stats
        assert stats["enabled"] is True


def test_connection_stats_disabled():
    """Test get_connection_stats when disabled."""
    from app.api.websocket import get_connection_stats

    with patch("app.api.websocket.get_settings") as mock_settings:
        settings = MagicMock()
        settings.copilot_websocket_enabled = False
        mock_settings.return_value = settings

        stats = get_connection_stats()
        assert stats["enabled"] is False


def test_session_queue():
    """Test session message queuing for reconnection."""
    import asyncio
    from app.api.websocket import ConnectionManager

    manager = ConnectionManager()

    async def test():
        # Queue messages for a session
        await manager.queue_for_session("session-1", {"type": "trace", "line": "msg1"})
        await manager.queue_for_session("session-1", {"type": "trace", "line": "msg2"})

        # Get pending messages
        pending = await manager.get_pending("session-1")
        assert len(pending) == 2
        assert pending[0]["line"] == "msg1"
        assert pending[1]["line"] == "msg2"

        # Queue should be cleared
        pending = await manager.get_pending("session-1")
        assert len(pending) == 0

    asyncio.run(test())


def test_session_queue_limit():
    """Test session queue respects size limit."""
    import asyncio
    from app.api.websocket import ConnectionManager

    manager = ConnectionManager()

    async def test():
        # Queue more than the limit (100)
        for i in range(150):
            await manager.queue_for_session("session-1", {"index": i})

        pending = await manager.get_pending("session-1")
        # Should be capped at 100
        assert len(pending) == 100

    asyncio.run(test())


# --------------------------------------------------------------------------- #
# End-to-end handshake. The app-level require_auth dependency used to ask for a
# Request, which FastAPI cannot supply to a websocket route, so every /ws/chat
# handshake died with `TypeError: require_auth() missing 1 required positional
# argument: 'request'` before the handler was ever reached.
# --------------------------------------------------------------------------- #
@pytest.fixture
def ws_client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.main import app
    from app.config import get_settings

    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "false")
    get_settings.cache_clear()
    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


def test_websocket_handshake_succeeds(ws_client):
    with ws_client.websocket_connect("/ws/chat/t1") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "connected"
    assert frame["thread_id"] == "t1"


def test_websocket_rejects_bad_token(ws_client, monkeypatch):
    """The token in the subprotocol is verified, not merely present."""
    from starlette.websockets import WebSocketDisconnect
    from app.config import get_settings

    monkeypatch.setenv("COPILOT_API_TOKEN", "right-token")
    get_settings.cache_clear()

    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(
            "/ws/chat/t1", subprotocols=["copilot-v1", "auth-wrong-token"]
        ) as ws:
            ws.receive_json()

    with ws_client.websocket_connect(
        "/ws/chat/t1", subprotocols=["copilot-v1", "auth-right-token"]
    ) as ws:
        assert ws.receive_json()["type"] == "connected"
