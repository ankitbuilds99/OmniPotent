import { useState } from "react";
import { Icon } from "../components/Icon";

interface MetricCard {
  title: string;
  value: string;
  change: number;
  changeLabel: string;
  trend: "up" | "down" | "neutral";
  good: "up" | "down";
}

interface IncidentTrend {
  date: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
}

interface ServiceHealth {
  name: string;
  uptime: number;
  incidents: number;
  mttr: number;
  errorRate: number;
}

interface TeamMetrics {
  name: string;
  mtta: number;
  mttr: number;
  incidents_handled: number;
  escalations: number;
}

const DEMO_METRICS: MetricCard[] = [
  { title: "MTTR", value: "18m", change: -23, changeLabel: "vs last month", trend: "down", good: "down" },
  { title: "MTTA", value: "2m 34s", change: -15, changeLabel: "vs last month", trend: "down", good: "down" },
  { title: "Total Incidents", value: "47", change: 12, changeLabel: "vs last month", trend: "up", good: "down" },
  { title: "AI Resolution Rate", value: "73%", change: 8, changeLabel: "vs last month", trend: "up", good: "up" },
  { title: "SLA Compliance", value: "99.7%", change: 0.2, changeLabel: "vs last month", trend: "up", good: "up" },
  { title: "Avg Confidence", value: "0.87", change: 5, changeLabel: "vs last month", trend: "up", good: "up" },
];

const DEMO_TRENDS: IncidentTrend[] = [
  { date: "Jul 1", critical: 2, high: 5, medium: 8, low: 12 },
  { date: "Jul 8", critical: 1, high: 3, medium: 10, low: 15 },
  { date: "Jul 15", critical: 3, high: 4, medium: 7, low: 11 },
  { date: "Jul 22", critical: 0, high: 2, medium: 9, low: 14 },
  { date: "Jul 29", critical: 1, high: 6, medium: 6, low: 10 },
  { date: "Aug 1", critical: 1, high: 3, medium: 8, low: 13 },
];

const DEMO_SERVICES: ServiceHealth[] = [
  { name: "api-gateway", uptime: 99.98, incidents: 3, mttr: 12, errorRate: 0.02 },
  { name: "checkout-service", uptime: 99.85, incidents: 8, mttr: 24, errorRate: 0.15 },
  { name: "user-service", uptime: 99.99, incidents: 1, mttr: 8, errorRate: 0.01 },
  { name: "payment-gateway", uptime: 99.95, incidents: 4, mttr: 18, errorRate: 0.05 },
  { name: "inventory-service", uptime: 99.92, incidents: 5, mttr: 15, errorRate: 0.08 },
];

const DEMO_TEAMS: TeamMetrics[] = [
  { name: "Platform Team", mtta: 2, mttr: 15, incidents_handled: 23, escalations: 2 },
  { name: "Payments Team", mtta: 3, mttr: 22, incidents_handled: 12, escalations: 3 },
  { name: "Growth Team", mtta: 4, mttr: 18, incidents_handled: 8, escalations: 1 },
  { name: "Data Team", mtta: 5, mttr: 25, incidents_handled: 4, escalations: 0 },
];

const ROI_METRICS = {
  incidents_auto_resolved: 34,
  hours_saved: 156,
  avg_engineer_cost_per_hour: 150,
  total_savings: 23400,
  mttr_reduction_pct: 45,
  false_positive_reduction_pct: 62,
};

function MetricCardComponent({ metric }: { metric: MetricCard }) {
  const isGood = (metric.trend === "up" && metric.good === "up") || (metric.trend === "down" && metric.good === "down");
  const changeColor = isGood ? "text-green-600" : metric.trend === "neutral" ? "text-gray-500" : "text-red-600";
  const arrow = metric.trend === "up" ? "^" : metric.trend === "down" ? "v" : "-";

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <div className="text-sm text-gray-500 dark:text-gray-400">{metric.title}</div>
      <div className="text-3xl font-bold mt-1">{metric.value}</div>
      <div className={`text-sm mt-2 ${changeColor}`}>
        {arrow} {Math.abs(metric.change)}% {metric.changeLabel}
      </div>
    </div>
  );
}

