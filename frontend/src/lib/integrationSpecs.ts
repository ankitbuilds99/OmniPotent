/**
 * What this build can actually connect, and under which names.
 *
 * The integration catalog in the marketplace is presentation metadata — logos
 * and blurbs. It says nothing about whether a connector exists, and
 * the connect flow used to store whatever was typed under the *catalog id*
 * ("datadog", "grafana", …). Nothing reads those names. The backend forwards a
 * fixed set of env-style secrets to its MCP subprocesses
 * (`_INTEGRATION_KEYS` in app/tenancy/auth.py, plus GITHUB_TOKEN/GITHUB_REPO in
 * `build_tenant_config`), and a key stored under any other name is written,
 * echoed back by GET /admin/integrations, rendered as "Connected" — and never
 * used by anything.
 *
 * This module is the single place that states, for each catalog entry:
 *   - whether a connector exists at all (`unsupported`),
 *   - the exact secret names a connector will read (`fields`),
 *   - which key of GET /integrations/status reports on it (`statusKey`).
 *
 * Keep it in step with MCP_CATALOG (app/api/main.py:80), `_INTEGRATION_KEYS`
 * (app/tenancy/auth.py:25) and GET /integrations/status (app/api/main.py:2896).
 */

export interface SecretField {
  /** The exact name POST /admin/integrations must be given for a connector to read it. */
  env: string;
  label: string;
  placeholder: string;
  /** Render as a password box and never echo the stored value. */
  secret: boolean;
  required: boolean;
  /** Shown under the field when the name alone is not enough. */
  help?: string;
}

export interface IntegrationSpec {
  /**
   * Key in GET /integrations/status, or null when nothing reports on it.
   * Doubles as the MCP server name in GET /config `servers[]` for every
   * connector that has one, which is how the marketplace lists real tools
   * instead of invented capability blurbs.
   */
  statusKey: string | null;
  /** Secrets this build reads. Empty when there is nothing an org can set. */
  fields: SecretField[];
  /**
   * Variables the *deployment's* environment supplies, for connectors an org
   * cannot configure itself. Empty when `fields` covers it.
   *
   * These names only ever existed inside the `unsupported` prose, so a card for
   * a connector that was working — Kubernetes, ArgoCD, Terraform, deploys — said
   * "Configured by the deployment" and stopped there. The operator most likely
   * to be reading it is the one trying to work out which variable to go and set,
   * and the answer was shown only in the state where it no longer mattered.
   */
  envNames: string[];
  /**
   * Why this build cannot connect it from the console; "" when it can.
   * Three distinct cases end up here: no connector exists, the connector is
   * configured by the deployment's environment and not per organisation, or
   * the credential is not something a browser can supply at all.
   */
  unsupported: string;
}

/** No connector exists for these; the catalog lists them as future work. */
function noConnector(name: string): IntegrationSpec {
  return {
    statusKey: null,
    fields: [],
    envNames: [],
    unsupported: `This build has no ${name} connector. Nothing would read a credential saved here.`,
  };
}

/** The connector exists but reads the deployment's environment, not per-org secrets. */
function deploymentOnly(statusKey: string, envNames: string[]): IntegrationSpec {
  return {
    statusKey,
    fields: [],
    envNames,
    unsupported:
      `Configured by the deployment, through ${envNames.join(" and ")}. ` +
      `Saving a value here would not reach it — ask whoever runs this instance.`,
  };
}

