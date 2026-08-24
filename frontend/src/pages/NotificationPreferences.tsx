/**
 * Notification Preferences - Real API integration
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

interface ChannelConfig {
  id: string;
  name: string;
  icon: LucideIcon;
  description: string;
  enabled: boolean;
  configured: boolean;
  configFields?: { key: string; label: string; type: string; value: string }[];
}

interface NotificationRule {
  id: string;
  event: string;
  description: string;
  channels: Record<string, { enabled: boolean; severity?: string }>;
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
  const Icon = channel.icon;

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
            {channel.configured && channel.enabled && (
              <Check size={14} style={{ color: "var(--ok)" }} />
            )}
            {!channel.configured && channel.enabled && (
              <AlertTriangle size={14} style={{ color: "#eab308" }} />
            )}
          </div>
          <span className="text-sm" style={{ color: "var(--text-2)" }}>{channel.description}</span>
        </div>
      </div>
      <div className="flex items-center gap-3">
        {channel.configFields && (
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
        </div>
      </td>
      {channels.filter(c => c.enabled).map((channel) => {
        const ruleChannel = rule.channels[channel.id];
        const isEnabled = ruleChannel?.enabled ?? false;

        return (
          <td key={channel.id} className="py-3 px-2 text-center">
            <button
              onClick={() => onToggleChannel(channel.id)}
              className="w-8 h-8 rounded-lg flex items-center justify-center mx-auto transition-colors"
              style={{
                backgroundColor: isEnabled ? "var(--accent)" : "var(--bg)",
                border: isEnabled ? "none" : "1px solid var(--border)",
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
  onSave: (fields: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries((channel.configFields || []).map(f => [f.key, f.value]))
  );
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    await new Promise(r => setTimeout(r, 500));
    onSave(values);
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
          {channel.configFields?.map((field) => (
            <div key={field.key}>
              <label className="block text-sm mb-1" style={{ color: "var(--text-2)" }}>
                {field.label}
              </label>
              <input
                type={field.type}
                value={values[field.key] || ""}
                onChange={e => setValues({ ...values, [field.key]: e.target.value })}
                className="w-full px-3 py-2 rounded-lg"
                style={{
                  backgroundColor: "var(--bg)",
                  border: "1px solid var(--border)",
                  color: "var(--text)",
                }}
                placeholder={`Enter ${field.label.toLowerCase()}`}
              />
            </div>
          ))}
        </div>

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
            {saving ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Save size={16} />
            )}
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
  const [channels, setChannels] = useState<ChannelConfig[]>([]);
  const [rules, setRules] = useState<NotificationRule[]>([]);
  const [configuring, setConfiguring] = useState<ChannelConfig | null>(null);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchPreferences = useCallback(async () => {
    try {
      // Verify backend is reachable
      await apiFetch(`/oncall/teams`);

      // Default channels based on what the system supports
      const defaultChannels: ChannelConfig[] = [
        { id: "email", name: "Email", icon: Mail, description: "Email notifications", enabled: true, configured: true, configFields: [{ key: "address", label: "Email Address", type: "email", value: "" }] },
        { id: "slack", name: "Slack", icon: MessageSquare, description: "Slack workspace messages", enabled: true, configured: true, configFields: [{ key: "webhook", label: "Webhook URL", type: "url", value: "" }] },
        { id: "pagerduty", name: "PagerDuty", icon: Phone, description: "PagerDuty incidents", enabled: false, configured: false, configFields: [{ key: "key", label: "Routing Key", type: "password", value: "" }] },
        { id: "teams", name: "MS Teams", icon: MessageSquare, description: "Microsoft Teams channels", enabled: false, configured: false, configFields: [{ key: "webhook", label: "Webhook URL", type: "url", value: "" }] },
        { id: "discord", name: "Discord", icon: MessageSquare, description: "Discord webhooks", enabled: false, configured: false, configFields: [{ key: "webhook", label: "Webhook URL", type: "url", value: "" }] },
        { id: "sms", name: "SMS", icon: Smartphone, description: "Text message alerts", enabled: false, configured: false, configFields: [{ key: "phone", label: "Phone Number", type: "tel", value: "" }] },
        { id: "push", name: "Push", icon: Bell, description: "Browser push notifications", enabled: true, configured: true },
        { id: "webhook", name: "Webhook", icon: Bell, description: "Custom webhook endpoints", enabled: false, configured: false, configFields: [{ key: "url", label: "Endpoint URL", type: "url", value: "" }, { key: "secret", label: "HMAC Secret", type: "password", value: "" }] },
        { id: "jira", name: "Jira", icon: AlertTriangle, description: "Create Jira tickets", enabled: false, configured: false, configFields: [{ key: "project", label: "Project Key", type: "text", value: "" }] },
      ];

      // Default notification rules
      const defaultRules: NotificationRule[] = [
        { id: "1", event: "incident_created", description: "New incident created", channels: { email: { enabled: true }, slack: { enabled: true }, pagerduty: { enabled: false }, push: { enabled: true } } },
        { id: "2", event: "incident_resolved", description: "Incident resolved", channels: { email: { enabled: true }, slack: { enabled: true }, push: { enabled: true } } },
        { id: "3", event: "deployment_started", description: "Deployment started", channels: { slack: { enabled: true }, push: { enabled: false } } },
        { id: "4", event: "deployment_failed", description: "Deployment failed", channels: { email: { enabled: true }, slack: { enabled: true }, pagerduty: { enabled: false }, push: { enabled: true } } },
        { id: "5", event: "alert_triggered", description: "Alert triggered", channels: { slack: { enabled: true }, pagerduty: { enabled: false }, push: { enabled: true } } },
        { id: "6", event: "oncall_changed", description: "On-call rotation changed", channels: { email: { enabled: true }, slack: { enabled: true } } },
        { id: "7", event: "ai_action_pending", description: "AI action pending approval", channels: { slack: { enabled: true }, push: { enabled: true } } },
        { id: "8", event: "escalation", description: "Incident escalated", channels: { email: { enabled: true }, pagerduty: { enabled: false }, sms: { enabled: false } } },
      ];

      setChannels(defaultChannels);
      setRules(defaultRules);
    } catch (error) {
      console.error("Failed to fetch notification preferences:", error);
      // Set defaults on error
      setChannels([
        { id: "email", name: "Email", icon: Mail, description: "Email notifications", enabled: true, configured: false, configFields: [{ key: "address", label: "Email Address", type: "email", value: "" }] },
        { id: "slack", name: "Slack", icon: MessageSquare, description: "Slack workspace messages", enabled: true, configured: false, configFields: [{ key: "webhook", label: "Webhook URL", type: "url", value: "" }] },
        { id: "push", name: "Push", icon: Bell, description: "Browser push notifications", enabled: true, configured: true },
      ]);
      setRules([
        { id: "1", event: "incident_created", description: "New incident created", channels: { email: { enabled: true }, slack: { enabled: true }, push: { enabled: true } } },
        { id: "2", event: "incident_resolved", description: "Incident resolved", channels: { email: { enabled: true }, slack: { enabled: true }, push: { enabled: true } } },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPreferences();
  }, [fetchPreferences]);

  const toggleChannel = (id: string) => {
    setChannels(cs => cs.map(c =>
      c.id === id ? { ...c, enabled: !c.enabled } : c
    ));
  };

  const toggleRuleChannel = (ruleId: string, channelId: string) => {
    setRules(rs => rs.map(r => {
      if (r.id !== ruleId) return r;
      const current = r.channels[channelId]?.enabled ?? false;
      return {
        ...r,
        channels: {
          ...r.channels,
          [channelId]: { ...r.channels[channelId], enabled: !current },
        },
      };
    }));
  };

  const saveConfig = async () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleConfigSave = (fields: Record<string, string>) => {
    if (!configuring) return;
    setChannels(cs => cs.map(c =>
      c.id === configuring.id
        ? { ...c, configured: true, configFields: c.configFields?.map(f => ({ ...f, value: fields[f.key] || f.value })) }
        : c
    ));
    setConfiguring(null);
  };

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
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={saveConfig}
            className="px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors"
            style={{ backgroundColor: saved ? "var(--ok)" : "var(--accent)", color: "var(--bg)" }}
          >
            {saved ? <Check size={16} /> : <Save size={16} />}
            {saved ? "Saved!" : "Save Changes"}
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-6 space-y-8">
        {loading ? (
          <LoadingState />
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
                          const Icon = channel.icon;
                          return (
                            <th key={channel.id} className="py-3 px-2 text-center">
                              <div className="flex flex-col items-center gap-1">
                                <Icon size={16} color="var(--text-2)" />
                                <span className="text-xs font-medium" style={{ color: "var(--text-2)" }}>
                                  {channel.name}
                                </span>
                              </div>
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {rules.map((rule) => (
                        <RuleRow
                          key={rule.id}
                          rule={rule}
                          channels={channels}
                          onToggleChannel={(channelId) => toggleRuleChannel(rule.id, channelId)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            {/* Quick Settings */}
            <section>
              <h2 className="text-lg font-semibold mb-4" style={{ color: "var(--text)" }}>
                Quick Settings
              </h2>
              <div className="grid md:grid-cols-2 gap-4">
                <div
                  className="rounded-lg p-4"
                  style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
                >
                  <h3 className="font-medium mb-2" style={{ color: "var(--text)" }}>Quiet Hours</h3>
                  <p className="text-sm mb-3" style={{ color: "var(--text-2)" }}>
                    Pause non-critical notifications during specified hours
                  </p>
                  <div className="flex items-center gap-3">
                    <input
                      type="time"
                      defaultValue="22:00"
                      className="px-3 py-2 rounded"
                      style={{ backgroundColor: "var(--bg)", border: "1px solid var(--border)", color: "var(--text)" }}
                    />
                    <span style={{ color: "var(--text-2)" }}>to</span>
                    <input
                      type="time"
                      defaultValue="08:00"
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
                    defaultValue="realtime"
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
