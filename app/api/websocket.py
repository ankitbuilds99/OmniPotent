"""WebSocket endpoint for real-time communication.

An alternative to SSE for clients that prefer bidirectional communication.
Supports the same message types as the SSE streaming endpoints:
  - chat: start an investigation
  - approval: approve/reject a pending action
  - ping/pong: connection health checks

The frontend can use either transport; SSE remains the default for backwards
compatibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app import audit
from app.config import get_settings

log = logging.getLogger("devcopilot.websocket")

router = APIRouter(tags=["websocket"])


# --------------------------------------------------------------------------- #
# Message types
# --------------------------------------------------------------------------- #
@dataclass
class WsMessage:
    """Inbound WebSocket message from client."""
    type: str  # chat | approval | ping
    thread_id: str = ""
    message: str = ""
    approved: bool = False
    reason: str = ""


def parse_message(raw: str) -> WsMessage | None:
    """Parse a JSON message from the client, returning None on invalid input."""
    try:
        data = json.loads(raw)
        return WsMessage(
            type=data.get("type", ""),
            thread_id=data.get("thread_id", ""),
            message=data.get("message", ""),
            approved=data.get("approved", False),
            reason=data.get("reason", ""),
        )
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


# --------------------------------------------------------------------------- #
# Connection Manager
# --------------------------------------------------------------------------- #
@dataclass
class ClientConnection:
    """Tracks a single WebSocket client connection."""
    websocket: WebSocket
    thread_id: str
    connected_at: float = field(default_factory=time.time)
    last_ping: float = field(default_factory=time.time)
    # Session state for reconnection support
    session_id: str = ""
    # Queue of messages sent while client was disconnected (for session resumption)
    pending_messages: list[dict] = field(default_factory=list)
    # Track if this connection has been closed
    closed: bool = False
    # Unique ID for this connection (used as dict key)
    _id: str = field(default_factory=lambda: uuid.uuid4().hex)


class ConnectionManager:
    """Manages WebSocket connections for all clients.

    Handles:
    - Multiple concurrent connections per thread
    - Broadcasting messages to all clients on a thread
    - Connection health via ping/pong
    - Session resumption after reconnection
    """

    def __init__(self):
        # thread_id -> {conn_id -> connection}
        self._connections: dict[str, dict[str, ClientConnection]] = {}
        # session_id -> pending messages for resumption
        self._sessions: dict[str, list[dict]] = {}
        # session_id -> thread_id mapping
        self._session_threads: dict[str, str] = {}
        # Lock for connection management
        self._lock = asyncio.Lock()

    async def connect(
        self, websocket: WebSocket, thread_id: str, session_id: str = ""
    ) -> ClientConnection:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        conn = ClientConnection(
            websocket=websocket,
            thread_id=thread_id,
            session_id=session_id,
        )
        async with self._lock:
            if thread_id not in self._connections:
                self._connections[thread_id] = {}
            self._connections[thread_id][conn._id] = conn
            if session_id:
                self._session_threads[session_id] = thread_id
        log.info("WebSocket connected: thread=%s session=%s", thread_id, session_id or "new")
        return conn

    async def disconnect(self, conn: ClientConnection) -> None:
        """Remove a connection (client disconnected)."""
        if conn.closed:
            return
        conn.closed = True
        async with self._lock:
            conns = self._connections.get(conn.thread_id)
            if conns:
                conns.pop(conn._id, None)
                if not conns:
                    del self._connections[conn.thread_id]
        log.info("WebSocket disconnected: thread=%s", conn.thread_id)

    async def send(self, conn: ClientConnection, message: dict) -> bool:
        """Send a message to a specific connection. Returns False if send failed."""
        if conn.closed:
            return False
        try:
            if conn.websocket.client_state == WebSocketState.CONNECTED:
                await conn.websocket.send_json(message)
                return True
        except Exception:
            log.debug("Failed to send to WebSocket", exc_info=True)
        return False

    async def broadcast(self, thread_id: str, message: dict) -> int:
        """Broadcast a message to all connections on a thread. Returns send count."""
        sent = 0
        async with self._lock:
            conns = self._connections.get(thread_id)
            if not conns:
                return 0
            # Copy to avoid mutation during iteration
            active = list(conns.values())
        for conn in active:
            if await self.send(conn, message):
                sent += 1
        return sent

    async def get_pending(self, session_id: str) -> list[dict]:
        """Get and clear pending messages for a session (resumption)."""
        async with self._lock:
            messages = self._sessions.pop(session_id, [])
            return messages

    async def queue_for_session(self, session_id: str, message: dict) -> None:
        """Queue a message for a disconnected session."""
        async with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = []
            # Bound the queue to prevent memory bloat from abandoned sessions
            if len(self._sessions[session_id]) < 100:
                self._sessions[session_id].append(message)

    def connection_count(self, thread_id: str | None = None) -> int:
        """Count active connections, optionally filtered by thread."""
        if thread_id:
            conns = self._connections.get(thread_id)
            return len(conns) if conns else 0
        return sum(len(c) for c in self._connections.values())


# Global connection manager (shared across all WebSocket endpoints)
manager = ConnectionManager()


# --------------------------------------------------------------------------- #
# WebSocket Endpoint
# --------------------------------------------------------------------------- #
def _extract_token_from_subprotocol(websocket: WebSocket) -> str | None:
    """Extract auth token from WebSocket subprotocol.

    Clients should connect with subprotocols: ["copilot-v1", "auth-<token>"]
    The token is NOT in the URL query string (which would be logged).
    """
    # Get requested subprotocols from headers
    subprotocol_header = websocket.headers.get("sec-websocket-protocol", "")
    if not subprotocol_header:
        return None

    protocols = [p.strip() for p in subprotocol_header.split(",")]
    for proto in protocols:
        if proto.startswith("auth-"):
            return proto[5:]  # Remove "auth-" prefix
    return None


@router.websocket("/ws/chat/{thread_id}")
async def websocket_chat(websocket: WebSocket, thread_id: str):
    """WebSocket endpoint for real-time chat.

    Authentication:
        Token should be passed via subprotocol, NOT query string (security).
        Client connects with: new WebSocket(url, ["copilot-v1", "auth-<token>"])

    Message protocol (JSON):

    Client -> Server:
        {"type": "chat", "thread_id": "...", "message": "..."}
        {"type": "approval", "thread_id": "...", "approved": true/false, "reason": "..."}
        {"type": "ping"}

    Server -> Client:
        {"type": "trace", "thread_id": "...", "line": "..."}
        {"type": "approval_request", "thread_id": "...", "approval_request": {...}, "trace": [...]}
        {"type": "done", "thread_id": "...", "status": "completed", "answer": "...", ...}
        {"type": "error", "thread_id": "...", "message": "..."}
        {"type": "pong", "timestamp": ...}
        {"type": "connected", "thread_id": "...", "session_id": "..."}
    """
    # Check if WebSocket is enabled
    settings = get_settings()
    if not settings.copilot_websocket_enabled:
        await websocket.close(code=4000, reason="WebSocket disabled")
        return

    # Extract auth token from subprotocol (secure - not logged in access logs)
    token = _extract_token_from_subprotocol(websocket)

    # Validate token if required
    if settings.copilot_require_auth and not token:
        await websocket.close(code=4001, reason="Authentication required")
        return

    # TODO: Validate token against tenant auth if multi-tenant
    # For now, presence of token is sufficient (matches SSE behavior)

    # Extract session_id from query params for reconnection support
    query_params = dict(websocket.query_params)
    session_id = query_params.get("session_id", "")

    conn = await manager.connect(websocket, thread_id, session_id)

    # Send connection confirmation with session info
    new_session_id = session_id or f"ws-{thread_id}-{int(time.time() * 1000)}"
    await manager.send(conn, {
        "type": "connected",
        "thread_id": thread_id,
        "session_id": new_session_id,
    })

    # Send any pending messages from a previous session
    if session_id:
        pending = await manager.get_pending(session_id)
        for msg in pending:
            await manager.send(conn, msg)

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(_heartbeat_loop(conn, settings))

    try:
        await _handle_messages(conn, thread_id)
    except WebSocketDisconnect:
        log.debug("Client disconnected: thread=%s", thread_id)
    except Exception:
        log.exception("WebSocket error: thread=%s", thread_id)
    finally:
        heartbeat_task.cancel()
        await manager.disconnect(conn)


async def _heartbeat_loop(conn: ClientConnection, settings: Any) -> None:
    """Send periodic pings to keep the connection alive."""
    interval = settings.copilot_websocket_ping_interval
    while not conn.closed:
        await asyncio.sleep(interval)
        if conn.closed:
            break
        try:
            await manager.send(conn, {"type": "ping", "timestamp": time.time()})
        except Exception:
            break


async def _handle_messages(conn: ClientConnection, thread_id: str) -> None:
    """Process incoming messages from the client."""
    # Import here to avoid circular dependency
    from app.api.main import (
        _AWAITING,
        _check_message,
        _evict,
        _friendly_error,
        _get_session,
        _meter_and_spend,
        _record_slo,
        _running_turn,
        _scoped,
        _stream_payload,
        quota_gate,
    )

    while not conn.closed:
        try:
            raw = await conn.websocket.receive_text()
        except WebSocketDisconnect:
            raise
        except Exception:
            break

        msg = parse_message(raw)
        if not msg:
            await manager.send(conn, {
                "type": "error",
                "thread_id": thread_id,
                "message": "Invalid message format",
            })
            continue

        # Handle different message types
        if msg.type == "ping":
            await manager.send(conn, {"type": "pong", "timestamp": time.time()})
            conn.last_ping = time.time()

        elif msg.type == "chat":
            # Start a new investigation
            await _handle_chat(
                conn, thread_id, msg.message,
                _check_message, _scoped, _running_turn, _get_session, _evict,
                _AWAITING, _friendly_error, _stream_payload, _meter_and_spend,
                _record_slo, quota_gate,
            )

        elif msg.type == "approval":
            # Resume a paused investigation
            await _handle_approval(
                conn, thread_id, msg.approved, msg.reason,
                _scoped, _running_turn, _get_session, _evict,
                _AWAITING, _friendly_error, _stream_payload, _meter_and_spend,
            )

        else:
            await manager.send(conn, {
                "type": "error",
                "thread_id": thread_id,
                "message": f"Unknown message type: {msg.type}",
            })


async def _handle_chat(
    conn: ClientConnection,
    thread_id: str,
    message: str,
    _check_message,
    _scoped,
    _running_turn,
    _get_session,
    _evict,
    _AWAITING,
    _friendly_error,
    _stream_payload,
    _meter_and_spend,
    _record_slo,
    quota_gate,
) -> None:
    """Handle a chat message (start investigation)."""
    from fastapi import HTTPException
    import time as time_mod

    try:
        _check_message(message)
    except HTTPException as e:
        await manager.send(conn, {
            "type": "error",
            "thread_id": thread_id,
            "message": e.detail,
        })
        return

    # Check quota
    try:
        await quota_gate()
    except HTTPException as e:
        await manager.send(conn, {
            "type": "error",
            "thread_id": thread_id,
            "status": "error",
            "message": e.detail,
        })
        return

    tid = _scoped(thread_id)
    existed = tid in _get_session.__self__._SESSIONS if hasattr(_get_session, '__self__') else False

    # Import _SESSIONS directly
    from app.api.main import _SESSIONS
    existed = tid in _SESSIONS

    try:
        async with _running_turn(tid):
            session = await _get_session(tid)
            if await session.pending_interrupt():
                _AWAITING.add(tid)
                await manager.send(conn, {
                    "type": "error",
                    "thread_id": thread_id,
                    "status": "error",
                    "message": "This thread is paused awaiting approval.",
                })
                return

            _AWAITING.discard(tid)
            _t0 = time_mod.monotonic()

            async for ev in session.ask_stream(message):
                t = ev.get("type")
                if t == "approval":
                    _AWAITING.add(tid)
                elif t == "done":
                    _AWAITING.discard(tid)
                    await _meter_and_spend(ev.get("tokens_used", 0))
                    _record_slo(
                        type("Result", (), {
                            "report": ev.get("report"),
                            "final_text": ev.get("final_text", ""),
                            "tokens_used": ev.get("tokens_used", 0),
                        })(),
                        time_mod.monotonic() - _t0,
                    )

                # Send event to client
                payload = _stream_payload(thread_id, ev)
                await manager.send(conn, payload)

    except Exception as exc:
        log.exception("WebSocket chat failed: thread=%s", tid)
        if not existed:
            await _evict(tid)
        await manager.send(conn, {
            "type": "error",
            "thread_id": thread_id,
            "status": "error",
            "message": _friendly_error(exc),
        })


async def _handle_approval(
    conn: ClientConnection,
    thread_id: str,
    approved: bool,
    reason: str,
    _scoped,
    _running_turn,
    _get_session,
    _evict,
    _AWAITING,
    _friendly_error,
    _stream_payload,
    _meter_and_spend,
) -> None:
    """Handle an approval response (resume investigation)."""
    from app.api.main import _SESSIONS

    tid = _scoped(thread_id)
    existed = tid in _SESSIONS

    try:
        async with _running_turn(tid):
            session = await _get_session(tid)
            if not await session.pending_interrupt():
                if not existed:
                    await _evict(tid)
                await manager.send(conn, {
                    "type": "error",
                    "thread_id": thread_id,
                    "status": "error",
                    "message": f"No investigation awaiting approval for thread '{thread_id}'",
                })
                return

            audit.record("approval.decided", thread=thread_id, approved=approved, via="websocket")

            async for ev in session.resume_stream(approved=approved, reason=reason):
                t = ev.get("type")
                if t == "approval":
                    _AWAITING.add(tid)
                elif t == "done":
                    _AWAITING.discard(tid)
                    await _meter_and_spend(ev.get("tokens_used", 0))

                payload = _stream_payload(thread_id, ev)
                await manager.send(conn, payload)

    except Exception as exc:
        log.exception("WebSocket approval failed: thread=%s", tid)
        await manager.send(conn, {
            "type": "error",
            "thread_id": thread_id,
            "status": "error",
            "message": _friendly_error(exc),
        })


# --------------------------------------------------------------------------- #
# Health / Stats
# --------------------------------------------------------------------------- #
def get_connection_stats() -> dict:
    """Return WebSocket connection statistics."""
    return {
        "total_connections": manager.connection_count(),
        "enabled": get_settings().copilot_websocket_enabled,
    }
