"""Central configuration, loaded from environment / .env file.

Everything the agent and MCP servers need is resolved here so the rest of the
code never reads os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime_defaults import (
    DEFAULT_BACKEND_URL,
    DEFAULT_DEV_ORIGINS,
    DEFAULT_FRONTEND_URL,
    DEFAULT_LOGS_DATA_PATH,
    DEFAULT_REPO_PATH,
    DEFAULT_PROJECT_REPO_URL,
)

# Project root = two levels up from this file (app/config.py -> app -> root)
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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
    target_repo_path: str = DEFAULT_REPO_PATH
    logs_data_path: str = DEFAULT_LOGS_DATA_PATH
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
    # Sentry connector. With an auth token it queries the real API; otherwise it
    # serves offline issue/event fixtures. SENTRY_ORG/SENTRY_PROJECT scope live calls.
    sentry_api_token: str = ""
    sentry_org: str = ""
    sentry_project: str = ""
    # Incident-memory corpus (prior RCAs/runbooks for similarity search). Blank =
    # the bundled demo corpus; point at your own JSON to search real postmortems.
    incident_corpus_path: str = ""
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
    # Azure credentials for multi-cloud connector.
    azure_subscription_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_tenant_id: str = ""
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
    # Jira Cloud/Server integration for issue management.
    jira_base_url: str = ""  # e.g., https://yourcompany.atlassian.net
    jira_email: str = ""     # Jira account email
    jira_api_token: str = "" # Jira API token
    # PagerDuty API key for incident management (different from webhook token).
    pagerduty_api_key: str = ""

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

    # --- URLs (configurable for different environments) ---
    # Frontend URL for redirects (Stripe, OAuth, etc.)
    frontend_url: str = DEFAULT_FRONTEND_URL
    # Backend API URL (for internal references)
    backend_url: str = DEFAULT_BACKEND_URL
    # Status page base URL
    status_page_url: str = ""
    # Documentation URL
    docs_url: str = ""
    # Public repository URL used in docs and generated links.
    project_repo_url: str = DEFAULT_PROJECT_REPO_URL

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
    # Default timeout for external HTTP requests (seconds)
    http_timeout_default: float = 30.0
    # Timeout for health checks
    http_timeout_health: float = 5.0
    # Timeout for GitHub API calls
    http_timeout_github: float = 15.0
    # Timeout for MCP server calls
    http_timeout_mcp: float = 20.0

    # --- Redis Connection Pool ---
    redis_min_connections: int = 5
    redis_max_connections: int = 50
    redis_connection_timeout: float = 2.0
    redis_socket_timeout: float = 1.0

    # --- Safety Gates Thresholds ---
    # Confidence gate threshold (0.0 - 1.0)
    safety_confidence_threshold: float = 0.80
    # Action-specific confidence thresholds (JSON string)
    safety_action_thresholds: str = '{"rollback": 0.75, "scale_up": 0.70, "scale_down": 0.85, "restart": 0.80, "deploy_fix": 0.90, "run_command": 0.85, "create_pr": 0.70}'
    # Max blast radius per action type (JSON string)
    safety_max_blast_radius: str = '{"scale_down": 3, "rollback": 5, "restart": 2, "deploy_fix": 10, "run_command": 1, "scale_up": 10}'
    # Protected services (comma-separated)
    safety_protected_services: str = "payments,auth,database"

    # --- Cost Optimization Thresholds ---
    cost_underutilized_threshold: float = 20.0  # percentage
    cost_idle_threshold: float = 5.0  # percentage
    cost_anomaly_threshold: float = 1.5  # multiplier (50% increase)
    cost_budget_alert_threshold: float = 0.8  # percentage of budget

    # --- Anomaly Detection ---
    anomaly_z_threshold: float = 3.0
    anomaly_trend_threshold: float = 0.1
    anomaly_pattern_threshold: float = 0.8

    # --- Semantic Cache ---
    cache_similarity_threshold_high: float = 0.95
    cache_similarity_threshold_medium: float = 0.92
    cache_similarity_threshold_low: float = 0.90
    cache_max_entries_small: int = 1000
    cache_max_entries_medium: int = 5000
    cache_max_entries_large: int = 20000

    # --- Log Clustering ---
    log_similarity_threshold: float = 0.6
    similar_incident_threshold: float = 0.7

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
    # SCIM 2.0 provisioning (enterprise SSO): a bearer token the IdP presents to the
    # /scim/v2/Users endpoints, scoped to one org. Empty = SCIM disabled (403).
    # Authentication itself is OIDC/JWT via supabase_jwks_url (app/tenancy/supabase_auth.py).
    copilot_scim_token: str = ""
    copilot_scim_org: str = ""
    copilot_scim_default_role: str = "responder"

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
    copilot_redis_url: str = ""
    # Response cache backend: "memory" (default), "redis", or "none" (disabled).
    # Redis uses COPILOT_REDIS_URL; falls back to memory if unavailable.
    copilot_cache_backend: str = "memory"
    # Default cache TTL in seconds. Individual operations may override.
    copilot_cache_ttl_default: int = 300
    # Max entries in the in-memory cache (LRU eviction past this).
    copilot_cache_max_size: int = 1000
    # Data governance: purge usage/metering rows older than this many days (0 = keep
    # forever). GDPR erasure + audit export are on-demand (see app/governance.py).
    copilot_retention_days: int = 0
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

    # --- Database (production persistence) ---
    # Database URL: postgresql://user:pass@host:5432/db or sqlite:///path.db
    # Empty = SQLite at data/copilot.db (dev default).
    copilot_database_url: str = ""
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
    # Log format: json (production) | human (dev)
    copilot_log_format: str = "human"
    # Log file path (optional, stdout by default)
    copilot_log_file: str = ""

    # --- Service Metadata ---
    service_name: str = "omnipotent"
    service_version: str = "1.0.0"

    # --- Deployment Strategy Defaults ---
    deployment_canary_steps: str = "10,25,50,75,100"  # percentage steps
    deployment_max_error_rate: float = 0.05
    deployment_max_latency_p99_ms: float = 2000.0
    deployment_min_success_rate: float = 0.95
    deployment_health_check_interval_s: int = 10
    deployment_rollback_on_failure: bool = True

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

    @model_validator(mode="after")
    def _fail_closed_in_production(self) -> "Settings":
        # Refuse to start an unauthenticated API in production: a public, open
        # endpoint that drives an LLM is a cost + security liability.
        if self.copilot_env == "production" and not self.copilot_api_token.strip():
            raise ValueError(
                "COPILOT_API_TOKEN must be set when COPILOT_ENV=production "
                "(refusing to start an unauthenticated, internet-exposed API)."
            )
        # Multi-tenant stores per-tenant credentials encrypted at rest; in
        # production refuse to boot with an ephemeral vault key (it would lose
        # every tenant's secrets on restart and weaken isolation).
        if (
            self.copilot_multi_tenant
            and self.copilot_env == "production"
            and not self.copilot_secret_key.strip()
        ):
            raise ValueError(
                "COPILOT_SECRET_KEY must be set when COPILOT_MULTI_TENANT=true in "
                "production (per-tenant secrets are encrypted with it)."
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
        base = list(DEFAULT_DEV_ORIGINS)
        extra = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return base + extra

    @property
    def repo_path(self) -> Path:
        return (ROOT / self.target_repo_path).resolve()

    @property
    def logs_path(self) -> Path:
        return (ROOT / self.logs_data_path).resolve()

    @property
    def sources_root(self) -> Path:
        """The directory tree /sources/* is confined to (defaults to project root)."""
        base = self.copilot_sources_root.strip()
        return Path(base).expanduser().resolve() if base else ROOT.resolve()

    @property
    def offline_mode(self) -> bool:
        """True when no GitHub token is set — the GitHub MCP server runs against
        local demo fixtures instead of the real API."""
        return not self.github_token

    def get_action_confidence_thresholds(self) -> dict[str, float]:
        """Parse action-specific confidence thresholds from JSON string."""
        import json
        try:
            return json.loads(self.safety_action_thresholds)
        except (json.JSONDecodeError, TypeError):
            return {
                "rollback": 0.75,
                "scale_up": 0.70,
                "scale_down": 0.85,
                "restart": 0.80,
                "deploy_fix": 0.90,
                "run_command": 0.85,
                "create_pr": 0.70,
            }

    def get_max_blast_radius(self) -> dict[str, int]:
        """Parse max blast radius per action from JSON string."""
        import json
        try:
            return json.loads(self.safety_max_blast_radius)
        except (json.JSONDecodeError, TypeError):
            return {
                "scale_down": 3,
                "rollback": 5,
                "restart": 2,
                "deploy_fix": 10,
                "run_command": 1,
                "scale_up": 10,
            }

    def get_protected_services(self) -> list[str]:
        """Parse protected services from comma-separated string."""
        return [s.strip() for s in self.safety_protected_services.split(",") if s.strip()]

    def get_canary_steps(self) -> list[int]:
        """Parse canary deployment steps from comma-separated string."""
        try:
            return [int(s.strip()) for s in self.deployment_canary_steps.split(",") if s.strip()]
        except ValueError:
            return [10, 25, 50, 75, 100]


@lru_cache
def get_settings() -> Settings:
    return Settings()
