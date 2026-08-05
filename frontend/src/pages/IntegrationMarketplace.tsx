/**
 * Integration Marketplace - Real API integration
 * Fetches connected integrations from /admin/integrations and /integrations/status
 */

import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";

const API_BASE = import.meta.env.VITE_API_URL || "";

interface Integration {
  id: string;
  name: string;
  description: string;
  category: "observability" | "alerting" | "code" | "infrastructure" | "chat" | "knowledge";
  logo: string;
  status: "connected" | "available" | "coming_soon";
  features: string[];
  setupTime: string;
}

// Static catalog of available integrations (metadata only)
const INTEGRATION_CATALOG: Omit<Integration, "status">[] = [
  // Observability
  {
    id: "prometheus",
    name: "Prometheus",
    description: "Query metrics and alerts from your Prometheus server",
    category: "observability",
    logo: "https://raw.githubusercontent.com/cncf/artwork/master/projects/prometheus/icon/color/prometheus-icon-color.svg",
    features: ["Metric queries", "Alert rules", "Recording rules", "Service discovery"],
    setupTime: "5 min",
  },
  {
    id: "datadog",
    name: "Datadog",
    description: "Full-stack observability with APM, logs, and metrics",
    category: "observability",
    logo: "https://imgix.datadoghq.com/img/dd_logo_n_70x75.png",
    features: ["APM traces", "Log search", "Metrics", "Dashboards", "Monitors"],
    setupTime: "10 min",
  },
  {
    id: "grafana",
    name: "Grafana",
    description: "Query dashboards and visualizations",
    category: "observability",
    logo: "https://raw.githubusercontent.com/grafana/grafana/main/public/img/grafana_icon.svg",
    features: ["Dashboard queries", "Annotations", "Alerting", "Data sources"],
    setupTime: "5 min",
  },
  {
    id: "cloudwatch",
    name: "AWS CloudWatch",
    description: "AWS native monitoring and observability",
    category: "observability",
    logo: "https://d1.awsstatic.com/product-marketing/CloudWatch/product-page-diagram_Amazon-CloudWatch.5c0c7b56646dff9d2c27f8a2c96e7631f87b85d1.png",
    features: ["Metrics", "Logs Insights", "Alarms", "Events"],
    setupTime: "10 min",
  },
  {
    id: "newrelic",
    name: "New Relic",
    description: "Full-stack observability platform",
    category: "observability",
    logo: "https://newrelic.com/themes/flavor/assets/images/logo/logo.svg",
    features: ["APM", "Infrastructure", "Logs", "Synthetics"],
    setupTime: "10 min",
  },

  // Alerting
  {
    id: "pagerduty",
    name: "PagerDuty",
    description: "Incident management and on-call scheduling",
    category: "alerting",
    logo: "https://www.pagerduty.com/wp-content/uploads/2022/09/pd-logo-green.svg",
    features: ["Incident webhooks", "On-call schedules", "Escalation policies", "Auto-acknowledge"],
    setupTime: "5 min",
  },
  {
    id: "opsgenie",
    name: "OpsGenie",
    description: "Alert management and on-call scheduling",
    category: "alerting",
    logo: "https://www.atlassian.com/dam/jcr:a6df169b-2d56-4035-8c60-36a69291c4de/opsgenie-icon-gradient-blue.svg",
    features: ["Alerts", "On-call", "Escalations", "Integrations"],
    setupTime: "5 min",
  },
  {
    id: "alertmanager",
    name: "Alertmanager",
    description: "Prometheus Alertmanager webhooks",
    category: "alerting",
    logo: "https://raw.githubusercontent.com/cncf/artwork/master/projects/prometheus/icon/color/prometheus-icon-color.svg",
    features: ["Webhook receiver", "Alert grouping", "Silences", "Inhibition"],
    setupTime: "3 min",
  },

  // Code
  {
    id: "github",
    name: "GitHub",
    description: "Repository analysis and deployment correlation",
    category: "code",
    logo: "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
    features: ["Commit history", "PR analysis", "Deployment events", "Code search"],
    setupTime: "5 min",
  },
  {
    id: "gitlab",
    name: "GitLab",
    description: "GitLab repository and CI/CD integration",
    category: "code",
    logo: "https://about.gitlab.com/images/press/logo/png/gitlab-icon-rgb.png",
    features: ["Commits", "Merge requests", "Pipelines", "Deployments"],
    setupTime: "5 min",
  },
  {
    id: "bitbucket",
    name: "Bitbucket",
    description: "Atlassian Bitbucket repository integration",
    category: "code",
    logo: "https://wac-cdn.atlassian.com/dam/jcr:c942540c-53a6-4e49-9f9a-8f3e9e6c6e5a/bitbucket-icon-gradient-blue.svg",
    features: ["Commits", "Pull requests", "Pipelines"],
    setupTime: "5 min",
  },

  // Infrastructure
  {
    id: "kubernetes",
    name: "Kubernetes",
    description: "Cluster state, pods, deployments, and events",
    category: "infrastructure",
    logo: "https://raw.githubusercontent.com/kubernetes/kubernetes/master/logo/logo.svg",
    features: ["Pod status", "Deployments", "Events", "Logs", "Resource usage"],
    setupTime: "15 min",
  },
  {
    id: "aws",
    name: "AWS",
    description: "Amazon Web Services infrastructure",
    category: "infrastructure",
    logo: "https://a0.awsstatic.com/libra-css/images/logos/aws_smile-header-desktop-en-white_59x35.png",
    features: ["EC2", "ECS", "Lambda", "RDS", "S3", "CloudFormation"],
    setupTime: "15 min",
  },
  {
    id: "gcp",
    name: "Google Cloud",
    description: "Google Cloud Platform services",
    category: "infrastructure",
    logo: "https://www.gstatic.com/devrel-devsite/prod/v1c4b1e8e9e8e0b8e8e9e8e0b8e8e9e8e0b8e8e9e/cloud/images/favicons/onecloud/favicon.ico",
    features: ["GKE", "Cloud Run", "Cloud Functions", "BigQuery"],
    setupTime: "15 min",
  },
  {
    id: "terraform",
    name: "Terraform",
    description: "Infrastructure as Code state and changes",
    category: "infrastructure",
    logo: "https://www.terraform.io/img/logo-hashicorp.svg",
    features: ["State inspection", "Plan analysis", "Drift detection"],
    setupTime: "10 min",
  },
  {
    id: "argocd",
    name: "ArgoCD",
    description: "GitOps continuous delivery for Kubernetes",
    category: "infrastructure",
    logo: "https://raw.githubusercontent.com/argoproj/argoproj/master/docs/assets/argo.png",
    features: ["App sync", "Rollback", "Health status", "Diff preview"],
    setupTime: "10 min",
  },

  // Chat
  {
    id: "slack",
    name: "Slack",
    description: "Chat integration with @mention support",
    category: "chat",
    logo: "https://a.slack-edge.com/80588/marketing/img/icons/icon_slack_hash_colored.png",
    features: ["@mention queries", "Alert channels", "Investigation threads", "Slash commands"],
    setupTime: "5 min",
  },
  {
    id: "msteams",
    name: "Microsoft Teams",
    description: "Teams integration for investigation",
    category: "chat",
    logo: "https://img.icons8.com/color/96/microsoft-teams.png",
    features: ["Bot mentions", "Adaptive cards", "Channel alerts"],
    setupTime: "10 min",
  },
  {
    id: "discord",
    name: "Discord",
    description: "Discord bot for team communication",
    category: "chat",
    logo: "https://assets-global.website-files.com/6257adef93867e50d84d30e2/636e0a6a49cf127bf92de1e2_icon_clyde_blurple_RGB.png",
    features: ["Bot commands", "Channel notifications"],
    setupTime: "5 min",
  },

  // Knowledge
  {
    id: "confluence",
    name: "Confluence",
    description: "Runbooks and documentation from Confluence",
    category: "knowledge",
    logo: "https://wac-cdn.atlassian.com/dam/jcr:eaa12b67-a29d-467c-b8fa-66f6c915a97f/confluence-icon-gradient-blue.svg",
    features: ["Runbook import", "Documentation search", "Knowledge sync"],
    setupTime: "10 min",
  },
  {
    id: "notion",
    name: "Notion",
    description: "Team knowledge and documentation",
    category: "knowledge",
    logo: "https://upload.wikimedia.org/wikipedia/commons/4/45/Notion_app_logo.png",
    features: ["Page import", "Database sync", "Knowledge base"],
    setupTime: "10 min",
  },
  {
    id: "sentry",
    name: "Sentry",
    description: "Error tracking and performance monitoring",
    category: "observability",
    logo: "https://sentry.io/_assets2/static/sentry-logo-black.svg",
    features: ["Error tracking", "Issue details", "Latest events", "Breadcrumbs"],
    setupTime: "5 min",
  },
];

