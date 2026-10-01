/**
 * Incidents Page - Incident management with 6-agent investigation and safety gates
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  ChevronLeft,
  Clock,
  CheckCircle,
  XCircle,
  Shield,
  Brain,
  Zap,
  Users,
  BookOpen,
  Radio,
  RefreshCw,
  Plus,
  Loader2
} from "lucide-react";

import { apiFetch, describeFailure } from "../lib/apiClient";

// These mirror GET /api/incidents/{id}/investigation exactly (app/incidents/router.py:283).
// The page used to declare its own shape and then build an object to fit it out
// of the list response's `has_investigation` boolean — see `fetchIncidents`.

/** `AgentStatus` in app/incidents/models.py:28. Not "completed"/"failed": the
 *  old union used those two spellings, so the status indicator's checks could
 *  never match and every agent rendered with the neutral pending clock. */
type AgentStatus = "idle" | "running" | "complete" | "error";

interface AgentFinding {
  finding: string;
  confidence: number;
  timestamp: string;
}

interface AgentResult {
  agent_type: string;
  status: AgentStatus;
  /** 0-100, straight from the agent's own progress field. */
  progress: number;
  current_action: string | null;
  findings: AgentFinding[];
}

/** `SafetyGateStatus` in app/incidents/models.py:35. The API returns a list of
 *  these, whatever gates the investigation happened to run. The page previously
 *  modelled it as a fixed three — confidence, policy, sandbox — with an
 *  `overall_decision` the API does not send. */
interface SafetyGate {
  gate_type: string;
  status: "pending" | "passed" | "failed" | "bypassed";
  threshold: number | null;
  current_value: number | null;
  message: string | null;
}

interface Investigation {
  id: string;
  incident_id: string;
  status: "running" | "completed";
  started_at: string | null;
  completed_at: string | null;
  overall_confidence: number;
  root_cause: string | null;
  recommended_actions: string[];
  agents: Record<string, AgentResult>;
  safety_gates: SafetyGate[];
}

interface Incident {
  id: string;
  title: string;
  severity: "critical" | "high" | "medium" | "low";
  status: "triggered" | "investigating" | "identified" | "monitoring" | "resolved";
  affected_services: string[];
  created_at: string;
  updated_at: string;
  assigned_to?: string;
  /** From the list response. Says an investigation exists; says nothing about
   *  what it found. The detail view fetches the investigation itself. */
  has_investigation: boolean;
  investigation_confidence: number | null;
}

/** One incident row, from either shape the API sends it in.
 *
 *  `GET /api/incidents` returns the summary (`has_investigation`,
 *  `investigation_confidence`); `GET /api/incidents/{id}` returns the full
 *  record with the investigation nested. Both map here so the detail view and
 *  the list cannot drift into disagreeing about the same incident. */
function toIncident(raw: Record<string, unknown>): Incident {
  const investigation = (raw.investigation as Investigation | null) ?? null;
  const triggered = (raw.triggered_at as string) || new Date().toISOString();
  return {
    id: raw.id as string,
    title: raw.title as string,
    severity: (raw.severity as Incident["severity"]) || "medium",
    status: (raw.status as Incident["status"]) || "triggered",
    affected_services: raw.service ? [raw.service as string] : [],
    created_at: triggered,
    updated_at: (raw.resolved_at as string) || (raw.acknowledged_at as string) || triggered,
    assigned_to: (raw.assignee as string) || undefined,
    has_investigation: raw.has_investigation === true || investigation !== null,
    investigation_confidence:
      typeof raw.investigation_confidence === "number"
        ? raw.investigation_confidence
        : investigation?.overall_confidence ?? null,
  };
}

function SeverityBadge({ severity }: { severity: Incident["severity"] }) {
  const config = {
    critical: { bg: "var(--bad)", text: "white" },
    high: { bg: "#f59e0b", text: "white" },
    medium: { bg: "#eab308", text: "black" },
    low: { bg: "var(--accent)", text: "white" },
  };
  const c = config[severity];
  return (
    <span
      className="px-2 py-0.5 text-xs font-medium rounded-full"
      style={{ backgroundColor: c.bg, color: c.text }}
    >
      {severity.toUpperCase()}
    </span>
  );
}

