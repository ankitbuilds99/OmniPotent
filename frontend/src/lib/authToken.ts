// The single source of truth for the bearer token every API call carries.
//
// Each client used to read `import.meta.env.VITE_API_TOKEN` directly. That is
// baked in at build time, so a user who signed in at run time still sent no
// credential and every request came back 401 — the app could not authenticate
// itself no matter how the user logged in.

const SESSION_TOKEN_KEY = "session_token"; // OAuth / Firebase session JWT
const SESSION_SOURCE_KEY = "session_token_source"; // which flow wrote it
const API_KEY_KEY = "api_key"; // tenant API key (dcp_…) from self-serve signup

/** Which login flow produced the stored session token. Two flows write to the
 *  same slot, and one of them must be able to retract its own credential
 *  without signing out a session the other established. */
export type TokenSource = "firebase" | "oauth";

// Build-time fallback, still useful for local dev against a backend with
// COPILOT_API_TOKEN set and no login flow in the way.
const BUILD_TIME_TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

function read(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    // Private-mode Safari and sandboxed iframes throw on localStorage access.
    return "";
  }
}

function write(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Nothing to do — the token just won't survive a reload.
  }
}

/** The credential to send, or "" when the user is not authenticated.
 *  A session from logging in wins over a pasted key, which wins over the
 *  build-time token. */
export function getAuthToken(): string {
  return read(SESSION_TOKEN_KEY) || read(API_KEY_KEY) || BUILD_TIME_TOKEN;
}

/** Persist the session token issued by an OAuth callback or Firebase login. */
export function setSessionToken(
  token: string,
  source: TokenSource = "oauth"
): void {
  write(SESSION_TOKEN_KEY, token);
  write(SESSION_SOURCE_KEY, token ? source : "");
}

/** Drop the stored session token, but only if `source` is what wrote it.
 *
 *  Firebase reporting "no session" means its own token is dead; it says nothing
 *  about a token the backend OAuth callback put here. Clearing unconditionally
 *  would sign out a perfectly good backend session every time a Supabase-enabled
 *  deployment loaded with no Supabase session of its own. */
export function clearSessionTokenFrom(source: TokenSource): void {
  if (read(SESSION_SOURCE_KEY) === source) {
    write(SESSION_TOKEN_KEY, "");
    write(SESSION_SOURCE_KEY, "");
  }
}

/** Persist the tenant API key returned by self-serve signup. */
export function setApiKey(key: string): void {
  write(API_KEY_KEY, key);
}

/** Drop every stored credential (logout). */
export function clearAuthToken(): void {
  write(SESSION_TOKEN_KEY, "");
  write(SESSION_SOURCE_KEY, "");
  write(API_KEY_KEY, "");
}

/** True when we have something to authenticate with. */
export function isAuthenticated(): boolean {
  return getAuthToken() !== "";
}

/** Merge the Authorization header into `extra`, omitting it when unauthenticated
 *  so the open local-dev backend still works. Read per call, not once at module
 *  load, so a login takes effect without a page reload. */
export function authHeaders(
  extra: Record<string, string> = {}
): Record<string, string> {
  const token = getAuthToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

/** Subprotocols for a WebSocket handshake. Browsers cannot set an Authorization
 *  header on a WebSocket, so the token rides in Sec-WebSocket-Protocol — kept out
 *  of the query string, which gets written to access logs. */
export function wsProtocols(): string[] {
  const token = getAuthToken();
  return token ? ["copilot-v1", `auth-${token}`] : ["copilot-v1"];
}
