"""Goal Decomposer for Planning Layer.

Uses LLM to decompose high-level goals into actionable sub-goals
with dependency tracking and priority assignment.
"""

from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid

from app.planning.task_graph import TaskGraph, TaskType, TaskPriority

log = logging.getLogger("devcopilot.planning.goal_decomposer")


class GoalCategory(str, Enum):
    """Categories of goals."""
    INCIDENT_RESPONSE = "incident_response"
    DEPLOYMENT = "deployment"
    INVESTIGATION = "investigation"
    OPTIMIZATION = "optimization"
    MAINTENANCE = "maintenance"
    MIGRATION = "migration"
    SECURITY = "security"
    MONITORING = "monitoring"


@dataclass
class Goal:
    """A high-level goal to be decomposed."""
    id: str
    description: str
    category: GoalCategory = GoalCategory.INVESTIGATION
    context: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.MEDIUM
    deadline: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "category": self.category.value,
            "context": self.context,
            "constraints": self.constraints,
            "success_criteria": self.success_criteria,
            "priority": self.priority.value,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class SubGoal:
    """A decomposed sub-goal."""
    id: str
    name: str
    description: str
    task_type: TaskType
    priority: TaskPriority
    dependencies: list[str] = field(default_factory=list)
    estimated_duration_minutes: int = 30
    required_tools: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    success_criteria: str = ""
    can_parallelize: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type.value,
            "priority": self.priority.value,
            "dependencies": self.dependencies,
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "required_tools": self.required_tools,
            "inputs": self.inputs,
            "success_criteria": self.success_criteria,
            "can_parallelize": self.can_parallelize,
        }


@dataclass
class DecompositionResult:
    """Result of goal decomposition."""
    goal_id: str
    sub_goals: list[SubGoal]
    task_graph: TaskGraph
    estimated_total_minutes: int
    parallelization_factor: float
    confidence: float
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "sub_goals": [sg.to_dict() for sg in self.sub_goals],
            "task_graph_id": self.task_graph.id,
            "estimated_total_minutes": self.estimated_total_minutes,
            "parallelization_factor": self.parallelization_factor,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


