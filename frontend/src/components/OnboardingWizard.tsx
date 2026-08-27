import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles,
  CheckCircle,
  ArrowRight,
  ArrowLeft,
  Play,
  Github,
  MessageSquare,
  Database,
  Bell,
  BookOpen,
  Zap,
  Shield,
  Users,
} from "lucide-react";

import { chatStream } from "../api";
import { apiFetch, describeFailure } from "../lib/apiClient";
import { specFor } from "../lib/integrationSpecs";

interface OnboardingStep {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  component: React.ReactNode;
}

interface OnboardingWizardProps {
  onComplete: () => void;
  onSkip: () => void;
}

// Step 1: Welcome
function WelcomeStep({ onTryPlayground }: { onTryPlayground: () => void }) {
  return (
    <div className="text-center">
      <motion.div
        className="w-20 h-20 mx-auto mb-6 rounded-2xl flex items-center justify-center"
        style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ type: "spring", duration: 0.6 }}
      >
        <Sparkles className="w-10 h-10 text-white" />
      </motion.div>

      <h2 className="text-2xl font-bold mb-3">Welcome to OmniPotent</h2>
      <p className="text-gray-400 mb-8 max-w-md mx-auto">
        AI-powered incident investigation that helps you resolve issues 5x faster.
        Let's get you set up in just a few minutes.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        {[
          { icon: <Zap className="w-5 h-5" />, title: "5x Faster MTTR", desc: "AI investigates while you sleep" },
          { icon: <Shield className="w-5 h-5" />, title: "Safety Gates", desc: "Human approval for risky actions" },
          { icon: <Users className="w-5 h-5" />, title: "6-Agent System", desc: "Parallel AI investigation" },
        ].map((feature, i) => (
          <motion.div
            key={i}
            className="p-4 rounded-xl border"
            style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 + i * 0.1 }}
          >
            <div className="w-10 h-10 rounded-lg flex items-center justify-center mb-3 mx-auto" style={{ backgroundColor: "var(--accent)", color: "white" }}>
              {feature.icon}
            </div>
            <div className="font-medium">{feature.title}</div>
            <div className="text-sm text-gray-500">{feature.desc}</div>
          </motion.div>
        ))}
      </div>

      <button
        onClick={onTryPlayground}
        className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white"
        style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
      >
        <Play className="w-5 h-5" />
        Try the Playground First
      </button>
      <p className="text-sm text-gray-500 mt-3">
        See AI investigate a sample incident before connecting your tools
      </p>
    </div>
  );
}

// Step 2: Playground Demo
const DEMO_PROMPT =
  "Investigate: High error rate detected in checkout-service. 5xx errors increased 300% in the last 10 minutes.";

