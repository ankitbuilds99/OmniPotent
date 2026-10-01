/**
 * Service Map — the dependency graph the backend actually knows about, plus the
 * per-service metrics a configured Prometheus actually reported.
 *
 * This page used to ask for `GET /api/kubernetes/services` (a route that has
 * never existed — it 404s), query Prometheus straight from the browser at
 * localhost:9090 (unreachable, and CORS-blocked, from any real deployment), and
 * then, when both failed, *invent* the map: an error rate accumulated from
 * incident severities, a hardcoded requests_per_sec/latency of 0, a star
 * topology from whichever node was named "gateway", and an edge from every
 * service to every database.
 *
 * Now: nodes and edges come from `GET /api/knowledge-graph/topology`, metrics
 * come from `GET /api/knowledge-graph/service-metrics` (which proxies the
 * operator-configured Prometheus server-side and 503s by name when there isn't
 * one), and anything we have no reading for renders as "—" rather than 0.
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  ChevronLeft,
  ZoomIn,
  ZoomOut,
  X,
  Activity,
  Database,
  Server,
  Cloud,
  Zap,
  Loader2,
  AlertTriangle,
  RefreshCw
} from "lucide-react";

import { apiFetch } from "../lib/apiClient";

type NodeStatus = "healthy" | "degraded" | "unhealthy" | "unknown";

/** A metric we have no reading for is `null`, never 0 — 0 claims "measured, and
 *  it was zero", which is what made the old fallback map look like a healthy
 *  fleet serving no traffic. */
interface ServiceMetrics {
  requests_per_sec: number | null;
  error_rate: number | null;
  latency_p99: number | null;
}

interface ServiceNode {
  id: string;
  name: string;
  type: "service" | "database" | "cache" | "queue" | "external";
  status: NodeStatus;
  metrics: ServiceMetrics;
  x: number;
  y: number;
}

/** An edge is a recorded DEPENDS_ON/CALLS relationship. It carries no traffic
 *  numbers because the graph does not record per-edge traffic. */
interface ServiceEdge {
  from: string;
  to: string;
}

interface ServiceMapData {
  nodes: ServiceNode[];
  edges: ServiceEdge[];
}

interface TopologyNode {
  id?: string;
  name?: string;
  health_status?: string;
}

interface TopologyResponse {
  nodes?: TopologyNode[];
  edges?: { source?: string; target?: string }[];
}

interface MetricsResponse {
  source?: string;
  url?: string;
  services?: Record<string, Partial<Record<keyof ServiceMetrics, number>>>;
}

/** "—" for anything we did not measure. */
function fmt(value: number | null, digits: number, suffix = ""): string {
  return value === null ? "—" : `${value.toFixed(digits)}${suffix}`;
}

function getNodeIcon(type: ServiceNode["type"]) {
  switch (type) {
    case "service": return Server;
    case "database": return Database;
    case "cache": return Zap;
    case "queue": return Activity;
    case "external": return Cloud;
  }
}

const STATUS_COLORS: Record<NodeStatus, string> = {
  healthy: "var(--ok)",
  degraded: "#eab308",
  unhealthy: "var(--bad)",
  unknown: "var(--text-2)",
};

function classifyType(serviceName: string): ServiceNode["type"] {
  const n = serviceName.toLowerCase();
  if (n.includes("db") || n.includes("postgres") || n.includes("mysql") || n.includes("mongo")) {
    return "database";
  }
  if (n.includes("redis") || n.includes("cache") || n.includes("memcached")) return "cache";
  if (n.includes("queue") || n.includes("rabbit") || n.includes("kafka") || n.includes("sqs")) {
    return "queue";
  }
  if (n.includes("external") || n.includes("stripe") || n.includes("aws") || n.includes("s3")) {
    return "external";
  }
  return "service";
}

/** Health from measurements when we have them, otherwise whatever the graph
 *  recorded, otherwise "unknown". Never inferred from incident counts. */
export function classifyStatus(metrics: ServiceMetrics, recorded: string | undefined): NodeStatus {
  const { error_rate, latency_p99 } = metrics;
  if (error_rate !== null || latency_p99 !== null) {
    if ((error_rate ?? 0) > 5 || (latency_p99 ?? 0) > 2000) return "unhealthy";
    if ((error_rate ?? 0) > 1 || (latency_p99 ?? 0) > 500) return "degraded";
    return "healthy";
  }
  const status = (recorded || "").toLowerCase();
  if (status === "healthy" || status === "degraded" || status === "unhealthy") return status;
  return "unknown";
}

