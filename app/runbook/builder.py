"""AI-powered runbook builder.

Generates runbooks from incident patterns and natural language descriptions.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from app.runbook.models import (
    ConditionOperator,
    Runbook,
    RunbookStep,
    StepCondition,
    StepType,
)

log = logging.getLogger("omnipotent.runbook.builder")


class RunbookBuilder:
    """Builds runbooks from templates and AI suggestions."""

    def __init__(self) -> None:
        self._step_counter = 0
        self._runbook_counter = 0
        self._templates = self._setup_templates()

    def _setup_templates(self) -> dict[str, dict[str, Any]]:
        """Set up runbook templates for common scenarios."""
        return {
            "redis_memory": {
                "name": "Redis High Memory",
                "description": "Handle Redis memory usage above threshold",
                "trigger": "redis_memory_usage > 80%",
                "severity": "medium",
                "steps": [
                    {
                        "name": "Diagnose memory usage",
                        "type": StepType.COMMAND,
                        "command": "redis-cli INFO memory",
                        "description": "Check Redis memory breakdown",
                        "auto": True,
                    },
                    {
                        "name": "Check for memory leaks",
                        "type": StepType.COMMAND,
                        "command": "redis-cli --bigkeys",
                        "description": "Find abnormally large keys",
                        "auto": True,
                    },
                    {
                        "name": "Check TTL coverage",
                        "type": StepType.QUERY,
                        "command": "SELECT key FROM keys WHERE ttl = -1 LIMIT 100",
                        "description": "Find keys without TTL",
                        "auto": True,
                    },
                    {
                        "name": "Evict stale keys",
                        "type": StepType.COMMAND,
                        "command": "redis-cli SCAN 0 MATCH 'cache:stale:*' COUNT 1000",
                        "description": "Remove stale cache keys",
                        "requires_approval": True,
                    },
                    {
                        "name": "Scale memory if needed",
                        "type": StepType.COMMAND,
                        "command": "kubectl patch statefulset redis --patch '{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"redis\",\"resources\":{\"limits\":{\"memory\":\"8Gi\"}}}]}}}}'",
                        "description": "Increase Redis memory limit",
                        "requires_approval": True,
                    },
                    {
                        "name": "Verify resolution",
                        "type": StepType.COMMAND,
                        "command": "redis-cli INFO memory | grep used_memory_human",
                        "description": "Confirm memory returned to normal",
                        "auto": True,
                    },
                ],
            },
            "database_failover": {
                "name": "Database Failover to Replica",
                "description": "Failover primary database to replica",
                "trigger": "database_primary_unhealthy",
                "severity": "high",
                "steps": [
                    {
                        "name": "Check replica lag",
                        "type": StepType.QUERY,
                        "command": "SELECT pg_last_wal_replay_lsn() - pg_last_wal_receive_lsn()",
                        "description": "Verify replica is caught up",
                        "success_criteria": "contains: 0",
                        "auto": True,
                    },
                    {
                        "name": "Set primary to read-only",
                        "type": StepType.COMMAND,
                        "command": "psql -c \"ALTER SYSTEM SET default_transaction_read_only = on;\"",
                        "description": "Prevent new writes to primary",
                        "rollback_command": "psql -c \"ALTER SYSTEM SET default_transaction_read_only = off;\"",
                    },
                    {
                        "name": "Drain active connections",
                        "type": StepType.COMMAND,
                        "command": "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'production' AND pid <> pg_backend_pid() AND state = 'idle'",
                        "description": "Close idle connections",
                        "timeout_seconds": 300,
                    },
                    {
                        "name": "Verify no active transactions",
                        "type": StepType.QUERY,
                        "command": "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'",
                        "description": "Ensure no active transactions",
                        "success_criteria": "equals: 0",
                    },
                    {
                        "name": "Promote replica",
                        "type": StepType.COMMAND,
                        "command": "pg_ctl promote -D /var/lib/postgresql/data",
                        "description": "Promote replica to primary",
                        "requires_approval": True,
                    },
                    {
                        "name": "Update connection strings",
                        "type": StepType.COMMAND,
                        "command": "kubectl patch configmap db-config --patch '{\"data\":{\"PRIMARY_HOST\":\"db-replica-01\"}}'",
                        "description": "Point applications to new primary",
                    },
                    {
                        "name": "Verify application health",
                        "type": StepType.COMMAND,
                        "command": "kubectl get pods -l app=api -o jsonpath='{.items[*].status.phase}'",
                        "description": "Confirm applications are healthy",
                        "success_criteria": "contains: Running",
                    },
                ],
            },
            "pod_crashloop": {
                "name": "Pod CrashLoopBackOff",
                "description": "Diagnose and remediate pod crash loops",
                "trigger": "pod_status = CrashLoopBackOff",
                "severity": "high",
                "steps": [
                    {
                        "name": "Get pod events",
                        "type": StepType.COMMAND,
                        "command": "kubectl describe pod ${POD_NAME} -n ${NAMESPACE}",
                        "description": "Check pod events for crash reason",
                        "auto": True,
                    },
                    {
                        "name": "Get pod logs",
                        "type": StepType.COMMAND,
                        "command": "kubectl logs ${POD_NAME} -n ${NAMESPACE} --previous",
                        "description": "Get logs from crashed container",
                        "auto": True,
                    },
                    {
                        "name": "Check resource limits",
                        "type": StepType.COMMAND,
                        "command": "kubectl top pod ${POD_NAME} -n ${NAMESPACE}",
                        "description": "Check if pod hit resource limits",
                        "auto": True,
                    },
                    {
                        "name": "Check for OOM",
                        "type": StepType.COMMAND,
                        "command": "kubectl get events -n ${NAMESPACE} --field-selector reason=OOMKilled",
                        "description": "Check for OOM kills",
                    },
                    {
                        "name": "Restart deployment",
                        "type": StepType.COMMAND,
                        "command": "kubectl rollout restart deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}",
                        "description": "Restart the deployment",
                        "requires_approval": True,
                    },
                    {
                        "name": "Verify pod health",
                        "type": StepType.COMMAND,
                        "command": "kubectl get pods -l app=${APP_NAME} -n ${NAMESPACE}",
                        "description": "Confirm pods are running",
                        "success_criteria": "contains: Running",
                    },
                ],
            },
            "high_latency": {
                "name": "High API Latency",
                "description": "Diagnose and remediate high API latency",
                "trigger": "api_latency_p99 > 1000ms",
                "severity": "medium",
                "steps": [
                    {
                        "name": "Check current latency",
                        "type": StepType.QUERY,
                        "command": "SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) FROM requests WHERE timestamp > NOW() - INTERVAL '5 minutes'",
                        "description": "Get current P99 latency",
                        "auto": True,
                    },
                    {
                        "name": "Identify slow endpoints",
                        "type": StepType.QUERY,
                        "command": "SELECT endpoint, AVG(duration_ms) FROM requests GROUP BY endpoint ORDER BY AVG(duration_ms) DESC LIMIT 10",
                        "description": "Find slowest endpoints",
                        "auto": True,
                    },
                    {
                        "name": "Check database connections",
                        "type": StepType.QUERY,
                        "command": "SELECT count(*) FROM pg_stat_activity",
                        "description": "Check connection pool status",
                        "auto": True,
                    },
                    {
                        "name": "Check for slow queries",
                        "type": StepType.QUERY,
                        "command": "SELECT query, calls, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 5",
                        "description": "Find slow database queries",
                    },
                    {
                        "name": "Scale up if needed",
                        "type": StepType.COMMAND,
                        "command": "kubectl scale deployment api --replicas=10",
                        "description": "Increase replica count",
                        "requires_approval": True,
                    },
                    {
                        "name": "Verify latency improved",
                        "type": StepType.WAIT,
                        "command": "",
                        "description": "Wait for metrics to update",
                        "metadata": {"wait_seconds": 60},
                    },
                ],
            },
        }

    def build_from_template(
        self,
        template_name: str,
        variables: dict[str, Any] | None = None,
    ) -> Runbook | None:
        """Build a runbook from a template."""
        template = self._templates.get(template_name)
        if not template:
            return None

        self._runbook_counter += 1
        runbook_id = f"rb_{self._runbook_counter}_{secrets.token_hex(4)}"

        steps: list[RunbookStep] = []
        auto_execute: list[str] = []

        for step_def in template["steps"]:
            step = self._create_step(step_def)
            steps.append(step)

            if step_def.get("auto", False):
                auto_execute.append(step.step_id)

        estimated_duration = sum(
            s.timeout_seconds / 60 for s in steps
        ) / 2

        return Runbook(
            runbook_id=runbook_id,
            name=template["name"],
            description=template["description"],
            trigger=template["trigger"],
            severity=template["severity"],
            steps=steps,
            auto_execute_steps=auto_execute,
            estimated_duration_minutes=int(estimated_duration),
        )

    def build_from_description(
        self,
        description: str,
        trigger: str = "",
    ) -> Runbook:
        """Build a runbook from a natural language description."""
        self._runbook_counter += 1
        runbook_id = f"rb_{self._runbook_counter}_{secrets.token_hex(4)}"

        steps = self._generate_steps_from_description(description)

        return Runbook(
            runbook_id=runbook_id,
            name=f"Runbook: {description[:50]}",
            description=description,
            trigger=trigger,
            severity="medium",
            steps=steps,
            estimated_duration_minutes=len(steps) * 5,
        )

    def _create_step(self, step_def: dict[str, Any]) -> RunbookStep:
        """Create a step from a definition."""
        self._step_counter += 1
        step_id = f"step_{self._step_counter}"

        conditions: list[StepCondition] = []
        for cond in step_def.get("conditions", []):
            conditions.append(StepCondition(
                variable=cond["variable"],
                operator=ConditionOperator(cond["operator"]),
                value=cond["value"],
                description=cond.get("description", ""),
            ))

        return RunbookStep(
            step_id=step_id,
            name=step_def["name"],
            description=step_def.get("description", ""),
            step_type=step_def.get("type", StepType.COMMAND),
            command=step_def.get("command", ""),
            script=step_def.get("script", ""),
            expected_output=step_def.get("expected_output", ""),
            success_criteria=step_def.get("success_criteria", ""),
            timeout_seconds=step_def.get("timeout_seconds", 300),
            retry_count=step_def.get("retry_count", 0),
            retry_delay_seconds=step_def.get("retry_delay_seconds", 10),
            requires_approval=step_def.get("requires_approval", False),
            approvers=step_def.get("approvers", []),
            conditions=conditions,
            on_failure=step_def.get("on_failure", "stop"),
            rollback_command=step_def.get("rollback_command", ""),
            output_variable=step_def.get("output_variable", ""),
            parallel_steps=step_def.get("parallel_steps", []),
            depends_on=step_def.get("depends_on", []),
            tags=step_def.get("tags", []),
            metadata=step_def.get("metadata", {}),
        )

    def _generate_steps_from_description(
        self,
        description: str,
    ) -> list[RunbookStep]:
        """Generate steps from a natural language description."""
        steps: list[RunbookStep] = []
        description_lower = description.lower()

        steps.append(self._create_step({
            "name": "Diagnose issue",
            "type": StepType.COMMAND,
            "command": "echo 'Gathering diagnostic information...'",
            "description": "Collect initial diagnostic data",
        }))

        if "redis" in description_lower or "cache" in description_lower:
            steps.append(self._create_step({
                "name": "Check Redis status",
                "type": StepType.COMMAND,
                "command": "redis-cli INFO",
                "description": "Get Redis server info",
            }))

        if "database" in description_lower or "db" in description_lower or "postgres" in description_lower:
            steps.append(self._create_step({
                "name": "Check database connections",
                "type": StepType.QUERY,
                "command": "SELECT count(*) FROM pg_stat_activity",
                "description": "Check active connections",
            }))

        if "memory" in description_lower:
            steps.append(self._create_step({
                "name": "Check memory usage",
                "type": StepType.COMMAND,
                "command": "free -m",
                "description": "Check system memory",
            }))

        if "kubernetes" in description_lower or "pod" in description_lower or "k8s" in description_lower:
            steps.append(self._create_step({
                "name": "Check pod status",
                "type": StepType.COMMAND,
                "command": "kubectl get pods",
                "description": "List pod status",
            }))

        steps.append(self._create_step({
            "name": "Apply remediation",
            "type": StepType.MANUAL,
            "command": "",
            "description": "Apply appropriate remediation based on diagnosis",
            "requires_approval": True,
        }))

        steps.append(self._create_step({
            "name": "Verify resolution",
            "type": StepType.COMMAND,
            "command": "echo 'Verification complete'",
            "description": "Confirm issue is resolved",
        }))

        return steps

    def add_conditional_branch(
        self,
        runbook: Runbook,
        after_step_id: str,
        condition: StepCondition,
        true_steps: list[dict[str, Any]],
        false_steps: list[dict[str, Any]] | None = None,
    ) -> Runbook:
        """Add a conditional branch to a runbook."""
        insert_index = -1
        for i, step in enumerate(runbook.steps):
            if step.step_id == after_step_id:
                insert_index = i + 1
                break

        if insert_index < 0:
            return runbook

        for step_def in true_steps:
            step_def["conditions"] = [condition]
            step = self._create_step(step_def)
            runbook.steps.insert(insert_index, step)
            insert_index += 1

        if false_steps:
            negated_condition = StepCondition(
                variable=condition.variable,
                operator=ConditionOperator.NOT_EQUALS if condition.operator == ConditionOperator.EQUALS else ConditionOperator.EQUALS,
                value=condition.value,
                description=f"NOT: {condition.description}",
            )

            for step_def in false_steps:
                step_def["conditions"] = [negated_condition]
                step = self._create_step(step_def)
                runbook.steps.insert(insert_index, step)
                insert_index += 1

        return runbook

    def get_template_names(self) -> list[str]:
        """Get available template names."""
        return list(self._templates.keys())

    def suggest_runbooks(
        self,
        incident_type: str,
    ) -> list[str]:
        """Suggest runbooks based on incident type."""
        suggestions: list[str] = []
        incident_lower = incident_type.lower()

        if "redis" in incident_lower or "cache" in incident_lower:
            suggestions.append("redis_memory")
        if "database" in incident_lower or "failover" in incident_lower:
            suggestions.append("database_failover")
        if "pod" in incident_lower or "crash" in incident_lower or "kubernetes" in incident_lower:
            suggestions.append("pod_crashloop")
        if "latency" in incident_lower or "slow" in incident_lower:
            suggestions.append("high_latency")

        return suggestions


_builder: RunbookBuilder | None = None


def get_runbook_builder() -> RunbookBuilder:
    """Get the default runbook builder."""
    global _builder
    if _builder is None:
        _builder = RunbookBuilder()
    return _builder
