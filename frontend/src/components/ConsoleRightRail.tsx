import { useEffect, useState } from "react";

import { getIntegrationStatus, type IntegrationStatus } from "../api";
import { useConfig } from "../hooks/useConfig";
import { Icon } from "./Icon";
import { IntegrationLogo } from "./IntegrationLogo";

const STATUS_SERVERS = ["datadog", "repo", "github", "pagerduty", "kubernetes", "aws", "slack"];
function statusLabel(name: string, status: IntegrationStatus | null, failed: boolean) {
  if (name === "repo") return { label: "Local", tone: "local" };
  if (failed) return { label: "Unknown", tone: "unknown" };
  if (!status) return { label: "Checking", tone: "unknown" };
  if (status[name] === true) return { label: "Live", tone: "live" };
  if (status[name] === false) return { label: "Demo", tone: "demo" };
  return { label: "Unknown", tone: "unknown" };
}

export function ConsoleRightRail({ onHistory }: { onHistory?: () => void }) {
  const { config } = useConfig();
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    getIntegrationStatus()
      .then((value) => active && setStatus(value))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, []);

  const configuredNames = new Set((config?.servers ?? []).map((server) => server.name));
  const names = STATUS_SERVERS.filter((name) => name === "repo" || configuredNames.has(name));
  const current = names.length > 0 ? names : ["repo"];
  const liveCount = current.filter((name) => status?.[name] === true).length;

  return (
    <aside className="console-rail" aria-label="Investigation context">
      <section className="rail-card rail-card--recent">
        <div className="rail-card__head">
          <div>
            <span className="rail-eyebrow">Workspace</span>
            <h2>Recent investigations</h2>
          </div>
          {onHistory && (
            <button className="rail-link" onClick={onHistory} type="button">
              View all <span aria-hidden="true">→</span>
            </button>
          )}
        </div>
        <div className="rail-empty">
          <span className="rail-empty__icon"><Icon name="history" size={18} /></span>
          <strong>Investigation history</strong>
          <p>Your completed investigations will appear here.</p>
          {onHistory && <button className="rail-action" onClick={onHistory} type="button">Open history</button>}
        </div>
      </section>

      <section className="rail-card">
        <div className="rail-card__head">
          <div>
            <span className="rail-eyebrow">Connections</span>
            <h2>System status</h2>
          </div>
          <span className="rail-count">{liveCount}/{current.length} live</span>
        </div>
        <div className="rail-status-list">
          {current.map((name) => {
            const item = statusLabel(name, status, failed);
            const server = config?.servers.find((candidate) => candidate.name === name);
            return (
              <div className="rail-status" key={name}>
                <IntegrationLogo name={name} size={28} />
                <span className="rail-status__name">{server?.label ?? (name === "repo" ? "Repository" : name)}</span>
                <span className={`rail-status__state rail-status__state--${item.tone}`}>
                  <i aria-hidden="true" /> {item.label}
                </span>
              </div>
            );
          })}
        </div>
        {onHistory && <button className="rail-footer-link" onClick={onHistory} type="button">View all integrations <span>→</span></button>}
      </section>
    </aside>
  );
}
