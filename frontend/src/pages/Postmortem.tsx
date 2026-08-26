/**
 * Postmortem page.
 *
 * This page used to build the entire document in the browser. Two of its four
 * sections were string constants — Root Cause Analysis was always "The incident
 * was caused by an issue that requires further investigation…" and Lessons
 * Learned was always the same What Went Well / What Could Be Improved block —
 * as were both action items, and every one of them was rendered under an "AI
 * Generated" badge. `regenerateSection` awaited a 2000ms timer and changed
 * nothing. No postmortem route existed to call.
 *
 * It now POSTs `/api/incidents/{id}/postmortem`, which reads the incident's real
 * timeline and durations off the record and has the configured model write the
 * prose from exactly those facts. When no model is configured the server answers
 * 503 and this page shows that, because the previous behaviour — printing a
 * canned root cause under an "AI Generated" badge — is the thing being fixed.
 */

import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";

import { apiFetch } from "../lib/apiClient";

interface PostmortemSection {
  id: string;
  title: string;
  content: string;
  /** "model" while it is the generated text; "edited" once a human changes it. */
  source: "model" | "edited";
  editing: boolean;
}

interface ActionItem {
  id: string;
  title: string;
  owner: string;
  priority: "P0" | "P1" | "P2" | "P3";
  status: "open" | "in_progress" | "completed";
  /** null unless a date was actually agreed — the page used to stamp now+7d. */
  due_date: string | null;
  type: "prevent" | "detect" | "mitigate";
}

interface TimelineEvent {
  timestamp: string;
  event: string;
  actor?: string | null;
  type: string;
}

/** Minutes, or null where the record has no second endpoint to measure against.
 *  The old page coerced these to 0 and printed "0m", which reads as "instant". */
interface PostmortemMetrics {
  time_to_detect: number | null;
  time_to_acknowledge: number | null;
  time_to_mitigate: number | null;
  time_to_resolve: number | null;
  affected_services: string[];
  customer_impact: boolean;
  impact: string | null;
}

interface PostmortemData {
  id: string;
  incident_id: string;
  title: string;
  status: "draft" | "in_review" | "published";
  severity: "SEV1" | "SEV2" | "SEV3" | "SEV4";
  generated_at: string;
  authors: string[];
  reviewers: string[];
  sections: PostmortemSection[];
  action_items: ActionItem[];
  timeline: TimelineEvent[];
  metrics: PostmortemMetrics;
}

/** Pull a human-readable reason out of an error response of any shape. */
async function errorDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text);
    const detail = body?.detail ?? body;
    if (typeof detail === "string") return detail;
    if (detail?.message) return String(detail.message);
  } catch {
    // Not JSON — fall through to the raw body.
  }
  return text.trim() || `${res.status} ${res.statusText}`;
}

function SeverityBadge({ severity }: { severity: PostmortemData["severity"] }) {
  const config = {
    SEV1: "bg-red-500 text-white",
    SEV2: "bg-orange-500 text-white",
    SEV3: "bg-yellow-500 text-black",
    SEV4: "bg-blue-500 text-white",
  };
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded ${config[severity]}`}>
      {severity}
    </span>
  );
}

function StatusBadge({ status }: { status: PostmortemData["status"] }) {
  const config = {
    draft: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
    in_review: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/50 dark:text-yellow-300",
    published: "bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300",
  };
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded ${config[status]}`}>
      {status.replace("_", " ")}
    </span>
  );
}

/** One duration. A null value renders "—", not "0m": the record has no second
 *  timestamp to measure against, and 0 would be a claim that it was instant. */
function Duration({
  label,
  minutes,
  color,
  unmeasured,
}: {
  label: string;
  minutes: number | null;
  color: string;
  unmeasured: string;
}) {
  return (
    <div>
      <div className="text-xs text-gray-500 uppercase">{label}</div>
      {minutes === null ? (
        <div className="text-2xl font-bold text-gray-400" title={unmeasured}>
          —
        </div>
      ) : (
        <div className={`text-2xl font-bold ${color}`}>{minutes}m</div>
      )}
    </div>
  );
}

