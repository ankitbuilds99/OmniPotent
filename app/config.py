"""Central configuration, loaded from environment / .env file.

Everything the agent and MCP servers need is resolved here so the rest of the
code never reads os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (app/config.py -> app -> root)
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Fields with a validation_alias below are still constructible by their
        # Python name (Settings(copilot_database_url=...)), which tests rely on.
        populate_by_name=True,
    )

    # --- LLM ---
    # Which provider backs the agent: anthropic | openai | gemini | groq | deepseek.
    copilot_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    # Optional model overrides. Leave blank to use the provider's defaults
    # (see app/llm.py: Opus 4.8 + Haiku 4.5 for Anthropic, Llama 3.3/3.1 for Groq).
    copilot_model: str = ""       # main reasoning / tool-calling model
    copilot_fast_model: str = ""  # cheaper model for lightweight nodes (plan, reflect)

    # --- LLM Request Settings ---
    # Request timeout for LLM API calls (seconds)
    llm_request_timeout: float = 120.0
    # Default max tokens for LLM responses
    llm_max_tokens: int = 4096
    # Circuit breaker settings
    llm_circuit_failure_threshold: int = 5
    llm_circuit_reset_timeout: float = 30.0

    # --- MCP servers ---
    target_repo_path: str = "./sample_repo"
    logs_data_path: str = "./app/mcp/servers/logs_metrics/sample_data"
    github_token: str = ""
    # "owner/repo" — required only when github_token is set (real GitHub mode).
    github_repo: str = ""
    # Datadog observability connector. With both keys set it queries the real
    # Datadog API; otherwise the connector serves the bundled offline fixtures.
    dd_api_key: str = ""
    dd_app_key: str = ""
    dd_site: str = "datadoghq.com"  # e.g. datadoghq.eu, us3.datadoghq.com
    # PagerDuty alerting connector. With a token it queries the real API;
    # otherwise the connector serves offline incident fixtures.
    pagerduty_api_token: str = ""
    # Email identifying the actor for PagerDuty write actions (note/ack/resolve).
    pagerduty_from_email: str = ""
    # Kubernetes connector. With a kubeconfig path set it queries the real cluster
    # (needs the `kubernetes` client); otherwise it serves offline pod/event fixtures.
    kube_config_path: str = ""
    kube_namespace: str = "default"
    # Binaries a runbook `command` step may execute, comma separated (e.g.
    # "kubectl,redis-cli"). Runbooks are operator-authored text that runs against
    # production, so the default is empty: command steps then fail with a message
    # naming this setting instead of running anything.
    runbook_command_allowlist: str = ""
    # Sentry connector. With an auth token it queries the real API; otherwise it
    # serves offline issue/event fixtures. SENTRY_ORG/SENTRY_PROJECT scope live calls.
    sentry_api_token: str = ""
    sentry_org: str = ""
    sentry_project: str = ""
    # Incident-memory corpus (prior RCAs/runbooks for similarity search). Blank =
    # the bundled demo corpus; point at your own JSON to search real postmortems.
    incident_corpus_path: str = ""
    # Evidence backends for the verification agent (claim verification, action
    # safety). Blank means the backend is absent: the verification endpoints then
    # refuse to answer (503) instead of reporting an unverifiable claim as
    # "refuted". Never point these at a stub.
    prometheus_url: str = ""   # Prometheus HTTP API base, e.g. http://prom:9090
    # Grafana dashboard/annotation connector. The deployment owns this URL;
    # credentials, when needed, are supplied through the connector configuration.
    grafana_url: str = ""
    grafana_api_key: str = ""
    grafana_admin_user: str = ""
    grafana_admin_password: str = ""
    # Alertmanager HTTP API base, e.g. http://alertmanager:9093. Read by the
    # knowledge-graph Prometheus sync source; blank means that source skips
    # alert ingestion rather than guessing a localhost address.
    alertmanager_url: str = ""
    loki_url: str = ""         # Loki HTTP API base, e.g. http://loki:3100
    elasticsearch_url: str = ""  # Elasticsearch base, e.g. http://es:9200
    # Distributed-traces connector. With a Jaeger-compatible query URL it queries
    # real traces; otherwise it serves offline OpenTelemetry-style span fixtures.
    traces_api_url: str = ""
    # Deploys / change-events connector. With a deploy-tracker URL it queries real
    # deploys; otherwise it serves offline fixtures (the checkout-svc rollout).
    deploys_api_url: str = ""
    deploys_api_token: str = ""
    # Terraform Cloud/Enterprise connector. With a token it queries the real API;
    # otherwise it serves offline workspace/run fixtures. Organization scopes API calls.
    terraform_cloud_token: str = ""
    terraform_organization: str = ""
    # ArgoCD GitOps connector. With a server URL and auth token it queries the real
    # ArgoCD API; otherwise it serves offline fixtures (the checkout-svc app).
    argocd_server_url: str = ""
    argocd_auth_token: str = ""
    # AWS credentials for cost optimization connector. With credentials set it
    # queries real AWS Cost Explorer; otherwise it serves demo cost data.
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    # GCP credentials for multi-cloud connector. Project ID + service account key.
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    # Azure credentials for multi-cloud connector. Only the subscription id and
    # region are forwarded to the multicloud MCP server (app/mcp/client.py); a
    # service-principal triple (client id/secret/tenant) was declared here and
    # read by nothing, so it is gone rather than pretending to authenticate.
    azure_subscription_id: str = ""
    azure_region: str = "eastus"

    # --- Trigger / delivery (webhooks → investigate → Slack) ---
    # PagerDuty webhook → auto-start an investigation (HMAC-verified).
    pagerduty_webhook_secret: str = ""
    # Slack app: post investigations + approval buttons, verify interaction callbacks.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel: str = ""  # channel id/name investigations post into
    slack_webhook_url: str = ""  # incoming webhook URL for simple posting
    # GitHub webhook secret for HMAC-SHA256 signature verification.
    github_webhook_secret: str = ""
    # GitLab webhook secret token for verification.
    gitlab_webhook_secret: str = ""
    # Microsoft Teams incoming webhook URL for notifications.
    teams_webhook_url: str = ""
    # Target for the generic WEBHOOK notification channel. NotificationPreferences
    # has no per-user webhook URL, so this channel is deployment-level only. Empty
    # means the channel reports delivery failure rather than silently succeeding.
    copilot_notification_webhook_url: str = ""
    # Jira Cloud/Server integration for issue management.
    jira_base_url: str = ""  # e.g., https://yourcompany.atlassian.net
    jira_email: str = ""     # Jira account email
    jira_api_token: str = "" # Jira API token
    # PagerDuty API key for incident management (different from webhook token).
    pagerduty_api_key: str = ""
    # OpsGenie API key for the alerting engine's OpsGenie notification channel.
    # Blank = the channel is skipped.
    opsgenie_api_key: str = ""

    # --- API ---
    # Deployment environment: "development" | "staging" | "production".
    copilot_env: str = "development"
    # Comma-separated browser origins allowed by CORS (the dev server is always
    # allowed). Set this to your deployed frontend origin in production.
    cors_origins: str = ""
    # Shared bearer token guarding the API. Empty = auth disabled (local dev).
    # Set it in production; the frontend sends it via VITE_API_TOKEN.
    copilot_api_token: str = ""
    # Filesystem root that /sources/* may point the repo/logs MCP servers at.
    # Empty = the project root, which keeps the bundled sample data working while
    # preventing the agent from being aimed at arbitrary host paths.
    copilot_sources_root: str = ""
    # Comma-separated `host` or `host:port` values that a request may point a
    # knowledge-graph sync source at (Prometheus, Alertmanager, Jaeger). Empty =
    # no request may set one, which is the safe default: those URLs are fetched
    # from inside the deployment network, so accepting an arbitrary one turns
    # any authenticated user into an SSRF primitive against internal services
    # and the cloud metadata endpoint. An operator naming their own observability
    # hosts here is the only thing that should widen it.
    copilot_sync_allowed_hosts: str = ""
    # Comma-separated `host` or `host:port` values exempt from the outbound-URL
    # check in app/url_guard.py, which otherwise refuses to send a status-page
    # webhook to a loopback, link-local, private or reserved address. Empty is
    # the right default for a hosted deployment: subscribers are customers, and
    # their endpoints are external. Name hosts here only for an on-prem install
    # whose subscribers genuinely are on the internal network.
    copilot_webhook_allowed_hosts: str = ""

    # --- Demo mode ---
    # Several subsystems (runbook execution, remediation handlers, the Slack
    # command handlers, the incident store, billing) have a code path that
    # fabricates a plausible result when their real backend isn't configured.
    # That path is OFF by default: unconfigured subsystems fail closed rather
    # than report success for work they never did. Set this to true only for
    # demos and local walkthroughs — every fabricated payload is then labelled
    # "(simulated)" so it can never be mistaken for a real result. Refused
    # outright when COPILOT_ENV=production.
    copilot_demo_mode: bool = False

    # --- Local state ---
    # Writable directory for state this process owns: SQLite databases, graph
    # checkpoints, the vector store. Empty = <project root>/data. In a container
    # point this at a mounted volume; the image filesystem is usually read-only
    # and is always ephemeral, so anything written elsewhere is lost on restart.
    copilot_state_dir: str = ""

    # --- URLs (configurable for different environments) ---
    # Frontend URL for redirects (Stripe, OAuth, etc.)
    frontend_url: str = "http://localhost:3000"
    # Backend API URL (for internal references)
    backend_url: str = "http://localhost:8000"
    # Status page base URL
    status_page_url: str = ""
    # Documentation URL
    docs_url: str = ""

    # --- Production safety limits (enforced by the API layer) ---
    # Per-client (IP) POST cap per minute; 0 disables rate limiting.
    copilot_rate_limit_per_min: int = 120
    # Max accepted request body size in bytes (guards against huge payloads).
    copilot_max_body_bytes: int = 1_000_000
    # Max characters accepted in a single chat message.
    copilot_max_message_chars: int = 16_000
    # Max concurrent in-memory sessions; the LRU-idle one is evicted past this.
    # Each live session holds one stdio subprocess per MCP server (3), so keep
    # this modest. 0 = unlimited.
    copilot_max_sessions: int = 50
    # Trust the X-Forwarded-For header for client IP (rate limiting). Only enable
    # when behind a trusted reverse proxy that sets it — otherwise clients can
    # spoof it to evade the limiter. Off by default = use the socket peer address.
    copilot_trust_proxy: bool = False
    # Fernet key for the per-tenant secret vault (app/secrets_vault.py). Generate:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Empty = an ephemeral key (encrypted secrets won't survive a restart).
    copilot_secret_key: str = ""

    # --- HTTP Client Settings ---
    # Default timeout for external HTTP requests (seconds). Read by the alerting
    # webhook sender, the background task runner and the OAuth token exchange.
    http_timeout_default: float = 30.0
    # Timeout for GitHub REST calls. The GitHub MCP server runs as a subprocess
    # and reads env rather than this model, so app/mcp/client.py forwards this
    # value to it as HTTP_TIMEOUT_GITHUB.
    http_timeout_github: float = 15.0
    # Timeout for one component health check. app/health.py also derives the
    # AGGREGATE check_all deadline from it, so raising this raises both — a fixed
    # aggregate would otherwise silently cap any value above it.
    http_timeout_health: float = 5.0
    # Timeout for MCP server calls. Like HTTP_TIMEOUT_GITHUB this has to cross a
    # process boundary to matter: app/mcp/client.py forwards it into the stdio
    # server's env as HTTP_TIMEOUT_MCP, which app/mcp/servers/github/server.py
    # (_mcp_timeout) reads. Unforwarded it was inert whatever the operator set.
    http_timeout_mcp: float = 20.0
    # Agent-to-tool gateway limits. These can be tuned per deployment without
    # editing MCP definitions or connector implementations.
    tool_gateway_timeout_seconds: float = Field(default=30.0, gt=0)
    tool_gateway_max_request_bytes: int = Field(default=1_048_576, gt=0)
    tool_gateway_max_response_bytes: int = Field(default=4_194_304, gt=0)

    # --- Redis Connection Pool ---
    # These are PER-WORKLOAD baselines, not process-wide totals. app/redis_pool.py
    # runs four pools (cache, session, pubsub, queue) and scales this baseline by a
    # per-workload factor, so the process can open roughly 3.8x this value in
    # total (session doubles it; pubsub and queue take fractions). Size against
    # your Redis `maxclients` accordingly: 50 here means ~190 connections per
    # replica, and replicaCount multiplies that again.
    redis_min_connections: int = 5
    redis_max_connections: int = 50
    redis_connection_timeout: float = 2.0
    redis_socket_timeout: float = 1.0

    # --- Safety gate ---
    # The single bar for "confident enough to change production" (0.0 - 1.0).
    # Read by the remediation decision gate (app/coordinator/handlers/
    # remediation.py) and the verification consistency checker, which each used to
    # carry their own literal 0.8 and could therefore drift apart.
    safety_confidence_threshold: float = 0.80
    #
    # Deliberately absent (deleted rather than left as decoration): a knob in
    # this file that no code reads is indistinguishable, from the operator's
    # side, from one that is silently ignored — Settings is extra="ignore", so
    # setting it produces no error and no effect. Removed because nothing in the
    # tree consumes them:
    #   SAFETY_ACTION_THRESHOLDS / SAFETY_MAX_BLAST_RADIUS /
    #     SAFETY_PROTECTED_SERVICES — parsed only by the get_* helpers that also
    #     lived here and had no callers (see the note at the end of this class);
    #   COST_UNDERUTILIZED/IDLE/ANOMALY/BUDGET_ALERT_THRESHOLD — their only
    #     reader was app/cost_optimization/analyzer.py, deleted in this branch;
    #   ANOMALY_Z/TREND/PATTERN_THRESHOLD — app/monitoring/ambient.py, likewise
    #     deleted (app/event_intelligence/noise_reduction.py carries its own
    #     NoiseReductionConfig and never read these);
    #   the CACHE_SIMILARITY_*/CACHE_MAX_ENTRIES_* sextet —
    #     app/llm_advanced/semantic_cache.py, deleted;
    #   LOG_SIMILARITY_THRESHOLD — app/log_intelligence/clustering.py, deleted;
    #   SIMILAR_INCIDENT_THRESHOLD — app/similar_search/*, deleted.
    # Re-add one only together with the line that reads it (and if one of those
    # modules comes back, its setting comes back with it).

    # --- Observability ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "omnipotent"
    # Optional Sentry error tracking (needs `pip install sentry-sdk`). Empty = off.
    sentry_dsn: str = ""
    # Datadog APM self-instrumentation of the copilot (needs `ddtrace` + a DD agent).
    # Honors DD_SERVICE / DD_ENV / DD_AGENT_HOST. Off by default.
    dd_trace_enabled: bool = False

    # --- Multi-tenancy (commercial; opt-in) ---
    # When true, the API requires a per-tenant API key (dcp_…) and resolves
    # per-tenant config/integrations/quotas. When false (default), the app runs
    # single-tenant exactly as the offline demo does — multi-tenancy is additive.
    copilot_multi_tenant: bool = False
    # Tenant store DB: SQLite path (default) or a postgres://… URL in production.
    copilot_tenant_db: str = "./copilot_tenants.sqlite"
    # Default org ID for single-tenant mode
    default_org_id: str = "default"
    # Stripe metered billing (optional). With a key set, the usage ledger syncs to
    # Stripe meter events; otherwise billing is local-ledger-only.
    stripe_api_key: str = ""
    stripe_meter_event: str = "investigation"
    # Stripe webhook signing secret — verifies inbound subscription events at
    # /webhooks/stripe (event → store.set_plan). Empty = the endpoint rejects (fails closed).
    stripe_webhook_secret: str = ""
    # Stripe Price IDs for the self-serve checkout, as
    # "team:monthly=price_123,team:annual=price_456,growth:monthly=price_789".
    # Prices are created inside a Stripe account, so they cannot ship in the plan
    # catalog (every PricingPlan.stripe_*_price_id is ""); without this mapping
    # POST /billing/checkout has no price to sell and fails.
    stripe_price_ids: str = ""
    # Supabase Auth SSO (optional). When SUPABASE_JWKS_URL is set, a Supabase-issued
    # JWT (Bearer) authenticates to the multi-tenant API, mapped to the user's org/
    # role membership. Verification uses only the PUBLIC JWKS — no Supabase secret.
    supabase_jwks_url: str = ""
    supabase_jwt_aud: str = "authenticated"
    # Supabase Database (optional). When SUPABASE_URL is set, persistent storage via
    # Supabase is enabled for incidents, investigations, and user data.
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    # Direct PostgreSQL connection string for migrations and raw SQL queries.
    supabase_database_url: str = ""
    # Self-serve onboarding: when true (and multi-tenant is on), POST /signup lets a
    # new user create an org + owner API key without an existing credential. Always
    # provisions the free plan (upgrades go through billing). Disable in locked-down
    # deployments to require admin-side provisioning (CLI / /admin).
    copilot_signup_enabled: bool = True
    # Who may complete OAuth/SSO login. OAuth authenticates against the *provider*,
    # not against this deployment: with GitHub login switched on and no allowlist,
    # every GitHub account on the internet can finish the flow and be issued a
    # session token. Comma-separated addresses and/or domains ("acme.com" matches
    # anyone @acme.com). Empty means no allowlist — see app/auth/endpoints.py
    # `_authorize_oauth_login` for what that permits in each mode.
    copilot_oauth_allowed_emails: str = ""
    copilot_oauth_allowed_domains: str = ""
    # SCIM 2.0 provisioning (enterprise SSO): a bearer token the IdP presents to the
    # /scim/v2/Users endpoints, scoped to one org. Empty = SCIM disabled (403).
    # Authentication itself is OIDC/JWT via supabase_jwks_url (app/tenancy/supabase_auth.py).
    copilot_scim_token: str = ""
    copilot_scim_org: str = ""
    copilot_scim_default_role: str = "responder"
    # Deployment-level operator token for billing-controlled mutations (PATCH
    # /admin/plan). This is deliberately NOT a tenant role: every self-serve signup
    # is an org owner, so gating the plan tier on the owner role would let anyone
    # grant themselves an enterprise plan for free. Empty = the endpoint is disabled
    # and the plan can only change through the Stripe webhook.
    copilot_operator_token: str = ""

    # --- Agent behavior ---
    # Max agent (LLM) calls per turn — bounds the agent<->tools loop.
    copilot_max_iterations: int = 8
    # Token budget per investigation (sum of all LLM calls in a turn). When the
    # running total crosses this, the agent is forced to conclude on its next step
    # — a hard cost kill-switch on top of the iteration cap. 0 = unlimited.
    copilot_max_tokens_per_run: int = 0
    # FLEET-WIDE token ceiling over a rolling window (default 24h) across all
    # investigations — the global backstop above per-run + per-tenant budgets. New
    # investigations are refused once crossed. 0 = unlimited. Shared across replicas
    # when COPILOT_REDIS_URL is set. copilot_global_spend_alert is the soft warn ratio.
    copilot_global_token_cap: int = 0
    copilot_global_spend_window_s: int = 86_400
    copilot_global_spend_alert: float = 0.8
    copilot_checkpoint_db: str = "./copilot_checkpoints.sqlite"
    # Redis URL for the SHARED (cross-replica) rate limiter and cache. Empty =
    # per-process in-memory (single-instance only). e.g. redis://host:6379/0
    # Also accepts the bare REDIS_URL that managed platforms (Render, Heroku,
    # Helm, docker-compose) inject by convention — see copilot_database_url.
    copilot_redis_url: str = Field(
        default="",
        validation_alias=AliasChoices("COPILOT_REDIS_URL", "REDIS_URL"),
    )
    # Shared alert deduplication snapshots. Empty is process-local and should be
    # used only for development or a deliberately single-replica deployment.
    copilot_dedup_state_url: str = Field(
        default="",
        validation_alias=AliasChoices("COPILOT_DEDUP_STATE_URL"),
    )
    copilot_dedup_state_required: bool = False
    # Response cache backend: "memory" (default), "redis", or "none" (disabled).
    # Redis uses COPILOT_REDIS_URL; falls back to memory if unavailable.
    copilot_cache_backend: str = "memory"
    # Max entries in the in-memory cache (LRU eviction past this). Read by
    # app/cache.py:get_cache.
    copilot_cache_max_size: int = 1000
    # COPILOT_CACHE_TTL_DEFAULT used to live here. The only things that would
    # have honoured it are app/cache.py's @cached/@cached_async decorators,
    # which nothing in app/ applies, so it was a knob with no reachable effect.
    # COPILOT_RETENTION_DAYS likewise: app/governance.apply_retention() is
    # never scheduled by the app, so a retention window set here would have
    # purged nothing while promising GDPR-style pruning. Both are removed;
    # re-add whichever one gets a caller.
    # Prompt caching (Anthropic): cache the stable system prompt + bound tool
    # schemas so the agent's many loop iterations re-read them at ~0.1x cost
    # instead of re-paying full input price each turn. No-op for other providers.
    copilot_prompt_cache: bool = True
    # Fix verification: after the RCA report is compiled, a verify node judges
    # whether a proposed fix actually addresses the root cause and — bounded by
    # copilot_verify_max_attempts — bounces the run back to the agent to revise a
    # fix that misses. When off, the run ends at the report node (prior behavior).
    copilot_verify_fix: bool = True
    # How many times the verify node may send the run back to the agent to revise a
    # fix that doesn't address the root cause. Bounds the loop; 0 disables the
    # bounce (verification still annotates the report). Default 1 (single revision).
    copilot_verify_max_attempts: int = 1
    # Sandbox counterfactual: when the agent attaches a machine-applicable `patch`
    # to its PR, apply it to a throwaway copy of the target repo and run a reproducer
    # to PROVE the fix resolves the incident. Executes the model's patch, so OFF by
    # default. See app/graph/sandbox.py.
    copilot_sandbox_verify: bool = False
    # Reproducer command the sandbox runs (in the temp repo copy) before and after
    # applying the patch — a FAIL→PASS transition proves the fix. Operator-set, never
    # model-supplied. Defaults to the bundled sample_repo's node reproducer.
    copilot_sandbox_cmd: str = "node checkout.test.js"
    # Wall-clock timeout (seconds) for each sandbox subprocess run.
    copilot_sandbox_timeout_s: int = 30
    # Confidence gate: refuse to AUTO-approve a consequential write that rests on
    # thin evidence (a human can still approve it explicitly). Protects programmatic
    # approvers — evals, bots, a future auto-remediation loop — from rubber-stamping
    # a high-risk action on a low-confidence investigation. true (default) | false.
    copilot_confidence_gate: bool = True
    # Continual learning: after a confidently-resolved investigation, append a
    # structured runbook record to the learned incident corpus so future runs
    # warm-start from it ("have we seen this before?" improves over time). Only
    # well-evidenced, non-abstained runs are recorded. true (default) | false.
    copilot_learn_incidents: bool = True
    # Where learned incidents accumulate (JSON). Kept separate from the bundled
    # demo corpus so the shipped fixture is never mutated. Defaults to project root.
    copilot_learned_corpus: str = ""
    # Adversarial RCA critique: after the report is compiled, a Prosecutor tries to
    # refute the root cause and a Defender rebuts using only observed evidence; a
    # deterministic judge downgrades/abstains when a serious objection stands. Cuts
    # confident-but-wrong RCAs. Two fast-model calls; true (default) | false.
    copilot_adversarial_critique: bool = True
    # Model routing: triage clearly-informational requests ("which services…?") onto
    # the cheap fast model in the agent loop, reserving the main reasoning model for
    # real incident investigation. Biased toward the main model on any doubt.
    copilot_model_routing: bool = True
    # Proactive SLO poller: periodically evaluate each service's error-budget burn
    # rate and auto-open an investigation when it's page-worthy — before a human is
    # paged. Spends tokens autonomously, so OFF by default. true | false (default).
    copilot_slo_poller: bool = False
    # How often the poller sweeps services (seconds) and the per-service cooldown
    # before it will re-open an investigation for a still-burning service.
    copilot_slo_poll_interval_s: int = 300
    copilot_slo_cooldown_s: int = 3600
    # How often the on-call sweeper looks for alerts whose next escalation step
    # has come due. This is the resolution of every `delay_minutes` in every
    # escalation policy, so it wants to be well under the shortest delay a team
    # would configure. Not a feature flag: an on-call system that does not
    # escalate is not on-call, so there is no "off".
    copilot_escalation_sweep_interval_s: int = 30
    # Parallel multi-hypothesis probe: when the RCA has 2+ competing hypotheses,
    # score each concurrently against the observed evidence and re-rank them, so a
    # weakly-supported top hypothesis can't win by ordering alone. true (default).
    copilot_parallel_hypotheses: bool = True
    # Resilience: bounded retries with exponential backoff + a circuit breaker around
    # each LLM call, so a transient 429/5xx/timeout doesn't fail an investigation and
    # a dead provider is stopped being hammered. 1 disables retries.
    copilot_llm_retries: int = 3
    # Optional cross-provider failover: when the primary provider stays unavailable,
    # retry the call on this provider (one of anthropic|openai|gemini|groq|deepseek).
    # Empty = no failover. Needs the fallback provider's key configured.
    copilot_fallback_provider: str = ""
    # Progressive-autonomy remediation: apply a REVERSIBLE fix, watch the signal, and
    # auto-revert on regression. Mutates real infra, so DOUBLY gated: OFF by default,
    # and DRY-RUN by default even when enabled — an operator must set both to let it
    # act. Only reversible actions, only on a high-confidence investigation.
    copilot_autonomy: bool = False
    copilot_autonomy_dryrun: bool = True
    # How long to watch the signal after applying a remediation before judging recovery.
    copilot_autonomy_watch_s: int = 60

    # --- Multi-agent collaboration ---
    # Enable multi-agent mode with specialized agents (Investigator, Remediation,
    # Reviewer, Orchestrator). When false (default), the original single-agent mode
    # is used. Multi-agent mode provides better separation of concerns and more
    # structured handoffs between investigation, remediation, and review phases.
    copilot_multi_agent_enabled: bool = False
    # Agent mode: "single" (legacy) | "multi" (collaborative agents). When
    # COPILOT_MULTI_AGENT_ENABLED is true, this defaults to "multi"; otherwise "single".
    copilot_agent_mode: str = "single"

    # --- WebSocket support (alternative to SSE) ---
    # Enable WebSocket endpoint at /ws/chat/{thread_id}. When false, WebSocket
    # connections are refused (SSE remains the default transport).
    copilot_websocket_enabled: bool = True
    # Ping interval in seconds for WebSocket heartbeat (connection health).
    copilot_websocket_ping_interval: int = 30
    # Require authentication for WebSocket connections. When true, clients must
    # provide a valid token via the subprotocol.
    copilot_require_auth: bool = False

    # --- Vector RAG (embeddings for semantic search) ---
    # Vector store backend: chromadb | faiss | none (default). When "none", the
    # existing BM25 lexical search is used exclusively. When enabled, hybrid search
    # combines BM25 + vector similarity via RRF (Reciprocal Rank Fusion).
    copilot_vector_store: str = "none"
    # Embedding model: OpenAI (text-embedding-3-small, text-embedding-3-large) or
    # local sentence-transformers (all-MiniLM-L6-v2, all-mpnet-base-v2). OpenAI
    # models require OPENAI_API_KEY; local models run offline but are slower.
    copilot_embedding_model: str = "text-embedding-3-small"
    # Persistence directory for the vector store. Defaults to .vector_store in the
    # project root.
    copilot_vector_persist_dir: str = ""

    # --- Knowledge graph (Neo4j) ---
    # Bolt URI of a Neo4j server, e.g. bolt://neo4j:7687. Empty (the default)
    # means the knowledge graph is served from the `graph_nodes`/`graph_edges`
    # tables in `copilot_database_url` instead — durable and org-scoped, but
    # without Cypher. When this IS set and the server cannot be reached, the
    # knowledge-graph routes return 503 rather than silently writing somewhere
    # else, because "the graph looks empty" is indistinguishable from "the graph
    # says nothing depends on this service" to the person reading a blast radius.
    copilot_neo4j_uri: str = ""
    copilot_neo4j_username: str = "neo4j"
    copilot_neo4j_password: str = ""
    copilot_neo4j_database: str = "neo4j"

    # --- Agent marketplace ---
    # Where installed marketplace agents are recorded. Empty = data/marketplace-installed.json
    # under the project root. This deliberately does NOT live in app/marketplace/:
    # that directory is part of the image, so every install was erased by the next
    # deploy, and a read-only rootfs made the write fail outright.
    copilot_marketplace_state_path: str = ""

    # --- Database (production persistence) ---
    # Database URL: postgresql://user:pass@host:5432/db or sqlite:///path.db
    # Empty = SQLite under COPILOT_STATE_DIR (dev default).
    # Managed platforms inject a bare DATABASE_URL (Render, Heroku, Helm charts,
    # docker-compose all do), so accept that name too — otherwise a correctly
    # provisioned Postgres is silently ignored and the app falls back to SQLite
    # on an ephemeral container filesystem.
    copilot_database_url: str = Field(
        default="",
        validation_alias=AliasChoices("COPILOT_DATABASE_URL", "DATABASE_URL"),
    )
    # Connection pool settings (PostgreSQL only).
    copilot_db_pool_size: int = 10
    copilot_db_pool_overflow: int = 20
    copilot_db_pool_timeout: int = 30
    copilot_db_pool_recycle: int = 3600
    # Echo SQL statements to logs (debug only).
    copilot_db_echo: bool = False

    # --- Logging ---
    # Log level: DEBUG | INFO | WARNING | ERROR | CRITICAL
    copilot_log_level: str = "INFO"
    # Log format: json | text (aliases: "human" == "text"). Empty (the default)
    # means "decide from the environment": json in production, text elsewhere.
    # It has to be empty rather than "human", otherwise a production deployment
    # that never set the variable would be pushed off JSON logs.
    # Read by app/observability_core.configure_logging.
    copilot_log_format: str = ""
    # Log file path. Empty (the default) = stdout only; when set, records go to
    # BOTH stdout and this file. Read by configure_logging, which creates the
    # parent directory.
    copilot_log_file: str = ""

    # --- Service Metadata ---
    # Reported to Datadog (DD_SERVICE/DD_VERSION) and Sentry (release=) by
    # app/observability_core.py so traces and errors are grouped per deploy.
    service_name: str = "omnipotent"
    service_version: str = "1.0.0"

    # DEPLOYMENT_* (canary steps, max error rate, max p99 latency, min success
    # rate, health-check interval, rollback-on-failure) used to be declared here
    # with a get_canary_steps() helper. Nothing progressive-delivery related read
    # any of them — the canary rollout the names imply is ArgoCD's, configured in
    # deploy/argocd/, not by this process — so they promised control the app
    # never had. Removed rather than left as decoration.

    # --- Email Notifications ---
    # Email provider: smtp | ses | sendgrid | resend
    email_provider: str = "smtp"
    email_from: str = "noreply@omnipotent.io"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    sendgrid_api_key: str = ""
    resend_api_key: str = ""

    # --- Webhook Configuration ---
    # Auto-trigger investigation when webhook creates an incident
    copilot_auto_investigate: bool = True
    # Generic webhook shared secret for HMAC verification
    copilot_webhook_secret: str = ""
    # HSTS enabled (Strict-Transport-Security header)
    copilot_hsts_enabled: bool = True

    @field_validator("copilot_provider")
    @classmethod
    def _normalize_provider(cls, v: str) -> str:
        v = (v or "").strip().lower()
        allowed = {"anthropic", "openai", "gemini", "groq", "deepseek"}
        if v not in allowed:
            raise ValueError(f"COPILOT_PROVIDER must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("copilot_env")
    @classmethod
    def _normalize_env(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v.startswith("prod"):
            return "production"
        elif v.startswith("stag"):
            return "staging"
        return "development"

    @model_validator(mode="before")
    @classmethod
    def _prefer_nonempty_service_urls(cls, values):
        """Do not let empty dotenv placeholders hide managed URLs.

        ``AliasChoices`` selects the first present key, even when its value is
        empty. Local dotenv files commonly contain empty COPILOT_* placeholders
        while deployments inject the bare DATABASE_URL/REDIS_URL names.
        """
        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        for preferred, fallback in (
            ("COPILOT_DATABASE_URL", "DATABASE_URL"),
            ("COPILOT_REDIS_URL", "REDIS_URL"),
        ):
            if not str(normalized.get(preferred, "") or "").strip():
                fallback_value = normalized.get(fallback)
                if str(fallback_value or "").strip():
                    normalized[preferred] = fallback_value
        return normalized

    @model_validator(mode="after")
    def _fail_closed_in_production(self) -> "Settings":
        # Refuse to start an unauthenticated API in production: a public, open
        # endpoint that drives an LLM is a cost + security liability.
        if self.copilot_env == "production" and not self.copilot_api_token.strip():
            raise ValueError(
                "COPILOT_API_TOKEN must be set when COPILOT_ENV=production "
                "(refusing to start an unauthenticated, internet-exposed API)."
            )
        # The secret key is not multi-tenant-only: it also signs the OAuth/session
        # JWTs (app/auth/oauth.py). Without it those fall back to a well-known
        # constant, so anyone can mint a valid token. Require it for every
        # production deployment, not just multi-tenant ones.
        if self.copilot_env == "production" and not self.copilot_secret_key.strip():
            raise ValueError(
                "COPILOT_SECRET_KEY must be set when COPILOT_ENV=production "
                "(it signs session tokens and encrypts stored secrets; without it "
                "tokens would be signed with a publicly known key)."
            )
        # Demo mode fabricates successful results for subsystems that have no
        # real backend. That is never acceptable in production.
        if self.copilot_demo_mode and self.copilot_env == "production":
            raise ValueError(
                "COPILOT_DEMO_MODE must be false when COPILOT_ENV=production "
                "(it makes remediation and runbook steps report success without "
                "doing anything)."
            )
        # Human-in-the-loop graph state must survive a process restart and be
        # resumable by any worker. SQLite is intentionally supported for local
        # development, but a local file is not a production durability or
        # multi-replica boundary. Require the explicitly documented Postgres
        # checkpointer before allowing a production process to start.
        if self.copilot_env == "production" and not self.copilot_checkpoint_db.strip().lower().startswith(("postgres://", "postgresql://")):
            raise ValueError(
                "COPILOT_CHECKPOINT_DB must be a PostgreSQL URL when "
                "COPILOT_ENV=production (SQLite checkpoints are single-process "
                "and cannot safely resume approvals across restarts/workers)."
            )
        if self.copilot_env == "production" and not self.copilot_redis_url.strip().lower().startswith(("redis://", "rediss://")):
            raise ValueError(
                "COPILOT_REDIS_URL must be a Redis URL when COPILOT_ENV=production "
                "(in-memory Redis substitutes cannot provide shared state or failover)."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.copilot_env == "production"

    @property
    def is_staging(self) -> bool:
        return self.copilot_env == "staging"

    @property
    def is_development(self) -> bool:
        return self.copilot_env == "development"

    @property
    def allowed_origins(self) -> list[str]:
        if self.is_production:
            # In production, only allow explicitly configured origins
            origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
            if self.frontend_url:
                origins.append(self.frontend_url)
            return origins
        # In development, allow localhost
        base = ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]
        extra = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return base + extra

    @property
    def stripe_prices(self) -> dict[str, str]:
        """`{"<plan>:<cycle>": "price_…"}` parsed from STRIPE_PRICE_IDS.

        Malformed entries are dropped rather than raising: a typo in one price
        should leave the other plans sellable, and the caller already reports
        "no price configured for <plan> <cycle>" for whatever is missing.
        """
        prices: dict[str, str] = {}
        for item in self.stripe_price_ids.split(","):
            key, sep, value = item.partition("=")
            if sep and key.strip() and value.strip():
                prices[key.strip().lower()] = value.strip()
        return prices

    @property
    def oauth_allowed_emails(self) -> set[str]:
        """Explicit addresses permitted to log in through OAuth (lowercased)."""
        return {
            e.strip().lower()
            for e in self.copilot_oauth_allowed_emails.split(",")
            if e.strip()
        }

    @property
    def oauth_allowed_domains(self) -> set[str]:
        """Email domains permitted to log in through OAuth (lowercased, no '@')."""
        return {
            d.strip().lower().lstrip("@")
            for d in self.copilot_oauth_allowed_domains.split(",")
            if d.strip()
        }

    @property
    def oauth_allowlist_configured(self) -> bool:
        return bool(self.oauth_allowed_emails or self.oauth_allowed_domains)

    def oauth_email_allowed(self, email: str) -> bool:
        """Whether this address matches the configured allowlist.

        Meaningless unless `oauth_allowlist_configured` — an empty allowlist
        matches nothing, and callers must decide what "unconfigured" means for
        their deployment mode rather than reading it as "deny everyone".
        """
        addr = (email or "").strip().lower()
        if not addr:
            return False
        domain = addr.split("@")[-1] if "@" in addr else ""
        return addr in self.oauth_allowed_emails or domain in self.oauth_allowed_domains

    @property
    def sync_allowed_hosts(self) -> set[str]:
        """Hosts a sync-source URL may name, as lowercase `host` or `host:port`."""
        return {
            h.strip().lower()
            for h in self.copilot_sync_allowed_hosts.split(",")
            if h.strip()
        }

    @property
    def repo_path(self) -> Path:
        return (ROOT / self.target_repo_path).resolve()

    @property
    def logs_path(self) -> Path:
        return (ROOT / self.logs_data_path).resolve()

    @property
    def marketplace_state_path(self) -> Path:
        raw = self.copilot_marketplace_state_path.strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return ROOT / "data" / "marketplace-installed.json"

    @property
    def sources_root(self) -> Path:
        """The directory tree /sources/* is confined to (defaults to project root)."""
        base = self.copilot_sources_root.strip()
        return Path(base).expanduser().resolve() if base else ROOT.resolve()

    @property
    def state_dir(self) -> Path:
        """Directory this process writes local state into (see copilot_state_dir).

        Resolution only — use ensure_state_dir()/state_path() before opening a
        file, so the directory exists.
        """
        base = self.copilot_state_dir.strip()
        return (Path(base).expanduser() if base else (ROOT / "data")).resolve()

    def ensure_state_dir(self) -> Path:
        """state_dir, created if missing. Raises if it cannot be created — a
        read-only state directory is a deployment error, not something to paper
        over by silently relocating data to /tmp where it is lost on restart."""
        path = self.state_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def state_path(self, name: str) -> Path:
        """Resolve `name` under state_dir. Absolute paths and paths the operator
        wrote explicitly (./foo, ../foo) are honoured as-is."""
        candidate = Path(name).expanduser()
        if candidate.is_absolute() or name.startswith((".", "/", "~")):
            candidate.parent.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        return self.ensure_state_dir() / name

    @property
    def offline_mode(self) -> bool:
        """True when no GitHub token is set — the GitHub MCP server runs against
        local demo fixtures instead of the real API."""
        return not self.github_token

    # get_action_confidence_thresholds / get_max_blast_radius /
    # get_protected_services / get_canary_steps lived here and had no callers —
    # they parsed SAFETY_ACTION_THRESHOLDS / SAFETY_MAX_BLAST_RADIUS /
    # SAFETY_PROTECTED_SERVICES / DEPLOYMENT_CANARY_STEPS, settings that were
    # themselves read by nothing, so the per-action safety gate and the canary
    # rollout they appeared to configure did not exist. They are removed along
    # with those fields. (SAFETY_CONFIDENCE_THRESHOLD is a different thing and
    # survives: it is read directly by the remediation decision gate and the
    # verification consistency checker.)


@lru_cache
def get_settings() -> Settings:
    return Settings()