function StatusBadge({ status }: { status: Incident["status"] }) {
  const colors: Record<string, string> = {
    triggered: "var(--bad)",
    investigating: "var(--accent)",
    identified: "#eab308",
    monitoring: "#8b5cf6",
    resolved: "var(--ok)",
  };
  return (
    <span
      className="px-2 py-0.5 text-xs font-medium rounded-full"
      style={{ backgroundColor: `${colors[status]}20`, color: colors[status] }}
    >
      {status.replace("_", " ")}
    </span>
  );
}

// `agent_type` is a free-form string on the server side, so an unrecognised one
// gets a generic icon instead of crashing on `icons[type]` being undefined.
const AGENT_ICONS: Record<string, typeof Brain> = {
  knowledge: BookOpen,
  reasoning: Brain,
  action: Zap,
  learning: BookOpen,
  collaboration: Users,
  ambient: Radio,
};

function AgentIcon({ type }: { type: string }) {
  const Icon = AGENT_ICONS[type] ?? Brain;
  return <Icon size={14} />;
}

function AgentStatusIndicator({ status }: { status: AgentStatus }) {
  if (status === "complete") return <CheckCircle size={14} style={{ color: "var(--ok)" }} />;
  if (status === "running") return <RefreshCw size={14} className="animate-spin" style={{ color: "var(--accent)" }} />;
  if (status === "error") return <XCircle size={14} style={{ color: "var(--bad)" }} />;
  return <Clock size={14} style={{ color: "var(--text-2)" }} />;
}

function ConfidenceBar({ score, threshold = 0.8 }: { score: number; threshold?: number }) {
  const pct = Math.round(score * 100);
  const passed = score >= threshold;
  return (
    <div className="flex items-center gap-2">
      <div
        className="flex-1 h-2 rounded-full overflow-hidden"
        style={{ backgroundColor: "var(--border)" }}
      >
        <div
          className="h-full transition-all duration-500"
          style={{
            width: `${pct}%`,
            backgroundColor: passed ? "var(--ok)" : "#eab308",
          }}
        />
      </div>
      <span
        className="text-sm font-mono"
        style={{ color: passed ? "var(--ok)" : "#eab308" }}
      >
        {pct}%
      </span>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center py-16">
      <Loader2 className="w-8 h-8 animate-spin" style={{ color: "var(--accent)" }} />
      <p className="mt-4 text-sm" style={{ color: "var(--text-2)" }}>Loading incidents...</p>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16">
      <CheckCircle className="w-12 h-12" style={{ color: "var(--ok)" }} />
      <p className="mt-4 text-lg font-medium" style={{ color: "var(--text)" }}>All Clear</p>
      <p className="text-sm" style={{ color: "var(--text-2)" }}>No active incidents</p>
    </div>
  );
}

/** The gates the investigation actually ran, in the order the API returned them.
 *
 *  There is no `overall_decision` field on the response, so the header verdict is
 *  derived here: any failed gate fails the set, any still pending holds it open,
 *  and everything passed passes. The previous version read `overall_decision`
 *  off an object the frontend had built itself, and rendered a fixed three-gate
 *  "3-Layer Defense" whether or not those were the gates that ran. */
