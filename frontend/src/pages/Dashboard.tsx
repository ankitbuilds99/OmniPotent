import { useState } from "react";
import { Icon } from "../components/Icon";

type Tab = "overview" | "alerts" | "runbooks" | "metrics";

interface Alert {
  id: string;
  rule_name: string;
  service: string;
  severity: string;
  status: string;
  message: string;
  started_at: string;
}

interface Runbook {
  id: string;
  name: string;
  description: string;
  service: string;
  trigger: string;
  execution_mode: string;
  step_count: number;
}

interface Execution {
  id: string;
  runbook_name: string;
  status: string;
  triggered_by: string;
  started_at: string;
}

const DEMO_ALERTS: Alert[] = [
  {
    id: "1",
    rule_name: "High Error Rate",
    service: "checkout-svc",
    severity: "high",
    status: "firing",
    message: "Error rate is 8.5%, above threshold 5%",
    started_at: new Date().toISOString(),
  },
  {
    id: "2",
    rule_name: "High P99 Latency",
    service: "api-gateway",
    severity: "medium",
    status: "firing",
    message: "P99 latency is 2.3s, above threshold 2s",
    started_at: new Date(Date.now() - 600000).toISOString(),
  },
];

const DEMO_RUNBOOKS: Runbook[] = [
  {
    id: "restart-service",
    name: "Restart Service",
    description: "Restart a Kubernetes deployment by rolling out new pods",
    service: "*",
    trigger: "manual",
    execution_mode: "requires_approval",
    step_count: 4,
  },
  {
    id: "scale-up",
    name: "Scale Up Deployment",
    description: "Increase replica count to handle load",
    service: "*",
    trigger: "alert:high-cpu-usage",
    execution_mode: "auto_approved",
    step_count: 3,
  },
  {
    id: "rollback-deployment",
    name: "Rollback Deployment",
    description: "Rollback to previous deployment revision",
    service: "*",
    trigger: "alert:deployment-failed",
    execution_mode: "requires_approval",
    step_count: 4,
  },
];

const DEMO_EXECUTIONS: Execution[] = [
  {
    id: "exec-1",
    runbook_name: "Scale Up Deployment",
    status: "success",
    triggered_by: "alert:high-cpu-usage",
    started_at: new Date(Date.now() - 3600000).toISOString(),
  },
  {
    id: "exec-2",
    runbook_name: "Restart Service",
    status: "waiting_approval",
    triggered_by: "manual",
    started_at: new Date(Date.now() - 300000).toISOString(),
  },
];

function SeverityBadge({ severity }: { severity: string }) {
  const colors: Record<string, string> = {
    critical: "bg-red-500",
    high: "bg-orange-500",
    medium: "bg-yellow-500",
    low: "bg-green-500",
    info: "bg-blue-500",
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
    success: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    failed: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    running: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    waiting_approval: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
    pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  };
  return (
    <span className={`px-2 py-0.5 text-xs rounded-full ${colors[status] || "bg-gray-100"}`}>
      {status.replace("_", " ")}
    </span>
  );
}

