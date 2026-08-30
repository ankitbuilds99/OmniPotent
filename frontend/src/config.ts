/**
 * The API's absolute base URL, for the places that need a real href.
 *
 * Every module used to re-derive this itself, and they disagreed: some wrote
 * `import.meta.env.VITE_API_URL ?? "http://localhost:8000"`, others `|| ""`.
 * `??` only falls back on null/undefined, so the Dockerfile's
 * `ENV VITE_API_URL=""` (deliberately empty => same-origin) survived as an empty
 * string, and `new URL("")` throws `TypeError: Invalid URL` — which killed the
 * WebSocket hook at module load in every production image.
 *
 * Requests do not come through here. `lib/apiClient` owns those, and deliberately
 * keeps its base *relative* ("" => same-origin), which is what its 22 callers and
 * the Vite dev proxy expect; `lib/authToken` owns the credential they carry. This
 * module is for what a relative base cannot do: an `<a href>` into the backend's
 * own pages (/docs, /redoc) has to be somewhere a new tab can actually go.
 *
 * It exported a `wsUrl`, an `API_TOKEN` and an `authHeaders` too, and all three
 * were dead. `lib/apiClient.wsUrl` is what the WebSocket hook calls. And this
 * module's `authHeaders` read a token fixed at build time, so a user who signed
 * in at run time still sent no credential and every request came back 401 —
 * which is the precise bug `lib/authToken` was written to fix. Keeping the losing
 * half of a fixed bug in the tree, under a docstring announcing itself as the
 * single source of truth, is how it gets imported again by mistake.
 */

/** Local dev fallback: the Vite dev server serves only the SPA, API is on 8000. */
const DEV_API_URL = "http://localhost:8000";

/**
 * Read an env value, treating undefined, null and blank/whitespace-only strings
 * as "unset". An operator who exports `VITE_API_URL=` means "I did not set this",
 * never "the base URL is the empty string" — the `??` form got that wrong.
 */
function envOr(value: string | undefined | null, fallback: string): string {
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
 * `new URL("/api")` throws exactly like `new URL("")` did. Resolving it here is
 * what lets this module promise an absolute answer, which is the whole point of
 * it: a link built from a relative base is not a link a new tab can follow.
 * Absolute inputs pass through unchanged. Resolution is against the origin, not
 * the current href, so the answer does not depend on which SPA route is open.
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

/** Absolute base URL for links into the API. Never empty, never trailing-slashed. */
export const API_BASE_URL: string = stripTrailingSlash(
  toAbsolute(envOr(import.meta.env.VITE_API_URL, defaultApiBaseUrl()))
);

/**
 * Where "contact us" mailto links point.
 *
 * Pricing and Billing used to hardcode `sales@omnipotent.ai` and
 * `support@omnipotent.ai` — omnipotent.ai is not a domain this project owns,
 * so mail to those addresses went to nobody (or to whoever does own it). The
 * footer always used the maintainer's real address; that is now the shared
 * default, overridable per deployment via VITE_CONTACT_EMAIL.
 */
export const CONTACT_EMAIL: string = envOr(
  import.meta.env.VITE_CONTACT_EMAIL,
  "ankit25bcs10610@gmail.com"
);
