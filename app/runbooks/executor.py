"""Runbook execution engine.

Executes runbook steps with approval workflows and real integrations
(subprocess, Kubernetes, HTTP, Slack).

Every step handler here used to return a canned success string — a runbook
"restarted" a service and "posted to Slack" without touching either, and the
agent then reported the remediation as done. Each handler now performs the
action for real, and when the credential or tooling it needs is absent it fails
with a message naming exactly what is missing. A step never reports success for
work that did not happen.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.runbooks.models import (
    DEFAULT_RUNBOOKS,
    ExecutionMode,
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepExecution,
    StepStatus,
    StepType,
)

log = logging.getLogger("omnipotent.runbooks")


class StepNotConfigured(RuntimeError):
    """A step cannot run because required config or tooling is absent.

    Raised instead of returning a plausible-looking success: the caller turns it
    into a failed step whose error names the missing setting.
    """


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _command_allowlist() -> set[str]:
    """Binaries this deployment is allowed to run from a runbook command step."""
    raw = get_settings().runbook_command_allowlist
    return {part.strip() for part in raw.split(",") if part.strip()}


def _kube_clients() -> tuple[Any, Any]:
    """Build (CoreV1Api, AppsV1Api) from the configured kubeconfig.

    Raises StepNotConfigured when there is no cluster to talk to, so a
    Kubernetes step fails loudly rather than pretending it rolled anything out.
    """
    s = get_settings()
    if not s.kube_config_path:
        raise StepNotConfigured(
            "Kubernetes steps need a cluster: set KUBE_CONFIG_PATH to a kubeconfig. "
            "Nothing was changed."
        )
    try:
        from kubernetes import client, config  # lazy: optional dependency
    except ImportError as e:  # pragma: no cover - depends on the install extras
        raise StepNotConfigured(
            "The `kubernetes` client is not installed; install the 'kubernetes' "
            "extra to run Kubernetes runbook steps. Nothing was changed."
        ) from e

    config.load_kube_config(config_file=s.kube_config_path)
    return client.CoreV1Api(), client.AppsV1Api()


class RunbookExecutor:
    """Executes runbooks with approval workflows.

    Handles step execution, approval gates, rollbacks,
    and notifications.
    """

    def __init__(self) -> None:
        self._runbooks: dict[str, Runbook] = {}
        self._executions: dict[str, RunbookExecution] = {}
        self._pending_approvals: dict[str, tuple[RunbookExecution, RunbookStep]] = {}
        self._load_default_runbooks()

    def _load_default_runbooks(self) -> None:
        """Load default runbook definitions."""
        for rb_data in DEFAULT_RUNBOOKS:
            runbook = Runbook.from_dict(rb_data)
            self._runbooks[runbook.id] = runbook

    def add_runbook(self, runbook: Runbook) -> None:
        """Add or update a runbook."""
        self._runbooks[runbook.id] = runbook
        log.info("Added runbook: %s", runbook.name)

    def remove_runbook(self, runbook_id: str) -> bool:
        """Remove a runbook."""
        if runbook_id in self._runbooks:
            del self._runbooks[runbook_id]
            return True
        return False

    def get_runbook(self, runbook_id: str) -> Runbook | None:
        """Get a runbook by ID from the in-process definitions."""
        return self._runbooks.get(runbook_id)

    async def resolve_runbook(self, runbook_id: str) -> Runbook | None:
        """Find a runbook, preferring the durable store over the built-in copies.

        The API writes runbooks to the database while this executor kept its own
        dict seeded from DEFAULT_RUNBOOKS, so a runbook created through the API
        was invisible to everything that went through the executor. The store is
        authoritative; the in-memory definitions remain a fallback for callers
        running without a reachable database.
        """
        try:
            from app.runbooks import store

            found = await store.get_runbook(runbook_id)
        except Exception as e:  # no database configured / not initialised
            log.warning(
                "Runbook store unavailable (%s: %s); falling back to built-in definitions",
                type(e).__name__, e,
            )
            return self._runbooks.get(runbook_id)
        return found or self._runbooks.get(runbook_id)

    async def resolve_runbooks(
        self,
        service: str | None = None,
        trigger: str | None = None,
    ) -> list[Runbook]:
        """List runbooks from the store, falling back to the built-in definitions."""
        try:
            from app.runbooks import store

            found = await store.list_runbooks(service=service, trigger=trigger, enabled=True)
        except Exception as e:
            log.warning(
                "Runbook store unavailable (%s: %s); falling back to built-in definitions",
                type(e).__name__, e,
            )
            return self.list_runbooks(service=service, trigger=trigger)
        return found or self.list_runbooks(service=service, trigger=trigger)

    def list_runbooks(
        self,
        service: str | None = None,
        trigger: str | None = None,
    ) -> list[Runbook]:
        """List runbooks with optional filtering."""
        runbooks = list(self._runbooks.values())

        if service:
            runbooks = [r for r in runbooks if r.service == "*" or r.service == service]

        if trigger:
            runbooks = [r for r in runbooks if r.trigger == trigger or r.trigger == "manual"]

        return [r for r in runbooks if r.enabled]

    def _substitute_variables(self, text: str, context: dict[str, Any]) -> str:
        """Substitute ${var} placeholders with context values."""
        def replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return str(context.get(var_name, match.group(0)))

        return re.sub(r"\$\{(\w+)\}", replace, text)

    def _substitute_step(self, step: RunbookStep, context: dict[str, Any]) -> RunbookStep:
        """Create a copy of step with substituted variables."""
        return RunbookStep(
            id=step.id,
            name=step.name,
            description=self._substitute_variables(step.description, context),
            step_type=step.step_type,
            action=self._substitute_variables(step.action, context),
            params={
                k: self._substitute_variables(str(v), context) if isinstance(v, str) else v
                for k, v in step.params.items()
            },
            timeout_seconds=step.timeout_seconds,
            continue_on_failure=step.continue_on_failure,
            requires_approval=step.requires_approval,
            rollback_step=step.rollback_step,
        )

    async def execute_step(
        self,
        step: RunbookStep,
        context: dict[str, Any],
    ) -> tuple[bool, str, str]:
        """Execute a single step for real.

        Returns (success, output, error). `error` is never empty on failure —
        it names the config or tooling the step needed and did not have.
        """
        step = self._substitute_step(step, context)

        try:
            if step.step_type == StepType.COMMAND:
                return await self._exec_command(step)
            elif step.step_type == StepType.KUBERNETES:
                return await self._exec_kubernetes(step, context)
            elif step.step_type == StepType.SLACK:
                return await self._exec_slack(step, context)
            elif step.step_type == StepType.WAIT:
                return await self._exec_wait(step)
            elif step.step_type == StepType.HTTP:
                return await self._exec_http(step)
            else:
                return False, "", (
                    f"Step type '{step.step_type.value}' has no executor in this "
                    f"build; nothing was run."
                )
        except asyncio.TimeoutError:
            return False, "", f"Step timed out after {step.timeout_seconds}s"
        except StepNotConfigured as e:
            return False, "", str(e)
        except Exception as e:
            return False, "", f"{type(e).__name__}: {e}"

    async def _exec_command(self, step: RunbookStep) -> tuple[bool, str, str]:
        """Run the step's command as a real subprocess.

        No shell is involved (the action is split with shlex and exec'd
        directly), and the binary must appear in RUNBOOK_COMMAND_ALLOWLIST —
        a runbook is arbitrary text that reaches production, so an empty
        allowlist means "this deployment runs no commands" and says so.
        """
        argv = shlex.split(step.action)
        if not argv:
            return False, "", "Command step has no action to run"

        allowed = _command_allowlist()
        if not allowed:
            return False, "", (
                "Command steps are disabled: set RUNBOOK_COMMAND_ALLOWLIST to the "
                "binaries this deployment may run (e.g. "
                "RUNBOOK_COMMAND_ALLOWLIST=kubectl,redis-cli). "
                f"'{argv[0]}' was not run."
            )
        binary = os.path.basename(argv[0])
        if binary not in allowed:
            return False, "", (
                f"'{binary}' is not in RUNBOOK_COMMAND_ALLOWLIST "
                f"({', '.join(sorted(allowed))}); it was not run."
            )

        resolved = shutil.which(argv[0])
        if resolved is None:
            return False, "", f"'{argv[0]}' was not found on PATH; it was not run."

        log.info("Executing command: %s", step.action)
        proc = await asyncio.create_subprocess_exec(
            resolved,
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(), timeout=step.timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        out = raw_out.decode(errors="replace").strip()
        err = raw_err.decode(errors="replace").strip()
        if proc.returncode != 0:
            return False, out, err or f"'{step.action}' exited with code {proc.returncode}"
        return True, out or f"'{step.action}' exited 0", ""

    def _namespace(self, step: RunbookStep, context: dict[str, Any]) -> str:
        ns = str(step.params.get("namespace") or context.get("namespace") or "").strip()
        return ns or get_settings().kube_namespace or "default"

    async def _exec_kubernetes(
        self,
        step: RunbookStep,
        context: dict[str, Any],
    ) -> tuple[bool, str, str]:
        """Perform a Kubernetes operation against the configured cluster."""
        action = step.action
        params = step.params
        ns = self._namespace(step, context)
        log.info("Kubernetes action: %s namespace=%s params=%s", action, ns, params)

        core, apps = await asyncio.to_thread(_kube_clients)

        if action == "rollout_restart":
            deployment = self._require(params, "deployment")
            stamp = datetime.now(timezone.utc).isoformat()
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {"kubectl.kubernetes.io/restartedAt": stamp}
                        }
                    }
                }
            }
            await asyncio.to_thread(apps.patch_namespaced_deployment, deployment, ns, patch)
            return True, f"Rollout restart triggered for {ns}/{deployment} at {stamp}", ""

        if action == "wait_rollout":
            deployment = self._require(params, "deployment")
            timeout = _as_int(params.get("timeout"), step.timeout_seconds)
            return await self._wait_rollout(apps, deployment, ns, timeout)

        if action == "scale":
            deployment = self._require(params, "deployment")
            current = await asyncio.to_thread(apps.read_namespaced_deployment, deployment, ns)
            replicas = self._resolve_replicas(
                params.get("replicas"), current.spec.replicas or 0
            )
            await asyncio.to_thread(
                apps.patch_namespaced_deployment_scale,
                deployment,
                ns,
                {"spec": {"replicas": replicas}},
            )
            return True, f"Scaled {ns}/{deployment} to {replicas} replicas", ""

        if action == "get_deployment":
            deployment = self._require(params, "deployment")
            dep = await asyncio.to_thread(apps.read_namespaced_deployment, deployment, ns)
            st = dep.status
            return True, (
                f"{ns}/{deployment}: desired={dep.spec.replicas} "
                f"ready={st.ready_replicas or 0} updated={st.updated_replicas or 0} "
                f"available={st.available_replicas or 0}"
            ), ""

        if action == "rollout_history":
            deployment = self._require(params, "deployment")
            revisions = await asyncio.to_thread(self._rollout_history, apps, deployment, ns)
            if not revisions:
                return False, "", f"No revision history found for {ns}/{deployment}"
            listed = ", ".join(f"rev{r}: {img}" for r, img in revisions)
            return True, f"Revision history for {ns}/{deployment}: {listed}", ""

        if action == "rollout_undo":
            deployment = self._require(params, "deployment")
            return await asyncio.to_thread(self._rollout_undo, apps, deployment, ns)

        if action == "cordon_node":
            node = self._require(params, "node")
            await asyncio.to_thread(
                core.patch_node, node, {"spec": {"unschedulable": True}}
            )
            return True, f"Node {node} cordoned (unschedulable)", ""

        if action == "drain_node":
            node = self._require(params, "node")
            grace = _as_int(params.get("grace_period"), 30)
            return await self._drain_node(core, node, grace)

        return False, "", f"Unknown Kubernetes action: {action}"

    @staticmethod
    def _require(params: dict[str, Any], key: str) -> str:
        value = str(params.get(key, "")).strip()
        if not value or value.startswith("${"):
            raise StepNotConfigured(
                f"Step is missing the '{key}' parameter (got {params.get(key)!r}); "
                f"nothing was run."
            )
        return value

    @staticmethod
    def _resolve_replicas(raw: Any, current: int) -> int:
        """Resolve a replica count, supporting the relative '+2' / '-1' forms."""
        text = str(raw if raw is not None else "").strip()
        if not text:
            raise StepNotConfigured("Scale step is missing the 'replicas' parameter")
        if text[0] in "+-":
            return max(0, current + _as_int(text, 0))
        value = _as_int(text, -1)
        if value < 0:
            raise StepNotConfigured(f"Scale step has a non-numeric replicas value: {raw!r}")
        return value

    async def _wait_rollout(
        self, apps: Any, deployment: str, ns: str, timeout: int
    ) -> tuple[bool, str, str]:
        """Poll the deployment until its rollout completes or the timeout expires."""
        deadline = asyncio.get_running_loop().time() + max(1, timeout)
        last = ""
        while True:
            dep = await asyncio.to_thread(apps.read_namespaced_deployment, deployment, ns)
            desired = dep.spec.replicas or 0
            st = dep.status
            updated = st.updated_replicas or 0
            ready = st.ready_replicas or 0
            unavailable = st.unavailable_replicas or 0
            last = (
                f"{ns}/{deployment}: desired={desired} updated={updated} "
                f"ready={ready} unavailable={unavailable}"
            )
            if updated >= desired and ready >= desired and unavailable == 0:
                return True, f"Rollout complete for {last}", ""
            if asyncio.get_running_loop().time() >= deadline:
                return False, last, (
                    f"Rollout of {ns}/{deployment} did not complete within {timeout}s"
                )
            await asyncio.sleep(2)

    @staticmethod
    def _rollout_history(apps: Any, deployment: str, ns: str) -> list[tuple[int, str]]:
        """Revision -> image for each ReplicaSet owned by the deployment."""
        dep = apps.read_namespaced_deployment(deployment, ns)
        match = (dep.spec.selector.match_labels or {}) if dep.spec.selector else {}
        selector = ",".join(f"{k}={v}" for k, v in match.items())
        sets = apps.list_namespaced_replica_set(ns, label_selector=selector).items
        out: list[tuple[int, str]] = []
        for rs in sets:
            revision = (rs.metadata.annotations or {}).get(
                "deployment.kubernetes.io/revision"
            )
            containers = rs.spec.template.spec.containers if rs.spec.template else []
            if revision and containers:
                out.append((_as_int(revision, 0), containers[0].image))
        return sorted(out)

    def _rollout_undo(self, apps: Any, deployment: str, ns: str) -> tuple[bool, str, str]:
        """Restore the deployment's previous ReplicaSet pod template.

        This is what `kubectl rollout undo` does; the previous build declined to
        do it live and returned "Rolled back <x>" anyway.
        """
        dep = apps.read_namespaced_deployment(deployment, ns)
        match = (dep.spec.selector.match_labels or {}) if dep.spec.selector else {}
        selector = ",".join(f"{k}={v}" for k, v in match.items())
        sets = apps.list_namespaced_replica_set(ns, label_selector=selector).items
        ranked = sorted(
            (
                (_as_int((rs.metadata.annotations or {}).get(
                    "deployment.kubernetes.io/revision"), 0), rs)
                for rs in sets
                if (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
            ),
            key=lambda pair: pair[0],
        )
        if len(ranked) < 2:
            return False, "", (
                f"{ns}/{deployment} has no previous revision to roll back to "
                f"({len(ranked)} revision(s) found)"
            )
        revision, previous = ranked[-2]
        apps.patch_namespaced_deployment(
            deployment, ns, {"spec": {"template": previous.spec.template}}
        )
        image = previous.spec.template.spec.containers[0].image
        return True, f"Rolled {ns}/{deployment} back to revision {revision} ({image})", ""

    async def _drain_node(
        self, core: Any, node: str, grace_period: int
    ) -> tuple[bool, str, str]:
        """Cordon the node and evict every pod it is not required to keep."""
        await asyncio.to_thread(core.patch_node, node, {"spec": {"unschedulable": True}})
        pods = await asyncio.to_thread(
            core.list_pod_for_all_namespaces, field_selector=f"spec.nodeName={node}"
        )

        evicted: list[str] = []
        skipped: list[str] = []
        failures: list[str] = []
        for pod in pods.items:
            owners = pod.metadata.owner_references or []
            if any(o.kind == "DaemonSet" for o in owners):
                skipped.append(f"{pod.metadata.namespace}/{pod.metadata.name} (DaemonSet)")
                continue
            body = {
                "apiVersion": "policy/v1",
                "kind": "Eviction",
                "metadata": {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                },
                "deleteOptions": {"gracePeriodSeconds": grace_period},
            }
            try:
                await asyncio.to_thread(
                    core.create_namespaced_pod_eviction,
                    pod.metadata.name,
                    pod.metadata.namespace,
                    body,
                )
                evicted.append(f"{pod.metadata.namespace}/{pod.metadata.name}")
            except Exception as e:  # one pod's PDB should not hide the rest
                failures.append(f"{pod.metadata.namespace}/{pod.metadata.name}: {e}")

        summary = (
            f"Node {node} cordoned; evicted {len(evicted)} pod(s)"
            + (f", skipped {len(skipped)}" if skipped else "")
        )
        if failures:
            return False, summary, "Could not evict: " + "; ".join(failures)
        return True, summary, ""

    async def _exec_slack(
        self,
        step: RunbookStep,
        context: dict[str, Any],
    ) -> tuple[bool, str, str]:
        """Post the step's message to Slack."""
        from app.integrations.slack_notify import is_slack_configured, send_quick_message

        message = self._substitute_variables(str(step.params.get("message", "")), context)
        if not message.strip():
            return False, "", "Slack step has no 'message' parameter; nothing was posted."

        if not is_slack_configured():
            return False, "", (
                "Slack is not configured: set SLACK_WEBHOOK_URL, or SLACK_BOT_TOKEN "
                "plus SLACK_CHANNEL. The message was not posted."
            )

        channel = str(step.params.get("channel", "")).strip() or None
        result = await send_quick_message(message, channel=channel)
        if result.get("success"):
            return True, f"Posted to Slack: {message}", ""
        return False, "", (
            f"Slack rejected the message: {result.get('message', 'unknown error')}"
        )

    async def _exec_wait(self, step: RunbookStep) -> tuple[bool, str, str]:
        """Wait for the requested duration, bounded by the step timeout."""
        seconds = max(0, _as_int(step.params.get("seconds"), 10))
        slept = min(seconds, max(0, step.timeout_seconds))
        log.info("Waiting %d seconds", slept)
        await asyncio.sleep(slept)
        if slept != seconds:
            return True, (
                f"Waited {slept} seconds (capped by the step's "
                f"{step.timeout_seconds}s timeout; {seconds} were requested)"
            ), ""
        return True, f"Waited {slept} seconds", ""

    async def _exec_http(self, step: RunbookStep) -> tuple[bool, str, str]:
        """Make the step's HTTP request and report what came back."""
        url = str(step.params.get("url", "")).strip()
        method = str(step.params.get("method", "GET")).upper()
        if not url:
            return False, "", "HTTP step has no 'url' parameter; no request was made."
        if not url.startswith(("http://", "https://")):
            return False, "", (
                f"HTTP step url must be an absolute http(s) URL, got {url!r}; "
                f"no request was made."
            )

        headers = step.params.get("headers") or {}
        body = step.params.get("body")
        log.info("HTTP %s %s", method, url)
        try:
            async with httpx.AsyncClient(
                timeout=step.timeout_seconds, follow_redirects=True
            ) as client:
                resp = await client.request(
                    method,
                    url,
                    headers={str(k): str(v) for k, v in dict(headers).items()},
                    json=body if isinstance(body, (dict, list)) else None,
                    content=body if isinstance(body, str) else None,
                )
        except httpx.HTTPError as e:
            return False, "", f"HTTP {method} {url} failed: {type(e).__name__}: {e}"

        excerpt = resp.text[:2000]
        summary = f"HTTP {method} {url} -> {resp.status_code}"
        if resp.is_success:
            return True, f"{summary}\n{excerpt}".rstrip(), ""
        return False, excerpt, summary

    async def execute(
        self,
        runbook_id: str,
        context: dict[str, Any],
        triggered_by: str = "manual",
        skip_approval: bool = False,
    ) -> RunbookExecution:
        """Execute a runbook.

        Args:
            runbook_id: ID of the runbook to execute
            context: Variables for substitution (service, namespace, etc.)
            triggered_by: Who/what triggered this execution
            skip_approval: Skip approval gates (for testing)

        Returns:
            RunbookExecution record
        """
        runbook = await self.resolve_runbook(runbook_id)
        if not runbook:
            raise ValueError(f"Runbook '{runbook_id}' not found")

        if not runbook.enabled:
            raise ValueError(f"Runbook '{runbook_id}' is disabled")

        execution = RunbookExecution(
            id=str(uuid.uuid4()),
            runbook_id=runbook.id,
            runbook_name=runbook.name,
            status=StepStatus.RUNNING,
            triggered_by=triggered_by,
            started_at=datetime.now(),
            context=context,
        )
        self._executions[execution.id] = execution

        log.info(
            "Starting runbook execution: %s (id=%s) triggered_by=%s",
            runbook.name, execution.id, triggered_by
        )

        for step in runbook.steps:
            step_exec = StepExecution(
                step_id=step.id,
                step_name=step.name,
                status=StepStatus.PENDING,
                started_at=datetime.now(),
            )

            if step.requires_approval and not skip_approval:
                if runbook.execution_mode == ExecutionMode.REQUIRES_APPROVAL:
                    step_exec.status = StepStatus.WAITING_APPROVAL
                    execution.steps.append(step_exec)
                    execution.status = StepStatus.WAITING_APPROVAL
                    self._pending_approvals[execution.id] = (execution, step)
                    log.info("Step %s waiting for approval", step.name)
                    return execution

            step_exec.status = StepStatus.RUNNING
            success, output, error = await self.execute_step(step, context)

            step_exec.completed_at = datetime.now()
            step_exec.output = output
            step_exec.error = error

            if success:
                step_exec.status = StepStatus.SUCCESS
                log.info("Step %s completed successfully", step.name)
            else:
                step_exec.status = StepStatus.FAILED
                log.error("Step %s failed: %s", step.name, error)

                if not step.continue_on_failure:
                    execution.steps.append(step_exec)
                    execution.status = StepStatus.FAILED
                    execution.completed_at = datetime.now()
                    return execution

            execution.steps.append(step_exec)

        execution.status = StepStatus.SUCCESS
        execution.completed_at = datetime.now()
        log.info("Runbook execution completed: %s", execution.id)

        return execution

    async def approve(self, execution_id: str) -> RunbookExecution | None:
        """Approve and continue a paused execution."""
        if execution_id not in self._pending_approvals:
            return None

        execution, step = self._pending_approvals.pop(execution_id)
        runbook = await self.resolve_runbook(execution.runbook_id)

        if not runbook:
            return None

        log.info("Approval granted for execution %s, continuing from step %s",
                 execution_id, step.name)

        continue_from = None
        for i, s in enumerate(runbook.steps):
            if s.id == step.id:
                continue_from = i
                break

        if continue_from is None:
            return None

        execution.status = StepStatus.RUNNING

        for step in runbook.steps[continue_from:]:
            step_exec = StepExecution(
                step_id=step.id,
                step_name=step.name,
                status=StepStatus.RUNNING,
                started_at=datetime.now(),
            )

            success, output, error = await self.execute_step(step, execution.context)

            step_exec.completed_at = datetime.now()
            step_exec.output = output
            step_exec.error = error

            if success:
                step_exec.status = StepStatus.SUCCESS
            else:
                step_exec.status = StepStatus.FAILED
                if not step.continue_on_failure:
                    execution.steps.append(step_exec)
                    execution.status = StepStatus.FAILED
                    execution.completed_at = datetime.now()
                    return execution

            execution.steps.append(step_exec)

        execution.status = StepStatus.SUCCESS
        execution.completed_at = datetime.now()
        return execution

    async def reject(self, execution_id: str) -> RunbookExecution | None:
        """Reject a pending execution."""
        if execution_id not in self._pending_approvals:
            return None

        execution, _ = self._pending_approvals.pop(execution_id)
        execution.status = StepStatus.SKIPPED
        execution.completed_at = datetime.now()
        log.info("Execution %s rejected", execution_id)
        return execution

    def get_execution(self, execution_id: str) -> RunbookExecution | None:
        """Get an execution by ID."""
        return self._executions.get(execution_id)

    def list_executions(
        self,
        runbook_id: str | None = None,
        status: StepStatus | None = None,
        limit: int = 50,
    ) -> list[RunbookExecution]:
        """List executions with optional filtering."""
        executions = list(self._executions.values())

        if runbook_id:
            executions = [e for e in executions if e.runbook_id == runbook_id]

        if status:
            executions = [e for e in executions if e.status == status]

        return sorted(
            executions,
            key=lambda e: e.started_at,
            reverse=True,
        )[:limit]

    def get_pending_approvals(self) -> list[RunbookExecution]:
        """Get all executions waiting for approval."""
        return [e for e, _ in self._pending_approvals.values()]


