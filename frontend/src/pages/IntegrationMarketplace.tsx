/**
 * Integration Marketplace - Real API integration
 * Fetches connected integrations from /admin/integrations and /integrations/status
 */

import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";

import { apiFetch } from "../lib/apiClient";
import { specFor } from "../lib/integrationSpecs";

/** Surface the backend's own explanation for a failed call rather than hiding it. */
async function errorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  } catch {
    // Non-JSON body.
  }
  return `${fallback} (HTTP ${res.status})`;
}

/**
 * `live`   — GET /integrations/status says the connector has working credentials.
 * `saved`  — every required secret is stored, but nothing has confirmed it works.
 * `available`   — this console can configure it and has not.
 * `unavailable` — no connector, or one the deployment configures, not the org.
 */
type IntegrationStatus = "live" | "saved" | "available" | "unavailable";

interface Integration {
  id: string;
  name: string;
  description: string;
  category: "observability" | "alerting" | "code" | "infrastructure" | "chat" | "knowledge";
  logo: string;
  status: IntegrationStatus;
  /** Why it is unavailable; "" otherwise. */
  reason: string;
  features: string[];
  setupTime: string;
}

// Static catalog of available integrations (metadata only)
const INTEGRATION_CATALOG: Omit<Integration, "status" | "reason">[] = [
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

// There used to be a hardcoded COMING_SOON list — ["gcp", "bitbucket",
// "msteams", "discord", "terraform"] — which was wrong in both directions:
// Terraform has a working connector in MCP_CATALOG, while Prometheus, Grafana,
// CloudWatch, New Relic, Opsgenie, Alertmanager, GitLab, AWS, Confluence and
// Notion have none and were all offered with a live "Connect" button. What a
// build can and cannot connect now comes from integrationSpecs.

function IntegrationCard({ integration, onConnect, onDisconnect }: {
  integration: Integration;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const statusConfig: Record<IntegrationStatus, { label: string; color: string }> = {
    live: { label: "Connected", color: "bg-green-500/20 text-green-400 border-green-500/30" },
    // Storing a secret is not evidence it works, and this is the only endpoint
    // in the flow that could have found out — it does not try.
    saved: { label: "Credentials saved", color: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30" },
    available: { label: "Available", color: "bg-blue-500/20 text-blue-400 border-blue-500/30" },
    unavailable: { label: "Not available", color: "bg-gray-500/20 text-gray-400 border-gray-500/30" },
  };

  const status = statusConfig[integration.status];
  const configured = integration.status === "live" || integration.status === "saved";

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
          {integration.reason && (
            <p className="text-xs mb-3" style={{ color: "var(--text-2)" }}>{integration.reason}</p>
          )}
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
        {configured ? (
          <div className="flex gap-2">
            <button
              onClick={onDisconnect}
              className="text-sm px-3 py-1.5 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
            >
              Disconnect
            </button>
            {/* "Manage" was a button with no handler. Re-entering the
                credential form is the only management this build offers. */}
            <button
              onClick={onConnect}
              className="text-sm px-3 py-1.5 rounded-lg bg-green-500/20 text-green-400 hover:bg-green-500/30 transition-colors"
            >
              Replace credentials
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
          <button
            disabled
            title={integration.reason}
            className="text-sm px-3 py-1.5 rounded-lg bg-gray-600/50 text-gray-400 cursor-not-allowed"
          >
            Not available
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Collect the credentials a connector actually reads, and say afterwards only
 * what happened.
 *
 * The previous wizard had three steps. Step 1 asked for one "API Token / Key"
 * plus an "Instance URL (optional)" that was never referenced again — a
 * self-hosted Grafana URL or a EU-region Datadog site typed there simply
 * vanished. Step 2 listed the integration's marketing features as uncontrolled
 * `defaultChecked` checkboxes with no state and no onChange, so unticking a
 * data scope you did not want shared did nothing and could not do anything:
 * SetIntegrationRequest is `{name, value}` and has no room for scopes. Step 3
 * then announced "Connection Successful! … AI can now query your data" for an
 * endpoint that stores the string and returns `{"status": "saved"}` without
 * ever contacting the vendor — an invalid token produced the same green screen.
 *
 * Worse, the one value that was transmitted went under `integration.id`, and
 * `_INTEGRATION_KEYS` (app/tenancy/auth.py) only forwards env-style names to
 * the MCP subprocesses. A key saved as "datadog" is stored, listed back by GET
 * /admin/integrations, rendered as "Connected", and read by nothing.
 *
 * So: the fields come from integrationSpecs, each under the name its connector
 * reads; GitHub goes through POST /github/connect, which verifies against
 * api.github.com before saving; and the closing screen distinguishes "verified"
 * from "saved, not tested".
 */
function ConnectModal({ integration, onClose, onSaved }: {
  integration: Integration;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const spec = specFor(integration.id);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<"verified" | "saved" | null>(null);

  const setValue = (env: string, value: string) =>
    setValues((prev) => ({ ...prev, [env]: value }));

  const missing = spec.fields.filter((f) => f.required && !(values[f.env] ?? "").trim());

  const handleConnect = async () => {
    if (missing.length > 0) {
      setError(`${missing.map((f) => f.label).join(" and ")} required.`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (integration.id === "github") {
        // The only endpoint in this build that proves a credential works.
        const res = await apiFetch("/github/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            token: (values.GITHUB_TOKEN ?? "").trim(),
            repo: (values.GITHUB_REPO ?? "").trim(),
          }),
        });
        if (!res.ok) throw new Error(await errorDetail(res, "GitHub rejected the connection"));
        setDone("verified");
      } else {
        // Written one secret at a time; the request body holds a single pair.
        for (const field of spec.fields) {
          const value = (values[field.env] ?? "").trim();
          if (!value) continue;
          const res = await apiFetch(`/admin/integrations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({ name: field.env, value }),
          });
          if (!res.ok) throw new Error(await errorDetail(res, `Could not save ${field.env}`));
        }
        setDone("saved");
      }
      await onSaved();
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
              <p className="text-sm text-gray-400">
                {spec.unsupported ? "Not configurable here" : "Credentials"}
              </p>
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-2 hover:bg-white/10 rounded-lg">
            <Icon name="x" size={20} />
          </button>
        </div>

        {spec.unsupported ? (
          <p className="text-sm text-gray-400">{spec.unsupported}</p>
        ) : done ? (
          <div className="text-center py-8">
            <div className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center mx-auto mb-4">
              <Icon name="check" size={32} className="text-green-500" />
            </div>
            <h4 className="font-semibold text-lg mb-2">
              {done === "verified" ? "Connected" : "Credentials saved"}
            </h4>
            <p className="text-gray-400">
              {done === "verified"
                ? `${integration.name} answered with the repository, so the token works.`
                : `Saved as ${spec.fields.map((f) => f.env).join(", ")}. This endpoint stores ` +
                  `credentials without contacting ${integration.name}, so the first ` +
                  `investigation that uses them is what will prove them right or wrong.`}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {spec.fields.map((field) => (
              <div key={field.env}>
                <label className="block text-sm mb-2" htmlFor={`field-${field.env}`}>
                  {field.label}
                  {!field.required && <span className="text-gray-500"> (optional)</span>}
                </label>
                <input
                  id={`field-${field.env}`}
                  type={field.secret ? "password" : "text"}
                  value={values[field.env] ?? ""}
                  onChange={(e) => setValue(field.env, e.target.value)}
                  placeholder={field.placeholder}
                  className="w-full px-4 py-2 rounded-lg border"
                  style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                />
                <p className="text-xs text-gray-500 mt-1">
                  {field.help ? `${field.help} ` : ""}
                  Stored as <span className="font-mono">{field.env}</span>.
                </p>
              </div>
            ))}
            {error && (
              <div
                role="alert"
                className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg p-3"
              >
                {error}
              </div>
            )}
          </div>
        )}

        <div className="flex items-center justify-end mt-6 pt-6 border-t" style={{ borderColor: "var(--border)" }}>
          {spec.unsupported || done ? (
            <button
              onClick={onClose}
              className="px-6 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700"
            >
              Done
            </button>
          ) : (
            <button
              onClick={handleConnect}
              disabled={saving}
              className="px-6 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {saving
                ? integration.id === "github"
                  ? "Checking…"
                  : "Saving…"
                : `Connect ${integration.name}`}
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
  // The backend refuses /admin/integrations outright on a single-tenant
  // deployment ("Admin endpoints require multi-tenant mode + a tenant API key.")
  // and 401s when the caller is unauthenticated. Both were swallowed, leaving a
  // page that looked like "you have no integrations" — say what actually happened.
  const [loadError, setLoadError] = useState<string | null>(null);

  // Fetch connected integrations from API
  const fetchIntegrations = useCallback(async () => {
    try {
      // Fetch from /admin/integrations (list of connected integration names)
      const adminRes = await apiFetch(`/admin/integrations`, {
        credentials: "include",
      });
      if (adminRes.ok) {
        const data = await adminRes.json();
        setConnectedIntegrations(new Set(data.integrations || []));
        setLoadError(null);
      } else {
        setConnectedIntegrations(new Set());
        setLoadError(await errorDetail(adminRes, "Could not list integrations"));
      }

      // Fetch from /integrations/status (individual status per integration)
      const statusRes = await apiFetch(`/integrations/status`, {
        credentials: "include",
      });
      if (statusRes.ok) {
        const status = await statusRes.json();
        setIntegrationStatus(status);
      }
    } catch (err) {
      console.error("Failed to fetch integrations:", err);
      setLoadError(err instanceof Error ? err.message : "Could not list integrations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchIntegrations();
  }, [fetchIntegrations]);

  // Build the list from what the two endpoints actually report.
  //
  // The old rule was `connectedIntegrations.has(cat.id) || integrationStatus[cat.id]`.
  // Both halves were wrong. /admin/integrations returns the *names* of stored
  // secrets, which are env-style (DD_API_KEY, …), so a catalog id was never
  // going to be in that set unless this page had put it there itself — which it
  // had, under a name no connector reads. And /integrations/status is keyed by
  // connector, not by catalog id: "grafana", "newrelic" and the rest are absent
  // from it entirely, so `integrationStatus[cat.id]` was undefined for two
  // thirds of the catalog.
  const integrations: Integration[] = INTEGRATION_CATALOG.map((cat) => {
    const spec = specFor(cat.id);
    const live = Boolean(spec.statusKey && integrationStatus[spec.statusKey]);
    const stored =
      spec.fields.length > 0 &&
      spec.fields.filter((f) => f.required).every((f) => connectedIntegrations.has(f.env));

    let status: IntegrationStatus;
    if (live) status = "live";
    else if (stored) status = "saved";
    else if (spec.unsupported) status = "unavailable";
    else status = "available";

    return { ...cat, status, reason: status === "unavailable" ? spec.unsupported : "" };
  });

  const filteredIntegrations = integrations.filter((integration) => {
    const matchesCategory = category === "all" || integration.category === category;
    const matchesSearch = integration.name.toLowerCase().includes(search.toLowerCase()) ||
      integration.description.toLowerCase().includes(search.toLowerCase());
    return matchesCategory && matchesSearch;
  });

  const connectedCount = integrations.filter(i => i.status === "live").length;
  const savedCount = integrations.filter(i => i.status === "saved").length;

  // Disconnect integration
  const handleDisconnect = async (integrationId: string) => {
    // Deleting by catalog id removed nothing, because the secrets are stored
    // under the connector's env names. Every one of them has to go, or a
    // half-configured integration keeps answering as "credentials saved".
    const names = specFor(integrationId).fields
      .map((f) => f.env)
      .filter((env) => connectedIntegrations.has(env));
    if (names.length === 0) {
      setLoadError(`Nothing stored for ${integrationId} to disconnect.`);
      return;
    }
    if (!confirm(`Delete the stored ${names.join(", ")} for ${integrationId}?`)) return;

    try {
      for (const name of names) {
        const res = await apiFetch(`/admin/integrations/${encodeURIComponent(name)}`, {
          method: "DELETE",
          credentials: "include",
        });

        if (!res.ok) {
          // This used to only act on res.ok, so a failed delete left the card
          // showing "Connected" with no explanation.
          setLoadError(await errorDetail(res, `Could not delete ${name}`));
          await fetchIntegrations();
          return;
        }
      }
      await fetchIntegrations();
    } catch (err) {
      console.error("Failed to disconnect:", err);
      setLoadError(
        err instanceof Error ? err.message : `Could not disconnect ${integrationId}`
      );
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
                  {loading
                    ? "Loading..."
                    : savedCount > 0
                      ? `${connectedCount} connected, ${savedCount} awaiting first use`
                      : `${connectedCount} connected`}
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
        {loadError && (
          <div
            role="alert"
            className="mb-6 rounded-lg border px-4 py-3 flex items-start gap-3"
            style={{ borderColor: "var(--bad, #ef4444)", backgroundColor: "var(--shell)" }}
          >
            <Icon name="alert-triangle" size={18} />
            <div className="flex-1">
              <p className="font-medium">Integrations unavailable</p>
              <p className="text-sm text-gray-400">{loadError}</p>
            </div>
            <button
              onClick={() => setLoadError(null)}
              aria-label="Dismiss"
              className="text-gray-400 hover:text-white"
            >
              <Icon name="x-circle" size={16} />
            </button>
          </div>
        )}

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
          onSaved={fetchIntegrations}
        />
      )}
    </div>
  );
}
