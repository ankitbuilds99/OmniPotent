"""Demo mode for onboarding.

Provides pre-loaded sample incidents for users to experience the product
without connecting real integrations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.onboarding.demo")


class DemoScenarioType(str, Enum):
    CHECKOUT_ERROR = "checkout_error"
    DATABASE_SLOW = "database_slow"
    MEMORY_LEAK = "memory_leak"
    DEPLOY_FAILURE = "deploy_failure"
    API_TIMEOUT = "api_timeout"


@dataclass
class DemoTimelineEvent:
    """An event in the demo timeline."""

    timestamp: datetime
    event_type: str
    description: str
    source: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "type": self.event_type,
            "description": self.description,
            "source": self.source,
            "details": self.details,
        }


@dataclass
class DemoIncident:
    """A demo incident with pre-loaded data."""

    incident_id: str
    title: str
    description: str
    severity: str
    service: str
    root_cause: str
    fix_suggestion: str
    timeline: list[DemoTimelineEvent]
    error_logs: list[str]
    metrics: dict[str, Any]
    suggested_queries: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.incident_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "service": self.service,
            "root_cause": self.root_cause,
            "fix_suggestion": self.fix_suggestion,
            "timeline": [e.to_dict() for e in self.timeline],
            "error_logs": self.error_logs,
            "metrics": self.metrics,
            "suggested_queries": self.suggested_queries,
        }


@dataclass
class DemoScenario:
    """A complete demo scenario."""

    scenario_id: str
    name: str
    description: str
    difficulty: str
    estimated_time_minutes: int
    incident: DemoIncident
    learning_points: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.scenario_id,
            "name": self.name,
            "description": self.description,
            "difficulty": self.difficulty,
            "estimated_time": self.estimated_time_minutes,
            "incident": self.incident.to_dict(),
            "learning_points": self.learning_points,
        }


class DemoMode:
    """Manages demo mode for onboarding."""

    def __init__(self) -> None:
        self._scenarios: dict[str, DemoScenario] = {}
        self._user_progress: dict[str, dict[str, Any]] = {}
        self._init_default_scenarios()

    def _init_default_scenarios(self) -> None:
        """Initialize default demo scenarios."""
        base_time = datetime.now(timezone.utc) - timedelta(minutes=15)

        checkout_timeline = [
            DemoTimelineEvent(
                base_time,
                "deploy",
                "Deploy #47 started",
                "GitHub Actions",
                {"commit": "abc123", "author": "carol@example.com"},
            ),
            DemoTimelineEvent(
                base_time + timedelta(minutes=2),
                "deploy_complete",
                "Deploy #47 completed successfully",
                "GitHub Actions",
            ),
            DemoTimelineEvent(
                base_time + timedelta(minutes=5),
                "alert",
                "Error rate spike detected",
                "Datadog",
                {"error_rate": "12.3%", "baseline": "0.1%"},
            ),
            DemoTimelineEvent(
                base_time + timedelta(minutes=6),
                "pagerduty",
                "Incident created, @eve paged",
                "PagerDuty",
            ),
            DemoTimelineEvent(
                base_time + timedelta(minutes=8),
                "metric",
                "Affected customers: 412",
                "Internal Metrics",
            ),
        ]

        checkout_incident = DemoIncident(
            incident_id="demo-001",
            title="Checkout API 500 Errors",
            description="Checkout API is returning 500 errors for 12% of requests",
            severity="SEV2",
            service="checkout-api",
            root_cause="Deploy #47 removed null check in payment validation. When payment_method is None, the code throws AttributeError.",
            fix_suggestion="""Add null check before accessing payment_method:

```python
if payment_method is None:
    raise PaymentMethodRequired(user_id)
payment_method.charge(amount)
```""",
            timeline=checkout_timeline,
            error_logs=[
                "[ERROR] 14:07:23 AttributeError: 'NoneType' object has no attribute 'charge'",
                "[ERROR] 14:07:23   File \"billing/checkout.py\", line 142, in process_payment",
                "[ERROR] 14:07:23     payment_method.charge(amount)",
                "[ERROR] 14:07:24 Transaction failed for user_id=u_8f3k2",
            ],
            metrics={
                "error_rate": 12.3,
                "baseline_error_rate": 0.1,
                "affected_requests": 847,
                "affected_customers": 412,
                "revenue_impact": 15000,
            },
            suggested_queries=[
                "Why is checkout failing?",
                "What changed recently?",
                "Show me the error logs",
                "What's the root cause?",
            ],
        )

        self._scenarios["checkout_error"] = DemoScenario(
            scenario_id="checkout_error",
            name="Checkout API Errors",
            description="Investigate a production checkout failure",
            difficulty="beginner",
            estimated_time_minutes=5,
            incident=checkout_incident,
            learning_points=[
                "How AI correlates deploys with errors",
                "Reading error timelines",
                "Understanding root cause analysis",
                "Seeing suggested fixes",
            ],
        )

        db_timeline = [
            DemoTimelineEvent(
                base_time,
                "metric",
                "Query latency increasing",
                "PostgreSQL",
                {"p99_latency": "2.5s", "baseline": "50ms"},
            ),
            DemoTimelineEvent(
                base_time + timedelta(minutes=3),
                "alert",
                "Database slow query alert",
                "Datadog",
            ),
            DemoTimelineEvent(
                base_time + timedelta(minutes=5),
                "metric",
                "Connection pool exhausted",
                "PostgreSQL",
                {"active_connections": 100, "max_connections": 100},
            ),
        ]

        db_incident = DemoIncident(
            incident_id="demo-002",
            title="Database Performance Degradation",
            description="PostgreSQL queries taking 50x longer than normal",
            severity="SEV2",
            service="user-db",
            root_cause="Missing index on users.email column causing full table scan on login queries. Recent data growth pushed table over threshold.",
            fix_suggestion="""Add index on email column:

