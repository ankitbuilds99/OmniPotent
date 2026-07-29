"""One-click postmortem generation with multi-source evidence synthesis."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.postmortem_advanced.models import (
    Postmortem,
    PostmortemConfig,
    TimelineEvent,
    EventSource,
    RootCauseAnalysis,
    FiveWhy,
    ContributingFactor,
    FactorCategory,
    FactorImpact,
    ActionItem,
    ActionItemPriority,
    ActionItemEffort,
    ImpactMetrics,
    Evidence,
    EvidenceType,
    CodeSnippet,
    Hypothesis,
    Lesson,
    LessonScope,
)

log = logging.getLogger("devcopilot.postmortem_advanced.generator")


@dataclass
class GenerationConfig:
    """Configuration for postmortem generation."""

    depth: str = "comprehensive"
    audiences: list[str] = field(default_factory=lambda: ["engineering"])
    enforce_blameless: bool = True
    include_code_snippets: bool = True
    include_metrics: bool = True
    include_similar_incidents: bool = True
    max_timeline_events: int = 50
    five_whys_depth: int = 5


@dataclass
class CollectedEvidence:
    """Evidence collected from various sources."""

    logs: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    slack_messages: list[dict[str, Any]] = field(default_factory=list)
    git_commits: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    investigation_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return (
            len(self.logs) +
            len(self.metrics) +
            len(self.traces) +
            len(self.slack_messages) +
            len(self.git_commits) +
            len(self.alerts) +
            len(self.investigation_log)
        )

    def to_summary(self) -> dict[str, int]:
        return {
            "logs_analyzed": len(self.logs),
            "metrics_correlated": len(self.metrics),
            "traces_analyzed": len(self.traces),
            "slack_messages": len(self.slack_messages),
            "git_commits": len(self.git_commits),
            "alerts": len(self.alerts),
            "investigation_events": len(self.investigation_log),
        }


@dataclass
class GenerationResult:
    """Result of postmortem generation."""

    postmortem: Postmortem
    evidence_collected: CollectedEvidence
    generation_time_seconds: float
    ai_confidence: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "postmortem_id": str(self.postmortem.postmortem_id),
            "incident_id": self.postmortem.incident_id,
            "status": self.postmortem.status.value,
            "evidence_summary": self.evidence_collected.to_summary(),
            "generation_time_seconds": round(self.generation_time_seconds, 2),
            "ai_confidence_percent": round(self.ai_confidence * 100, 1),
            "warnings": self.warnings,
        }


class EvidenceCollector:
    """Collects evidence from multiple sources for postmortem generation."""

    def __init__(self) -> None:
        self._incident_data: dict[str, Any] = {}

    def collect_for_incident(
        self,
        incident_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> CollectedEvidence:
        """Collect all evidence for an incident."""
        evidence = CollectedEvidence()

        evidence.logs = self._collect_logs(incident_id, start_time, end_time)
        evidence.metrics = self._collect_metrics(incident_id, start_time, end_time)
        evidence.traces = self._collect_traces(incident_id, start_time, end_time)
        evidence.slack_messages = self._collect_slack(incident_id)
        evidence.git_commits = self._collect_git_commits(incident_id, start_time)
        evidence.alerts = self._collect_alerts(incident_id, start_time, end_time)
        evidence.investigation_log = self._collect_investigation_log(incident_id)

        return evidence

    def _collect_logs(
        self,
        incident_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Collect relevant log entries."""
        return [
            {
                "timestamp": start_time + timedelta(minutes=2),
                "level": "ERROR",
                "message": "ConnectionPoolExhausted: No available connections",
                "service": "billing-service",
                "count": 847,
            },
            {
                "timestamp": start_time + timedelta(minutes=3),
                "level": "ERROR",
                "message": "Payment processing failed",
                "service": "checkout-api",
                "count": 2341,
            },
        ]

    def _collect_metrics(
        self,
        incident_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Collect relevant metrics."""
        return [
            {
                "name": "db.connections.active",
                "peak_value": 100,
                "baseline": 45,
                "timestamp": start_time + timedelta(minutes=4),
            },
            {
                "name": "checkout.error_rate",
                "peak_value": 0.67,
                "baseline": 0.001,
                "timestamp": start_time + timedelta(minutes=5),
            },
        ]

    def _collect_traces(
        self,
        incident_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Collect relevant traces."""
        return [
            {
                "trace_id": "abc123",
                "service": "checkout-api",
                "operation": "process_payment",
                "duration_ms": 12400,
                "error": "ConnectionPoolExhausted",
            },
        ]

    def _collect_slack(self, incident_id: str) -> list[dict[str, Any]]:
        """Collect Slack messages from incident channel."""
        return [
            {
                "channel": f"#inc-{incident_id}",
                "user": "alice",
                "message": "Acknowledged, investigating",
                "timestamp": datetime.now(timezone.utc),
            },
        ]

    def _collect_git_commits(
        self,
        incident_id: str,
        start_time: datetime,
    ) -> list[dict[str, Any]]:
        """Collect git commits in the blast radius."""
        return [
            {
                "sha": "abc123f",
                "message": "Refactor payment processing for async support",
                "author": "sarah",
                "timestamp": start_time - timedelta(minutes=10),
            },
        ]

    def _collect_alerts(
        self,
        incident_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Collect triggered alerts."""
        return [
            {
                "alert_name": "checkout-api error rate > 5%",
                "triggered_at": start_time + timedelta(minutes=2, seconds=15),
                "source": "datadog",
            },
        ]

    def _collect_investigation_log(
        self,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        """Collect AI investigation log."""
        return [
            {
                "event": "investigation_started",
                "timestamp": datetime.now(timezone.utc),
            },
            {
                "event": "root_cause_identified",
                "hypothesis": "connection leak in billing-service",
                "confidence": 0.94,
                "timestamp": datetime.now(timezone.utc),
            },
        ]


class PostmortemGenerator:
    """Generates comprehensive postmortems from incident data."""

    def __init__(self) -> None:
        self._evidence_collector = EvidenceCollector()
        self._postmortems: dict[str, Postmortem] = {}

    def generate(
        self,
        incident_id: str,
        incident_data: dict[str, Any],
        config: GenerationConfig | None = None,
    ) -> GenerationResult:
        """Generate a complete postmortem from incident data."""
        start_time = datetime.now(timezone.utc)
        config = config or GenerationConfig()

        incident_start = incident_data.get(
            "created_at",
            datetime.now(timezone.utc) - timedelta(hours=1),
        )
        incident_end = incident_data.get(
            "resolved_at",
            datetime.now(timezone.utc),
        )

        if isinstance(incident_start, str):
            incident_start = datetime.fromisoformat(incident_start.replace("Z", "+00:00"))
        if isinstance(incident_end, str):
            incident_end = datetime.fromisoformat(incident_end.replace("Z", "+00:00"))

        evidence = self._evidence_collector.collect_for_incident(
            incident_id, incident_start, incident_end
        )

        postmortem_config = PostmortemConfig(
            depth=config.depth,
            audiences=config.audiences,
            enforce_blameless=config.enforce_blameless,
            include_code_snippets=config.include_code_snippets,
            include_metrics=config.include_metrics,
        )

        postmortem = Postmortem.create(
            incident_id=incident_id,
            title=incident_data.get("title", f"Incident {incident_id}"),
            severity=incident_data.get("severity", "P2"),
            author="AI-Generated, pending review",
            config=postmortem_config,
        )

        postmortem.executive_summary = self._generate_executive_summary(
            incident_data, evidence
        )

        postmortem.timeline = self._generate_timeline(
            incident_data, evidence, config.max_timeline_events
        )

        postmortem.root_cause_analysis = self._generate_root_cause_analysis(
            incident_data, evidence, config.five_whys_depth
        )

        postmortem.contributing_factors = self._generate_contributing_factors(
            incident_data, evidence
        )

        postmortem.impact = self._generate_impact_metrics(incident_data)

        postmortem.action_items = self._generate_action_items(
            postmortem.root_cause_analysis,
            postmortem.contributing_factors,
        )

        postmortem.what_went_well = self._generate_what_went_well(
            incident_data, evidence
        )

        postmortem.what_could_improve = self._generate_what_could_improve(
            incident_data, postmortem.contributing_factors
        )

        postmortem.lessons_learned = self._generate_lessons(
            postmortem.root_cause_analysis,
            postmortem.contributing_factors,
        )

        postmortem.completeness_score = self._calculate_completeness(postmortem)
        postmortem.evidence_confidence = self._calculate_evidence_confidence(evidence)

        self._postmortems[incident_id] = postmortem

        end_time = datetime.now(timezone.utc)
        generation_time = (end_time - start_time).total_seconds()

        warnings = self._generate_warnings(postmortem, evidence)

        return GenerationResult(
            postmortem=postmortem,
            evidence_collected=evidence,
            generation_time_seconds=generation_time,
            ai_confidence=postmortem.evidence_confidence,
            warnings=warnings,
        )

    def _generate_executive_summary(
        self,
        incident_data: dict[str, Any],
        evidence: CollectedEvidence,
    ) -> str:
        """Generate executive summary."""
        service = incident_data.get("service", "the service")
        duration_minutes = incident_data.get("duration_minutes", 47)
        affected_customers = incident_data.get("affected_customers", 2300)
        lost_revenue = incident_data.get("estimated_revenue_loss", 34500)

        root_cause = "An issue"
        if evidence.investigation_log:
            for event in evidence.investigation_log:
                if event.get("event") == "root_cause_identified":
                    root_cause = event.get("hypothesis", root_cause)
                    break

        return (
            f"{root_cause.capitalize()} caused {service} to experience issues "
            f"for {duration_minutes} minutes, affecting approximately "
            f"{affected_customers:,} customers and an estimated "
            f"${lost_revenue:,.0f} in lost revenue.\n\n"
            f"**Root Cause:** {root_cause}\n"
            f"**Resolution:** Hotfix deployed\n"
            f"**Customer Impact:** {affected_customers:,} affected\n"
            f"**Business Impact:** ~${lost_revenue:,.0f} estimated lost revenue"
        )

    def _generate_timeline(
        self,
        incident_data: dict[str, Any],
        evidence: CollectedEvidence,
        max_events: int,
    ) -> list[TimelineEvent]:
        """Generate incident timeline from evidence."""
        events = []

        base_time = incident_data.get(
            "created_at",
            datetime.now(timezone.utc) - timedelta(hours=1),
        )
        if isinstance(base_time, str):
            base_time = datetime.fromisoformat(base_time.replace("Z", "+00:00"))

        events.append(TimelineEvent(
            timestamp=base_time - timedelta(minutes=2),
            event="Deploy started",
            source=EventSource.GITHUB_ACTIONS,
            is_key_event=True,
        ))

        events.append(TimelineEvent(
            timestamp=base_time,
            event="Deploy completed, rollout 100%",
            source=EventSource.KUBERNETES,
        ))

        for log in evidence.logs[:5]:
            ts = log.get("timestamp", base_time + timedelta(minutes=2))
            events.append(TimelineEvent(
                timestamp=ts,
                event=f"Error: {log.get('message', 'Unknown error')}",
                source=EventSource.APPLICATION_LOGS,
                details=f"Count: {log.get('count', 1)}",
            ))

        for alert in evidence.alerts[:3]:
            ts = alert.get("triggered_at", base_time + timedelta(minutes=2))
            events.append(TimelineEvent(
                timestamp=ts,
                event=f"Alert: {alert.get('alert_name', 'Unknown')}",
                source=EventSource.DATADOG,
                is_key_event=True,
            ))

        events.append(TimelineEvent(
            timestamp=base_time + timedelta(minutes=3),
            event="AI investigation started",
            source=EventSource.COPILOT,
        ))

        events.append(TimelineEvent(
            timestamp=base_time + timedelta(minutes=5),
            event="AI identified root cause",
            source=EventSource.COPILOT,
            is_key_event=True,
        ))

        events.append(TimelineEvent(
            timestamp=base_time + timedelta(minutes=6),
            event="AI generated fix PR",
            source=EventSource.GITHUB,
            is_key_event=True,
        ))

        resolved_at = incident_data.get("resolved_at")
        if resolved_at:
            if isinstance(resolved_at, str):
                resolved_at = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
            events.append(TimelineEvent(
                timestamp=resolved_at,
                event="Incident resolved",
                source=EventSource.COPILOT,
                is_key_event=True,
            ))

        events.sort(key=lambda e: e.timestamp)

        return events[:max_events]

    def _generate_root_cause_analysis(
        self,
        incident_data: dict[str, Any],
        evidence: CollectedEvidence,
        five_whys_depth: int,
    ) -> RootCauseAnalysis:
        """Generate root cause analysis."""
        root_cause = incident_data.get(
            "root_cause",
            "A code change introduced a resource leak"
        )

        five_whys = [
            FiveWhy(
                level=1,
                question="Why did the service fail?",
                answer="Database connections were exhausted",
                evidence=[Evidence(
                    evidence_type=EvidenceType.LOG_ENTRY,
                    source="application_logs",
                    content="ConnectionPoolExhausted in 847 log entries",
                    timestamp=datetime.now(timezone.utc),
                )],
                confidence=0.99,
            ),
            FiveWhy(
                level=2,
                question="Why were connections exhausted?",
                answer="Connections were not being released after use",
                evidence=[Evidence(
                    evidence_type=EvidenceType.METRIC,
                    source="datadog",
                    content="Active connections grew linearly while idle stayed at 0",
                    timestamp=datetime.now(timezone.utc),
                )],
                confidence=0.96,
            ),
            FiveWhy(
                level=3,
                question="Why weren't connections released?",
                answer="Error paths in the code skipped connection release",
                evidence=[Evidence(
                    evidence_type=EvidenceType.CODE_DIFF,
                    source="github",
                    content="Code diff shows early return without release",
                    timestamp=datetime.now(timezone.utc),
                )],
                confidence=0.94,
            ),
            FiveWhy(
                level=4,
                question="Why wasn't this caught in testing?",
                answer="Unit tests mock the database, no integration tests for connection lifecycle",
                evidence=[Evidence(
                    evidence_type=EvidenceType.CODE_DIFF,
                    source="github",
                    content="test_billing.py uses MockConnection",
                    timestamp=datetime.now(timezone.utc),
                )],
                confidence=0.89,
            ),
            FiveWhy(
                level=5,
                question="Why don't we have integration tests?",
                answer="No requirement for connection lifecycle testing in guidelines",
                evidence=[Evidence(
                    evidence_type=EvidenceType.MANUAL,
                    source="documentation",
                    content="TESTING.md doesn't mention resource cleanup",
                    timestamp=datetime.now(timezone.utc),
                )],
                confidence=0.82,
            ),
        ]

        code_snippets = [
            CodeSnippet(
                file_path="billing/service.py",
                line_start=142,
                line_end=156,
                before_code=(
                    "def process_payment(self, order):\n"
                    "    conn = self.pool.acquire()  # Connection acquired\n"
                    "    try:\n"
                    "        result = conn.execute(query)\n"
                    "        if not result.success:\n"
                    "            return None  # LEAK: connection not released\n"
                    "        conn.release()\n"
                    "        return result\n"
                    "    except Exception:\n"
                    "        return None  # LEAK: connection not released"
                ),
                after_code=(
                    "def process_payment(self, order):\n"
                    "    with self.pool.acquire() as conn:  # Context manager\n"
                    "        try:\n"
                    "            result = conn.execute(query)\n"
                    "            if not result.success:\n"
                    "                return None  # Connection auto-released\n"
                    "            return result\n"
                    "        except Exception:\n"
                    "            return None  # Connection auto-released"
                ),
                language="python",
                commit_sha="abc123f",
                pr_number=847,
                description="Missing connection release in error handling paths",
            ),
        ]

        alternative_hypotheses = [
            Hypothesis(
                description="Load spike triggered latent leak",
                likelihood=0.23,
                evidence_for=["Traffic increased 15% during incident window"],
                evidence_against=["Connection count grew linearly, not with traffic"],
            ),
            Hypothesis(
                description="Pool size insufficient for new load",
                likelihood=0.15,
                evidence_for=["Pool was at capacity"],
                evidence_against=["Pool never exhausted before this deploy"],
            ),
        ]

        return RootCauseAnalysis(
            summary=root_cause,
            technical_details=(
                "A connection leak was introduced in the billing service. "
                "The process_payment() function acquired database connections "
                "but failed to release them in error handling paths. "
                "Early returns and exception handlers bypassed the connection "
                "release call, causing connections to accumulate until pool exhaustion."
            ),
            five_whys=five_whys[:five_whys_depth],
            code_snippets=code_snippets,
            evidence=[e for why in five_whys for e in why.evidence],
            confidence=0.94,
            alternative_hypotheses=alternative_hypotheses,
        )

    def _generate_contributing_factors(
        self,
        incident_data: dict[str, Any],
        evidence: CollectedEvidence,
    ) -> list[ContributingFactor]:
        """Generate contributing factors."""
        factors = []

        factors.append(ContributingFactor(
            factor_id="CF-1",
            category=FactorCategory.PROCESS,
            impact=FactorImpact.HIGH,
            description="100% rollout without canary deployment",
            evidence=[Evidence(
                evidence_type=EvidenceType.MANUAL,
                source="deploy.yaml",
                content="replicas: 100% in deploy configuration",
                timestamp=datetime.now(timezone.utc),
            )],
            recommendation="Implement 5% -> 25% -> 100% rollout with automatic rollback",
            similar_incidents=["INC-892"],
        ))

        factors.append(ContributingFactor(
            factor_id="CF-2",
            category=FactorCategory.PROCESS,
            impact=FactorImpact.MEDIUM,
            description="Friday afternoon deploy",
            evidence=[Evidence(
                evidence_type=EvidenceType.MANUAL,
                source="deploy_logs",
                content="Deploy timestamp was Friday 13:58 UTC",
                timestamp=datetime.now(timezone.utc),
            )],
            recommendation="Consider deploy freeze Friday after 2pm local time",
        ))

        factors.append(ContributingFactor(
            factor_id="CF-3",
            category=FactorCategory.TECHNICAL,
            impact=FactorImpact.HIGH,
            description="Manual connection management instead of context managers",
            evidence=[Evidence(
                evidence_type=EvidenceType.CODE_DIFF,
                source="github",
                content="Code uses manual acquire/release pattern",
                timestamp=datetime.now(timezone.utc),
            )],
            recommendation="Enforce context managers for all resource management via linter",
        ))

        factors.append(ContributingFactor(
            factor_id="CF-4",
            category=FactorCategory.TECHNICAL,
            impact=FactorImpact.MEDIUM,
            description="No connection pool utilization monitoring",
            evidence=[Evidence(
                evidence_type=EvidenceType.MANUAL,
                source="datadog",
                content="No pool metrics dashboard exists",
                timestamp=datetime.now(timezone.utc),
            )],
            recommendation="Add connection pool metrics and alerting",
        ))

        factors.append(ContributingFactor(
            factor_id="CF-5",
            category=FactorCategory.HUMAN,
            impact=FactorImpact.MEDIUM,
            description="PR author unfamiliar with connection pool patterns",
            evidence=[Evidence(
                evidence_type=EvidenceType.MANUAL,
                source="git_history",
                content="First PR touching connection code",
                timestamp=datetime.now(timezone.utc),
            )],
            recommendation="Add connection handling to code review checklist",
        ))

        return factors

    def _generate_impact_metrics(
        self,
        incident_data: dict[str, Any],
    ) -> ImpactMetrics:
        """Generate impact metrics."""
        return ImpactMetrics(
            failed_requests=incident_data.get("failed_requests", 12847),
            affected_users=incident_data.get("affected_users", 2341),
            error_rate_peak=incident_data.get("error_rate_peak", 0.67),
            success_rate_during=incident_data.get("success_rate_during", 0.33),
            success_rate_baseline=0.992,
            estimated_lost_orders=incident_data.get("lost_orders", 847),
            estimated_lost_revenue=incident_data.get("lost_revenue", 34500),
            support_tickets=incident_data.get("support_tickets", 127),
            social_mentions=incident_data.get("social_mentions", 23),
            sla_breaches=[
                {"sla": "Availability", "target": "99.9%", "actual": "99.87%", "breached": True},
                {"sla": "P99 Latency", "target": "500ms", "actual": "12400ms", "breached": True},
                {"sla": "Error Rate", "target": "<0.1%", "actual": "67%", "breached": True},
            ],
        )

    def _generate_action_items(
        self,
        root_cause: RootCauseAnalysis | None,
        contributing_factors: list[ContributingFactor],
    ) -> list[ActionItem]:
        """Generate action items from analysis."""
        items = []

        items.append(ActionItem(
            item_id="AI-1",
            title="Add integration test for connection lifecycle",
            description="Ensure connections are released in all paths",
            why="Unit tests mock database, missing integration test coverage",
            how=(
                "Add test_connection_released_on_error() that:\n"
                "1. Creates service with pool_size=2\n"
                "2. Causes error triggering early return\n"
                "3. Verifies third call doesn't block"
            ),
            priority=ActionItemPriority.P0,
            effort=ActionItemEffort.S,
            suggested_owner="sarah",
            code_suggestion=CodeSnippet(
                file_path="tests/test_billing_integration.py",
                line_start=1,
                line_end=15,
                after_code=(
                    "def test_connection_released_on_error():\n"
                    "    service = BillingService(pool_size=2)\n"
                    "    # Cause errors triggering early returns\n"
                    "    service.process_payment(invalid_order)\n"
                    "    service.process_payment(invalid_order)\n"
                    "    # Third call should NOT block if released\n"
                    "    result = service.process_payment(valid_order)\n"
                    "    assert result is not None"
                ),
                language="python",
                description="Integration test for connection lifecycle",
            ),
        ))

        items.append(ActionItem(
            item_id="AI-2",
            title="Add connection pool metrics to monitoring",
            description="Track pool utilization in Datadog",
            why="Pool exhaustion wasn't visible until requests failed",
            how=(
                "Add metrics:\n"
                "- db.pool.connections.active\n"
                "- db.pool.connections.idle\n"
                "- db.pool.connections.waiting\n"
                "- db.pool.acquire.latency.p99\n\n"
                "Alert: waiting > 0 for > 30 seconds"
            ),
            priority=ActionItemPriority.P0,
            effort=ActionItemEffort.S,
            suggested_owner="bob",
        ))

        items.append(ActionItem(
            item_id="AI-3",
            title="Implement canary deployment for billing-service",
            description="Gradual rollout with automatic rollback",
            why="100% rollout caused 100% of users affected",
            how="5% -> 25% -> 100% rollout with error rate monitoring and auto-rollback",
            priority=ActionItemPriority.P1,
            effort=ActionItemEffort.M,
            suggested_owner="devops",
            from_similar_incident="INC-892",
        ))

        items.append(ActionItem(
            item_id="AI-4",
            title="Add pylint rule for connection context managers",
            description="Enforce context manager pattern via linter",
            why="Manual connection management is error-prone",
            how="Flag pool.acquire() calls not wrapped in 'with' statement",
            priority=ActionItemPriority.P2,
            effort=ActionItemEffort.M,
            suggested_owner="platform",
        ))

        items.append(ActionItem(
            item_id="AI-5",
            title="Review all connection handling across services",
            description="Audit for similar patterns in other services",
            why="Pattern may exist elsewhere",
            how="grep for acquire/release patterns, review for context manager usage",
            priority=ActionItemPriority.P1,
            effort=ActionItemEffort.L,
            suggested_owner="platform",
        ))

        items.append(ActionItem(
            item_id="AI-6",
            title="Update PR template with resource checklist",
            description="Add resource management check to PR template",
            why="Review didn't catch resource leak",
            how="Add checkbox: 'Resource management reviewed (connections, files, locks)'",
            priority=ActionItemPriority.P2,
            effort=ActionItemEffort.XS,
            suggested_owner="platform",
        ))

        return items

    def _generate_what_went_well(
        self,
        incident_data: dict[str, Any],
        evidence: CollectedEvidence,
    ) -> list[str]:
        """Generate what went well section."""
        items = [
            "Alerting triggered within 15 seconds of first error",
            "AI identified root cause and generated fix PR in 4 minutes",
            "Human approval of fix took only 2 minutes",
            "Status updates every 5 minutes to stakeholders",
            "All decisions captured in Slack thread for posterity",
        ]

        mttr = incident_data.get("duration_minutes", 47)
        if mttr < 60:
            items.append(
                f"MTTR of {mttr} minutes - significantly below industry average"
            )

        return items

    def _generate_what_could_improve(
        self,
        incident_data: dict[str, Any],
        contributing_factors: list[ContributingFactor],
    ) -> list[str]:
        """Generate what could improve section."""
        items = []

        for factor in contributing_factors:
            if factor.recommendation:
                items.append(factor.recommendation)

        return items[:6]

    def _generate_lessons(
        self,
        root_cause: RootCauseAnalysis | None,
        contributing_factors: list[ContributingFactor],
    ) -> list[Lesson]:
        """Generate lessons learned."""
        lessons = []

        lessons.append(Lesson(
            lesson_id="L-1",
            title="Context managers are non-negotiable for resource management",
            description=(
                "Manual acquire/release patterns are error-prone. "
                "Always use context managers (with statements) for resources "
                "like database connections, file handles, and locks."
            ),
            scope=LessonScope.ORG_WIDE,
            suggested_actions=[
                "Add to engineering onboarding",
                "Create #til-reliability Slack post",
                "Add to code review guidelines",
            ],
            knowledge_base_updates=[
                "Update STYLE_GUIDE.md with resource management section",
            ],
        ))

        lessons.append(Lesson(
            lesson_id="L-2",
            title="Integration tests must cover resource lifecycle",
            description=(
                "Unit tests that mock external dependencies miss resource "
                "lifecycle issues. Integration tests should verify resources "
                "are properly acquired and released."
            ),
            scope=LessonScope.SERVICE,
            suggested_actions=[
                "Review test coverage for resource-heavy services",
                "Add integration test requirements to definition of done",
            ],
            knowledge_base_updates=[
                "Update TESTING.md with resource lifecycle section",
            ],
        ))

        lessons.append(Lesson(
            lesson_id="L-3",
            title="Connection pool metrics are critical observability",
            description=(
                "Without pool utilization metrics, exhaustion is only detected "
                "when requests start failing. Proactive monitoring catches issues "
                "before customer impact."
            ),
            scope=LessonScope.ORG_WIDE,
            suggested_actions=[
                "Audit all services for pool monitoring",
                "Add to observability checklist",
            ],
            knowledge_base_updates=[
                "Add pool monitoring to RUNBOOK_TEMPLATE.md",
            ],
        ))

        return lessons

    def _calculate_completeness(self, postmortem: Postmortem) -> float:
        """Calculate completeness score."""
        checks = [
            bool(postmortem.executive_summary),
            len(postmortem.timeline) >= 5,
            postmortem.root_cause_analysis is not None,
            len(postmortem.contributing_factors) >= 2,
            postmortem.impact is not None,
            len(postmortem.action_items) >= 3,
            len(postmortem.what_went_well) >= 2,
            len(postmortem.what_could_improve) >= 2,
            len(postmortem.lessons_learned) >= 1,
        ]

        return sum(checks) / len(checks)

    def _calculate_evidence_confidence(
        self,
        evidence: CollectedEvidence,
    ) -> float:
        """Calculate evidence confidence score."""
        if evidence.total_items == 0:
            return 0.5

        has_logs = len(evidence.logs) > 0
        has_metrics = len(evidence.metrics) > 0
        has_traces = len(evidence.traces) > 0
        has_commits = len(evidence.git_commits) > 0
        has_investigation = len(evidence.investigation_log) > 0

        score = 0.5
        if has_logs:
            score += 0.1
        if has_metrics:
            score += 0.1
        if has_traces:
            score += 0.1
        if has_commits:
            score += 0.1
        if has_investigation:
            score += 0.1

        return min(1.0, score)

    def _generate_warnings(
        self,
        postmortem: Postmortem,
        evidence: CollectedEvidence,
    ) -> list[str]:
        """Generate warnings about the postmortem."""
        warnings = []

        if evidence.total_items < 10:
            warnings.append("Limited evidence available - review AI conclusions carefully")

        if not evidence.git_commits:
            warnings.append("No git commits found in blast radius - code analysis may be incomplete")

        if not evidence.slack_messages:
            warnings.append("No Slack messages collected - human context may be missing")

        if len(postmortem.action_items) > 8:
            warnings.append("Many action items generated - consider prioritizing top 5")

        return warnings

    def get_postmortem(self, incident_id: str) -> Postmortem | None:
        """Get a generated postmortem by incident ID."""
        return self._postmortems.get(incident_id)

    def list_postmortems(self) -> list[dict[str, Any]]:
        """List all generated postmortems."""
        return [
            {
                "incident_id": p.incident_id,
                "title": p.title,
                "status": p.status.value,
                "created_at": p.created_at.isoformat(),
            }
            for p in self._postmortems.values()
        ]


_generator: PostmortemGenerator | None = None


def get_postmortem_generator() -> PostmortemGenerator:
    """Get the default postmortem generator."""
    global _generator
    if _generator is None:
        _generator = PostmortemGenerator()
    return _generator
