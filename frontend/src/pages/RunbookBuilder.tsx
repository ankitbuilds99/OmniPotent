/**
 * Runbook Builder - Real API integration
 */

import { useState, useCallback, useEffect } from "react";
import { Icon } from "../components/Icon";

import { apiFetch } from "../lib/apiClient";

interface RunbookStep {
  id: string;
  type: "command" | "approval" | "condition" | "notification" | "wait" | "script" | "ai_action";
  name: string;
  config: Record<string, unknown>;
  next?: string;
  onSuccess?: string;
  onFailure?: string;
}

interface Variable {
  name: string;
  type: "string" | "number" | "boolean" | "secret";
  default?: string;
  required: boolean;
}

interface Runbook {
  id: string;
  name: string;
  description: string;
  trigger: "manual" | "alert" | "schedule" | "webhook";
  steps: RunbookStep[];
  variables: Variable[];
  status: "draft" | "active" | "disabled";
  execution_count: number;
}

const STEP_TEMPLATES = [
  { type: "command", name: "Run Command", icon: "terminal", description: "Execute shell command or kubectl" },
  { type: "script", name: "Run Script", icon: "code", description: "Execute Python or Bash script" },
  { type: "ai_action", name: "AI Action", icon: "cpu", description: "Let AI decide the best action" },
  { type: "approval", name: "Request Approval", icon: "check-circle", description: "Wait for human approval" },
  { type: "condition", name: "Condition", icon: "git-branch", description: "Branch based on condition" },
  { type: "notification", name: "Send Notification", icon: "bell", description: "Notify via Slack, email, etc." },
  { type: "wait", name: "Wait", icon: "clock", description: "Wait for duration or condition" },
];

