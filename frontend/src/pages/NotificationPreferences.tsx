/**
 * Notification Preferences — backed by GET/PUT /api/notifications/preferences.
 *
 * Every channel and rule on this page used to be a literal in this file: nine
 * `ChannelConfig`s with `configured: true` hardcoded on the first two, eight
 * `NotificationRule`s, and a second copy of both inside the catch block. The
 * only network call was `await apiFetch('/oncall/teams')` whose result was
 * discarded, and Save was
 *
 *     const saveConfig = async () => { setSaved(true); setTimeout(...) }
 *
 * — a two-second checkmark that issued no request. Toggling a channel, pasting
 * a Slack webhook or switching off deploy notifications survived exactly until
 * the next reload.
 */

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronLeft,
  Mail,
  MessageSquare,
  Phone,
  Bell,
  Smartphone,
  Save,
  Check,
  AlertTriangle,
  Loader2,
  RefreshCw,
  type LucideIcon
} from "lucide-react";

import { apiFetch } from "../lib/apiClient";

/** Sentinel the API accepts to erase a stored secret. A blank submission means
 *  "leave it alone", because the GET never echoes a secret back. */
const CLEAR_SECRET = "__clear__";

interface FieldSpec {
  key: string;
  label: string;
  type: string;
  secret: boolean;
  required: boolean;
  value: string;
  is_set: boolean;
}

interface ChannelConfig {
  id: string;
  name: string;
  description: string;
  icon: string;
  enabled: boolean;
  configured: boolean;
  /** Why this build cannot deliver on this channel at all; "" when it can.
   *  Distinct from `configured: false`, which the org can go and fix. */
  unavailable: string;
  fields: FieldSpec[];
}

interface NotificationRule {
  event: string;
  description: string;
  /** Why nothing in this build raises this event; "" when something does. */
  unproduced: string;
  channels: Record<string, boolean>;
}

interface QuietHours {
  enabled: boolean;
  start: string;
  end: string;
}

interface Preferences {
  channels: ChannelConfig[];
  rules: NotificationRule[];
  quiet_hours: QuietHours;
  digest_mode: string;
}

const ICONS: Record<string, LucideIcon> = {
  mail: Mail,
  "message-square": MessageSquare,
  phone: Phone,
  smartphone: Smartphone,
  bell: Bell,
  "alert-triangle": AlertTriangle,
};

function iconFor(name: string): LucideIcon {
  return ICONS[name] ?? Bell;
}

/** Read an error body without assuming it is JSON. */
async function errorDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown };
    if (typeof parsed?.detail === "string") return parsed.detail;
    if (typeof parsed?.message === "string") return parsed.message;
  } catch {
    /* not JSON */
  }
  return text.slice(0, 300) || `${res.status} ${res.statusText}`;
}

