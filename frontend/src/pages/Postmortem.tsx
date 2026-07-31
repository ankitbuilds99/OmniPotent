import { useState } from "react";
import { Icon } from "../components/Icon";

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

const DEMO_POSTMORTEM: PostmortemData = {
  id: "pm-001",
  incident_id: "inc-001",
  title: "Checkout Service Outage - Database Connection Pool Exhaustion",
  status: "draft",
  severity: "SEV1",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  authors: ["Alice Chen"],
  reviewers: ["Bob Smith", "Carol Davis"],
  sections: [
    {
      id: "summary",
      title: "Executive Summary",
      content: "On August 1st, 2026, the checkout service experienced a 45-minute outage affecting approximately 12,000 customers. The root cause was identified as database connection pool exhaustion due to a combination of increased traffic and a connection leak introduced in deployment v2.4.1.",
      aiGenerated: true,
      editing: false,
    },
    {
      id: "impact",
      title: "Impact",
      content: "- **Duration**: 45 minutes (14:23 - 15:08 UTC)\n- **Affected Users**: ~12,000 customers\n- **Failed Transactions**: 2,847\n- **Estimated Revenue Impact**: $142,350\n- **SLA Breach**: Yes (99.9% monthly SLA impacted)",
      aiGenerated: true,
      editing: false,
    },
    {
      id: "root_cause",
      title: "Root Cause Analysis",
      content: "The incident was caused by a database connection leak introduced in checkout-service v2.4.1. The leak occurred in the new payment retry logic, where connections were not being properly released on timeout errors.\n\nUnder normal load, the leak was not noticeable. However, a marketing campaign drove 3x normal traffic, accelerating connection pool exhaustion.",
      aiGenerated: true,
      editing: false,
    },
    {
      id: "lessons",
      title: "Lessons Learned",
      content: "### What Went Well\n- AI investigation identified root cause in under 5 minutes\n- Safety gates prevented automated action on high-blast-radius change\n- Rollback was clean with no data issues\n\n### What Went Wrong\n- Connection leak was not caught in code review\n- Load testing did not simulate sustained high traffic",
      aiGenerated: true,
      editing: false,
    },
  ],
  action_items: [
    { id: "ai-1", title: "Add connection leak detection to CI/CD pipeline", owner: "Alice Chen", priority: "P0", status: "in_progress", due_date: "2026-08-08", type: "prevent" },
    { id: "ai-2", title: "Lower connection pool exhaustion alert threshold", owner: "Bob Smith", priority: "P1", status: "completed", due_date: "2026-08-02", type: "detect" },
    { id: "ai-3", title: "Implement automatic connection pool scaling", owner: "Carol Davis", priority: "P1", status: "open", due_date: "2026-08-15", type: "mitigate" },
  ],
  timeline: [
    { timestamp: "2026-08-01T12:15:00Z", event: "checkout-service v2.4.1 deployed", actor: "CI/CD", type: "action" },
    { timestamp: "2026-08-01T14:00:00Z", event: "Marketing campaign starts, traffic increases 3x", type: "action" },
    { timestamp: "2026-08-01T14:23:00Z", event: "Error rate alert fires (5.2%)", type: "detection" },
    { timestamp: "2026-08-01T14:28:00Z", event: "Root cause identified: connection pool exhaustion", type: "detection" },
    { timestamp: "2026-08-01T14:42:00Z", event: "Rollback to v2.4.0 initiated", actor: "Alice Chen", type: "action" },
    { timestamp: "2026-08-01T15:08:00Z", event: "All metrics normalized, incident resolved", type: "resolution" },
  ],
  metrics: {
    time_to_detect: 8,
    time_to_acknowledge: 2,
    time_to_mitigate: 35,
    time_to_resolve: 45,
    customer_impact: "12,000 users unable to complete checkout",
    affected_services: ["checkout-service", "payment-gateway", "inventory-service"],
  },
};

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

export function Postmortem({ onBack }: { onBack: () => void }) {
  const [postmortem, setPostmortem] = useState<PostmortemData>(DEMO_POSTMORTEM);
  const [generating, setGenerating] = useState(false);
  const [activeTab, setActiveTab] = useState<"content" | "timeline" | "actions">("content");

  const toggleEdit = (sectionId: string) => {
    setPostmortem({
      ...postmortem,
      sections: postmortem.sections.map(s =>
        s.id === sectionId ? { ...s, editing: !s.editing } : s
      ),
    });
  };

  const saveSection = (sectionId: string, content: string) => {
    setPostmortem({
      ...postmortem,
      sections: postmortem.sections.map(s =>
        s.id === sectionId ? { ...s, content, editing: false, aiGenerated: false } : s
      ),
    });
  };

  const updateActionItem = (itemId: string, status: ActionItem["status"]) => {
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
                  <SeverityBadge severity={postmortem.severity} />
                  <StatusBadge status={postmortem.status} />
                </div>
                <div className="text-sm text-gray-500">{postmortem.incident_id}</div>
              </div>
            </div>
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
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
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
      </div>
    </div>
  );
}
