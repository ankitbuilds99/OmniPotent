"""Action policy engine — graduated human-in-the-loop by consequence class.

The old gate was binary: a tool was either a "write" (always approve) or a read
(always auto-run). That doesn't scale as the agent gains more mutating tools with
very different blast radii (open a PR vs. scale a deployment to zero vs. ack a
page). This module maps every tool — and, where it matters, its *arguments* — to
one of three decisions:

    allow   — read-only / no external side effect; run immediately.
    notify  — low-consequence, reversible mutation; run, but record an audit event.
    approve — consequential or hard-to-reverse mutation; pause for a human.

It also attaches a risk tier and a human-readable impact PREVIEW so the approval
card shows *what will happen* (terraform-plan style), not just a tool name. This
is the structural piece behind graduated oversight (a compliance expectation for
agents that can act on infrastructure).

Pure and dependency-free so the routing logic stays unit-testable. The set of
approve-class tools is the single source of truth for the graph's approval gate
(re-exported as WRITE_TOOLS for backward compatibility).
"""

from __future__ import annotations

from typing import Literal

Decision = Literal["allow", "notify", "approve"]
Risk = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]

_CONF_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

# Minimum investigation confidence required to AUTO-approve a write of each risk
# tier. A programmatic/automated approver (evals, a future auto-remediation loop,
# a bot) must not rubber-stamp a consequential write that rests on thin evidence —
# a human reviewer can still approve it explicitly. High-blast-radius actions
# demand a correspondingly well-evidenced investigation.
_MIN_AUTOAPPROVE_CONF: dict[str, str] = {"low": "low", "medium": "medium", "high": "high"}

