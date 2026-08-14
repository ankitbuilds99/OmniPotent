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
import { useApiBaseUrl } from "../lib/runtime";

interface AgentResult {
  agent_type: "knowledge" | "reasoning" | "action" | "learning" | "collaboration" | "ambient";
  status: "pending" | "running" | "completed" | "failed" | "timeout";
  confidence_score: number;
  findings_count: number;
  duration_ms: number;
}

interface SafetyEvaluation {
  overall_decision: "approved" | "blocked" | "escalated";
  gates: {
    confidence: { passed: boolean; score: number; threshold: number };
    policy: { passed: boolean; violations: string[] };
    sandbox: { passed: boolean; test_result: string };
  };
}

interface Investigation {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  confidence_score: number;
  root_cause_hypothesis: string;
  agent_results: AgentResult[];
  safety_evaluation?: SafetyEvaluation;
  duration_ms: number;
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
  investigation?: Investigation;
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

function AgentIcon({ type }: { type: AgentResult["agent_type"] }) {
  const icons = {
    knowledge: Brain,
    reasoning: Brain,
    action: Zap,
    learning: BookOpen,
    collaboration: Users,
    ambient: Radio,
  };
  const Icon = icons[type];
  return <Icon size={14} />;
}

function AgentStatusIndicator({ status }: { status: AgentResult["status"] }) {
  if (status === "completed") return <CheckCircle size={14} style={{ color: "var(--ok)" }} />;
  if (status === "running") return <RefreshCw size={14} className="animate-spin" style={{ color: "var(--accent)" }} />;
  if (status === "failed") return <XCircle size={14} style={{ color: "var(--bad)" }} />;
  if (status === "timeout") return <Clock size={14} style={{ color: "#eab308" }} />;
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

function SafetyGatesCard({ evaluation }: { evaluation: SafetyEvaluation }) {
  const decisionColors = {
    approved: { border: "var(--ok)", bg: "rgba(45, 212, 167, 0.1)" },
    blocked: { border: "var(--bad)", bg: "rgba(255, 107, 107, 0.1)" },
    escalated: { border: "#eab308", bg: "rgba(234, 179, 8, 0.1)" },
  };
  const colors = decisionColors[evaluation.overall_decision];

  return (
    <div
      className="rounded-xl p-4"
      style={{ backgroundColor: colors.bg, border: `2px solid ${colors.border}` }}
    >
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-semibold flex items-center gap-2" style={{ color: "var(--text)" }}>
          <Shield size={18} style={{ color: colors.border }} />
          Safety Gates (3-Layer Defense)
        </h4>
        <span
          className="px-3 py-1 rounded-full text-sm font-medium"
          style={{
            backgroundColor: colors.border,
            color: evaluation.overall_decision === "escalated" ? "black" : "white",
          }}
        >
          {evaluation.overall_decision.toUpperCase()}
        </span>
      </div>

      <div className="space-y-3">
        <div
          className="flex items-center justify-between p-2 rounded"
          style={{ backgroundColor: "var(--surface)" }}
        >
          <div className="flex items-center gap-2">
            <span style={{ color: evaluation.gates.confidence.passed ? "var(--ok)" : "var(--bad)" }}>
              {evaluation.gates.confidence.passed ? "✓" : "✗"}
            </span>
            <span className="font-medium" style={{ color: "var(--text)" }}>Gate 1: Confidence</span>
            <span className="text-xs" style={{ color: "var(--text-2)" }}>
              (threshold: {evaluation.gates.confidence.threshold * 100}%)
            </span>
          </div>
          <span className="font-mono text-sm" style={{ color: "var(--text)" }}>
            {Math.round(evaluation.gates.confidence.score * 100)}%
          </span>
        </div>

        <div
          className="flex items-center justify-between p-2 rounded"
          style={{ backgroundColor: "var(--surface)" }}
        >
          <div className="flex items-center gap-2">
            <span style={{ color: evaluation.gates.policy.passed ? "var(--ok)" : "var(--bad)" }}>
              {evaluation.gates.policy.passed ? "✓" : "✗"}
            </span>
            <span className="font-medium" style={{ color: "var(--text)" }}>Gate 2: Policy Guardrails</span>
          </div>
          {evaluation.gates.policy.violations.length > 0 && (
            <span className="text-xs" style={{ color: "var(--bad)" }}>
              {evaluation.gates.policy.violations.join(", ")}
            </span>
          )}
        </div>

        <div
          className="flex items-center justify-between p-2 rounded"
          style={{ backgroundColor: "var(--surface)" }}
        >
          <div className="flex items-center gap-2">
            <span style={{ color: evaluation.gates.sandbox.passed ? "var(--ok)" : "#eab308" }}>
              {evaluation.gates.sandbox.passed ? "✓" : "⏳"}
            </span>
            <span className="font-medium" style={{ color: "var(--text)" }}>Gate 3: Sandbox Test</span>
          </div>
          <span className="text-xs" style={{ color: "var(--text-2)" }}>
            {evaluation.gates.sandbox.test_result}
          </span>
        </div>
      </div>
    </div>
  );
}

function InvestigationCard({ investigation }: { investigation: Investigation }) {
  const completedAgents = investigation.agent_results.filter(a => a.status === "completed").length;
  const totalAgents = investigation.agent_results.length;

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
              backgroundColor: investigation.status === "completed" ? "rgba(45, 212, 167, 0.2)" :
                investigation.status === "running" ? "rgba(100, 145, 255, 0.2)" :
                "rgba(255, 107, 107, 0.2)",
              color: investigation.status === "completed" ? "var(--ok)" :
                investigation.status === "running" ? "var(--accent)" :
                "var(--bad)",
            }}
          >
            {investigation.status}
          </span>
        </h3>
        <span className="text-sm" style={{ color: "var(--text-2)" }}>
          {investigation.duration_ms ? `${(investigation.duration_ms / 1000).toFixed(1)}s` : "..."}
        </span>
      </div>

