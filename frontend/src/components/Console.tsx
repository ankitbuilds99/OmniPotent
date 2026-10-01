import { useEffect, useRef, useState } from "react";

import { health } from "../api";
import { useCopilot } from "../hooks/useCopilot";
import { modelShort, useConfig } from "../hooks/useConfig";
import { useMe } from "../hooks/useMe";
import { useSupabaseAuth } from "../hooks/useSupabaseAuth";
import { AdminPanel } from "./AdminPanel";
import { SignupModal } from "./SignupModal";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { Message } from "./Message";
import { Sidebar } from "./Sidebar";
import { ThemePicker } from "./ThemePicker";
import { PageBackground } from "./PageBackground";

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
  onDashboard,
  onBilling,
  onLogout,
  onProfile,
  onHistory,
  onStatus,
  onTeam,
  isLoggedIn,
  userAvatar,
}: {
  onHome: () => void;
  onNew: () => void;
  onAdmin: () => void;
  onSignup: () => void;
  onDashboard?: () => void;
  onBilling?: () => void;
  onLogout?: () => void;
  onProfile?: () => void;
  onHistory?: () => void;
  onStatus?: () => void;
  onTeam?: () => void;
  isLoggedIn?: boolean;
  userAvatar?: string | null;
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
        <span className="nav__name">OmniPotent</span>
      </button>

      <div className="nav__right">
        <button className="nav__btn nav__btn--accent" onClick={onNew}>
          <Icon name="refresh" size={14} />
          <span>New</span>
        </button>
        {!isLoggedIn && <button className="nav__btn" onClick={onSignup}>Sign up</button>}
        {onHistory && <button className="nav__btn" onClick={onHistory}>History</button>}
        {onTeam && <button className="nav__btn" onClick={onTeam}>Team</button>}
        {onStatus && <button className="nav__btn" onClick={onStatus}>Status</button>}
        {onDashboard && <button className="nav__btn" onClick={onDashboard}>Dashboard</button>}
        {onBilling && <button className="nav__btn" onClick={onBilling}>Billing</button>}
        <button className="nav__btn" onClick={onAdmin}>Admin</button>
        {isLoggedIn && onLogout && (
          <button className="nav__btn" onClick={onLogout}>
            <Icon name="logout" size={14} />
            <span>Logout</span>
          </button>
        )}
        <div className="nav__sep" />
        {cfg && <span className="nav__tag">{modelShort(cfg.model)}</span>}
        <span className={`nav__status ${online ? 'on' : ''}`}>
          <span className="nav__dot" />
        </span>
        <ThemePicker />
        {isLoggedIn && onProfile ? (
          <button
            className="nav__avatar-btn"
            onClick={onProfile}
            title="View Profile"
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: 0
            }}
          >
            {userAvatar ? (
              <img
                src={userAvatar}
                alt="Profile"
                className="nav__avatar"
                style={{ width: 32, height: 32, borderRadius: '50%' }}
              />
            ) : (
              <span className="nav__avatar">{me.label.charAt(0).toUpperCase()}</span>
            )}
          </button>
        ) : (
          <span className="nav__avatar">{me.label.charAt(0).toUpperCase()}</span>
        )}
      </div>
    </header>
  );
}

export function Console({ onHome, onDashboard, onBilling, onLogout, onProfile, onHistory, onStatus, onTeam }: { onHome: () => void; onDashboard?: () => void; onBilling?: () => void; onLogout?: () => void; onProfile?: () => void; onHistory?: () => void; onStatus?: () => void; onTeam?: () => void }) {
  const {
    turns,
    busy,
    awaitingApproval,
    send,
    respond,
    stop,
    newConversation,
    sendFeedback,
    // WebSocket state - available for future UI enhancements (e.g., connection indicator)
    wsEnabled: _wsEnabled,
    wsAvailable: _wsAvailable,
    wsStatus: _wsStatus,
    toggleWebSocket: _toggleWebSocket,
  } = useCopilot();
  // Suppress unused variable warnings - these are intentionally available for UI features
  void _wsEnabled; void _wsAvailable; void _wsStatus; void _toggleWebSocket;
  const endRef = useRef<HTMLDivElement>(null);
  useMe();
  const { user, signOut } = useSupabaseAuth();
  const [adminOpen, setAdminOpen] = useState(false);
  const [signupOpen, setSignupOpen] = useState(false);
  const disabled = busy || awaitingApproval;

  const handleLogout = async () => {
    await signOut();
    onLogout?.();
  };

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [turns]);

  const empty = turns.length === 0;

  return (
    <div className="app omni-page-shell">
      <PageBackground />
      <TopBar
        onHome={onHome}
        onNew={newConversation}
        onAdmin={() => setAdminOpen(true)}
        onSignup={() => setSignupOpen(true)}
        onDashboard={onDashboard}
        onBilling={onBilling}
        onLogout={handleLogout}
        onProfile={onProfile}
        onHistory={onHistory}
        onStatus={onStatus}
        onTeam={onTeam}
        isLoggedIn={!!user}
        userAvatar={(() => {
          const avatar = user?.user_metadata?.avatar_url || user?.user_metadata?.picture
          return typeof avatar === 'string' ? avatar : null
        })()}
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
                <p className="empty__tip">
                  <strong>Tip:</strong> Click "Change model" in the sidebar to add your own API key
                  (Groq, OpenAI, Anthropic, etc.) for unlimited usage.
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
                <div className="thread" role="log" aria-live="polite" aria-label="Conversation">
                  {busy && <span className="sr-only" aria-live="assertive">Investigation in progress</span>}
                  {awaitingApproval && <span className="sr-only" aria-live="assertive">Awaiting your approval</span>}
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
