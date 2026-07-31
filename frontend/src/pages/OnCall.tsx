/**
 * On-Call Management Page
 */

import { useState } from "react";
import { motion } from "framer-motion";
import {
  ChevronLeft,
  Phone,
  MessageSquare,
  Users,
  Plus,
  X,
  ChevronRight
} from "lucide-react";

interface OnCallUser {
  id: string;
  name: string;
  email: string;
  avatar?: string;
  phone?: string;
  slack_id?: string;
}

interface OnCallShift {
  id: string;
  user: OnCallUser;
  role: "primary" | "secondary" | "escalation";
  start_time: string;
  end_time: string;
  status: "active" | "upcoming" | "completed";
}

interface OnCallSchedule {
  id: string;
  name: string;
  timezone: string;
  rotation_type: "weekly" | "daily" | "custom";
  current_shifts: OnCallShift[];
  participants: OnCallUser[];
}

interface EscalationStep {
  order: number;
  targets: string[];
  delay_minutes: number;
  channels: ("sms" | "voice" | "email" | "slack" | "push")[];
}

interface EscalationPolicy {
  id: string;
  name: string;
  steps: EscalationStep[];
}

const DEMO_USERS: OnCallUser[] = [
  { id: "u1", name: "Alice Chen", email: "alice@example.com", phone: "+1-555-0101", slack_id: "U001" },
  { id: "u2", name: "Bob Smith", email: "bob@example.com", phone: "+1-555-0102", slack_id: "U002" },
  { id: "u3", name: "Carol Davis", email: "carol@example.com", phone: "+1-555-0103", slack_id: "U003" },
  { id: "u4", name: "David Lee", email: "david@example.com", phone: "+1-555-0104", slack_id: "U004" },
];

const DEMO_SCHEDULE: OnCallSchedule = {
  id: "sched-1",
  name: "Platform Team On-Call",
  timezone: "America/Los_Angeles",
  rotation_type: "weekly",
  current_shifts: [
    {
      id: "shift-1",
      user: DEMO_USERS[0],
      role: "primary",
      start_time: new Date(Date.now() - 86400000).toISOString(),
      end_time: new Date(Date.now() + 518400000).toISOString(),
      status: "active",
    },
    {
      id: "shift-2",
      user: DEMO_USERS[1],
      role: "secondary",
      start_time: new Date(Date.now() - 86400000).toISOString(),
      end_time: new Date(Date.now() + 518400000).toISOString(),
      status: "active",
    },
  ],
  participants: DEMO_USERS,
};

const DEMO_ESCALATION: EscalationPolicy = {
  id: "esc-1",
  name: "Critical Incident Escalation",
  steps: [
    { order: 1, targets: ["primary"], delay_minutes: 0, channels: ["sms", "slack", "push"] },
    { order: 2, targets: ["primary", "secondary"], delay_minutes: 5, channels: ["voice", "sms"] },
    { order: 3, targets: ["escalation", "manager"], delay_minutes: 15, channels: ["voice", "sms", "email"] },
  ],
};

function UserAvatar({ user, size = "md" }: { user: OnCallUser; size?: "sm" | "md" | "lg" }) {
  const sizes = { sm: 32, md: 40, lg: 48 };
  const fontSize = { sm: "0.75rem", md: "0.875rem", lg: "1rem" };
  const initials = user.name.split(" ").map(n => n[0]).join("");

  return (
    <div
      className="rounded-full flex items-center justify-center text-white font-medium"
      style={{
        width: sizes[size],
        height: sizes[size],
        fontSize: fontSize[size],
        background: "linear-gradient(135deg, var(--accent), #8b5cf6)",
      }}
    >
      {initials}
    </div>
  );
}

function RoleBadge({ role }: { role: OnCallShift["role"] }) {
  const colors: Record<string, { bg: string; text: string }> = {
    primary: { bg: "rgba(45, 212, 167, 0.2)", text: "var(--ok)" },
    secondary: { bg: "rgba(100, 145, 255, 0.2)", text: "var(--accent)" },
    escalation: { bg: "rgba(139, 92, 246, 0.2)", text: "#8b5cf6" },
  };
  const c = colors[role];
  return (
    <span
      className="px-2 py-0.5 text-xs font-medium rounded-full"
      style={{ backgroundColor: c.bg, color: c.text }}
    >
      {role}
    </span>
  );
}

