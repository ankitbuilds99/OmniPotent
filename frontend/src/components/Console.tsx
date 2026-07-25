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
  "Why is the checkout API throwing 500 errors?",
  "What recent deploy caused the checkout failures?",
  "Are any pods crashing in Kubernetes?",
  "Have we seen this incident before?",
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

export function Console({ onHome }: { onHome: () => void }) {
  const { turns, busy, awaitingApproval, send, respond, stop, newConversation, sendFeedback } =
    useCopilot();
  const endRef = useRef<HTMLDivElement>(null);
  useMe(); // Keep hook for auth state
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
              <div className="pro-welcome">
                <div className="pro-welcome__icon">
                  <Icon name="sparkles" size={32} />
                </div>
                <h1 className="pro-welcome__title">
                  What would you like to investigate?
                </h1>
                <p className="pro-welcome__sub">
                  DevOps Copilot will analyze logs, metrics, traces, and code to find the root cause.
                </p>
              </div>

              <div className="pro-suggestions">
                {SUGGESTIONS.map((q) => (
                  <button
                    key={q}
                    className="pro-suggestion"
                    disabled={disabled}
                    onClick={() => send(q)}
                  >
                    <Icon name="send" size={14} />
                    <span>{q}</span>
                  </button>
                ))}
              </div>

              <div className="pro-composer-wrap">
                <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
              </div>

              <p className="pro-footer-note">
                9 MCP servers · 48 tools · Human-in-the-loop approval · Multi-tenant
              </p>
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
