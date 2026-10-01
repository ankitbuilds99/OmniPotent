/**
 * Integration Marketplace.
 *
 * Every claim on this page comes from an endpoint: stored secret names from
 * GET /admin/integrations, working/not-working from GET /integrations/status,
 * and the capability chips from GET /config `servers[].tools`. What a build can
 * and cannot connect comes from lib/integrationSpecs.
 */

import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";

import { apiFetch } from "../lib/apiClient";
import { specFor } from "../lib/integrationSpecs";
import type { McpServer } from "../types";

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
  /**
   * Tool names the connector's MCP server exposes, straight from GET /config.
   * Empty when there is no server behind the entry — which is the honest answer.
   */
  tools: string[];
  /**
   * The secret names this org has stored, per GET /admin/integrations.
   * Distinct from `status`: a connector can be "live" purely from the
   * deployment's own environment with nothing stored here to manage.
   */
  storedNames: string[];
}

/*
 * Presentation metadata only: logo, blurb, category. Two fields used to live
 * here and neither was ever true of anything.
 *
 * `setupTime` — "5 min", "10 min", "15 min" — was pure invention. No endpoint
 * reports it, nothing measures it, and it rendered as "Setup: 15 min" on cards
 * that in the same breath said "Not available", promising a quarter hour of
 * work for something the console cannot do at all.
 *
 * `features` was worse, because it read as a capability contract and
 * contradicted the actual tool lists in MCP_CATALOG (app/api/main.py:80).
 * Datadog advertised "Dashboards" and "Monitors" — there are no such tools —
 * and "APM traces", which belongs to a different server entirely. Sentry
 * advertised "Breadcrumbs", Kubernetes "Resource usage", GitHub "Code search":
 * none exist. Meanwhile the eleven entries with no connector at all listed four
 * confident bullet points each.
 *
 * Both are gone. Capabilities now come from GET /config `servers[].tools`,
 * so a card can only claim what the agent can actually call.
 */
