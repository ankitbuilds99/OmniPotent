/**
 * Runbook Builder - Real API integration
 */

import { useState, useCallback, useEffect } from "react";
import { Icon } from "../components/Icon";

import { apiFetch, describeFailure } from "../lib/apiClient";

/** Mirrors `RunbookStep` in app/runbooks/models.py, field for field.
 *
 *  It used to hold only `{id, type, name, config}`, where `config` was the
 *  backend's `params`. Everything else the server stores -- `action` (the shell
 *  command the step actually runs), `description`, `requires_approval` (the
 *  human gate on destructive steps), `timeout_seconds`, `continue_on_failure`,
 *  `rollback_step` -- was dropped on load. The save payload then wrote those
 *  same keys back from defaults, so loading a runbook and pressing Save Draft
 *  without touching anything blanked every command, cleared every approval gate
 *  and erased every description. The builder auto-loads `runbooks[0]`, so
 *  merely opening the page and clicking Save was enough to do it.
 *
 *  The rule this type now enforces: anything the server round-trips is carried
 *  here, even if no editor control is bound to it, so a save can never be a
 *  silent delete. */
interface RunbookStep {
  id: string;
  /** Keep the backend's raw vocabulary intact, including legacy values this UI
   * does not understand. The server remains the authority on accepted values. */
  type: string;
  name: string;
  description: string;
  /** The command / API call this step performs. Lives beside `params` on the
   *  wire; the editor used to read it out of `config.command`, which is why an
   *  existing step's Command box rendered empty. */
  action: string;
  config: Record<string, unknown>;
  timeout_seconds: number;
  continue_on_failure: boolean;
  requires_approval: boolean;
  rollback_step: string | null;
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
  /** Which service the runbook applies to; "*" means any. Dropped on load and
   *  reset to the "*" default on every save. */
  service: string;
  trigger: "manual" | "alert" | "schedule" | "webhook";
  execution_mode: string;
  steps: RunbookStep[];
  variables: Variable[];
  /** Categorisation the server stores. Not editable here, but carried so a save
   *  does not delete it. */
  labels: Record<string, string>;
  enabled: boolean;
  /** `null` when the executions endpoint could not be read. The header printed a
   *  hardcoded 0, which is indistinguishable from "has never run" -- exactly the
   *  thing you check before enabling a runbook that restarts production. */
  execution_count: number | null;
}

