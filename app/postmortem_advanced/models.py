"""Core data models for AI Postmortem system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class PostmortemStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHED = "published"


class EventSource(str, Enum):
    GITHUB_ACTIONS = "github_actions"
    KUBERNETES = "kubernetes"
    APPLICATION_LOGS = "application_logs"
    DATADOG = "datadog"
    PAGERDUTY = "pagerduty"
    SLACK = "slack"
    COPILOT = "copilot"
    GITHUB = "github"
    MANUAL = "manual"


class FactorCategory(str, Enum):
    PROCESS = "process"
    TECHNICAL = "technical"
    HUMAN = "human"
    ENVIRONMENTAL = "environmental"


class FactorImpact(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionItemStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WONT_DO = "wont_do"
    BLOCKED = "blocked"


class ActionItemPriority(str, Enum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class ActionItemEffort(str, Enum):
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


class LessonScope(str, Enum):
    TEAM = "team"
    SERVICE = "service"
    ORG_WIDE = "org_wide"


class EvidenceType(str, Enum):
    LOG_ENTRY = "log_entry"
    METRIC = "metric"
    TRACE = "trace"
    SLACK_MESSAGE = "slack_message"
    GIT_COMMIT = "git_commit"
    CODE_DIFF = "code_diff"
    ALERT = "alert"
    MANUAL = "manual"


@dataclass
class Evidence:
    """Evidence supporting a conclusion."""

    evidence_type: EvidenceType
    source: str
    content: str
    timestamp: datetime
    url: str | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.evidence_type.value,
            "source": self.source,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "url": self.url,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class CodeSnippet:
    """Code snippet with before/after comparison."""

    file_path: str
    line_start: int
    line_end: int
    before_code: str | None = None
    after_code: str | None = None
    language: str = "python"
    commit_sha: str | None = None
    pr_number: int | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "lines": f"{self.line_start}-{self.line_end}",
            "before": self.before_code,
            "after": self.after_code,
            "language": self.language,
            "commit": self.commit_sha,
            "pr": self.pr_number,
            "description": self.description,
        }


@dataclass
class TimelineEvent:
    """An event in the incident timeline."""

    timestamp: datetime
    event: str
    source: EventSource
    details: str | None = None
    actor: str | None = None
    url: str | None = None
    is_key_event: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "time_utc": self.timestamp.strftime("%H:%M:%S"),
            "event": self.event,
            "source": self.source.value,
            "details": self.details,
            "actor": self.actor,
            "url": self.url,
            "is_key_event": self.is_key_event,
        }


@dataclass
class FiveWhy:
    """A single Why in the Five Whys analysis."""

    level: int
    question: str
    answer: str
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "question": self.question,
            "answer": self.answer,
            "evidence": [e.to_dict() for e in self.evidence],
            "confidence": round(self.confidence * 100, 0),
        }


@dataclass
class Hypothesis:
    """An alternative hypothesis for root cause."""

    description: str
    likelihood: float
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "likelihood_percent": round(self.likelihood * 100, 0),
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
        }


@dataclass
class RootCauseAnalysis:
    """Complete root cause analysis."""

    summary: str
    technical_details: str
    five_whys: list[FiveWhy] = field(default_factory=list)
    code_snippets: list[CodeSnippet] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.0
    alternative_hypotheses: list[Hypothesis] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "technical_details": self.technical_details,
            "five_whys": [w.to_dict() for w in self.five_whys],
            "code_snippets": [c.to_dict() for c in self.code_snippets],
            "evidence": [e.to_dict() for e in self.evidence],
            "confidence_percent": round(self.confidence * 100, 0),
            "alternative_hypotheses": [h.to_dict() for h in self.alternative_hypotheses],
        }


@dataclass
class ContributingFactor:
    """A factor that contributed to the incident."""

    factor_id: str
    category: FactorCategory
    impact: FactorImpact
    description: str
    evidence: list[Evidence] = field(default_factory=list)
    recommendation: str | None = None
    similar_incidents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.factor_id,
            "category": self.category.value,
            "impact": self.impact.value,
            "description": self.description,
            "evidence": [e.to_dict() for e in self.evidence],
            "recommendation": self.recommendation,
            "similar_incidents": self.similar_incidents,
        }


@dataclass
class ActionItem:
    """An action item from the postmortem."""

    item_id: str
    title: str
    description: str
    why: str
    how: str | None = None
    priority: ActionItemPriority = ActionItemPriority.P1
    effort: ActionItemEffort = ActionItemEffort.M
    status: ActionItemStatus = ActionItemStatus.OPEN
    suggested_owner: str | None = None
    actual_owner: str | None = None
    due_date: datetime | None = None
    completed_at: datetime | None = None
    jira_ticket_id: str | None = None
    code_suggestion: CodeSnippet | None = None
    effectiveness_score: float | None = None
    from_similar_incident: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "title": self.title,
            "description": self.description,
            "why": self.why,
            "how": self.how,
            "priority": self.priority.value,
            "effort": self.effort.value,
            "status": self.status.value,
            "suggested_owner": self.suggested_owner,
            "actual_owner": self.actual_owner,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "jira_ticket": self.jira_ticket_id,
            "code_suggestion": self.code_suggestion.to_dict() if self.code_suggestion else None,
            "effectiveness_score": self.effectiveness_score,
            "from_similar_incident": self.from_similar_incident,
        }


@dataclass
class SimilarIncident:
    """A similar past incident."""

    incident_id: str
    title: str
    date: datetime
    similarity_score: float
    matching_factors: list[str] = field(default_factory=list)
    differing_factors: list[str] = field(default_factory=list)
    root_cause: str | None = None
    resolution: str | None = None
    unfulfilled_action_items: list[ActionItem] = field(default_factory=list)
    lessons_applicable: list[str] = field(default_factory=list)
    mttr_minutes: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "date": self.date.isoformat(),
            "similarity_percent": round(self.similarity_score * 100, 0),
            "matching_factors": self.matching_factors,
            "differing_factors": self.differing_factors,
            "root_cause": self.root_cause,
            "resolution": self.resolution,
            "unfulfilled_actions": [a.to_dict() for a in self.unfulfilled_action_items],
            "lessons_applicable": self.lessons_applicable,
            "mttr_minutes": self.mttr_minutes,
        }


@dataclass
class Lesson:
    """A lesson learned from the incident."""

    lesson_id: str
    title: str
    description: str
    scope: LessonScope
    suggested_actions: list[str] = field(default_factory=list)
    knowledge_base_updates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.lesson_id,
            "title": self.title,
            "description": self.description,
            "scope": self.scope.value,
            "suggested_actions": self.suggested_actions,
            "knowledge_base_updates": self.knowledge_base_updates,
        }


@dataclass
class ImpactMetrics:
    """Impact metrics for the incident."""

    failed_requests: int = 0
    affected_users: int = 0
    error_rate_peak: float = 0.0
    success_rate_during: float = 0.0
    success_rate_baseline: float = 0.99
    estimated_lost_orders: int = 0
    estimated_lost_revenue: float = 0.0
    support_tickets: int = 0
    social_mentions: int = 0
    sla_breaches: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_impact": {
                "failed_requests": self.failed_requests,
                "affected_users": self.affected_users,
                "error_rate_peak_percent": round(self.error_rate_peak * 100, 1),
                "success_rate_during_percent": round(self.success_rate_during * 100, 1),
                "success_rate_baseline_percent": round(self.success_rate_baseline * 100, 1),
            },
            "business_impact": {
                "estimated_lost_orders": self.estimated_lost_orders,
                "estimated_lost_revenue": round(self.estimated_lost_revenue, 2),
                "support_tickets": self.support_tickets,
                "social_mentions": self.social_mentions,
            },
            "sla_breaches": self.sla_breaches,
        }


@dataclass
class PostmortemConfig:
    """Configuration for postmortem generation."""

    depth: str = "comprehensive"
    audiences: list[str] = field(default_factory=lambda: ["engineering"])
    enforce_blameless: bool = True
    include_code_snippets: bool = True
    include_metrics: bool = True
    include_graphs: bool = True
    template_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "audiences": self.audiences,
            "enforce_blameless": self.enforce_blameless,
            "include_code_snippets": self.include_code_snippets,
            "include_metrics": self.include_metrics,
            "include_graphs": self.include_graphs,
            "template_id": self.template_id,
        }


@dataclass
class Postmortem:
    """Complete postmortem document."""

    postmortem_id: UUID
    incident_id: str
    status: PostmortemStatus
    config: PostmortemConfig

    title: str
    severity: str
    author: str

    executive_summary: str = ""
    timeline: list[TimelineEvent] = field(default_factory=list)
    root_cause_analysis: RootCauseAnalysis | None = None
    contributing_factors: list[ContributingFactor] = field(default_factory=list)
    impact: ImpactMetrics | None = None
    action_items: list[ActionItem] = field(default_factory=list)
    similar_incidents: list[SimilarIncident] = field(default_factory=list)
    lessons_learned: list[Lesson] = field(default_factory=list)
    what_went_well: list[str] = field(default_factory=list)
    what_could_improve: list[str] = field(default_factory=list)

    template_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "ai"
    reviewed_by: list[str] = field(default_factory=list)
    approved_at: datetime | None = None
    published_at: datetime | None = None

    completeness_score: float = 0.0
    blameless_score: float = 0.0
    evidence_confidence: float = 0.0

    @classmethod
    def create(
        cls,
        incident_id: str,
        title: str,
        severity: str,
        author: str = "AI + Human",
        config: PostmortemConfig | None = None,
    ) -> Postmortem:
        return cls(
            postmortem_id=uuid4(),
            incident_id=incident_id,
            status=PostmortemStatus.DRAFT,
            config=config or PostmortemConfig(),
            title=title,
            severity=severity,
            author=author,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.postmortem_id),
            "incident_id": self.incident_id,
            "status": self.status.value,
            "config": self.config.to_dict(),
            "header": {
                "title": self.title,
                "severity": self.severity,
                "author": self.author,
            },
            "executive_summary": self.executive_summary,
            "timeline": [e.to_dict() for e in self.timeline],
            "root_cause_analysis": (
                self.root_cause_analysis.to_dict() if self.root_cause_analysis else None
            ),
            "contributing_factors": [f.to_dict() for f in self.contributing_factors],
            "impact": self.impact.to_dict() if self.impact else None,
            "action_items": [a.to_dict() for a in self.action_items],
            "similar_incidents": [s.to_dict() for s in self.similar_incidents],
            "lessons_learned": [lesson.to_dict() for lesson in self.lessons_learned],
            "what_went_well": self.what_went_well,
            "what_could_improve": self.what_could_improve,
            "metadata": {
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "created_by": self.created_by,
                "reviewed_by": self.reviewed_by,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
                "published_at": self.published_at.isoformat() if self.published_at else None,
            },
            "quality_scores": {
                "completeness": round(self.completeness_score * 100, 1),
                "blameless": round(self.blameless_score * 100, 1),
                "evidence_confidence": round(self.evidence_confidence * 100, 1),
            },
        }

    def to_markdown(self) -> str:
        """Generate markdown representation."""
        lines = [
            f"# Postmortem: {self.title}",
            "",
            f"**Incident ID:** {self.incident_id}",
            f"**Severity:** {self.severity}",
            f"**Author:** {self.author}",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
            self.executive_summary,
            "",
        ]

        if self.impact:
            lines.extend([
                "---",
                "",
                "## Impact Analysis",
                "",
                "### Customer Impact",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Failed requests | {self.impact.failed_requests:,} |",
                f"| Affected users | {self.impact.affected_users:,} |",
                f"| Error rate at peak | {self.impact.error_rate_peak * 100:.1f}% |",
                "",
                "### Business Impact",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Estimated lost orders | {self.impact.estimated_lost_orders:,} |",
                f"| Estimated lost revenue | ${self.impact.estimated_lost_revenue:,.2f} |",
                f"| Support tickets | {self.impact.support_tickets} |",
                "",
            ])

        if self.timeline:
            lines.extend([
                "---",
                "",
                "## Timeline",
                "",
                "| Time (UTC) | Event | Source |",
                "|------------|-------|--------|",
            ])
            for event in self.timeline:
                lines.append(
                    f"| {event.timestamp.strftime('%H:%M:%S')} | "
                    f"{event.event} | {event.source.value} |"
                )
            lines.append("")

        if self.root_cause_analysis:
            lines.extend([
                "---",
                "",
                "## Root Cause Analysis",
                "",
                "### Summary",
                self.root_cause_analysis.summary,
                "",
                "### Technical Details",
                self.root_cause_analysis.technical_details,
                "",
            ])

            if self.root_cause_analysis.five_whys:
                lines.extend([
                    "### Five Whys",
                    "",
                ])
                for why in self.root_cause_analysis.five_whys:
                    lines.extend([
                        f"**Why #{why.level}:** {why.question}",
                        f"**Answer:** {why.answer}",
                        f"**Confidence:** {why.confidence * 100:.0f}%",
                        "",
                    ])

        if self.contributing_factors:
            lines.extend([
                "---",
                "",
                "## Contributing Factors",
                "",
            ])
            for factor in self.contributing_factors:
                impact_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
                lines.extend([
                    f"### {impact_emoji.get(factor.impact.value, '')} {factor.description}",
                    f"**Category:** {factor.category.value}",
                    f"**Impact:** {factor.impact.value}",
                    "",
                ])
                if factor.recommendation:
                    lines.append(f"**Recommendation:** {factor.recommendation}")
                    lines.append("")

        if self.action_items:
            lines.extend([
                "---",
                "",
                "## Action Items",
                "",
                "| ID | Action | Owner | Due | Status |",
                "|----|--------|-------|-----|--------|",
            ])
            for item in self.action_items:
                status_emoji = {
                    "open": "🔴",
                    "in_progress": "🟡",
                    "completed": "✅",
                    "wont_do": "⏭️",
                    "blocked": "🚫",
                }
                due = item.due_date.strftime("%Y-%m-%d") if item.due_date else "TBD"
                owner = item.actual_owner or item.suggested_owner or "TBD"
                lines.append(
                    f"| {item.item_id} | {item.title} | {owner} | {due} | "
                    f"{status_emoji.get(item.status.value, '')} {item.status.value} |"
                )
            lines.append("")

        if self.what_went_well:
            lines.extend([
                "---",
                "",
                "## What Went Well",
                "",
            ])
            for well_item in self.what_went_well:
                lines.append(f"- {well_item}")
            lines.append("")

        if self.what_could_improve:
            lines.extend([
                "---",
                "",
                "## What Could Be Improved",
                "",
            ])
            for improve_item in self.what_could_improve:
                lines.append(f"- {improve_item}")
            lines.append("")

        if self.lessons_learned:
            lines.extend([
                "---",
                "",
                "## Lessons Learned",
                "",
            ])
            for lesson in self.lessons_learned:
                lines.extend([
                    f"### {lesson.title}",
                    f"**Scope:** {lesson.scope.value}",
                    lesson.description,
                    "",
                ])

        lines.extend([
            "---",
            "",
            "## Document History",
            "",
            f"- **Created:** {self.created_at.strftime('%Y-%m-%d')} by {self.created_by}",
        ])
        if self.reviewed_by:
            lines.append(f"- **Reviewed by:** {', '.join(self.reviewed_by)}")
        if self.approved_at:
            lines.append(f"- **Approved:** {self.approved_at.strftime('%Y-%m-%d')}")

        return "\n".join(lines)
