/**
 * Service Map - Live dependency graph visualization with real metrics from Prometheus
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
  RefreshCw
} from "lucide-react";

import { apiFetch } from "../lib/apiClient";

interface ServiceNode {
  id: string;
  name: string;
  type: "service" | "database" | "cache" | "queue" | "external";
  status: "healthy" | "degraded" | "unhealthy";
  metrics: {
    requests_per_sec: number;
    error_rate: number;
    latency_p99: number;
  };
  x: number;
  y: number;
}

interface ServiceEdge {
  from: string;
  to: string;
  requests_per_sec: number;
  error_rate: number;
}

interface ServiceMapData {
  nodes: ServiceNode[];
  edges: ServiceEdge[];
}

interface PrometheusResult {
  metric: Record<string, string>;
  value: [number, string];
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

function ServiceNodeCard({
  node,
  selected,
  onSelect,
}: {
  node: ServiceNode;
  selected: boolean;
  onSelect: () => void;
}) {
  const statusColors = {
    healthy: { border: "var(--ok)", bg: "rgba(45, 212, 167, 0.1)" },
    degraded: { border: "#eab308", bg: "rgba(234, 179, 8, 0.1)" },
    unhealthy: { border: "var(--bad)", bg: "rgba(255, 107, 107, 0.1)" },
  };
  const colors = statusColors[node.status];
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
          border: `2px solid ${colors.border}`,
          boxShadow: selected ? `0 0 20px ${colors.border}40` : undefined,
        }}
      >
        <div className="flex items-center gap-2 mb-2">
          <Icon size={16} style={{ color: colors.border }} />
          <span className="font-medium text-sm whitespace-nowrap" style={{ color: "var(--text)" }}>
            {node.name}
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>RPS</div>
            <div className="font-mono" style={{ color: "var(--text)" }}>
              {node.metrics.requests_per_sec.toFixed(1)}
            </div>
          </div>
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>Err%</div>
            <div
              className="font-mono"
              style={{ color: node.metrics.error_rate > 1 ? "var(--bad)" : "var(--text)" }}
            >
              {node.metrics.error_rate.toFixed(2)}
            </div>
          </div>
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>P99</div>
            <div
              className="font-mono"
              style={{ color: node.metrics.latency_p99 > 500 ? "var(--bad)" : "var(--text)" }}
            >
              {node.metrics.latency_p99.toFixed(0)}ms
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
  onClose,
}: {
  node: ServiceNode;
  edges: ServiceEdge[];
  onClose: () => void;
}) {
  const inbound = edges.filter(e => e.to === node.id);
  const outbound = edges.filter(e => e.from === node.id);
  const Icon = getNodeIcon(node.type);

  const statusColors = {
    healthy: "var(--ok)",
    degraded: "#eab308",
    unhealthy: "var(--bad)",
  };

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
            <Icon size={20} style={{ color: statusColors[node.status] }} />
            <h3 className="font-semibold" style={{ color: "var(--text)" }}>{node.name}</h3>
          </div>
          <button onClick={onClose} style={{ color: "var(--text-2)" }}>
            <X size={20} />
          </button>
        </div>
        <div className="mt-2">
          <span
            className="px-2 py-0.5 text-xs font-medium rounded-full"
            style={{
              backgroundColor: `${statusColors[node.status]}20`,
              color: statusColors[node.status],
            }}
          >
            {node.status}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-6">
        {/* Metrics */}
        <div>
          <h4 className="text-sm font-medium mb-3" style={{ color: "var(--text-2)" }}>
            Current Metrics
          </h4>
          <div className="space-y-3">
            <div className="flex justify-between items-center" style={{ color: "var(--text)" }}>
              <span>Requests/sec</span>
              <span className="font-mono">{node.metrics.requests_per_sec.toFixed(2)}</span>
            </div>
            <div className="flex justify-between items-center">
              <span style={{ color: "var(--text)" }}>Error Rate</span>
              <span
                className="font-mono"
                style={{ color: node.metrics.error_rate > 1 ? "var(--bad)" : "var(--text)" }}
              >
                {node.metrics.error_rate.toFixed(2)}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span style={{ color: "var(--text)" }}>Latency P99</span>
              <span
                className="font-mono"
                style={{ color: node.metrics.latency_p99 > 500 ? "var(--bad)" : "var(--text)" }}
              >
                {node.metrics.latency_p99.toFixed(0)}ms
              </span>
            </div>
          </div>
        </div>

        {/* Inbound */}
        {inbound.length > 0 && (
          <div>
            <h4 className="text-sm font-medium mb-3" style={{ color: "var(--text-2)" }}>
              Inbound ({inbound.length})
            </h4>
            <div className="space-y-2">
              {inbound.map((edge, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-sm p-2 rounded"
                  style={{ backgroundColor: "var(--bg)" }}
                >
                  <span style={{ color: "var(--text)" }}>from {edge.from}</span>
                  <span className="font-mono text-xs" style={{ color: "var(--text-2)" }}>
                    {edge.requests_per_sec.toFixed(1)} rps
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Outbound */}
        {outbound.length > 0 && (
          <div>
            <h4 className="text-sm font-medium mb-3" style={{ color: "var(--text-2)" }}>
              Outbound ({outbound.length})
            </h4>
            <div className="space-y-2">
              {outbound.map((edge, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-sm p-2 rounded"
                  style={{ backgroundColor: "var(--bg)" }}
                >
                  <span style={{ color: "var(--text)" }}>to {edge.to}</span>
                  <span className="font-mono text-xs" style={{ color: "var(--text-2)" }}>
                    {edge.requests_per_sec.toFixed(1)} rps
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Actions */}
        <div>
          <h4 className="text-sm font-medium mb-3" style={{ color: "var(--text-2)" }}>
            Quick Actions
          </h4>
          <div className="space-y-2">
            {["View Traces", "View Metrics", "View Logs"].map((action) => (
              <button
                key={action}
                className="w-full px-3 py-2 text-left text-sm rounded transition-colors"
                style={{ border: "1px solid var(--border)", color: "var(--text)" }}
              >
                {action}
              </button>
            ))}
            {node.status !== "healthy" && (
              <button
                className="w-full px-3 py-2 text-left text-sm rounded font-medium"
                style={{ backgroundColor: "var(--accent)", color: "var(--bg)" }}
              >
                Investigate with AI
              </button>
            )}
          </div>
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
        <marker id="arrowhead-error" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#ef4444" />
        </marker>
        <marker id="arrowhead-selected" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#3b82f6" />
        </marker>
      </defs>
      {edges.map((edge, i) => {
        const from = getNodePos(edge.from);
        const to = getNodePos(edge.to);
        const isSelected = selectedNode === edge.from || selectedNode === edge.to;
        const isError = edge.error_rate > 1;

        const color = isSelected ? "var(--accent)" : isError ? "var(--bad)" : "#6b7280";
        const marker = isSelected ? "url(#arrowhead-selected)" : isError ? "url(#arrowhead-error)" : "url(#arrowhead)";
        const strokeWidth = isSelected ? 2.5 : isError ? 2 : 1.5;
        const opacity = selectedNode && !isSelected ? 0.2 : 1;

        return (
          <line
            key={i}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke={color}
            strokeWidth={strokeWidth}
            strokeDasharray={isError ? "5,5" : undefined}
            markerEnd={marker}
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

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full">
      <Server className="w-12 h-12" style={{ color: "var(--text-2)" }} />
      <p className="mt-4 text-lg font-medium" style={{ color: "var(--text)" }}>No Services</p>
      <p className="text-sm" style={{ color: "var(--text-2)" }}>
        Connect Prometheus or Kubernetes to see live service metrics
      </p>
    </div>
  );
}

async function queryPrometheus(query: string): Promise<PrometheusResult[]> {
  const prometheusUrl = import.meta.env.VITE_PROMETHEUS_URL || "http://localhost:9090";
  try {
    const response = await fetch(
      `${prometheusUrl}/api/v1/query?query=${encodeURIComponent(query)}`
    );
    if (!response.ok) return [];
    const data = await response.json();
    return data.status === "success" ? data.data.result : [];
  } catch {
    return [];
  }
}

export function ServiceMap({ onBack }: { onBack: () => void }) {
  const [mapData, setMapData] = useState<ServiceMapData>({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [loading, setLoading] = useState(true);
  const [dataSource, setDataSource] = useState<"prometheus" | "kubernetes" | "fallback">("fallback");

  const fetchServiceMap = useCallback(async () => {
    try {
      const serviceMetrics = new Map<string, {
        requests_per_sec: number;
        error_rate: number;
        latency_p99: number;
        type: ServiceNode["type"];
      }>();

      // Try to fetch from Prometheus first
      const [rpsResults, errorResults, latencyResults] = await Promise.all([
        queryPrometheus('sum(rate(http_requests_total[5m])) by (service)'),
        queryPrometheus('sum(rate(http_requests_total{status=~"5.."}[5m])) by (service) / sum(rate(http_requests_total[5m])) by (service) * 100'),
        queryPrometheus('histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service)) * 1000'),
      ]);

      // Process Prometheus results
      rpsResults.forEach((result: PrometheusResult) => {
        const service = result.metric.service || result.metric.job || "unknown";
        if (!serviceMetrics.has(service)) {
          serviceMetrics.set(service, {
            requests_per_sec: 0,
            error_rate: 0,
            latency_p99: 0,
            type: "service",
          });
        }
        serviceMetrics.get(service)!.requests_per_sec = parseFloat(result.value[1]) || 0;
      });

      errorResults.forEach((result: PrometheusResult) => {
        const service = result.metric.service || result.metric.job || "unknown";
        if (serviceMetrics.has(service)) {
          serviceMetrics.get(service)!.error_rate = parseFloat(result.value[1]) || 0;
        }
      });

      latencyResults.forEach((result: PrometheusResult) => {
        const service = result.metric.service || result.metric.job || "unknown";
        if (serviceMetrics.has(service)) {
          serviceMetrics.get(service)!.latency_p99 = parseFloat(result.value[1]) || 0;
        }
      });

      // If Prometheus returned data, use it
      if (serviceMetrics.size > 0) {
        setDataSource("prometheus");
      } else {
        // Try Kubernetes API
        try {
          const k8sRes = await apiFetch(`/api/kubernetes/services`);
          if (k8sRes.ok) {
            const services = await k8sRes.json();
            services.forEach((svc: { name: string; namespace: string; type: string }) => {
              serviceMetrics.set(svc.name, {
                requests_per_sec: 0,
                error_rate: 0,
                latency_p99: 0,
                type: svc.type === "database" ? "database" : "service",
              });
            });
            setDataSource("kubernetes");
          }
        } catch {
          // Kubernetes not available
        }
      }

      // If still no data, try to get from incidents/alerts
      if (serviceMetrics.size === 0) {
        setDataSource("fallback");

        const incidentsRes = await apiFetch(`/api/incidents`);
        const incidents = incidentsRes.ok ? await incidentsRes.json() : [];

        const alertsRes = await apiFetch(`/oncall/alerts?limit=50`);
        const alerts = alertsRes.ok ? await alertsRes.json() : [];

        incidents.forEach((inc: Record<string, unknown>) => {
          const service = (inc.service as string) || "api-gateway";
          if (!serviceMetrics.has(service)) {
            serviceMetrics.set(service, {
              requests_per_sec: 0,
              error_rate: 0,
              latency_p99: 0,
              type: "service",
            });
          }
          // Increment error rate based on incident severity
          const severity = (inc.severity as string) || "medium";
          const errorIncrement = severity === "critical" ? 5 : severity === "high" ? 2 : 0.5;
          serviceMetrics.get(service)!.error_rate += errorIncrement;
        });

        alerts.forEach((alert: Record<string, unknown>) => {
          const service = (alert.source as string) || "unknown-service";
          if (!serviceMetrics.has(service)) {
            serviceMetrics.set(service, {
              requests_per_sec: 0,
              error_rate: 0,
              latency_p99: 0,
              type: "service",
            });
          }
          serviceMetrics.get(service)!.error_rate += 0.5;
        });
      }

      // Generate nodes with proper layout
      const nodes: ServiceNode[] = [];
      const edges: ServiceEdge[] = [];

      const positions = [
        { x: 400, y: 100 },
        { x: 200, y: 220 },
        { x: 600, y: 220 },
        { x: 120, y: 360 },
        { x: 400, y: 360 },
        { x: 680, y: 360 },
        { x: 200, y: 480 },
        { x: 600, y: 480 },
      ];

      let idx = 0;
      serviceMetrics.forEach((data, serviceName) => {
        const pos = positions[idx % positions.length];

        // Determine node type from name
        let type: ServiceNode["type"] = data.type;
        if (serviceName.includes("db") || serviceName.includes("postgres") || serviceName.includes("mysql") || serviceName.includes("mongo")) {
          type = "database";
        } else if (serviceName.includes("redis") || serviceName.includes("cache") || serviceName.includes("memcached")) {
          type = "cache";
        } else if (serviceName.includes("queue") || serviceName.includes("rabbit") || serviceName.includes("kafka") || serviceName.includes("sqs")) {
          type = "queue";
        } else if (serviceName.includes("external") || serviceName.includes("stripe") || serviceName.includes("aws") || serviceName.includes("s3")) {
          type = "external";
        }

        // Determine health status
        let status: ServiceNode["status"] = "healthy";
        if (data.error_rate > 5 || data.latency_p99 > 2000) {
          status = "unhealthy";
        } else if (data.error_rate > 1 || data.latency_p99 > 500) {
          status = "degraded";
        }

        nodes.push({
          id: serviceName,
          name: serviceName,
          type,
          status,
          metrics: {
            requests_per_sec: data.requests_per_sec,
            error_rate: data.error_rate,
            latency_p99: data.latency_p99,
          },
          x: pos.x,
          y: pos.y,
        });
        idx++;
      });

      // Generate edges based on service relationships
      if (nodes.length > 1) {
        // Find gateway/ingress service
        const gateway = nodes.find(n =>
          n.name.includes("gateway") ||
          n.name.includes("ingress") ||
          n.name.includes("frontend")
        ) || nodes[0];

        // Connect gateway to other services
        nodes.forEach((node) => {
          if (node.id !== gateway.id && node.type === "service") {
            edges.push({
              from: gateway.id,
              to: node.id,
              requests_per_sec: node.metrics.requests_per_sec * 0.8,
              error_rate: node.metrics.error_rate,
            });
          }
        });

        // Connect services to databases/caches
        const dbNodes = nodes.filter(n => n.type === "database" || n.type === "cache");
        const serviceNodes = nodes.filter(n => n.type === "service" && n.id !== gateway.id);

        serviceNodes.forEach((svc) => {
          dbNodes.forEach((db) => {
            edges.push({
              from: svc.id,
              to: db.id,
              requests_per_sec: svc.metrics.requests_per_sec * 0.5,
              error_rate: db.metrics.error_rate,
            });
          });
        });
      }

      setMapData({ nodes, edges });
    } catch (error) {
      console.error("Failed to fetch service map:", error);
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
    <div className="min-h-screen flex flex-col" style={{ backgroundColor: "var(--bg)" }}>
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
            Live dependency graph
            {dataSource === "prometheus" && " (Prometheus)"}
            {dataSource === "kubernetes" && " (Kubernetes)"}
            {dataSource === "fallback" && " (Incidents)"}
          </span>
        </div>
        <div className="nav__right">
          <button
            onClick={fetchServiceMap}
            className="p-2 rounded-lg mr-2"
            style={{ border: "1px solid var(--border)" }}
            title="Refresh"
          >
            <RefreshCw size={16} />
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setZoom(z => Math.max(0.5, z - 0.1))}
              className="p-2 rounded transition-colors"
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
              style={{ color: "var(--text-2)" }}
            >
              <ZoomIn size={18} />
            </button>
          </div>
          <div className="h-6 w-px" style={{ backgroundColor: "var(--border)" }} />
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: "var(--ok)" }} />
              <span style={{ color: "var(--text-2)" }}>Healthy</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: "#eab308" }} />
              <span style={{ color: "var(--text-2)" }}>Degraded</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: "var(--bad)" }} />
              <span style={{ color: "var(--text-2)" }}>Unhealthy</span>
            </div>
          </div>
        </div>
      </header>

      <div className="flex-1 relative overflow-hidden">
        {loading ? (
          <LoadingState />
        ) : mapData.nodes.length === 0 ? (
          <EmptyState />
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
            onClose={() => setSelectedNode(null)}
          />
        )}
      </div>
    </div>
  );
}
