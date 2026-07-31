// Thin, typed client for the DevOps Copilot backend.

import type { AppConfig, ChatResponse, GithubStatus, Me, MetricsResponse, StreamEvent } from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
// Optional bearer token (set VITE_API_TOKEN when the backend has COPILOT_API_TOKEN).
const TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return TOKEN ? { ...extra, Authorization: `Bearer ${TOKEN}` } : { ...extra };
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { method: "DELETE", headers: authHeaders() });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json() as Promise<T>;
}

/** POST that consumes a Server-Sent Events stream, invoking `onEvent` per message.
 *  Pass an AbortSignal to cancel mid-stream (the Stop button) — aborting the fetch
 *  disconnects the SSE, which cancels the agent run server-side. */
async function streamPost(
  path: string,
  body: unknown,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? ""; // keep the partial frame
    for (const frame of frames) {
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n");
      if (!data) continue; // skip pings / comments
      try {
        onEvent(JSON.parse(data) as StreamEvent);
      } catch {
        /* ignore non-JSON keep-alives */
      }
    }
  }
}

/** Start an investigation, streaming trace + final answer events. */
export function chatStream(
  threadId: string,
  message: string,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamPost("/chat/stream", { thread_id: threadId, message }, onEvent, signal);
}

/** Resume a paused investigation, streaming the continuation. */
export function approveStream(
  threadId: string,
  approved: boolean,
  reason: string,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamPost(
    "/approve/stream",
    { thread_id: threadId, approved, reason },
    onEvent,
    signal
  );
}

/** Non-streaming fallbacks (also used by tools/tests). */
export function chat(threadId: string, message: string): Promise<ChatResponse> {
  return post<ChatResponse>("/chat", { thread_id: threadId, message });
}
export function approve(
  threadId: string,
  approved: boolean,
  reason = ""
): Promise<ChatResponse> {
  return post<ChatResponse>("/approve", { thread_id: threadId, approved, reason });
}

/** Real metric series + error summary for the dashboard charts. */
export function getMetrics(): Promise<MetricsResponse> {
  return get<MetricsResponse>("/metrics");
}

/** Submit thumbs up/down on an investigation (feeds the eval/learning loop). */
export function submitFeedback(
  threadId: string,
  rating: "up" | "down",
  comment = "",
  question = ""
): Promise<unknown> {
  return post("/feedback", { thread_id: threadId, rating, comment, question });
}

/** Fetch the running agent's provider, models, and MCP server catalog. */
export function getConfig(): Promise<AppConfig> {
  return get<AppConfig>("/config");
}

/** Current identity for the UI (real org/role in multi-tenant mode). */
export function getMe(): Promise<Me> {
  return get<Me>("/me");
}

/** Switch LLM provider/model/key at runtime. */
export function configureModel(
  provider: string,
  api_key: string,
  model: string,
  fast_model: string
): Promise<{ provider: string; model: string; fast_model: string }> {
  return post("/model/configure", { provider, api_key, model, fast_model });
}

/** Point the repo MCP server at a local directory. */
export function connectRepo(path: string): Promise<{ repo_path: string }> {
  return post("/sources/repo", { path });
}

/** Point the logs/metrics MCP server at a local directory. */
export function connectLogs(
  path: string
): Promise<{ logs_path: string; missing_files: string[] }> {
  return post("/sources/logs", { path });
}

/** Revert all runtime overrides to .env defaults. */
export function resetConfig(): Promise<unknown> {
  return post("/reset", {});
}

/** Current GitHub connection state. */
export function githubStatus(): Promise<GithubStatus> {
  return get<GithubStatus>("/github/status");
}

/** Connect a GitHub repo (validated server-side against the real API). */
export function githubConnect(token: string, repo: string): Promise<GithubStatus> {
  return post<GithubStatus>("/github/connect", { token, repo });
}

/** Disconnect GitHub and revert to offline-demo mode. */
export function githubDisconnect(): Promise<GithubStatus> {
  return post<GithubStatus>("/github/disconnect", {});
}

// --- Admin / multi-tenant (only functional when COPILOT_MULTI_TENANT=true) --- //
import type { AdminOrg, ApiKeyInfo, AuditEvent, UsageSummary } from "./types";

