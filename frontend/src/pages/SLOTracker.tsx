/**
 * SLO Tracker - Real API integration
 */

import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";

// No local API base any more: apiFetch resolves it the same way for every caller
// (`|| ""` here vs `??` elsewhere meant two different behaviours for one concept)
// and attaches the Authorization header these calls were missing.
import { apiFetch, describeFailure } from "../lib/apiClient";

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
  /** The measured SLI as a percentage, or null when nothing has been measured. */
  current: number | null;
  window: "30d" | "7d" | "1d";
  /** Minutes of the window, or null when the budget could not be read. */
  budget_total: number | null;
  budget_remaining: number | null;
  /** Null when nothing has been measured: 0 is a burn rate, and a service
   *  burning none of its budget is not the same as one nobody is watching. */
  budget_burn_rate: number | null;
  /** When the tracker projects the budget will be gone, ISO-8601. Null when it
   *  makes no projection — nothing burning, nothing measured, or already gone. */
  projected_exhaustion: string | null;
  /** The burn rate at which this SLO alerts, or null if the API omitted it. */
  alert_burn_rate_threshold: number | null;
  /** SLI samples behind `current`; 0 means the SLO exists but has no data yet. */
  sample_count: number;
  /** `no_data` is a real answer from the tracker, not an absence of one: the SLO
   *  exists but nothing has recorded an SLI against it in the window. It used to
   *  fall through to `healthy`, which is the one reading it definitely is not —
   *  an SLO nobody is measuring looked identical to one that is passing. */
  status: "healthy" | "warning" | "critical" | "exhausted" | "no_data";
  alerts: SLOAlert[];
}

/** The shape of GET /api/slos/{id}/budget. */
interface BudgetResponse {
  remaining: number;
  remaining_percent: number;
  burn_rate: number;
  status: string;
  current_sli: number;
  good_count: number;
  total_count: number;
  budget_minutes_total: number;
  budget_minutes_remaining: number;
  /** The tracker's own projection, which this page used to ignore in favour of
   *  dividing budget minutes by a dimensionless multiplier. */
  projected_exhaustion: string | null;
}

/** The shape of GET /api/slos/{id}/burn-rate. */
interface BurnRateWindows {
  short_window: string;
  short_window_burn_rate: number;
  long_window: string;
  long_window_burn_rate: number;
  is_alerting: boolean;
  severity: string;
  message: string;
}

