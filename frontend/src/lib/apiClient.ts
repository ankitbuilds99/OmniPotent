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

/** Why a response failed, in words an operator can act on.
 *
 *  Pages that want to report a failed action need something to put on screen,
 *  and `res.statusText` is empty over HTTP/2. FastAPI puts a plain string in
 *  `detail` for an HTTPException but a list of field objects there for a 422,
 *  so rendering `detail` directly gives "[object Object]" on precisely the
 *  failure that most needs explaining — a rejected field. Both shapes are
 *  flattened here, and the status code is always kept as the fallback. */
export async function describeFailure(res: Response): Promise<string> {
  let body = "";
  try {
    body = await res.text();
  } catch {
    // A body that cannot be read is not worth reporting over the status code.
  }
  if (body) {
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      const detail = parsed?.detail;
      if (typeof detail === "string" && detail.trim()) {
        return `${detail.trim()} (HTTP ${res.status})`;
      }
      if (Array.isArray(detail) && detail.length) {
        const fields = detail
          .map(item => {
            const entry = item as { loc?: unknown[]; msg?: unknown };
            const where = Array.isArray(entry.loc) ? entry.loc.join(".") : "";
            const why = typeof entry.msg === "string" ? entry.msg : "invalid";
            return where ? `${where}: ${why}` : why;
          })
          .join("; ");
        return `${fields} (HTTP ${res.status})`;
      }
    } catch {
      // Not JSON — a proxy error page or a stack trace. The first line of it is
      // still more use than the bare status, so fall through to the raw text.
    }
    const firstLine = body.split("\n")[0].trim();
    if (firstLine) return `${firstLine.slice(0, 200)} (HTTP ${res.status})`;
  }
  return `HTTP ${res.status}`;
}

/** The WebSocket URL for a backend path, over ws:// or wss:// to match the page. */
export function wsUrl(path: string): string {
  if (API_BASE) return API_BASE.replace(/^http/, "ws") + path;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${path}`;
}