const ROW_HEIGHT = 150;
const COL_WIDTH = 220;
const PER_ROW = 5;

/**
 * Deterministic layered layout driven by the real edges: services nothing
 * depends on sit at the top, their dependencies below them, and so on. The old
 * code indexed into a fixed 8-position literal with `idx % positions.length`,
 * so a 9th service was drawn exactly on top of the 1st.
 */
export function layout(
  ids: string[],
  edges: ServiceEdge[],
): Map<string, { x: number; y: number }> {
  const known = new Set(ids);
  const outgoing = new Map<string, string[]>();
  const indegree = new Map<string, number>(ids.map(id => [id, 0]));
  for (const edge of edges) {
    if (!known.has(edge.from) || !known.has(edge.to)) continue;
    outgoing.set(edge.from, [...(outgoing.get(edge.from) ?? []), edge.to]);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  }

  const depth = new Map<string, number>();
  const roots = ids.filter(id => (indegree.get(id) ?? 0) === 0);
  let frontier = roots.length > 0 ? roots : ids.slice(0, 1);
  let level = 0;
  while (frontier.length > 0 && level <= ids.length) {
    const next: string[] = [];
    for (const id of frontier) {
      if (depth.has(id)) continue;
      depth.set(id, level);
      for (const target of outgoing.get(id) ?? []) {
        if (!depth.has(target)) next.push(target);
      }
    }
    frontier = next;
    level += 1;
  }
  // Anything inside a cycle the BFS never entered gets its own bottom row.
  const orphanLevel = level;
  for (const id of ids) if (!depth.has(id)) depth.set(id, orphanLevel);

  const byLevel = new Map<number, string[]>();
  for (const id of ids) {
    const d = depth.get(id) ?? 0;
    byLevel.set(d, [...(byLevel.get(d) ?? []), id]);
  }

  const positions = new Map<string, { x: number; y: number }>();
  let rowCursor = 0;
  for (const d of [...byLevel.keys()].sort((a, b) => a - b)) {
    const members = byLevel.get(d) ?? [];
    members.forEach((id, i) => {
      const col = i % PER_ROW;
      const row = Math.floor(i / PER_ROW);
      const wide = Math.min(members.length - row * PER_ROW, PER_ROW);
      positions.set(id, {
        x: 480 - ((wide - 1) * COL_WIDTH) / 2 + col * COL_WIDTH,
        y: 110 + (rowCursor + row) * ROW_HEIGHT,
      });
    });
    rowCursor += Math.ceil(members.length / PER_ROW);
  }
  return positions;
}