function ChannelCard({
  channel,
  onToggle,
  onConfigure,
}: {
  channel: ChannelConfig;
  onToggle: () => void;
  onConfigure: () => void;
}) {
  const Icon = iconFor(channel.icon);
  // "Credentials missing" is an instruction: go and fill them in. For a channel
  // this build cannot deliver on there is nothing to fill in — push has no
  // fields at all, so the card offered no Configure button and the yellow
  // warning could never be cleared by anything the reader could do.
  const unavailable = Boolean(channel.unavailable);

  return (
    <motion.div
      className="rounded-lg p-4 flex items-center justify-between"
      style={{
        backgroundColor: "var(--surface)",
        border: "1px solid var(--border)",
        opacity: channel.enabled ? 1 : 0.6,
      }}
      whileHover={{ scale: 1.01 }}
    >
      <div className="flex items-center gap-3">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center"
          style={{
            backgroundColor: channel.enabled ? "var(--accent)" : "var(--bg)",
          }}
        >
          <Icon size={20} color={channel.enabled ? "var(--bg)" : "var(--text-2)"} />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium" style={{ color: "var(--text)" }}>{channel.name}</span>
            {unavailable && (
              <AlertTriangle
                size={14}
                aria-label={`${channel.name} cannot deliver in this build`}
                style={{ color: "var(--text-2)" }}
              />
            )}
            {!unavailable && channel.configured && channel.enabled && (
              <Check size={14} aria-label={`${channel.name} configured`} style={{ color: "var(--ok)" }} />
            )}
            {!unavailable && !channel.configured && channel.enabled && (
              <AlertTriangle
                size={14}
                aria-label={`${channel.name} enabled but not configured`}
                style={{ color: "#eab308" }}
              />
            )}
          </div>
          <span className="text-sm" style={{ color: "var(--text-2)" }}>
            {unavailable
              ? channel.unavailable
              : channel.enabled && !channel.configured
                ? `${channel.description} — credentials missing`
                : channel.description}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3">
        {channel.fields.length > 0 && (
          <button
            onClick={onConfigure}
            className="px-3 py-1.5 text-sm rounded transition-colors"
            style={{ border: "1px solid var(--border)", color: "var(--text-2)" }}
          >
            Configure
          </button>
        )}
        <button
          onClick={onToggle}
          aria-label={`Toggle ${channel.name}`}
          aria-pressed={channel.enabled}
          className="w-12 h-6 rounded-full p-1 transition-colors"
          style={{ backgroundColor: channel.enabled ? "var(--accent)" : "var(--bg)" }}
        >
          <motion.div
            className="w-4 h-4 rounded-full"
            style={{ backgroundColor: "white" }}
            animate={{ x: channel.enabled ? 24 : 0 }}
            transition={{ type: "spring", stiffness: 500, damping: 30 }}
          />
        </button>
      </div>
    </motion.div>
  );
}

function RuleRow({
  rule,
  channels,
  onToggleChannel,
}: {
  rule: NotificationRule;
  channels: ChannelConfig[];
  onToggleChannel: (channelId: string) => void;
}) {
  return (
    <tr style={{ borderBottom: "1px solid var(--border)" }}>
      <td className="py-3 pr-4">
        <div>
          <div className="font-medium" style={{ color: "var(--text)" }}>{rule.event}</div>
          <div className="text-sm" style={{ color: "var(--text-2)" }}>{rule.description}</div>
          {rule.unproduced && (
            <div className="text-xs mt-1" style={{ color: "#eab308" }}>{rule.unproduced}</div>
          )}
        </div>
      </td>
      {channels.filter(c => c.enabled).map((channel) => {
        const isEnabled = rule.channels[channel.id] ?? false;
        // A switch here is an offer to deliver this event on this channel. When
        // either half of that is impossible the offer is withdrawn — but the
        // saved state stays on screen, because "you asked for this and it never
        // arrived" is exactly what the reader came to find out.
        const blocked = rule.unproduced || channel.unavailable || "";

        return (
          <td key={channel.id} className="py-3 px-2 text-center">
            <button
              onClick={() => onToggleChannel(channel.id)}
              disabled={Boolean(blocked)}
              title={blocked || undefined}
              aria-label={`${rule.event} via ${channel.name}`}
              aria-pressed={isEnabled}
              className="w-8 h-8 rounded-lg flex items-center justify-center mx-auto transition-colors"
              style={{
                backgroundColor: isEnabled ? "var(--accent)" : "var(--bg)",
                border: isEnabled ? "none" : "1px solid var(--border)",
                opacity: blocked ? 0.4 : 1,
                cursor: blocked ? "not-allowed" : "pointer",
              }}
            >
              {isEnabled && <Check size={16} color="var(--bg)" />}
            </button>
          </td>
        );
      })}
    </tr>
  );
}