function MetricsCard({ metrics }: { metrics: PostmortemMetrics }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">Incident Metrics</h3>
      <div className="grid grid-cols-2 gap-4">
        <Duration
          label="Time to Detect"
          minutes={metrics.time_to_detect}
          color="text-blue-600"
          unmeasured="Not measurable: the record holds when the incident was declared, not when the fault began."
        />
        <Duration
          label="Time to Acknowledge"
          minutes={metrics.time_to_acknowledge}
          color="text-green-600"
          unmeasured="Never acknowledged, so there is no interval to measure."
        />
        <Duration
          label="Time to Mitigate"
          minutes={metrics.time_to_mitigate}
          color="text-yellow-600"
          unmeasured="No move to 'monitoring' was recorded, so mitigation has no timestamp."
        />
        <Duration
          label="Time to Resolve"
          minutes={metrics.time_to_resolve}
          color="text-red-600"
          unmeasured="Still open, so there is no resolution time yet."
        />
      </div>
      <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
        <div className="text-xs text-gray-500 uppercase mb-2">Affected Services</div>
        <div className="flex flex-wrap gap-1">
          {metrics.affected_services.length === 0 ? (
            <span className="text-xs text-gray-500">None recorded</span>
          ) : (
            metrics.affected_services.map((svc) => (
              <span key={svc} className="px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs">
                {svc}
              </span>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function TimelineView({ events }: { events: TimelineEvent[] }) {
  const typeColors: Record<string, string> = {
    detection: "bg-blue-500",
    action: "bg-yellow-500",
    communication: "bg-purple-500",
    resolution: "bg-green-500",
  };

  if (events.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <h3 className="font-semibold mb-4">Timeline</h3>
        <div className="text-center text-gray-500 py-8">No timeline events</div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">Timeline</h3>
      <div className="space-y-3">
        {events.map((event, i) => (
          <div key={i} className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className={`w-3 h-3 rounded-full ${typeColors[event.type] ?? "bg-gray-400"}`} />
              {i < events.length - 1 && <div className="w-0.5 flex-1 bg-gray-200 dark:bg-gray-700 my-1" />}
            </div>
            <div className="flex-1 pb-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500">
                  {new Date(event.timestamp).toLocaleTimeString()}
                </span>
                {event.actor && (
                  <span className="text-xs bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded">
                    {event.actor}
                  </span>
                )}
              </div>
              <div className="text-sm mt-0.5">{event.event}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ActionItemsTable({ items, onUpdate }: { items: ActionItem[]; onUpdate: (id: string, status: ActionItem["status"]) => void }) {
  const priorityColors = {
    P0: "bg-red-500 text-white",
    P1: "bg-orange-500 text-white",
    P2: "bg-yellow-500 text-black",
    P3: "bg-blue-500 text-white",
  };

  const statusColors = {
    open: "text-gray-500",
    in_progress: "text-blue-600",
    completed: "text-green-600",
  };

  if (items.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 className="font-semibold">Action Items</h3>
          <button className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
            + Add Item
          </button>
        </div>
        <div className="text-center text-gray-500 py-8">No action items yet</div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <h3 className="font-semibold">Action Items</h3>
        <button className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
          + Add Item
        </button>
      </div>
      <table className="w-full">
        <thead className="bg-gray-50 dark:bg-gray-900">
          <tr>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Owner</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Priority</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Due</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
          {items.map((item) => (
            <tr key={item.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
              <td className="px-4 py-3 text-sm">{item.title}</td>
              <td className="px-4 py-3 text-sm">
                {item.owner || <span className="text-gray-400">Unassigned</span>}
              </td>
              <td className="px-4 py-3">
                <span className={`px-2 py-0.5 text-xs font-medium rounded ${priorityColors[item.priority]}`}>
                  {item.priority}
                </span>
              </td>
              <td className="px-4 py-3 text-sm text-gray-500">
                {item.due_date ? new Date(item.due_date).toLocaleDateString() : "Not set"}
              </td>
              <td className="px-4 py-3">
                <select
                  value={item.status}
                  onChange={(e) => onUpdate(item.id, e.target.value as ActionItem["status"])}
                  className={`text-sm border-0 bg-transparent cursor-pointer ${statusColors[item.status]}`}
                >
                  <option value="open">Open</option>
                  <option value="in_progress">In Progress</option>
                  <option value="completed">Completed</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SectionEditor({ section, onSave, onCancel }: {
  section: PostmortemSection;
  onSave: (content: string) => void;
  onCancel: () => void;
}) {
  const [content, setContent] = useState(section.content);

  return (
    <div className="space-y-3">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        className="w-full h-64 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 font-mono text-sm"
      />
      <div className="flex justify-end gap-2">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
        >
          Cancel
        </button>
        <button
          onClick={() => onSave(content)}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          Save
        </button>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex items-center justify-center py-16">
      <div className="animate-spin w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full" />
    </div>
  );
}

function EmptyState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-500">
      <Icon name="file-text" size={48} />
      <p className="mt-4 text-lg font-medium">No Postmortem</p>
      <p className="text-sm max-w-xl text-center">{message}</p>
      <button
        onClick={onRetry}
        className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
      >
        Try Again
      </button>
    </div>
  );
}

export function Postmortem({ onBack }: { onBack: () => void }) {
  const [postmortem, setPostmortem] = useState<PostmortemData | null>(null);
  const [loading, setLoading] = useState(true);
  /** The id of the section currently being regenerated, or null. */
  const [generating, setGenerating] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"content" | "timeline" | "actions">("content");

  /** A server document into page state. `editing` is presentation-only and is
   *  not part of the API shape, so it is added here rather than expected back. */
  const applyDocument = useCallback((doc: PostmortemData) => {
    setPostmortem({
      ...doc,
      action_items: doc.action_items ?? [],
      sections: (doc.sections ?? []).map((s: PostmortemSection) => ({ ...s, editing: false })),
    });
  }, []);

  const fetchPostmortem = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const incidentsRes = await apiFetch("/api/incidents");
      if (!incidentsRes.ok) {
        setPostmortem(null);
        setError(`Could not load incidents: ${await errorDetail(incidentsRes)}`);
        return;
      }
      const incidents = await incidentsRes.json();
      // Prefer a resolved incident — a postmortem of something still burning is
      // premature — but fall back to the most recent one.
      const incident =
        incidents.find((i: Record<string, unknown>) => i.status === "resolved") ?? incidents[0];
      if (!incident) {
        setPostmortem(null);
        setError("There are no incidents to write a postmortem about yet.");
        return;
      }

      // Read the stored document first. This page used to POST unconditionally,
      // which re-ran the model on every visit — a fresh draft each time, and any
      // edit an operator had made was overwritten by a document that had never
      // seen it. 404 here means nobody has written one yet, so generate.
      const stored = await apiFetch(`/api/incidents/${incident.id}/postmortem`);
      if (stored.ok) {
        applyDocument(await stored.json());
        return;
      }
      if (stored.status !== 404) {
        setPostmortem(null);
        setError(await errorDetail(stored));
        return;
      }

      // The server assembles the document: real timeline and durations off the
      // incident record, prose written by the configured model from those facts.
      const res = await apiFetch(`/api/incidents/${incident.id}/postmortem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        // 503 here means no model is configured. Say so — the version of this
        // page that printed a canned root cause instead is what we are fixing.
        setPostmortem(null);
        setError(await errorDetail(res));
        return;
      }
      applyDocument(await res.json());
    } catch (err) {
      setPostmortem(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [applyDocument]);

  useEffect(() => {
    fetchPostmortem();
  }, [fetchPostmortem]);

  const toggleEdit = (sectionId: string) => {
    if (!postmortem) return;
    setPostmortem({
      ...postmortem,
      sections: postmortem.sections.map(s =>
        s.id === sectionId ? { ...s, editing: !s.editing } : s
      ),
    });
  };

  // Both of these used to be `setPostmortem(...)` and nothing more. The Save
  // button on a section and the status dropdown on an action item wrote to
  // component state, so the rewrite of a root cause — the one part of the
  // document a human actually authored — and every action item marked done were
  // discarded by the next reload. They now PATCH the stored document, and the
  // server's copy is what re-renders.
  const saveSection = async (sectionId: string, content: string) => {
    if (!postmortem) return;
    const previous = postmortem;
    // Optimistic: the editor closes immediately, and a failure restores it with
    // the operator's text intact rather than silently dropping what they wrote.
    setPostmortem({
      ...postmortem,
      sections: postmortem.sections.map(s =>
        s.id === sectionId ? { ...s, content, editing: false, source: "edited" } : s
      ),
    });
    setError(null);
    try {
      const res = await apiFetch(
        `/api/incidents/${postmortem.incident_id}/postmortem/sections/${sectionId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        }
      );
      if (!res.ok) throw new Error(await errorDetail(res));
      applyDocument(await res.json());
    } catch (err) {
      setPostmortem({
        ...previous,
        sections: previous.sections.map(s =>
          s.id === sectionId ? { ...s, content, editing: true } : s
        ),
      });
      setError(`Could not save this section: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const updateActionItem = async (itemId: string, status: ActionItem["status"]) => {
    if (!postmortem) return;
    const previous = postmortem;
    setPostmortem({
      ...postmortem,
      action_items: postmortem.action_items.map(item =>
        item.id === itemId ? { ...item, status } : item
      ),
    });
    setError(null);
    try {
      const res = await apiFetch(
        `/api/incidents/${postmortem.incident_id}/postmortem/action-items/${itemId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status }),
        }
      );
      if (!res.ok) throw new Error(await errorDetail(res));
      applyDocument(await res.json());
    } catch (err) {
      setPostmortem(previous);
      setError(`Could not update this action item: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  /** Re-run the model for one section. This used to be a 2000ms sleep. */
  const regenerateSection = async (sectionId: string) => {
    if (!postmortem) return;
    setGenerating(sectionId);
    setError(null);
    try {
      const res = await apiFetch(`/api/incidents/${postmortem.incident_id}/postmortem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sections: [sectionId] }),
      });
      if (!res.ok) {
        setError(`Could not regenerate this section: ${await errorDetail(res)}`);
        return;
      }
      const doc = await res.json();
      const fresh = (doc.sections ?? []).find(
        (s: PostmortemSection) => s.id === sectionId
      );
      if (!fresh) {
        setError(`The server returned no "${sectionId}" section.`);
        return;
      }
      setPostmortem(prev =>
        prev
          ? {
              ...prev,
              sections: prev.sections.map(s =>
                s.id === sectionId
                  ? { ...s, content: fresh.content, source: "model", editing: false }
                  : s
              ),
            }
          : prev
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGenerating(null);
    }
  };

  const exportMarkdown = () => {
    if (!postmortem) return;
    const md = postmortem.sections.map(s => `## ${s.title}\n\n${s.content}`).join("\n\n");
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `postmortem-${postmortem.incident_id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                <Icon name="arrow-left" size={20} />
              </button>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-lg font-semibold">Postmortem</h1>
                  {postmortem && (
                    <>
                      <SeverityBadge severity={postmortem.severity} />
                      <StatusBadge status={postmortem.status} />
                    </>
                  )}
                </div>
                {postmortem && (
                  <div className="text-sm text-gray-500">{postmortem.incident_id}</div>
                )}
              </div>
            </div>
            {postmortem && (
              <div className="flex items-center gap-2">
                <button
                  onClick={exportMarkdown}
                  className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 flex items-center gap-1"
                >
                  <Icon name="download" size={14} />
                  Export
                </button>
                <button className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700">
                  Publish
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {loading ? (
          <LoadingState />
        ) : !postmortem ? (
          <EmptyState
            message={error ?? "There are no incidents to write a postmortem about yet."}
            onRetry={fetchPostmortem}
          />
        ) : (
          <>
            {error && (
              <div
                role="alert"
                className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300"
              >
                {error}
              </div>
            )}
            <div className="mb-6">
              <h2 className="text-2xl font-bold">{postmortem.title}</h2>
              <div className="flex items-center gap-4 mt-2 text-sm text-gray-500">
                <span>
                  Authors:{" "}
                  {postmortem.authors.length ? postmortem.authors.join(", ") : "none recorded"}
                </span>
                <span>
                  Reviewers:{" "}
                  {postmortem.reviewers.length
                    ? postmortem.reviewers.join(", ")
                    : "not yet reviewed"}
                </span>
              </div>
            </div>

            <div className="flex gap-2 mb-6 border-b border-gray-200 dark:border-gray-700">
              {(["content", "timeline", "actions"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                    activeTab === tab
                      ? "border-blue-500 text-blue-600"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>

            {activeTab === "content" && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                  {postmortem.sections.map((section) => (
                    <div
                      key={section.id}
                      className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4"
                    >
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="font-semibold flex items-center gap-2">
                          {section.title}
                          {section.source === "model" ? (
                            <span className="text-xs bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-300 px-1.5 py-0.5 rounded">
                              AI Generated
                            </span>
                          ) : (
                            <span className="text-xs bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300 px-1.5 py-0.5 rounded">
                              Edited
                            </span>
                          )}
                        </h3>
                        <div className="flex gap-1">
                          <button
                            onClick={() => regenerateSection(section.id)}
                            disabled={generating !== null}
                            className="p-1.5 text-gray-400 hover:text-purple-600 rounded disabled:opacity-50"
                            title="Regenerate with AI"
                            aria-label={`Regenerate ${section.title}`}
                          >
                            <Icon
                              name="refresh-cw"
                              size={14}
                              className={generating === section.id ? "animate-spin" : undefined}
                            />
                          </button>
                          <button
                            onClick={() => toggleEdit(section.id)}
                            className="p-1.5 text-gray-400 hover:text-blue-600 rounded"
                            title={section.editing ? "Stop editing" : "Edit"}
                            aria-label={`${section.editing ? "Stop editing" : "Edit"} ${section.title}`}
                          >
                            <Icon name="edit-2" size={14} />
                          </button>
                        </div>
                      </div>

                      {section.editing ? (
                        <SectionEditor
                          section={section}
                          onSave={(content) => saveSection(section.id, content)}
                          onCancel={() => toggleEdit(section.id)}
                        />
                      ) : (
                        <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap">
                          {section.content}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <div className="space-y-6">
                  <MetricsCard metrics={postmortem.metrics} />
                </div>
              </div>
            )}

            {activeTab === "timeline" && (
              <TimelineView events={postmortem.timeline} />
            )}

            {activeTab === "actions" && (
              <ActionItemsTable
                items={postmortem.action_items}
                onUpdate={updateActionItem}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
