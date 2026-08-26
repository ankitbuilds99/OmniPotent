/**
 * On-Call Management Page - Real API integration
 */

import { useState, useEffect, useCallback, useMemo } from "react";
import { motion } from "framer-motion";
import {
  ChevronLeft,
  Phone,
  MessageSquare,
  Users,
  Plus,
  X,
  ChevronRight,
  RefreshCw,
  Loader2
} from "lucide-react";

import { apiFetch } from "../lib/apiClient";

interface OnCallUser {
  id: string;
  name: string;
  email: string;
  avatar?: string;
  phone?: string;
  slack_id?: string;
}

type ShiftRole = "primary" | "secondary" | "shadow" | "escalation";

interface OnCallShift {
  id: string;
  user: OnCallUser;
  role: ShiftRole;
  start_time: string;
  end_time: string;
  status: "active" | "upcoming" | "completed";
}

interface OnCallSchedule {
  /** Real schedule UUID, or "" when the team has no schedule yet. It used to
   *  fall back to the literal "default", which every override POST rejected
   *  with 422 "Input should be a valid UUID". */
  id: string;
  name: string;
  timezone: string;
  rotation_type: "weekly" | "daily" | "custom";
  current_shifts: OnCallShift[];
  participants: OnCallUser[];
}

/** The on-call schema has no display-name column anywhere: `oncall_*.user_id`
 *  FKs to `members`, which holds an email. The email local part is therefore
 *  the only real name available — this used to render "On-Call User" and
 *  "Team Member" for every human on the page. */
function toUser(userId: string, email?: string | null): OnCallUser {
  const mail = email ?? "";
  return {
    id: userId,
    name: mail ? mail.split("@")[0] : `user ${userId.slice(0, 8)}`,
    email: mail,
  };
}

async function errorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.message === "string") return body.message;
  } catch {
    /* not JSON */
  }
  return `${fallback} (HTTP ${res.status})`;
}

function toIsoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
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
    shadow: { bg: "rgba(148, 163, 184, 0.2)", text: "var(--text-2)" },
    escalation: { bg: "rgba(139, 92, 246, 0.2)", text: "#8b5cf6" },
  };
  const c = colors[role] ?? colors.primary;
  return (
    <span
      className="px-2 py-0.5 text-xs font-medium rounded-full"
      style={{ backgroundColor: c.bg, color: c.text }}
    >
      {role}
    </span>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center py-16">
      <Loader2 className="w-8 h-8 animate-spin" style={{ color: "var(--accent)" }} />
      <p className="mt-4 text-sm" style={{ color: "var(--text-2)" }}>Loading on-call data...</p>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16">
      <Users className="w-12 h-12" style={{ color: "var(--text-2)" }} />
      <p className="mt-4 text-lg font-medium" style={{ color: "var(--text)" }}>No Schedules</p>
      <p className="text-sm" style={{ color: "var(--text-2)" }}>Create a schedule to get started</p>
    </div>
  );
}

