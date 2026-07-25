import { useEffect, useRef, useState } from "react";

import { getMetrics, health } from "../api";
import { useCopilot } from "../hooks/useCopilot";
import { modelShort, providerLabel, useConfig } from "../hooks/useConfig";
import { useMe } from "../hooks/useMe";
import { AdminPanel } from "./AdminPanel";
import { SignupModal } from "./SignupModal";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { Message } from "./Message";
import { Sidebar } from "./Sidebar";
import { ThemePicker } from "./ThemePicker";

const PIPELINE = [
  { n: 1, icon: "clipboard", name: "Plan", sub: "Scope + prior incidents" },
  { n: 2, icon: "search", name: "Investigate", sub: "Logs · traces · k8s · deploys" },
  { n: 3, icon: "pause", name: "Approve", sub: "Human gate before writes" },
  { n: 4, icon: "insight", name: "Diagnose", sub: "Find root cause" },
  { n: 5, icon: "refresh", name: "Reflect", sub: "Verify completeness" },
  { n: 6, icon: "download", name: "Report", sub: "RCA + postmortem" },
  { n: 7, icon: "check", name: "Verify", sub: "Prove the fix works" },
];

const SUGGESTIONS = [
  "Why is the checkout API throwing 500 errors?",
  "Are any checkout-svc pods crashing in Kubernetes?",
  "What recent deploy most likely caused the checkout failures?",
  "Have we seen this checkout incident before?",
];

const FEATURES = [
  { icon: "insight", title: "Root-cause Reports", sub: "Ranked hypotheses + postmortem", color: "#00E7FF" },
  { icon: "server", title: "9 MCP Tool Servers", sub: "Logs · traces · k8s · sentry · deploys", color: "#7C3AED" },
  { icon: "lock", title: "Risk-tiered Approval", sub: "Human-in-the-loop before writes", color: "#00FFB3" },
  { icon: "alert", title: "Injection + PII Guards", sub: "Untrusted telemetry scrubbed", color: "#FF4D6D" },
  { icon: "cpu", title: "Token Budget", sub: "Per-investigation cost kill-switch", color: "#FFC247" },
  { icon: "branch", title: "Multi-tenant + RBAC", sub: "Orgs · roles · scoped API keys", color: "#00E7FF" },
  { icon: "database", title: "Usage Metering", sub: "Plan quotas · Stripe sync", color: "#7C3AED" },
  { icon: "check", title: "Tamper-evident Audit", sub: "Hash-chained, verifiable trail", color: "#3CF281" },
  { icon: "tool", title: "SSO Login", sub: "Supabase / OIDC JWT", color: "#00E7FF" },
  { icon: "refresh", title: "Deterministic Evals", sub: "Record/replay golden gate", color: "#7C3AED" },
  { icon: "send", title: "Triggered → Slack", sub: "PagerDuty auto-investigate", color: "#00FFB3" },
  { icon: "sparkles", title: "Datadog APM + LLMObs", sub: "Trace the agent itself", color: "#FFC247" },
];

