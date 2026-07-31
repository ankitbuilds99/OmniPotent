/**
 * Service Map - Live dependency graph visualization
 */

import { useState, useEffect } from "react";
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
  Zap
} from "lucide-react";

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

const DEMO_MAP: ServiceMapData = {
  nodes: [
    { id: "api-gateway", name: "API Gateway", type: "service", status: "healthy", metrics: { requests_per_sec: 1250, error_rate: 0.02, latency_p99: 45 }, x: 400, y: 50 },
    { id: "auth-service", name: "Auth Service", type: "service", status: "healthy", metrics: { requests_per_sec: 890, error_rate: 0.01, latency_p99: 23 }, x: 150, y: 150 },
    { id: "user-service", name: "User Service", type: "service", status: "healthy", metrics: { requests_per_sec: 450, error_rate: 0.03, latency_p99: 67 }, x: 650, y: 150 },
    { id: "checkout-service", name: "Checkout Service", type: "service", status: "unhealthy", metrics: { requests_per_sec: 320, error_rate: 8.5, latency_p99: 2340 }, x: 250, y: 280 },
    { id: "payment-gateway", name: "Payment Gateway", type: "external", status: "healthy", metrics: { requests_per_sec: 180, error_rate: 0.5, latency_p99: 890 }, x: 100, y: 400 },
    { id: "inventory-service", name: "Inventory Service", type: "service", status: "degraded", metrics: { requests_per_sec: 210, error_rate: 2.1, latency_p99: 456 }, x: 400, y: 280 },
    { id: "notification-service", name: "Notification Service", type: "service", status: "healthy", metrics: { requests_per_sec: 560, error_rate: 0.04, latency_p99: 34 }, x: 550, y: 280 },
    { id: "postgres-main", name: "PostgreSQL (Main)", type: "database", status: "healthy", metrics: { requests_per_sec: 2100, error_rate: 0.01, latency_p99: 12 }, x: 320, y: 420 },
    { id: "redis-cache", name: "Redis Cache", type: "cache", status: "healthy", metrics: { requests_per_sec: 8500, error_rate: 0.001, latency_p99: 2 }, x: 500, y: 420 },
    { id: "rabbitmq", name: "RabbitMQ", type: "queue", status: "healthy", metrics: { requests_per_sec: 1200, error_rate: 0.02, latency_p99: 5 }, x: 680, y: 420 },
  ],
  edges: [
    { from: "api-gateway", to: "auth-service", requests_per_sec: 890, error_rate: 0.01 },
    { from: "api-gateway", to: "user-service", requests_per_sec: 450, error_rate: 0.03 },
    { from: "api-gateway", to: "checkout-service", requests_per_sec: 320, error_rate: 8.5 },
    { from: "api-gateway", to: "inventory-service", requests_per_sec: 210, error_rate: 2.1 },
    { from: "checkout-service", to: "payment-gateway", requests_per_sec: 180, error_rate: 0.5 },
    { from: "checkout-service", to: "inventory-service", requests_per_sec: 150, error_rate: 1.2 },
    { from: "checkout-service", to: "postgres-main", requests_per_sec: 280, error_rate: 0.02 },
    { from: "inventory-service", to: "postgres-main", requests_per_sec: 400, error_rate: 0.01 },
    { from: "user-service", to: "postgres-main", requests_per_sec: 350, error_rate: 0.01 },
    { from: "auth-service", to: "redis-cache", requests_per_sec: 2000, error_rate: 0.001 },
    { from: "notification-service", to: "rabbitmq", requests_per_sec: 560, error_rate: 0.02 },
    { from: "checkout-service", to: "notification-service", requests_per_sec: 180, error_rate: 0.04 },
  ],
};

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
            <div className="font-mono" style={{ color: "var(--text)" }}>{node.metrics.requests_per_sec}</div>
          </div>
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>Err%</div>
            <div
              className="font-mono"
              style={{ color: node.metrics.error_rate > 1 ? "var(--bad)" : "var(--text)" }}
            >
              {node.metrics.error_rate.toFixed(1)}
            </div>
          </div>
          <div className="text-center">
            <div style={{ color: "var(--text-2)" }}>P99</div>
            <div
              className="font-mono"
              style={{ color: node.metrics.latency_p99 > 500 ? "var(--bad)" : "var(--text)" }}
            >
              {node.metrics.latency_p99}ms
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
              <span className="font-mono">{node.metrics.requests_per_sec}</span>
            </div>
            <div className="flex justify-between items-center">
              <span style={{ color: "var(--text)" }}>Error Rate</span>
              <span
                className="font-mono"
                style={{ color: node.metrics.error_rate > 1 ? "var(--bad)" : "var(--text)" }}
              >
                {node.metrics.error_rate}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span style={{ color: "var(--text)" }}>Latency P99</span>
              <span
                className="font-mono"
                style={{ color: node.metrics.latency_p99 > 500 ? "var(--bad)" : "var(--text)" }}
              >
                {node.metrics.latency_p99}ms
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
                  <span style={{ color: "var(--text)" }}>← {edge.from}</span>
                  <span className="font-mono text-xs" style={{ color: "var(--text-2)" }}>
                    {edge.requests_per_sec} rps
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
                  <span style={{ color: "var(--text)" }}>→ {edge.to}</span>
                  <span className="font-mono text-xs" style={{ color: "var(--text-2)" }}>
                    {edge.requests_per_sec} rps
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
                🔬 Investigate with AI
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

export function ServiceMap({ onBack }: { onBack: () => void }) {
  const [mapData] = useState<ServiceMapData>(DEMO_MAP);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);

  const selectedNodeData = mapData.nodes.find(n => n.id === selectedNode);

  // Simulate live updates
  useEffect(() => {
    const interval = setInterval(() => {
      // Would update metrics here
    }, 5000);
    return () => clearInterval(interval);
  }, []);

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
          <span className="text-sm" style={{ color: "var(--text-2)" }}>Live dependency graph</span>
        </div>
        <div className="nav__right">
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