function CurrentOnCall({ schedule }: { schedule: OnCallSchedule }) {
  const activeShifts = schedule.current_shifts.filter(s => s.status === "active");

  if (activeShifts.length === 0) {
    return (
      <div
        className="rounded-xl p-6"
        style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
      >
        <h3 className="font-semibold text-lg" style={{ color: "var(--text)" }}>
          Who's On-Call Now
        </h3>
        <p className="mt-4 text-sm" style={{ color: "var(--text-2)" }}>No active shifts</p>
      </div>
    );
  }

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

interface RawShift {
  id?: string;
  user_id?: string;
  start_time?: string;
  end_time?: string;
  shift_type?: string;
  status?: string;
}

function ScheduleCalendar({ schedule }: { schedule: OnCallSchedule }) {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const today = new Date();

  const weekDays = useMemo(() => {
    const now = new Date();
    const startOfWeek = new Date(now);
    startOfWeek.setHours(0, 0, 0, 0);
    startOfWeek.setDate(now.getDate() - now.getDay());
    return Array.from({ length: 7 }, (_, i) => {
      const date = new Date(startOfWeek);
      date.setDate(startOfWeek.getDate() + i);
      return date;
    });
  }, []);

  // This grid used to assign `participants[i % participants.length]` to each
  // weekday — an invented rotation that matched nothing in the database. These
  // are the real shift rows for the displayed week.
  const [weekShifts, setWeekShifts] = useState<RawShift[] | null>(null);
  const [shiftsError, setShiftsError] = useState<string | null>(null);

  useEffect(() => {
    if (!schedule.id) {
      setWeekShifts([]);
      return;
    }
    let cancelled = false;
    const from = toIsoDate(weekDays[0]);
    const to = toIsoDate(weekDays[6]);
    (async () => {
      try {
        const res = await apiFetch(
          `/oncall/schedules/${schedule.id}/shifts?start_date=${from}&end_date=${to}`
        );
        if (cancelled) return;
        if (!res.ok) {
          setShiftsError(await errorDetail(res, "Could not load this week's shifts."));
          setWeekShifts([]);
          return;
        }
        setShiftsError(null);
        setWeekShifts(await res.json());
      } catch (err) {
        if (!cancelled) {
          setShiftsError((err as Error).message);
          setWeekShifts([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [schedule.id, weekDays]);

  const userById = new Map(schedule.participants.map((p) => [p.id, p]));
  const shiftOn = (day: Date): OnCallUser | null => {
    const dayStart = new Date(day);
    dayStart.setHours(0, 0, 0, 0);
    const dayEnd = new Date(dayStart);
    dayEnd.setDate(dayStart.getDate() + 1);
    const hit = (weekShifts ?? []).find((s) => {
      if (!s.start_time || !s.end_time) return false;
      if (s.shift_type && s.shift_type !== "primary") return false;
      return new Date(s.start_time) < dayEnd && new Date(s.end_time) > dayStart;
    });
    if (!hit?.user_id) return null;
    return userById.get(String(hit.user_id)) ?? toUser(String(hit.user_id));
  };

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
          const onCallUser = shiftOn(date);

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
              <div className="mt-1 flex justify-center" title={onCallUser?.email || undefined}>
                {onCallUser ? (
                  <UserAvatar user={onCallUser} size="sm" />
                ) : (
                  <span className="text-xs" style={{ color: "var(--text-2)" }}>
                    —
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {shiftsError && (
        <p role="alert" className="mt-4 text-sm" style={{ color: "var(--err, #f87171)" }}>
          {shiftsError}
        </p>
      )}
      {!shiftsError && weekShifts !== null && weekShifts.length === 0 && (
        <p className="mt-4 text-sm" style={{ color: "var(--text-2)" }}>
          No shifts generated for this week.
        </p>
      )}
    </div>
  );
}

function EscalationPolicyCard({ policy }: { policy: EscalationPolicy }) {
  const channelIcons: Record<string, string> = {
    sms: "SMS",
    voice: "Call",
    email: "Email",
    slack: "Slack",
    push: "Push",
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
                      {channelIcons[ch] || ch}
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

function CreateScheduleModal({ onClose, onCreate }: {
  onClose: () => void;
  onCreate: (name: string, timezone: string) => void;
}) {
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("UTC");

  const timezones = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Paris",
    "Asia/Tokyo",
    "Asia/Kolkata",
    "Australia/Sydney",
  ];

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="fixed inset-0 flex items-center justify-center z-50"
      style={{ backgroundColor: "rgba(0, 0, 0, 0.7)" }}
    >
      <motion.div
        initial={{ scale: 0.95 }}
        animate={{ scale: 1 }}
        className="w-full max-w-md p-6 rounded-xl"
        style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
      >
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold" style={{ color: "var(--text)" }}>
            Create New Schedule
          </h2>
          <button onClick={onClose} className="p-1 rounded-lg" style={{ color: "var(--text-2)" }}>
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-2" style={{ color: "var(--text-2)" }}>
              Team Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Platform Engineering"
              className="w-full px-3 py-2 rounded-lg"
              style={{
                backgroundColor: "var(--bg)",
                border: "1px solid var(--border)",
                color: "var(--text)",
              }}
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-2" style={{ color: "var(--text-2)" }}>
              Timezone
            </label>
            <select
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="w-full px-3 py-2 rounded-lg"
              style={{
                backgroundColor: "var(--bg)",
                border: "1px solid var(--border)",
                color: "var(--text)",
              }}
            >
              {timezones.map((tz) => (
                <option key={tz} value={tz}>{tz}</option>
              ))}
            </select>
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
            onClick={() => {
              if (name.trim()) {
                onCreate(name.trim(), timezone);
              }
            }}
            disabled={!name.trim()}
            className="flex-1 px-4 py-2 rounded-lg font-medium"
            style={{
              backgroundColor: name.trim() ? "var(--accent)" : "var(--border)",
              color: name.trim() ? "var(--bg)" : "var(--text-2)",
            }}
          >
            Create Team
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function OverrideModal({ users, onClose, onCreate }: {
  users: OnCallUser[];
  onClose: () => void;
  onCreate: (userId: string, start: string, end: string) => void;
}) {
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
              {users.map((user) => (
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
            onClick={() => {
              if (selectedUser && startDate && endDate) {
                onCreate(selectedUser, startDate, endDate);
              }
            }}
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
  const [schedule, setSchedule] = useState<OnCallSchedule | null>(null);
  const [escalationPolicy, setEscalationPolicy] = useState<EscalationPolicy | null>(null);
  const [users, setUsers] = useState<OnCallUser[]>([]);
  const [showOverrideModal, setShowOverrideModal] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      // Fetch teams
      const teamsRes = await apiFetch(`/oncall/teams`);
      if (!teamsRes.ok) {
        setLoadError(await errorDetail(teamsRes, "Could not load on-call teams."));
        setSchedule(null);
        setEscalationPolicy(null);
        setUsers([]);
        return;
      }
      const teams = await teamsRes.json();

      if (Array.isArray(teams) && teams.length > 0) {
        const teamId = teams[0].id;

        // Fetch current on-call for team
        const oncallRes = await apiFetch(`/oncall/teams/${teamId}/current-oncall`);
        if (!oncallRes.ok) {
          setLoadError(await errorDetail(oncallRes, "Could not load the current on-call."));
          setSchedule(null);
          return;
        }
        const currentOncall = await oncallRes.json();
        setLoadError(null);

        // Fetch escalation policies
        const policiesRes = await apiFetch(`/oncall/teams/${teamId}/escalation-policies`);
        const policies = policiesRes.ok ? await policiesRes.json() : [];

        const participants: OnCallUser[] = (currentOncall.participants ?? []).map(
          (p: Record<string, unknown>) => toUser(String(p.user_id), p.user_email as string | null)
        );

        // `team_schedules` lists every active schedule, including ones nobody
        // is currently on call for; `schedules` only covers the latter. The
        // page needs a real UUID here or the Override modal cannot POST.
        const primarySchedule = (currentOncall.team_schedules ?? [])[0];
        const rotation = String(primarySchedule?.rotation_type ?? "weekly");

        const scheduleData: OnCallSchedule = {
          id: primarySchedule?.id ? String(primarySchedule.id) : "",
          name: primarySchedule?.name || teams[0].name || "On-Call Schedule",
          timezone: primarySchedule?.timezone || teams[0].timezone || "UTC",
          rotation_type:
            rotation === "daily" ? "daily" : rotation === "weekly" ? "weekly" : "custom",
          current_shifts: (currentOncall.shifts ?? []).map((s: Record<string, unknown>) => ({
            id: String(s.id ?? `${s.schedule_id}-${s.user_id}`),
            user: toUser(String(s.user_id ?? ""), s.user_email as string | null),
            role: (s.role as ShiftRole) || "primary",
            start_time: String(s.start_time ?? ""),
            end_time: String(s.end_time ?? ""),
            status: "active" as const,
          })),
          participants,
        };

        setSchedule(scheduleData);
        setUsers(participants);

        // Set escalation policy
        if (policies.length > 0) {
          const policyId = policies[0].id;
          const policyDetailRes = await apiFetch(`/oncall/escalation-policies/${policyId}`);
          if (policyDetailRes.ok) {
            const policyDetail = await policyDetailRes.json();
            setEscalationPolicy({
              id: policyDetail.id,
              name: policyDetail.name,
              steps: policyDetail.steps?.map((s: Record<string, unknown>, idx: number) => ({
                order: idx + 1,
                targets: s.targets || ["on-call"],
                delay_minutes: s.delay_minutes || 0,
                channels: s.notification_channels || ["slack"],
              })) || [],
            });
          }
        } else {
          setEscalationPolicy(null);
        }
      } else {
        // No teams - show empty state
        setLoadError(null);
        setSchedule(null);
        setEscalationPolicy(null);
        setUsers([]);
      }
    } catch (error) {
      setLoadError(`Could not reach the on-call API: ${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleCreateOverride = async (userId: string, start: string, end: string) => {
    if (!schedule) return;
    setActionError(null);
    // Without a real schedule this used to post the literal "default" and the
    // API answered 422 "Input should be a valid UUID" — silently, in a catch
    // that only reached console.error.
    if (!schedule.id) {
      setActionError(
        "This team has no schedule yet, so there is nothing to override. Create a schedule first."
      );
      return;
    }

    try {
      const res = await apiFetch(`/oncall/overrides`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schedule_id: schedule.id,
          override_user_id: userId,
          start_time: new Date(start).toISOString(),
          end_time: new Date(end).toISOString(),
        }),
      });
      if (!res.ok) {
        setActionError(await errorDetail(res, "Could not create the override."));
        return;
      }
      setShowOverrideModal(false);
      fetchData();
    } catch (error) {
      setActionError(`Could not create the override: ${(error as Error).message}`);
    }
  };

  const handleCreateSchedule = async (name: string, timezone: string) => {
    setActionError(null);
    try {
      const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      const res = await apiFetch(`/oncall/teams`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          slug,
          description: `On-call team for ${name}`,
          timezone,
        }),
      });
      if (!res.ok) {
        setActionError(await errorDetail(res, "Could not create the team."));
        return;
      }
      const team = await res.json();
      // A team on its own has no rotation; without a schedule the page can
      // neither show a roster nor accept an override.
      const schedRes = await apiFetch(`/oncall/schedules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_id: team.id,
          name: `${name} primary`,
          timezone,
          rotation_type: "weekly",
          layer: 1,
        }),
      });
      if (!schedRes.ok) {
        setActionError(await errorDetail(schedRes, "Team created, but its schedule was not."));
      }
      setShowCreateModal(false);
      fetchData();
    } catch (error) {
      setActionError(`Could not create the team: ${(error as Error).message}`);
    }
  };

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
            onClick={fetchData}
            className="p-2 rounded-lg mr-2"
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={() => setShowOverrideModal(true)}
            className="px-4 py-2 rounded-lg font-medium"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
            disabled={!schedule?.id}
            title={
              schedule?.id ? undefined : "Create a schedule before overriding it"
            }
          >
            Create Override
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 rounded-lg font-medium flex items-center gap-2"
            style={{ backgroundColor: "var(--accent)", color: "var(--bg)" }}
          >
            <Plus size={16} />
            New Schedule
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">
        {(loadError || actionError) && (
          <div
            role="alert"
            className="mb-6 rounded-xl p-4 text-sm"
            style={{
              backgroundColor: "rgba(248, 113, 113, 0.1)",
              border: "1px solid rgba(248, 113, 113, 0.4)",
              color: "var(--text)",
            }}
          >
            {loadError ?? actionError}
          </div>
        )}
        {loading ? (
          <LoadingState />
        ) : !schedule ? (
          <EmptyState />
        ) : (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="grid grid-cols-1 lg:grid-cols-3 gap-6"
          >
            <div className="lg:col-span-2 space-y-6">
              <CurrentOnCall schedule={schedule} />
              <ScheduleCalendar schedule={schedule} />
            </div>
            <div className="space-y-6">
              {escalationPolicy ? (
                <EscalationPolicyCard policy={escalationPolicy} />
              ) : (
                <div
                  className="rounded-xl p-6"
                  style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
                >
                  <h3 className="font-semibold text-lg mb-4" style={{ color: "var(--text)" }}>
                    Escalation Policy
                  </h3>
                  <p className="text-sm" style={{ color: "var(--text-2)" }}>
                    No escalation policy configured
                  </p>
                </div>
              )}

              {/* Team Members */}
              <div
                className="rounded-xl p-6"
                style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
              >
                <h3 className="font-semibold text-lg mb-4" style={{ color: "var(--text)" }}>
                  Team Members
                </h3>
                {users.length > 0 ? (
                  <div className="space-y-3">
                    {users.map((user) => (
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
                ) : (
                  <p className="text-sm" style={{ color: "var(--text-2)" }}>
                    No team members found
                  </p>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </main>

      {showOverrideModal && (
        <OverrideModal
          users={users}
          onClose={() => setShowOverrideModal(false)}
          onCreate={handleCreateOverride}
        />
      )}

      {showCreateModal && (
        <CreateScheduleModal
          onClose={() => setShowCreateModal(false)}
          onCreate={handleCreateSchedule}
        />
      )}
    </div>
  );
}