const STATS = [
  { icon: "server", value: "9", label: "MCP Servers", color: "#00E7FF" },
  { icon: "cpu", value: "48", label: "Agent Tools", color: "#7C3AED" },
  { icon: "sparkles", value: "5", label: "LLM Providers", color: "#00FFB3" },
  { icon: "check", value: "379", label: "Tests Passing", color: "#3CF281" },
];

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function IncidentSignal() {
  const [data, setData] = useState<number[]>([2, 1, 3, 2, 4, 3, 6, 11, 22, 40, 60, 71]);
  useEffect(() => {
    let active = true;
    getMetrics()
      .then((m) => {
        const s = m.services?.["checkout-svc"]?.error_rate_5xx;
        if (active && s && s.length) setData(s.map((p) => Math.round((p.value ?? 0) * 100)));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  const W = 180;
  const H = 60;
  const n = data.length;
  const max = 75;
  const px = (i: number) => (i / (n - 1)) * W;
  const py = (v: number) => H - (v / max) * H;
  const line = data.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `0,${H} ${line} ${W},${H}`;
  const last = Math.round(data[data.length - 1] ?? 0);
  return (
    <div className="signal-card">
      <div className="signal-card__header">
        <div className="signal-card__status">
          <span className="signal-card__dot" />
          <span className="signal-card__svc">checkout-svc</span>
        </div>
        <span className="signal-card__badge">5xx ↑{last}%</span>
      </div>
      <svg className="signal-card__chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="sig-gradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#FF4D6D" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#FF4D6D" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={area} fill="url(#sig-gradient)" />
        <polyline
          points={line}
          fill="none"
          stroke="#FF4D6D"
          strokeWidth="2.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
      <div className="signal-card__footer">
        <Icon name="alert" size={12} />
        <span>Sample incident signal</span>
      </div>
    </div>
  );
}

function TopBar({
  onHome,
  onNew,
  onAdmin,
  onSignup,
}: {
  onHome: () => void;
  onNew: () => void;
  onAdmin: () => void;
  onSignup: () => void;
}) {
  const { config: cfg } = useConfig();
  const me = useMe();
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    const ping = async () => {
      const ok = await health();
      if (active) setOnline(ok);
    };
    ping();
    const id = setInterval(ping, 10_000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  return (
    <header className="topbar">
      <button className="topbar__brand" onClick={onHome} title="Back to home">
        <span className="topbar__logo">
          <Icon name="sparkles" size={22} />
        </span>
        <span className="topbar__text">
          <strong>DevOps Copilot</strong>
          <span>AI-Powered Incident Investigation</span>
        </span>
      </button>

      <div className="topbar__actions">
        <button className="topbar__btn topbar__btn--primary" onClick={onNew}>
          <Icon name="refresh" size={14} />
          New Investigation
        </button>
        <button className="topbar__btn" onClick={onSignup}>
          <Icon name="sparkles" size={14} />
          Sign up
        </button>
        <button className="topbar__btn" onClick={onAdmin}>
          <Icon name="lock" size={14} />
          Admin
        </button>

        <div className="topbar__divider" />

        {cfg && (
          <div className="topbar__model">
            <span className="topbar__model-provider">{providerLabel(cfg.provider)}</span>
            <span className="topbar__model-name">{modelShort(cfg.model)}</span>
          </div>
        )}

        <div className={`topbar__status ${online === null ? '' : online ? 'topbar__status--online' : 'topbar__status--offline'}`}>
          <span className="topbar__status-dot" />
          <span>{online === null ? "Connecting" : online ? "Online" : "Offline"}</span>
        </div>

        <ThemePicker />

        <div className="topbar__avatar" title={me.label}>
          {me.label.charAt(0).toUpperCase()}
        </div>
      </div>
    </header>
  );
}

function StatCard({ stat }: { stat: typeof STATS[0] }) {
  return (
    <div className="stat-card">
      <div className="stat-card__icon" style={{ color: stat.color, background: `${stat.color}15` }}>
        <Icon name={stat.icon} size={18} />
      </div>
      <div className="stat-card__content">
        <span className="stat-card__value">{stat.value}</span>
        <span className="stat-card__label">{stat.label}</span>
      </div>
    </div>
  );
}

function FeatureCard({ feature }: { feature: typeof FEATURES[0] }) {
  return (
    <div className="feature-card">
      <div className="feature-card__icon" style={{ color: feature.color, background: `${feature.color}15` }}>
        <Icon name={feature.icon} size={16} />
      </div>
      <div className="feature-card__content">
        <strong>{feature.title}</strong>
        <span>{feature.sub}</span>
      </div>
    </div>
  );
}

function PipelineStep({ step, isLast }: { step: typeof PIPELINE[0]; isLast: boolean }) {
  return (
    <div className="pipeline-step">
      <div className="pipeline-step__header">
        <span className="pipeline-step__number">{step.n}</span>
        <span className="pipeline-step__name">{step.name}</span>
        {!isLast && <span className="pipeline-step__connector" />}
      </div>
      <div className="pipeline-step__body">
        <div className="pipeline-step__icon">
          <Icon name={step.icon} size={16} />
        </div>
        <span className="pipeline-step__sub">{step.sub}</span>
      </div>
    </div>
  );
}

export function Console({ onHome }: { onHome: () => void }) {
  const { turns, busy, awaitingApproval, send, respond, stop, newConversation, sendFeedback } =
    useCopilot();
  const endRef = useRef<HTMLDivElement>(null);
  const me = useMe();
  const [adminOpen, setAdminOpen] = useState(false);
  const [signupOpen, setSignupOpen] = useState(false);
  const disabled = busy || awaitingApproval;

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [turns]);

  const empty = turns.length === 0;

  const liveStatus = awaitingApproval
    ? "The agent is waiting for your approval."
    : busy
      ? "Investigating, please wait…"
      : "";

  return (
    <div className="console">
      <a className="skip-link" href="#console-main">Skip to main content</a>
      <TopBar
        onHome={onHome}
        onNew={newConversation}
        onAdmin={() => setAdminOpen(true)}
        onSignup={() => setSignupOpen(true)}
      />
      {adminOpen && <AdminPanel onClose={() => setAdminOpen(false)} />}
      {signupOpen && <SignupModal onClose={() => setSignupOpen(false)} />}
      <div className="sr-only" role="status" aria-live="polite">
        {liveStatus}
      </div>

      <div className="console__body">
        <Sidebar />
        <main className="console__main" id="console-main">
          {empty ? (
            <div className="console__scroll">
              {/* Hero Section */}
              <section className="hero-section">
                <div className="hero-section__content">
                  <p className="hero-section__greeting">{greeting()}, {me.label}!</p>
                  <h1 className="hero-section__title">
                    Investigate a <span className="gradient-text">Production Incident</span>
                  </h1>
                  <p className="hero-section__description">
                    Ask a question and DevOps Copilot will pull logs, metrics, traces, Kubernetes
                    and recent deploys, read the code, correlate the change, and deliver a
                    structured root-cause report — <strong>pausing for your approval before any write.</strong>
                  </p>
                </div>
                <IncidentSignal />
              </section>

              {/* Trust Pills */}
              <div className="trust-pills">
                <span className="trust-pill">
                  <Icon name="lock" size={14} />
                  Human-in-the-loop
                </span>
                <span className="trust-pill">
                  <Icon name="branch" size={14} />
                  Multi-tenant + RBAC
                </span>
                <span className="trust-pill">
                  <Icon name="check" size={14} />
                  Tamper-evident Audit
                </span>
                <span className="trust-pill">
                  <Icon name="server" size={14} />
                  Self-hostable
                </span>
              </div>

              {/* Stats Grid */}
              <div className="stats-grid">
                {STATS.map((stat) => (
                  <StatCard key={stat.label} stat={stat} />
                ))}
              </div>

              {/* Pipeline Section */}
              <section className="section">
                <h2 className="section__title">
                  <Icon name="branch" size={18} />
                  How It Works
                </h2>
                <div className="pipeline-grid">
                  {PIPELINE.map((step, i) => (
                    <PipelineStep key={step.name} step={step} isLast={i === PIPELINE.length - 1} />
                  ))}
                </div>
              </section>

              {/* Features Section */}
              <section className="section">
                <h2 className="section__title">
                  <Icon name="sparkles" size={18} />
                  Built-in Capabilities
                </h2>
                <div className="features-grid">
                  {FEATURES.map((feature) => (
                    <FeatureCard key={feature.title} feature={feature} />
                  ))}
                </div>
              </section>

              {/* Suggested Questions */}
              <section className="section">
                <h2 className="section__title">
                  <Icon name="send" size={18} />
                  Suggested Questions
                </h2>
                <div className="suggestions-grid">
                  {SUGGESTIONS.map((q) => (
                    <button
                      key={q}
                      className="suggestion-btn"
                      disabled={disabled}
                      onClick={() => send(q)}
                    >
                      <Icon name="search" size={14} />
                      {q}
                    </button>
                  ))}
                </div>
              </section>

              <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
            </div>
          ) : (
            <>
              <div className="console__scroll">
                <div className="thread">
                  {turns.map((turn, i) => (
                    <Message
                      key={turn.id}
                      turn={turn}
                      onDecision={respond}
                      onRetry={
                        turn.status === "error" && !disabled && turns[i - 1]?.role === "user"
                          ? () => send(turns[i - 1].text)
                          : undefined
                      }
                      onFeedback={
                        turn.role === "assistant"
                          ? (rating) => sendFeedback(rating, turns[i - 1]?.text ?? "")
                          : undefined
                      }
                    />
                  ))}
                  <div ref={endRef} />
                </div>
              </div>
              <div className="console__dock">
                <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
