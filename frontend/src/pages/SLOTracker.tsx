import { useState } from "react";
import { Icon } from "../components/Icon";

interface SLOAlert {
  id: string;
  type: "burn_rate" | "budget_low" | "budget_exhausted";
  severity: "warning" | "critical";
  message: string;
  triggered_at: string;
}

interface SLO {
  id: string;
  name: string;
  service: string;
  type: "availability" | "latency" | "error_rate" | "throughput";
  target: number;
  current: number;
  window: "30d" | "7d" | "1d";
  budget_total: number;
  budget_remaining: number;
  budget_burn_rate: number;
  status: "healthy" | "warning" | "critical" | "exhausted";
  alerts: SLOAlert[];
}

const DEMO_SLOS: SLO[] = [
  {
    id: "slo-1",
    name: "Checkout Availability",
    service: "checkout-service",
    type: "availability",
    target: 99.9,
    current: 99.85,
    window: "30d",
    budget_total: 43.2,
    budget_remaining: 28.4,
    budget_burn_rate: 1.2,
    status: "warning",
    alerts: [
      { id: "a1", type: "burn_rate", severity: "warning", message: "Burn rate 1.2x (threshold: 1x)", triggered_at: new Date(Date.now() - 3600000).toISOString() },
    ],
  },
  {
    id: "slo-2",
    name: "API Gateway P99 Latency",
    service: "api-gateway",
    type: "latency",
    target: 200,
    current: 156,
    window: "30d",
    budget_total: 100,
    budget_remaining: 78,
    budget_burn_rate: 0.7,
    status: "healthy",
    alerts: [],
  },
  {
    id: "slo-3",
    name: "Payment Error Rate",
    service: "payment-gateway",
    type: "error_rate",
    target: 0.1,
    current: 0.08,
    window: "30d",
    budget_total: 100,
    budget_remaining: 85,
    budget_burn_rate: 0.5,
    status: "healthy",
    alerts: [],
  },
  {
    id: "slo-4",
    name: "Search Availability",
    service: "search-service",
    type: "availability",
    target: 99.95,
    current: 99.2,
    window: "30d",
    budget_total: 21.6,
    budget_remaining: 2.1,
    budget_burn_rate: 3.5,
    status: "critical",
    alerts: [
      { id: "a2", type: "budget_low", severity: "critical", message: "Error budget < 10% remaining", triggered_at: new Date(Date.now() - 7200000).toISOString() },
      { id: "a3", type: "burn_rate", severity: "critical", message: "Burn rate 3.5x (threshold: 2x)", triggered_at: new Date(Date.now() - 1800000).toISOString() },
    ],
  },
  {
    id: "slo-5",
    name: "User Service Latency",
    service: "user-service",
    type: "latency",
    target: 100,
    current: 145,
    window: "30d",
    budget_total: 100,
    budget_remaining: 0,
    budget_burn_rate: 0,
    status: "exhausted",
    alerts: [
      { id: "a4", type: "budget_exhausted", severity: "critical", message: "Error budget exhausted for this window", triggered_at: new Date(Date.now() - 86400000).toISOString() },
    ],
  },
];

