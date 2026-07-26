import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "../api";
import type { StreamEvent, Turn, WsStatus } from "../types";
import { useWebSocket, isWebSocketSupported, probeWebSocket } from "./useWebSocket";

const newId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

const STORE_KEY = "copilot-conversation-v1";
const WS_PREFERENCE_KEY = "copilot-websocket-enabled";

interface Persisted {
  threadId: string;
  turns: Turn[];
}

/** Restore the conversation from localStorage so a reload doesn't orphan the
 *  backend thread (its checkpointer state — incl. a pending approval — survives,
 *  and the API reconstructs the session on demand). A turn left mid-stream by a
 *  reload is downgraded to a recoverable note; a pending approval is kept intact. */
function loadPersisted(): Persisted {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Persisted;
      if (p && typeof p.threadId === "string" && Array.isArray(p.turns)) {
        const turns = p.turns.map((t) =>
          t.status === "thinking"
            ? { ...t, status: "error" as const, text: t.text || "Interrupted by a page reload — please re-send." }
            : t
        );
        return { threadId: p.threadId, turns };
      }
    }
  } catch {
    /* corrupt/unavailable storage — fall through to a fresh conversation */
  }
  return { threadId: `web-${newId()}`, turns: [] };
}

/** Load WebSocket preference from localStorage. */
function loadWsPreference(): boolean {
  try {
    const raw = localStorage.getItem(WS_PREFERENCE_KEY);
    // Default to true if not set
    return raw === null ? true : raw === "true";
  } catch {
    return true;
  }
}

/** Save WebSocket preference to localStorage. */
function saveWsPreference(enabled: boolean): void {
  try {
    localStorage.setItem(WS_PREFERENCE_KEY, String(enabled));
  } catch {
    /* storage full / disabled — non-fatal */
  }
}

/**
 * Owns the whole conversation: a stable thread_id (so the backend checkpointer
 * can pause/resume across requests), the list of turns, the approval flow, and
 * cancellation. Trace + answer arrive LIVE over SSE or WebSocket (with auto-fallback).
 * State is persisted to localStorage so it survives reloads and navigating home.
 */