function SafetyGatesCard({ gates }: { gates: SafetyGate[] }) {
  const decision = gates.some(g => g.status === "failed")
    ? "blocked"
    : gates.some(g => g.status === "pending")
      ? "pending"
      : "approved";
  const decisionColors = {
    approved: { border: "var(--ok)", bg: "rgba(45, 212, 167, 0.1)" },
    blocked: { border: "var(--bad)", bg: "rgba(255, 107, 107, 0.1)" },
    pending: { border: "#eab308", bg: "rgba(234, 179, 8, 0.1)" },
  };
  const colors = decisionColors[decision];

  const mark = (status: SafetyGate["status"]) =>
    status === "passed" ? "✓" : status === "failed" ? "✗" : status === "bypassed" ? "⤳" : "⏳";
  const markColor = (status: SafetyGate["status"]) =>
    status === "passed" ? "var(--ok)" : status === "failed" ? "var(--bad)" : "#eab308";

  return (
    <div
      className="rounded-xl p-4"
      style={{ backgroundColor: colors.bg, border: `2px solid ${colors.border}` }}
    >
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-semibold flex items-center gap-2" style={{ color: "var(--text)" }}>
          <Shield size={18} style={{ color: colors.border }} />
          Safety Gates ({gates.length})
        </h4>
        <span
          className="px-3 py-1 rounded-full text-sm font-medium"
          style={{ backgroundColor: colors.border, color: decision === "pending" ? "black" : "white" }}
        >
          {decision.toUpperCase()}
        </span>
      </div>

      <div className="space-y-3">
        {gates.map((gate, i) => (
          <div
            key={`${gate.gate_type}-${i}`}
            className="flex items-center justify-between gap-3 p-2 rounded"
            style={{ backgroundColor: "var(--surface)" }}
          >
            <div className="flex items-center gap-2 min-w-0">
              <span style={{ color: markColor(gate.status) }}>{mark(gate.status)}</span>
              <span className="font-medium" style={{ color: "var(--text)" }}>{gate.gate_type}</span>
              {gate.threshold !== null && (
                <span className="text-xs whitespace-nowrap" style={{ color: "var(--text-2)" }}>
                  (threshold: {gate.threshold})
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 min-w-0">
              {gate.message && (
                <span className="text-xs truncate" style={{ color: "var(--text-2)" }} title={gate.message}>
                  {gate.message}
                </span>
              )}
              {gate.current_value !== null && (
                <span className="font-mono text-sm whitespace-nowrap" style={{ color: "var(--text)" }}>
                  {gate.current_value}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function InvestigationCard({ investigation }: { investigation: Investigation }) {
  const agents = Object.entries(investigation.agents).map(([agent_type, a]) => ({ ...a, agent_type }));
  const completedAgents = agents.filter(a => a.status === "complete").length;
  const totalAgents = agents.length;

  // Elapsed time, from the investigation's own timestamps. `duration_ms` was
  // always the literal 0 the page had assigned itself, so the header read "..."
  // for every investigation ever run, finished or not.
  const started = investigation.started_at ? new Date(investigation.started_at).getTime() : null;
  const finished = investigation.completed_at ? new Date(investigation.completed_at).getTime() : null;
  const elapsedMs = started === null ? null : (finished ?? Date.now()) - started;

  return (
    <div
      className="rounded-xl p-4 space-y-4"
      style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center justify-between">
        <h3 className="font-semibold flex items-center gap-2" style={{ color: "var(--text)" }}>
          <Brain size={18} style={{ color: "var(--accent)" }} />
          AI Investigation
          <span
            className="text-xs px-2 py-0.5 rounded"
            style={{
              backgroundColor: investigation.status === "completed"
                ? "rgba(45, 212, 167, 0.2)"
                : "rgba(100, 145, 255, 0.2)",
              color: investigation.status === "completed" ? "var(--ok)" : "var(--accent)",
            }}
          >
            {investigation.status}
          </span>
        </h3>
        <span className="text-sm" style={{ color: "var(--text-2)" }}>
          {elapsedMs === null ? "—" : `${(elapsedMs / 1000).toFixed(1)}s`}
        </span>
      </div>

      {totalAgents > 0 && (
        <div>
          <div className="flex items-center justify-between text-sm mb-2">
            <span style={{ color: "var(--text-2)" }}>Agents</span>
            <span className="font-mono" style={{ color: "var(--text)" }}>{completedAgents}/{totalAgents}</span>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-1">
            {agents.map((agent) => (
              <div
                key={agent.agent_type}
                className="p-2 rounded text-center text-xs"
                style={{
                  backgroundColor: agent.status === "complete" ? "rgba(45, 212, 167, 0.15)" :
                    agent.status === "running" ? "rgba(100, 145, 255, 0.15)" :
                    agent.status === "error" ? "rgba(255, 107, 107, 0.15)" :
                    "var(--bg)",
                }}
                title={`${agent.agent_type}: ${agent.findings.length} findings, ${agent.progress}%${
                  agent.current_action ? ` — ${agent.current_action}` : ""
                }`}
              >
                <div className="flex justify-center mb-1">
                  <AgentStatusIndicator status={agent.status} />
                </div>
                <div className="truncate flex items-center justify-center gap-1" style={{ color: "var(--text)" }}>
                  <AgentIcon type={agent.agent_type} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="text-sm mb-1" style={{ color: "var(--text-2)" }}>Overall Confidence</div>
        <ConfidenceBar score={investigation.overall_confidence} />
      </div>

      {/* Rendered only when the investigation has actually produced one. It used
          to show the literal string "Investigation in progress..." under a "Root
          Cause Hypothesis" heading — a sentence the frontend wrote, presented as
          the AI's finding. An investigation that has not concluded says so. */}
      {investigation.root_cause ? (
        <div className="p-3 rounded-lg" style={{ backgroundColor: "var(--bg)" }}>
          <div className="text-xs uppercase tracking-wide mb-1" style={{ color: "var(--text-2)" }}>
            Root Cause
          </div>
          <p className="text-sm font-medium" style={{ color: "var(--text)" }}>
            {investigation.root_cause}
          </p>
        </div>
      ) : (
        <div className="p-3 rounded-lg text-sm" style={{ backgroundColor: "var(--bg)", color: "var(--text-2)" }}>
          {investigation.status === "running"
            ? "No root cause yet — the investigation is still running."
            : "The investigation finished without identifying a root cause."}
        </div>
      )}

      {investigation.recommended_actions.length > 0 && (
        <div className="p-3 rounded-lg" style={{ backgroundColor: "var(--bg)" }}>
          <div className="text-xs uppercase tracking-wide mb-2" style={{ color: "var(--text-2)" }}>
            Recommended Actions
          </div>
          <ul className="text-sm space-y-1" style={{ color: "var(--text)" }}>
            {investigation.recommended_actions.map((action, i) => (
              <li key={i}>• {action}</li>
            ))}
          </ul>
        </div>
      )}

      {investigation.safety_gates.length > 0 && (
        <SafetyGatesCard gates={investigation.safety_gates} />
      )}
    </div>
  );
}

function IncidentRow({ incident, onSelect }: { incident: Incident; onSelect: () => void }) {
  const timeAgo = (date: string) => {
    const seconds = Math.floor((Date.now() - new Date(date).getTime()) / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };

  return (
    <tr
      className="cursor-pointer transition-colors"
      onClick={onSelect}
      style={{ borderBottom: "1px solid var(--border)" }}
      onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--surface)")}
      onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
    >
      <td className="px-4 py-3">
        <div className="font-medium" style={{ color: "var(--text)" }}>{incident.title}</div>
        <div className="text-xs mt-0.5" style={{ color: "var(--text-2)" }}>
          {incident.affected_services.join(", ")}
        </div>
      </td>
      <td className="px-4 py-3"><SeverityBadge severity={incident.severity} /></td>
      <td className="px-4 py-3"><StatusBadge status={incident.status} /></td>
      <td className="px-4 py-3 text-sm" style={{ color: "var(--text-2)" }}>
        {incident.assigned_to || "-"}
      </td>
      <td className="px-4 py-3 text-sm" style={{ color: "var(--text-2)" }}>
        {timeAgo(incident.created_at)}
      </td>
      <td className="px-4 py-3">
        {/* The list response says only whether an investigation exists and, if it
            has one, its confidence. It does not say whether it is still running,
            so the dot no longer pulses on a guess — the row used to hardcode
            `status: "running"` for every investigated incident, including ones
            that finished days ago. */}
        {incident.has_investigation && (
          <div className="flex items-center gap-1">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: "var(--accent)" }}
            />
            <span className="text-xs" style={{ color: "var(--text)" }}>
              {incident.investigation_confidence === null
                ? "—"
                : `${Math.round(incident.investigation_confidence * 100)}%`}
            </span>
          </div>
        )}
      </td>
    </tr>
  );
}

function IncidentDetail({
  seed,
  onBack,
  onChanged,
  onError
}: {
  /** The row that was clicked. Only the starting picture: everything below is
   *  re-read from the server, so this is here to avoid a blank frame. */
  seed: Incident;
  onBack: () => void;
  onChanged: () => void;
  onError: (message: string | null) => void;
}) {
  // The detail view used to render the object captured at click time and never
  // look at it again — the 10s list poll replaced `incidents` and nothing
  // copied the fresh row back in. So an investigation dispatched while the page
  // was open never appeared, and an incident someone else had already resolved
  // kept showing its old badge and a live "Resolve" button. It reads its own
  // record now, from GET /api/incidents/{id}, which carries the investigation
  // with it — one request where there used to be a stale prop and a second fetch.
  const [incident, setIncident] = useState<Incident>(seed);
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [invError, setInvError] = useState<string | null>(null);
  const [invLoading, setInvLoading] = useState(seed.has_investigation);
  const [acting, setActing] = useState<string | null>(null);

  const incidentId = seed.id;

  const load = useCallback(async () => {
    setInvLoading(true);
    try {
      const res = await apiFetch(`/api/incidents/${incidentId}`);
      if (!res.ok) throw new Error(await describeFailure(res));
      const body = await res.json();
      const raw = (body.incident ?? body) as Record<string, unknown>;
      const found = (body.investigation as Investigation | null) ?? null;
      setIncident(toIncident({ ...raw, investigation: found }));
      setInvestigation(found);
      setInvError(null);
    } catch (err) {
      // The incident itself stays on screen — what failed is the refresh, and
      // blanking the page an operator is reading would be the worse answer.
      setInvError(err instanceof Error ? err.message : "Could not refresh this incident");
    } finally {
      setInvLoading(false);
    }
  }, [incidentId]);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled) load();
    };
    tick();
    // A running investigation changes underneath the page, and so does the
    // incident: someone else can acknowledge or resolve it while this is open.
    const interval = setInterval(tick, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [load]);

  /** Acknowledge or resolve.
   *
   *  Neither of these checked `res.ok`. `await apiFetch(...)` resolves for a
   *  404, a 422 or a 500 exactly as it does for a 200, so the page closed the
   *  detail view and refreshed the list either way — and the incident was still
   *  sitting there unacknowledged with nothing on screen to say why. */
  const runAction = async (label: string, path: string) => {
    setActing(label);
    try {
      const res = await apiFetch(path, { method: "POST" });
      if (!res.ok) throw new Error(await describeFailure(res));
      onError(null);
      // Stay on the incident being worked and show the new status, rather than
      // bouncing back to the list and making the operator find it again.
      await load();
      onChanged();
    } catch (err) {
      onError(
        `Could not ${label} incident ${incidentId}: ${err instanceof Error ? err.message : "request failed"}`
      );
    } finally {
      setActing(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button
          onClick={onBack}
          className="p-2 rounded-lg transition-colors"
          style={{ color: "var(--text-2)" }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--surface)")}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
        >
          <ChevronLeft size={20} />
        </button>
        <div className="flex-1">
          <h2 className="text-xl font-semibold" style={{ color: "var(--text)" }}>{incident.title}</h2>
          <div className="flex items-center gap-3 mt-1">
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
            <span className="text-sm" style={{ color: "var(--text-2)" }}>ID: {incident.id}</span>
          </div>
        </div>
        <div className="flex gap-2">
          {incident.status === "triggered" && (
            <button
              onClick={() => runAction("acknowledge", `/api/incidents/${incidentId}/acknowledge`)}
              disabled={acting !== null}
              className="px-4 py-2 rounded-lg font-medium"
              style={{ backgroundColor: "var(--accent)", color: "var(--bg)", opacity: acting ? 0.6 : 1 }}
            >
              {acting === "acknowledge" ? "Acknowledging..." : "Acknowledge"}
            </button>
          )}
          {incident.status !== "resolved" && (
            <button
              onClick={() => runAction("resolve", `/api/incidents/${incidentId}/resolve`)}
              disabled={acting !== null}
              className="px-4 py-2 rounded-lg"
              style={{ border: "1px solid var(--ok)", color: "var(--ok)", opacity: acting ? 0.6 : 1 }}
            >
              {acting === "resolve" ? "Resolving..." : "Resolve"}
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          {investigation && <InvestigationCard investigation={investigation} />}
          {invError && (
            <div
              className="rounded-xl p-4 text-sm"
              style={{ backgroundColor: "rgba(255, 107, 107, 0.1)", border: "1px solid var(--bad)", color: "var(--bad)" }}
            >
              {invError}
            </div>
          )}
          {invLoading && !investigation && !invError && (
            <div className="rounded-xl p-4 flex items-center gap-2 text-sm"
              style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}>
              <Loader2 size={14} className="animate-spin" />
              Loading investigation...
            </div>
          )}

          <div
            className="rounded-xl p-4"
            style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <h3 className="font-semibold mb-3" style={{ color: "var(--text)" }}>Timeline</h3>
            <div className="space-y-3 text-sm">
              <div className="flex gap-3">
                <span
                  className="w-2 h-2 mt-1.5 rounded-full flex-shrink-0"
                  style={{ backgroundColor: "var(--bad)" }}
                />
                <div>
                  <div className="font-medium" style={{ color: "var(--text)" }}>Incident triggered</div>
                  <div style={{ color: "var(--text-2)" }}>
                    {new Date(incident.created_at).toLocaleString()}
                  </div>
                </div>
              </div>
              {/* "6 parallel agents dispatched" was printed whether six agents
                  ran or none did. The count comes off the investigation now, and
                  the entry carries the time it actually started. */}
              {investigation && (
                <div className="flex gap-3">
                  <span
                    className="w-2 h-2 mt-1.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: "var(--accent)" }}
                  />
                  <div>
                    <div className="font-medium" style={{ color: "var(--text)" }}>AI investigation started</div>
                    <div style={{ color: "var(--text-2)" }}>
                      {investigation.started_at
                        ? new Date(investigation.started_at).toLocaleString()
                        : "start time not recorded"}
                      {" · "}
                      {Object.keys(investigation.agents).length} agent
                      {Object.keys(investigation.agents).length === 1 ? "" : "s"}
                    </div>
                  </div>
                </div>
              )}
              {investigation?.completed_at && (
                <div className="flex gap-3">
                  <span
                    className="w-2 h-2 mt-1.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: "var(--ok)" }}
                  />
                  <div>
                    <div className="font-medium" style={{ color: "var(--text)" }}>AI investigation completed</div>
                    <div style={{ color: "var(--text-2)" }}>
                      {new Date(investigation.completed_at).toLocaleString()}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="space-y-6">
          <div
            className="rounded-xl p-4"
            style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <h3 className="font-semibold mb-3" style={{ color: "var(--text)" }}>Affected Services</h3>
            <div className="flex flex-wrap gap-2">
              {incident.affected_services.map((svc) => (
                <span
                  key={svc}
                  className="px-2 py-1 rounded text-sm"
                  style={{ backgroundColor: "var(--bg)", color: "var(--text)" }}
                >
                  {svc}
                </span>
              ))}
            </div>
          </div>

          {/* A "Quick Actions" panel used to sit here with four buttons —
              "Rollback last deployment", "Scale up replicas", "Restart service",
              "Run diagnostic runbook" — none of which had an onClick handler.
              Clicking them did nothing at all, on an incident page, during an
              incident. Removed rather than left in place: a button that looks
              like it can roll back a deployment is worse than no button when the
              person pressing it is under pressure. What the AI recommends for
              this incident is shown in the investigation card, which reads
              `recommended_actions` from the server. */}
          {investigation && investigation.recommended_actions.length === 0 && investigation.status === "completed" && (
            <div
              className="rounded-xl p-4 text-sm"
              style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-2)" }}
            >
              The investigation did not recommend any actions.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** The form behind "Declare Incident".
 *
 *  That button had no onClick. It rendered in the header of the incidents page,
 *  styled in the danger colour, and did nothing when pressed — there was no way
 *  to open an incident from this UI at all, even though POST /api/incidents has
 *  existed the whole time. `title`, `severity` and `service` are the three
 *  fields CreateIncidentRequest requires (app/incidents/models.py:115). */
function DeclareIncidentModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [service, setService] = useState("");
  const [severity, setSeverity] = useState<Incident["severity"]>("high");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setFormError(null);
    try {
      const res = await apiFetch("/api/incidents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          service: service.trim(),
          severity,
          description: description.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(await describeFailure(res));
      onCreated();
      onClose();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to declare incident");
    } finally {
      setSubmitting(false);
    }
  };

  const inputStyle = {
    backgroundColor: "var(--bg)",
    border: "1px solid var(--border)",
    color: "var(--text)",
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <motion.form
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        className="w-full max-w-lg rounded-xl p-6 space-y-4"
        style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
      >
        <h2 className="text-lg font-semibold" style={{ color: "var(--text)" }}>Declare Incident</h2>

        <label className="block">
          <span className="text-sm" style={{ color: "var(--text-2)" }}>Title</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            autoFocus
            className="w-full mt-1 px-3 py-2 rounded-lg text-sm"
            style={inputStyle}
            placeholder="Checkout API returning 500s"
          />
        </label>

        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-sm" style={{ color: "var(--text-2)" }}>Service</span>
            <input
              value={service}
              onChange={(e) => setService(e.target.value)}
              required
              className="w-full mt-1 px-3 py-2 rounded-lg text-sm"
              style={inputStyle}
              placeholder="payments-api"
            />
          </label>
          <label className="block">
            <span className="text-sm" style={{ color: "var(--text-2)" }}>Severity</span>
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Incident["severity"])}
              className="w-full mt-1 px-3 py-2 rounded-lg text-sm"
              style={inputStyle}
            >
              {(["critical", "high", "medium", "low"] as const).map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
        </div>

        <label className="block">
          <span className="text-sm" style={{ color: "var(--text-2)" }}>Description (optional)</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full mt-1 px-3 py-2 rounded-lg text-sm"
            style={inputStyle}
          />
        </label>

        {formError && (
          <p className="text-sm" style={{ color: "var(--bad)" }}>{formError}</p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting || !title.trim() || !service.trim()}
            className="px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2"
            style={{ backgroundColor: "var(--bad)", color: "white", opacity: submitting ? 0.6 : 1 }}
          >
            {submitting && <Loader2 size={14} className="animate-spin" />}
            Declare
          </button>
        </div>
      </motion.form>
    </div>
  );
}

export function Incidents({ onBack }: { onBack: () => void }) {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  // The clicked row, kept only to seed the detail view so it has something to
  // draw on its first frame. It is not the detail view's source of truth any
  // more — that was the bug: the object captured here never changed again.
  const [selectedIncident, setSelectedIncident] = useState<Incident | null>(null);
  const [filter, setFilter] = useState<"all" | "active" | "resolved">("active");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [declaring, setDeclaring] = useState(false);

  const fetchIncidents = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/incidents`);
      if (!res.ok) throw new Error(await describeFailure(res));
      const data: Record<string, unknown>[] = await res.json();
      // A straight mapping of the list response. The `investigation` object this
      // used to synthesise here — a fabricated `inv-${id}`, a hardcoded "running"
      // status, an empty agent list and the literal root cause "Investigation in
      // progress..." — is gone; the detail view fetches the real one.
      setIncidents(data.map(toIncident));
      setError(null);
    } catch (err) {
      console.error("Failed to fetch incidents:", err);
      setError(err instanceof Error ? err.message : "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchIncidents();
    const interval = setInterval(fetchIncidents, 10000);
    return () => clearInterval(interval);
  }, [fetchIncidents]);

  const filteredIncidents = incidents.filter(inc => {
    if (filter === "active") return inc.status !== "resolved";
    if (filter === "resolved") return inc.status === "resolved";
    return true;
  });

  return (
    <div className="min-h-screen omni-workspace-page" style={{ backgroundColor: "var(--bg)" }}>
      <header className="nav">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="nav__btn"
            style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}
          >
            <ChevronLeft size={18} />
            <span>Back</span>
          </button>
          <h1 className="text-xl font-semibold" style={{ color: "var(--text)" }}>
            <AlertTriangle size={20} style={{ display: "inline", marginRight: "0.5rem", color: "var(--bad)" }} />
            Incidents
          </h1>
        </div>
        <div className="nav__right">
          <button
            onClick={fetchIncidents}
            className="p-2 rounded-lg mr-2"
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={() => setDeclaring(true)}
            className="px-4 py-2 rounded-lg font-medium flex items-center gap-2"
            style={{ backgroundColor: "var(--bad)", color: "white" }}
          >
            <Plus size={16} />
            Declare Incident
          </button>
        </div>
      </header>

      {declaring && (
        <DeclareIncidentModal onClose={() => setDeclaring(false)} onCreated={fetchIncidents} />
      )}

      <main className="max-w-7xl mx-auto px-4 py-6">
        {/* Outside the detail/list branch: an acknowledge that fails also closes
            the detail view, so a banner nested inside it would unmount with the
            very message the operator needs to read. */}
        {error && (
          <div
            className="mb-6 rounded-xl px-4 py-3 flex items-start justify-between gap-4"
            style={{ backgroundColor: "var(--bad-bg, rgba(239,68,68,0.1))", border: "1px solid var(--bad)" }}
          >
            <span className="text-sm" style={{ color: "var(--bad)" }}>{error}</span>
            <button
              onClick={() => setError(null)}
              className="text-sm underline shrink-0"
              style={{ color: "var(--bad)" }}
            >
              Dismiss
            </button>
          </div>
        )}

        {selectedIncident ? (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <IncidentDetail
              key={selectedIncident.id}
              seed={selectedIncident}
              onBack={() => setSelectedIncident(null)}
              onChanged={fetchIncidents}
              onError={setError}
            />
          </motion.div>
        ) : (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <div className="flex items-center justify-between mb-6">
              <div className="flex gap-2">
                {(["all", "active", "resolved"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className="px-3 py-1.5 text-sm font-medium rounded-lg transition-colors"
                    style={{
                      backgroundColor: filter === f ? "var(--accent)" : "var(--surface)",
                      color: filter === f ? "var(--bg)" : "var(--text)",
                    }}
                  >
                    {f.charAt(0).toUpperCase() + f.slice(1)}
                  </button>
                ))}
              </div>
            </div>

            <div
              className="rounded-xl overflow-hidden"
              style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
            >
              {loading ? (
                <LoadingState />
              ) : filteredIncidents.length === 0 ? (
                <EmptyState />
              ) : (
                <table className="w-full">
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <th className="px-4 py-3 text-left text-sm font-medium" style={{ color: "var(--text-2)" }}>Incident</th>
                      <th className="px-4 py-3 text-left text-sm font-medium" style={{ color: "var(--text-2)" }}>Severity</th>
                      <th className="px-4 py-3 text-left text-sm font-medium" style={{ color: "var(--text-2)" }}>Status</th>
                      <th className="px-4 py-3 text-left text-sm font-medium" style={{ color: "var(--text-2)" }}>Assigned</th>
                      <th className="px-4 py-3 text-left text-sm font-medium" style={{ color: "var(--text-2)" }}>Created</th>
                      <th className="px-4 py-3 text-left text-sm font-medium" style={{ color: "var(--text-2)" }}>AI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredIncidents.map((incident) => (
                      <IncidentRow
                        key={incident.id}
                        incident={incident}
                        onSelect={() => setSelectedIncident(incident)}
                      />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </motion.div>
        )}
      </main>
    </div>
  );
}