function OverviewTab() {
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
          <p className="text-2xl font-bold mt-2">{DEMO_ALERTS.length}</p>
          <p className="text-xs text-gray-500 mt-1">1 critical, 1 high</p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500 dark:text-gray-400">Runbooks</span>
            <span className="p-2 bg-blue-100 dark:bg-blue-900 rounded-lg">
              <Icon name="book" size={16} />
            </span>
          </div>
          <p className="text-2xl font-bold mt-2">{DEMO_RUNBOOKS.length}</p>
          <p className="text-xs text-gray-500 mt-1">5 available</p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500 dark:text-gray-400">Pending Approvals</span>
            <span className="p-2 bg-yellow-100 dark:bg-yellow-900 rounded-lg">
              <Icon name="clock" size={16} />
            </span>
          </div>
          <p className="text-2xl font-bold mt-2">1</p>
          <p className="text-xs text-gray-500 mt-1">Awaiting action</p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-500 dark:text-gray-400">Services</span>
            <span className="p-2 bg-green-100 dark:bg-green-900 rounded-lg">
              <Icon name="server" size={16} />
            </span>
          </div>
          <p className="text-2xl font-bold mt-2">12</p>
          <p className="text-xs text-gray-500 mt-1">11 healthy</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700">
            <h3 className="font-semibold">Recent Alerts</h3>
          </div>
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {DEMO_ALERTS.slice(0, 3).map((alert) => (
              <div key={alert.id} className="p-4 flex items-center justify-between">
                <div>
                  <p className="font-medium">{alert.rule_name}</p>
                  <p className="text-sm text-gray-500">{alert.service}</p>
                </div>
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={alert.severity} />
                  <StatusBadge status={alert.status} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700">
            <h3 className="font-semibold">Recent Executions</h3>
          </div>
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {DEMO_EXECUTIONS.map((exec) => (
              <div key={exec.id} className="p-4 flex items-center justify-between">
                <div>
                  <p className="font-medium">{exec.runbook_name}</p>
                  <p className="text-sm text-gray-500">{exec.triggered_by}</p>
                </div>
                <StatusBadge status={exec.status} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function AlertsTab() {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
      <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <h3 className="font-semibold">Active Alerts</h3>
        <button className="text-sm text-blue-600 hover:text-blue-700 dark:text-blue-400">
          View All Rules
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-gray-50 dark:bg-gray-900">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Alert</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Service</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Severity</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Message</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Started</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {DEMO_ALERTS.map((alert) => (
              <tr key={alert.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                <td className="px-4 py-3 font-medium">{alert.rule_name}</td>
                <td className="px-4 py-3 text-sm">{alert.service}</td>
                <td className="px-4 py-3"><SeverityBadge severity={alert.severity} /></td>
                <td className="px-4 py-3"><StatusBadge status={alert.status} /></td>
                <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-400 max-w-xs truncate">{alert.message}</td>
                <td className="px-4 py-3 text-sm text-gray-500">{new Date(alert.started_at).toLocaleTimeString()}</td>
                <td className="px-4 py-3">
                  <button className="text-sm text-blue-600 hover:text-blue-700 dark:text-blue-400">
                    Investigate
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RunbooksTab() {
  return (
    <div className="space-y-6">
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 className="font-semibold">Available Runbooks</h3>
          <button className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">
            Create Runbook
          </button>
        </div>
        <div className="divide-y divide-gray-200 dark:divide-gray-700">
          {DEMO_RUNBOOKS.map((runbook) => (
            <div key={runbook.id} className="p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <h4 className="font-medium">{runbook.name}</h4>
                  <p className="text-sm text-gray-500 mt-1">{runbook.description}</p>
                  <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                    <span>Trigger: {runbook.trigger}</span>
                    <span>Mode: {runbook.execution_mode.replace("_", " ")}</span>
                    <span>{runbook.step_count} steps</span>
                  </div>
                </div>
                <button className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700">
                  Execute
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <div className="p-4 border-b border-gray-200 dark:border-gray-700">
          <h3 className="font-semibold">Pending Approvals</h3>
        </div>
        {DEMO_EXECUTIONS.filter(e => e.status === "waiting_approval").length > 0 ? (
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {DEMO_EXECUTIONS.filter(e => e.status === "waiting_approval").map((exec) => (
              <div key={exec.id} className="p-4 flex items-center justify-between">
                <div>
                  <p className="font-medium">{exec.runbook_name}</p>
                  <p className="text-sm text-gray-500">Triggered by {exec.triggered_by}</p>
                </div>
                <div className="flex gap-2">
                  <button className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700">
                    Approve
                  </button>
                  <button className="px-3 py-1.5 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700">
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-8 text-center text-gray-500">
            No pending approvals
          </div>
        )}
      </div>
    </div>
  );
}

function MetricsTab() {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8">
      <div className="text-center">
        <Icon name="chart" size={48} />
        <h3 className="mt-4 text-lg font-medium">Metrics Dashboard</h3>
        <p className="mt-2 text-gray-500">
          Connect to Datadog, Prometheus, or CloudWatch to view metrics.
        </p>
        <button className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
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
}

export function Dashboard({ onBack, onBilling: _onBilling, onIncidents, onOnCall, onServiceMap, onNotifications }: DashboardProps) {
  const [activeTab, setActiveTab] = useState<Tab>("overview");

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
              <span className="text-sm text-gray-500">Last updated: {new Date().toLocaleTimeString()}</span>
              <button className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">
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

        {activeTab === "overview" && <OverviewTab />}
        {activeTab === "alerts" && <AlertsTab />}
        {activeTab === "runbooks" && <RunbooksTab />}
        {activeTab === "metrics" && <MetricsTab />}
      </div>
    </div>
  );
}