function IncidentTrendChart({ data }: { data: IncidentTrend[] }) {
  const maxValue = Math.max(...data.map(d => d.critical + d.high + d.medium + d.low));

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
                  style={{ height: `${(week.critical / total) * 100}%` }}
                  title={`Critical: ${week.critical}`}
                />
                <div
                  className="bg-orange-500 w-full"
                  style={{ height: `${(week.high / total) * 100}%` }}
                  title={`High: ${week.high}`}
                />
                <div
                  className="bg-yellow-500 w-full"
                  style={{ height: `${(week.medium / total) * 100}%` }}
                  title={`Medium: ${week.medium}`}
                />
                <div
                  className="bg-blue-500 w-full rounded-t"
                  style={{ height: `${(week.low / total) * 100}%` }}
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

function MTTRChart() {
  const data = [
    { label: "Week 1", value: 32, ai: 18 },
    { label: "Week 2", value: 28, ai: 15 },
    { label: "Week 3", value: 25, ai: 12 },
    { label: "Week 4", value: 22, ai: 10 },
  ];

  const maxValue = Math.max(...data.map(d => Math.max(d.value, d.ai)));

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
      <table className="w-full">
        <thead className="bg-gray-50 dark:bg-gray-900">
          <tr>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Service</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Uptime</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Incidents</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">MTTR</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Error Rate</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
          {services.map((svc) => (
            <tr key={svc.name} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
              <td className="px-4 py-3 font-medium">{svc.name}</td>
              <td className="px-4 py-3">
                <span className={`font-mono ${svc.uptime >= 99.95 ? "text-green-600" : svc.uptime >= 99.9 ? "text-yellow-600" : "text-red-600"}`}>
                  {svc.uptime.toFixed(2)}%
                </span>
              </td>
              <td className="px-4 py-3">{svc.incidents}</td>
              <td className="px-4 py-3">{svc.mttr}m</td>
              <td className="px-4 py-3">
                <span className={svc.errorRate > 0.1 ? "text-red-600" : ""}>
                  {svc.errorRate}%
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TeamLeaderboard({ teams }: { teams: TeamMetrics[] }) {
  const sorted = [...teams].sort((a, b) => a.mttr - b.mttr);

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

function ROICalculator() {
  return (
    <div className="bg-gradient-to-br from-green-500 to-emerald-600 rounded-lg p-6 text-white">
      <h3 className="font-semibold text-lg mb-4">AI ROI This Month</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-3xl font-bold">${ROI_METRICS.total_savings.toLocaleString()}</div>
          <div className="text-sm text-green-100">Total Savings</div>
        </div>
        <div>
          <div className="text-3xl font-bold">{ROI_METRICS.hours_saved}h</div>
          <div className="text-sm text-green-100">Engineer Hours Saved</div>
        </div>
        <div>
          <div className="text-3xl font-bold">{ROI_METRICS.incidents_auto_resolved}</div>
          <div className="text-sm text-green-100">Auto-Resolved Incidents</div>
        </div>
        <div>
          <div className="text-3xl font-bold">{ROI_METRICS.mttr_reduction_pct}%</div>
          <div className="text-sm text-green-100">MTTR Reduction</div>
        </div>
      </div>
    </div>
  );
}

function AIPerformanceMetrics() {
  const agentMetrics = [
    { name: "Knowledge", accuracy: 92, avgTime: 4.2 },
    { name: "Reasoning", accuracy: 88, avgTime: 5.1 },
    { name: "Action", accuracy: 85, avgTime: 3.8 },
    { name: "Learning", accuracy: 91, avgTime: 4.8 },
    { name: "Collaboration", accuracy: 89, avgTime: 2.1 },
    { name: "Ambient", accuracy: 94, avgTime: 2.8 },
  ];

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">6-Agent Performance</h3>
      <div className="space-y-3">
        {agentMetrics.map((agent) => (
          <div key={agent.name} className="flex items-center gap-3">
            <div className="w-24 text-sm font-medium">{agent.name}</div>
            <div className="flex-1">
              <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full"
                  style={{ width: `${agent.accuracy}%` }}
                />
              </div>
            </div>
            <div className="w-12 text-right text-sm font-mono">{agent.accuracy}%</div>
            <div className="w-16 text-right text-xs text-gray-500">{agent.avgTime}s</div>
          </div>
        ))}
      </div>
      <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700 flex justify-between text-sm">
        <span className="text-gray-500">Average Accuracy</span>
        <span className="font-bold">89.8%</span>
      </div>
    </div>
  );
}

export function Analytics({ onBack }: { onBack: () => void }) {
  const [timeRange, setTimeRange] = useState<"7d" | "30d" | "90d">("30d");

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
        {/* Key Metrics */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          {DEMO_METRICS.map((metric, i) => (
            <MetricCardComponent key={i} metric={metric} />
          ))}
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <IncidentTrendChart data={DEMO_TRENDS} />
          <MTTRChart />
        </div>

        {/* ROI and AI Performance */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <ROICalculator />
          <AIPerformanceMetrics />
        </div>

        {/* Tables Row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <ServiceHealthTable services={DEMO_SERVICES} />
          <TeamLeaderboard teams={DEMO_TEAMS} />
        </div>
      </div>
    </div>
  );
}