export function useCopilot() {
  const initial = useRef(loadPersisted()).current;
  const [threadId, setThreadId] = useState(initial.threadId);
  const [turns, setTurns] = useState<Turn[]>(initial.turns);
  const [busy, setBusy] = useState(false);
  // Controls the in-flight SSE fetch so the Stop button can cancel it.
  const abortRef = useRef<AbortController | null>(null);

  // WebSocket state
  const [useWs, setUseWs] = useState(loadWsPreference);
  const [wsAvailable, setWsAvailable] = useState<boolean | null>(null);
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
  const wsMessageQueue = useRef<{ type: "chat" | "approval"; message?: string; approved?: boolean; reason?: string }[]>([]);
  const currentAssistantId = useRef<string | null>(null);

  // Probe WebSocket availability on mount
  useEffect(() => {
    if (isWebSocketSupported()) {
      probeWebSocket(threadId).then(setWsAvailable);
    } else {
      setWsAvailable(false);
    }
  }, [threadId]);

  // Persist on every change (cheap; entries are small). A turn still "thinking"
  // is sanitized on the next load, so an interrupted stream restores cleanly.
  useEffect(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({ threadId, turns }));
    } catch {
      /* storage full / disabled — non-fatal */
    }
  }, [threadId, turns]);

  const patch = useCallback((id: string, update: Partial<Turn>) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...update } : t)));
  }, []);

  // Apply one streamed event to the given assistant turn.
  const applyEvent = useCallback(
    (assistantId: string, ev: StreamEvent) => {
      if (ev.type === "trace" && ev.line) {
        setTurns((prev) =>
          prev.map((t) =>
            t.id === assistantId ? { ...t, trace: [...t.trace, ev.line as string] } : t
          )
        );
      } else if (ev.type === "approval") {
        patch(assistantId, {
          status: "awaiting_approval",
          text: ev.approval_request?.message ?? "Awaiting approval...",
          approval: ev.approval_request ?? null,
          trace: ev.trace ?? [],
        });
        setBusy(false);
      } else if (ev.type === "done") {
        patch(assistantId, {
          status: "completed",
          text: ev.answer ?? "",
          trace: ev.trace ?? [],
          approval: null,
          report: ev.report ?? null,
          tokensUsed: ev.tokens_used ?? 0,
        });
        setBusy(false);
      } else if (ev.type === "error") {
        patch(assistantId, { status: "error", text: ev.answer ?? ev.message ?? "Something went wrong." });
        setBusy(false);
      }
    },
    [patch]
  );

  // WebSocket message handler
  const handleWsMessage = useCallback(
    (ev: StreamEvent) => {
      const assistantId = currentAssistantId.current;
      if (assistantId) {
        applyEvent(assistantId, ev);
      }
    },
    [applyEvent]
  );

  // WebSocket connection
  const {
    status: wsConnectionStatus,
    available: wsServerAvailable,
    connect: wsConnect,
    disconnect: wsDisconnect,
    sendChat: wsSendChat,
    sendApproval: wsSendApproval,
  } = useWebSocket({
    threadId,
    onMessage: handleWsMessage,
    onStatusChange: setWsStatus,
    autoConnect: false,
  });

  // Update wsAvailable from hook
  useEffect(() => {
    if (!wsServerAvailable) {
      setWsAvailable(false);
    }
  }, [wsServerAvailable]);

  const isAbort = (e: unknown) =>
    e instanceof DOMException ? e.name === "AbortError" : (e as { name?: string })?.name === "AbortError";

  /** Send using SSE (fallback). */
  const sendViaSse = useCallback(
    async (message: string, assistantTurn: Turn) => {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        await api.chatStream(threadId, message, (ev) => applyEvent(assistantTurn.id, ev), ctrl.signal);
      } catch (e) {
        if (isAbort(e)) {
          // Keep the trace so far; mark the turn as stopped rather than errored.
          setTurns((prev) =>
            prev.map((t) =>
              t.id === assistantTurn.id && t.status === "thinking"
                ? { ...t, status: "completed", text: t.text || "_Investigation stopped._" }
                : t
            )
          );
        } else {
          patch(assistantTurn.id, {
            status: "error",
            text: e instanceof Error ? e.message : String(e),
          });
        }
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [threadId, applyEvent, patch]
  );

  /** Send using WebSocket. */
  const sendViaWs = useCallback(
    async (message: string, assistantTurn: Turn) => {
      currentAssistantId.current = assistantTurn.id;

      // Ensure WebSocket is connected
      if (wsConnectionStatus !== "connected") {
        wsConnect();
        // Queue the message to be sent once connected
        wsMessageQueue.current.push({ type: "chat", message });
        return;
      }

      wsSendChat(message);
    },
    [wsConnectionStatus, wsConnect, wsSendChat]
  );

  // Process queued messages when WebSocket connects
  useEffect(() => {
    if (wsConnectionStatus === "connected" && wsMessageQueue.current.length > 0) {
      const queue = wsMessageQueue.current;
      wsMessageQueue.current = [];
      for (const msg of queue) {
        if (msg.type === "chat" && msg.message) {
          wsSendChat(msg.message);
        } else if (msg.type === "approval" && msg.approved !== undefined) {
          wsSendApproval(msg.approved, msg.reason ?? "");
        }
      }
    }
  }, [wsConnectionStatus, wsSendChat, wsSendApproval]);

  /** Send a new question and stream the investigation. */
  const send = useCallback(
    async (message: string) => {
      const userTurn: Turn = {
        id: newId(),
        role: "user",
        text: message,
        trace: [],
        status: "completed",
        approval: null,
      };
      const assistantTurn: Turn = {
        id: newId(),
        role: "assistant",
        text: "",
        trace: [],
        status: "thinking",
        approval: null,
      };
      setTurns((prev) => [...prev, userTurn, assistantTurn]);
      setBusy(true);

      // Use WebSocket if available and enabled, otherwise fallback to SSE
      const shouldUseWs = useWs && wsAvailable !== false && isWebSocketSupported();

      if (shouldUseWs) {
        await sendViaWs(message, assistantTurn);
      } else {
        await sendViaSse(message, assistantTurn);
      }
    },
    [useWs, wsAvailable, sendViaWs, sendViaSse]
  );

  /** Approve or reject via SSE (fallback). */
  const respondViaSse = useCallback(
    async (turnId: string, approved: boolean, reason: string) => {
      patch(turnId, { status: "thinking" });
      setBusy(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        await api.approveStream(threadId, approved, reason, (ev) => applyEvent(turnId, ev), ctrl.signal);
      } catch {
        // Restore the approval gate so the user can review/retry — no context lost.
        // (Covers both a real error and a user-cancelled resume.)
        patch(turnId, { status: "awaiting_approval" });
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [threadId, applyEvent, patch]
  );

  /** Approve or reject via WebSocket. */
  const respondViaWs = useCallback(
    async (turnId: string, approved: boolean, reason: string) => {
      patch(turnId, { status: "thinking" });
      setBusy(true);
      currentAssistantId.current = turnId;

      // Ensure WebSocket is connected
      if (wsConnectionStatus !== "connected") {
        wsConnect();
        wsMessageQueue.current.push({ type: "approval", approved, reason });
        return;
      }

      wsSendApproval(approved, reason);
    },
    [patch, wsConnectionStatus, wsConnect, wsSendApproval]
  );

  /** Approve or reject the pending write action on a given turn (streamed). */
  const respond = useCallback(
    async (turnId: string, approved: boolean, reason = "") => {
      // Use WebSocket if available and enabled, otherwise fallback to SSE
      const shouldUseWs = useWs && wsAvailable !== false && isWebSocketSupported();

      if (shouldUseWs) {
        await respondViaWs(turnId, approved, reason);
      } else {
        await respondViaSse(turnId, approved, reason);
      }
    },
    [useWs, wsAvailable, respondViaWs, respondViaSse]
  );

  /** Record a thumbs up/down on an investigation (feeds the eval/learning loop). */
  const sendFeedback = useCallback(
    (rating: "up" | "down", question = "") => {
      void api.submitFeedback(threadId, rating, "", question).catch(() => {
        /* feedback is best-effort — never block the UI on it */
      });
    },
    [threadId]
  );

  /** Cancel the in-flight investigation (Stop button). */
  const stop = useCallback(() => {
    abortRef.current?.abort();
    // For WebSocket, we disconnect to stop the stream
    if (useWs && wsConnectionStatus === "connected") {
      wsDisconnect();
    }
    setBusy(false);
  }, [useWs, wsConnectionStatus, wsDisconnect]);

  /** Start a fresh conversation: cancel anything running, clear turns, new thread. */
  const newConversation = useCallback(() => {
    abortRef.current?.abort();
    wsDisconnect();
    setTurns([]);
    setThreadId(`web-${newId()}`);
    currentAssistantId.current = null;
  }, [wsDisconnect]);

  /** Toggle WebSocket mode on/off. */
  const toggleWebSocket = useCallback((enabled: boolean) => {
    setUseWs(enabled);
    saveWsPreference(enabled);
    if (!enabled) {
      wsDisconnect();
    }
  }, [wsDisconnect]);

  // Block new input while a turn is paused for approval (the thread is mid-graph).
  const awaitingApproval = turns.some((t) => t.status === "awaiting_approval");

  return {
    turns,
    busy,
    awaitingApproval,
    send,
    respond,
    stop,
    newConversation,
    sendFeedback,
    // WebSocket-related state
    wsEnabled: useWs,
    wsAvailable: wsAvailable ?? false,
    wsStatus,
    toggleWebSocket,
  };
}
