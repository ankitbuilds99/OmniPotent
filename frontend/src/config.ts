/**
 * Single source of truth for the frontend's build-time configuration.
 *
 * Every module used to re-derive the API base URL itself, and they disagreed:
 * some wrote `import.meta.env.VITE_API_URL ?? "http://localhost:8000"`, others
 * `|| ""`. `??` only falls back on null/undefined, so the Dockerfile's
 * `ENV VITE_API_URL=""` (deliberately empty => same-origin) survived as an empty
 * string, and `new URL("")` throws `TypeError: Invalid URL` — which killed the
 * WebSocket hook at module load in every production image. Resolve it once, here.
 */

/** Local dev fallback: the Vite dev server serves only the SPA, API is on 8000. */
const DEV_API_URL = "http://localhost:8000";

/**
 * Read an env value, treating undefined, null and blank/whitespace-only strings
 * as "unset". An operator who exports `VITE_API_URL=` means "I did not set this",
 * never "the base URL is the empty string" — the `??` form got that wrong.
 */
export function envOr(value: string | undefined | null, fallback: string): string {
  if (value === undefined || value === null) return fallback;
  const trimmed = value.trim();
  return trimmed === "" ? fallback : trimmed;
}

/** Drop trailing slashes so `${API_BASE_URL}${path}` never yields a double slash. */
function stripTrailingSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/**
 * Where the API lives when VITE_API_URL is unset.
 *
 * In a production build the container serves the API *and* these static assets
 * from one host, so the page's own origin is the API. Defaulting to
 * `localhost:8000` in a deployed browser would point at the visitor's own
 * machine, which is both broken and a privacy footgun.
 */
function defaultApiBaseUrl(): string {
  if (import.meta.env.DEV) return DEV_API_URL;
  if (typeof window !== "undefined" && window.location?.origin) {
    return stripTrailingSlash(window.location.origin);
  }
  // No DOM (SSR, node-based tooling) and no explicit config: dev default is the
  // only sane guess left, and it fails loudly against a real deployment.
  return DEV_API_URL;
}

/**
 * Resolve a path-only base against the page origin.
 *
 * The Dockerfile frames the empty value as "relative, same-origin", so an operator
 * deploying under a prefix will reasonably write `VITE_API_URL=/api` — and
 * `new URL("/api")` throws exactly like `new URL("")` did. Resolving here keeps
 * that spelling working for `wsUrl`, whose whole job is to never throw at module
 * load. Absolute inputs pass through unchanged. Resolution is against the origin,
 * not the current href, so the answer does not depend on which SPA route is open.
 */
function toAbsolute(url: string): string {
  const origin = typeof window !== "undefined" ? window.location?.origin : undefined;
  try {
    return new URL(url, origin).toString();
  } catch {
    // Relative value with no DOM to resolve it against (SSR, node tooling). Hand
    // back a valid absolute URL rather than one that detonates in a later caller.
    return DEV_API_URL;
  }
}

/** Absolute base URL for every API call. Never empty, never trailing-slashed. */
export const API_BASE_URL: string = stripTrailingSlash(
  toAbsolute(envOr(import.meta.env.VITE_API_URL, defaultApiBaseUrl()))
);

/** Optional bearer token — set VITE_API_TOKEN when the backend has COPILOT_API_TOKEN. */
export const API_TOKEN: string = envOr(import.meta.env.VITE_API_TOKEN, "");

/** `Authorization` header when a token is configured, otherwise just `extra`. */
export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return API_TOKEN ? { ...extra, Authorization: `Bearer ${API_TOKEN}` } : { ...extra };
}

/**
 * WebSocket URL for `path` (e.g. `/ws/chat/abc`), with the http->ws scheme swap.
 *
 * `API_BASE_URL` is guaranteed absolute above, so `new URL` cannot throw here —
 * that guarantee is the whole point of this module.
 */
export function wsUrl(path = ""): string {
  const url = new URL(API_BASE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const base = stripTrailingSlash(url.toString());
  if (!path) return base;
  return `${base}${path.startsWith("/") ? "" : "/"}${path}`;
}