# tool name -> (decision, risk, why). This table used to cover six servers only,
# and everything else fell through to a default of allow/low. That default was
# the hole: `execute_runbook` shells out to the cluster, `send_message` posts as
# us, `delete_alert_rule` removes detection coverage — and all of them routed
# straight past the approval gate while the audit trail recorded them as
# "Read-only — no external side effects". The table below now classifies every
# mutating tool we ship, and the default has been inverted (see `_READ_ONLY` and
# `classify`): an unclassified tool is treated as a consequential write, so a
# newly wired server cannot silently gain the right to auto-run.
_POLICY: dict[str, tuple[Decision, Risk, str]] = {
    # github
    "create_pull_request": ("approve", "medium", "Opens a real pull request against the repository"),
    # pagerduty (write actions added with the connector work)
    "add_incident_note": ("notify", "low", "Posts a note to the incident timeline"),
    "acknowledge_incident": ("notify", "low", "Acknowledges the incident (reversible)"),
    "resolve_incident": ("approve", "medium", "Marks the incident resolved"),
    # kubernetes (write actions added with the connector work)
    "scale_deployment": ("approve", "high", "Changes the replica count of a live deployment"),
    "rollback_deployment": ("approve", "high", "Rolls a deployment back to a previous revision"),
    "restart_deployment": ("approve", "medium", "Triggers a rolling restart of a deployment"),
    # argocd (GitOps deployment management)
    "sync_application": ("approve", "high", "Syncs an ArgoCD application (deploys from Git to cluster)"),
    "rollback_application": ("approve", "high", "Rolls back an ArgoCD application to a previous revision"),
    # terraform (infrastructure-as-code management)
    "trigger_plan": ("approve", "medium", "Queues a Terraform plan on a workspace"),
    "trigger_apply": ("approve", "high", "Applies a Terraform run (changes live infrastructure)"),
    # marketplace — these mutate the deployment itself, not a monitored system.
    # An install changes which MCP subprocesses launch and which tools every
    # future investigation sees, so it is not a read no matter how it reads.
    "install_agent": ("approve", "medium", "Installs a marketplace agent: launches a new MCP server subprocess and adds its tools to every future investigation"),
    "uninstall_agent": ("approve", "medium", "Removes an installed agent and the tools other runs may depend on"),
    "configure_agent": ("approve", "medium", "Rewrites an installed agent's runtime configuration, including the endpoints and credentials its subprocess receives"),
    "enable_agent": ("notify", "low", "Re-enables an installed agent's tools (reversible)"),
    "disable_agent": ("notify", "low", "Stops loading an installed agent's tools (reversible)"),
    # runbooks — the steps shell out to the cluster with the deployment's
    # kubeconfig, and a MANUAL/AUTO_APPROVED runbook starts running the moment
    # the tool returns, so this is the single highest-blast-radius tool we ship.
    "execute_runbook": ("approve", "high", "Runs a runbook's steps (shell/kubectl) against the live environment"),
    "approve_execution": ("approve", "high", "Approves a parked runbook execution and releases its remaining steps to run"),
    "reject_execution": ("notify", "low", "Cancels a parked runbook execution (stops work, changes nothing else)"),
    # alerting — writes here change what the deployment can still detect, which
    # is why removing or silencing a rule is gated harder than adding one.
    "create_alert_rule": ("approve", "medium", "Creates an alert rule that will page on-call when it fires"),
    "update_alert_rule": ("approve", "medium", "Rewrites an existing alert rule's condition, threshold or severity"),
    "delete_alert_rule": ("approve", "high", "Permanently deletes an alert rule — the signal it watched stops being detected"),
    "disable_alert_rule": ("approve", "medium", "Silences an alert rule: the condition it watches will no longer page"),
    "enable_alert_rule": ("notify", "low", "Re-enables a disabled alert rule (reversible)"),
    "record_metric": ("notify", "low", "Records a metric value into the alert engine, which may trip a rule"),
    "test_alert_rule": ("notify", "low", "Feeds a value through a rule to see whether it would fire (records the value)"),
    # opsgenie — acking and noting are routine on-call hygiene; closing an alert
    # stops the page for everyone, so it needs a human.
    "acknowledge_alert": ("notify", "low", "Acknowledges the alert (reversible)"),
    "add_note": ("notify", "low", "Posts a note to the alert timeline"),
    "assign_alert": ("notify", "low", "Reassigns the alert to another responder"),
    "escalate_alert": ("notify", "medium", "Escalates the alert — pages additional responders"),
    "create_alert": ("notify", "medium", "Creates a new alert, which pages whoever is on call"),
    "close_alert": ("approve", "medium", "Closes the alert and stops it paging anyone"),
    # slack — low blast radius but externally visible: these post as us, in the
    # customer's workspace, and cannot be unsaid.
    "send_message": ("notify", "low", "Posts a message to a Slack channel as this deployment"),
    "post_incident_update": ("notify", "low", "Posts an incident status update to Slack"),
    "create_channel": ("notify", "low", "Creates a Slack channel"),
    "create_incident_channel": ("notify", "low", "Creates a Slack incident channel and invites responders"),
    "invite_to_channel": ("notify", "low", "Invites people to a Slack channel"),
    "set_channel_topic": ("notify", "low", "Rewrites a Slack channel's topic"),
    "add_reaction": ("notify", "low", "Adds a reaction to a Slack message"),
    # issue trackers (jira + linear share these tool names)
    "create_issue": ("notify", "low", "Files a new issue in the tracker"),
    "update_issue": ("notify", "low", "Edits an existing issue's fields or status"),
    "add_comment": ("notify", "low", "Comments on an existing issue"),
    "link_issues": ("notify", "low", "Links two issues together"),
    "link_incident": ("notify", "low", "Links an incident to an issue"),
}