const CATEGORIES = [
  { id: "all", name: "All Integrations", icon: "server" },
  { id: "observability", name: "Observability", icon: "chart" },
  { id: "alerting", name: "Alerting", icon: "bell" },
  { id: "code", name: "Code", icon: "code" },
  { id: "infrastructure", name: "Infrastructure", icon: "server" },
  { id: "chat", name: "Chat", icon: "message-square" },
  { id: "knowledge", name: "Knowledge", icon: "book" },
];

// Coming soon integrations (not yet available)
const COMING_SOON = ["gcp", "bitbucket", "msteams", "discord", "terraform"];

function IntegrationCard({ integration, onConnect, onDisconnect }: {
  integration: Integration;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const statusConfig = {
    connected: { label: "Connected", color: "bg-green-500/20 text-green-400 border-green-500/30" },
    available: { label: "Available", color: "bg-blue-500/20 text-blue-400 border-blue-500/30" },
    coming_soon: { label: "Coming Soon", color: "bg-gray-500/20 text-gray-400 border-gray-500/30" },
  };

  const status = statusConfig[integration.status];

  return (
    <div
      className="rounded-xl border p-5 hover:border-blue-500/50 transition-all group"
      style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
    >
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-xl bg-white/10 flex items-center justify-center overflow-hidden">
          <img
            src={integration.logo}
            alt={integration.name}
            className="w-8 h-8 object-contain"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold truncate">{integration.name}</h3>
            <span className={`text-xs px-2 py-0.5 rounded-full border ${status.color}`}>
              {status.label}
            </span>
          </div>
          <p className="text-sm text-gray-400 mb-3">{integration.description}</p>
          <div className="flex flex-wrap gap-1 mb-3">
            {integration.features.slice(0, 3).map((feature) => (
              <span
                key={feature}
                className="text-xs px-2 py-0.5 rounded"
                style={{ backgroundColor: "var(--panel)" }}
              >
                {feature}
              </span>
            ))}
            {integration.features.length > 3 && (
              <span className="text-xs text-gray-500">+{integration.features.length - 3} more</span>
            )}
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between mt-4 pt-4 border-t" style={{ borderColor: "var(--border)" }}>
        <span className="text-xs text-gray-500">Setup: {integration.setupTime}</span>
        {integration.status === "connected" ? (
          <div className="flex gap-2">
            <button
              onClick={onDisconnect}
              className="text-sm px-3 py-1.5 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
            >
              Disconnect
            </button>
            <button className="text-sm px-3 py-1.5 rounded-lg bg-green-500/20 text-green-400">
              Manage
            </button>
          </div>
        ) : integration.status === "available" ? (
          <button
            onClick={onConnect}
            className="text-sm px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
          >
            Connect
          </button>
        ) : (
          <button className="text-sm px-3 py-1.5 rounded-lg bg-gray-600/50 text-gray-400 cursor-not-allowed">
            Coming Soon
          </button>
        )}
      </div>
    </div>
  );
}

function ConnectModal({ integration, onClose, onSave }: {
  integration: Integration;
  onClose: () => void;
  onSave: (name: string, value: string) => Promise<void>;
}) {
  const [step, setStep] = useState(1);
  const [apiKey, setApiKey] = useState("");
  const [instanceUrl, setInstanceUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConnect = async () => {
    if (!apiKey.trim()) {
      setError("API key is required");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      await onSave(integration.id, apiKey);
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect integration");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div
        className="w-full max-w-lg rounded-2xl p-6"
        style={{ backgroundColor: "var(--panel)", border: "1px solid var(--border)" }}
      >
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-white/10 flex items-center justify-center overflow-hidden">
              <img src={integration.logo} alt={integration.name} className="w-6 h-6 object-contain" />
            </div>
            <div>
              <h3 className="font-semibold">Connect {integration.name}</h3>
              <p className="text-sm text-gray-400">Step {step} of 3</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg">
            <Icon name="x" size={20} />
          </button>
        </div>

        {step === 1 && (
          <div>
            <h4 className="font-medium mb-4">1. Authentication</h4>
            <div className="space-y-4">
              <div>
                <label className="block text-sm mb-2">API Token / Key</label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Enter your API token"
                  className="w-full px-4 py-2 rounded-lg border"
                  style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                />
              </div>
              <div>
                <label className="block text-sm mb-2">Instance URL (optional)</label>
                <input
                  type="url"
                  value={instanceUrl}
                  onChange={(e) => setInstanceUrl(e.target.value)}
                  placeholder="https://your-instance.example.com"
                  className="w-full px-4 py-2 rounded-lg border"
                  style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                />
              </div>
              {error && (
                <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg p-3">
                  {error}
                </div>
              )}
            </div>
          </div>
        )}

        {step === 2 && (
          <div>
            <h4 className="font-medium mb-4">2. Configure Access</h4>
            <div className="space-y-3">
              {integration.features.map((feature) => (
                <label key={feature} className="flex items-center gap-3 p-3 rounded-lg" style={{ backgroundColor: "var(--shell)" }}>
                  <input type="checkbox" defaultChecked className="rounded" />
                  <span>{feature}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="text-center py-8">
            <div className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center mx-auto mb-4">
              <Icon name="check" size={32} className="text-green-500" />
            </div>
            <h4 className="font-semibold text-lg mb-2">Connection Successful!</h4>
            <p className="text-gray-400">
              {integration.name} is now connected to OmniPotent.
              AI can now query your {integration.category} data.
            </p>
          </div>
        )}

        <div className="flex items-center justify-between mt-6 pt-6 border-t" style={{ borderColor: "var(--border)" }}>
          {step > 1 && step < 3 ? (
            <button
              onClick={() => setStep(step - 1)}
              className="px-4 py-2 rounded-lg hover:bg-white/10"
            >
              Back
            </button>
          ) : (
            <div />
          )}
          {step === 1 && (
            <button
              onClick={() => setStep(2)}
              disabled={!apiKey.trim()}
              className="px-6 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Continue
            </button>
          )}
          {step === 2 && (
            <button
              onClick={handleConnect}
              disabled={saving}
              className="px-6 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? "Connecting..." : "Connect"}
            </button>
          )}
          {step === 3 && (
            <button
              onClick={onClose}
              className="px-6 py-2 rounded-lg bg-green-600 text-white hover:bg-green-700"
            >
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function IntegrationMarketplace({ onBack }: { onBack: () => void }) {
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [connectingIntegration, setConnectingIntegration] = useState<Integration | null>(null);
  const [connectedIntegrations, setConnectedIntegrations] = useState<Set<string>>(new Set());
  const [integrationStatus, setIntegrationStatus] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);

  // Fetch connected integrations from API
  const fetchIntegrations = useCallback(async () => {
    try {
      // Fetch from /admin/integrations (list of connected integration names)
      const adminRes = await fetch(`${API_BASE}/admin/integrations`, {
        credentials: "include",
      });
      if (adminRes.ok) {
        const data = await adminRes.json();
        setConnectedIntegrations(new Set(data.integrations || []));
      }

      // Fetch from /integrations/status (individual status per integration)
      const statusRes = await fetch(`${API_BASE}/integrations/status`, {
        credentials: "include",
      });
      if (statusRes.ok) {
        const status = await statusRes.json();
        setIntegrationStatus(status);
      }
    } catch (err) {
      console.error("Failed to fetch integrations:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchIntegrations();
  }, [fetchIntegrations]);

  // Build integrations list with real status
  const integrations: Integration[] = INTEGRATION_CATALOG.map((cat) => ({
    ...cat,
    status: connectedIntegrations.has(cat.id) || integrationStatus[cat.id]
      ? "connected"
      : COMING_SOON.includes(cat.id)
      ? "coming_soon"
      : "available",
  }));

  const filteredIntegrations = integrations.filter((integration) => {
    const matchesCategory = category === "all" || integration.category === category;
    const matchesSearch = integration.name.toLowerCase().includes(search.toLowerCase()) ||
      integration.description.toLowerCase().includes(search.toLowerCase());
    return matchesCategory && matchesSearch;
  });

  const connectedCount = integrations.filter(i => i.status === "connected").length;

  // Save integration to API
  const handleSaveIntegration = async (name: string, value: string) => {
    const res = await fetch(`${API_BASE}/admin/integrations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name, value }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to save integration");
    }

    // Refresh list
    await fetchIntegrations();
  };

  // Disconnect integration
  const handleDisconnect = async (integrationId: string) => {
    if (!confirm(`Are you sure you want to disconnect ${integrationId}?`)) return;

    try {
      const res = await fetch(`${API_BASE}/admin/integrations/${integrationId}`, {
        method: "DELETE",
        credentials: "include",
      });

      if (res.ok) {
        await fetchIntegrations();
      }
    } catch (err) {
      console.error("Failed to disconnect:", err);
    }
  };

  return (
    <div className="min-h-screen" style={{ backgroundColor: "var(--bg)" }}>
      <header className="border-b" style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="text-gray-400 hover:text-white">
                <Icon name="arrow-left" size={20} />
              </button>
              <div>
                <h1 className="text-xl font-semibold">Integrations</h1>
                <p className="text-sm text-gray-400">
                  {loading ? "Loading..." : `${connectedCount} connected`}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={fetchIntegrations}
                className="p-2 hover:bg-white/10 rounded-lg"
                title="Refresh"
              >
                <Icon name="refresh-cw" size={18} />
              </button>
              <div className="relative">
                <Icon name="search" size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search integrations..."
                  className="pl-10 pr-4 py-2 rounded-lg border w-64"
                  style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                />
              </div>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Categories */}
        <div className="flex items-center gap-2 mb-6 overflow-x-auto pb-2">
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setCategory(cat.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg whitespace-nowrap transition-colors ${
                category === cat.id
                  ? "bg-blue-600 text-white"
                  : "hover:bg-white/10"
              }`}
              style={category !== cat.id ? { backgroundColor: "var(--shell)" } : {}}
            >
              <Icon name={cat.icon as "server" | "chart" | "bell" | "code" | "book"} size={16} />
              {cat.name}
            </button>
          ))}
        </div>

        {/* Loading State */}
        {loading && (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full" />
          </div>
        )}

        {/* Integration Grid */}
        {!loading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredIntegrations.map((integration) => (
              <IntegrationCard
                key={integration.id}
                integration={integration}
                onConnect={() => setConnectingIntegration(integration)}
                onDisconnect={() => handleDisconnect(integration.id)}
              />
            ))}
          </div>
        )}

        {!loading && filteredIntegrations.length === 0 && (
          <div className="text-center py-12">
            <Icon name="search" size={48} className="mx-auto text-gray-600 mb-4" />
            <p className="text-gray-400">No integrations found matching your search</p>
          </div>
        )}
      </div>

      {/* Connect Modal */}
      {connectingIntegration && (
        <ConnectModal
          integration={connectingIntegration}
          onClose={() => setConnectingIntegration(null)}
          onSave={handleSaveIntegration}
        />
      )}
    </div>
  );
}
