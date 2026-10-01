import { useEffect, useState } from "react";

import { getIntegrationStatus, type IntegrationStatus } from "../api";
import { providerLabel, useConfig } from "../hooks/useConfig";
import { GithubConnect } from "./GithubConnect";
import { Icon } from "./Icon";
import { ModelConfig } from "./ModelConfig";
import { SourceConnect } from "./SourceConnect";
import { IntegrationLogo } from "./IntegrationLogo";

// Connectors with no live mode to be offline from: the repo card points at a
// checkout on this disk and incident memory is a local table. Everything else
// in the catalog talks to a third party when it has credentials and serves
// bundled fixtures when it does not.
const LOCAL_SERVERS = new Set(["repo", "memory"]);

interface Badge {
  text: string;
  modifier: string;
  title: string;
}

/** What the badge under a server name can honestly say.
 *
 *  It used to be the literal string "connected", for every server, always.
 *  `cfg.servers` is `MCP_CATALOG` -- a static list of the connectors this build
 *  ships, carrying no connection state whatsoever -- so the sidebar reported
 *  eleven live integrations on an instance with none configured. The GitHub row
 *  contradicted itself on screen: "connected" sat directly above that server's
 *  own "Offline demo · Connect" widget.
 *
 *  /integrations/status is the real answer: per connector, whether credentials
 *  are present, which is what decides live mode from fixture mode. */
function badgeFor(name: string, status: IntegrationStatus | null, failed: boolean): Badge {
  if (LOCAL_SERVERS.has(name)) {
    return { text: "local", modifier: "", title: "Reads local state; nothing to connect." };
  }
  if (failed) {
    return { text: "unknown", modifier: "--unknown", title: "Could not read integration status." };
  }
  if (!status) {
    return { text: "checking…", modifier: "--unknown", title: "Reading integration status…" };
  }
  const live = status[name as keyof IntegrationStatus];
  if (live === undefined) {
    // A catalog entry this endpoint says nothing about. Better to admit that
    // than to guess in either direction.
    return { text: "unknown", modifier: "--unknown", title: "No status reported for this connector." };
  }
  return live
    ? { text: "live", modifier: "--live", title: "Credentials configured; querying the real service." }
    : {
        text: "offline demo",
        modifier: "--demo",
        title: "No credentials configured, so this connector serves bundled sample data.",
      };
}

export function Sidebar({ mobileOpen = false, onClose }: { mobileOpen?: boolean; onClose?: () => void }) {
  const { config: cfg, failed } = useConfig();
  const placeholder = failed ? "Backend offline — start the API" : "Connecting…";

  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null);
  const [integrationsFailed, setIntegrationsFailed] = useState(false);
  useEffect(() => {
    let live = true;
    getIntegrationStatus()
      .then((s) => live && setIntegrations(s))
      .catch(() => live && setIntegrationsFailed(true));
    return () => {
      live = false;
    };
  }, []);

  const sourceFor = (name: string) =>
    // "datadog" is the logs/metrics server. This read "logs-metrics", a name
    // that has not been in the catalog since the Datadog connector replaced it,
    // so the logs source picker never rendered and the only way to point the
    // agent at your own logs was gone.
    name === "datadog" ? (
      <SourceConnect kind="logs" path={cfg?.sources.logs_path} />
    ) : name === "repo" ? (
      <SourceConnect kind="repo" path={cfg?.sources.repo_path} />
    ) : name === "github" ? (
      <GithubConnect />
    ) : null;

  return (
    <>
      {mobileOpen && <button className="sidebar__scrim" onClick={onClose} aria-label="Close tools" type="button" />}
    <aside className={`sidebar${mobileOpen ? " sidebar--mobile-open" : ""}`}>
      <div className="sidebar__mobile-head">
        <strong>Workspace tools</strong>
        <button onClick={onClose} aria-label="Close tools" type="button"><Icon name="x" size={17} /></button>
      </div>
      <section className="side-section">
        <h3 className="side-title">Model</h3>
        {cfg ? (
          <div className="model-card">
            <div className="model-card__top">
              <span className="model-card__provider">
                <IntegrationLogo name={cfg.provider} size={28} />
                <span className="provider-pill">{providerLabel(cfg.provider)}</span>
              </span>
              {/* Was a hardcoded "online" -- a status no state could change,
                  shown even with no API key configured at all, which is exactly
                  when the model cannot answer. `has_key` is what decides it. */}
              {cfg.has_key ? (
                <span className="online-pill" title="An API key is configured for this provider.">
                  online
                </span>
              ) : (
                <span
                  className="online-pill online-pill--off"
                  title="No API key is configured, so this provider cannot answer. Use “Change model” to add one."
                >
                  no API key
                </span>
              )}
            </div>
            <div className="model-row">
              <span className="model-row__k">main</span>
              <code className="model-row__v">{cfg.model}</code>
            </div>
            <div className="model-row">
              <span className="model-row__k">fast</span>
              <code className="model-row__v">{cfg.fast_model}</code>
            </div>
          </div>
        ) : (
          <div className={`side-skel${failed ? " side-skel--offline" : ""}`}>{placeholder}</div>
        )}
        <ModelConfig />
      </section>

      <section className="side-section">
        <h3 className="side-title">MCP Servers{cfg ? ` · ${cfg.servers.length}` : ""}</h3>
        <div className="servers">
          {!cfg && <div className="side-skel">{placeholder}</div>}
          {(cfg?.servers ?? []).map((s) => {
            const badge = badgeFor(s.name, integrations, integrationsFailed);
            return (
              <div key={s.name} className="server">
                <div className="server__head">
                  <IntegrationLogo name={s.name} size={28} />
                  <span className="server__name">{s.label}</span>
                  <span
                    className={`server__badge${badge.modifier ? ` server__badge${badge.modifier}` : ""}`}
                    title={badge.title}
                  >
                    {badge.text}
                  </span>
                </div>
                <div className="server__tools">
                  {s.tools.map((t) => (
                    <span key={t} className="tool-chip">
                      {t}
                    </span>
                  ))}
                </div>
                {sourceFor(s.name)}
              </div>
            );
          })}
        </div>
      </section>

      <a
        className="side-help"
        href="https://github.com/ankit25bcs10610/OmniPotent#readme"
        target="_blank"
        rel="noopener noreferrer"
      >
        <div className="side-help__txt">
          <strong>Need help getting started?</strong>
          <span>Check out our documentation</span>
        </div>
        <Icon name="send" size={15} />
      </a>
    </aside>
    </>
  );
}
