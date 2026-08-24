// One fetch wrapper for every backend call.
//
// The page modules each called bare `fetch` with no Authorization header. The
// backend applies `require_auth` to all ~291 routes, so on any deployment with
// COPILOT_API_TOKEN set — i.e. every real one — those calls returned 401 and
// the pages rendered empty. They also each defined their own API_BASE, which
// drifted: some read VITE_API_URL, the committed bundle had localhost:8000
// baked in.
//
// `apiFetch` resolves the base and attaches the current credential per call, so
// signing in takes effect without a reload.

import { authHeaders } from "./authToken";

/** Backend origin. Empty string means same-origin, which is what the Vite dev
 *  proxy and the production single-origin deployment both want. */
export const API_BASE: string = import.meta.env.VITE_API_URL || "";

/** Absolute URL for a backend path. Passes through anything already absolute. */
export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path}`;
}

/** `fetch`, with the API base and the Authorization header applied. */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const extra = (init.headers ?? {}) as Record<string, string>;
  return fetch(apiUrl(path), { ...init, headers: authHeaders(extra) });
}

/** The WebSocket URL for a backend path, over ws:// or wss:// to match the page. */
export function wsUrl(path: string): string {
  if (API_BASE) return API_BASE.replace(/^http/, "ws") + path;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${path}`;
}
