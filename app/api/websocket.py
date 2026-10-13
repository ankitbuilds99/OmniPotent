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
from app import investigation_history

log = logging.getLogger("omnipotent.websocket")

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


# The subprotocol the browser client offers on every connection
# (frontend/src/lib/authToken.ts::wsProtocols).
WS_SUBPROTOCOL = "copilot-v1"


def negotiate_subprotocol(websocket: WebSocket) -> str | None:
    """Pick the subprotocol to echo back in the handshake response.

    RFC 6455 requires the server to answer a non-empty Sec-WebSocket-Protocol
    offer with exactly one of the offered values. Browsers enforce this: Chrome
    fails the connection with "Sent non-empty 'Sec-WebSocket-Protocol' header
    but no response was received" when the server accepts without one. The SPA
    always offers ["copilot-v1"] (plus "auth-<token>" when signed in), so
    accepting with no subprotocol made every real WebSocket chat unusable even
    though Starlette's TestClient — which does not enforce negotiation — was
    happy.

    The `auth-<token>` entry is a credential and is never echoed back; it would
    then appear in response headers and proxy logs.
    """
    offered = [
        p.strip()
        for p in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if p.strip()
    ]
    if not offered:
        return None
    if WS_SUBPROTOCOL in offered:
        return WS_SUBPROTOCOL
    for proto in offered:
        if not proto.startswith("auth-"):
            return proto
    # Only a credential was offered: there is nothing safe to echo, so the
    # client will (correctly) fail the handshake.
    log.warning("WebSocket client offered no usable subprotocol; handshake will fail")
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
        """Accept a new WebSocket connection.

        The accept must answer the client's Sec-WebSocket-Protocol offer (see
        `negotiate_subprotocol`), or every browser client fails the handshake.
        """
        await websocket.accept(subprotocol=negotiate_subprotocol(websocket))
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
        """Send a message to a specific connection. Returns False if send failed.

        A failed send on a session-bearing connection is queued for resumption.
        Nothing used to fill that buffer, so `get_pending` always returned []:
        the client reconnected with `?session_id=` exactly as
        frontend/src/hooks/useWebSocket.ts intends, and every event emitted while
        it was away — the whole investigation, in a long one — was gone.
        """
        if conn.closed:
            await self._queue_missed(conn, message)
            return False
        try:
            if conn.websocket.client_state == WebSocketState.CONNECTED:
                await conn.websocket.send_json(message)
                return True
        except Exception:
            log.debug("Failed to send to WebSocket", exc_info=True)
        await self._queue_missed(conn, message)
        return False

    async def _queue_missed(self, conn: ClientConnection, message: dict) -> None:
        """Buffer a message the client did not receive, if it can resume."""
        if conn.session_id:
            await self.queue_for_session(conn.session_id, message)

    async def broadcast(self, thread_id: str, message: dict) -> int:
        """Broadcast a message to all connections on a thread. Returns send count.

        When nobody is connected the message is still buffered for every session
        known to belong to this thread — the common case is a client that dropped
        mid-investigation, where "no connections" is precisely when resumption
        has to work.
        """
        sent = 0
        async with self._lock:
            conns = self._connections.get(thread_id)
            active = list(conns.values()) if conns else []
            orphaned = (
                [sid for sid, tid in self._session_threads.items() if tid == thread_id]
                if not active
                else []
            )
        for session_id in orphaned:
            await self.queue_for_session(session_id, message)
        for conn in active:
            if await self.send(conn, message):
                sent += 1
        return sent

    async def get_pending(self, session_id: str) -> list[dict]:
        """Get and clear pending messages for a session (resumption)."""
        async with self._lock:
            messages = self._sessions.pop(session_id, [])
            return messages

    # Buffers are only useful until the client gives up reconnecting, and this is
    # now written to on every failed send, so both dimensions are bounded: at
    # most _MAX_QUEUED_MESSAGES per session and _MAX_BUFFERED_SESSIONS sessions.
    _MAX_QUEUED_MESSAGES = 100
    _MAX_BUFFERED_SESSIONS = 500

    async def queue_for_session(self, session_id: str, message: dict) -> None:
        """Queue a message for a disconnected session."""
        async with self._lock:
            if session_id not in self._sessions:
                if len(self._sessions) >= self._MAX_BUFFERED_SESSIONS:
                    # Drop the oldest abandoned session rather than the live one.
                    oldest = next(iter(self._sessions))
                    del self._sessions[oldest]
                    self._session_threads.pop(oldest, None)
                self._sessions[session_id] = []
            if len(self._sessions[session_id]) < self._MAX_QUEUED_MESSAGES:
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

    # The token itself was already verified by the app-level require_auth gate,
    # which reads the same `auth-<token>` subprotocol and closes the handshake with
    # 1008 on a bad credential. This check only covers the copilot_require_auth
    # flag, which demands a token even in deployments where auth is otherwise off.
    token = _extract_token_from_subprotocol(websocket)
    if settings.copilot_require_auth and not token:
        await websocket.close(code=4001, reason="Authentication required")
        return

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

        elif msg.type == "pong":
            # The browser replies to server heartbeats with a pong. It is a
            # valid heartbeat acknowledgement, not an application message.
            # Previously it fell through to the generic "Unknown message type"
            # response and appeared as an error after otherwise successful runs.
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