function PlaygroundStep({ onNext }: { onNext: () => void }) {
  const [investigating, setInvestigating] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // This used to POST /api/chat {prompt}, a route that does not exist (405), and
  // parse event types (tool_start/tool_end/agent/message) the backend never
  // emits — so every run fell into the catch, which replayed a hardcoded
  // "Root cause: Database connection pool exhaustion after v2.4.1 deploy /
  // Confidence: 87%" script. The first thing a new user saw was an invented
  // root cause presented as a live investigation. It now streams the real
  // /chat/stream SSE endpoint and, when that fails, says so.
  const runDemo = async () => {
    setInvestigating(true);
    setSteps([]);
    setAnswer(null);
    setError(null);

    abortRef.current = new AbortController();

    try {
      await chatStream(
        `onboarding-${Date.now()}`,
        DEMO_PROMPT,
        (ev) => {
          if (ev.type === "trace" && ev.line) {
            setSteps((prev) => [...prev, ev.line as string]);
          } else if (ev.type === "approval") {
            setSteps((prev) => [
              ...prev,
              "Paused — the agent is asking for approval before it acts.",
            ]);
          } else if (ev.type === "done") {
            setSteps((prev) => [...prev, "Investigation complete"]);
            setAnswer(ev.report?.root_cause || ev.answer || null);
          } else if (ev.type === "error") {
            setError(ev.answer || ev.message || "The investigation failed.");
          }
        },
        abortRef.current.signal
      );
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setSteps((prev) => [...prev, "Investigation cancelled"]);
      } else {
        setError(
          `Could not run the investigation: ${(err as Error).message}. ` +
            "The backend has to be reachable and an LLM provider configured."
        );
      }
    } finally {
      setInvestigating(false);
      abortRef.current = null;
    }
  };

  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">See AI Investigation in Action</h2>
      <p className="text-gray-400 mb-6 text-center">
        Watch how OmniPotent investigates a sample incident
      </p>

      <div
        className="rounded-xl border p-4 mb-6 min-h-[300px]"
        style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-2 mb-4 pb-4 border-b" style={{ borderColor: "var(--border)" }}>
          <div className="w-3 h-3 rounded-full bg-red-500" />
          <div className="w-3 h-3 rounded-full bg-yellow-500" />
          <div className="w-3 h-3 rounded-full bg-green-500" />
          <span className="ml-2 text-sm text-gray-500">AI Investigation Console</span>
        </div>

        {!investigating && steps.length === 0 && (
          <div className="text-center py-12">
            <p className="text-gray-500 mb-4">Click below to run a real AI investigation</p>
            <button
              onClick={runDemo}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium"
              style={{ backgroundColor: "var(--accent)", color: "white" }}
            >
              <Play className="w-4 h-4" />
              Run Investigation
            </button>
            <p className="text-xs text-gray-500 mt-2">Connects to your backend API</p>
          </div>
        )}

        {error && (
          <div role="alert" className="text-center py-4 text-red-400 text-sm">
            {error}
          </div>
        )}

        <div className="space-y-2 font-mono text-sm">
          {steps.map((step, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-start gap-2"
            >
              <CheckCircle className="w-4 h-4 mt-0.5 text-green-500 flex-shrink-0" />
              <span style={{ color: step.includes("Root cause") || step.includes("Confidence") ? "var(--accent)" : "var(--text)" }}>
                {step}
              </span>
            </motion.div>
          ))}
          {investigating && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center gap-2 text-gray-500"
            >
              <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
              Investigating...
            </motion.div>
          )}
        </div>

        {!investigating && steps.includes("Investigation complete") && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 p-4 rounded-lg border"
            style={{ backgroundColor: "rgba(34, 197, 94, 0.1)", borderColor: "rgba(34, 197, 94, 0.3)" }}
          >
            <div className="font-semibold text-green-500 mb-2">Investigation Complete</div>
            <div className="text-sm text-gray-300">
              {answer ?? "The agent finished without a written conclusion."}
            </div>
          </motion.div>
        )}
      </div>

      {/* Previously gated on `steps.length === 8` — the length of the canned
          script — so a real investigation left the user with no way forward. */}
      {!investigating && (
        <div className="text-center">
          <button
            onClick={onNext}
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          >
            Connect Your Tools
            <ArrowRight className="w-5 h-5" />
          </button>
        </div>
      )}
    </div>
  );
}

// Step 3: Connect Integrations
//
// Clicking a tile used to push its id into a local `connected` array, which
// drew an accent ring and a green CheckCircle — the same tick this wizard uses
// for a completed step — and counted "4 integrations selected". No request was
// ever sent. A user finished setup believing four tools were wired up while
// /integrations/status still reported every one of them false.
//
// The tiles now report what that endpoint actually says, and the one tool this
// build can genuinely connect from here — GitHub, whose POST /github/connect
// verifies the credential against api.github.com before saving — gets a real
// form. The rest name the secrets they need and say where to set them.

const WIZARD_INTEGRATIONS = [
  { id: "github", name: "GitHub", icon: <Github className="w-6 h-6" />, desc: "Commits, diffs and workflow runs", category: "Code" },
  { id: "datadog", name: "Datadog", icon: <Database className="w-6 h-6" />, desc: "Logs, metrics and anomalies", category: "Observability" },
  { id: "pagerduty", name: "PagerDuty", icon: <Bell className="w-6 h-6" />, desc: "Incidents and on-call", category: "Alerting" },
  { id: "slack", name: "Slack", icon: <MessageSquare className="w-6 h-6" />, desc: "Where findings are posted", category: "Chat" },
];