const INTEGRATION_CATALOG: Omit<Integration, "status" | "reason" | "tools" | "storedNames">[] = [
  // Observability
  {
    id: "prometheus",
    name: "Prometheus",
    description: "Query metrics and alerts from your Prometheus server",
    category: "observability",
    logo: "https://raw.githubusercontent.com/cncf/artwork/master/projects/prometheus/icon/color/prometheus-icon-color.svg",
  },
  {
    id: "datadog",
    name: "Datadog",
    description: "Full-stack observability with APM, logs, and metrics",
    category: "observability",
    logo: "https://imgix.datadoghq.com/img/dd_logo_n_70x75.png",
  },
  {
    id: "grafana",
    name: "Grafana",
    description: "Query dashboards and visualizations",
    category: "observability",
    logo: "https://raw.githubusercontent.com/grafana/grafana/main/public/img/grafana_icon.svg",
  },
  {
    id: "cloudwatch",
    name: "AWS CloudWatch",
    description: "AWS native monitoring and observability",
    category: "observability",
    logo: "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/ManagementGovernance/CloudWatch.png",
  },
  {
    id: "newrelic",
    name: "New Relic",
    description: "Full-stack observability platform",
    category: "observability",
    logo: "https://cdn.simpleicons.org/newrelic",
  },

  // Alerting
  {
    id: "pagerduty",
    name: "PagerDuty",
    description: "Incident management and on-call scheduling",
    category: "alerting",
    logo: "https://www.pagerduty.com/wp-content/uploads/2022/09/pd-logo-green.svg",
  },
  {
    id: "opsgenie",
    name: "OpsGenie",
    description: "Alert management and on-call scheduling",
    category: "alerting",
    logo: "https://cdn.simpleicons.org/opsgenie",
  },
  {
    id: "alertmanager",
    name: "Alertmanager",
    description: "Prometheus Alertmanager webhooks",
    category: "alerting",
    logo: "https://raw.githubusercontent.com/cncf/artwork/master/projects/prometheus/icon/color/prometheus-icon-color.svg",
  },

  // Code
  {
    id: "github",
    name: "GitHub",
    description: "Repository analysis and deployment correlation",
    category: "code",
    logo: "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
  },
  {
    id: "gitlab",
    name: "GitLab",
    description: "GitLab repository and CI/CD integration",
    category: "code",
    logo: "https://cdn.simpleicons.org/gitlab",
  },
  {
    id: "bitbucket",
    name: "Bitbucket",
    description: "Atlassian Bitbucket repository integration",
    category: "code",
    logo: "https://cdn.simpleicons.org/bitbucket",
  },

  // Infrastructure
  {
    id: "kubernetes",
    name: "Kubernetes",
    description: "Cluster state, pods, deployments, and events",
    category: "infrastructure",
    logo: "https://raw.githubusercontent.com/kubernetes/kubernetes/master/logo/logo.svg",
  },
  {
    id: "aws",
    name: "AWS",
    description: "Amazon Web Services infrastructure",
    category: "infrastructure",
    logo: "https://a0.awsstatic.com/libra-css/images/logos/aws_smile-header-desktop-en-white_59x35.png",
  },
  {
    id: "gcp",
    name: "Google Cloud",
    description: "Google Cloud Platform services",
    category: "infrastructure",
    logo: "https://cdn.simpleicons.org/googlecloud",
  },
  {
    id: "terraform",
    name: "Terraform",
    description: "Infrastructure as Code state and changes",
    category: "infrastructure",
    logo: "https://cdn.simpleicons.org/terraform",
  },
  {
    id: "argocd",
    name: "ArgoCD",
    description: "GitOps continuous delivery for Kubernetes",
    category: "infrastructure",
    logo: "https://raw.githubusercontent.com/argoproj/argoproj/master/docs/assets/argo.png",
  },

  // Chat
  {
    id: "slack",
    name: "Slack",
    description: "Chat integration with @mention support",
    category: "chat",
    logo: "https://a.slack-edge.com/80588/marketing/img/icons/icon_slack_hash_colored.png",
  },
  {
    id: "msteams",
    name: "Microsoft Teams",
    description: "Teams integration for investigation",
    category: "chat",
    logo: "https://img.icons8.com/color/96/microsoft-teams.png",
  },
  {
    id: "discord",
    name: "Discord",
    description: "Discord bot for team communication",
    category: "chat",
    logo: "https://assets-global.website-files.com/6257adef93867e50d84d30e2/636e0a6a49cf127bf92de1e2_icon_clyde_blurple_RGB.png",
  },

  // Knowledge
  {
    id: "confluence",
    name: "Confluence",
    description: "Runbooks and documentation from Confluence",
    category: "knowledge",
    logo: "https://cdn.simpleicons.org/confluence",
  },
  {
    id: "notion",
    name: "Notion",
    description: "Team knowledge and documentation",
    category: "knowledge",
    logo: "https://upload.wikimedia.org/wikipedia/commons/4/45/Notion_app_logo.png",
  },
  {
    id: "sentry",
    name: "Sentry",
    description: "Error tracking and performance monitoring",
    category: "observability",
    logo: "https://cdn.simpleicons.org/sentry",
  },
  // The gap in the other direction. `traces` is a real MCP server with seven
  // tools, TRACES_API_URL is one of the secrets `_INTEGRATION_KEYS` forwards,
  // and GET /integrations/status has always reported on it — but the catalog
  // listed eleven connectors that do not exist and omitted this one, which does.
  {
    id: "traces",
    name: "Distributed tracing",
    description: "Jaeger-compatible trace search, critical paths, and blast radius",
    category: "observability",
    logo: "https://raw.githubusercontent.com/cncf/artwork/master/projects/jaeger/icon/color/jaeger-icon-color.svg",
  },
  // And the same omission again, which the `traces` fix walked straight past.
  // `deploys` is a real MCP server (list_deploys, get_deploy, deploys_in_window),
  // it is in MCP_CATALOG, and GET /integrations/status reports on it — but no
  // catalog row meant the connector that answers "what shipped, and when" could
  // not be seen or diagnosed from the page whose whole job is showing what is
  // connected. Deployment-configured, so it renders as such; see integrationSpecs.
  //
  // No logo, deliberately. Every other row wears the mark of the product behind
  // it, and `traces` can wear Jaeger's because TRACES_API_URL *is* a Jaeger
  // query API. This connector is vendor-neutral — DEPLOYS_API_URL points at
  // whatever deploy API the deployment runs — so borrowing Spinnaker's or
  // Octopus's mark to fill the tile would advertise an integration that does not
  // exist. The blank falls through to the initial, which claims nothing.
  {
    id: "deploys",
    name: "Deploy tracker",
    description: "Deploy timeline for correlating an error onset with the change that caused it",
    category: "infrastructure",
    logo: "",
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

/**
 * The connector's mark, or its initial when that cannot be fetched.
 *
 * Ten of the twenty-three logo URLs in the catalog above were dead — CloudWatch
 * 403, and 404 for New Relic, Opsgenie, GitLab, Bitbucket, Google Cloud,
 * Terraform, Confluence and Sentry, several of them vendor CDN paths that had
 * simply been reorganised since. Two of those (Sentry, Terraform) are live
 * connectors. They are repointed now, but a hardcoded third-party URL is a thing
 * that rots on someone else's schedule, so the failure needs a floor.
 *
 * The card used to set `display: none` on error, leaving an empty grey tile that
 * looks like a rendering bug; the modal had no error handling at all, so it
 * showed the browser's broken-image glyph next to "Connect New Relic". Both now
 * fall back to the initial, which reads as deliberate.
 */
function IntegrationLogo({ src, name, size }: { src: string; name: string; size: "sm" | "md" }) {
  const [broken, setBroken] = useState(false);
  const box = size === "md" ? "w-12 h-12 rounded-xl" : "w-10 h-10 rounded-lg";
  const img = size === "md" ? "w-8 h-8" : "w-6 h-6";

  return (
    <div className={`${box} bg-white/10 flex items-center justify-center overflow-hidden shrink-0`}>
      {broken || !src ? (
        <span aria-hidden="true" className="font-semibold text-gray-300">
          {name.charAt(0).toUpperCase()}
        </span>
      ) : (
        <img
          src={src}
          alt={name}
          className={`${img} object-contain`}
          onError={() => setBroken(true)}
        />
      )}
    </div>
  );
}

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
  // Whether this org has a secret of its own to manage.
  // Not the same question as "is it connected". Kubernetes reports connected
  // whenever the deployment mounted a kubeconfig, and there is nothing stored
  // here to disconnect or replace; offering those buttons would produce
  // "Nothing stored for kubernetes to disconnect" on every click.
  const manageable = integration.storedNames.length > 0;
  // Replaces the invented "Setup: 10 min". The one thing worth knowing before
  // clicking Connect is which names the credentials land under, since that is
  // what a deployment's own env has to agree with.
  //
  // For a connector the org cannot configure, that is the deployment's own
  // variables. This slot used to be blank for every one of them — Kubernetes,
  // ArgoCD, Terraform, deploys — so a working card said "Configured by the
  // deployment" and left the reader to guess by what.
  const spec = specFor(integration.id);
  const storedAs = spec.fields.length > 0 ? spec.fields.map((f) => f.env) : spec.envNames;

  return (
    <div
      className="rounded-xl border p-5 hover:border-blue-500/50 transition-all group"
      style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
    >
      <div className="flex items-start gap-4">
        <IntegrationLogo src={integration.logo} name={integration.name} size="md" />
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
          {integration.tools.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3">
              {integration.tools.slice(0, 3).map((tool) => (
                <span
                  key={tool}
                  className="text-xs px-2 py-0.5 rounded font-mono"
                  style={{ backgroundColor: "var(--panel)" }}
                >
                  {tool}
                </span>
              ))}
              {integration.tools.length > 3 && (
                <span className="text-xs text-gray-500" title={integration.tools.join(", ")}>
                  +{integration.tools.length - 3} more
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="flex items-center justify-between mt-4 pt-4 border-t" style={{ borderColor: "var(--border)" }}>
        <span className="text-xs text-gray-500 font-mono truncate pr-2" title={storedAs.join(", ")}>
          {storedAs.join(", ")}
        </span>
        {manageable ? (
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
        ) : integration.status === "live" ? (
          // Working, but from the process environment. There is no per-org
          // secret behind it, so there is no button that would do anything.
          <span className="text-xs text-gray-500">Configured by the deployment</span>
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
            <IntegrationLogo src={integration.logo} name={integration.name} size="sm" />
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
  // connector name -> the tools its MCP server exposes, from GET /config.
  const [serverTools, setServerTools] = useState<Record<string, string[]>>({});
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
        const detail = await errorDetail(adminRes, "Could not list integrations");
        // Single-tenant deployments intentionally reject the tenant-admin
        // endpoint. Deployment credentials are still reported by
        // /integrations/status, so this is not an unavailable-integrations
        // condition and should not appear as a red page-level error.
        if (detail.includes("multi-tenant mode + a tenant API key")) {
          setLoadError(null);
        } else {
          setLoadError(detail);
        }
      }

      // Fetch from /integrations/status (individual status per integration)
      const statusRes = await apiFetch(`/integrations/status`, {
        credentials: "include",
      });
      if (statusRes.ok) {
        const status = await statusRes.json();
        setIntegrationStatus(status);
      }

      // The tool lists behind the capability chips. A failure here is not worth
      // an error banner — the cards simply show no capabilities, which beats
      // showing four made-up ones.
      const configRes = await apiFetch(`/config`, { credentials: "include" });
      if (configRes.ok) {
        const config = (await configRes.json()) as { servers?: McpServer[] };
        const byName: Record<string, string[]> = {};
        for (const server of config.servers ?? []) {
          if (server?.name) byName[server.name] = server.tools ?? [];
        }
        setServerTools(byName);
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

  // Build the list from what the three endpoints actually report.
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

    return {
      ...cat,
      status,
      reason: status === "unavailable" ? spec.unsupported : "",
      // statusKey doubles as the MCP server name. An entry with no connector
      // finds nothing here and shows no capability chips at all.
      tools: (spec.statusKey && serverTools[spec.statusKey]) || [],
      storedNames: spec.fields.map((f) => f.env).filter((env) => connectedIntegrations.has(env)),
    };
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
