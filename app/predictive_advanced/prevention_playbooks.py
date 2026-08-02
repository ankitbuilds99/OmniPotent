"""Prevention Playbook Auto-Suggestion.

Automatically suggests prevention playbooks based on predicted incidents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.predictive_advanced.prevention_playbooks")


class PlaybookCategory(str, Enum):
    MEMORY = "memory"
    CONNECTION = "connection"
    LATENCY = "latency"
    SCALING = "scaling"
    ROLLBACK = "rollback"
    RESTART = "restart"
    CONFIG = "config"
    NETWORK = "network"
    DATABASE = "database"
    CACHE = "cache"


class StepType(str, Enum):
    CHECK = "check"
    ACTION = "action"
    APPROVAL = "approval"
    WAIT = "wait"
    CONDITION = "condition"


class ApprovalRequirement(str, Enum):
    NONE = "none"
    OWNER = "owner"
    ONCALL = "oncall"
    MANAGER = "manager"
    SRE = "sre"


@dataclass
class PlaybookStep:
    """A step in a prevention playbook."""

    step_id: str
    name: str
    description: str
    step_type: StepType
    command: str = ""
    approval: ApprovalRequirement = ApprovalRequirement.NONE
    timeout_seconds: int = 300
    rollback_on_failure: bool = True
    conditions: dict[str, str] = field(default_factory=dict)
    expected_outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "type": self.step_type.value,
            "command": self.command,
            "approval": self.approval.value,
            "timeout_seconds": self.timeout_seconds,
            "rollback_on_failure": self.rollback_on_failure,
            "conditions": self.conditions,
            "expected_outcome": self.expected_outcome,
        }


@dataclass
class PreventionPlaybook:
    """A playbook for preventing a predicted incident."""

    playbook_id: str
    name: str
    description: str
    category: PlaybookCategory
    steps: list[PlaybookStep]
    target_services: list[str]
    estimated_time_minutes: int
    approval_steps_count: int
    success_rate: float
    risk_level: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook_id": self.playbook_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "steps": [s.to_dict() for s in self.steps],
            "target_services": self.target_services,
            "estimated_minutes": self.estimated_time_minutes,
            "total_steps": self.total_steps,
            "approval_steps": self.approval_steps_count,
            "success_rate": round(self.success_rate, 2),
            "risk_level": self.risk_level,
        }


@dataclass
class PlaybookSuggestion:
    """A suggested playbook with relevance scoring."""

    playbook: PreventionPlaybook
    relevance_score: float
    reason: str
    predicted_event: str
    time_available_minutes: float
    can_complete_in_time: bool
    auto_execute_safe: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook": self.playbook.to_dict(),
            "relevance_score": round(self.relevance_score, 2),
            "reason": self.reason,
            "predicted_event": self.predicted_event,
            "time_available_minutes": round(self.time_available_minutes, 1),
            "can_complete_in_time": self.can_complete_in_time,
            "auto_execute_safe": self.auto_execute_safe,
        }


class PreventionPlaybookEngine:
    """Engine for suggesting and managing prevention playbooks."""

    def __init__(self) -> None:
        self._playbooks: dict[str, PreventionPlaybook] = {}
        self._execution_history: dict[str, list[dict[str, Any]]] = {}
        self._init_default_playbooks()

    def _init_default_playbooks(self) -> None:
        """Initialize default prevention playbooks."""
        self._playbooks["memory_leak_mitigation"] = PreventionPlaybook(
            playbook_id="memory_leak_mitigation",
            name="Memory Leak Mitigation",
            description="Mitigate memory leaks before OOM occurs",
            category=PlaybookCategory.MEMORY,
            steps=[
                PlaybookStep(
                    step_id="step_1",
                    name="Capture Memory Profile",
                    description="Capture heap dump for later analysis",
                    step_type=StepType.ACTION,
                    command="kubectl exec {pod} -- jmap -dump:live,format=b,file=/tmp/heap.bin",
                    timeout_seconds=120,
                    expected_outcome="Heap dump saved successfully",
                ),
                PlaybookStep(
                    step_id="step_2",
                    name="Scale Up Pods",
                    description="Add more pods to distribute memory load",
                    step_type=StepType.ACTION,
                    command="kubectl scale deployment {service} --replicas={current_replicas + 2}",
                    timeout_seconds=180,
                    expected_outcome="Pods scaled from {current} to {target}",
                ),
                PlaybookStep(
                    step_id="step_3",
                    name="Enable GC Logging",
                    description="Enable detailed GC logging for diagnosis",
                    step_type=StepType.ACTION,
                    command="kubectl set env deployment/{service} JAVA_OPTS=-verbose:gc",
                    timeout_seconds=60,
                ),
                PlaybookStep(
                    step_id="step_4",
                    name="Rolling Restart",
                    description="Rolling restart to clear memory",
                    step_type=StepType.APPROVAL,
                    approval=ApprovalRequirement.ONCALL,
                    command="kubectl rollout restart deployment/{service}",
                    timeout_seconds=300,
                    expected_outcome="All pods restarted with fresh memory state",
                ),
                PlaybookStep(
                    step_id="step_5",
                    name="Verify Memory Stable",
                    description="Verify memory usage has stabilized",
                    step_type=StepType.CHECK,
                    command="kubectl top pods -l app={service}",
                    timeout_seconds=60,
                    conditions={"memory_percent": "< 70"},
                ),
            ],
            target_services=["*"],
            estimated_time_minutes=8,
            approval_steps_count=1,
            success_rate=0.89,
            risk_level="low",
        )

        self._playbooks["connection_pool_recovery"] = PreventionPlaybook(
            playbook_id="connection_pool_recovery",
            name="Connection Pool Recovery",
            description="Recover from connection pool exhaustion",
            category=PlaybookCategory.CONNECTION,
            steps=[
                PlaybookStep(
                    step_id="step_1",
                    name="Check Pool Status",
                    description="Get current connection pool metrics",
                    step_type=StepType.CHECK,
                    command="curl -s {service}:8080/actuator/metrics/hikaricp.connections.active",
                    timeout_seconds=30,
                ),
                PlaybookStep(
                    step_id="step_2",
                    name="Kill Idle Connections",
                    description="Force release of idle connections",
                    step_type=StepType.ACTION,
                    command="psql -c 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle'",
                    timeout_seconds=60,
                ),
                PlaybookStep(
                    step_id="step_3",
                    name="Rollback Deploy",
                    description="Rollback to previous known-good version",
                    step_type=StepType.APPROVAL,
                    approval=ApprovalRequirement.ONCALL,
                    command="kubectl rollout undo deployment/{service}",
                    timeout_seconds=180,
                ),
            ],
            target_services=["*"],
            estimated_time_minutes=5,
            approval_steps_count=1,
            success_rate=0.92,
            risk_level="low",
        )

        self._playbooks["latency_spike_mitigation"] = PreventionPlaybook(
            playbook_id="latency_spike_mitigation",
            name="Latency Spike Mitigation",
            description="Reduce latency before timeout cascade",
            category=PlaybookCategory.LATENCY,
            steps=[
                PlaybookStep(
                    step_id="step_1",
                    name="Enable Circuit Breaker",
                    description="Enable circuit breaker for slow downstream",
                    step_type=StepType.ACTION,
                    command="kubectl set env deployment/{service} CIRCUIT_BREAKER_ENABLED=true",
                    timeout_seconds=60,
                ),
                PlaybookStep(
                    step_id="step_2",
                    name="Reduce Timeout",
                    description="Reduce timeout to fail fast",
                    step_type=StepType.ACTION,
                    command="kubectl set env deployment/{service} REQUEST_TIMEOUT_MS=2000",
                    timeout_seconds=60,
                ),
                PlaybookStep(
                    step_id="step_3",
                    name="Scale Up",
                    description="Add capacity to handle backlog",
                    step_type=StepType.ACTION,
                    command="kubectl scale deployment {service} --replicas={current + 3}",
                    timeout_seconds=180,
                ),
                PlaybookStep(
                    step_id="step_4",
                    name="Enable Cache",
                    description="Enable response caching if available",
                    step_type=StepType.ACTION,
                    command="kubectl set env deployment/{service} CACHE_ENABLED=true",
                    timeout_seconds=60,
                ),
            ],
            target_services=["*"],
            estimated_time_minutes=6,
            approval_steps_count=0,
            success_rate=0.85,
            risk_level="low",
        )

        self._playbooks["emergency_rollback"] = PreventionPlaybook(
            playbook_id="emergency_rollback",
            name="Emergency Rollback",
            description="Emergency rollback to last known good state",
            category=PlaybookCategory.ROLLBACK,
            steps=[
                PlaybookStep(
                    step_id="step_1",
                    name="Check Rollback Safety",
                    description="Verify no migrations need reversal",
                    step_type=StepType.CHECK,
                    command="kubectl get cm {service}-migration-status -o jsonpath={.data.pending}",
                    timeout_seconds=30,
                    conditions={"pending_migrations": "0"},
                ),
                PlaybookStep(
                    step_id="step_2",
                    name="Execute Rollback",
                    description="Rollback to previous revision",
                    step_type=StepType.APPROVAL,
                    approval=ApprovalRequirement.ONCALL,
                    command="kubectl rollout undo deployment/{service}",
                    timeout_seconds=180,
                    expected_outcome="Deployment rolled back successfully",
                ),
                PlaybookStep(
                    step_id="step_3",
                    name="Wait for Stable",
                    description="Wait for rollout to complete",
                    step_type=StepType.WAIT,
                    command="kubectl rollout status deployment/{service} --timeout=300s",
                    timeout_seconds=300,
                ),
                PlaybookStep(
                    step_id="step_4",
                    name="Verify Health",
                    description="Verify service health after rollback",
                    step_type=StepType.CHECK,
                    command="curl -s {service}/health",
                    timeout_seconds=60,
                    conditions={"status": "healthy"},
                ),
            ],
            target_services=["*"],
            estimated_time_minutes=10,
            approval_steps_count=1,
            success_rate=0.97,
            risk_level="low",
        )

        self._playbooks["scale_auto_remediation"] = PreventionPlaybook(
            playbook_id="scale_auto_remediation",
            name="Auto-Scale Remediation",
            description="Automatically scale to handle predicted load",
            category=PlaybookCategory.SCALING,
            steps=[
                PlaybookStep(
                    step_id="step_1",
                    name="Calculate Target Replicas",
                    description="Calculate replicas needed based on prediction",
                    step_type=StepType.ACTION,
                    command="echo $(($(kubectl get deploy {service} -o jsonpath={.spec.replicas}) * 2))",
                    timeout_seconds=30,
                ),
                PlaybookStep(
                    step_id="step_2",
                    name="Scale Deployment",
                    description="Scale to target replicas",
                    step_type=StepType.ACTION,
                    command="kubectl scale deployment {service} --replicas={target}",
                    timeout_seconds=180,
                    expected_outcome="Scaled to {target} replicas",
                ),
                PlaybookStep(
                    step_id="step_3",
                    name="Wait for Ready",
                    description="Wait for all pods to be ready",
                    step_type=StepType.WAIT,
                    command="kubectl wait --for=condition=ready pod -l app={service} --timeout=300s",
                    timeout_seconds=300,
                ),
            ],
            target_services=["*"],
            estimated_time_minutes=5,
            approval_steps_count=0,
            success_rate=0.94,
            risk_level="low",
        )

        self._playbooks["database_connection_fix"] = PreventionPlaybook(
            playbook_id="database_connection_fix",
            name="Database Connection Fix",
            description="Fix database connection issues before timeout",
            category=PlaybookCategory.DATABASE,
            steps=[
                PlaybookStep(
                    step_id="step_1",
                    name="Check DB Connections",
                    description="Get current connection count",
                    step_type=StepType.CHECK,
                    command="psql -c 'SELECT count(*) FROM pg_stat_activity'",
                    timeout_seconds=30,
                ),
                PlaybookStep(
                    step_id="step_2",
                    name="Kill Long Queries",
                    description="Terminate queries running > 5 minutes",
                    step_type=StepType.ACTION,
                    command="psql -c 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query_start < now() - interval '5 minutes' AND state != 'idle'",
                    timeout_seconds=60,
                ),
                PlaybookStep(
                    step_id="step_3",
                    name="Reset Pool",
                    description="Reset application connection pool",
                    step_type=StepType.ACTION,
                    command="curl -X POST {service}/admin/reset-pool",
                    timeout_seconds=60,
                ),
            ],
            target_services=["*"],
            estimated_time_minutes=4,
            approval_steps_count=0,
            success_rate=0.88,
            risk_level="medium",
        )

    def suggest_playbooks(
        self,
        predicted_event: str,
        service: str,
        time_to_impact_minutes: float,
        symptoms: list[str] | None = None,
    ) -> list[PlaybookSuggestion]:
        """Suggest relevant playbooks for a predicted incident."""
        suggestions: list[PlaybookSuggestion] = []

        event_lower = predicted_event.lower()
        symptoms_text = " ".join(symptoms or []).lower()

        for playbook in self._playbooks.values():
            relevance = self._calculate_relevance(
                playbook,
                event_lower,
                symptoms_text,
                service,
            )

            if relevance < 0.5:
                continue

            can_complete = playbook.estimated_time_minutes < time_to_impact_minutes

            auto_safe = (
                playbook.approval_steps_count == 0 and
                playbook.risk_level == "low" and
                playbook.success_rate >= 0.9
            )

            reason = self._generate_reason(playbook, event_lower)

            suggestions.append(PlaybookSuggestion(
                playbook=playbook,
                relevance_score=relevance,
                reason=reason,
                predicted_event=predicted_event,
                time_available_minutes=time_to_impact_minutes,
                can_complete_in_time=can_complete,
                auto_execute_safe=auto_safe,
            ))

        suggestions.sort(key=lambda s: s.relevance_score, reverse=True)

        return suggestions[:5]

    def _calculate_relevance(
        self,
        playbook: PreventionPlaybook,
        event: str,
        symptoms: str,
        service: str,
    ) -> float:
        """Calculate playbook relevance to the predicted event."""
        score = 0.0

        category_keywords = {
            PlaybookCategory.MEMORY: ["memory", "oom", "heap", "gc", "leak"],
            PlaybookCategory.CONNECTION: ["connection", "pool", "socket", "leak"],
            PlaybookCategory.LATENCY: ["latency", "timeout", "slow", "delay"],
            PlaybookCategory.SCALING: ["load", "capacity", "scale", "traffic"],
            PlaybookCategory.ROLLBACK: ["deploy", "release", "regression", "bug"],
            PlaybookCategory.DATABASE: ["database", "db", "query", "connection"],
        }

        keywords = category_keywords.get(playbook.category, [])
        combined = f"{event} {symptoms}"

        matches = sum(1 for kw in keywords if kw in combined)
        score += min(0.5, matches * 0.15)

        if "*" in playbook.target_services or service in playbook.target_services:
            score += 0.2

        score += playbook.success_rate * 0.3

        return min(1.0, score)

    def _generate_reason(
        self,
        playbook: PreventionPlaybook,
        event: str,
    ) -> str:
        """Generate a human-readable reason for suggesting this playbook."""
        reasons = {
            PlaybookCategory.MEMORY: "Memory pressure detected - this playbook has 89% success rate for similar issues",
            PlaybookCategory.CONNECTION: "Connection issues detected - pooling recovery has worked in 92% of similar cases",
            PlaybookCategory.LATENCY: "Latency spike predicted - circuit breaker pattern recommended",
            PlaybookCategory.SCALING: "Load increase predicted - auto-scaling can prevent impact",
            PlaybookCategory.ROLLBACK: "Recent deploy suspected - rollback is fastest resolution",
            PlaybookCategory.DATABASE: "Database pressure detected - connection management recommended",
        }

        return reasons.get(
            playbook.category,
            f"Recommended based on {playbook.success_rate:.0%} historical success rate",
        )

    def get_playbook(self, playbook_id: str) -> PreventionPlaybook | None:
        """Get a playbook by ID."""
        return self._playbooks.get(playbook_id)

    def record_execution(
        self,
        org_id: str,
        playbook_id: str,
        service: str,
        success: bool,
        duration_minutes: float,
        notes: str = "",
    ) -> None:
        """Record a playbook execution."""
        if org_id not in self._execution_history:
            self._execution_history[org_id] = []

        self._execution_history[org_id].append({
            "playbook_id": playbook_id,
            "service": service,
            "success": success,
            "duration_minutes": duration_minutes,
            "notes": notes,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        })

        playbook = self._playbooks.get(playbook_id)
        if playbook:
            total = len([
                e for e in self._execution_history[org_id]
                if e["playbook_id"] == playbook_id
            ])
            successes = len([
                e for e in self._execution_history[org_id]
                if e["playbook_id"] == playbook_id and e["success"]
            ])
            playbook.success_rate = successes / total if total > 0 else 0.9

        log.info(
            "Recorded playbook execution: playbook=%s service=%s success=%s",
            playbook_id,
            service,
            success,
        )

    def create_custom_playbook(
        self,
        org_id: str,
        name: str,
        description: str,
        category: PlaybookCategory,
        steps: list[dict[str, Any]],
        target_services: list[str],
    ) -> PreventionPlaybook:
        """Create a custom playbook."""
        playbook_id = f"custom_{org_id}_{name.lower().replace(' ', '_')}"

        playbook_steps = []
        approval_count = 0

        for i, step_data in enumerate(steps):
            step_type = StepType(step_data.get("type", "action"))
            approval = ApprovalRequirement(step_data.get("approval", "none"))

            if approval != ApprovalRequirement.NONE:
                approval_count += 1

            playbook_steps.append(PlaybookStep(
                step_id=f"step_{i + 1}",
                name=step_data.get("name", f"Step {i + 1}"),
                description=step_data.get("description", ""),
                step_type=step_type,
                command=step_data.get("command", ""),
                approval=approval,
                timeout_seconds=step_data.get("timeout", 300),
                rollback_on_failure=step_data.get("rollback_on_failure", True),
                conditions=step_data.get("conditions", {}),
                expected_outcome=step_data.get("expected_outcome", ""),
            ))

        estimated_time = sum(s.timeout_seconds for s in playbook_steps) // 60 + 1

        playbook = PreventionPlaybook(
            playbook_id=playbook_id,
            name=name,
            description=description,
            category=category,
            steps=playbook_steps,
            target_services=target_services,
            estimated_time_minutes=estimated_time,
            approval_steps_count=approval_count,
            success_rate=0.9,
            risk_level="medium" if approval_count > 0 else "low",
        )

        self._playbooks[playbook_id] = playbook

        log.info("Created custom playbook: id=%s name=%s", playbook_id, name)

        return playbook


_engine: PreventionPlaybookEngine | None = None


def get_prevention_playbook_engine() -> PreventionPlaybookEngine:
    """Get the default prevention playbook engine."""
    global _engine
    if _engine is None:
        _engine = PreventionPlaybookEngine()
    return _engine