function CurrentOnCall({ schedule }: { schedule: OnCallSchedule }) {
  const activeShifts = schedule.current_shifts.filter(s => s.status === "active");

  return (
    <div
      className="rounded-xl p-6"
      style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-lg" style={{ color: "var(--text)" }}>
          Who's On-Call Now
        </h3>
        <span className="text-sm" style={{ color: "var(--text-2)" }}>{schedule.timezone}</span>
      </div>

      <div className="space-y-4">
        {activeShifts.map((shift) => (
          <div
            key={shift.id}
            className="flex items-center justify-between p-4 rounded-lg"
            style={{ backgroundColor: "var(--bg)" }}
          >
            <div className="flex items-center gap-4">
              <div className="relative">
                <UserAvatar user={shift.user} size="lg" />
                <span
                  className="absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2"
                  style={{ backgroundColor: "var(--ok)", borderColor: "var(--surface)" }}
                />
              </div>
              <div>
                <div className="font-medium" style={{ color: "var(--text)" }}>{shift.user.name}</div>
                <div className="text-sm" style={{ color: "var(--text-2)" }}>{shift.user.email}</div>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <RoleBadge role={shift.role} />
              <div className="flex gap-1">
                {shift.user.phone && (
                  <button
                    className="p-2 rounded-lg transition-colors"
                    style={{ color: "var(--text-2)" }}
                    title="Call"
                  >
                    <Phone size={18} />
                  </button>
                )}
                {shift.user.slack_id && (
                  <button
                    className="p-2 rounded-lg transition-colors"
                    style={{ color: "var(--text-2)" }}
                    title="Slack"
                  >
                    <MessageSquare size={18} />
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div
        className="mt-4 pt-4 flex items-center justify-between text-sm"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <span style={{ color: "var(--text-2)" }}>Shift ends</span>
        <span className="font-medium" style={{ color: "var(--text)" }}>
          {activeShifts[0] && new Date(activeShifts[0].end_time).toLocaleDateString(undefined, {
            weekday: "long",
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
          })}
        </span>
      </div>
    </div>
  );
}

function ScheduleCalendar({ schedule }: { schedule: OnCallSchedule }) {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const today = new Date();
  const startOfWeek = new Date(today);
  startOfWeek.setDate(today.getDate() - today.getDay());

  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const date = new Date(startOfWeek);
    date.setDate(startOfWeek.getDate() + i);
    return date;
  });

  return (
    <div
      className="rounded-xl p-6"
      style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-lg" style={{ color: "var(--text)" }}>This Week</h3>
        <div className="flex gap-2">
          <button className="p-1 rounded transition-colors" style={{ color: "var(--text-2)" }}>
            <ChevronLeft size={16} />
          </button>
          <button className="p-1 rounded transition-colors" style={{ color: "var(--text-2)" }}>
            <ChevronRight size={16} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-7 gap-2">
        {days.map((day) => (
          <div
            key={day}
            className="text-center text-xs font-medium py-2"
            style={{ color: "var(--text-2)" }}
          >
            {day}
          </div>
        ))}
        {weekDays.map((date, i) => {
          const isToday = date.toDateString() === today.toDateString();
          const onCallUser = schedule.participants[i % schedule.participants.length];

          return (
            <div
              key={i}
              className="p-2 rounded-lg text-center transition-colors"
              style={{
                backgroundColor: isToday ? "rgba(100, 145, 255, 0.2)" : "transparent",
                border: isToday ? "2px solid var(--accent)" : "none",
              }}
            >
              <div
                className="text-sm font-medium"
                style={{ color: isToday ? "var(--accent)" : "var(--text)" }}
              >
                {date.getDate()}
              </div>
              <div className="mt-1 flex justify-center">
                <UserAvatar user={onCallUser} size="sm" />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EscalationPolicyCard({ policy }: { policy: EscalationPolicy }) {
  const channelIcons: Record<string, string> = {
    sms: "📱",
    voice: "📞",
    email: "📧",
    slack: "💬",
    push: "🔔",
  };

  return (
    <div
      className="rounded-xl p-6"
      style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-lg" style={{ color: "var(--text)" }}>{policy.name}</h3>
        <button className="text-sm" style={{ color: "var(--accent)" }}>Edit</button>
      </div>

      <div className="space-y-4">
        {policy.steps.map((step, i) => (
          <div key={i} className="relative">
            {i < policy.steps.length - 1 && (
              <div
                className="absolute left-4 top-10 bottom-0 w-0.5"
                style={{ backgroundColor: "var(--border)" }}
              />
            )}
            <div className="flex gap-4">
              <div
                className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium flex-shrink-0"
                style={{ backgroundColor: "rgba(100, 145, 255, 0.2)", color: "var(--accent)" }}
              >
                {step.order}
              </div>
              <div className="flex-1 pb-4">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-medium" style={{ color: "var(--text)" }}>
                    Notify {step.targets.join(", ")}
                  </span>
                  {step.delay_minutes > 0 && (
                    <span style={{ color: "var(--text-2)" }}>after {step.delay_minutes}min</span>
                  )}
                </div>
                <div className="flex gap-1 mt-2">
                  {step.channels.map((ch) => (
                    <span
                      key={ch}
                      className="px-2 py-0.5 rounded text-xs"
                      style={{ backgroundColor: "var(--bg)", color: "var(--text-2)" }}
                    >
                      {channelIcons[ch]} {ch}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function OverrideModal({ onClose }: { onClose: () => void }) {
  const [selectedUser, setSelectedUser] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        className="w-full max-w-md rounded-xl p-6"
        style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold" style={{ color: "var(--text)" }}>Create Override</h3>
          <button onClick={onClose} style={{ color: "var(--text-2)" }}>
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1" style={{ color: "var(--text-2)" }}>
              Who will cover?
            </label>
            <select
              value={selectedUser}
              onChange={(e) => setSelectedUser(e.target.value)}
              className="w-full px-3 py-2 rounded-lg"
              style={{
                backgroundColor: "var(--bg)",
                border: "1px solid var(--border)",
                color: "var(--text)",
              }}
            >
              <option value="">Select a person...</option>
              {DEMO_USERS.map((user) => (
                <option key={user.id} value={user.id}>{user.name}</option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: "var(--text-2)" }}>Start</label>
              <input
                type="datetime-local"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full px-3 py-2 rounded-lg"
                style={{
                  backgroundColor: "var(--bg)",
                  border: "1px solid var(--border)",
                  color: "var(--text)",
                }}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: "var(--text-2)" }}>End</label>
              <input
                type="datetime-local"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full px-3 py-2 rounded-lg"
                style={{
                  backgroundColor: "var(--bg)",
                  border: "1px solid var(--border)",
                  color: "var(--text)",
                }}
              />
            </div>
          </div>
        </div>

        <div className="flex gap-3 mt-6">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 rounded-lg font-medium"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            Cancel
          </button>
          <button
            className="flex-1 px-4 py-2 rounded-lg font-medium"
            style={{ backgroundColor: "var(--accent)", color: "var(--bg)" }}
          >
            Create Override
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

export function OnCall({ onBack }: { onBack: () => void }) {
  const [showOverrideModal, setShowOverrideModal] = useState(false);

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
            <Users size={20} style={{ display: "inline", marginRight: "0.5rem", color: "var(--accent)" }} />
            On-Call Management
          </h1>
        </div>
        <div className="nav__right">
          <button
            onClick={() => setShowOverrideModal(true)}
            className="px-4 py-2 rounded-lg font-medium"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            Create Override
          </button>
          <button
            className="px-4 py-2 rounded-lg font-medium flex items-center gap-2"
            style={{ backgroundColor: "var(--accent)", color: "var(--bg)" }}
          >
            <Plus size={16} />
            New Schedule
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-1 lg:grid-cols-3 gap-6"
        >
          <div className="lg:col-span-2 space-y-6">
            <CurrentOnCall schedule={DEMO_SCHEDULE} />
            <ScheduleCalendar schedule={DEMO_SCHEDULE} />
          </div>
          <div className="space-y-6">
            <EscalationPolicyCard policy={DEMO_ESCALATION} />

            {/* Team Members */}
            <div
              className="rounded-xl p-6"
              style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
            >
              <h3 className="font-semibold text-lg mb-4" style={{ color: "var(--text)" }}>
                Team Members
              </h3>
              <div className="space-y-3">
                {DEMO_USERS.map((user) => (
                  <div key={user.id} className="flex items-center gap-3">
                    <UserAvatar user={user} size="sm" />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium truncate" style={{ color: "var(--text)" }}>
                        {user.name}
                      </div>
                      <div className="text-xs truncate" style={{ color: "var(--text-2)" }}>
                        {user.email}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </motion.div>
      </main>

      {showOverrideModal && <OverrideModal onClose={() => setShowOverrideModal(false)} />}
    </div>
  );
}