function ConfigModal({
  channel,
  onClose,
  onSave,
}: {
  channel: ChannelConfig;
  onClose: () => void;
  onSave: (fields: Record<string, string>) => Promise<string | null>;
}) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(channel.fields.map(f => [f.key, f.value]))
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    // Only send what the user actually typed. A secret sent as "" means "leave
    // it as it is" server-side, so untouched secret inputs are simply omitted.
    const submitted = Object.fromEntries(
      Object.entries(values).filter(([key, value]) => {
        const spec = channel.fields.find(f => f.key === key);
        if (!spec) return false;
        if (spec.secret) return value.trim() !== "";
        return true;
      })
    );
    const failure = await onSave(submitted);
    setSaving(false);
    if (failure) setError(failure);
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 flex items-center justify-center z-50"
      style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95 }}
        animate={{ scale: 1 }}
        exit={{ scale: 0.95 }}
        className="w-full max-w-md rounded-xl p-6"
        style={{ backgroundColor: "var(--surface)" }}
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold mb-4" style={{ color: "var(--text)" }}>
          Configure {channel.name}
        </h3>

        <div className="space-y-4">
          {channel.fields.map((field) => (
            <div key={field.key}>
              <label className="block text-sm mb-1" style={{ color: "var(--text-2)" }} htmlFor={`cfg-${field.key}`}>
                {field.label}
                {!field.required && " (optional)"}
              </label>
              <input
                id={`cfg-${field.key}`}
                type={field.type}
                value={values[field.key] || ""}
                onChange={e => setValues({ ...values, [field.key]: e.target.value })}
                className="w-full px-3 py-2 rounded-lg"
                style={{
                  backgroundColor: "var(--bg)",
                  border: "1px solid var(--border)",
                  color: "var(--text)",
                }}
                placeholder={
                  field.secret && field.is_set
                    ? "Stored — leave blank to keep it"
                    : `Enter ${field.label.toLowerCase()}`
                }
              />
              {field.secret && field.is_set && (
                <button
                  type="button"
                  className="mt-1 text-xs underline"
                  style={{ color: "var(--text-2)" }}
                  onClick={() => setValues({ ...values, [field.key]: CLEAR_SECRET })}
                >
                  Clear stored value
                </button>
              )}
            </div>
          ))}
        </div>

        {error && (
          <p className="mt-4 text-sm" role="alert" style={{ color: "var(--bad)" }}>
            {error}
          </p>
        )}

        <div className="flex gap-3 mt-6">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 rounded-lg transition-colors"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 px-4 py-2 rounded-lg font-medium flex items-center justify-center gap-2"
            style={{ backgroundColor: "var(--accent)", color: "var(--bg)" }}
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center py-16">
      <Loader2 className="w-8 h-8 animate-spin" style={{ color: "var(--accent)" }} />
      <p className="mt-4 text-sm" style={{ color: "var(--text-2)" }}>Loading preferences...</p>
    </div>
  );
}