class GoalDecomposer:
    """Decomposes high-level goals into task graphs.

    Uses LLM for intelligent decomposition with domain knowledge
    about DevOps, incident response, and infrastructure operations.
    """

    DECOMPOSITION_TEMPLATES = {
        GoalCategory.INCIDENT_RESPONSE: [
            SubGoal(
                id="assess",
                name="Initial Assessment",
                description="Assess incident scope and severity",
                task_type=TaskType.INVESTIGATION,
                priority=TaskPriority.CRITICAL,
                estimated_duration_minutes=5,
                required_tools=["monitoring", "logs"],
            ),
            SubGoal(
                id="notify",
                name="Stakeholder Notification",
                description="Notify relevant stakeholders",
                task_type=TaskType.COMMUNICATION,
                priority=TaskPriority.HIGH,
                dependencies=["assess"],
                estimated_duration_minutes=5,
            ),
            SubGoal(
                id="investigate",
                name="Root Cause Investigation",
                description="Investigate root cause",
                task_type=TaskType.DIAGNOSIS,
                priority=TaskPriority.HIGH,
                dependencies=["assess"],
                estimated_duration_minutes=30,
                required_tools=["logs", "traces", "metrics"],
            ),
            SubGoal(
                id="mitigate",
                name="Apply Mitigation",
                description="Apply mitigation measures",
                task_type=TaskType.MITIGATION,
                priority=TaskPriority.CRITICAL,
                dependencies=["investigate"],
                estimated_duration_minutes=15,
                can_parallelize=False,
            ),
            SubGoal(
                id="verify",
                name="Verify Resolution",
                description="Verify the issue is resolved",
                task_type=TaskType.VERIFICATION,
                priority=TaskPriority.HIGH,
                dependencies=["mitigate"],
                estimated_duration_minutes=10,
            ),
            SubGoal(
                id="document",
                name="Document Incident",
                description="Create incident documentation",
                task_type=TaskType.DOCUMENTATION,
                priority=TaskPriority.MEDIUM,
                dependencies=["verify"],
                estimated_duration_minutes=30,
            ),
        ],
        GoalCategory.DEPLOYMENT: [
            SubGoal(
                id="prepare",
                name="Prepare Deployment",
                description="Prepare deployment artifacts and configuration",
                task_type=TaskType.CUSTOM,
                priority=TaskPriority.HIGH,
                estimated_duration_minutes=10,
                required_tools=["ci_cd"],
            ),
            SubGoal(
                id="validate",
                name="Pre-deployment Validation",
                description="Validate deployment prerequisites",
                task_type=TaskType.VERIFICATION,
                priority=TaskPriority.HIGH,
                dependencies=["prepare"],
                estimated_duration_minutes=5,
            ),
            SubGoal(
                id="deploy_staging",
                name="Deploy to Staging",
                description="Deploy to staging environment",
                task_type=TaskType.CUSTOM,
                priority=TaskPriority.HIGH,
                dependencies=["validate"],
                estimated_duration_minutes=15,
                can_parallelize=False,
            ),
            SubGoal(
                id="test_staging",
                name="Test Staging",
                description="Run tests on staging",
                task_type=TaskType.VERIFICATION,
                priority=TaskPriority.HIGH,
                dependencies=["deploy_staging"],
                estimated_duration_minutes=20,
            ),
            SubGoal(
                id="deploy_prod",
                name="Deploy to Production",
                description="Deploy to production environment",
                task_type=TaskType.CUSTOM,
                priority=TaskPriority.CRITICAL,
                dependencies=["test_staging"],
                estimated_duration_minutes=15,
                can_parallelize=False,
            ),
            SubGoal(
                id="monitor",
                name="Post-deployment Monitoring",
                description="Monitor deployment health",
                task_type=TaskType.MONITORING,
                priority=TaskPriority.HIGH,
                dependencies=["deploy_prod"],
                estimated_duration_minutes=30,
            ),
        ],
        GoalCategory.INVESTIGATION: [
            SubGoal(
                id="gather_context",
                name="Gather Context",
                description="Collect relevant information and context",
                task_type=TaskType.INVESTIGATION,
                priority=TaskPriority.HIGH,
                estimated_duration_minutes=10,
            ),
            SubGoal(
                id="analyze_logs",
                name="Analyze Logs",
                description="Analyze relevant log data",
                task_type=TaskType.INVESTIGATION,
                priority=TaskPriority.HIGH,
                dependencies=["gather_context"],
                estimated_duration_minutes=20,
                required_tools=["logs"],
            ),
            SubGoal(
                id="analyze_metrics",
                name="Analyze Metrics",
                description="Analyze relevant metrics",
                task_type=TaskType.INVESTIGATION,
                priority=TaskPriority.HIGH,
                dependencies=["gather_context"],
                estimated_duration_minutes=15,
                required_tools=["metrics"],
            ),
            SubGoal(
                id="analyze_traces",
                name="Analyze Traces",
                description="Analyze distributed traces",
                task_type=TaskType.INVESTIGATION,
                priority=TaskPriority.MEDIUM,
                dependencies=["gather_context"],
                estimated_duration_minutes=15,
                required_tools=["traces"],
            ),
            SubGoal(
                id="correlate",
                name="Correlate Findings",
                description="Correlate findings from all sources",
                task_type=TaskType.DIAGNOSIS,
                priority=TaskPriority.HIGH,
                dependencies=["analyze_logs", "analyze_metrics", "analyze_traces"],
                estimated_duration_minutes=15,
            ),
            SubGoal(
                id="report",
                name="Generate Report",
                description="Generate investigation report",
                task_type=TaskType.DOCUMENTATION,
                priority=TaskPriority.MEDIUM,
                dependencies=["correlate"],
                estimated_duration_minutes=20,
            ),
        ],
    }

    def __init__(
        self,
        llm_client: Any | None = None,
        use_templates: bool = True,
    ):
        self.llm_client = llm_client
        self.use_templates = use_templates
        self._decomposition_cache: dict[str, DecompositionResult] = {}

    async def decompose(
        self,
        goal: Goal,
        max_depth: int = 3,
        max_sub_goals: int = 15,
    ) -> DecompositionResult:
        """Decompose a goal into sub-goals and task graph."""
        cache_key = f"{goal.category.value}:{goal.description[:50]}"
        if cache_key in self._decomposition_cache:
            cached = self._decomposition_cache[cache_key]
            return DecompositionResult(
                goal_id=goal.id,
                sub_goals=cached.sub_goals,
                task_graph=self._create_task_graph(goal, cached.sub_goals),
                estimated_total_minutes=cached.estimated_total_minutes,
                parallelization_factor=cached.parallelization_factor,
                confidence=cached.confidence,
                reasoning=cached.reasoning + " (cached)",
            )

        if self.use_templates and goal.category in self.DECOMPOSITION_TEMPLATES:
            sub_goals = self._apply_template(goal)
        else:
            sub_goals = await self._decompose_with_llm(goal, max_depth, max_sub_goals)

        sub_goals = self._enrich_with_context(sub_goals, goal)

        task_graph = self._create_task_graph(goal, sub_goals)

        total_minutes = sum(sg.estimated_duration_minutes for sg in sub_goals)

        plan = task_graph.get_execution_plan()
        sequential_time = sum(
            max(
                task_graph.nodes[tid].timeout_seconds / 60
                for tid in level
            )
            for level in plan.levels
        )
        parallel_factor = total_minutes / max(sequential_time, 1)

        result = DecompositionResult(
            goal_id=goal.id,
            sub_goals=sub_goals,
            task_graph=task_graph,
            estimated_total_minutes=total_minutes,
            parallelization_factor=parallel_factor,
            confidence=0.85 if self.use_templates else 0.7,
            reasoning=f"Decomposed into {len(sub_goals)} sub-goals using "
            f"{'template' if self.use_templates else 'LLM'} approach",
        )

        self._decomposition_cache[cache_key] = result

        return result

    def _apply_template(self, goal: Goal) -> list[SubGoal]:
        """Apply a pre-defined template for the goal category."""
        template = self.DECOMPOSITION_TEMPLATES.get(goal.category, [])

        sub_goals = []
        for template_sg in template:
            sg = SubGoal(
                id=f"{goal.id}_{template_sg.id}",
                name=template_sg.name,
                description=template_sg.description,
                task_type=template_sg.task_type,
                priority=template_sg.priority,
                dependencies=[f"{goal.id}_{d}" for d in template_sg.dependencies],
                estimated_duration_minutes=template_sg.estimated_duration_minutes,
                required_tools=template_sg.required_tools.copy(),
                can_parallelize=template_sg.can_parallelize,
            )
            sub_goals.append(sg)

        return sub_goals

    async def _decompose_with_llm(
        self,
        goal: Goal,
        max_depth: int,
        max_sub_goals: int,
    ) -> list[SubGoal]:
        """Use LLM to decompose a goal."""
        if not self.llm_client:
            return self._generate_basic_decomposition(goal)

        prompt = self._build_decomposition_prompt(goal, max_sub_goals)

        try:
            response = await self.llm_client.generate(prompt)
            sub_goals = self._parse_llm_response(response, goal.id)
            return sub_goals[:max_sub_goals]

        except Exception as e:
            log.error("LLM decomposition failed: %s", e)
            return self._generate_basic_decomposition(goal)

    def _build_decomposition_prompt(self, goal: Goal, max_sub_goals: int) -> str:
        """Build prompt for LLM decomposition."""
        return f"""Decompose this DevOps goal into actionable sub-goals.

Goal: {goal.description}
Category: {goal.category.value}
Priority: {goal.priority.name}
Context: {json.dumps(goal.context)}
Constraints: {', '.join(goal.constraints) if goal.constraints else 'None'}
Success Criteria: {', '.join(goal.success_criteria) if goal.success_criteria else 'None'}

Generate up to {max_sub_goals} sub-goals as a JSON array. Each sub-goal should have:
- id: unique identifier (lowercase, underscore-separated)
- name: short descriptive name
- description: what this sub-goal accomplishes
- task_type: one of investigation, diagnosis, mitigation, verification, communication, documentation, rollback, monitoring, custom
- priority: 0 (critical), 1 (high), 2 (medium), or 3 (low)
- dependencies: list of sub-goal ids that must complete first
- estimated_duration_minutes: estimated time to complete
- required_tools: list of tools needed (e.g., logs, metrics, traces, ci_cd)
- can_parallelize: whether this can run in parallel with siblings

Output only valid JSON array.
"""

    def _parse_llm_response(self, response: str, goal_id: str) -> list[SubGoal]:
        """Parse LLM response into sub-goals."""
        try:
            json_match = re.search(r'\[[\s\S]*\]', response)
            if not json_match:
                return []

            data = json.loads(json_match.group())

            sub_goals = []
            for item in data:
                try:
                    task_type = TaskType(item.get("task_type", "custom"))
                except ValueError:
                    task_type = TaskType.CUSTOM

                priority_val = item.get("priority", 2)
                try:
                    priority = TaskPriority(priority_val)
                except ValueError:
                    priority = TaskPriority.MEDIUM

                sg = SubGoal(
                    id=f"{goal_id}_{item.get('id', str(uuid.uuid4())[:8])}",
                    name=item.get("name", "Unnamed Task"),
                    description=item.get("description", ""),
                    task_type=task_type,
                    priority=priority,
                    dependencies=[f"{goal_id}_{d}" for d in item.get("dependencies", [])],
                    estimated_duration_minutes=item.get("estimated_duration_minutes", 30),
                    required_tools=item.get("required_tools", []),
                    can_parallelize=item.get("can_parallelize", True),
                )
                sub_goals.append(sg)

            return sub_goals

        except json.JSONDecodeError as e:
            log.error("Failed to parse LLM response: %s", e)
            return []

    def _generate_basic_decomposition(self, goal: Goal) -> list[SubGoal]:
        """Generate a basic decomposition without LLM."""
        return [
            SubGoal(
                id=f"{goal.id}_analyze",
                name="Analyze Situation",
                description=f"Analyze the situation for: {goal.description}",
                task_type=TaskType.INVESTIGATION,
                priority=goal.priority,
                estimated_duration_minutes=30,
            ),
            SubGoal(
                id=f"{goal.id}_plan",
                name="Create Action Plan",
                description="Create a detailed action plan",
                task_type=TaskType.DIAGNOSIS,
                priority=goal.priority,
                dependencies=[f"{goal.id}_analyze"],
                estimated_duration_minutes=20,
            ),
            SubGoal(
                id=f"{goal.id}_execute",
                name="Execute Plan",
                description="Execute the action plan",
                task_type=TaskType.CUSTOM,
                priority=goal.priority,
                dependencies=[f"{goal.id}_plan"],
                estimated_duration_minutes=60,
            ),
            SubGoal(
                id=f"{goal.id}_verify",
                name="Verify Completion",
                description="Verify the goal has been achieved",
                task_type=TaskType.VERIFICATION,
                priority=goal.priority,
                dependencies=[f"{goal.id}_execute"],
                estimated_duration_minutes=15,
            ),
        ]

    def _enrich_with_context(
        self,
        sub_goals: list[SubGoal],
        goal: Goal,
    ) -> list[SubGoal]:
        """Enrich sub-goals with context from the goal."""
        for sg in sub_goals:
            sg.inputs.update({
                "goal_id": goal.id,
                "goal_category": goal.category.value,
                "goal_context": goal.context,
            })

            if goal.context.get("affected_services"):
                sg.inputs["affected_services"] = goal.context["affected_services"]

            if goal.context.get("incident_id"):
                sg.inputs["incident_id"] = goal.context["incident_id"]

        return sub_goals

    def _create_task_graph(
        self,
        goal: Goal,
        sub_goals: list[SubGoal],
    ) -> TaskGraph:
        """Create a task graph from sub-goals."""
        graph = TaskGraph(f"goal-{goal.id}")

        sub_goal_map = {sg.id: sg for sg in sub_goals}

        for sg in sub_goals:
            valid_deps = [
                d for d in sg.dependencies
                if d in sub_goal_map
            ]

            graph.add_task(
                task_id=sg.id,
                name=sg.name,
                description=sg.description,
                task_type=sg.task_type,
                priority=sg.priority,
                dependencies=valid_deps,
                inputs=sg.inputs,
                timeout_seconds=sg.estimated_duration_minutes * 60,
                metadata={
                    "required_tools": sg.required_tools,
                    "can_parallelize": sg.can_parallelize,
                    "success_criteria": sg.success_criteria,
                },
            )

        return graph

    async def refine_decomposition(
        self,
        result: DecompositionResult,
        feedback: str,
    ) -> DecompositionResult:
        """Refine a decomposition based on feedback."""
        if not self.llm_client:
            return result

        prompt = f"""Refine this goal decomposition based on feedback.

Current sub-goals:
{json.dumps([sg.to_dict() for sg in result.sub_goals], indent=2)}

Feedback: {feedback}

Provide an updated JSON array of sub-goals with the same schema.
"""

        try:
            response = await self.llm_client.generate(prompt)
            new_sub_goals = self._parse_llm_response(response, result.goal_id)

            if new_sub_goals:
                goal = Goal(
                    id=result.goal_id,
                    description="Refined goal",
                    category=GoalCategory.INVESTIGATION,
                )
                new_graph = self._create_task_graph(goal, new_sub_goals)

                return DecompositionResult(
                    goal_id=result.goal_id,
                    sub_goals=new_sub_goals,
                    task_graph=new_graph,
                    estimated_total_minutes=sum(
                        sg.estimated_duration_minutes for sg in new_sub_goals
                    ),
                    parallelization_factor=result.parallelization_factor,
                    confidence=result.confidence * 0.9,
                    reasoning=f"Refined based on feedback: {feedback[:100]}",
                )

        except Exception as e:
            log.error("Refinement failed: %s", e)

        return result