# The read-only tools we ship, enumerated so the default can be deny. Everything
# in here has no external side effect: it queries an API, reads the repo, or
# renders a document/diagram as a string without writing it anywhere. A tool that
# is neither here nor in `_POLICY` is unknown to us, and `classify` treats
# unknown as approve-class rather than guessing it is safe.
_READ_ONLY: frozenset[str] = frozenset({
    # repo
    "read_file", "list_dir", "grep", "git_log",
    # github (reads)
    "list_recent_commits", "get_commit_diff", "first_bad_deploy", "correlate_changes",
    "list_workflow_runs", "get_failed_job_logs",
    # kubernetes (reads)
    "list_pods", "describe_pod", "get_events", "get_deployment_status", "rollout_history",
    # logs / metrics / datadog / newrelic / sentry / traces
    "list_services", "search_logs", "get_error_summary", "get_metric", "compute_burn_rate",
    "onset_timeline", "cluster_logs", "detect_anomaly", "nrql", "get_error_rate",
    "get_throughput", "get_latency", "get_errors", "get_alerts", "get_applications",
    "get_deployments", "get_hosts", "get_service_health", "get_slowest_transactions",
    "get_issue", "list_issues", "get_latest_event",
    "get_trace", "search_traces", "get_exemplars", "service_dependencies",
    "analyze_blast_radius", "analyze_critical_path", "correlate_incidents",
    # deploys
    "list_deploys", "get_deploy", "deploys_in_window",
    # pagerduty / opsgenie (reads)
    "get_incident", "list_incidents", "get_incident_alerts",
    "get_alert", "list_alerts", "get_on_call", "get_team_on_call", "list_schedules", "list_teams",
    # alerting (reads)
    "list_alert_rules", "get_alert_rule", "list_firing_alerts",
    # argocd (reads)
    "list_applications", "get_application", "get_application_history",
    "get_application_resources", "get_sync_status",
    # terraform (reads + generators that return text)
    "list_workspaces", "get_workspace", "get_run_details", "get_run_history",
    "get_plan_output", "get_state", "list_state_resources", "list_module_templates",
    "generate_terraform_module", "generate_backend_config", "generate_provider_config",
    "generate_tfvars",
    # issue trackers (reads)
    "search_issues", "get_project_info", "get_teams", "get_workflow_states",
    # slack (reads)
    "list_channels", "get_channel_history",
    # runbooks (reads)
    "list_runbooks", "get_runbook", "list_executions", "get_execution", "list_pending_approvals",
    # marketplace (reads)
    "list_marketplace_agents", "list_marketplace_categories", "get_agent_details",
    "list_installed_agents",
    # memory
    "search_incidents", "get_incident_record",
    # security scanners (read the tree, report findings)
    "scan_dependencies", "scan_dockerfile", "scan_kubernetes", "scan_secrets",
    "security_summary", "get_cve_details",
    # cost / multicloud (analysis only)
    "analyze_costs", "detect_unused", "rightsizing_recommendations", "cost_forecast",
    "estimate_resource_cost", "estimate_multicloud_cost", "compare_cloud_services",
    "get_cloud_regions", "list_cloud_resources", "generate_multicloud_iac",
    # generators — these return the rendered text to the agent, they do not write
    # files or touch any external system.
    "list_architecture_patterns", "analyze_architecture", "generate_architecture_diagram",
    "generate_iac_blueprint", "list_cicd_templates", "analyze_for_cicd",
    "generate_github_actions", "generate_gitlab_ci", "generate_circleci",
    "list_templates", "analyze_for_docker", "generate_dockerfile", "generate_compose",
    "optimize_dockerfile", "generate_readme", "generate_api_docs", "generate_runbook",
    "analyze_documentation", "generate_changelog",
})

# What an unclassified tool gets. Deliberately approve/high: we do not know what
# it does, so we must not let it run unattended, and the "why" says exactly that
# rather than claiming a side-effect analysis nobody performed.
_UNCLASSIFIED: tuple[Decision, Risk, str] = (
    "approve",
    "high",
    "UNCLASSIFIED tool — no policy entry exists for it, so its blast radius is unknown "
    "and it is gated as a consequential write until someone classifies it",
)


def classify(tool_name: str, args: dict | None = None) -> dict:
    """Return the policy decision for a tool call: decision, risk, why, preview.

    Argument-aware escalation: e.g. scaling a deployment to zero replicas is a
    full outage, so it is always high-risk/approve regardless of base policy.

    A tool we have never classified is NOT assumed to be a read: it falls through
    to `_UNCLASSIFIED` (approve/high). Only the tools enumerated in `_READ_ONLY`
    are declared side-effect-free, because that is the only set anybody actually
    checked.
    """
    if tool_name in _POLICY:
        decision, risk, why = _POLICY[tool_name]
    elif tool_name in _READ_ONLY:
        decision, risk, why = "allow", "low", "Read-only — no external side effects"
    else:
        decision, risk, why = _UNCLASSIFIED
    args = args or {}

    # Arg-aware escalations (consequence depends on the arguments, not just the tool).
    if tool_name == "scale_deployment":
        replicas = args.get("replicas")
        try:
            if replicas is not None and int(replicas) == 0:
                decision, risk = "approve", "high"
                why = "Scales the deployment to ZERO replicas (full outage of the service)"
        except (TypeError, ValueError):
            pass

    return {
        "tool": tool_name,
        "decision": decision,
        "risk": risk,
        "why": why,
        "write": decision in ("notify", "approve"),
        "preview": describe_action(tool_name, args),
    }