function ServiceNodeCard({
  node,
  selected,
  onSelect,
}: {
  node: ServiceNode;
  selected: boolean;
  onSelect: () => void;
}) {
  const border = STATUS_COLORS[node.status];
  const Icon = getNodeIcon(node.type);

  return (
    <motion.div
      onClick={onSelect}
      className="absolute cursor-pointer"
      style={{
        left: node.x,
        top: node.y,
        transform: "translate(-50%, -50%)",
        zIndex: selected ? 10 : 1,
      }}
      whileHover={{ scale: 1.05 }}
      animate={{ scale: selected ? 1.1 : 1 }}
    >
      <div
        className="rounded-lg p-3 shadow-lg"
        style={{
          backgroundColor: "var(--surface)",
          border: `2px solid ${border}`,
          boxShadow: selected ? `0 0 20px ${border}40` : undefined,
        }}
      >
        <div className="flex items-center gap-2 mb-2">
          <Icon size={16} style={{ color: border }} />
          <span className="font-medium text-sm whitespace-nowrap" style={{ color: "var(--text)" }}>
            {node.name}
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>RPS</div>
            <div className="font-mono" style={{ color: "var(--text)" }}>
              {fmt(node.metrics.requests_per_sec, 1)}
            </div>
          </div>
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>Err%</div>
            <div
              className="font-mono"
              style={{ color: (node.metrics.error_rate ?? 0) > 1 ? "var(--bad)" : "var(--text)" }}
            >
              {fmt(node.metrics.error_rate, 2)}
            </div>
          </div>
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>P99</div>
            <div
              className="font-mono"
              style={{ color: (node.metrics.latency_p99 ?? 0) > 500 ? "var(--bad)" : "var(--text)" }}
            >
              {fmt(node.metrics.latency_p99, 0, "ms")}
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function ServiceDetailPanel({
  node,
  edges,
  metricsNote,
  onClose,
}: {
  node: ServiceNode;
  edges: ServiceEdge[];
  metricsNote: string | null;
  onClose: () => void;
}) {
  const inbound = edges.filter(e => e.to === node.id);
  const outbound = edges.filter(e => e.from === node.id);
  const Icon = getNodeIcon(node.type);
  const hasMetrics =
    node.metrics.requests_per_sec !== null ||
    node.metrics.error_rate !== null ||
    node.metrics.latency_p99 !== null;

  return (
    <motion.div
      initial={{ x: 320, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 320, opacity: 0 }}
      className="absolute right-0 top-0 bottom-0 w-80 overflow-y-auto"
      style={{
        backgroundColor: "var(--surface)",
        borderLeft: "1px solid var(--border)",
      }}
    >
      <div
        className="sticky top-0 p-4"
        style={{
          backgroundColor: "var(--surface)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon size={20} style={{ color: STATUS_COLORS[node.status] }} />
            <h3 className="font-semibold" style={{ color: "var(--text)" }}>{node.name}</h3>
          </div>
          <button onClick={onClose} aria-label="Close details" style={{ color: "var(--text-2)" }}>
            <X size={20} />
          </button>
        </div>
        <div className="mt-2">
          <span
            className="px-2 py-0.5 text-xs font-medium rounded-full"
            data-testid="service-status"
            style={{
              backgroundColor: `${STATUS_COLORS[node.status]}20`,
              color: STATUS_COLORS[node.status],
            }}
          >
            {node.status}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-6">
        <div>
          <h4 className="text-sm font-medium mb-3" style={{ color: "var(--text-2)" }}>
            Current Metrics
          </h4>
          {!hasMetrics && (
            <p className="text-xs mb-3" style={{ color: "var(--text-2)" }}>
              {metricsNote ?? "Prometheus reported no series for this service."}
            </p>
          )}
          <div className="space-y-3">
            <div className="flex justify-between items-center" style={{ color: "var(--text)" }}>
              <span>Requests/sec</span>
              <span className="font-mono">{fmt(node.metrics.requests_per_sec, 2)}</span>
            </div>
            <div className="flex justify-between items-center">
              <span style={{ color: "var(--text)" }}>Error Rate</span>
              <span
                className="font-mono"
                style={{ color: (node.metrics.error_rate ?? 0) > 1 ? "var(--bad)" : "var(--text)" }}
              >
                {fmt(node.metrics.error_rate, 2, "%")}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span style={{ color: "var(--text)" }}>Latency P99</span>
              <span
                className="font-mono"
                style={{ color: (node.metrics.latency_p99 ?? 0) > 500 ? "var(--bad)" : "var(--text)" }}
              >
                {fmt(node.metrics.latency_p99, 0, "ms")}
              </span>
            </div>
          </div>
        </div>

        <div>
          <h4 className="text-sm font-medium mb-3" style={{ color: "var(--text-2)" }}>
            Depended on by ({inbound.length})
          </h4>
          {inbound.length === 0 ? (
            <p className="text-xs" style={{ color: "var(--text-2)" }}>No recorded dependents.</p>
          ) : (
            <div className="space-y-2">
              {inbound.map((edge) => (
                <div
                  key={`in-${edge.from}`}
                  className="text-sm p-2 rounded"
                  style={{ backgroundColor: "var(--bg)", color: "var(--text)" }}
                >
                  {edge.from}
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <h4 className="text-sm font-medium mb-3" style={{ color: "var(--text-2)" }}>
            Depends on ({outbound.length})
          </h4>
          {outbound.length === 0 ? (
            <p className="text-xs" style={{ color: "var(--text-2)" }}>No recorded dependencies.</p>
          ) : (
            <div className="space-y-2">
              {outbound.map((edge) => (
                <div
                  key={`out-${edge.to}`}
                  className="text-sm p-2 rounded"
                  style={{ backgroundColor: "var(--bg)", color: "var(--text)" }}
                >
                  {edge.to}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

function ConnectionLines({
  edges,
  nodes,
  selectedNode,
}: {
  edges: ServiceEdge[];
  nodes: ServiceNode[];
  selectedNode: string | null;
}) {
  const getNodePos = (id: string) => {
    const node = nodes.find(n => n.id === id);
    return node ? { x: node.x, y: node.y } : { x: 0, y: 0 };
  };

  return (
    <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ zIndex: 0 }}>
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#6b7280" />
        </marker>
        <marker id="arrowhead-selected" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#3b82f6" />
        </marker>
      </defs>
      {edges.map((edge) => {
        const from = getNodePos(edge.from);
        const to = getNodePos(edge.to);
        const isSelected = selectedNode === edge.from || selectedNode === edge.to;
        const opacity = selectedNode && !isSelected ? 0.2 : 1;

        return (
          <line
            key={`${edge.from}->${edge.to}`}
            data-testid="dependency-edge"
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke={isSelected ? "var(--accent)" : "#6b7280"}
            strokeWidth={isSelected ? 2.5 : 1.5}
            markerEnd={isSelected ? "url(#arrowhead-selected)" : "url(#arrowhead)"}
            style={{ opacity, transition: "opacity 0.2s" }}
          />
        );
      })}
    </svg>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center h-full">
      <Loader2 className="w-8 h-8 animate-spin" style={{ color: "var(--accent)" }} />
      <p className="mt-4 text-sm" style={{ color: "var(--text-2)" }}>Loading service map...</p>
    </div>
  );
}

function EmptyState({ error }: { error: string | null }) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      {error ? (
        <AlertTriangle className="w-12 h-12" style={{ color: "var(--bad)" }} />
      ) : (
        <Server className="w-12 h-12" style={{ color: "var(--text-2)" }} />
      )}
      <p className="mt-4 text-lg font-medium" style={{ color: "var(--text)" }}>
        {error ? "Could not load the service map" : "No services in the knowledge graph"}
      </p>
      <p className="text-sm max-w-lg" style={{ color: "var(--text-2)" }}>
        {error ??
          "Register services with POST /api/knowledge-graph/services, or turn on graph sync, and their recorded dependencies will be drawn here."}
      </p>
    </div>
  );
}

/** Read an error body without assuming it is JSON — a 502 from a proxy is HTML. */
async function errorDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed?.detail === "string") return parsed.detail;
  } catch {
    /* not JSON */
  }
  return text.slice(0, 300) || `${res.status} ${res.statusText}`;
}

export function ServiceMap({ onBack }: { onBack: () => void }) {
  const [mapData, setMapData] = useState<ServiceMapData>({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [loading, setLoading] = useState(true);
  const [topologyError, setTopologyError] = useState<string | null>(null);
  const [metricsNote, setMetricsNote] = useState<string | null>(null);

  const fetchServiceMap = useCallback(async () => {
    // Metrics are best-effort: a map with no numbers is still a real map, so a
    // 503 from an unconfigured Prometheus must not blank the topology — it just
    // means every reading stays null and we say why.
    let measured: Record<string, Partial<ServiceMetrics>> = {};
    let note: string | null = null;
    try {
      const res = await apiFetch("/api/knowledge-graph/service-metrics");
      if (res.ok) {
        const body = (await res.json()) as MetricsResponse;
        measured = body.services ?? {};
        note =
          Object.keys(measured).length === 0
            ? `${body.url ?? "Prometheus"} returned no series for these queries.`
            : null;
      } else {
        note = await errorDetail(res);
      }
    } catch (err) {
      note = `could not reach the metrics endpoint: ${(err as Error).message}`;
    }

    try {
      const res = await apiFetch("/api/knowledge-graph/topology");
      if (!res.ok) {
        setMetricsNote(note);
        setTopologyError(await errorDetail(res));
        setMapData({ nodes: [], edges: [] });
        return;
      }
      const body = (await res.json()) as TopologyResponse;
      const raw = (body.nodes ?? []).filter(
        (n): n is TopologyNode & { id: string } => typeof n.id === "string" && n.id.length > 0,
      );
      const ids = raw.map(n => n.id);
      const known = new Set(ids);
      const edges: ServiceEdge[] = (body.edges ?? [])
        .filter(e => typeof e.source === "string" && typeof e.target === "string")
        .map(e => ({ from: e.source as string, to: e.target as string }))
        .filter(e => known.has(e.from) && known.has(e.to));

      const positions = layout(ids, edges);
      const nodes: ServiceNode[] = raw.map(n => {
        const m = measured[n.id] ?? measured[n.name ?? ""] ?? {};
        const metrics: ServiceMetrics = {
          requests_per_sec: m.requests_per_sec ?? null,
          error_rate: m.error_rate ?? null,
          latency_p99: m.latency_p99 ?? null,
        };
        const pos = positions.get(n.id) ?? { x: 480, y: 110 };
        return {
          id: n.id,
          name: n.name || n.id,
          type: classifyType(n.name || n.id),
          status: classifyStatus(metrics, n.health_status),
          metrics,
          x: pos.x,
          y: pos.y,
        };
      });

      setTopologyError(null);
      setMetricsNote(note);
      setMapData({ nodes, edges });
    } catch (err) {
      setTopologyError(`Could not load the topology: ${(err as Error).message}`);
      setMapData({ nodes: [], edges: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServiceMap();
    const interval = setInterval(fetchServiceMap, 15000);
    return () => clearInterval(interval);
  }, [fetchServiceMap]);

  const selectedNodeData = mapData.nodes.find(n => n.id === selectedNode);

  return (
    <div className="min-h-screen flex flex-col omni-workspace-page" style={{ backgroundColor: "var(--bg)" }}>
      <header className="nav flex-shrink-0">
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
            <Activity size={20} style={{ display: "inline", marginRight: "0.5rem", color: "var(--accent)" }} />
            Service Map
          </h1>
          <span className="text-sm" style={{ color: "var(--text-2)" }}>
            {mapData.nodes.length} services · {mapData.edges.length} recorded dependencies
          </span>
        </div>
        <div className="nav__right">
          <button
            onClick={fetchServiceMap}
            className="p-2 rounded-lg mr-2"
            style={{ border: "1px solid var(--border)" }}
            title="Refresh"
            aria-label="Refresh"
          >
            <RefreshCw size={16} />
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setZoom(z => Math.max(0.5, z - 0.1))}
              className="p-2 rounded transition-colors"
              aria-label="Zoom out"
              style={{ color: "var(--text-2)" }}
            >
              <ZoomOut size={18} />
            </button>
            <span className="text-sm font-mono w-12 text-center" style={{ color: "var(--text)" }}>
              {Math.round(zoom * 100)}%
            </span>
            <button
              onClick={() => setZoom(z => Math.min(2, z + 0.1))}
              className="p-2 rounded transition-colors"
              aria-label="Zoom in"
              style={{ color: "var(--text-2)" }}
            >
              <ZoomIn size={18} />
            </button>
          </div>
          <div className="h-6 w-px" style={{ backgroundColor: "var(--border)" }} />
          <div className="flex items-center gap-4 text-sm">
            {(["healthy", "degraded", "unhealthy", "unknown"] as NodeStatus[]).map(s => (
              <div key={s} className="flex items-center gap-1">
                <span className="w-3 h-3 rounded-full" style={{ backgroundColor: STATUS_COLORS[s] }} />
                <span style={{ color: "var(--text-2)", textTransform: "capitalize" }}>{s}</span>
              </div>
            ))}
          </div>
        </div>
      </header>

      {metricsNote && !loading && (
        <div
          className="px-4 py-2 text-sm flex items-center gap-2 flex-shrink-0"
          role="status"
          style={{
            backgroundColor: "var(--surface)",
            borderBottom: "1px solid var(--border)",
            color: "var(--text-2)",
          }}
        >
          <AlertTriangle size={14} style={{ color: "#eab308" }} />
          <span>Live metrics unavailable — {metricsNote}</span>
        </div>
      )}

      <div className="flex-1 relative overflow-hidden">
        {loading ? (
          <LoadingState />
        ) : mapData.nodes.length === 0 ? (
          <EmptyState error={topologyError} />
        ) : (
          <div
            className="absolute inset-0"
            style={{
              transform: `scale(${zoom})`,
              transformOrigin: "center center",
              transition: "transform 0.2s",
            }}
            onClick={(e) => {
              if (e.target === e.currentTarget) {
                setSelectedNode(null);
              }
            }}
          >
            <ConnectionLines
              edges={mapData.edges}
              nodes={mapData.nodes}
              selectedNode={selectedNode}
            />

            {mapData.nodes.map((node) => (
              <ServiceNodeCard
                key={node.id}
                node={node}
                selected={selectedNode === node.id}
                onSelect={() => setSelectedNode(node.id === selectedNode ? null : node.id)}
              />
            ))}
          </div>
        )}

        {selectedNodeData && (
          <ServiceDetailPanel
            node={selectedNodeData}
            edges={mapData.edges}
            metricsNote={metricsNote}
            onClose={() => setSelectedNode(null)}
          />
        )}
      </div>
    </div>
  );
}