function StepCard({
  step,
  index,
  selected,
  onSelect,
  onDelete,
  onMoveUp,
  onMoveDown,
  isFirst,
  isLast,
}: {
  step: RunbookStep;
  index: number;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  const typeIcons: Record<string, string> = {
    command: "terminal",
    script: "code",
    ai_action: "cpu",
    approval: "check-circle",
    condition: "git-branch",
    notification: "bell",
    wait: "clock",
  };

  return (
    <div
      className={`relative bg-white dark:bg-gray-800 rounded-lg border-2 p-4 cursor-pointer transition-all ${
        selected
          ? "border-blue-500 ring-2 ring-blue-200 dark:ring-blue-800"
          : "border-gray-200 dark:border-gray-700 hover:border-gray-300"
      }`}
      onClick={onSelect}
    >
      <div className="flex items-start gap-3">
        <div className="flex flex-col items-center">
          <div className="w-8 h-8 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center text-sm font-medium">
            {index + 1}
          </div>
          {!isLast && (
            <div className="w-0.5 h-8 bg-gray-300 dark:bg-gray-600 my-2" />
          )}
        </div>

        <div className="flex-1">
          <div className="flex items-center gap-2">
            <Icon name={typeIcons[step.type] as "terminal" | "code" | "cpu" | "check-circle" | "git-branch" | "bell" | "clock"} size={16} />
            <span className="font-medium">{step.name}</span>
          </div>
          <div className="text-sm text-gray-500 mt-1">
            {step.type === "command" && <code className="text-xs">{String(step.config.command || "")}</code>}
            {step.type === "notification" && `Channels: ${(step.config.channels as string[])?.join(", ") || ""}`}
            {step.type === "approval" && `Approvers: ${(step.config.approvers as string[])?.join(", ") || ""}`}
            {step.type === "condition" && <code className="text-xs">{String(step.config.condition || "")}</code>}
            {step.type === "wait" && `${step.config.duration_seconds || 30}s`}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <button
            onClick={(e) => { e.stopPropagation(); onMoveUp(); }}
            disabled={isFirst}
            className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded disabled:opacity-30"
          >
            <Icon name="chevron-up" size={14} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onMoveDown(); }}
            disabled={isLast}
            className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded disabled:opacity-30"
          >
            <Icon name="chevron-down" size={14} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="p-1 hover:bg-red-100 dark:hover:bg-red-900/30 text-red-500 rounded"
          >
            <Icon name="trash-2" size={14} />
          </button>
        </div>
      </div>

      {step.type === "condition" && (
        <div className="mt-3 flex gap-4 text-xs">
          <div className="flex items-center gap-1 text-green-600">
            <span>Success:</span>
            <span>{step.onSuccess || "End"}</span>
          </div>
          <div className="flex items-center gap-1 text-red-600">
            <span>Failure:</span>
            <span>{step.onFailure || "End"}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function StepEditor({ step, onChange }: { step: RunbookStep; onChange: (step: RunbookStep) => void }) {
  const updateConfig = (key: string, value: unknown) => {
    onChange({ ...step, config: { ...step.config, [key]: value } });
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">Edit Step</h3>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">Step Name</label>
          <input
            type="text"
            value={step.name}
            onChange={(e) => onChange({ ...step, name: e.target.value })}
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
          />
        </div>

        {step.type === "command" && (
          <>
            <div>
              <label className="block text-sm font-medium mb-1">Command</label>
              <textarea
                value={String(step.config.command || "")}
                onChange={(e) => updateConfig("command", e.target.value)}
                rows={3}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 font-mono text-sm"
                placeholder="kubectl rollout restart deployment/{{service}}"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Timeout (seconds)</label>
              <input
                type="number"
                value={Number(step.config.timeout_seconds) || 60}
                onChange={(e) => updateConfig("timeout_seconds", parseInt(e.target.value))}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
              />
            </div>
          </>
        )}

        {step.type === "notification" && (
          <>
            <div>
              <label className="block text-sm font-medium mb-1">Channels</label>
              <div className="flex flex-wrap gap-2">
                {["slack", "email", "pagerduty", "teams"].map((ch) => (
                  <label key={ch} className="flex items-center gap-1 text-sm">
                    <input
                      type="checkbox"
                      checked={(step.config.channels as string[])?.includes(ch)}
                      onChange={(e) => {
                        const channels = (step.config.channels as string[]) || [];
                        if (e.target.checked) {
                          updateConfig("channels", [...channels, ch]);
                        } else {
                          updateConfig("channels", channels.filter((c: string) => c !== ch));
                        }
                      }}
                      className="rounded"
                    />
                    {ch}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Message</label>
              <textarea
                value={String(step.config.message || "")}
                onChange={(e) => updateConfig("message", e.target.value)}
                rows={3}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
                placeholder="Use {{variable}} for dynamic values"
              />
            </div>
          </>
        )}

        {step.type === "approval" && (
          <>
            <div>
              <label className="block text-sm font-medium mb-1">Approvers</label>
              <input
                type="text"
                value={(step.config.approvers as string[])?.join(", ") || ""}
                onChange={(e) => updateConfig("approvers", e.target.value.split(",").map(s => s.trim()))}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
                placeholder="oncall-primary, team-lead"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Timeout (minutes)</label>
              <input
                type="number"
                value={Number(step.config.timeout_minutes) || 5}
                onChange={(e) => updateConfig("timeout_minutes", parseInt(e.target.value))}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
              />
            </div>
          </>
        )}

        {step.type === "condition" && (
          <>
            <div>
              <label className="block text-sm font-medium mb-1">Condition</label>
              <input
                type="text"
                value={String(step.config.condition || "")}
                onChange={(e) => updateConfig("condition", e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 font-mono"
                placeholder="error_rate > 5"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1 text-green-600">On Success</label>
                <input
                  type="text"
                  value={step.onSuccess || ""}
                  onChange={(e) => onChange({ ...step, onSuccess: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
                  placeholder="step-id"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1 text-red-600">On Failure</label>
                <input
                  type="text"
                  value={step.onFailure || ""}
                  onChange={(e) => onChange({ ...step, onFailure: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
                  placeholder="step-id"
                />
              </div>
            </div>
          </>
        )}

        {step.type === "wait" && (
          <div>
            <label className="block text-sm font-medium mb-1">Duration (seconds)</label>
            <input
              type="number"
              value={Number(step.config.duration_seconds) || 30}
              onChange={(e) => updateConfig("duration_seconds", parseInt(e.target.value))}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
            />
          </div>
        )}

        {step.type === "ai_action" && (
          <div>
            <label className="block text-sm font-medium mb-1">AI Instruction</label>
            <textarea
              value={String(step.config.instruction || "")}
              onChange={(e) => updateConfig("instruction", e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
              placeholder="Analyze the situation and suggest the best remediation action"
            />
            <p className="text-xs text-gray-500 mt-1">
              AI will use the 6-agent system to determine the best action
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function VariablesEditor({ variables, onChange }: { variables: Variable[]; onChange: (vars: Variable[]) => void }) {
  const addVariable = () => {
    onChange([...variables, { name: "", type: "string", required: false }]);
  };

  const updateVariable = (index: number, updates: Partial<Variable>) => {
    const newVars = [...variables];
    newVars[index] = { ...newVars[index], ...updates };
    onChange(newVars);
  };

  const removeVariable = (index: number) => {
    onChange(variables.filter((_, i) => i !== index));
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold">Variables</h3>
        <button
          onClick={addVariable}
          className="px-2 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          + Add
        </button>
      </div>

      <div className="space-y-3">
        {variables.map((variable, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              type="text"
              value={variable.name}
              onChange={(e) => updateVariable(i, { name: e.target.value })}
              placeholder="name"
              className="flex-1 px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
            />
            <select
              value={variable.type}
              onChange={(e) => updateVariable(i, { type: e.target.value as Variable["type"] })}
              className="px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
            >
              <option value="string">string</option>
              <option value="number">number</option>
              <option value="boolean">boolean</option>
              <option value="secret">secret</option>
            </select>
            <input
              type="text"
              value={variable.default || ""}
              onChange={(e) => updateVariable(i, { default: e.target.value })}
              placeholder="default"
              className="w-24 px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
            />
            <label className="flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={variable.required}
                onChange={(e) => updateVariable(i, { required: e.target.checked })}
                className="rounded"
              />
              req
            </label>
            <button
              onClick={() => removeVariable(i)}
              className="p-1 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 rounded"
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}

        {variables.length === 0 && (
          <p className="text-sm text-gray-500 text-center py-4">
            No variables defined. Add variables to make your runbook reusable.
          </p>
        )}
      </div>
    </div>
  );
}

function AddStepPanel({ onAdd }: { onAdd: (type: string) => void }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="font-semibold mb-4">Add Step</h3>
      <div className="grid grid-cols-2 gap-2">
        {STEP_TEMPLATES.map((template) => (
          <button
            key={template.type}
            onClick={() => onAdd(template.type)}
            className="flex items-center gap-2 p-3 text-left border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <Icon name={template.icon as "terminal" | "code" | "cpu" | "check-circle" | "git-branch" | "bell" | "clock"} size={16} />
            <div>
              <div className="font-medium text-sm">{template.name}</div>
              <div className="text-xs text-gray-500">{template.description}</div>
            </div>
          </button>
        ))}
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

function EmptyRunbook(): Runbook {
  return {
    id: `rb-${Date.now()}`,
    name: "New Runbook",
    description: "",
    trigger: "manual",
    steps: [],
    variables: [],
    status: "draft",
    execution_count: 0,
  };
}

export function RunbookBuilder({ onBack }: { onBack: () => void }) {
  const [runbook, setRunbook] = useState<Runbook | null>(null);
  const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchRunbook = useCallback(async () => {
    try {
      // Fetch existing runbooks
      const res = await apiFetch(`/api/runbooks`);
      if (res.ok) {
        const runbooks = await res.json();
        if (runbooks.length > 0) {
          // Load the first runbook for editing
          const detailRes = await apiFetch(`/api/runbooks/${runbooks[0].id}`);
          if (detailRes.ok) {
            const detail = await detailRes.json();
            const loadedRunbook: Runbook = {
              id: detail.id,
              name: detail.name,
              description: detail.description || "",
              trigger: detail.trigger || "manual",
              steps: detail.steps?.map((s: Record<string, unknown>, i: number) => ({
                id: s.id || `step-${i}`,
                type: s.step_type || "command",
                name: s.name || `Step ${i + 1}`,
                config: s.params || {},
                next: s.next,
                onSuccess: s.on_success,
                onFailure: s.on_failure,
              })) || [],
              variables: [],
              status: detail.enabled ? "active" : "draft",
              execution_count: 0,
            };
            setRunbook(loadedRunbook);
          } else {
            setRunbook(EmptyRunbook());
          }
        } else {
          setRunbook(EmptyRunbook());
        }
      } else {
        setRunbook(EmptyRunbook());
      }
    } catch (error) {
      console.error("Failed to fetch runbook:", error);
      setRunbook(EmptyRunbook());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRunbook();
  }, [fetchRunbook]);

  const saveRunbook = async () => {
    if (!runbook) return;

    setSaving(true);
    try {
      const payload = {
        name: runbook.name,
        description: runbook.description,
        trigger: runbook.trigger,
        execution_mode: "requires_approval",
        steps: runbook.steps.map(s => ({
          id: s.id,
          name: s.name,
          step_type: s.type,
          action: s.config.command || "",
          params: s.config,
          timeout_seconds: Number(s.config.timeout_seconds) || 300,
        })),
      };

      const isNew = runbook.id.startsWith("rb-") && !runbook.id.includes("-");
      const url = isNew ? "/api/runbooks" : `/api/runbooks/${runbook.id}`;
      const method = isNew ? "POST" : "PUT";

      const res = await apiFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        const saved = await res.json();
        setRunbook({ ...runbook, id: saved.id });
      }
    } catch (error) {
      console.error("Failed to save runbook:", error);
    } finally {
      setSaving(false);
    }
  };

  const selectedStep = runbook && selectedStepIndex !== null ? runbook.steps[selectedStepIndex] : null;

  const addStep = useCallback((type: string) => {
    if (!runbook) return;
    const newStep: RunbookStep = {
      id: `step-${Date.now()}`,
      type: type as RunbookStep["type"],
      name: `New ${type} step`,
      config: {},
    };
    setRunbook({ ...runbook, steps: [...runbook.steps, newStep] });
    setSelectedStepIndex(runbook.steps.length);
  }, [runbook]);

  const updateStep = useCallback((step: RunbookStep) => {
    if (!runbook || selectedStepIndex === null) return;
    const newSteps = [...runbook.steps];
    newSteps[selectedStepIndex] = step;
    setRunbook({ ...runbook, steps: newSteps });
  }, [runbook, selectedStepIndex]);

  const deleteStep = useCallback((index: number) => {
    if (!runbook) return;
    setRunbook({
      ...runbook,
      steps: runbook.steps.filter((_, i) => i !== index),
    });
    setSelectedStepIndex(null);
  }, [runbook]);

  const moveStep = useCallback((index: number, direction: -1 | 1) => {
    if (!runbook) return;
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= runbook.steps.length) return;

    const newSteps = [...runbook.steps];
    [newSteps[index], newSteps[newIndex]] = [newSteps[newIndex], newSteps[index]];
    setRunbook({ ...runbook, steps: newSteps });
    setSelectedStepIndex(newIndex);
  }, [runbook]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <LoadingState />
      </div>
    );
  }

  if (!runbook) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-center text-gray-500">
          <p>Failed to load runbook</p>
          <button onClick={fetchRunbook} className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg">
            Retry
          </button>
        </div>
      </div>
    );
  }

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
                <input
                  type="text"
                  value={runbook.name}
                  onChange={(e) => setRunbook({ ...runbook, name: e.target.value })}
                  className="text-xl font-semibold bg-transparent border-none focus:outline-none focus:ring-2 focus:ring-blue-500 rounded px-1"
                />
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    runbook.status === "active" ? "bg-green-100 text-green-700" :
                    runbook.status === "draft" ? "bg-gray-100 text-gray-700" :
                    "bg-red-100 text-red-700"
                  }`}>
                    {runbook.status}
                  </span>
                  <span>{runbook.execution_count} executions</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700">
                Test Run
              </button>
              <button
                onClick={saveRunbook}
                disabled={saving}
                className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
              >
                {saving ? "Saving..." : "Save Draft"}
              </button>
              <button className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700">
                Publish
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Steps List */}
          <div className="lg:col-span-2 space-y-4">
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <h3 className="font-semibold mb-4">Trigger</h3>
              <div className="flex gap-2">
                {(["manual", "alert", "schedule", "webhook"] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setRunbook({ ...runbook, trigger: t })}
                    className={`px-3 py-1.5 text-sm rounded ${
                      runbook.trigger === t
                        ? "bg-blue-600 text-white"
                        : "bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600"
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              {runbook.steps.length === 0 ? (
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 text-center text-gray-500">
                  No steps yet. Add a step to get started.
                </div>
              ) : (
                runbook.steps.map((step, i) => (
                  <StepCard
                    key={step.id}
                    step={step}
                    index={i}
                    selected={selectedStepIndex === i}
                    onSelect={() => setSelectedStepIndex(i)}
                    onDelete={() => deleteStep(i)}
                    onMoveUp={() => moveStep(i, -1)}
                    onMoveDown={() => moveStep(i, 1)}
                    isFirst={i === 0}
                    isLast={i === runbook.steps.length - 1}
                  />
                ))
              )}
            </div>

            <AddStepPanel onAdd={addStep} />
          </div>

          {/* Editor Panel */}
          <div className="space-y-4">
            {selectedStep ? (
              <StepEditor step={selectedStep} onChange={updateStep} />
            ) : (
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 text-center text-gray-500">
                Select a step to edit or add a new step
              </div>
            )}

            <VariablesEditor
              variables={runbook.variables}
              onChange={(vars) => setRunbook({ ...runbook, variables: vars })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