def requires_approval(tool_name: str, args: dict | None = None) -> bool:
    """True when a tool call must pause for a human (approve-class)."""
    return classify(tool_name, args)["decision"] == "approve"


def describe_action(tool_name: str, args: dict | None = None) -> str:
    """A short, human-readable preview of what a tool call will do — shown on the
    approval card so the reviewer sees the impact, not just the tool name."""
    args = args or {}

    def _g(*keys: str) -> str:
        for k in keys:
            v = args.get(k)
            if v not in (None, ""):
                return str(v)
        return "?"

    if tool_name == "create_pull_request":
        return f"Open PR “{_g('title')}” ({_g('head')} → {_g('base')})"
    if tool_name == "scale_deployment":
        return f"Scale {_g('deployment', 'name')} to {_g('replicas')} replica(s)"
    if tool_name == "rollback_deployment":
        return f"Roll back {_g('deployment', 'name')} to a previous revision"
    if tool_name == "restart_deployment":
        return f"Restart {_g('deployment', 'name')}"
    if tool_name == "resolve_incident":
        return f"Resolve incident {_g('incident_id', 'id')}"
    if tool_name == "acknowledge_incident":
        return f"Acknowledge incident {_g('incident_id', 'id')}"
    if tool_name == "add_incident_note":
        return f"Add note to incident {_g('incident_id', 'id')}"
    # argocd
    if tool_name == "sync_application":
        rev = _g('revision')
        rev_str = f" to revision {rev}" if rev != "?" else ""
        return f"Sync ArgoCD application {_g('name')}{rev_str}"
    if tool_name == "rollback_application":
        return f"Roll back ArgoCD application {_g('name')} to revision {_g('revision')}"
    # terraform
    if tool_name == "trigger_plan":
        spec = " (speculative)" if args.get("speculative") else ""
        return f"Trigger Terraform plan{spec} on workspace {_g('workspace')}"
    if tool_name == "trigger_apply":
        return f"Apply Terraform run {_g('run_id')}"
    # marketplace
    if tool_name == "install_agent":
        return f"Install marketplace agent {_g('agent_id')} into this deployment"
    if tool_name == "uninstall_agent":
        return f"Uninstall marketplace agent {_g('agent_id')}"
    if tool_name == "configure_agent":
        return f"Reconfigure marketplace agent {_g('agent_id')}"
    if tool_name == "enable_agent":
        return f"Enable marketplace agent {_g('agent_id')}"
    if tool_name == "disable_agent":
        return f"Disable marketplace agent {_g('agent_id')}"
    # runbooks
    if tool_name == "execute_runbook":
        return f"Execute runbook {_g('runbook_id', 'runbook', 'name')} against {_g('service', 'target', 'namespace')}"
    if tool_name == "approve_execution":
        return f"Approve runbook execution {_g('execution_id', 'id')} and let its remaining steps run"
    if tool_name == "reject_execution":
        return f"Reject runbook execution {_g('execution_id', 'id')}"
    # alerting
    if tool_name in ("create_alert_rule", "update_alert_rule"):
        verb = "Create" if tool_name.startswith("create") else "Update"
        return f"{verb} alert rule {_g('rule_id', 'name')} on {_g('service')}/{_g('metric')}"
    if tool_name == "delete_alert_rule":
        return f"Delete alert rule {_g('rule_id')} — its condition stops being detected"
    if tool_name == "disable_alert_rule":
        return f"Disable alert rule {_g('rule_id')} — it stops paging"
    if tool_name == "enable_alert_rule":
        return f"Enable alert rule {_g('rule_id')}"
    # opsgenie
    if tool_name == "close_alert":
        return f"Close alert {_g('alert_id', 'id')} (stops paging)"
    if tool_name == "escalate_alert":
        return f"Escalate alert {_g('alert_id', 'id')} to {_g('escalation', 'escalation_name', 'team')}"
    if tool_name == "create_alert":
        return f"Create alert “{_g('message', 'title')}” (pages on-call)"
    if tool_name == "acknowledge_alert":
        return f"Acknowledge alert {_g('alert_id', 'id')}"
    if tool_name == "assign_alert":
        return f"Assign alert {_g('alert_id', 'id')} to {_g('owner', 'assignee', 'user')}"
    # slack
    if tool_name in ("send_message", "post_incident_update"):
        return f"Post to Slack {_g('channel', 'channel_id')}: {_g('text', 'message', 'update')}"
    if tool_name in ("create_channel", "create_incident_channel"):
        return f"Create Slack channel {_g('name', 'incident_id', 'channel')}"
    # issue trackers
    if tool_name == "create_issue":
        return f"File issue “{_g('title', 'summary')}” in {_g('project', 'project_key', 'team_id', 'team')}"
    if tool_name == "update_issue":
        return f"Update issue {_g('issue_key', 'issue_id', 'id')}"
    if tool_name == "add_comment":
        return f"Comment on issue {_g('issue_key', 'issue_id', 'id')}"
    return tool_name