async def _authorize(conn: ClientConnection, thread_id: str, action: str) -> bool:
    """Apply the RBAC matrix to one inbound message, as the HTTP twin does.

    `/chat/stream` and `/approve/stream` are declared with
    `Depends(require_perm("run_investigation"))` and
    `Depends(require_perm("approve_action"))`, but a `@router.websocket` route
    cannot carry route dependencies, so this transport enforced no role at all.
    The handshake's `require_auth` resolves the tenant from the `auth-<token>`
    subprotocol and stops there: a read-only `viewer` key that the HTTP routes
    answered with 403 could open the socket and both start investigations and
    approve the agent's pending writes, with the approval landing in the trail
    as a legitimate `approval.decided` row.

    Calling `require_perm` itself, rather than re-deriving the matrix here,
    guarantees that one credential gets the same verdict whichever transport it
    arrives on — the client is handed the HTTP 403 detail verbatim in the
    error frame this handler already speaks. Returns True when the caller may
    proceed; when it returns False the denial frame has already been sent.

    The check is per message rather than per handshake because a single
    connection carries both actions and they are separate permissions; opening
    the socket stays available to any authenticated role, since watching an
    investigation is a read.
    """
    from fastapi import HTTPException

    from app.api.main import require_perm

    try:
        await require_perm(action)()
    except HTTPException as exc:
        log.warning(
            "WebSocket %s denied: thread=%s: %s", action, thread_id, exc.detail
        )
        await manager.send(conn, {
            "type": "error",
            "thread_id": thread_id,
            "message": exc.detail,
        })
        return False
    return True


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

    if not await _authorize(conn, thread_id, "run_investigation"):
        return

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

    # Imported here rather than threaded through the argument list, which is
    # already long; the module-level import is what would be circular, not this.
    from app.api.main import _SESSIONS, _record_slo_failure
    existed = tid in _SESSIONS

    _t0 = time_mod.monotonic()
    session = None
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

            async for ev in session.ask_stream(message):
                t = ev.get("type")
                if t == "approval":
                    _AWAITING.add(tid)
                elif t == "done":
                    _AWAITING.discard(tid)
                    await _meter_and_spend(ev.get("tokens_used", 0))
                    # A thread is the agent conversation/checkpointer key, but
                    # each completed question is a separate investigation in
                    # History and Analytics. Do not let the stable browser
                    # thread id upsert every question into one row.
                    history_id = f"{thread_id}:{uuid.uuid4().hex[:12]}"
                    # The browser's live console uses WebSocket transport. Keep
                    # it consistent with the SSE path: a completed interactive
                    # investigation must be persisted before the done event is
                    # delivered to the client.
                    report = ev.get("report") or {}
                    await investigation_history.record(
                        history_id,
                        message,
                        status="completed",
                        summary=report.get("root_cause") or ev.get("final_text") or None,
                        report=report or None,
                        latency_ms=int((time_mod.monotonic() - _t0) * 1000),
                    )
                    # Analytics reads the dashboard metrics projection, which
                    # is separate from the narrative history row above.
                    from app.api.main import _record_dashboard_history_from_stream
                    await _record_dashboard_history_from_stream(
                        history_id,
                        ev,
                        time_mod.monotonic() - _t0,
                    )
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
        _record_slo_failure(time_mod.monotonic() - _t0)
        # A failed agent turn may leave an assistant tool call without a matching
        # ToolMessage in the checkpoint. Reset the failed thread so a retry starts
        # with valid provider history instead of another generic 400 error.
        if session is not None:
            try:
                await session.reset_after_failure()
            except Exception:  # noqa: BLE001 — cleanup must not mask the error
                log.warning("could not reset failed websocket thread=%s", tid, exc_info=True)
        if existed or session is not None:
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
    import time as time_mod

    from app.api.main import _SESSIONS, _record_slo_failure, _record_slo_fields

    # Ahead of the audit row as well as the resume: a decision the role may not
    # make must never be recorded as one that was made.
    if not await _authorize(conn, thread_id, "approve_action"):
        return

    tid = _scoped(thread_id)
    existed = tid in _SESSIONS

    # The pause itself is never sampled, so an investigation that went through an
    # approval gate was missing from /metrics/slo entirely unless it is counted
    # here. Latency covers the continuation only — not the human's thinking time.
    _t0 = time_mod.monotonic()
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
                    _record_slo_fields(
                        ev.get("report"),
                        ev.get("final_text", ""),
                        ev.get("tokens_used", 0),
                        time_mod.monotonic() - _t0,
                    )

                payload = _stream_payload(thread_id, ev)
                await manager.send(conn, payload)

    except Exception as exc:
        log.exception("WebSocket approval failed: thread=%s", tid)
        _record_slo_failure(time_mod.monotonic() - _t0)
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