/** Inline GitHub connect. The one path here that proves the credential works. */
function GithubConnectForm({ onConnected }: { onConnected: () => void }) {
  const [token, setToken] = useState("");
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connect = async () => {
    if (!token.trim() || !repo.trim()) {
      setError("Both a token and a repository are needed.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch("/github/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim(), repo: repo.trim() }),
      });
      // The backend calls api.github.com/repos/{repo} before saving, so a
      // failure here is a real answer about the credential, not a guess.
      if (!res.ok) throw new Error(await describeFailure(res));
      setToken("");
      onConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 space-y-2">
      <div>
        <label className="block text-xs mb-1" htmlFor="gh-token">Personal access token</label>
        <input
          id="gh-token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="ghp_…"
          className="w-full px-3 py-2 rounded-lg border text-sm"
          style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
        />
      </div>
      <div>
        <label className="block text-xs mb-1" htmlFor="gh-repo">Repository</label>
        <input
          id="gh-repo"
          type="text"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="owner/name"
          className="w-full px-3 py-2 rounded-lg border text-sm"
          style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
        />
      </div>
      {error && (
        <div role="alert" className="text-xs text-red-400">{error}</div>
      )}
      <button
        onClick={connect}
        disabled={busy}
        className="text-sm px-3 py-1.5 rounded-lg font-medium disabled:opacity-60"
        style={{ backgroundColor: "var(--accent)", color: "var(--bg)" }}
      >
        {busy ? "Checking…" : "Connect GitHub"}
      </button>
    </div>
  );
}

