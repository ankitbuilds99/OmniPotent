/**
 * What this build can actually connect, and under which names.
 *
 * The integration catalog in the marketplace is presentation metadata — logos,
 * blurbs, "setup time". It says nothing about whether a connector exists, and
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
  /** Key in GET /integrations/status, or null when nothing reports on it. */
  statusKey: string | null;
  /** Secrets this build reads. Empty when there is nothing an org can set. */
  fields: SecretField[];
  /**
   * Why this build cannot connect it from the console; "" when it can.
   * Two distinct cases end up here: no connector exists, or the connector is
   * configured by the deployment's environment and not per organisation.
   */
  unsupported: string;
}

/** No connector exists for these; the catalog lists them as future work. */
function noConnector(name: string): IntegrationSpec {
  return {
    statusKey: null,
    fields: [],
    unsupported: `This build has no ${name} connector. Nothing would read a credential saved here.`,
  };
}

/** The connector exists but reads the deployment's environment, not per-org secrets. */
function deploymentOnly(statusKey: string, envNames: string[]): IntegrationSpec {
  return {
    statusKey,
    fields: [],
    unsupported:
      `Configured by the deployment, through ${envNames.join(" and ")}. ` +
      `Saving a value here would not reach it — ask whoever runs this instance.`,
  };
}

export const INTEGRATION_SPECS: Record<string, IntegrationSpec> = {
  datadog: {
    statusKey: "datadog",
    unsupported: "",
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
    fields: [
      { env: "SENTRY_API_TOKEN", label: "Auth token", placeholder: "sntrys_…", secret: true, required: true },
      { env: "SENTRY_ORG", label: "Organization slug", placeholder: "acme", secret: false, required: true },
      { env: "SENTRY_PROJECT", label: "Project slug", placeholder: "checkout", secret: false, required: true },
    ],
  },

  kubernetes: {
    statusKey: "kubernetes",
    unsupported: "",
    fields: [
      {
        env: "KUBE_CONFIG_PATH",
        label: "Kubeconfig path",
        placeholder: "/etc/omnipotent/kubeconfig",
        secret: false,
        required: true,
        help: "A path on the OmniPotent server, not on your machine — the file has to be readable there.",
      },
      { env: "KUBE_NAMESPACE", label: "Default namespace", placeholder: "default", secret: false, required: false },
    ],
  },

  github: {
    statusKey: "github",
    unsupported: "",
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
    unsupported:
      "Slack posting is configured by the deployment (SLACK_BOT_TOKEN). For your own " +
      "notification routing, add an incoming webhook on the Notifications page instead.",
  },
  argocd: deploymentOnly("argocd", ["ARGOCD_SERVER_URL", "ARGOCD_AUTH_TOKEN"]),
  terraform: deploymentOnly("terraform", ["TERRAFORM_CLOUD_TOKEN", "TERRAFORM_ORGANIZATION"]),

  // Catalog entries with no connector behind them.
  prometheus: {
    statusKey: null,
    fields: [],
    unsupported:
      "This build has no Prometheus connector. Its metric tools read Datadog; a " +
      "PROMETHEUS_URL can be set by the deployment but nothing in the console reads it.",
  },
  grafana: noConnector("Grafana"),
  cloudwatch: noConnector("CloudWatch"),
  newrelic: noConnector("New Relic"),
  opsgenie: noConnector("Opsgenie"),
  alertmanager: {
    statusKey: null,
    fields: [],
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
      unsupported: `This build has no connector for "${id}".`,
    }
  );
}

/** Whether the console can save credentials for this integration itself. */
export function isConnectable(id: string): boolean {
  const spec = specFor(id);
  return spec.unsupported === "" && spec.fields.length > 0;
}