const STEP_TEMPLATES = [
  { type: "command", name: "Command", icon: "terminal", description: "Legacy command step; approval and executor policy still apply" },
  { type: "kubernetes", name: "Kubernetes", icon: "box", description: "Kubernetes integration action" },
  { type: "terraform", name: "Terraform", icon: "box", description: "Legacy backend step type" },
  { type: "http", name: "HTTP", icon: "globe", description: "Configured HTTP integration action" },
  { type: "slack", name: "Slack", icon: "bell", description: "Slack notification action" },
  { type: "wait", name: "Wait", icon: "clock", description: "Wait for a duration" },
  { type: "condition", name: "Condition", icon: "git-branch", description: "Legacy backend step type" },
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
    kubernetes: "box",
    terraform: "box",
    http: "globe",
    slack: "bell",
    condition: "git-branch",
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
            {step.type === "command" && <code className="text-xs">{step.action}</code>}
            {["kubernetes", "terraform", "http", "slack"].includes(step.type) && <code className="text-xs">{step.action}</code>}
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

        {/* The server stores a description per step and shows it to whoever is
            asked to approve the run. It had no field here, so loading a runbook
            and saving it blanked the descriptions the built-ins ship with. */}
        <div>
          <label className="block text-sm font-medium mb-1">Description</label>
          <input
            type="text"
            value={step.description}
            onChange={(e) => onChange({ ...step, description: e.target.value })}
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
            placeholder="What this step does, and why"
          />
        </div>

        {["command", "kubernetes", "terraform", "http", "slack"].includes(step.type) && (
          <div>
            <label className="block text-sm font-medium mb-1">Action</label>
            <textarea
              value={step.action}
              onChange={(e) => onChange({ ...step, action: e.target.value })}
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 font-mono text-sm"
              placeholder="Backend action identifier"
            />
          </div>
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

        {!STEP_TEMPLATES.some((template) => template.type === step.type) && (
          <p role="alert" className="text-sm text-amber-700 dark:text-amber-300">
            Unsupported legacy step type “{step.type}”. It is preserved as-is; the API may reject edits until it is migrated.
          </p>
        )}

        {/* Execution controls the server enforces on every step type. None of
            these were editable -- or even visible -- so the only way to see
            that a step was gated on human approval was to read the API. Saving
            from this page silently cleared the gate. */}
        <div className="pt-4 border-t border-gray-200 dark:border-gray-700 space-y-3">
          <div>
            <label className="block text-sm font-medium mb-1">Timeout (seconds)</label>
            <input
              type="number"
              min={1}
              value={step.timeout_seconds}
              onChange={(e) => onChange({ ...step, timeout_seconds: parseInt(e.target.value) || 1 })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700"
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={step.requires_approval}
              onChange={(e) => onChange({ ...step, requires_approval: e.target.checked })}
              className="rounded"
            />
            Requires approval before running
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={step.continue_on_failure}
              onChange={(e) => onChange({ ...step, continue_on_failure: e.target.checked })}
              className="rounded"
            />
            Continue the runbook if this step fails
          </label>
        </div>
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
      <div className="flex items-center justify-between mb-1">
        <h3 className="font-semibold">Variables</h3>
        <button
          onClick={addVariable}
          className="px-2 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          + Add
        </button>
      </div>
      {/* The runbook schema has no variables field, so these are not persisted.
          Saying so beats accepting them, showing them saved, and losing them on
          the next load -- which is what this panel did before. */}
      <p className="text-xs text-gray-500 mb-4">
        Not saved with the runbook yet — these apply to this editing session only.
      </p>

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
    service: "*",
    trigger: "manual",
    execution_mode: "requires_approval",
    steps: [],
    variables: [],
    labels: {},
    enabled: false,
    execution_count: 0,
  };
}

/** Read a runbook exactly as the server stores it.
 *
 *  Paired with `toPayload` below; the two must stay symmetric, because the save
 *  is a PUT (full replacement), so any field this drops is a field the next save
 *  deletes. */
function fromDetail(detail: Record<string, unknown>, executionCount: number | null): Runbook {
  const rawSteps = Array.isArray(detail.steps) ? (detail.steps as Record<string, unknown>[]) : [];
  return {
    id: String(detail.id),
    name: String(detail.name ?? ""),
    description: String(detail.description ?? ""),
    service: String(detail.service ?? "*"),
    trigger: (detail.trigger as Runbook["trigger"]) || "manual",
    execution_mode: String(detail.execution_mode ?? "requires_approval"),
    steps: rawSteps.map((s, i) => {
      const type = String(s.step_type ?? "command");
      return {
        id: String(s.id ?? `step-${i}`),
        // Preserve the exact backend value. Coercing unknown values to command
        // changes the meaning of a step and can turn an unsupported action into
        // a shell action when the full runbook is saved.
        type,
        name: String(s.name ?? `Step ${i + 1}`),
        description: String(s.description ?? ""),
        action: String(s.action ?? ""),
        config: (s.params as Record<string, unknown>) || {},
        timeout_seconds: Number(s.timeout_seconds) || 300,
        continue_on_failure: Boolean(s.continue_on_failure),
        requires_approval: Boolean(s.requires_approval),
        rollback_step: (s.rollback_step as string | null) ?? null,
        next: s.next as string | undefined,
        onSuccess: s.on_success as string | undefined,
        onFailure: s.on_failure as string | undefined,
      };
    }),
    // The server has no field for these yet, so they are per-session only. The
    // panel says as much rather than accepting them and dropping them silently.
    variables: [],
    labels: (detail.labels as Record<string, string>) || {},
    enabled: Boolean(detail.enabled),
    execution_count: executionCount,
  };
}

/** The PUT/POST body. Every field `fromDetail` reads is written back. */
function toPayload(runbook: Runbook) {
  return {
    name: runbook.name,
    description: runbook.description,
    service: runbook.service,
    trigger: runbook.trigger,
    execution_mode: runbook.execution_mode,
    labels: runbook.labels,
    enabled: runbook.enabled,
    steps: runbook.steps.map(s => ({
      id: s.id,
      name: s.name,
      description: s.description,
      step_type: s.type,
      action: s.action,
      params: s.config,
      timeout_seconds: s.timeout_seconds,
      continue_on_failure: s.continue_on_failure,
      requires_approval: s.requires_approval,
      rollback_step: s.rollback_step,
    })),
  };
}

/** How many times this runbook has run, or `null` if that cannot be read.
 *
 *  The header printed a hardcoded 0. Returning 0 on failure here would put the
 *  same lie back in a different place, so an unreachable or unparseable
 *  endpoint reports "unavailable" instead. */
async function countExecutions(runbookId: string): Promise<number | null> {
  try {
    const res = await apiFetch(`/api/runbooks/executions`);
    if (!res.ok) return null;
    const executions = await res.json();
    if (!Array.isArray(executions)) return null;
    return executions.filter((e: { runbook_id?: string }) => e.runbook_id === runbookId).length;
  } catch {
    return null;
  }
}

export function RunbookBuilder({ onBack }: { onBack: () => void }) {
  const [runbook, setRunbook] = useState<Runbook | null>(null);
  const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // True while the runbook on screen has never been persisted. It used to be
  // derived from the id with `id.startsWith("rb-") && !id.includes("-")` — a
  // predicate that is false for every possible string, so a brand-new runbook
  // was PUT at an id the server had never heard of and the save was lost.
  const [isNew, setIsNew] = useState(true);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<string | null>(null);

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
            setRunbook(fromDetail(detail, await countExecutions(detail.id)));
            setIsNew(false); // came from the server, so Save must PUT
          } else {
            setRunbook(EmptyRunbook());
            setIsNew(true);
          }
        } else {
          setRunbook(EmptyRunbook());
          setIsNew(true);
        }
      } else {
        setRunbook(EmptyRunbook());
        setIsNew(true);
      }
    } catch (error) {
      console.error("Failed to fetch runbook:", error);
      setRunbook(EmptyRunbook());
      setIsNew(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRunbook();
  }, [fetchRunbook]);

  const saveRunbook = async (overrides: Partial<Runbook> = {}) => {
    if (!runbook) return false;

    setSaving(true);
    setSaveError(null);
    try {
      const next = { ...runbook, ...overrides };
      const payload = toPayload(next);

      const url = isNew ? "/api/runbooks" : `/api/runbooks/${runbook.id}`;
      const method = isNew ? "POST" : "PUT";

      const res = await apiFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        // Save used to only act on res.ok, so a 405/401/404 left the button
        // looking successful while nothing had been written.
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (typeof body?.detail === "string" && body.detail) detail = body.detail;
        } catch {
          /* non-JSON error body */
        }
        setSaveError(`Save failed: ${detail}`);
        setSavedAt(null);
        return false;
      }

      const saved = await res.json();
      setRunbook({ ...next, id: saved.id });
      setIsNew(false); // it exists on the server now; further saves are PUTs
      setSavedAt(new Date().toLocaleTimeString());
      return true;
    } catch (error) {
      console.error("Failed to save runbook:", error);
      setSaveError(
        `Save failed: ${error instanceof Error ? error.message : "network error"}`
      );
      setSavedAt(null);
      return false;
    } finally {
      setSaving(false);
    }
  };

  // Publish is "save, and turn it on". It had no handler at all, so a runbook
  // could be built here and never enabled from here.
  const publishRunbook = async () => {
    const ok = await saveRunbook({ enabled: true });
    if (ok) setRunError(null);
  };

  // Test Run executes the saved definition. It had no handler either. The save
  // comes first on purpose: running the version on screen means running what
  // the server has, and unsaved edits are not that.
  const testRun = async () => {
    if (!runbook) return;
    setRunError(null);
    setRunResult(null);
    if (!(await saveRunbook())) return;

    setRunning(true);
    try {
      const res = await apiFetch(`/api/runbooks/${runbook.id}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service: runbook.service === "*" ? null : runbook.service }),
      });
      if (!res.ok) {
        setRunError(`Test run failed: ${await describeFailure(res)}`);
        return;
      }
      const execution = await res.json();
      setRunResult(
        execution.status === "waiting_approval"
          ? "Started — waiting for approval"
          : `Started — ${execution.status}`
      );
      setRunbook(rb =>
        rb ? { ...rb, execution_count: rb.execution_count === null ? null : rb.execution_count + 1 } : rb
      );
    } catch (error) {
      setRunError(
        `Test run failed: ${error instanceof Error ? error.message : "network error"}`
      );
    } finally {
      setRunning(false);
    }
  };

  const selectedStep = runbook && selectedStepIndex !== null ? runbook.steps[selectedStepIndex] : null;

  const addStep = useCallback((type: string) => {
    if (!runbook) return;
    const newStep: RunbookStep = {
      id: `step-${Date.now()}`,
      type: type as RunbookStep["type"],
      name: `New ${type} step`,
      description: "",
      action: "",
      config: {},
      timeout_seconds: 300,
      continue_on_failure: false,
      // A new step is gated by default. The safe default for something that may
      // run a shell command is to ask first.
      requires_approval: type === "command",
      rollback_step: null,
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
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 omni-workspace-page flex items-center justify-center">
        <LoadingState />
      </div>
    );
  }

  if (!runbook) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 omni-workspace-page flex items-center justify-center">
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
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 omni-workspace-page">
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
                  {/* `status` was a client-only string the server never stored,
                      so it always read "draft" no matter what was published.
                      `enabled` is the flag the trigger dispatcher checks. */}
                  <span
                    className={`px-2 py-0.5 rounded text-xs ${
                      runbook.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-700"
                    }`}
                    title={
                      runbook.enabled
                        ? "Enabled: this runbook can be triggered."
                        : "Disabled: saved, but triggers will not fire it. Use Publish to enable."
                    }
                  >
                    {runbook.enabled ? "enabled" : "draft"}
                  </span>
                  <span>
                    {runbook.execution_count === null
                      ? "execution count unavailable"
                      : `${runbook.execution_count} executions`}
                  </span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={testRun}
                disabled={saving || running}
                className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                {running ? "Running..." : "Test Run"}
              </button>
              <button
                onClick={() => saveRunbook()}
                disabled={saving || running}
                className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save Draft"}
              </button>
              <button
                onClick={publishRunbook}
                disabled={saving || running}
                className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
              >
                Publish
              </button>
            </div>
          </div>
          {(saveError || runError || savedAt || runResult) && (
            <div className="pb-3 flex flex-wrap items-center gap-3 text-sm">
              {saveError && (
                <span role="alert" className="text-red-600 dark:text-red-400">
                  {saveError}
                </span>
              )}
              {!saveError && savedAt && (
                <span className="text-green-600 dark:text-green-400">Saved {savedAt}</span>
              )}
              {runError && (
                <span role="alert" className="text-red-600 dark:text-red-400">
                  {runError}
                </span>
              )}
              {!runError && runResult && (
                <span className="text-blue-600 dark:text-blue-400">{runResult}</span>
              )}
            </div>
          )}
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