export const INTEGRATION_SPECS: Record<string, IntegrationSpec> = {
  datadog: {
    statusKey: "datadog",
    unsupported: "",
    envNames: [],
    fields: [
      { env: "DD_API_KEY", label: "API key", placeholder: "32-character key", secret: true, required: true },
      { env: "DD_APP_KEY", label: "Application key", placeholder: "40-character key", secret: true, required: true },
      {
        env: "DD_SITE",
        label: "Site",
        placeholder: "datadoghq.com",
        secret: false,
        required: false,
        help: "Leave blank for datadoghq.com; set datadoghq.eu or us3.datadoghq.com to match your account.",
      },
    ],
  },

  pagerduty: {
    statusKey: "pagerduty",
    unsupported: "",
    envNames: [],
    fields: [
      { env: "PAGERDUTY_API_TOKEN", label: "REST API token", placeholder: "u+…", secret: true, required: true },
      {
        env: "PAGERDUTY_FROM_EMAIL",
        label: "From address",
        placeholder: "oncall@example.com",
        secret: false,
        required: false,
        help: "A PagerDuty user's email. Required before notes, acknowledgements or resolves can be written back.",
      },
    ],
  },

  sentry: {
    statusKey: "sentry",
    unsupported: "",
    envNames: [],
    fields: [
      { env: "SENTRY_API_TOKEN", label: "Auth token", placeholder: "sntrys_…", secret: true, required: true },
      { env: "SENTRY_ORG", label: "Organization slug", placeholder: "acme", secret: false, required: true },
      { env: "SENTRY_PROJECT", label: "Project slug", placeholder: "checkout", secret: false, required: true },
    ],
  },

  /*
   * Kubernetes has no API key to ask for.
   *
   * The connector's only credential is KUBE_CONFIG_PATH, and it is a *filesystem
   * path read by the server process*, not a secret: the MCP server does
   * `config.load_kube_config(config_file=KUBE_CONFIG_PATH)`
   * (app/mcp/servers/kubernetes/server.py:80, and the same in
   * app/runbooks/executor.py:123 and app/coordinator/handlers/remediation.py:840).
   * Nothing in app/ accepts a file upload — there is no multipart endpoint
   * anywhere — so a browser cannot put a kubeconfig where that path would find
   * it. A path typed here names a file on someone else's machine.
   *
   * Worse than useless, in fact: the server treats a blank path as
   * `OFFLINE = not KUBE_CONFIG_PATH` and serves fixtures. Saving a path that
   * does not resolve on the server flips it into live mode, where every tool
   * call then fails on load_kube_config. So the form could only ever leave the
   * cluster tooling in a worse state than it found it.
   *
   * statusKey stays: a deployment that mounted a kubeconfig and set the
   * variable still reports kubernetes: true, and the card still says Connected.
   */
  kubernetes: {
    statusKey: "kubernetes",
    fields: [],
    envNames: ["KUBE_CONFIG_PATH", "KUBE_NAMESPACE"],
    unsupported:
      "There is no Kubernetes API key to enter. The connector authenticates with a " +
      "kubeconfig file that has to exist on the OmniPotent server (KUBE_CONFIG_PATH), " +
      "and this build has no way to upload one — whoever runs this instance has to " +
      "mount it and set the path.",
  },

  /*
   * Traces is the inverse of the Kubernetes case: a connector this console can
   * genuinely configure, which the catalog never offered. TRACES_API_URL is in
   * `_INTEGRATION_KEYS`, is forwarded to app.mcp.servers.traces.server, and is
   * an ordinary URL a browser can supply.
   */
  traces: {
    statusKey: "traces",
    unsupported: "",
    envNames: [],
    fields: [
      {
        env: "TRACES_API_URL",
        label: "Query API base URL",
        placeholder: "https://jaeger.example.com",
        secret: false,
        required: true,
        help: "A Jaeger-compatible query API, reachable from the OmniPotent server. Leave this unset and the trace tools answer from fixtures.",
      },
    ],
  },

  github: {
    statusKey: "github",
    unsupported: "",
    envNames: [],
    fields: [
      {
        env: "GITHUB_TOKEN",
        label: "Personal access token",
        placeholder: "ghp_…",
        secret: true,
        required: true,
        help: "Needs repo read access; contents and pull-request write if the agent should open PRs.",
      },
      { env: "GITHUB_REPO", label: "Repository", placeholder: "owner/name", secret: false, required: true },
    ],
  },

  // Real connectors whose credentials come from the process environment.
  slack: {
    statusKey: "slack",
    fields: [],
    envNames: ["SLACK_BOT_TOKEN"],
    unsupported:
      "Slack posting is configured by the deployment (SLACK_BOT_TOKEN). For your own " +
      "notification routing, add an incoming webhook on the Notifications page instead.",
  },
  argocd: deploymentOnly("argocd", ["ARGOCD_SERVER_URL", "ARGOCD_AUTH_TOKEN"]),
  terraform: deploymentOnly("terraform", ["TERRAFORM_CLOUD_TOKEN", "TERRAFORM_ORGANIZATION"]),
  /*
   * The same omission `traces` had, one row over. `deploys` is a real MCP server
   * with three tools, it is in MCP_CATALOG, and GET /integrations/status has
   * always reported on it — the marketplace simply never listed it, so the one
   * connector that answers "what shipped, and when" was invisible to the people
   * configuring the product.
   *
   * Deployment-only, like argocd and terraform: DEPLOYS_API_URL and
   * DEPLOYS_API_TOKEN are read through `_isecret`, but neither name is in
   * `_INTEGRATION_KEYS`, so a per-org secret saved under them is never forwarded
   * to the subprocess. Only the deployment's own environment reaches it.
   */
  deploys: deploymentOnly("deploys", ["DEPLOYS_API_URL", "DEPLOYS_API_TOKEN"]),

  // Catalog entries with no connector behind them.
  /*
   * Prometheus is the one entry where "no connector" was the wrong summary.
   *
   * There is genuinely no Prometheus MCP server — the agent's metric tools read
   * Datadog — so `statusKey` stays null and the card shows no capability chips.
   * But the old text went on to say a PROMETHEUS_URL "can be set by the
   * deployment but nothing in the console reads it", and that is false in the
   * expensive direction. PROMETHEUS_URL is exactly what backs
   * GET /api/knowledge-graph/service-metrics (app/knowledge_graph/api_router.py),
   * which is the only thing that measures service health anywhere in this
   * product: leave it unset and the Service Map shows no request rate, error
   * rate or p99 for anything, and the dashboard's Services card reads "health
   * unmeasured" permanently (`readServiceHealth` in pages/Dashboard.tsx).
   *
   * An operator who believed that sentence would never set the one variable that
   * turns both of those on.
   */
  prometheus: {
    statusKey: null,
    fields: [],
    envNames: ["PROMETHEUS_URL"],
    unsupported:
      "The agent has no Prometheus tools — its metric tools read Datadog. The Service " +
      "Map and the dashboard's service-health card do read Prometheus though, through " +
      "PROMETHEUS_URL on the API; without it both report health as unmeasured. That is " +
      "a deployment setting rather than a per-organisation secret, so ask whoever runs " +
      "this instance to set it.",
  },
  grafana: noConnector("Grafana"),
  cloudwatch: noConnector("CloudWatch"),
  newrelic: noConnector("New Relic"),
  opsgenie: noConnector("Opsgenie"),
  alertmanager: {
    statusKey: null,
    fields: [],
    envNames: [],
    unsupported:
      "There is no Alertmanager connector to query. Alertmanager sends alerts *to* " +
      "OmniPotent instead — point its webhook receiver at this deployment's /webhooks endpoint.",
  },
  gitlab: noConnector("GitLab"),
  bitbucket: noConnector("Bitbucket"),
  aws: noConnector("AWS"),
  gcp: noConnector("Google Cloud"),
  msteams: noConnector("Microsoft Teams"),
  discord: noConnector("Discord"),
  confluence: noConnector("Confluence"),
  notion: noConnector("Notion"),
};

/** An unknown catalog id is unsupported by definition — nothing maps it to a connector. */
export function specFor(id: string): IntegrationSpec {
  return (
    INTEGRATION_SPECS[id] ?? {
      statusKey: null,
      fields: [],
      envNames: [],
      unsupported: `This build has no connector for "${id}".`,
    }
  );
}

/** Whether the console can save credentials for this integration itself. */
export function isConnectable(id: string): boolean {
  const spec = specFor(id);
  return spec.unsupported === "" && spec.fields.length > 0;
}
