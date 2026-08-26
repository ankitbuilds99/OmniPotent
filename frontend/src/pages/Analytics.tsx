/**
 * Analytics Dashboard - Real API integration
 */

import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";

import { apiFetch, describeFailure } from "../lib/apiClient";

interface MetricCard {
  title: string;
  value: string;
  /** Percent change against the previous window of the same length, or null when
   *  the fetched data does not reach back far enough to compute one. Every card
   *  used to hardcode `change: 0` and still render the delta line, so the page
   *  claimed "0% vs last month" for six metrics that had never been compared to
   *  anything. Null means the line is left off rather than filled with a zero. */
  change: number | null;
  changeLabel: string;
  good: "up" | "down";
}

interface ServiceHealth {
  name: string;
  incidents: number;
  resolved: number;
  /** Mean minutes from trigger to resolve for this service, or null if nothing
   *  here has been resolved yet. */
  mttr: number | null;
  /** This service's share of all incidents in the window, 0..1. */
  share: number;
}

interface IncidentTrend {
  date: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
}

interface TeamMetrics {
  name: string;
  mtta: number;
  mttr: number;
  incidents_handled: number;
  escalations: number;
}

function MetricCardComponent({ metric }: { metric: MetricCard }) {
  // The direction is read off the change rather than passed in beside it. The
  // two used to be set independently, so every card carried `change: 0` with a
  // hand-picked `trend`, and the arrow pointed up or down at a number that had
  // not moved.
  const trend = metric.change === null || metric.change === 0
    ? "neutral"
    : metric.change > 0 ? "up" : "down";
  const isGood = (trend === "up" && metric.good === "up") || (trend === "down" && metric.good === "down");
  const changeColor = isGood ? "text-green-600" : trend === "neutral" ? "text-gray-500" : "text-red-600";
  const arrow = trend === "up" ? "^" : trend === "down" ? "v" : "-";

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <div className="text-sm text-gray-500 dark:text-gray-400">{metric.title}</div>
      <div className="text-3xl font-bold mt-1">{metric.value}</div>
      {metric.change === null ? (
        <div className="text-sm mt-2 text-gray-400">{metric.changeLabel}</div>
      ) : (
        <div className={`text-sm mt-2 ${changeColor}`}>
          {arrow} {Math.abs(metric.change)}% {metric.changeLabel}
        </div>
      )}
    </div>
  );
}

function IncidentTrendChart({ data }: { data: IncidentTrend[] }) {
  if (data.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <h3 className="font-semibold mb-4">Incident Trends</h3>
        <div className="flex items-center justify-center h-48 text-gray-500">
          No trend data available
        </div>
      </div>
    );
  }

  const maxValue = Math.max(...data.map(d => d.critical + d.high + d.medium + d.low), 1);

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">Incident Trends</h3>
      <div className="flex items-end gap-2 h-48">
        {data.map((week, i) => {
          const total = week.critical + week.high + week.medium + week.low;
          const height = (total / maxValue) * 100;

          return (
            <div key={i} className="flex-1 flex flex-col items-center">
              <div className="w-full flex flex-col-reverse" style={{ height: `${height}%` }}>
                <div
                  className="bg-red-500 w-full"
                  style={{ height: total > 0 ? `${(week.critical / total) * 100}%` : 0 }}
                  title={`Critical: ${week.critical}`}
                />
                <div
                  className="bg-orange-500 w-full"
                  style={{ height: total > 0 ? `${(week.high / total) * 100}%` : 0 }}
                  title={`High: ${week.high}`}
                />
                <div
                  className="bg-yellow-500 w-full"
                  style={{ height: total > 0 ? `${(week.medium / total) * 100}%` : 0 }}
                  title={`Medium: ${week.medium}`}
                />
                <div
                  className="bg-blue-500 w-full rounded-t"
                  style={{ height: total > 0 ? `${(week.low / total) * 100}%` : 0 }}
                  title={`Low: ${week.low}`}
                />
              </div>
              <div className="text-xs text-gray-500 mt-2">{week.date}</div>
            </div>
          );
        })}
      </div>
      <div className="flex items-center justify-center gap-4 mt-4 text-xs">
        <div className="flex items-center gap-1">
          <span className="w-3 h-3 bg-red-500 rounded" />
          <span>Critical</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-3 h-3 bg-orange-500 rounded" />
          <span>High</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-3 h-3 bg-yellow-500 rounded" />
          <span>Medium</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-3 h-3 bg-blue-500 rounded" />
          <span>Low</span>
        </div>
      </div>
    </div>
  );
}

