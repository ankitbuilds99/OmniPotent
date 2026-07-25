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
  "What deploy caused the checkout failures?",
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
    <header className="nav">
      <button className="nav__brand" onClick={onHome}>
        <span className="nav__logo"><Icon name="sparkles" size={16} /></span>
        <span className="nav__name">DevOps Copilot</span>
      </button>

      <div className="nav__right">
        <button className="nav__btn nav__btn--accent" onClick={onNew}>
          <Icon name="refresh" size={14} />
          <span>New</span>
        </button>
        <button className="nav__btn" onClick={onSignup}>Sign up</button>
        <button className="nav__btn" onClick={onAdmin}>Admin</button>
        <div className="nav__sep" />
        {cfg && <span className="nav__tag">{modelShort(cfg.model)}</span>}
        <span className={`nav__status ${online ? 'on' : ''}`}>
          <span className="nav__dot" />
        </span>
        <ThemePicker />
        <span className="nav__avatar">{me.label.charAt(0).toUpperCase()}</span>
      </div>
    </header>
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

  return (
    <div className="app">
      <TopBar
        onHome={onHome}
        onNew={newConversation}
        onAdmin={() => setAdminOpen(true)}
        onSignup={() => setSignupOpen(true)}
      />
      {adminOpen && <AdminPanel onClose={() => setAdminOpen(false)} />}
      {signupOpen && <SignupModal onClose={() => setSignupOpen(false)} />}

      <div className="app__body">
        <Sidebar />
        <main className="main">
          {empty ? (
            <div className="empty">
              <div className="empty__content">
                <div className="empty__icon">
                  <Icon name="sparkles" size={28} />
                </div>
                <h1 className="empty__title">What can I help investigate?</h1>
                <p className="empty__sub">
                  I'll analyze logs, metrics, traces, and code to find the root cause.
                </p>

                <div className="prompts">
                  {SUGGESTIONS.map((text) => (
                    <button
                      key={text}
                      className="prompt"
                      disabled={disabled}
                      onClick={() => send(text)}
                    >
                      {text}
                    </button>
                  ))}
                </div>

                <div className="empty__input">
                  <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="main__scroll">
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
              <div className="main__dock">
                <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