export function getUsage(): Promise<UsageSummary> {
  return get<UsageSummary>("/usage");
}
export function getAudit(limit = 50): Promise<{ events: AuditEvent[] }> {
  return get(`/audit?limit=${limit}`);
}
export function verifyAudit(): Promise<{ valid: boolean; count?: number; broken_at?: number }> {
  return get("/audit/verify");
}
export function adminOrg(): Promise<AdminOrg> {
  return get<AdminOrg>("/admin/org");
}
export function adminListKeys(): Promise<{ api_keys: ApiKeyInfo[] }> {
  return get("/admin/api-keys");
}
export function adminCreateKey(name: string, role: string): Promise<{ api_key: string }> {
  return post("/admin/api-keys", { name, role });
}
export function adminRevokeKey(id: string): Promise<unknown> {
  return del(`/admin/api-keys/${id}`);
}

export interface SignupResult {
  org_id: string;
  org_name: string;
  plan: string;
  role: string;
  api_key: string;
  note: string;
}
/** Self-serve onboarding: create an org + owner key (multi-tenant mode only). */
export function signup(orgName: string, email: string): Promise<SignupResult> {
  return post<SignupResult>("/signup", { org_name: orgName, email });
}

/** Liveness check used by the header status dot. */
export async function health(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/healthz`);
    return res.ok;
  } catch {
    return false;
  }
}

// --------------------------------------------------------------------------- //
// WebSocket support
// --------------------------------------------------------------------------- //

/** Get the WebSocket base URL (converts http(s) to ws(s)). */
export function getWebSocketUrl(): string {
  const url = new URL(BASE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString().replace(/\/$/, "");
}

/** Get the auth token for WebSocket connections. */
export function getAuthToken(): string {
  return TOKEN;
}

/** Health check response type with WebSocket info. */
export interface HealthResponse {
  status: string;
  active_sessions: number;
  websocket?: {
    total_connections: number;
    enabled: boolean;
  };
}

/** Extended health check that includes WebSocket status. */
export async function healthExtended(): Promise<HealthResponse | null> {
  try {
    const res = await fetch(`${BASE_URL}/healthz`);
    if (!res.ok) return null;
    return res.json() as Promise<HealthResponse>;
  } catch {
    return null;
  }
}

/** Check if WebSocket is enabled on the server. */
export async function isWebSocketEnabled(): Promise<boolean> {
  const health = await healthExtended();
  return health?.websocket?.enabled ?? false;
}

// --------------------------------------------------------------------------- //
// Integrations
// --------------------------------------------------------------------------- //

export interface IntegrationStatus {
  slack: boolean;
  github: boolean;
  pagerduty: boolean;
  datadog: boolean;
  sentry: boolean;
  kubernetes: boolean;
}

/** Get status of configured integrations. */
export function getIntegrationStatus(): Promise<IntegrationStatus> {
  return get<IntegrationStatus>("/integrations/status");
}

/** Share an investigation report to Slack. */
export async function shareToSlack(
  query: string,
  report: Record<string, unknown>,
  channel?: string
): Promise<{ success: boolean; message?: string }> {
  return post("/share/slack", { query, report, channel: channel || "" });
}

// --------------------------------------------------------------------------- //
// Incidents API
// --------------------------------------------------------------------------- //

export interface IncidentSummary {
  id: string;
  title: string;
  description: string | null;
  severity: "critical" | "high" | "medium" | "low";
  status: "triggered" | "investigating" | "identified" | "monitoring" | "resolved";
  service: string;
  triggered_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  assignee: string | null;
  tags: string[];
  has_investigation: boolean;
  investigation_confidence: number | null;
}

export interface AgentState {
  status: "idle" | "running" | "complete" | "error";
  progress: number;
  current_action: string | null;
  findings: Array<{
    finding: string;
    confidence: number;
    timestamp: string;
  }>;
}

export interface SafetyGate {
  gate_type: string;
  status: "pending" | "passed" | "failed" | "bypassed";
  threshold: number | null;
  current_value: number | null;
  message: string | null;
}

export interface Investigation {
  id: string;
  incident_id: string;
  started_at: string;
  completed_at: string | null;
  overall_confidence: number;
  root_cause: string | null;
  recommended_actions: string[];
  agents: Record<string, AgentState>;
  safety_gates: SafetyGate[];
}

export interface TimelineEntry {
  timestamp: string;
  type: string;
  message: string;
  user: string | null;
}

export interface IncidentDetail {
  incident: IncidentSummary & {
    timeline: TimelineEntry[];
    impact: string | null;
    customer_impact: boolean;
  };
  investigation: Investigation | null;
}

export function listIncidents(params?: {
  status?: string;
  severity?: string;
  service?: string;
  limit?: number;
}): Promise<IncidentSummary[]> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.severity) query.set("severity", params.severity);
  if (params?.service) query.set("service", params.service);
  if (params?.limit) query.set("limit", String(params.limit));
  const qs = query.toString();
  return get<IncidentSummary[]>(`/api/incidents${qs ? `?${qs}` : ""}`);
}

export function getIncident(id: string): Promise<IncidentDetail> {
  return get<IncidentDetail>(`/api/incidents/${id}`);
}

export function createIncident(data: {
  title: string;
  severity: string;
  service: string;
  description?: string;
  tags?: string[];
}): Promise<IncidentSummary> {
  return post<IncidentSummary>("/api/incidents", data);
}

export function acknowledgeIncident(id: string, user: string): Promise<{ id: string; status: string }> {
  return post<{ id: string; status: string }>(`/api/incidents/${id}/acknowledge?user=${encodeURIComponent(user)}`, {});
}

export function resolveIncident(id: string, user: string, notes?: string): Promise<{ id: string; status: string }> {
  const query = `user=${encodeURIComponent(user)}${notes ? `&notes=${encodeURIComponent(notes)}` : ""}`;
  return post<{ id: string; status: string }>(`/api/incidents/${id}/resolve?${query}`, {});
}

export function startInvestigation(id: string): Promise<{ investigation_id: string }> {
  return post<{ investigation_id: string }>(`/api/incidents/${id}/investigate`, {});
}

export function getInvestigation(incidentId: string): Promise<Investigation> {
  return get<Investigation>(`/api/incidents/${incidentId}/investigation`);
}

export function getIncidentTimeline(id: string): Promise<TimelineEntry[]> {
  return get<TimelineEntry[]>(`/api/incidents/${id}/timeline`);
}

// --------------------------------------------------------------------------- //
// On-Call API
// --------------------------------------------------------------------------- //

export interface OncallTeam {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  timezone: string;
  is_active: boolean;
}

export interface OncallShift {
  id: string;
  schedule_id: string;
  user_id: string;
  start_time: string;
  end_time: string;
  shift_type: string;
  status: string;
}

export interface OncallAlert {
  id: string;
  title: string;
  description: string | null;
  severity: string;
  status: string;
  triggered_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

export function listOncallTeams(): Promise<OncallTeam[]> {
  return get<OncallTeam[]>("/oncall/teams");
}

export function getCurrentOncall(teamId: string): Promise<unknown> {
  return get(`/oncall/teams/${teamId}/current-oncall`);
}

export function listOncallAlerts(params?: {
  team_id?: string;
  status?: string;
  severity?: string;
}): Promise<OncallAlert[]> {
  const query = new URLSearchParams();
  if (params?.team_id) query.set("team_id", params.team_id);
  if (params?.status) query.set("status", params.status);
  if (params?.severity) query.set("severity", params.severity);
  const qs = query.toString();
  return get<OncallAlert[]>(`/oncall/alerts${qs ? `?${qs}` : ""}`);
}

export function acknowledgeOncallAlert(alertId: string, userId: string): Promise<{ id: string; status: string }> {
  return post<{ id: string; status: string }>(`/oncall/alerts/${alertId}/acknowledge?user_id=${userId}`, {});
}

export function resolveOncallAlert(alertId: string, userId: string, notes?: string): Promise<{ id: string; status: string }> {
  const query = `user_id=${userId}${notes ? `&notes=${encodeURIComponent(notes)}` : ""}`;
  return post<{ id: string; status: string }>(`/oncall/alerts/${alertId}/resolve?${query}`, {});
}