export function NotificationPreferences({ onBack }: { onBack: () => void }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [configuring, setConfiguring] = useState<ChannelConfig | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const fetchPreferences = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch("/api/notifications/preferences");
      if (!res.ok) {
        // No literals to fall back on: an unreachable API means we do not know
        // what this org's routing is, and guessing is how the old page lied.
        setLoadError(await errorDetail(res));
        setPrefs(null);
        return;
      }
      setPrefs((await res.json()) as Preferences);
      setLoadError(null);
    } catch (err) {
      setLoadError(`Could not load notification preferences: ${(err as Error).message}`);
      setPrefs(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPreferences();
  }, [fetchPreferences]);

  const toggleChannel = (id: string) => {
    setPrefs(p =>
      p === null
        ? p
        : { ...p, channels: p.channels.map(c => (c.id === id ? { ...c, enabled: !c.enabled } : c)) }
    );
  };

  const toggleRuleChannel = (event: string, channelId: string) => {
    setPrefs(p =>
      p === null
        ? p
        : {
            ...p,
            rules: p.rules.map(r =>
              r.event === event
                ? { ...r, channels: { ...r.channels, [channelId]: !(r.channels[channelId] ?? false) } }
                : r
            ),
          }
    );
  };

  /** PUT a partial document; returns an error message, or null on success. */
  const put = useCallback(async (body: Record<string, unknown>): Promise<string | null> => {
    try {
      const res = await apiFetch("/api/notifications/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) return await errorDetail(res);
      setPrefs((await res.json()) as Preferences);
      return null;
    } catch (err) {
      return `Could not save: ${(err as Error).message}`;
    }
  }, []);

  const saveConfig = async () => {
    if (!prefs) return;
    setSaving(true);
    setSaveError(null);
    const failure = await put({
      channels: prefs.channels.map(c => ({ id: c.id, enabled: c.enabled })),
      rules: prefs.rules.map(r => ({ event: r.event, channels: r.channels })),
      quiet_hours: prefs.quiet_hours,
      digest_mode: prefs.digest_mode,
    });
    setSaving(false);
    if (failure) {
      setSaveError(failure);
      return;
    }
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleConfigSave = async (fields: Record<string, string>): Promise<string | null> => {
    if (!configuring) return null;
    const failure = await put({
      channels: [{ id: configuring.id, enabled: configuring.enabled, config: fields }],
    });
    if (!failure) setConfiguring(null);
    return failure;
  };

  const channels = prefs?.channels ?? [];
  const rules = prefs?.rules ?? [];
  const enabledChannels = channels.filter(c => c.enabled);

  return (
    <div className="min-h-screen" style={{ backgroundColor: "var(--bg)" }}>
      <header className="nav">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="nav__btn"
            style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}
          >
            <ChevronLeft size={18} />
            <span>Back</span>
          </button>
          <h1 className="text-xl font-semibold" style={{ color: "var(--text)" }}>
            <Bell size={20} style={{ display: "inline", marginRight: "0.5rem", color: "var(--accent)" }} />
            Notification Preferences
          </h1>
        </div>
        <div className="nav__right">
          <button
            onClick={fetchPreferences}
            className="p-2 rounded-lg mr-2"
            aria-label="Reload preferences"
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={saveConfig}
            disabled={saving || prefs === null}
            className="px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors"
            style={{
              backgroundColor: saved ? "var(--ok)" : "var(--accent)",
              color: "var(--bg)",
              opacity: prefs === null ? 0.5 : 1,
            }}
          >
            {saving ? (
              <Loader2 size={16} className="animate-spin" />
            ) : saved ? (
              <Check size={16} />
            ) : (
              <Save size={16} />
            )}
            {saving ? "Saving..." : saved ? "Saved!" : "Save Changes"}
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-6 space-y-8">
        {saveError && (
          <div
            className="rounded-lg p-4 text-sm"
            role="alert"
            style={{ border: "1px solid var(--bad)", color: "var(--bad)" }}
          >
            Save failed — {saveError}
          </div>
        )}

        {loading ? (
          <LoadingState />
        ) : loadError || !prefs ? (
          <div
            className="rounded-lg p-6 text-center"
            role="alert"
            style={{ border: "1px solid var(--border)", backgroundColor: "var(--surface)" }}
          >
            <AlertTriangle className="w-8 h-8 mx-auto" style={{ color: "var(--bad)" }} />
            <p className="mt-3 font-medium" style={{ color: "var(--text)" }}>
              Could not load notification preferences
            </p>
            <p className="mt-1 text-sm" style={{ color: "var(--text-2)" }}>
              {loadError ?? "The API returned no preferences."}
            </p>
          </div>
        ) : (
          <>
            {/* Channels */}
            <section>
              <h2 className="text-lg font-semibold mb-4" style={{ color: "var(--text)" }}>
                Notification Channels
              </h2>
              <p className="text-sm mb-4" style={{ color: "var(--text-2)" }}>
                Configure where you want to receive notifications. Enable channels and set up their credentials.
              </p>
              <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                {channels.map((channel) => (
                  <ChannelCard
                    key={channel.id}
                    channel={channel}
                    onToggle={() => toggleChannel(channel.id)}
                    onConfigure={() => setConfiguring(channel)}
                  />
                ))}
              </div>
            </section>

            {/* Rules Matrix */}
            <section>
              <h2 className="text-lg font-semibold mb-4" style={{ color: "var(--text)" }}>
                Notification Rules
              </h2>
              <p className="text-sm mb-4" style={{ color: "var(--text-2)" }}>
                Choose which events trigger notifications on each channel.
              </p>

              <div
                className="rounded-lg overflow-hidden"
                style={{ border: "1px solid var(--border)" }}
              >
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr
                        style={{
                          backgroundColor: "var(--surface)",
                          borderBottom: "1px solid var(--border)",
                        }}
                      >
                        <th className="text-left py-3 px-4 font-medium" style={{ color: "var(--text)" }}>
                          Event
                        </th>
                        {enabledChannels.map((channel) => {
                          const Icon = iconFor(channel.icon);
                          return (
                            <th key={channel.id} className="py-3 px-2 text-center">
                              <div className="flex flex-col items-center gap-1">
                                <Icon size={16} color="var(--text-2)" />
                                <span className="text-xs font-medium" style={{ color: "var(--text-2)" }}>
                                  {channel.name}
                                </span>
                                {channel.unavailable && (
                                  <span
                                    className="text-xs font-normal"
                                    title={channel.unavailable}
                                    style={{ color: "#eab308" }}
                                  >
                                    cannot deliver
                                  </span>
                                )}
                              </div>
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {rules.map((rule) => (
                        <RuleRow
                          key={rule.event}
                          rule={rule}
                          channels={channels}
                          onToggleChannel={(channelId) => toggleRuleChannel(rule.event, channelId)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            {/* Delivery settings */}
            <section>
              <h2 className="text-lg font-semibold mb-4" style={{ color: "var(--text)" }}>
                Delivery Settings
              </h2>
              <div className="grid md:grid-cols-2 gap-4">
                <div
                  className="rounded-lg p-4"
                  style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
                >
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-medium" style={{ color: "var(--text)" }}>Quiet Hours</h3>
                    <label className="flex items-center gap-2 text-sm" style={{ color: "var(--text-2)" }}>
                      <input
                        type="checkbox"
                        checked={prefs.quiet_hours.enabled}
                        aria-label="Enable quiet hours"
                        onChange={e =>
                          setPrefs({
                            ...prefs,
                            quiet_hours: { ...prefs.quiet_hours, enabled: e.target.checked },
                          })
                        }
                      />
                      Enabled
                    </label>
                  </div>
                  <p className="text-sm mb-3" style={{ color: "var(--text-2)" }}>
                    Pause non-critical notifications during specified hours
                  </p>
                  <div className="flex items-center gap-3">
                    <input
                      type="time"
                      aria-label="Quiet hours start"
                      value={prefs.quiet_hours.start}
                      onChange={e =>
                        setPrefs({
                          ...prefs,
                          quiet_hours: { ...prefs.quiet_hours, start: e.target.value },
                        })
                      }
                      className="px-3 py-2 rounded"
                      style={{ backgroundColor: "var(--bg)", border: "1px solid var(--border)", color: "var(--text)" }}
                    />
                    <span style={{ color: "var(--text-2)" }}>to</span>
                    <input
                      type="time"
                      aria-label="Quiet hours end"
                      value={prefs.quiet_hours.end}
                      onChange={e =>
                        setPrefs({
                          ...prefs,
                          quiet_hours: { ...prefs.quiet_hours, end: e.target.value },
                        })
                      }
                      className="px-3 py-2 rounded"
                      style={{ backgroundColor: "var(--bg)", border: "1px solid var(--border)", color: "var(--text)" }}
                    />
                  </div>
                </div>

                <div
                  className="rounded-lg p-4"
                  style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
                >
                  <h3 className="font-medium mb-2" style={{ color: "var(--text)" }}>Digest Mode</h3>
                  <p className="text-sm mb-3" style={{ color: "var(--text-2)" }}>
                    Batch non-critical notifications into periodic digests
                  </p>
                  <select
                    aria-label="Digest mode"
                    value={prefs.digest_mode}
                    onChange={e => setPrefs({ ...prefs, digest_mode: e.target.value })}
                    className="w-full px-3 py-2 rounded"
                    style={{ backgroundColor: "var(--bg)", border: "1px solid var(--border)", color: "var(--text)" }}
                  >
                    <option value="realtime">Real-time (no batching)</option>
                    <option value="hourly">Hourly digest</option>
                    <option value="daily">Daily digest</option>
                  </select>
                </div>
              </div>
            </section>
          </>
        )}
      </main>

      <AnimatePresence>
        {configuring && (
          <ConfigModal
            channel={configuring}
            onClose={() => setConfiguring(null)}
            onSave={handleConfigSave}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