function StatusBadge({ status }: { status: SLO["status"] }) {
  const config = {
    healthy: { bg: "bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300", icon: "check" },
    warning: { bg: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/50 dark:text-yellow-300", icon: "alert-triangle" },
    critical: { bg: "bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300", icon: "alert-circle" },
    exhausted: { bg: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300", icon: "x-circle" },
  };
  const c = config[status];

  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded-full flex items-center gap-1 ${c.bg}`}>
      <Icon name={c.icon as "check" | "alert-triangle" | "alert-circle" | "x-circle"} size={12} />
      {status}
    </span>
  );
}

function BudgetGauge({ remaining, total, status }: { remaining: number; total: number; status: SLO["status"] }) {
  const pct = Math.max(0, Math.min(100, (remaining / total) * 100));
  const color = status === "healthy" ? "bg-green-500" :
                status === "warning" ? "bg-yellow-500" :
                status === "critical" ? "bg-red-500" : "bg-gray-400";

  return (
    <div className="w-full">
      <div className="flex justify-between text-xs mb-1">
        <span>Error Budget</span>
        <span className="font-mono">{pct.toFixed(1)}%</span>
      </div>
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full ${color} transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between text-xs text-gray-500 mt-1">
        <span>{remaining.toFixed(1)}m remaining</span>
        <span>{total.toFixed(1)}m total</span>
      </div>
    </div>
  );
}

function BurnRateIndicator({ rate }: { rate: number }) {
  const color = rate <= 1 ? "text-green-600" : rate <= 2 ? "text-yellow-600" : "text-red-600";
  const icon = rate <= 1 ? "check" : rate <= 2 ? "alert-triangle" : "alert-circle";

  return (
    <div className={`flex items-center gap-1 ${color}`}>
      <Icon name={icon as "check" | "alert-triangle" | "alert-circle"} size={14} />
      <span className="font-mono">{rate.toFixed(1)}x</span>
    </div>
  );
}

function SLOCard({ slo, onSelect }: { slo: SLO; onSelect: () => void }) {
  const typeLabels = {
    availability: "Availability",
    latency: "P99 Latency",
    error_rate: "Error Rate",
    throughput: "Throughput",
  };

  const formatValue = (value: number, type: SLO["type"]) => {
    if (type === "availability") return `${value.toFixed(2)}%`;
    if (type === "latency") return `${value}ms`;
    if (type === "error_rate") return `${value}%`;
    return `${value}/s`;
  };

  const isMet = slo.type === "latency" || slo.type === "error_rate"
    ? slo.current <= slo.target
    : slo.current >= slo.target;

  return (
    <div
      onClick={onSelect}
      className={`bg-white dark:bg-gray-800 rounded-lg border-2 p-4 cursor-pointer transition-all hover:shadow-lg ${
        slo.status === "healthy" ? "border-green-200 dark:border-green-800" :
        slo.status === "warning" ? "border-yellow-200 dark:border-yellow-800" :
        slo.status === "critical" ? "border-red-200 dark:border-red-800" :
        "border-gray-300 dark:border-gray-600"
      }`}
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-semibold">{slo.name}</h3>
          <div className="text-sm text-gray-500">{slo.service}</div>
        </div>
        <StatusBadge status={slo.status} />
      </div>

      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <div className="text-xs text-gray-500 uppercase">{typeLabels[slo.type]}</div>
          <div className={`text-2xl font-bold ${isMet ? "text-green-600" : "text-red-600"}`}>
            {formatValue(slo.current, slo.type)}
          </div>
          <div className="text-xs text-gray-500">
            Target: {formatValue(slo.target, slo.type)}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase">Burn Rate</div>
          <BurnRateIndicator rate={slo.budget_burn_rate} />
          <div className="text-xs text-gray-500">{slo.window} window</div>
        </div>
      </div>

      <BudgetGauge
        remaining={slo.budget_remaining}
        total={slo.budget_total}
        status={slo.status}
      />

      {slo.alerts.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
          <div className="text-xs text-red-600 dark:text-red-400">
            {slo.alerts.length} active alert{slo.alerts.length > 1 ? "s" : ""}
          </div>
        </div>
      )}
    </div>
  );
}

function SLODetailPanel({ slo, onClose }: { slo: SLO; onClose: () => void }) {
  return (
    <div className="fixed inset-y-0 right-0 w-96 bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 shadow-xl overflow-y-auto z-50">
      <div className="sticky top-0 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">{slo.name}</h3>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded">
            <Icon name="x" size={20} />
          </button>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-sm text-gray-500">{slo.service}</span>
          <StatusBadge status={slo.status} />
        </div>
      </div>

      <div className="p-4 space-y-6">
        <div>
          <h4 className="text-sm font-medium text-gray-500 mb-3">Error Budget</h4>
          <BudgetGauge remaining={slo.budget_remaining} total={slo.budget_total} status={slo.status} />
          <div className="mt-3 flex items-center justify-between text-sm">
            <span>Burn Rate</span>
            <BurnRateIndicator rate={slo.budget_burn_rate} />
          </div>
          <div className="mt-1 flex items-center justify-between text-sm">
            <span>Time to Exhaustion</span>
            <span className={slo.budget_remaining < 10 ? "text-red-600 font-bold" : ""}>
              {slo.budget_burn_rate > 0 ? `${Math.round(slo.budget_remaining / slo.budget_burn_rate)}h` : "N/A"}
            </span>
          </div>
        </div>

        {slo.alerts.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-gray-500 mb-3">Active Alerts</h4>
            <div className="space-y-2">
              {slo.alerts.map((alert) => (
                <div
                  key={alert.id}
                  className={`p-3 rounded ${
                    alert.severity === "critical"
                      ? "bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800"
                      : "bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800"
                  }`}
                >
                  <div className="font-medium text-sm">{alert.message}</div>
                  <div className="text-xs text-gray-500 mt-1">
                    {new Date(alert.triggered_at).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <h4 className="text-sm font-medium text-gray-500 mb-3">Actions</h4>
          <div className="space-y-2">
            <button className="w-full px-3 py-2 text-sm text-left border border-gray-200 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700">
              View Detailed Metrics
            </button>
            <button className="w-full px-3 py-2 text-sm text-left border border-gray-200 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700">
              Configure Alerts
            </button>
            {slo.status !== "healthy" && (
              <button className="w-full px-3 py-2 text-sm text-left bg-blue-600 text-white rounded hover:bg-blue-700">
                Investigate with AI
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CreateSLOModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">Create New SLO</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            <Icon name="x" size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Name</label>
            <input
              type="text"
              placeholder="e.g., Checkout Availability"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Service</label>
            <select className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700">
              <option>checkout-service</option>
              <option>api-gateway</option>
              <option>payment-gateway</option>
              <option>user-service</option>
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Type</label>
              <select className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700">
                <option value="availability">Availability</option>
                <option value="latency">Latency (P99)</option>
                <option value="error_rate">Error Rate</option>
                <option value="throughput">Throughput</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Window</label>
              <select className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700">
                <option value="30d">30 Days</option>
                <option value="7d">7 Days</option>
                <option value="1d">1 Day</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Target</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                step="0.01"
                placeholder="99.9"
                className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
              />
              <span className="text-gray-500">%</span>
            </div>
          </div>
        </div>

        <div className="flex gap-3 mt-6">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            Cancel
          </button>
          <button className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
            Create SLO
          </button>
        </div>
      </div>
    </div>
  );
}

export function SLOTracker({ onBack }: { onBack: () => void }) {
  const [slos] = useState<SLO[]>(DEMO_SLOS);
  const [selectedSLO, setSelectedSLO] = useState<SLO | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [filter, setFilter] = useState<"all" | "healthy" | "warning" | "critical">("all");

  const filteredSLOs = slos.filter(slo => {
    if (filter === "all") return true;
    if (filter === "warning") return slo.status === "warning";
    if (filter === "critical") return slo.status === "critical" || slo.status === "exhausted";
    return slo.status === filter;
  });

  const summary = {
    total: slos.length,
    healthy: slos.filter(s => s.status === "healthy").length,
    warning: slos.filter(s => s.status === "warning").length,
    critical: slos.filter(s => s.status === "critical" || s.status === "exhausted").length,
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                <Icon name="arrow-left" size={20} />
              </button>
              <h1 className="text-xl font-semibold">SLO Tracker</h1>
            </div>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              + Create SLO
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* Summary */}
        <div className="grid grid-cols-4 gap-4">
          <div
            onClick={() => setFilter("all")}
            className={`bg-white dark:bg-gray-800 rounded-lg border p-4 cursor-pointer transition-all ${
              filter === "all" ? "border-blue-500 ring-2 ring-blue-200" : "border-gray-200 dark:border-gray-700"
            }`}
          >
            <div className="text-sm text-gray-500">Total SLOs</div>
            <div className="text-3xl font-bold">{summary.total}</div>
          </div>
          <div
            onClick={() => setFilter("healthy")}
            className={`bg-white dark:bg-gray-800 rounded-lg border p-4 cursor-pointer transition-all ${
              filter === "healthy" ? "border-green-500 ring-2 ring-green-200" : "border-gray-200 dark:border-gray-700"
            }`}
          >
            <div className="text-sm text-gray-500">Healthy</div>
            <div className="text-3xl font-bold text-green-600">{summary.healthy}</div>
          </div>
          <div
            onClick={() => setFilter("warning")}
            className={`bg-white dark:bg-gray-800 rounded-lg border p-4 cursor-pointer transition-all ${
              filter === "warning" ? "border-yellow-500 ring-2 ring-yellow-200" : "border-gray-200 dark:border-gray-700"
            }`}
          >
            <div className="text-sm text-gray-500">Warning</div>
            <div className="text-3xl font-bold text-yellow-600">{summary.warning}</div>
          </div>
          <div
            onClick={() => setFilter("critical")}
            className={`bg-white dark:bg-gray-800 rounded-lg border p-4 cursor-pointer transition-all ${
              filter === "critical" ? "border-red-500 ring-2 ring-red-200" : "border-gray-200 dark:border-gray-700"
            }`}
          >
            <div className="text-sm text-gray-500">Critical</div>
            <div className="text-3xl font-bold text-red-600">{summary.critical}</div>
          </div>
        </div>

        {/* SLO Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredSLOs.map((slo) => (
            <SLOCard key={slo.id} slo={slo} onSelect={() => setSelectedSLO(slo)} />
          ))}
        </div>
      </div>

      {/* Detail Panel */}
      {selectedSLO && (
        <SLODetailPanel slo={selectedSLO} onClose={() => setSelectedSLO(null)} />
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <CreateSLOModal onClose={() => setShowCreateModal(false)} />
      )}
    </div>
  );
}