```sql
CREATE INDEX CONCURRENTLY idx_users_email ON users(email);
```""",
            timeline=db_timeline,
            error_logs=[
                "[SLOW] Query took 2534ms: SELECT * FROM users WHERE email = $1",
                "[WARN] Connection pool exhausted, queries queueing",
            ],
            metrics={
                "query_latency_p99": 2500,
                "baseline_latency": 50,
                "affected_queries": 15000,
                "table_size_gb": 45,
            },
            suggested_queries=[
                "Why is the database slow?",
                "Show slow queries",
                "What's causing the latency?",
            ],
        )

        self._scenarios["database_slow"] = DemoScenario(
            scenario_id="database_slow",
            name="Database Performance Issue",
            description="Investigate slow database queries",
            difficulty="intermediate",
            estimated_time_minutes=7,
            incident=db_incident,
            learning_points=[
                "Database performance analysis",
                "Identifying missing indexes",
                "Connection pool issues",
            ],
        )

    def get_scenarios(self) -> list[DemoScenario]:
        """Get all available demo scenarios."""
        return list(self._scenarios.values())

    def get_scenario(self, scenario_id: str) -> DemoScenario | None:
        """Get a specific scenario."""
        return self._scenarios.get(scenario_id)

    def start_demo(self, user_id: str, scenario_id: str) -> dict[str, Any]:
        """Start a demo session for a user."""
        scenario = self._scenarios.get(scenario_id)
        if not scenario:
            return {"error": "Scenario not found"}

        self._user_progress[user_id] = {
            "scenario_id": scenario_id,
            "started_at": datetime.now(timezone.utc),
            "completed": False,
            "queries_made": [],
        }

        log.info("Demo started: user=%s scenario=%s", user_id, scenario_id)

        return {
            "scenario": scenario.to_dict(),
            "message": f"Demo started: {scenario.name}",
            "suggested_first_query": scenario.incident.suggested_queries[0],
        }

    def process_query(
        self,
        user_id: str,
        query: str,
    ) -> dict[str, Any]:
        """Process a demo query and return simulated response."""
        progress = self._user_progress.get(user_id)
        if not progress:
            return {"error": "No active demo session"}

        scenario = self._scenarios.get(progress["scenario_id"])
        if not scenario:
            return {"error": "Scenario not found"}

        progress["queries_made"].append(query)
        incident = scenario.incident

        query_lower = query.lower()

        if "why" in query_lower or "cause" in query_lower or "root" in query_lower:
            return {
                "type": "root_cause",
                "title": "Root Cause Analysis",
                "content": incident.root_cause,
                "confidence": 94,
                "related_events": [e.to_dict() for e in incident.timeline[:3]],
            }
        elif "log" in query_lower or "error" in query_lower:
            return {
                "type": "logs",
                "title": "Error Logs",
                "logs": incident.error_logs,
                "count": len(incident.error_logs),
            }
        elif "change" in query_lower or "deploy" in query_lower or "recent" in query_lower:
            deploys = [e for e in incident.timeline if "deploy" in e.event_type]
            return {
                "type": "changes",
                "title": "Recent Changes",
                "deploys": [d.to_dict() for d in deploys],
                "correlation": "Deploy #47 correlates with error spike (94% confidence)",
            }
        elif "fix" in query_lower or "suggest" in query_lower or "solve" in query_lower:
            return {
                "type": "fix",
                "title": "Suggested Fix",
                "content": incident.fix_suggestion,
                "auto_apply_available": False,
                "upgrade_message": "Upgrade to Pro for auto-apply",
            }
        elif "metric" in query_lower or "impact" in query_lower:
            return {
                "type": "metrics",
                "title": "Impact Metrics",
                "metrics": incident.metrics,
            }
        else:
            return {
                "type": "summary",
                "title": incident.title,
                "description": incident.description,
                "severity": incident.severity,
                "root_cause": incident.root_cause,
                "suggested_queries": incident.suggested_queries,
            }

    def complete_demo(self, user_id: str) -> dict[str, Any]:
        """Mark demo as completed."""
        progress = self._user_progress.get(user_id)
        if not progress:
            return {"error": "No active demo session"}

        progress["completed"] = True
        progress["completed_at"] = datetime.now(timezone.utc)

        duration = (
            progress["completed_at"] - progress["started_at"]
        ).total_seconds() / 60

        log.info(
            "Demo completed: user=%s scenario=%s duration=%.1f min",
            user_id,
            progress["scenario_id"],
            duration,
        )

        return {
            "completed": True,
            "duration_minutes": round(duration, 1),
            "queries_made": len(progress["queries_made"]),
            "next_steps": [
                "Connect your first integration",
                "Investigate a real incident",
                "Invite a teammate",
            ],
        }

    def get_user_progress(self, user_id: str) -> dict[str, Any] | None:
        """Get user's demo progress."""
        return self._user_progress.get(user_id)


_demo: DemoMode | None = None


def get_demo_mode() -> DemoMode:
    """Get the default demo mode."""
    global _demo
    if _demo is None:
        _demo = DemoMode()
    return _demo