# The approve-class tools — the canonical set the graph's approval gate keys on.
# Re-exported as WRITE_TOOLS so existing imports keep working unchanged.
APPROVE_TOOLS: set[str] = {t for t, (d, _, _) in _POLICY.items() if d == "approve"}


def classified_tools() -> frozenset[str]:
    """Every tool this module has an explicit verdict for.

    Both tables are hand-maintained, and the default is fail-closed, so drift is
    silent in both directions: a genuinely read-only tool left out of `_READ_ONLY`
    quietly starts demanding human approval on every investigation that touches
    it, and a renamed tool leaves behind a verdict for something nobody ships.
    Neither shows up as a failure anywhere.

    This is exposed so the suite can derive the real MCP tool surface from the
    servers themselves and assert it equals this set. Deriving the allowlist that
    way at runtime is not an option — "the servers expose it" is not evidence
    that a tool has no side effects, and importing every server to answer a
    routing question would be the wrong trade — but comparing against it is
    exactly the check that keeps the hand-written verdicts honest.
    """
    return frozenset(_POLICY) | _READ_ONLY


# --------------------------------------------------------------------------- #
# Confidence gate — don't let a low-evidence investigation AUTO-write.
# Pure + testable; the single source of truth shared by the approval node (which
# surfaces the gate to the human) and the session (which enforces it against
# programmatic approvers).
# --------------------------------------------------------------------------- #
def evidence_confidence(evidence_count: int) -> Confidence:
    """Map how much telemetry the agent gathered before proposing a write to a
    coarse confidence tier. Deliberately conservative: a write proposed after one
    tool result is 'low'."""
    if evidence_count < 2:
        return "low"
    if evidence_count < 4:
        return "medium"
    return "high"


def auto_approvable(risk: str, confidence: str) -> bool:
    """True when a write of `risk` may be auto-approved at investigation `confidence`.
    High-risk needs high confidence; medium needs medium+; low needs any."""
    need = _MIN_AUTOAPPROVE_CONF.get(risk, "high")
    return _CONF_RANK.get(confidence, 0) >= _CONF_RANK[need]


def confidence_gate(tool_calls: list[dict] | None, evidence_count: int) -> dict:
    """Assess whether the pending write(s) may be auto-approved given the evidence.

    Returns {highest_risk, confidence, auto_approve_blocked, reason}. Only
    approve-class calls are gated — read/notify calls never block. Used both to
    warn the human (approval card) and to refuse a programmatic auto-approval."""
    confidence = evidence_confidence(evidence_count)
    approve_calls = [
        c for c in (tool_calls or []) if requires_approval(c.get("name", ""), c.get("args"))
    ]
    if not approve_calls:
        return {
            "highest_risk": "low",
            "confidence": confidence,
            "auto_approve_blocked": False,
            "reason": "",
        }
    highest = "low"
    for c in approve_calls:
        risk = classify(c.get("name", ""), c.get("args"))["risk"]
        if _CONF_RANK[risk] > _CONF_RANK[highest]:
            highest = risk
    blocked = not auto_approvable(highest, confidence)
    reason = (
        f"{highest}-risk write proposed on {confidence}-confidence evidence "
        f"({evidence_count} tool result{'' if evidence_count == 1 else 's'}); "
        "auto-approval refused — needs explicit human review."
        if blocked
        else ""
    )
    return {
        "highest_risk": highest,
        "confidence": confidence,
        "auto_approve_blocked": blocked,
        "reason": reason,
    }