function IntegrationsStep({ onNext }: { onNext: () => void }) {
  const [status, setStatus] = useState<Record<string, boolean> | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const res = await apiFetch("/integrations/status");
      if (!res.ok) throw new Error(await describeFailure(res));
      setStatus((await res.json()) as Record<string, boolean>);
      setStatusError(null);
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const liveCount = status
    ? WIZARD_INTEGRATIONS.filter(i => {
        const key = specFor(i.id).statusKey;
        return key ? Boolean(status[key]) : false;
      }).length
    : 0;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">Connect Your Tools</h2>
      <p className="text-gray-400 mb-6 text-center">
        These are the tools an investigation can read from. Connect them now or later.
      </p>

      {statusError && (
        <div role="alert" className="text-sm text-red-400 mb-4 text-center">
          Could not read integration status: {statusError}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {WIZARD_INTEGRATIONS.map((integration) => {
          const spec = specFor(integration.id);
          const live = Boolean(spec.statusKey && status?.[spec.statusKey]);
          return (
            <div
              key={integration.id}
              className="p-4 rounded-xl border text-left"
              style={{
                backgroundColor: "var(--shell)",
                borderColor: live ? "var(--accent)" : "var(--border)",
              }}
            >
              <div className="flex items-start gap-3">
                <div
                  className="w-12 h-12 rounded-lg flex items-center justify-center"
                  style={{ backgroundColor: live ? "var(--accent)" : "var(--panel)" }}
                >
                  {integration.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold">{integration.name}</span>
                    <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "var(--panel)" }}>
                      {integration.category}
                    </span>
                  </div>
                  <div className="text-sm text-gray-500">{integration.desc}</div>
                  {/* Only the server's answer earns a tick. Before it arrives
                      nothing is claimed either way. */}
                  <div className="text-xs mt-1" style={{ color: live ? "var(--ok, #22c55e)" : "var(--text-2)" }}>
                    {status === null
                      ? "Checking…"
                      : live
                        ? "Connected — using live data"
                        : "Not connected — investigations use the bundled demo data"}
                  </div>
                </div>
                {live && <CheckCircle className="w-5 h-5 text-green-500" />}
              </div>

              {!live && integration.id === "github" && <GithubConnectForm onConnected={loadStatus} />}

              {!live && integration.id !== "github" && (
                <div className="text-xs mt-3" style={{ color: "var(--text-2)" }}>
                  {spec.unsupported
                    ? spec.unsupported
                    : `Needs ${spec.fields
                        .filter(f => f.required)
                        .map(f => f.env)
                        .join(", ")}. Add them on the Integrations page once setup is done.`}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="text-center">
        <p className="text-sm text-gray-500 mb-4">
          {status === null
            ? "Reading which tools this deployment can reach…"
            : liveCount === 0
              ? "Nothing is connected yet — OmniPotent will investigate against its demo data."
              : `${liveCount} of ${WIZARD_INTEGRATIONS.length} connected.`}
        </p>
        <button
          onClick={onNext}
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        >
          Continue
          <ArrowRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}

// Step 4: Alert notifications
//
// This step used to offer two controls that nothing read. `autoInvestigate` and
// `slackChannel` were plain useState; Continue called onNext() and the component
// unmounted, taking both with it. Neither had anywhere to go even in principle:
// auto-investigation is COPILOT_AUTO_INVESTIGATE, a deployment-level setting
// with no API to read or write it (app/config.py, app/webhooks/router.py), and
// nothing in the backend stores a Slack channel *name* — this build posts to an
// incoming-webhook URL, whose channel is chosen in Slack.
//
// What is real, and is what this step now edits, is the per-event routing the
// notification preferences API persists: the alert_triggered → Slack rule and
// the Slack webhook itself.

interface AlertPrefs {
  /** Whether alert_triggered currently routes to Slack. */
  notifyOnAlert: boolean;
  /** Whether Slack has a webhook stored, per the server. */
  slackConfigured: boolean;
  /** The reason Slack cannot deliver at all, if the server gives one. */
  slackUnavailable: string;
}

function AlertsStep({ onNext }: { onNext: () => void }) {
  const [prefs, setPrefs] = useState<AlertPrefs | null>(null);
  const [notifyOnAlert, setNotifyOnAlert] = useState(false);
  const [webhook, setWebhook] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const res = await apiFetch("/api/notifications/preferences");
        if (!res.ok) throw new Error(await describeFailure(res));
        const body = (await res.json()) as {
          channels?: { id: string; configured?: boolean; unavailable?: string }[];
          rules?: { event: string; channels?: Record<string, boolean> }[];
        };
        const slack = body.channels?.find(c => c.id === "slack");
        const alertRule = body.rules?.find(r => r.event === "alert_triggered");
        const next: AlertPrefs = {
          notifyOnAlert: Boolean(alertRule?.channels?.slack),
          slackConfigured: Boolean(slack?.configured),
          slackUnavailable: slack?.unavailable ?? "",
        };
        if (!cancelled) {
          setPrefs(next);
          setNotifyOnAlert(next.notifyOnAlert);
          setLoadError(null);
        }
      } catch (err) {
        // No defaults invented here: a switch showing "on" that nothing
        // confirmed is the bug this step is being fixed for.
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const saveAndContinue = async () => {
    if (prefs === null) {
      onNext();
      return;
    }
    const trimmedWebhook = webhook.trim();
    const ruleChanged = notifyOnAlert !== prefs.notifyOnAlert;
    if (!ruleChanged && !trimmedWebhook) {
      onNext();
      return;
    }

    setSaving(true);
    setSaveError(null);
    try {
      const body: Record<string, unknown> = {};
      if (trimmedWebhook) {
        // Only the keys sent are written, so this leaves the channel's own
        // enabled flag and every other channel exactly as they were.
        body.channels = [{ id: "slack", enabled: true, config: { webhook: trimmedWebhook } }];
      }
      if (ruleChanged) {
        body.rules = [{ event: "alert_triggered", channels: { slack: notifyOnAlert } }];
      }
      const res = await apiFetch("/api/notifications/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await describeFailure(res));
      onNext();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const willNotDeliver =
    notifyOnAlert && prefs !== null && !prefs.slackConfigured && !webhook.trim();

  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">Alert Notifications</h2>
      <p className="text-gray-400 mb-6 text-center">
        Choose where OmniPotent tells you an alert fired
      </p>

      <div
        className="rounded-xl border p-6 mb-6"
        style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
      >
        {loadError && (
          <div role="alert" className="text-sm text-red-400 mb-4">
            Could not read your notification settings: {loadError}. Nothing on this
            step can be changed until it loads — skip it and set routing up later on
            the Notifications page.
          </div>
        )}

        <div className="flex items-center justify-between mb-6">
          <div>
            <div className="font-semibold">Tell me in Slack when an alert fires</div>
            <div className="text-sm text-gray-500">
              Routes the <span className="font-mono">alert_triggered</span> event to Slack
            </div>
          </div>
          <button
            onClick={() => setNotifyOnAlert(!notifyOnAlert)}
            disabled={prefs === null}
            aria-label="Tell me in Slack when an alert fires"
            aria-pressed={notifyOnAlert}
            className={`w-12 h-6 rounded-full transition-colors disabled:opacity-40 ${notifyOnAlert ? "bg-green-500" : "bg-gray-600"}`}
          >
            <div
              className={`w-5 h-5 rounded-full bg-white transition-transform ${notifyOnAlert ? "translate-x-6" : "translate-x-1"}`}
            />
          </button>
        </div>

        <div className="mb-4">
          <label className="block text-sm font-medium mb-2" htmlFor="slack-webhook">
            Slack incoming-webhook URL
          </label>
          <input
            id="slack-webhook"
            type="url"
            value={webhook}
            onChange={(e) => setWebhook(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border"
            style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
            placeholder="https://hooks.slack.com/services/…"
          />
          <p className="text-xs text-gray-500 mt-1">
            {prefs?.slackConfigured
              ? "Slack is already configured. Leave this blank to keep the stored webhook."
              : "The webhook decides which Slack channel the message lands in — this build has no separate channel setting."}
          </p>
        </div>

        {prefs?.slackUnavailable && (
          <div className="text-sm mb-4" style={{ color: "#eab308" }}>
            {prefs.slackUnavailable}
          </div>
        )}

        {willNotDeliver && (
          <div role="alert" className="text-sm mb-4" style={{ color: "#eab308" }}>
            Slack has no webhook stored, so this routing will not deliver anything
            until one is set.
          </div>
        )}

        <div className="p-4 rounded-lg" style={{ backgroundColor: "var(--panel)" }}>
          <div className="text-sm font-medium mb-2">What happens when an alert arrives:</div>
          <ol className="text-sm text-gray-400 space-y-1">
            <li>1. Your alert manager posts it to the OmniPotent webhook endpoint</li>
            <li>2. An incident is opened, and an investigation starts on its own if the
              deployment has <span className="font-mono">COPILOT_AUTO_INVESTIGATE</span> on
              (the default). That is a server setting, not a console one.</li>
            <li>3. The routing above decides who hears about it</li>
            <li>4. You review the findings and approve any action the agent proposes</li>
          </ol>
        </div>
      </div>

      {saveError && (
        <div role="alert" className="text-sm text-red-400 mb-4 text-center">
          Could not save: {saveError}
        </div>
      )}

      <div className="text-center flex items-center justify-center gap-3">
        <button
          onClick={saveAndContinue}
          disabled={saving}
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white disabled:opacity-60"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        >
          {saving ? "Saving…" : "Save and Continue"}
          <ArrowRight className="w-5 h-5" />
        </button>
        <button
          onClick={onNext}
          className="px-4 py-3 rounded-xl text-sm"
          style={{ color: "var(--text-2)" }}
        >
          Skip
        </button>
      </div>
    </div>
  );
}

// Step 5: Add Knowledge
//
// All four cards used to carry a button with no onClick at all — Import,
// Configure, Add, Add — so a user setting up their runbooks clicked and clicked
// and nothing happened, no request, no error, no "not implemented". The closing
// line then told them they could "add more knowledge later", as though some had
// been added. Each card now writes to the store that actually backs it, and the
// titles say what this build can do: there is no bulk import endpoint anywhere
// in the backend, so nothing here offers one.

/** Which store a card writes to. */
type KnowledgeKind = "runbook" | "service" | "glossary" | "practice";

interface KnowledgeCardSpec {
  kind: KnowledgeKind;
  icon: React.ReactNode;
  title: string;
  desc: string;
  /** First field: a name or a term. */
  primaryLabel: string;
  primaryPlaceholder: string;
  /** Second field. `service` takes a one-line owner; the rest take prose. */
  secondaryLabel: string;
  secondaryPlaceholder: string;
}

const KNOWLEDGE_CARDS: KnowledgeCardSpec[] = [
  {
    kind: "runbook",
    icon: <BookOpen className="w-6 h-6" />,
    title: "Runbooks",
    desc: "Write a procedure the AI can follow during an incident",
    primaryLabel: "Runbook name",
    primaryPlaceholder: "Restart the checkout workers",
    secondaryLabel: "What it does",
    secondaryPlaceholder: "Drain traffic, roll the deployment, wait for readiness.",
  },
  {
    kind: "service",
    icon: <Database className="w-6 h-6" />,
    title: "Service Catalog",
    desc: "Register a service so investigations can attribute findings to it",
    primaryLabel: "Service name",
    primaryPlaceholder: "checkout-service",
    secondaryLabel: "Owning team (optional)",
    secondaryPlaceholder: "payments",
  },
  {
    kind: "glossary",
    icon: <MessageSquare className="w-6 h-6" />,
    title: "Terminology",
    desc: "Explain a term or acronym your team uses",
    primaryLabel: "Term",
    primaryPlaceholder: "CDP",
    secondaryLabel: "What it means",
    secondaryPlaceholder: "Customer Data Platform — the service that owns profile records.",
  },
  {
    kind: "practice",
    icon: <Zap className="w-6 h-6" />,
    title: "Best Practices",
    desc: "Document how your team investigates",
    primaryLabel: "Title",
    primaryPlaceholder: "Always check the deploy timeline first",
    secondaryLabel: "Details",
    secondaryPlaceholder: "Correlate the incident window against Argo CD before reading logs.",
  },
];

/** A service id the graph will accept: lowercase, hyphenated, no spaces. */
function serviceId(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

/** Write one piece of knowledge. Throws with the server's own words on failure. */
async function saveKnowledge(kind: KnowledgeKind, primary: string, secondary: string): Promise<void> {
  const json = { "Content-Type": "application/json" };

  if (kind === "runbook") {
    const res = await apiFetch("/api/runbooks", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ name: primary, description: secondary }),
    });
    if (!res.ok) throw new Error(await describeFailure(res));
    return;
  }

  if (kind === "service") {
    const id = serviceId(primary);
    if (!id) throw new Error("A service name needs at least one letter or digit.");
    const res = await apiFetch("/api/knowledge-graph/services", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ id, name: primary.trim(), team: secondary.trim() }),
    });
    if (!res.ok) throw new Error(await describeFailure(res));
    return;
  }

  // The knowledge base has no glossary type — DocumentType is a closed set of
  // seven — so these are documents tagged for what they are.
  const res = await apiFetch("/api/knowledge/documents", {
    method: "POST",
    headers: json,
    body: JSON.stringify({
      title: primary,
      content: secondary,
      doc_type: "documentation",
      tags: [kind === "glossary" ? "glossary" : "best-practices"],
    }),
  });
  if (!res.ok) throw new Error(await describeFailure(res));
}

function KnowledgeCard({ spec }: { spec: KnowledgeCardSpec }) {
  const [open, setOpen] = useState(false);
  const [primary, setPrimary] = useState("");
  const [secondary, setSecondary] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string[]>([]);

  const submit = async () => {
    if (!primary.trim()) {
      setError(`${spec.primaryLabel} is required.`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await saveKnowledge(spec.kind, primary.trim(), secondary.trim());
      setSaved(prev => [...prev, primary.trim()]);
      setPrimary("");
      setSecondary("");
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="p-4 rounded-xl border"
      style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
    >
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: "var(--panel)" }}>
          {spec.icon}
        </div>
        <div className="flex-1">
          <div className="font-semibold">{spec.title}</div>
          <div className="text-sm text-gray-500 mb-2">{spec.desc}</div>

          {saved.length > 0 && (
            <div className="text-sm text-green-500 mb-2">
              Added {saved.map(s => `"${s}"`).join(", ")}
            </div>
          )}

          {open ? (
            <div className="space-y-2">
              <label className="block text-xs text-gray-400" htmlFor={`${spec.kind}-primary`}>
                {spec.primaryLabel}
              </label>
              <input
                id={`${spec.kind}-primary`}
                value={primary}
                onChange={e => setPrimary(e.target.value)}
                placeholder={spec.primaryPlaceholder}
                className="w-full px-3 py-2 rounded-lg border text-sm"
                style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
              />
              <label className="block text-xs text-gray-400" htmlFor={`${spec.kind}-secondary`}>
                {spec.secondaryLabel}
              </label>
              {spec.kind === "service" ? (
                <input
                  id={`${spec.kind}-secondary`}
                  value={secondary}
                  onChange={e => setSecondary(e.target.value)}
                  placeholder={spec.secondaryPlaceholder}
                  className="w-full px-3 py-2 rounded-lg border text-sm"
                  style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
                />
              ) : (
                <textarea
                  id={`${spec.kind}-secondary`}
                  value={secondary}
                  onChange={e => setSecondary(e.target.value)}
                  placeholder={spec.secondaryPlaceholder}
                  rows={3}
                  className="w-full px-3 py-2 rounded-lg border text-sm"
                  style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
                />
              )}
              {error && (
                <div role="alert" className="text-sm text-red-400">
                  {error}
                </div>
              )}
              <div className="flex items-center gap-2">
                <button
                  onClick={submit}
                  disabled={saving}
                  className="text-sm px-3 py-1 rounded-lg disabled:opacity-50"
                  style={{ backgroundColor: "var(--accent)", color: "white" }}
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={() => {
                    setOpen(false);
                    setError(null);
                  }}
                  className="text-sm px-3 py-1 rounded-lg"
                  style={{ backgroundColor: "var(--panel)", color: "var(--text-2)" }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setOpen(true)}
              className="text-sm px-3 py-1 rounded-lg"
              style={{ backgroundColor: "var(--panel)", color: "var(--accent)" }}
            >
              Add
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function KnowledgeStep({ onComplete }: { onComplete: () => void }) {
  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">Add Team Knowledge</h2>
      <p className="text-gray-400 mb-6 text-center">
        Help the AI understand your systems better (optional)
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {KNOWLEDGE_CARDS.map((spec) => (
          <KnowledgeCard key={spec.kind} spec={spec} />
        ))}
      </div>

      <div className="text-center">
        <motion.button
          onClick={onComplete}
          className="inline-flex items-center gap-2 px-8 py-4 rounded-xl font-semibold text-white text-lg"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <Sparkles className="w-6 h-6" />
          Start Investigating
        </motion.button>
        <p className="text-sm text-gray-500 mt-3">
          You can add more any time from the Knowledge Base and Runbooks pages.
        </p>
      </div>
    </div>
  );
}

export function OnboardingWizard({ onComplete, onSkip }: OnboardingWizardProps) {
  const [currentStep, setCurrentStep] = useState(0);

  const steps: OnboardingStep[] = [
    {
      id: "welcome",
      title: "Welcome",
      description: "Get started",
      icon: <Sparkles className="w-5 h-5" />,
      component: <WelcomeStep onTryPlayground={() => setCurrentStep(1)} />,
    },
    {
      id: "playground",
      title: "Playground",
      description: "Try demo",
      icon: <Play className="w-5 h-5" />,
      component: <PlaygroundStep onNext={() => setCurrentStep(2)} />,
    },
    {
      id: "integrations",
      title: "Integrations",
      description: "Connect tools",
      icon: <Database className="w-5 h-5" />,
      component: <IntegrationsStep onNext={() => setCurrentStep(3)} />,
    },
    {
      id: "alerts",
      title: "Alerts",
      description: "Auto-investigate",
      icon: <Bell className="w-5 h-5" />,
      component: <AlertsStep onNext={() => setCurrentStep(4)} />,
    },
    {
      id: "knowledge",
      title: "Knowledge",
      description: "Team context",
      icon: <BookOpen className="w-5 h-5" />,
      component: <KnowledgeStep onComplete={onComplete} />,
    },
  ];

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ backgroundColor: "var(--bg)" }}>
      <div className="w-full max-w-3xl">
        {/* Progress indicator */}
        <div className="flex items-center justify-between mb-8">
          {steps.map((step, i) => (
            <div key={step.id} className="flex items-center">
              <button
                onClick={() => i < currentStep && setCurrentStep(i)}
                disabled={i > currentStep}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg transition-colors ${
                  i === currentStep
                    ? "bg-white/10"
                    : i < currentStep
                    ? "hover:bg-white/5 cursor-pointer"
                    : "opacity-50 cursor-not-allowed"
                }`}
              >
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center ${
                    i < currentStep
                      ? "bg-green-500"
                      : i === currentStep
                      ? "bg-white/20"
                      : "bg-white/10"
                  }`}
                >
                  {i < currentStep ? <CheckCircle className="w-5 h-5" /> : step.icon}
                </div>
                <div className="hidden md:block text-left">
                  <div className="text-sm font-medium">{step.title}</div>
                  <div className="text-xs text-gray-500">{step.description}</div>
                </div>
              </button>
              {i < steps.length - 1 && (
                <div
                  className="w-8 h-0.5 mx-2"
                  style={{ backgroundColor: i < currentStep ? "var(--accent)" : "var(--border)" }}
                />
              )}
            </div>
          ))}
        </div>

        {/* Step content */}
        <div
          className="rounded-2xl border p-8"
          style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={currentStep}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.3 }}
            >
              {steps[currentStep].component}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between mt-6">
          <button
            onClick={() => currentStep > 0 ? setCurrentStep(currentStep - 1) : onSkip()}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-gray-400 hover:text-white transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            {currentStep > 0 ? "Back" : "Skip Setup"}
          </button>

          <div className="text-sm text-gray-500">
            Step {currentStep + 1} of {steps.length}
          </div>
        </div>
      </div>
    </div>
  );
}
