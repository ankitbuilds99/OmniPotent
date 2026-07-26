import { useState, useEffect } from "react";

import { githubConnect, githubDisconnect } from "../api";
import { refreshConfig, useConfig } from "../hooks/useConfig";

const STORAGE_KEY = "devops-copilot-github";

/** Load saved GitHub credentials from localStorage */
function loadSavedCredentials(): { token: string; repo: string } | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return JSON.parse(saved);
    }
  } catch {
    // Ignore parse errors
  }
  return null;
}

/** Save GitHub credentials to localStorage */
function saveCredentials(token: string, repo: string) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, repo }));
  } catch {
    // Ignore storage errors
  }
}

/** Clear saved credentials */
function clearCredentials() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore storage errors
  }
}

/** Connect-GitHub control inside the sidebar's GitHub card.
 *  Now remembers credentials in localStorage for convenience. */
export function GithubConnect() {
  const { config } = useConfig();
  const gh = config?.github;
  const connected = !!gh?.connected;

  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [repo, setRepo] = useState("");
  const [rememberMe, setRememberMe] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSaved, setHasSaved] = useState(false);

  // Load saved credentials on mount
  useEffect(() => {
    const saved = loadSavedCredentials();
    if (saved) {
      setToken(saved.token);
      setRepo(saved.repo);
      setHasSaved(true);
    }
  }, []);

  // Auto-reconnect with saved credentials when app loads
  useEffect(() => {
    const autoConnect = async () => {
      const saved = loadSavedCredentials();
      if (saved && !connected && saved.token && saved.repo) {
        setBusy(true);
        try {
          await githubConnect(saved.token, saved.repo);
          await refreshConfig();
        } catch {
          // Silent fail for auto-reconnect - user can manually retry
        } finally {
          setBusy(false);
        }
      }
    };
    autoConnect();
  }, []); // Only run once on mount

  const [owner, name] = repo.trim().split("/");
  const repoOk = !!owner && !!name;
  const canSubmit = token.trim().length > 0 && repoOk && !busy;

  const connect = async () => {
    setError(null);
    setBusy(true);
    try {
      await githubConnect(token.trim(), repo.trim());
      await refreshConfig();

      // Save credentials if remember me is checked
      if (rememberMe) {
        saveCredentials(token.trim(), repo.trim());
        setHasSaved(true);
      }

      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setError(null);
    setBusy(true);
    try {
      await githubDisconnect();
      await refreshConfig();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const forgetCredentials = () => {
    clearCredentials();
    setToken("");
    setRepo("");
    setHasSaved(false);
  };

  const switchRepo = () => {
    setOpen(true);
  };

  return (
    <div className="gh">
      <div className="gh__status">
        <span className={`gh__dot ${connected ? "gh__dot--live" : "gh__dot--off"}`} />
        <span className="gh__label" title={gh?.repo ?? undefined}>
          {connected ? gh?.repo : "Offline demo"}
        </span>
        {connected ? (
          <div className="gh__actions">
            <button className="gh__link" onClick={switchRepo}>
              Switch
            </button>
            <button className="gh__link" onClick={disconnect} disabled={busy}>
              Disconnect
            </button>
          </div>
        ) : (
          <button className="gh__link" onClick={() => setOpen((o) => !o)}>
            {open ? "Cancel" : "Connect"}
          </button>
        )}
      </div>

      {open && (
        <div className="gh__form">
          <a
            className="gh__gettoken"
            href="https://github.com/settings/tokens/new?description=DevOps%20Copilot&scopes=repo"
            target="_blank"
            rel="noopener noreferrer"
          >
            → Get a GitHub token (opens GitHub)
          </a>
          <input
            className="gh__input"
            type="password"
            placeholder="Paste token here (ghp_...)"
            aria-label="GitHub access token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <input
            className="gh__input"
            type="text"
            placeholder="owner/repo (e.g. facebook/react)"
            aria-label="GitHub repository"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && canSubmit && connect()}
          />

          <label className="gh__remember">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
            />
            <span>Remember credentials (saves to browser)</span>
          </label>

          {error && (
            <div className="gh__error" role="alert">
              {error}
            </div>
          )}

          <button className="btn btn--approve gh__btn" disabled={!canSubmit} onClick={connect}>
            {busy ? "Connecting…" : "Connect repo"}
          </button>

          {hasSaved && (
            <button className="gh__forget" onClick={forgetCredentials}>
              Forget saved credentials
            </button>
          )}

          <p className="gh__note">
            {rememberMe
              ? "Token saved in browser · auto-connects on reload"
              : "Session only · enter token each time"}
          </p>
        </div>
      )}
      {error && !open && (
        <div className="gh__error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
