"""Evaluation Scenarios - Test cases for agent evaluation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger("devcopilot.eval.scenarios")


class IncidentType(str, Enum):
    """Types of incidents for evaluation."""
    LATENCY_SPIKE = "latency_spike"
    ERROR_RATE = "error_rate"
    MEMORY_LEAK = "memory_leak"
    CPU_SPIKE = "cpu_spike"
    DISK_FULL = "disk_full"
    CONNECTION_POOL = "connection_pool"
    DEPENDENCY_FAILURE = "dependency_failure"
    DEPLOYMENT_REGRESSION = "deployment_regression"
    CONFIG_CHANGE = "config_change"
    TRAFFIC_SPIKE = "traffic_spike"


class Difficulty(str, Enum):
    """Scenario difficulty levels."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


@dataclass
class ExpectedOutcome:
    """Expected outcome for a scenario."""
    root_cause: str
    root_cause_keywords: list[str] = field(default_factory=list)
    severity: str = "medium"
    affected_services: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    min_confidence: float = 0.7
    max_investigation_time_seconds: float = 300.0

    def matches_root_cause(self, actual: str) -> bool:
        """Check if actual root cause matches expected."""
        if not actual:
            return False
        actual_lower = actual.lower()
        # Exact match
        if self.root_cause.lower() in actual_lower:
            return True
        # Keyword match
        matched_keywords = sum(
            1 for kw in self.root_cause_keywords
            if kw.lower() in actual_lower
        )
        return matched_keywords >= len(self.root_cause_keywords) // 2 + 1


@dataclass
class Scenario:
    """A single evaluation scenario."""
    id: str
    name: str
    description: str
    incident_type: IncidentType
    difficulty: Difficulty

    # Incident payload (what the agent receives)
    incident: dict[str, Any]

    # Mock data for tools
    mock_logs: list[dict[str, Any]] = field(default_factory=list)
    mock_metrics: dict[str, Any] = field(default_factory=dict)
    mock_traces: list[dict[str, Any]] = field(default_factory=list)
    mock_deployments: list[dict[str, Any]] = field(default_factory=list)

    # Expected outcome
    expected: ExpectedOutcome = field(default_factory=lambda: ExpectedOutcome(root_cause="unknown"))

    # Metadata
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    author: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "incident_type": self.incident_type.value,
            "difficulty": self.difficulty.value,
            "incident": self.incident,
            "expected": {
                "root_cause": self.expected.root_cause,
                "severity": self.expected.severity,
                "affected_services": self.expected.affected_services,
            },
            "tags": self.tags,
        }


