import { useEffect, useRef, useState } from "react";

import { health } from "../api";
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

const SUGGESTIONS = [
  { text: "Why is the checkout API throwing 500 errors?", icon: "alert" },
  { text: "What recent deploy caused the failures?", icon: "branch" },
  { text: "Are any pods crashing in Kubernetes?", icon: "server" },
  { text: "Have we seen this incident before?", icon: "search" },
];

const CAPABILITIES = [
  { icon: "search", label: "Log Analysis" },
  { icon: "insight", label: "Root Cause" },
  { icon: "server", label: "K8s & Cloud" },
  { icon: "branch", label: "Git History" },
  { icon: "lock", label: "Safe Actions" },
  { icon: "check", label: "Verification" },
];

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
    <header className="pro-topbar">
      <button className="pro-topbar__brand" onClick={onHome}>
        <span className="pro-topbar__logo">
          <Icon name="sparkles" size={18} />
        </span>
        <span className="pro-topbar__name">DevOps Copilot</span>
      </button>

      <div className="pro-topbar__actions">
        <button className="pro-topbar__btn pro-topbar__btn--primary" onClick={onNew}>
          <Icon name="refresh" size={14} />
          <span>New</span>
        </button>
        <button className="pro-topbar__btn" onClick={onSignup}>
          Sign up
        </button>
        <button className="pro-topbar__btn" onClick={onAdmin}>
          Admin
        </button>

        <div className="pro-topbar__sep" />

        {cfg && (
          <span className="pro-topbar__model">
            {providerLabel(cfg.provider)} · {modelShort(cfg.model)}
          </span>
        )}

        <span className={`pro-topbar__status ${online ? 'online' : ''}`}>
          <span className="pro-topbar__dot" />
          {online === null ? "..." : online ? "Online" : "Offline"}
        </span>

        <ThemePicker />

        <span className="pro-topbar__avatar">{me.label.charAt(0).toUpperCase()}</span>
      </div>
    </header>
  );
}

function AnimatedOrb() {
  return (
    <div className="orb-container">
      <div className="orb orb--1" />
      <div className="orb orb--2" />
      <div className="orb orb--3" />
    </div>
  );
}

export function Console({ onHome }: { onHome: () => void }) {
  const { turns, busy, awaitingApproval, send, respond, stop, newConversation, sendFeedback } =
    useCopilot();
  const endRef = useRef<HTMLDivElement>(null);
  useMe();
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
    <div className="pro-console">
      <a className="skip-link" href="#pro-main">Skip to main content</a>
      <TopBar
        onHome={onHome}
        onNew={newConversation}
        onAdmin={() => setAdminOpen(true)}
        onSignup={() => setSignupOpen(true)}
      />
      {adminOpen && <AdminPanel onClose={() => setAdminOpen(false)} />}
      {signupOpen && <SignupModal onClose={() => setSignupOpen(false)} />}
      <div className="sr-only" role="status" aria-live="polite">{liveStatus}</div>

      <div className="pro-console__body">
        <Sidebar />
        <main className="pro-console__main" id="pro-main">
          {empty ? (
            <div className="pro-console__empty">
              <AnimatedOrb />

              <div className="pro-hero">
                <div className="pro-hero__badge">
                  <Icon name="sparkles" size={12} />
                  AI-Powered Incident Investigation
                </div>

                <h1 className="pro-hero__title">
                  What would you like to<br />
                  <span className="pro-hero__gradient">investigate?</span>
                </h1>

                <p className="pro-hero__sub">
                  Analyze logs, metrics, traces, Kubernetes state, and code to find root causes automatically.
                </p>

                {/* Capabilities strip */}
                <div className="pro-caps">
                  {CAPABILITIES.map((cap) => (
                    <div key={cap.label} className="pro-cap">
                      <Icon name={cap.icon} size={14} />
                      <span>{cap.label}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Suggestion cards */}
              <div className="pro-suggestions">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s.text}
                    className="pro-suggestion"
                    disabled={disabled}
                    onClick={() => send(s.text)}
                  >
                    <span className="pro-suggestion__icon">
                      <Icon name={s.icon} size={16} />
                    </span>
                    <span className="pro-suggestion__text">{s.text}</span>
                    <Icon name="send" size={14} className="pro-suggestion__arrow" />
                  </button>
                ))}
              </div>

              <div className="pro-composer-wrap">
                <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
              </div>

              {/* Stats footer */}
              <div className="pro-stats">
                <div className="pro-stat">
                  <span className="pro-stat__value">9</span>
                  <span className="pro-stat__label">MCP Servers</span>
                </div>
                <div className="pro-stat__sep" />
                <div className="pro-stat">
                  <span className="pro-stat__value">48</span>
                  <span className="pro-stat__label">Tools</span>
                </div>
                <div className="pro-stat__sep" />
                <div className="pro-stat">
                  <span className="pro-stat__value">5</span>
                  <span className="pro-stat__label">LLM Providers</span>
                </div>
                <div className="pro-stat__sep" />
                <div className="pro-stat">
                  <span className="pro-stat__value">379</span>
                  <span className="pro-stat__label">Tests</span>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="pro-console__scroll">
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
              <div className="pro-console__dock">
                <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