function StatusBadge({ status }: { status: SLO["status"] }) {
  const config = {
    healthy: { bg: "bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300", icon: "check", label: "healthy" },
    warning: { bg: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/50 dark:text-yellow-300", icon: "alert-triangle", label: "warning" },
    critical: { bg: "bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300", icon: "alert-circle", label: "critical" },
    exhausted: { bg: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300", icon: "x-circle", label: "exhausted" },
    // Blue, not green and not red: nothing is wrong with the service and nothing
    // is known to be right with it either. The label is spelled out because the
    // badge used to render the raw key, which would have read "no_data".
    no_data: { bg: "bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300", icon: "help", label: "no data" },
  };
  const c = config[status];

  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded-full flex items-center gap-1 ${c.bg}`}>
      <Icon name={c.icon as "check" | "alert-triangle" | "alert-circle" | "x-circle" | "help"} size={12} />
      {c.label}
    </span>
  );
}

function BudgetGauge({ remaining, total, status }: { remaining: number | null; total: number | null; status: SLO["status"] }) {
  if (remaining === null || total === null) {
    return (
      <div className="w-full text-xs text-gray-500">
        Error budget unavailable — the budget endpoint could not be read.
      </div>
    );
  }
  if (status === "no_data") {
    // An untouched budget is arithmetically full, and drawing it as a complete
    // green bar says "you have spent none of your allowance" when the truth is
    // "nothing has been measured". Those look the same on a gauge and mean
    // opposite things to whoever is deciding whether it is safe to ship.
    return (
      <div className="w-full text-xs text-gray-500">
        No SLI recorded in this window — the budget cannot be computed yet.
      </div>
    );
  }
  const pct = total > 0 ? Math.max(0, Math.min(100, (remaining / total) * 100)) : 0;
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

function BurnRateIndicator({ rate }: { rate: number | null }) {
  if (rate === null) {
    // 0.0x is a measurement — a service that is burning none of its allowance.
    // Falling back to it when the budget could not be read put a green tick
    // beside "we have no idea how fast this is burning".
    return (
      <div className="flex items-center gap-1 text-gray-500">
        <Icon name="help" size={14} />
        <span className="font-mono">no data</span>
      </div>
    );
  }

  const color = rate <= 1 ? "text-green-600" : rate <= 2 ? "text-yellow-600" : "text-red-600";
  const icon = rate <= 1 ? "check" : rate <= 2 ? "alert-triangle" : "alert-circle";

  return (
    <div className={`flex items-center gap-1 ${color}`}>
      <Icon name={icon as "check" | "alert-triangle" | "alert-circle"} size={14} />
      <span className="font-mono">{rate.toFixed(1)}x</span>
    </div>
  );
}

/** "3d 4h", "5h 12m", "40m" — coarse enough to read at a glance. */
function formatDuration(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    const rem = minutes % 60;
    return rem > 0 ? `${hours}h ${rem}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const rem = hours % 24;
  return rem > 0 ? `${days}d ${rem}h` : `${days}d`;
}

/**
 * The old estimate divided the remaining budget *in minutes* by the burn rate,
 * a dimensionless multiplier, and labelled the result "h" — so a budget with
 * 300 minutes left burning at 2x read "150h" instead of the 2.5 hours it had.
 * The tracker already computes the projection; show that.
 */
function ExhaustionEstimate({ slo }: { slo: SLO }) {
  if (slo.status === "exhausted" || (slo.budget_remaining !== null && slo.budget_remaining <= 0)) {
    return <span className="text-red-600 font-bold">already exhausted</span>;
  }
  if (!slo.projected_exhaustion) {
    // The tracker only projects while a measured budget is actually burning.
    return <span className="text-gray-500">not projected</span>;
  }

  const at = new Date(slo.projected_exhaustion);
  const ms = at.getTime() - Date.now();
  if (ms <= 0) {
    return <span className="text-red-600 font-bold" title={at.toLocaleString()}>due now</span>;
  }

  return (
    <span
      className={ms <= 24 * 60 * 60 * 1000 ? "text-red-600 font-bold" : ""}
      title={at.toLocaleString()}
    >
      in {formatDuration(ms)}
    </span>
  );
}

function SLOCard({ slo, onSelect }: { slo: SLO; onSelect: () => void }) {
  const typeLabels = {
    availability: "Availability",
    latency: "P99 Latency",
    error_rate: "Error Rate",
    throughput: "Throughput",
  };

  // A percentage for every SLI type: the tracker measures good/total, so a
  // latency SLO's number is "% of requests within the threshold", not a
  // millisecond figure. Rendering it as `250ms` when the value is 99.4 was how
  // a healthy latency SLO came to read as "99.4ms".
  const formatValue = (value: number) => `${value.toFixed(2)}%`;

  // An SLO with no samples is neither met nor missed, and colouring it either
  // way asserts something the data does not say.
  //
  // Higher is better for every type: `current` is good/total whatever the SLI
  // measures, so an error-rate SLO's number is the share of requests that did
  // *not* error. The comparison used to be inverted for latency and error_rate,
  // which was correct only for the raw millisecond/error-percentage readings
  // this page never actually had — so those two types showed green exactly when
  // they were failing.
  const isMet = slo.current === null ? null : slo.current >= slo.target;

  return (
    <div
      onClick={onSelect}
      className={`bg-white dark:bg-gray-800 rounded-lg border-2 p-4 cursor-pointer transition-all hover:shadow-lg ${
        slo.status === "healthy" ? "border-green-200 dark:border-green-800" :
        slo.status === "warning" ? "border-yellow-200 dark:border-yellow-800" :
        slo.status === "critical" ? "border-red-200 dark:border-red-800" :
        slo.status === "no_data" ? "border-blue-200 dark:border-blue-800" :
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
          <div
            className={`text-2xl font-bold ${
              isMet === null ? "text-gray-400" : isMet ? "text-green-600" : "text-red-600"
            }`}
          >
            {slo.current === null ? "No data" : formatValue(slo.current)}
          </div>
          <div className="text-xs text-gray-500">
            Target: {formatValue(slo.target)}
            {slo.current !== null && ` · ${slo.sample_count.toLocaleString()} samples`}
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

function SLODetailPanel({
  slo,
  onClose,
  onChanged,
}: {
  slo: SLO;
  onClose: () => void;
  /** Re-read the SLO list after something on the server changes. */
  onChanged: () => void;
}) {
  // All three of these buttons used to be decorative: no onClick, no handler,
  // nothing. Clicking them looked like nothing had loaded yet.
  const [busy, setBusy] = useState<null | "metrics" | "alerts" | "investigate">(null);

  const [metrics, setMetrics] = useState<BurnRateWindows | null>(null);
  const [metricsError, setMetricsError] = useState<string | null>(null);

  const [editingThreshold, setEditingThreshold] = useState(false);
  const [threshold, setThreshold] = useState("");
  const [thresholdError, setThresholdError] = useState<string | null>(null);

  const [investigation, setInvestigation] = useState<string | null>(null);
  const [investigateError, setInvestigateError] = useState<string | null>(null);

  const toggleMetrics = async () => {
    if (metrics) {
      setMetrics(null);
      setMetricsError(null);
      return;
    }
    setBusy("metrics");
    setMetricsError(null);
    try {
      const res = await apiFetch(`/api/slos/${slo.id}/burn-rate`);
      if (!res.ok) throw new Error(await describeFailure(res));
      setMetrics((await res.json()) as BurnRateWindows);
    } catch (err) {
      setMetricsError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const openThresholdEditor = () => {
    if (editingThreshold) {
      setEditingThreshold(false);
      return;
    }
    // Seed from the current value each time it opens, so a poll that lands
    // while the panel is closed is not overwritten by a stale mount-time read.
    setThreshold(slo.alert_burn_rate_threshold === null ? "" : String(slo.alert_burn_rate_threshold));
    setThresholdError(null);
    setEditingThreshold(true);
  };

  const saveThreshold = async () => {
    const value = Number(threshold);
    if (!threshold.trim() || !Number.isFinite(value) || value <= 0) {
      setThresholdError("Enter a burn rate above 0 — for example 2 for twice the sustainable rate.");
      return;
    }
    setBusy("alerts");
    setThresholdError(null);
    try {
      const res = await apiFetch(`/api/slos/${slo.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_burn_rate_threshold: value }),
      });
      if (!res.ok) throw new Error(await describeFailure(res));
      setEditingThreshold(false);
      onChanged();
    } catch (err) {
      setThresholdError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const investigate = async () => {
    setBusy("investigate");
    setInvestigateError(null);
    setInvestigation(null);
    try {
      const res = await apiFetch("/api/coordinator/investigations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          incident_id: `slo-${slo.id}`,
          affected_services: [slo.service],
          severity: slo.status === "critical" || slo.status === "exhausted" ? "P1" : "P2",
          context: {
            slo_id: slo.id,
            slo_name: slo.name,
            slo_type: slo.type,
            target_percent: slo.target,
            current_sli_percent: slo.current,
            budget_minutes_remaining: slo.budget_remaining,
            burn_rate: slo.budget_burn_rate,
            window: slo.window,
          },
        }),
      });
      if (!res.ok) throw new Error(await describeFailure(res));
      const started = (await res.json()) as { execution_id?: string };
      setInvestigation(started.execution_id ?? "started");
    } catch (err) {
      setInvestigateError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

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
            <ExhaustionEstimate slo={slo} />
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
            <button
              onClick={toggleMetrics}
              disabled={busy === "metrics"}
              className="w-full px-3 py-2 text-sm text-left border border-gray-200 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-60"
            >
              {busy === "metrics"
                ? "Loading metrics…"
                : metrics
                  ? "Hide Detailed Metrics"
                  : "View Detailed Metrics"}
            </button>

            {metricsError && (
              <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                Could not load burn-rate windows: {metricsError}
              </p>
            )}

            {metrics && (
              <div className="p-3 space-y-1 text-xs rounded border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50">
                <div className="flex justify-between">
                  <span>{metrics.short_window} burn rate</span>
                  <span className="font-mono">{metrics.short_window_burn_rate.toFixed(2)}x</span>
                </div>
                <div className="flex justify-between">
                  <span>{metrics.long_window} burn rate</span>
                  <span className="font-mono">{metrics.long_window_burn_rate.toFixed(2)}x</span>
                </div>
                <div className="flex justify-between">
                  <span>Alerting</span>
                  <span className={metrics.is_alerting ? "text-red-600 font-medium" : ""}>
                    {metrics.is_alerting ? `yes · ${metrics.severity}` : "no"}
                  </span>
                </div>
                {metrics.message && <p className="text-gray-500 pt-1">{metrics.message}</p>}
              </div>
            )}

            <button
              onClick={openThresholdEditor}
              className="w-full px-3 py-2 text-sm text-left border border-gray-200 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              {editingThreshold ? "Cancel" : "Configure Alerts"}
            </button>

            {editingThreshold && (
              <div className="p-3 space-y-2 rounded border border-gray-200 dark:border-gray-600">
                <label htmlFor="burn-rate-threshold" className="block text-xs text-gray-500">
                  Alert when the burn rate exceeds
                </label>
                <input
                  id="burn-rate-threshold"
                  type="number"
                  min="0"
                  step="0.1"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  className="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                />
                {thresholdError && (
                  <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                    {thresholdError}
                  </p>
                )}
                <button
                  onClick={saveThreshold}
                  disabled={busy === "alerts"}
                  className="w-full px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-60"
                >
                  {busy === "alerts" ? "Saving…" : "Save threshold"}
                </button>
              </div>
            )}

            {slo.status !== "healthy" && (
              <button
                onClick={investigate}
                disabled={busy === "investigate"}
                className="w-full px-3 py-2 text-sm text-left bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-60"
              >
                {busy === "investigate" ? "Starting investigation…" : "Investigate with AI"}
              </button>
            )}

            {investigation && (
              <p className="text-xs text-green-700 dark:text-green-400">
                Investigation started — execution {investigation}.
              </p>
            )}
            {investigateError && (
              <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                Could not start an investigation: {investigateError}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CreateSLOModal({ onClose, onCreate }: { onClose: () => void; onCreate: (slo: Partial<SLO>) => void }) {
  const [name, setName] = useState("");
  const [service, setService] = useState("");
  const [sloType, setSloType] = useState<SLO["type"]>("availability");
  const [target, setTarget] = useState("");
  const [window, setWindow] = useState<SLO["window"]>("30d");

  const handleCreate = () => {
    onCreate({
      name,
      service,
      type: sloType,
      target: parseFloat(target),
      window,
    });
  };

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
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Checkout Availability"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Service</label>
            <input
              type="text"
              value={service}
              onChange={(e) => setService(e.target.value)}
              placeholder="e.g., checkout-service"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Type</label>
              <select
                value={sloType}
                onChange={(e) => setSloType(e.target.value as SLO["type"])}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
              >
                <option value="availability">Availability</option>
                <option value="latency">Latency (P99)</option>
                <option value="error_rate">Error Rate</option>
                <option value="throughput">Throughput</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Window</label>
              <select
                value={window}
                onChange={(e) => setWindow(e.target.value as SLO["window"])}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
              >
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
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                placeholder="99.9"
                className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
              />
              {/* Always a percentage. This said "ms" for a latency SLO and "/s"
                  for a throughput one, which invited a target of 250 — the API
                  takes a 0..1 ratio for every SLI type, because the SLI is
                  good/total in all of them ("99% of requests under 250ms"), so
                  250 was silently sent as 2.5 and rejected. There is no field
                  for the 250ms threshold itself; it belongs to whatever records
                  the SLI. */}
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
          <button
            onClick={handleCreate}
            className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            Create SLO
          </button>
        </div>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex items-center justify-center py-16">
      <div className="animate-spin w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full" />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-500">
      <Icon name="target" size={48} />
      <p className="mt-4 text-lg font-medium">No SLOs configured</p>
      <p className="text-sm">Create your first SLO to start tracking service reliability</p>
    </div>
  );
}

export function SLOTracker({ onBack }: { onBack: () => void }) {
  const [slos, setSlos] = useState<SLO[]>([]);
  // The id, not the object. Holding the object froze the detail panel against
  // the 30-second poll: the grid behind it updated and the open panel kept
  // showing the burn rate, budget and status from the moment it was clicked.
  // /api/slos is unpaginated, so `slos` always holds the whole list to look in.
  const [selectedSLOId, setSelectedSLOId] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [filter, setFilter] = useState<"all" | "healthy" | "warning" | "critical" | "no_data">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSLOs = useCallback(async () => {
    // Everything shown here comes from /api/slos and its /budget endpoint.
    // This used to fall back to inventing SLOs out of the incident list when
    // that returned nothing — a "99.9% target, 43.2 minutes of budget" row per
    // service with an uptime of `100 - incidents * 0.1`, none of which was
    // measured. A service with no SLO defined looked like a service meeting one.
    setError(null);
    try {
      const sloRes = await apiFetch(`/api/slos`);
      if (!sloRes.ok) {
        throw new Error(`SLO list failed: HTTP ${sloRes.status}`);
      }
      const sloData = await sloRes.json();

      const slosWithBudgets: SLO[] = await Promise.all(
        (sloData as Record<string, unknown>[]).map(async (slo) => {
          const target = (slo.target as number) ?? 0;
          let budget: BudgetResponse | null = null;
          try {
            const budgetRes = await apiFetch(`/api/slos/${slo.id}/budget`);
            if (budgetRes.ok) budget = await budgetRes.json();
          } catch {
            // Leave the budget null; the row renders as "no data" rather than
            // borrowing a number from somewhere else.
          }

          // Order matters, and "no data" has to be tested first. The tracker
          // returns `no_data` with remaining=1.0 and burn_rate=0 when nothing has
          // been measured, which sailed past every branch below and landed on the
          // default — so an SLO with no SLI recorded against it at all reported a
          // full error budget and a green badge.
          let status: SLO["status"] = "healthy";
          if (!budget || budget.status === "no_data" || budget.total_count === 0) {
            status = "no_data";
          } else if (budget.status === "breached") status = "exhausted";
          else if (budget.status === "at_risk") status = "critical";
          else if (budget.burn_rate > 1) status = "warning";

          return {
            id: slo.id as string,
            name: slo.name as string,
            service: slo.service as string,
            type: ((slo.sli_type as string) || "availability") as SLO["type"],
            target: target * 100,
            // The measured SLI, not `target - burn_rate * 0.1`. That expression
            // could only ever produce a number just below the target, so every
            // SLO rendered as very nearly met however badly it was doing.
            current: budget && budget.total_count > 0 ? budget.current_sli * 100 : null,
            window: (((slo.window as string) || "").replace("rolling_", "") ||
              "30d") as SLO["window"],
            // Minutes of the SLO's own window, computed server-side from its own
            // target — not the 43.2 that only ever applied to a monthly 99.9%.
            budget_total: budget?.budget_minutes_total ?? null,
            budget_remaining: budget?.budget_minutes_remaining ?? null,
            // Same rule as `current` just above: an unreadable or unmeasured
            // budget has no burn rate. `?? 0` rendered that as a green 0.0x —
            // the exact badge a perfectly behaved service earns.
            budget_burn_rate: budget && budget.total_count > 0 ? budget.burn_rate : null,
            projected_exhaustion: budget?.projected_exhaustion ?? null,
            alert_burn_rate_threshold:
              typeof slo.alert_burn_rate_threshold === "number"
                ? (slo.alert_burn_rate_threshold as number)
                : null,
            sample_count: budget?.total_count ?? 0,
            status,
            alerts: [],
          };
        })
      );
      setSlos(slosWithBudgets);
    } catch (err) {
      console.error("Failed to fetch SLOs:", err);
      setError(err instanceof Error ? err.message : "Failed to load SLOs");
      setSlos([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSLOs();
    const interval = setInterval(fetchSLOs, 30000);
    return () => clearInterval(interval);
  }, [fetchSLOs]);

  const handleCreateSLO = async (sloData: Partial<SLO>) => {
    // A failed create used to append the SLO to local state anyway, with a
    // fabricated 43.2-minute budget and `current` set to the target — so the new
    // SLO appeared, healthy and exactly on target, and stayed that way until the
    // page was reloaded and it silently vanished. A create that did not happen
    // has to say so.
    setError(null);
    try {
      const response = await apiFetch(`/api/slos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: sloData.name || "New SLO",
          service: sloData.service || "unknown",
          sli_type: sloData.type || "availability",
          // Always a ratio. Every SLI here is good_count/total_count, including
          // latency ("99% of requests under the threshold") — the API constrains
          // target to 0..1 for that reason.
          target: (sloData.target ?? 99.9) / 100,
          window: `rolling_${sloData.window || "30d"}`,
        }),
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail.slice(0, 200) || `HTTP ${response.status}`);
      }
      await fetchSLOs();
      setShowCreateModal(false);
    } catch (err) {
      console.error("Failed to create SLO:", err);
      setError(err instanceof Error ? err.message : "Failed to create SLO");
    }
  };

  // Derived, so every poll flows straight through to the open panel. An SLO
  // that disappears from the list closes its panel rather than stranding it.
  const selectedSLO = selectedSLOId === null ? null : slos.find(s => s.id === selectedSLOId) ?? null;

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
    // Counted and shown rather than left as the difference between Total and the
    // other three. An SLO measuring nothing is a broken instrumentation job, and
    // the only way anyone finds out is if the page puts a number on it.
    noData: slos.filter(s => s.status === "no_data").length,
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 omni-workspace-page">
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
        {/* Shown above the list rather than instead of it: a create that failed
            must not hide the SLOs that already exist. */}
        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-900/30 p-3 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <Icon name="alert-circle" size={16} />
            <span className="flex-1">{error}</span>
            <button
              onClick={() => setError(null)}
              className="text-red-700 dark:text-red-300 hover:underline"
            >
              Dismiss
            </button>
          </div>
        )}

        {loading ? (
          <LoadingState />
        ) : slos.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            {/* Summary */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
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
              <div
                onClick={() => setFilter("no_data")}
                className={`bg-white dark:bg-gray-800 rounded-lg border p-4 cursor-pointer transition-all ${
                  filter === "no_data" ? "border-blue-500 ring-2 ring-blue-200" : "border-gray-200 dark:border-gray-700"
                }`}
              >
                <div className="text-sm text-gray-500">No Data</div>
                <div className="text-3xl font-bold text-blue-600">{summary.noData}</div>
              </div>
            </div>

            {/* SLO Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {filteredSLOs.map((slo) => (
                <SLOCard key={slo.id} slo={slo} onSelect={() => setSelectedSLOId(slo.id)} />
              ))}
            </div>
          </>
        )}
      </div>

      {/* Detail Panel */}
      {selectedSLO && (
        <SLODetailPanel
          // Keyed so switching to another SLO starts the panel's own state
          // (loaded metrics, half-typed threshold) fresh rather than carrying
          // one SLO's readings over to the next.
          key={selectedSLO.id}
          slo={selectedSLO}
          onClose={() => setSelectedSLOId(null)}
          onChanged={fetchSLOs}
        />
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <CreateSLOModal onClose={() => setShowCreateModal(false)} onCreate={handleCreateSLO} />
      )}
    </div>
  );
}