      {totalAgents > 0 && (
        <div>
          <div className="flex items-center justify-between text-sm mb-2">
            <span style={{ color: "var(--text-2)" }}>6-Agent Parallel Investigation</span>
            <span className="font-mono" style={{ color: "var(--text)" }}>{completedAgents}/{totalAgents}</span>
          </div>
          <div className="grid grid-cols-6 gap-1">
            {investigation.agent_results.map((agent) => (
              <div
                key={agent.agent_type}
                className="p-2 rounded text-center text-xs"
                style={{
                  backgroundColor: agent.status === "completed" ? "rgba(45, 212, 167, 0.15)" :
                    agent.status === "running" ? "rgba(100, 145, 255, 0.15)" :
                    agent.status === "failed" ? "rgba(255, 107, 107, 0.15)" :
                    "var(--bg)",
                }}
                title={`${agent.agent_type}: ${agent.findings_count} findings, ${agent.confidence_score * 100}% confidence`}
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
        <ConfidenceBar score={investigation.confidence_score} />
      </div>

      {investigation.root_cause_hypothesis && (
        <div className="p-3 rounded-lg" style={{ backgroundColor: "var(--bg)" }}>
          <div className="text-xs uppercase tracking-wide mb-1" style={{ color: "var(--text-2)" }}>
            Root Cause Hypothesis
          </div>
          <p className="text-sm font-medium" style={{ color: "var(--text)" }}>
            {investigation.root_cause_hypothesis}
          </p>
        </div>
      )}

      {investigation.safety_evaluation && (
        <SafetyGatesCard evaluation={investigation.safety_evaluation} />
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
        {incident.investigation && (
          <div className="flex items-center gap-1">
            <span
              className="w-2 h-2 rounded-full"
              style={{
                backgroundColor: incident.investigation.status === "completed" ? "var(--ok)" :
                  incident.investigation.status === "running" ? "var(--accent)" :
                  "var(--text-2)",
                animation: incident.investigation.status === "running" ? "pulse 2s infinite" : "none",
              }}
            />
            <span className="text-xs" style={{ color: "var(--text)" }}>
              {Math.round(incident.investigation.confidence_score * 100)}%
            </span>
          </div>
        )}
      </td>
    </tr>
  );
}

function IncidentDetail({
  incident,
  onBack,
  onAcknowledge,
  onResolve
}: {
  incident: Incident;
  onBack: () => void;
  onAcknowledge: () => void;
  onResolve: () => void;
}) {
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
              onClick={onAcknowledge}
              className="px-4 py-2 rounded-lg font-medium"
              style={{ backgroundColor: "var(--accent)", color: "var(--bg)" }}
            >
              Acknowledge
            </button>
          )}
          {incident.status !== "resolved" && (
            <button
              onClick={onResolve}
              className="px-4 py-2 rounded-lg"
              style={{ border: "1px solid var(--ok)", color: "var(--ok)" }}
            >
              Resolve
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          {incident.investigation && (
            <InvestigationCard investigation={incident.investigation} />
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
              {incident.investigation && (
                <div className="flex gap-3">
                  <span
                    className="w-2 h-2 mt-1.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: "var(--accent)" }}
                  />
                  <div>
                    <div className="font-medium" style={{ color: "var(--text)" }}>AI investigation started</div>
                    <div style={{ color: "var(--text-2)" }}>6 parallel agents dispatched</div>
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

          <div
            className="rounded-xl p-4"
            style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
          >
            <h3 className="font-semibold mb-3" style={{ color: "var(--text)" }}>Quick Actions</h3>
            <div className="space-y-2">
              {["Rollback last deployment", "Scale up replicas", "Restart service", "Run diagnostic runbook"].map((action) => (
                <button
                  key={action}
                  className="w-full px-3 py-2 text-left text-sm rounded transition-colors"
                  style={{ border: "1px solid var(--border)", color: "var(--text)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
                >
                  {action}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Incidents({ onBack }: { onBack: () => void }) {
  const apiBaseUrl = useApiBaseUrl();
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<Incident | null>(null);
  const [filter, setFilter] = useState<"all" | "active" | "resolved">("active");
  const [loading, setLoading] = useState(true);

  const fetchIncidents = useCallback(async () => {
    try {
      const res = await fetch(`${apiBaseUrl}/api/incidents`);
      if (res.ok) {
        const data = await res.json();
        const transformed: Incident[] = data.map((inc: Record<string, unknown>) => ({
          id: inc.id,
          title: inc.title,
          severity: inc.severity || "medium",
          status: inc.status || "triggered",
          affected_services: inc.service ? [inc.service] : [],
          created_at: inc.triggered_at || new Date().toISOString(),
          updated_at: inc.acknowledged_at || inc.triggered_at || new Date().toISOString(),
          assigned_to: inc.assignee || undefined,
          investigation: inc.has_investigation ? {
            id: `inv-${inc.id}`,
            status: "running" as const,
            confidence_score: (inc.investigation_confidence as number) || 0,
            root_cause_hypothesis: "Investigation in progress...",
            agent_results: [],
            duration_ms: 0,
          } : undefined,
        }));
        setIncidents(transformed);
      }
    } catch (error) {
      console.error("Failed to fetch incidents:", error);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl]);

  useEffect(() => {
    fetchIncidents();
    const interval = setInterval(fetchIncidents, 10000);
    return () => clearInterval(interval);
  }, [fetchIncidents]);

  const handleAcknowledge = async (incidentId: string) => {
    try {
      await fetch(`${apiBaseUrl}/api/incidents/${incidentId}/acknowledge`, { method: "POST" });
      fetchIncidents();
      setSelectedIncident(null);
    } catch (error) {
      console.error("Failed to acknowledge incident:", error);
    }
  };

  const handleResolve = async (incidentId: string) => {
    try {
      await fetch(`${apiBaseUrl}/api/incidents/${incidentId}/resolve`, { method: "POST" });
      fetchIncidents();
      setSelectedIncident(null);
    } catch (error) {
      console.error("Failed to resolve incident:", error);
    }
  };

  const filteredIncidents = incidents.filter(inc => {
    if (filter === "active") return inc.status !== "resolved";
    if (filter === "resolved") return inc.status === "resolved";
    return true;
  });

  return (
    <div className="min-h-screen" style={{ backgroundColor: "var(--bg)" }}>
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
            className="px-4 py-2 rounded-lg font-medium flex items-center gap-2"
            style={{ backgroundColor: "var(--bad)", color: "white" }}
          >
            <Plus size={16} />
            Declare Incident
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">
        {selectedIncident ? (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <IncidentDetail
              incident={selectedIncident}
              onBack={() => setSelectedIncident(null)}
              onAcknowledge={() => handleAcknowledge(selectedIncident.id)}
              onResolve={() => handleResolve(selectedIncident.id)}
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