_executor: RunbookExecutor | None = None


def get_executor() -> RunbookExecutor:
    """Get the singleton executor instance."""
    global _executor
    if _executor is None:
        _executor = RunbookExecutor()
    return _executor


async def run_execution(execution_id: str, approved_through: int = -1) -> None:
    """Drive a stored execution to a terminal state, persisting every step.

    The API used to create an execution row and return: four declared steps,
    zero StepExecutions, status stuck on `pending` forever. This is the piece
    that actually runs it.

    Resumption is derived from the durable record rather than from process
    memory: the next step to run is `len(execution.steps)`, because a step is
    appended only once it has finished. So a run that paused for approval can be
    resumed by a different worker, or after a restart.

    `approved_through` is the index of the last step a human has already
    approved. Steps after it that carry `requires_approval` park the execution in
    WAITING_APPROVAL instead of running; without it, approving would re-enter the
    same gate forever.
    """
    from app.runbooks import store

    execution = await store.get_execution(execution_id)
    if execution is None:
        log.warning("run_execution: execution %s no longer exists", execution_id)
        return

    runbook = await store.get_runbook(execution.runbook_id)
    if runbook is None:
        log.error(
            "run_execution: runbook %s for execution %s no longer exists",
            execution.runbook_id, execution_id,
        )
        await store.set_execution_status(execution_id, StepStatus.FAILED)
        return

    executor = get_executor()
    context = dict(execution.context)
    failed = False

    for index in range(len(execution.steps), len(runbook.steps)):
        step = runbook.steps[index]

        if (
            step.requires_approval
            and index > approved_through
            and runbook.execution_mode == ExecutionMode.REQUIRES_APPROVAL
        ):
            log.info("Execution %s waiting for approval of step %s", execution_id, step.id)
            await store.set_execution_status(execution_id, StepStatus.WAITING_APPROVAL)
            return

        started = datetime.now(timezone.utc)
        success, output, error = await executor.execute_step(step, context)
        record = StepExecution(
            step_id=step.id,
            step_name=step.name,
            status=StepStatus.SUCCESS if success else StepStatus.FAILED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            output=output,
            error=error,
        )
        await store.append_step_result(execution_id, record)

        if not success:
            failed = True
            log.error("Execution %s step %s failed: %s", execution_id, step.id, error)
            if not step.continue_on_failure:
                await store.set_execution_status(execution_id, StepStatus.FAILED)
                return

    # A tolerated failure is still a failure: reporting "success" for a run that
    # contains a failed step is exactly the false green this engine is here to
    # stop. `continue_on_failure` decides whether later steps run, not how the
    # run is recorded.
    await store.set_execution_status(
        execution_id, StepStatus.FAILED if failed else StepStatus.SUCCESS
    )
