/**
 * Postmortem Page - Real API integration
 */

import { useState, useEffect, useCallback } from "react";
import { Icon } from "../components/Icon";
import { useApiBaseUrl } from "../lib/runtime";

interface PostmortemSection {
  id: string;
  title: string;
  content: string;
  aiGenerated: boolean;
  editing: boolean;
}

interface ActionItem {
  id: string;
  title: string;
  owner: string;
  priority: "P0" | "P1" | "P2" | "P3";
  status: "open" | "in_progress" | "completed";
  due_date: string;
  type: "prevent" | "detect" | "mitigate";
}

interface TimelineEvent {
  timestamp: string;
  event: string;
  actor?: string;
  type: "detection" | "action" | "communication" | "resolution";
}

interface PostmortemMetrics {
  time_to_detect: number;
  time_to_acknowledge: number;
  time_to_mitigate: number;
  time_to_resolve: number;
  customer_impact: string;
  affected_services: string[];
}

interface PostmortemData {
  id: string;
  incident_id: string;
  title: string;
  status: "draft" | "in_review" | "published";
  severity: "SEV1" | "SEV2" | "SEV3" | "SEV4";
  created_at: string;
  updated_at: string;
  authors: string[];
  reviewers: string[];
  sections: PostmortemSection[];
  action_items: ActionItem[];
  timeline: TimelineEvent[];
  metrics: PostmortemMetrics;
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

function MetricsCard({ metrics }: { metrics: PostmortemMetrics }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">Incident Metrics</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-gray-500 uppercase">Time to Detect</div>
          <div className="text-2xl font-bold text-blue-600">{metrics.time_to_detect}m</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase">Time to Acknowledge</div>
          <div className="text-2xl font-bold text-green-600">{metrics.time_to_acknowledge}m</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase">Time to Mitigate</div>
          <div className="text-2xl font-bold text-yellow-600">{metrics.time_to_mitigate}m</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase">Time to Resolve</div>
          <div className="text-2xl font-bold text-red-600">{metrics.time_to_resolve}m</div>
        </div>
      </div>
      <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
        <div className="text-xs text-gray-500 uppercase mb-2">Affected Services</div>
        <div className="flex flex-wrap gap-1">
          {metrics.affected_services.map((svc) => (
            <span key={svc} className="px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs">
              {svc}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function TimelineView({ events }: { events: TimelineEvent[] }) {
  const typeColors = {
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
              <div className={`w-3 h-3 rounded-full ${typeColors[event.type]}`} />
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
              <td className="px-4 py-3 text-sm">{item.owner}</td>
              <td className="px-4 py-3">
                <span className={`px-2 py-0.5 text-xs font-medium rounded ${priorityColors[item.priority]}`}>
                  {item.priority}
                </span>
              </td>
              <td className="px-4 py-3 text-sm text-gray-500">
                {new Date(item.due_date).toLocaleDateString()}
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

function EmptyState({ onSelectIncident }: { onSelectIncident: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-500">
      <Icon name="file-text" size={48} />
      <p className="mt-4 text-lg font-medium">No Postmortem</p>
      <p className="text-sm">Select an incident to generate a postmortem</p>
      <button
        onClick={onSelectIncident}
        className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
      >
        Select Incident
      </button>
    </div>
  );
}

export function Postmortem({ onBack }: { onBack: () => void }) {
  const apiBaseUrl = useApiBaseUrl();
  const [postmortem, setPostmortem] = useState<PostmortemData | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [activeTab, setActiveTab] = useState<"content" | "timeline" | "actions">("content");

  const fetchPostmortem = useCallback(async () => {
    try {
      // Fetch the most recent resolved incident to generate postmortem from
      const incidentsRes = await fetch(`${apiBaseUrl}/api/incidents`);
      const incidents = incidentsRes.ok ? await incidentsRes.json() : [];

      // Find a resolved incident to base postmortem on
      const resolvedIncident = incidents.find((i: Record<string, unknown>) => i.status === "resolved") || incidents[0];

      if (resolvedIncident) {
        // Generate postmortem data from incident with real timestamps
        const now = new Date();
        const triggeredAt = new Date(resolvedIncident.triggered_at || now.toISOString());
        const acknowledgedAt = resolvedIncident.acknowledged_at ? new Date(resolvedIncident.acknowledged_at) : null;
        const resolvedAt = new Date(resolvedIncident.resolved_at || now.toISOString());
        const ttDetect = 0;
        const ttAck = acknowledgedAt ? Math.floor((acknowledgedAt.getTime() - triggeredAt.getTime()) / 60000) : 0;
        const ttMitigate = Math.floor((resolvedAt.getTime() - triggeredAt.getTime()) / 60000) || 0;
        const ttResolve = ttMitigate;

        const pm: PostmortemData = {
          id: `pm-${resolvedIncident.id}`,
          incident_id: resolvedIncident.id,
          title: resolvedIncident.title || "Service Incident",
          status: "draft",
          severity: resolvedIncident.severity === "critical" ? "SEV1" :
                   resolvedIncident.severity === "high" ? "SEV2" :
                   resolvedIncident.severity === "medium" ? "SEV3" : "SEV4",
          created_at: now.toISOString(),
          updated_at: now.toISOString(),
          authors: ["On-Call Engineer"],
          reviewers: ["Team Lead"],
          sections: [
            {
              id: "summary",
              title: "Executive Summary",
              content: `On ${triggeredAt.toLocaleDateString()}, the ${resolvedIncident.service || "service"} experienced an incident. The root cause was identified and the issue was resolved after ${ttResolve} minutes.`,
              aiGenerated: true,
              editing: false,
            },
            {
              id: "impact",
              title: "Impact",
              content: `- **Duration**: ${ttResolve} minutes\n- **Affected Service**: ${resolvedIncident.service || "Unknown"}\n- **Severity**: ${resolvedIncident.severity || "Medium"}`,
              aiGenerated: true,
              editing: false,
            },
            {
              id: "root_cause",
              title: "Root Cause Analysis",
              content: "The incident was caused by an issue that requires further investigation. Initial analysis suggests the root cause may be related to recent changes or increased load.",
              aiGenerated: true,
              editing: false,
            },
            {
              id: "lessons",
              title: "Lessons Learned",
              content: "### What Went Well\n- Incident was detected quickly\n- Team responded promptly\n\n### What Could Be Improved\n- Detection could be faster\n- Documentation should be updated",
              aiGenerated: true,
              editing: false,
            },
          ],
          action_items: [
            {
              id: "ai-1",
              title: "Review and update monitoring alerts",
              owner: "On-Call Engineer",
              priority: "P1",
              status: "open",
              due_date: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString().split("T")[0],
              type: "detect",
            },
            {
              id: "ai-2",
              title: "Document runbook for this incident type",
              owner: "Team Lead",
              priority: "P2",
              status: "open",
              due_date: new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString().split("T")[0],
              type: "mitigate",
            },
          ],
          timeline: [
            { timestamp: triggeredAt.toISOString(), event: "Incident detected", type: "detection" },
            { timestamp: new Date(triggeredAt.getTime() + ttAck * 60000).toISOString(), event: "Incident acknowledged", actor: "On-Call", type: "action" },
            { timestamp: resolvedAt.toISOString(), event: "Incident resolved", type: "resolution" },
          ],
          metrics: {
            time_to_detect: ttDetect,
            time_to_acknowledge: ttAck,
            time_to_mitigate: ttMitigate,
            time_to_resolve: ttResolve,
            customer_impact: "Service was degraded during the incident",
            affected_services: [resolvedIncident.service || "unknown-service"],
          },
        };

        setPostmortem(pm);
      } else {
        setPostmortem(null);
      }
    } catch (error) {
      console.error("Failed to fetch postmortem:", error);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl]);

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

  const saveSection = (sectionId: string, content: string) => {
    if (!postmortem) return;
    setPostmortem({
      ...postmortem,
      sections: postmortem.sections.map(s =>
        s.id === sectionId ? { ...s, content, editing: false, aiGenerated: false } : s
      ),
    });
  };

  const updateActionItem = (itemId: string, status: ActionItem["status"]) => {
    if (!postmortem) return;
    setPostmortem({
      ...postmortem,
      action_items: postmortem.action_items.map(item =>
        item.id === itemId ? { ...item, status } : item
      ),
    });
  };

  const regenerateSection = async (_sectionId: string) => {
    setGenerating(true);
    await new Promise(resolve => setTimeout(resolve, 2000));
    setGenerating(false);
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
          <EmptyState onSelectIncident={fetchPostmortem} />
        ) : (
          <>
            <div className="mb-6">
              <h2 className="text-2xl font-bold">{postmortem.title}</h2>
              <div className="flex items-center gap-4 mt-2 text-sm text-gray-500">
                <span>Authors: {postmortem.authors.join(", ")}</span>
                <span>Reviewers: {postmortem.reviewers.join(", ")}</span>
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
                          {section.aiGenerated && (
                            <span className="text-xs bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-300 px-1.5 py-0.5 rounded">
                              AI Generated
                            </span>
                          )}
                        </h3>
                        <div className="flex gap-1">
                          {section.aiGenerated && (
                            <button
                              onClick={() => regenerateSection(section.id)}
                              disabled={generating}
                              className="p-1.5 text-gray-400 hover:text-purple-600 rounded"
                              title="Regenerate with AI"
                            >
                              <Icon name="refresh-cw" size={14} />
                            </button>
                          )}
                          <button
                            onClick={() => toggleEdit(section.id)}
                            className="p-1.5 text-gray-400 hover:text-blue-600 rounded"
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