function MTTRChart({ data }: { data: { label: string; value: number; ai: number }[] }) {
  if (data.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <h3 className="font-semibold mb-4">MTTR Comparison (Manual vs AI-Assisted)</h3>
        <div className="flex items-center justify-center h-48 text-gray-500 text-center px-4">
          Not enough resolved incidents to compare. A bucket needs at least one
          resolved incident with an investigation and one without.
        </div>
      </div>
    );
  }

  const maxValue = Math.max(...data.map(d => Math.max(d.value, d.ai)), 1);

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">MTTR Comparison (Manual vs AI-Assisted)</h3>
      <div className="space-y-4">
        {data.map((week, i) => (
          <div key={i}>
            <div className="flex justify-between text-sm mb-1">
              <span>{week.label}</span>
              <span className="text-gray-500">{week.value}m vs {week.ai}m</span>
            </div>
            <div className="flex gap-1 h-6">
              <div
                className="bg-gray-300 dark:bg-gray-600 rounded-l"
                style={{ width: `${(week.value / maxValue) * 100}%` }}
                title={`Manual: ${week.value}m`}
              />
              <div
                className="bg-green-500 rounded-r"
                style={{ width: `${(week.ai / maxValue) * 100}%` }}
                title={`AI-Assisted: ${week.ai}m`}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-center gap-4 mt-4 text-xs">
        <div className="flex items-center gap-1">
          <span className="w-3 h-3 bg-gray-300 dark:bg-gray-600 rounded" />
          <span>Manual</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-3 h-3 bg-green-500 rounded" />
          <span>AI-Assisted</span>
        </div>
      </div>
    </div>
  );
}

function ServiceHealthTable({ services }: { services: ServiceHealth[] }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="p-4 border-b border-gray-200 dark:border-gray-700">
        <h3 className="font-semibold">Service Health</h3>
      </div>
      {/* The Uptime and Error Rate columns are gone. Nothing in this product
          measures either one per service: uptime was `Math.max(95, 100 - incidents * 0.5)`,
          a number derived from the incident count and then floored at 95% so it
          always looked plausible, and Error Rate was the service's share of all
          incidents printed with a `%` sign on a 0..1 fraction — so a service
          responsible for a quarter of them read "0.25%". Incident counts and MTTR
          are the two things this page can actually measure, so they are what it
          shows. Real uptime belongs to the SLO tracker, which measures it. */}
      {services.length === 0 ? (
        <div className="p-8 text-center text-gray-500">No service data available</div>
      ) : (
        <table className="w-full">
          <thead className="bg-gray-50 dark:bg-gray-900">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Service</th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Incidents</th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Resolved</th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">MTTR</th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Share</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {services.map((svc) => (
              <tr key={svc.name} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-4 py-3 font-medium">{svc.name}</td>
                <td className="px-4 py-3">{svc.incidents}</td>
                <td className="px-4 py-3">{svc.resolved}</td>
                <td className="px-4 py-3">
                  {svc.mttr === null ? (
                    <span className="text-gray-400">—</span>
                  ) : (
                    <span className="font-mono">{Math.round(svc.mttr)}m</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className={svc.share > 0.25 ? "text-red-600 font-mono" : "font-mono"}>
                    {(svc.share * 100).toFixed(1)}%
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TeamLeaderboard({ teams }: { teams: TeamMetrics[] }) {
  const sorted = [...teams].sort((a, b) => a.mttr - b.mttr);

  if (teams.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <h3 className="font-semibold mb-4">Team Performance</h3>
        <div className="text-center text-gray-500 py-8">No team data available</div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">Team Performance</h3>
      <div className="space-y-3">
        {sorted.map((team, i) => (
          <div key={team.name} className="flex items-center gap-3">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold ${
              i === 0 ? "bg-yellow-500" : i === 1 ? "bg-gray-400" : i === 2 ? "bg-orange-600" : "bg-gray-300"
            }`}>
              {i + 1}
            </div>
            <div className="flex-1">
              <div className="font-medium">{team.name}</div>
              <div className="text-xs text-gray-500">
                {team.incidents_handled} incidents | {team.escalations} escalations
              </div>
            </div>
            <div className="text-right">
              <div className="font-mono text-lg">{team.mttr}m</div>
              <div className="text-xs text-gray-500">MTTR</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** What the AI actually did, in units this product records.
 *
 *  This replaces an "AI ROI This Month" panel whose four figures were:
 *    - Total Savings: resolved incidents x $150/hr x 2hr. Neither constant comes
 *      from anywhere — the product has no cost model and never asks the operator
 *      for an hourly rate — so it was a headline dollar figure with no input.
 *    - Engineer Hours Saved: the same invented 2 hours per incident.
 *    - Auto-Resolved Incidents: every resolved incident, however it was resolved.
 *      Nothing checked whether the AI was involved.
 *    - MTTR Reduction: `(1 - avgMttr * 0.6 / avgMttr) * 100`, which is 40 for
 *      every possible input. A constant rendered as a measurement.
 *
 *  The counts below come from `has_investigation` and `investigation_confidence`
 *  on the incident record, which are set when an investigation actually ran. */
function AIImpactPanel({ metrics }: {
  metrics: {
    investigated: number;
    resolved_with_investigation: number;
    mean_confidence: number | null;
    mttr_with: number | null;
    mttr_without: number | null;
  };
}) {
  const delta =
    metrics.mttr_with !== null && metrics.mttr_without !== null && metrics.mttr_without > 0
      ? Math.round(((metrics.mttr_without - metrics.mttr_with) / metrics.mttr_without) * 100)
      : null;

  return (
    <div className="bg-gradient-to-br from-blue-500 to-indigo-600 rounded-lg p-6 text-white">
      <h3 className="font-semibold text-lg mb-4">AI Involvement</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-3xl font-bold">{metrics.investigated}</div>
          <div className="text-sm text-blue-100">Incidents Investigated</div>
        </div>
        <div>
          <div className="text-3xl font-bold">{metrics.resolved_with_investigation}</div>
          <div className="text-sm text-blue-100">Of Those, Resolved</div>
        </div>
        <div>
          <div className="text-3xl font-bold">
            {metrics.mean_confidence === null ? "—" : `${Math.round(metrics.mean_confidence * 100)}%`}
          </div>
          <div className="text-sm text-blue-100">Mean Confidence</div>
        </div>
        <div>
          {/* Shown only when both halves of the comparison exist. An org whose
              incidents were all investigated, or none of them, has no contrast
              to report and gets a dash rather than a fabricated percentage. */}
          <div className="text-3xl font-bold">{delta === null ? "—" : `${delta > 0 ? "-" : "+"}${Math.abs(delta)}%`}</div>
          <div className="text-sm text-blue-100">MTTR vs Uninvestigated</div>
        </div>
      </div>
    </div>
  );
}

/** Which models the router actually sent work to.
 *
 *  This replaces a "6-Agent Performance" panel with a per-agent accuracy bar and
 *  a response time beside it. Nothing in this build records either number — no
 *  agent reports an accuracy, and no code path times one — so the panel had no
 *  producer at all and sat permanently on "No agent metrics available", which
 *  reads like a service that is down rather than a measurement that does not
 *  exist. Routing counts do exist, from GET /api/model-router/stats, so that is what
 *  is shown.
 *
 *  The counts are held in the API process's memory, not a table, so they start
 *  from zero at every restart and cover only the worker that answered. The
 *  caption says so — a routing share is misleading if the reader assumes it
 *  covers all time. */
function ModelRoutingPanel({ routing }: {
  routing: { total: number; byModel: { model: string; count: number }[] } | null;
}) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-1">Model Routing</h3>
      <p className="text-xs text-gray-500 mb-4">
        Counted in memory since this API process started — not a stored total.
      </p>
      {routing === null ? (
        <div className="text-center text-gray-500 py-8">Routing stats could not be read.</div>
      ) : routing.total === 0 ? (
        <div className="text-center text-gray-500 py-8">
          Nothing has been routed since this process started.
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {routing.byModel.map((entry) => {
              const share = (entry.count / routing.total) * 100;
              return (
                <div key={entry.model} className="flex items-center gap-3">
                  <div className="w-32 text-sm font-medium truncate" title={entry.model}>{entry.model}</div>
                  <div className="flex-1">
                    <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                      <div className="h-full bg-blue-500 rounded-full" style={{ width: `${share}%` }} />
                    </div>
                  </div>
                  <div className="w-12 text-right text-sm font-mono">{share.toFixed(0)}%</div>
                  <div className="w-16 text-right text-xs text-gray-500">{entry.count}</div>
                </div>
              );
            })}
          </div>
          <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700 flex justify-between text-sm">
            <span className="text-gray-500">Total Routed</span>
            <span className="font-bold">{routing.total.toLocaleString()}</span>
          </div>
        </>
      )}
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

/** Days in each range, and how wide a trend bucket is for it. The range buttons
 *  used to be decoration: `fetchData` was a `useCallback` with an empty
 *  dependency list that never read `timeRange`, and the trend chart was hardcoded
 *  to six weekly buckets, so clicking 7d and 90d produced byte-identical pages. */
const RANGES = {
  "7d": { days: 7, bucketDays: 1 },
  "30d": { days: 30, bucketDays: 5 },
  "90d": { days: 90, bucketDays: 15 },
} as const;

type Record_ = Record<string, unknown>;

function timestamp(value: unknown): number | null {
  if (typeof value !== "string" || !value) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : null;
}

/** When a record started. Incidents call it `triggered_at`, alerts too, and
 *  anything older that only has `created_at` still counts. */
function startedAt(record: Record_): number | null {
  return timestamp(record.triggered_at) ?? timestamp(record.created_at);
}

/** Minutes between two fields of a record, or null if either is absent or the
 *  pair runs backwards. There is no upper bound here: the old code discarded
 *  anything over 10,000 minutes, which quietly dropped every incident that took
 *  more than a week to resolve — exactly the ones that pull MTTR up. */
function minutesBetween(record: Record_, from: string, to: string): number | null {
  const a = timestamp(record[from]);
  const b = timestamp(record[to]);
  if (a === null || b === null) return null;
  const minutes = (b - a) / 60000;
  return minutes >= 0 ? minutes : null;
}

function mean(values: number[]): number | null {
  if (!values.length) return null;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

/** Percent change from `before` to `after`, or null when there is nothing to
 *  divide by or the previous window was never fetched. */
function percentChange(after: number, before: number | null): number | null {
  if (before === null || before === 0) return null;
  return Math.round(((after - before) / before) * 100);
}

export function Analytics({ onBack }: { onBack: () => void }) {
  const [timeRange, setTimeRange] = useState<"7d" | "30d" | "90d">("30d");
  const [loading, setLoading] = useState(true);
  const [metrics, setMetrics] = useState<MetricCard[]>([]);
  const [trends, setTrends] = useState<IncidentTrend[]>([]);
  const [services, setServices] = useState<ServiceHealth[]>([]);
  const [teams, setTeams] = useState<TeamMetrics[]>([]);
  const [aiImpact, setAiImpact] = useState({
    investigated: 0,
    resolved_with_investigation: 0,
    mean_confidence: null as number | null,
    mttr_with: null as number | null,
    mttr_without: null as number | null,
  });
  const [routing, setRouting] = useState<{ total: number; byModel: { model: string; count: number }[] } | null>(null);
  const [mttrData, setMttrData] = useState<{ label: string; value: number; ai: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  /** True when the API returned a full page, so the oldest records in the window
   *  may have been cut off. The page says so rather than reporting a total that
   *  is really "the most recent 200". */
  const [truncated, setTruncated] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    const { days, bucketDays } = RANGES[timeRange];
    try {
      // `limit` is the API maximum. It used to be left at the default, so every
      // figure on this page was computed from the 50 most recent incidents
      // regardless of which range button was selected.
      const INCIDENT_LIMIT = 200;
      const ALERT_LIMIT = 200;
      const [incidentsRes, alertsRes, teamsRes, routingRes] = await Promise.all([
        apiFetch(`/api/incidents?limit=${INCIDENT_LIMIT}`),
        apiFetch(`/oncall/alerts?limit=${ALERT_LIMIT}`),
        apiFetch(`/oncall/teams`),
        apiFetch(`/api/model-router/stats`),
      ]);
      if (!incidentsRes.ok) {
        throw new Error(`Incidents: ${await describeFailure(incidentsRes)}`);
      }
      const incidents: Record_[] = await incidentsRes.json();
      const alerts: Record_[] = alertsRes.ok ? await alertsRes.json() : [];
      const teamsData: Record_[] = teamsRes.ok ? await teamsRes.json() : [];
      setTruncated(incidents.length >= INCIDENT_LIMIT || alerts.length >= ALERT_LIMIT);

      // Routing stats are a separate concern from the incident window: they are
      // process-lifetime counters and take no date range, so a failure here must
      // not empty the rest of the page.
      if (routingRes.ok) {
        const stats = (await routingRes.json()) as { routing?: { total_routed?: number; by_model?: Record<string, number> } };
        const byModel = Object.entries(stats.routing?.by_model ?? {})
          .map(([model, count]) => ({ model, count: Number(count) || 0 }))
          .sort((a, b) => b.count - a.count);
        setRouting({ total: Number(stats.routing?.total_routed) || 0, byModel });
      } else {
        setRouting(null);
      }

      // The selected range, and the window of the same length before it. The
      // second is what the "vs previous" figures compare against.
      const nowMs = Date.now();
      const windowStart = nowMs - days * 86400000;
      const prevStart = windowStart - days * 86400000;

      const all = [...incidents, ...alerts];
      const inWindow = (r: Record_) => {
        const t = startedAt(r);
        return t !== null && t >= windowStart;
      };
      const inPrevWindow = (r: Record_) => {
        const t = startedAt(r);
        return t !== null && t >= prevStart && t < windowStart;
      };

      const current = all.filter(inWindow);
      const previous = all.filter(inPrevWindow);
      const windowIncidents = incidents.filter(inWindow);

      // Only compare against the previous window when the fetched data plainly
      // reaches back past its start. Otherwise the API's `limit` cut the older
      // half off and every metric would read as a dramatic improvement.
      const oldest = all.reduce<number | null>((min, r) => {
        const t = startedAt(r);
        return t === null ? min : min === null || t < min ? t : min;
      }, null);
      const comparable = oldest !== null && oldest <= prevStart;

      const isResolved = (r: Record_) => r.status === "resolved";
      const resolvedNow = current.filter(isResolved).length;
      const resolvedPrev = previous.filter(isResolved).length;

      const mttrNow = mean(current.map(r => minutesBetween(r, "triggered_at", "resolved_at")).filter((v): v is number => v !== null));
      const mttrPrev = mean(previous.map(r => minutesBetween(r, "triggered_at", "resolved_at")).filter((v): v is number => v !== null));
      // Measured, not `mttr * 0.15`. Both records carry `acknowledged_at`; the
      // page invented MTTA as a fixed fraction of MTTR, so it moved only when
      // MTTR did and was never once the actual time to acknowledge.
      const mttaNow = mean(current.map(r => minutesBetween(r, "triggered_at", "acknowledged_at")).filter((v): v is number => v !== null));
      const mttaPrev = mean(previous.map(r => minutesBetween(r, "triggered_at", "acknowledged_at")).filter((v): v is number => v !== null));

      const resolutionRate = current.length > 0 ? Math.round((resolvedNow / current.length) * 100) : null;
      const resolutionRatePrev = previous.length > 0 ? Math.round((resolvedPrev / previous.length) * 100) : null;

      const label = `vs previous ${days}d`;
      const noCompare = comparable ? label : "no earlier data to compare";
      setMetrics([
        { title: "MTTR", value: mttrNow === null ? "N/A" : `${Math.round(mttrNow)}m`,
          change: mttrNow === null ? null : percentChange(mttrNow, comparable ? mttrPrev : null),
          changeLabel: noCompare, good: "down" },
        { title: "MTTA", value: mttaNow === null ? "N/A" : `${Math.round(mttaNow)}m`,
          change: mttaNow === null ? null : percentChange(mttaNow, comparable ? mttaPrev : null),
          changeLabel: noCompare, good: "down" },
        { title: "Total Incidents", value: `${current.length}`,
          change: percentChange(current.length, comparable ? previous.length : null),
          changeLabel: noCompare, good: "down" },
        // Not "AI Resolution Rate". Nothing in this number knows whether the AI
        // was involved — it is the share of incidents that reached `resolved`,
        // by whatever means. The AI's actual contribution is the panel below.
        { title: "Resolution Rate", value: resolutionRate === null ? "N/A" : `${resolutionRate}%`,
          change: resolutionRate === null ? null : percentChange(resolutionRate, comparable ? resolutionRatePrev : null),
          changeLabel: noCompare, good: "up" },
        { title: "Resolved", value: `${resolvedNow}`,
          change: percentChange(resolvedNow, comparable ? resolvedPrev : null),
          changeLabel: noCompare, good: "up" },
        { title: "Open", value: `${current.length - resolvedNow}`,
          change: percentChange(current.length - resolvedNow, comparable ? previous.length - resolvedPrev : null),
          changeLabel: noCompare, good: "down" },
      ]);

      // Trend buckets sized to the selected range, oldest first.
      const bucketCount = Math.ceil(days / bucketDays);
      const trendData: IncidentTrend[] = [];
      for (let i = bucketCount - 1; i >= 0; i--) {
        const bucketEnd = nowMs - i * bucketDays * 86400000;
        const bucketStart = bucketEnd - bucketDays * 86400000;
        const bucket = current.filter(r => {
          const t = startedAt(r);
          return t !== null && t >= bucketStart && t < bucketEnd;
        });
        const sev = (r: Record_) => String(r.severity ?? "").toLowerCase();
        trendData.push({
          date: new Date(bucketStart).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
          critical: bucket.filter(r => sev(r) === "critical" || sev(r) === "sev1").length,
          high: bucket.filter(r => sev(r) === "high" || sev(r) === "sev2").length,
          medium: bucket.filter(r => sev(r) === "medium" || sev(r) === "warning").length,
          low: bucket.filter(r => !["critical", "sev1", "high", "sev2", "medium", "warning"].includes(sev(r))).length,
        });
      }
      setTrends(trendData);

      // Per-service breakdown. Alerts are left out: they carry no `service`, so
      // folding them in would file every alert under "unknown".
      const serviceMap = new Map<string, { incidents: number; resolved: number; mttrs: number[] }>();
      windowIncidents.forEach(inc => {
        const name = (inc.service as string) || "unknown";
        const entry = serviceMap.get(name) ?? { incidents: 0, resolved: 0, mttrs: [] };
        entry.incidents++;
        if (isResolved(inc)) entry.resolved++;
        const m = minutesBetween(inc, "triggered_at", "resolved_at");
        if (m !== null) entry.mttrs.push(m);
        serviceMap.set(name, entry);
      });
      setServices(
        Array.from(serviceMap.entries())
          .map(([name, e]) => ({
            name,
            incidents: e.incidents,
            resolved: e.resolved,
            mttr: mean(e.mttrs),
            share: windowIncidents.length > 0 ? e.incidents / windowIncidents.length : 0,
          }))
          .sort((a, b) => b.incidents - a.incidents)
          .slice(0, 5)
      );

      // Teams come back with names only; nothing in the API attributes an
      // incident to a team, so the per-team figures stay at zero and the
      // leaderboard sorts on nothing. Left as-is rather than invented.
      setTeams(teamsData.map(team => ({
        name: (team.name as string) || "Unknown Team",
        mtta: 0,
        mttr: 0,
        incidents_handled: 0,
        escalations: 0,
      })));

      // AI involvement, measured off the investigation fields on each incident.
      const investigated = windowIncidents.filter(i => i.has_investigation === true);
      const confidences = investigated
        .map(i => (typeof i.investigation_confidence === "number" ? i.investigation_confidence : null))
        .filter((v): v is number => v !== null);
      const mttrsWith = investigated
        .map(i => minutesBetween(i, "triggered_at", "resolved_at"))
        .filter((v): v is number => v !== null);
      const mttrsWithout = windowIncidents
        .filter(i => i.has_investigation !== true)
        .map(i => minutesBetween(i, "triggered_at", "resolved_at"))
        .filter((v): v is number => v !== null);
      setAiImpact({
        investigated: investigated.length,
        resolved_with_investigation: investigated.filter(isResolved).length,
        mean_confidence: mean(confidences),
        mttr_with: mean(mttrsWith),
        mttr_without: mean(mttrsWithout),
      });

      // Manual vs AI-assisted, per bucket, from the same split. `ai` used to be
      // `avgMttr * 0.6` — a bar guaranteed to sit 40% shorter than the one next
      // to it whatever the incidents actually cost to resolve. A bucket appears
      // only when both sides of it were measured.
      const comparison: { label: string; value: number; ai: number }[] = [];
      for (let i = bucketCount - 1; i >= 0; i--) {
        const bucketEnd = nowMs - i * bucketDays * 86400000;
        const bucketStart = bucketEnd - bucketDays * 86400000;
        const bucket = windowIncidents.filter(inc => {
          const t = startedAt(inc);
          return t !== null && t >= bucketStart && t < bucketEnd;
        });
        const withAi = mean(bucket.filter(i2 => i2.has_investigation === true)
          .map(i2 => minutesBetween(i2, "triggered_at", "resolved_at"))
          .filter((v): v is number => v !== null));
        const withoutAi = mean(bucket.filter(i2 => i2.has_investigation !== true)
          .map(i2 => minutesBetween(i2, "triggered_at", "resolved_at"))
          .filter((v): v is number => v !== null));
        if (withAi === null || withoutAi === null) continue;
        comparison.push({
          label: new Date(bucketStart).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
          value: Math.round(withoutAi),
          ai: Math.round(withAi),
        });
      }
      setMttrData(comparison);
    } catch (err) {
      console.error("Failed to fetch analytics:", err);
      setError(err instanceof Error ? err.message : "Failed to load analytics");
      setMetrics([]);
      setTrends([]);
      setServices([]);
      setTeams([]);
      setMttrData([]);
    } finally {
      setLoading(false);
    }
  }, [timeRange]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                <Icon name="arrow-left" size={20} />
              </button>
              <h1 className="text-xl font-semibold">Analytics Dashboard</h1>
            </div>
            <div className="flex items-center gap-2">
              {(["7d", "30d", "90d"] as const).map((range) => (
                <button
                  key={range}
                  onClick={() => setTimeRange(range)}
                  className={`px-3 py-1.5 text-sm rounded ${
                    timeRange === range
                      ? "bg-blue-600 text-white"
                      : "bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600"
                  }`}
                >
                  {range}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-900/30 p-3 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <Icon name="warning" size={16} />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError(null)} className="hover:underline">Dismiss</button>
          </div>
        )}

        {/* The API caps a page at 200 records. Once that cap is hit the numbers
            below describe the most recent 200, not the whole range, and a total
            printed without that caveat is simply wrong. */}
        {truncated && !error && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/30 p-3 text-sm text-amber-800 dark:text-amber-300">
            The API returned a full page. These figures cover the most recent
            records only, so totals for {timeRange} may be understated.
          </div>
        )}

        {loading ? (
          <LoadingState />
        ) : (
          <>
            {/* Key Metrics */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
              {metrics.map((metric, i) => (
                <MetricCardComponent key={i} metric={metric} />
              ))}
            </div>

            {/* Charts Row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <IncidentTrendChart data={trends} />
              <MTTRChart data={mttrData} />
            </div>

            {/* ROI and AI Performance */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <AIImpactPanel metrics={aiImpact} />
              <ModelRoutingPanel routing={routing} />
            </div>

            {/* Tables Row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <ServiceHealthTable services={services} />
              <TeamLeaderboard teams={teams} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
