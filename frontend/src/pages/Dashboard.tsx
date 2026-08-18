import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";
import { API_BASE_URL } from "../config";

// Same resolution as every other caller: `|| ""` here vs `??` elsewhere meant two
// different behaviours for one concept.
const API_BASE = API_BASE_URL;

type Tab = "overview" | "alerts" | "runbooks" | "metrics";

interface Alert {
  id: string;
  title: string;
  description: string | null;
  severity: string;
  status: string;
  source: string | null;
  triggered_at: string;
  team_id: string;
}

interface Runbook {
  id: string;
  name: string;
  description: string;
  service: string;
  trigger: string;
  execution_mode: string;
  step_count: number;
  labels: Record<string, string>;
  enabled: boolean;
}

interface Execution {
  id: string;
  runbook_id: string;
  runbook_name: string;
  status: string;
  triggered_by: string;
  started_at: string;
  completed_at: string | null;
}

interface DashboardStats {
  activeAlerts: number;
  criticalAlerts: number;
  highAlerts: number;
  runbookCount: number;
  pendingApprovals: number;
  serviceCount: number;
  healthyServices: number;
}

function SeverityBadge({ severity }: { severity: string }) {
  const colors: Record<string, string> = {
    critical: "bg-red-500",
    high: "bg-orange-500",
    medium: "bg-yellow-500",
    low: "bg-green-500",
    info: "bg-blue-500",
    warning: "bg-yellow-500",
  };
  return (
    <span className={`px-2 py-0.5 text-xs rounded-full text-white ${colors[severity] || "bg-gray-500"}`}>
      {severity}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    firing: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    triggered: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    acknowledged: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
    resolved: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    success: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    failed: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    running: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    waiting_approval: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
    pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  };
  return (
    <span className={`px-2 py-0.5 text-xs rounded-full ${colors[status] || "bg-gray-100"}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center p-8">
      <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function EmptyState({ message, icon }: { message: string; icon: string }) {
  return (
    <div className="p-8 text-center text-gray-500">
      <Icon name={icon} size={32} />
      <p className="mt-2">{message}</p>
    </div>
  );
}

function OverviewTab({
  alerts,
  runbooks,
  executions,
  stats,
  loading
}: {
  alerts: Alert[];
  runbooks: Runbook[];
  executions: Execution[];
  stats: DashboardStats;
  loading: boolean;
}) {
  if (loading) return <LoadingSpinner />;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500 dark:text-gray-400">Active Alerts</span>
            <span className="p-2 bg-red-100 dark:bg-red-900 rounded-lg">
              <Icon name="warning" size={16} />
            </span>
          </div>
          <p className="text-2xl font-bold mt-2">{stats.activeAlerts}</p>
          <p className="text-xs text-gray-500 mt-1">
            {stats.criticalAlerts} critical, {stats.highAlerts} high
          </p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500 dark:text-gray-400">Runbooks</span>
            <span className="p-2 bg-blue-100 dark:bg-blue-900 rounded-lg">
              <Icon name="book" size={16} />
            </span>
          </div>
          <p className="text-2xl font-bold mt-2">{stats.runbookCount}</p>
          <p className="text-xs text-gray-500 mt-1">{runbooks.filter(r => r.enabled).length} available</p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500 dark:text-gray-400">Pending Approvals</span>
            <span className="p-2 bg-yellow-100 dark:bg-yellow-900 rounded-lg">
              <Icon name="clock" size={16} />
            </span>
          </div>
          <p className="text-2xl font-bold mt-2">{stats.pendingApprovals}</p>
          <p className="text-xs text-gray-500 mt-1">Awaiting action</p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500 dark:text-gray-400">Services</span>
            <span className="p-2 bg-green-100 dark:bg-green-900 rounded-lg">
              <Icon name="server" size={16} />
            </span>
          </div>
          <p className="text-2xl font-bold mt-2">{stats.serviceCount}</p>
          <p className="text-xs text-gray-500 mt-1">{stats.healthyServices} healthy</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700">
            <h3 className="font-semibold">Recent Alerts</h3>
          </div>
          {alerts.length > 0 ? (
            <div className="divide-y divide-gray-200 dark:divide-gray-700">
              {alerts.slice(0, 3).map((alert) => (
                <div key={alert.id} className="p-4 flex items-center justify-between">
                  <div>
                    <p className="font-medium">{alert.title}</p>
                    <p className="text-sm text-gray-500">{alert.source || "System"}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={alert.severity} />
                    <StatusBadge status={alert.status} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState message="No active alerts" icon="check" />
          )}
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700">
            <h3 className="font-semibold">Recent Executions</h3>
          </div>
          {executions.length > 0 ? (
            <div className="divide-y divide-gray-200 dark:divide-gray-700">
              {executions.slice(0, 3).map((exec) => (
                <div key={exec.id} className="p-4 flex items-center justify-between">
                  <div>
                    <p className="font-medium">{exec.runbook_name}</p>
                    <p className="text-sm text-gray-500">{exec.triggered_by}</p>
                  </div>
                  <StatusBadge status={exec.status} />
                </div>
              ))}
            </div>
          ) : (
            <EmptyState message="No recent executions" icon="book" />
          )}
        </div>
      </div>
    </div>
  );
}

function AlertsTab({
  alerts,
  loading,
  onAcknowledge,
  onResolve
}: {
  alerts: Alert[];
  loading: boolean;
  onAcknowledge: (id: string) => void;
  onResolve: (id: string) => void;
}) {
  if (loading) return <LoadingSpinner />;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
      <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <h3 className="font-semibold">Active Alerts</h3>
        <button className="text-sm text-blue-600 hover:text-blue-700 dark:text-blue-400">
          View All Rules
        </button>
      </div>
      {alerts.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Alert</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Source</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Severity</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Started</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {alerts.map((alert) => (
                <tr key={alert.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                  <td className="px-4 py-3 font-medium">{alert.title}</td>
                  <td className="px-4 py-3 text-sm">{alert.source || "System"}</td>
                  <td className="px-4 py-3"><SeverityBadge severity={alert.severity} /></td>
                  <td className="px-4 py-3"><StatusBadge status={alert.status} /></td>
                  <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-400 max-w-xs truncate">
                    {alert.description || "-"}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {new Date(alert.triggered_at).toLocaleTimeString()}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      {alert.status === "triggered" && (
                        <button
                          onClick={() => onAcknowledge(alert.id)}
                          className="text-sm text-yellow-600 hover:text-yellow-700"
                        >
                          Ack
                        </button>
                      )}
                      {alert.status !== "resolved" && (
                        <button
                          onClick={() => onResolve(alert.id)}
                          className="text-sm text-green-600 hover:text-green-700"
                        >
                          Resolve
                        </button>
                      )}
                      <button className="text-sm text-blue-600 hover:text-blue-700 dark:text-blue-400">
                        Investigate
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState message="No active alerts - all systems operational" icon="check" />
      )}
    </div>
  );
}

function RunbooksTab({
  runbooks,
  executions,
  loading,
  onExecute,
  onApprove,
  onReject,
  onCreateRunbook
}: {
  runbooks: Runbook[];
  executions: Execution[];
  loading: boolean;
  onExecute: (id: string) => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onCreateRunbook: () => void;
}) {
  if (loading) return <LoadingSpinner />;

  const pendingApprovals = executions.filter(e =>
    e.status === "waiting_approval" || e.status === "pending"
  );

  return (
    <div className="space-y-6">
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 className="font-semibold">Available Runbooks</h3>
          <button
            onClick={onCreateRunbook}
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            Create Runbook
          </button>
        </div>
        {runbooks.length > 0 ? (
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {runbooks.map((runbook) => (
              <div key={runbook.id} className="p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <h4 className="font-medium">{runbook.name}</h4>
                    <p className="text-sm text-gray-500 mt-1">{runbook.description}</p>
                    <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                      <span>Trigger: {runbook.trigger}</span>
                      <span>Mode: {runbook.execution_mode.replace(/_/g, " ")}</span>
                      <span>{runbook.step_count} steps</span>
                    </div>
                  </div>
                  <button
                    onClick={() => onExecute(runbook.id)}
                    disabled={!runbook.enabled}
                    className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Execute
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState message="No runbooks configured yet" icon="book" />
        )}
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <div className="p-4 border-b border-gray-200 dark:border-gray-700">
          <h3 className="font-semibold">Pending Approvals</h3>
        </div>
        {pendingApprovals.length > 0 ? (
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {pendingApprovals.map((exec) => (
              <div key={exec.id} className="p-4 flex items-center justify-between">
                <div>
                  <p className="font-medium">{exec.runbook_name}</p>
                  <p className="text-sm text-gray-500">Triggered by {exec.triggered_by}</p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => onApprove(exec.id)}
                    className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => onReject(exec.id)}
                    className="px-3 py-1.5 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700"
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState message="No pending approvals" icon="check" />
        )}
      </div>
    </div>
  );
}

function MetricsTab({ onIntegrations }: { onIntegrations?: () => void }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8">
      <div className="text-center">
        <Icon name="chart" size={48} />
        <h3 className="mt-4 text-lg font-medium">Metrics Dashboard</h3>
        <p className="mt-2 text-gray-500">
          Connect to Datadog, Prometheus, or CloudWatch to view metrics.
        </p>
        <button
          onClick={onIntegrations}
          className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          Configure Integrations
        </button>
      </div>
    </div>
  );
}

interface DashboardProps {
  onBack: () => void;
  onBilling?: () => void;
  onIncidents?: () => void;
  onOnCall?: () => void;
  onServiceMap?: () => void;
  onNotifications?: () => void;
  onPostmortem?: () => void;
  onAnalytics?: () => void;
  onSLO?: () => void;
  onRunbooks?: () => void;
  onIntegrations?: () => void;
  onKnowledge?: () => void;
}

export function Dashboard({
  onBack,
  onBilling: _onBilling,
  onIncidents,
  onOnCall,
  onServiceMap,
  onNotifications,
  onPostmortem,
  onAnalytics,
  onSLO,
  onRunbooks,
  onIntegrations,
  onKnowledge
}: DashboardProps) {
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [runbooks, setRunbooks] = useState<Runbook[]>([]);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(new Date());

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [alertsRes, runbooksRes, executionsRes] = await Promise.allSettled([
        fetch(`${API_BASE}/oncall/alerts`),
        fetch(`${API_BASE}/api/runbooks`),
        fetch(`${API_BASE}/api/runbooks/executions`),
      ]);

      if (alertsRes.status === "fulfilled" && alertsRes.value.ok) {
        const data = await alertsRes.value.json();
        setAlerts(data);
      }

      if (runbooksRes.status === "fulfilled" && runbooksRes.value.ok) {
        const data = await runbooksRes.value.json();
        setRunbooks(data);
      }

      if (executionsRes.status === "fulfilled" && executionsRes.value.ok) {
        const data = await executionsRes.value.json();
        setExecutions(data);
      }

      setLastUpdated(new Date());
    } catch (error) {
      console.error("Failed to fetch dashboard data:", error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const serviceSet = new Set(runbooks.map(r => r.service).filter(s => s && s !== "*"));
  const stats: DashboardStats = {
    activeAlerts: alerts.filter(a => a.status !== "resolved").length,
    criticalAlerts: alerts.filter(a => a.severity === "critical" && a.status !== "resolved").length,
    highAlerts: alerts.filter(a => a.severity === "high" && a.status !== "resolved").length,
    runbookCount: runbooks.length,
    pendingApprovals: executions.filter(e => e.status === "waiting_approval" || e.status === "pending").length,
    serviceCount: serviceSet.size,
    healthyServices: serviceSet.size,
  };

  const handleAcknowledgeAlert = async (alertId: string) => {
    try {
      const res = await fetch(`${API_BASE}/oncall/alerts/${alertId}/acknowledge`, { method: "POST" });
      if (res.ok) {
        fetchData();
      }
    } catch (error) {
      console.error("Failed to acknowledge alert:", error);
    }
  };

  const handleResolveAlert = async (alertId: string) => {
    try {
      const res = await fetch(`${API_BASE}/oncall/alerts/${alertId}/resolve`, { method: "POST" });
      if (res.ok) {
        fetchData();
      }
    } catch (error) {
      console.error("Failed to resolve alert:", error);
    }
  };

  const handleExecuteRunbook = async (runbookId: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/runbooks/${runbookId}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        fetchData();
      }
    } catch (error) {
      console.error("Failed to execute runbook:", error);
    }
  };

  const handleApproveExecution = async (executionId: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/runbooks/executions/${executionId}/approve`, { method: "POST" });
      if (res.ok) {
        fetchData();
      }
    } catch (error) {
      console.error("Failed to approve execution:", error);
    }
  };

  const handleRejectExecution = async (executionId: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/runbooks/executions/${executionId}/reject`, { method: "POST" });
      if (res.ok) {
        fetchData();
      }
    } catch (error) {
      console.error("Failed to reject execution:", error);
    }
  };

  const tabs: { id: Tab; label: string; icon: string }[] = [
    { id: "overview", label: "Overview", icon: "dashboard" },
    { id: "alerts", label: "Alerts", icon: "warning" },
    { id: "runbooks", label: "Runbooks", icon: "book" },
    { id: "metrics", label: "Metrics", icon: "chart" },
  ];

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                <Icon name="arrow-left" size={20} />
              </button>
              <h1 className="text-xl font-semibold">DevOps Dashboard</h1>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-500">Last updated: {lastUpdated.toLocaleTimeString()}</span>
              <button
                onClick={fetchData}
                className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
              >
                <Icon name="refresh" size={16} />
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Quick Navigation */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          {onIncidents && (
            <button
              onClick={onIncidents}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-500 transition-colors"
            >
              <Icon name="warning" size={18} />
              <span className="font-medium">Incidents</span>
            </button>
          )}
          {onOnCall && (
            <button
              onClick={onOnCall}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-500 transition-colors"
            >
              <Icon name="user" size={18} />
              <span className="font-medium">On-Call</span>
            </button>
          )}
          {onServiceMap && (
            <button
              onClick={onServiceMap}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-500 transition-colors"
            >
              <Icon name="server" size={18} />
              <span className="font-medium">Service Map</span>
            </button>
          )}
          {onNotifications && (
            <button
              onClick={onNotifications}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-500 transition-colors"
            >
              <Icon name="bell" size={18} />
              <span className="font-medium">Notifications</span>
            </button>
          )}
          {onAnalytics && (
            <button
              onClick={onAnalytics}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-green-500 transition-colors"
            >
              <Icon name="chart" size={18} />
              <span className="font-medium">Analytics</span>
            </button>
          )}
          {onSLO && (
            <button
              onClick={onSLO}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-orange-500 transition-colors"
            >
              <Icon name="target" size={18} />
              <span className="font-medium">SLOs</span>
            </button>
          )}
          {onRunbooks && (
            <button
              onClick={onRunbooks}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-purple-500 transition-colors"
            >
              <Icon name="book" size={18} />
              <span className="font-medium">Runbooks</span>
            </button>
          )}
          {onPostmortem && (
            <button
              onClick={onPostmortem}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-gray-500 transition-colors"
            >
              <Icon name="file-text" size={18} />
              <span className="font-medium">Postmortems</span>
            </button>
          )}
          {onIntegrations && (
            <button
              onClick={onIntegrations}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-indigo-500 transition-colors"
            >
              <Icon name="zap" size={18} />
              <span className="font-medium">Integrations</span>
            </button>
          )}
          {onKnowledge && (
            <button
              onClick={onKnowledge}
              className="flex items-center gap-2 p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-teal-500 transition-colors"
            >
              <Icon name="book" size={18} />
              <span className="font-medium">Knowledge Base</span>
            </button>
          )}
        </div>

        <div className="flex gap-2 mb-6 border-b border-gray-200 dark:border-gray-700">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === tab.id
                  ? "border-blue-500 text-blue-600 dark:text-blue-400"
                  : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              }`}
            >
              <span className="flex items-center gap-2">
                <Icon name={tab.icon} size={16} />
                {tab.label}
              </span>
            </button>
          ))}
        </div>

        {activeTab === "overview" && (
          <OverviewTab
            alerts={alerts}
            runbooks={runbooks}
            executions={executions}
            stats={stats}
            loading={loading}
          />
        )}
        {activeTab === "alerts" && (
          <AlertsTab
            alerts={alerts}
            loading={loading}
            onAcknowledge={handleAcknowledgeAlert}
            onResolve={handleResolveAlert}
          />
        )}
        {activeTab === "runbooks" && (
          <RunbooksTab
            runbooks={runbooks}
            executions={executions}
            loading={loading}
            onExecute={handleExecuteRunbook}
            onApprove={handleApproveExecution}
            onReject={handleRejectExecution}
            onCreateRunbook={() => onRunbooks?.()}
          />
        )}
        {activeTab === "metrics" && <MetricsTab onIntegrations={onIntegrations} />}
      </div>
    </div>
  );
}
