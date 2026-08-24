/**
 * WebSocket hook for real-time communication with OmniPotent.
 *
 * Provides an alternative to SSE with:
 * - Bidirectional communication
 * - Automatic reconnection with exponential backoff
 * - Session resumption after reconnection
 * - Fallback to SSE if WebSocket fails
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { StreamEvent } from "../types";

import { wsProtocols } from "../lib/authToken";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Convert HTTP URL to WebSocket URL
function getWsUrl(): string {
  const url = new URL(BASE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString().replace(/\/$/, "");
}

export type WsStatus = "connecting" | "connected" | "disconnected" | "error";

export interface WsMessage {
  type: string;
  thread_id?: string;
  message?: string;
  approved?: boolean;
  reason?: string;
}

export interface UseWebSocketOptions {
  /** Thread ID for this connection */
  threadId: string;
  /** Callback when a message is received */
  onMessage?: (event: StreamEvent) => void;
  /** Callback when connection status changes */
  onStatusChange?: (status: WsStatus) => void;
  /** Whether to auto-connect on mount */
  autoConnect?: boolean;
  /** Max reconnection attempts (0 = unlimited) */
  maxReconnectAttempts?: number;
  /** Base delay for reconnection backoff (ms) */
  reconnectBaseDelay?: number;
}

export interface UseWebSocketReturn {
  /** Current connection status */
  status: WsStatus;
  /** Session ID for reconnection */
  sessionId: string | null;
  /** Whether WebSocket is available (server supports it) */
  available: boolean;
  /** Connect to WebSocket */
  connect: () => void;
  /** Disconnect from WebSocket */
  disconnect: () => void;
  /** Send a chat message */
  sendChat: (message: string) => void;
  /** Send an approval response */
  sendApproval: (approved: boolean, reason?: string) => void;
  /** Send a ping */
  ping: () => void;
}

/**
 * Hook for WebSocket communication with the OmniPotent backend.
 *
 * Usage:
 * ```tsx
 * const { status, sendChat, sendApproval, connect, disconnect } = useWebSocket({
 *   threadId: "my-thread",
 *   onMessage: (event) => {
 *     if (event.type === "trace") console.log(event.line);
 *     if (event.type === "done") console.log(event.answer);
 *   },
 * });
 * ```
 */