class ScenarioLoader:
    """Loads evaluation scenarios from files or generates them."""

    # Built-in scenarios for testing
    BUILTIN_SCENARIOS = [
        Scenario(
            id="latency-001",
            name="Database Connection Pool Exhaustion",
            description="API latency spike caused by database connection pool exhaustion",
            incident_type=IncidentType.LATENCY_SPIKE,
            difficulty=Difficulty.MEDIUM,
            incident={
                "id": "INC-001",
                "title": "High latency on /api/users endpoint",
                "service": "api-server",
                "severity": "high",
                "metrics": {
                    "latency_p99": 5200,
                    "latency_p95": 3100,
                    "error_rate": 0.02,
                },
            },
            mock_logs=[
                {"level": "ERROR", "message": "Connection pool exhausted, waiting for available connection", "service": "api-server"},
                {"level": "WARN", "message": "Query took 4500ms to execute", "service": "api-server"},
                {"level": "ERROR", "message": "Timeout waiting for database connection after 30s", "service": "api-server"},
            ],
            mock_metrics={
                "db_connection_pool_active": 50,
                "db_connection_pool_max": 50,
                "db_connection_wait_time_ms": 4500,
            },
            expected=ExpectedOutcome(
                root_cause="Database connection pool exhaustion",
                root_cause_keywords=["connection", "pool", "database", "exhausted"],
                severity="high",
                affected_services=["api-server"],
                recommended_actions=["Increase connection pool size", "Add connection timeout", "Review slow queries"],
            ),
        ),
        Scenario(
            id="memory-001",
            name="Memory Leak in Payment Service",
            description="Gradual memory increase leading to OOM kills",
            incident_type=IncidentType.MEMORY_LEAK,
            difficulty=Difficulty.HARD,
            incident={
                "id": "INC-002",
                "title": "Payment service pods restarting frequently",
                "service": "payment-service",
                "severity": "critical",
                "metrics": {
                    "pod_restarts_1h": 5,
                    "memory_usage_percent": 98,
                },
            },
            mock_logs=[
                {"level": "ERROR", "message": "Container killed due to OOM", "service": "payment-service"},
                {"level": "WARN", "message": "Memory usage at 95%", "service": "payment-service"},
                {"level": "INFO", "message": "Processing payment batch of 10000 records", "service": "payment-service"},
            ],
            mock_metrics={
                "memory_usage_bytes": 3900000000,
                "memory_limit_bytes": 4000000000,
                "gc_pause_time_ms": 500,
            },
            mock_deployments=[
                {"sha": "abc123", "deployed_at": "2024-03-15T10:00:00Z", "message": "Add batch payment processing"},
            ],
            expected=ExpectedOutcome(
                root_cause="Memory leak in batch payment processing",
                root_cause_keywords=["memory", "leak", "batch", "OOM"],
                severity="critical",
                affected_services=["payment-service"],
                recommended_actions=["Rollback recent deployment", "Add memory limits", "Fix batch processing memory handling"],
            ),
        ),
        Scenario(
            id="deploy-001",
            name="Deployment Regression",
            description="Error rate spike after deployment",
            incident_type=IncidentType.DEPLOYMENT_REGRESSION,
            difficulty=Difficulty.EASY,
            incident={
                "id": "INC-003",
                "title": "500 errors on checkout endpoint",
                "service": "checkout-service",
                "severity": "high",
                "metrics": {
                    "error_rate": 0.15,
                    "http_500_count": 450,
                },
            },
            mock_logs=[
                {"level": "ERROR", "message": "NullPointerException in CartValidator.validate()", "service": "checkout-service"},
                {"level": "ERROR", "message": "Failed to process checkout: cart items is null", "service": "checkout-service"},
            ],
            mock_deployments=[
                {"sha": "def456", "deployed_at": "2024-03-15T14:30:00Z", "message": "Refactor cart validation logic"},
            ],
            expected=ExpectedOutcome(
                root_cause="Bug introduced in recent deployment - null pointer in cart validation",
                root_cause_keywords=["deployment", "null", "cart", "validation", "regression"],
                severity="high",
                affected_services=["checkout-service"],
                recommended_actions=["Rollback to previous version", "Fix null check in CartValidator"],
            ),
        ),
        Scenario(
            id="dependency-001",
            name="Third-party API Failure",
            description="External payment provider experiencing outage",
            incident_type=IncidentType.DEPENDENCY_FAILURE,
            difficulty=Difficulty.MEDIUM,
            incident={
                "id": "INC-004",
                "title": "Payment processing failures",
                "service": "payment-service",
                "severity": "critical",
                "metrics": {
                    "payment_success_rate": 0.10,
                    "external_api_latency": 30000,
                },
            },
            mock_logs=[
                {"level": "ERROR", "message": "Stripe API timeout after 30s", "service": "payment-service"},
                {"level": "ERROR", "message": "Payment failed: upstream service unavailable", "service": "payment-service"},
                {"level": "WARN", "message": "Circuit breaker opened for stripe-client", "service": "payment-service"},
            ],
            expected=ExpectedOutcome(
                root_cause="Stripe API outage causing payment failures",
                root_cause_keywords=["stripe", "external", "dependency", "timeout", "outage"],
                severity="critical",
                affected_services=["payment-service"],
                recommended_actions=["Enable fallback payment provider", "Monitor Stripe status page", "Queue failed payments for retry"],
            ),
        ),
        Scenario(
            id="config-001",
            name="Configuration Change Impact",
            description="Feature flag change causing unexpected behavior",
            incident_type=IncidentType.CONFIG_CHANGE,
            difficulty=Difficulty.HARD,
            incident={
                "id": "INC-005",
                "title": "Users unable to access premium features",
                "service": "feature-service",
                "severity": "high",
                "metrics": {
                    "premium_feature_access_rate": 0.0,
                },
            },
            mock_logs=[
                {"level": "INFO", "message": "Feature flag 'premium_v2' evaluated to false", "service": "feature-service"},
                {"level": "WARN", "message": "Premium access denied for user_id=12345", "service": "feature-service"},
            ],
            expected=ExpectedOutcome(
                root_cause="Feature flag misconfiguration disabled premium features",
                root_cause_keywords=["feature", "flag", "config", "premium", "disabled"],
                severity="high",
                affected_services=["feature-service"],
                recommended_actions=["Revert feature flag change", "Add feature flag change alerts"],
            ),
        ),
    ]

    def __init__(self, scenarios_dir: Path | None = None):
        """Initialize the scenario loader.

        Args:
            scenarios_dir: Directory containing scenario JSON files
        """
        self.scenarios_dir = scenarios_dir
        self._scenarios: dict[str, Scenario] = {}
        self._load_builtin()

    def _load_builtin(self) -> None:
        """Load built-in scenarios."""
        for scenario in self.BUILTIN_SCENARIOS:
            self._scenarios[scenario.id] = scenario

    def load_from_file(self, path: Path) -> Scenario:
        """Load a scenario from a JSON file."""
        with open(path) as f:
            data = json.load(f)

        scenario = Scenario(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            incident_type=IncidentType(data["incident_type"]),
            difficulty=Difficulty(data.get("difficulty", "medium")),
            incident=data["incident"],
            mock_logs=data.get("mock_logs", []),
            mock_metrics=data.get("mock_metrics", {}),
            mock_traces=data.get("mock_traces", []),
            mock_deployments=data.get("mock_deployments", []),
            expected=ExpectedOutcome(
                root_cause=data["expected"]["root_cause"],
                root_cause_keywords=data["expected"].get("root_cause_keywords", []),
                severity=data["expected"].get("severity", "medium"),
                affected_services=data["expected"].get("affected_services", []),
                recommended_actions=data["expected"].get("recommended_actions", []),
            ),
            tags=data.get("tags", []),
            author=data.get("author", ""),
        )

        self._scenarios[scenario.id] = scenario
        return scenario

    def load_all(self) -> list[Scenario]:
        """Load all scenarios from the scenarios directory."""
        if self.scenarios_dir and self.scenarios_dir.exists():
            for path in self.scenarios_dir.glob("*.json"):
                try:
                    self.load_from_file(path)
                except Exception as e:
                    log.warning("Failed to load scenario %s: %s", path, e)

        return list(self._scenarios.values())

    def get(self, scenario_id: str) -> Scenario | None:
        """Get a scenario by ID."""
        return self._scenarios.get(scenario_id)

    def filter(
        self,
        incident_type: IncidentType | None = None,
        difficulty: Difficulty | None = None,
        tags: list[str] | None = None,
    ) -> list[Scenario]:
        """Filter scenarios by criteria."""
        scenarios = list(self._scenarios.values())

        if incident_type:
            scenarios = [s for s in scenarios if s.incident_type == incident_type]

        if difficulty:
            scenarios = [s for s in scenarios if s.difficulty == difficulty]

        if tags:
            scenarios = [
                s for s in scenarios
                if any(t in s.tags for t in tags)
            ]

        return scenarios

    def count(self) -> int:
        """Return total number of scenarios."""
        return len(self._scenarios)