export function useWebSocket(options: UseWebSocketOptions): UseWebSocketReturn {
  const {
    threadId,
    onMessage,
    onStatusChange,
    autoConnect = false,
    maxReconnectAttempts = 5,
    reconnectBaseDelay = 1000,
  } = options;

  const [status, setStatus] = useState<WsStatus>("disconnected");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [available, setAvailable] = useState(true);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Update status and notify
  const updateStatus = useCallback(
    (newStatus: WsStatus) => {
      setStatus(newStatus);
      onStatusChange?.(newStatus);
    },
    [onStatusChange]
  );

  // Calculate reconnect delay with exponential backoff + jitter
  const getReconnectDelay = useCallback(() => {
    const attempt = reconnectAttempts.current;
    const baseDelay = reconnectBaseDelay * Math.pow(2, attempt);
    const jitter = Math.random() * 0.3 * baseDelay;
    return Math.min(baseDelay + jitter, 30000); // Cap at 30 seconds
  }, [reconnectBaseDelay]);

  // Clean up timers
  const clearTimers = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (pingTimer.current) {
      clearInterval(pingTimer.current);
      pingTimer.current = null;
    }
  }, []);

  // Connect to WebSocket
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    clearTimers();
    updateStatus("connecting");

    // Build WebSocket URL - session_id only (not sensitive)
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    const queryString = params.toString();
    const wsUrl = `${getWsUrl()}/ws/chat/${threadId}${queryString ? `?${queryString}` : ""}`;

    // Use subprotocol for auth token (not logged in access logs)
    // Format: ["copilot-v1", "auth-<token>"] or just ["copilot-v1"] if no token
    const protocols = wsProtocols();

    try {
      const ws = new WebSocket(wsUrl, protocols);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttempts.current = 0;
        updateStatus("connected");

        // Start ping interval (every 25 seconds, server pings at 30)
        pingTimer.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as StreamEvent & { session_id?: string };

          // Handle connection confirmation
          if (data.type === "connected" && data.session_id) {
            setSessionId(data.session_id);
            return;
          }

          // Handle pong
          if (data.type === "pong") {
            return;
          }

          // Handle server ping
          if (data.type === "ping") {
            ws.send(JSON.stringify({ type: "pong" }));
            return;
          }

          // Forward to message handler
          onMessage?.(data as StreamEvent);
        } catch {
          // Ignore non-JSON messages (keep-alives, etc.)
        }
      };

      ws.onerror = () => {
        updateStatus("error");
      };

      ws.onclose = (event) => {
        wsRef.current = null;
        clearTimers();

        // Check if server rejected WebSocket (disabled)
        if (event.code === 4000) {
          setAvailable(false);
          updateStatus("disconnected");
          return;
        }

        // Attempt reconnection if not a clean close
        if (event.code !== 1000 && event.code !== 1001) {
          if (maxReconnectAttempts === 0 || reconnectAttempts.current < maxReconnectAttempts) {
            const delay = getReconnectDelay();
            reconnectAttempts.current++;
            updateStatus("disconnected");
            reconnectTimer.current = setTimeout(connect, delay);
            return;
          }
        }

        updateStatus("disconnected");
      };
    } catch {
      setAvailable(false);
      updateStatus("error");
    }
  }, [
    threadId,
    sessionId,
    onMessage,
    updateStatus,
    clearTimers,
    getReconnectDelay,
    maxReconnectAttempts,
  ]);

  // Disconnect from WebSocket
  const disconnect = useCallback(() => {
    clearTimers();
    reconnectAttempts.current = maxReconnectAttempts; // Prevent reconnection
    if (wsRef.current) {
      wsRef.current.close(1000, "Client disconnect");
      wsRef.current = null;
    }
    updateStatus("disconnected");
  }, [clearTimers, updateStatus, maxReconnectAttempts]);

  // Send a message
  const send = useCallback((msg: WsMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  // Send a chat message
  const sendChat = useCallback(
    (message: string) => {
      send({
        type: "chat",
        thread_id: threadId,
        message,
      });
    },
    [send, threadId]
  );

  // Send an approval response
  const sendApproval = useCallback(
    (approved: boolean, reason = "") => {
      send({
        type: "approval",
        thread_id: threadId,
        approved,
        reason,
      });
    },
    [send, threadId]
  );

  // Send a ping
  const ping = useCallback(() => {
    send({ type: "ping" });
  }, [send]);

  // Auto-connect on mount if enabled
  useEffect(() => {
    if (autoConnect) {
      connect();
    }
    return () => {
      disconnect();
    };
  }, [autoConnect]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reconnect when thread ID changes
  useEffect(() => {
    if (status === "connected" || status === "connecting") {
      disconnect();
      // Clear session ID since it's tied to the old thread
      setSessionId(null);
      reconnectAttempts.current = 0;
      connect();
    }
  }, [threadId]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    status,
    sessionId,
    available,
    connect,
    disconnect,
    sendChat,
    sendApproval,
    ping,
  };
}

/**
 * Check if WebSocket is supported in this environment.
 */
export function isWebSocketSupported(): boolean {
  return typeof WebSocket !== "undefined";
}

/**
 * Probe the server to check if WebSocket is enabled.
 * Returns true if WebSocket is likely available.
 */
export async function probeWebSocket(threadId: string): Promise<boolean> {
  if (!isWebSocketSupported()) return false;

  return new Promise((resolve) => {
    const wsUrl = `${getWsUrl()}/ws/chat/${threadId}`;

    // Use subprotocol for auth token (secure - not in URL/logs)
    const protocols = wsProtocols();

    try {
      const ws = new WebSocket(wsUrl, protocols);
      const timeout = setTimeout(() => {
        ws.close();
        resolve(false);
      }, 5000);

      ws.onopen = () => {
        clearTimeout(timeout);
        ws.close(1000);
        resolve(true);
      };

      ws.onerror = () => {
        clearTimeout(timeout);
        resolve(false);
      };

      ws.onclose = (event) => {
        clearTimeout(timeout);
        // Code 4000 means WebSocket is disabled on server
        resolve(event.code !== 4000);
      };
    } catch {
      resolve(false);
    }
  });
}
