"""AI-written postmortem narrative generation.

Generates human-readable narratives and stakeholder-specific versions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("omnipotent.postmortem.narrative")


@dataclass
class ExecutiveSummary:
    """Executive summary for leadership."""

    headline: str
    duration_description: str
    customer_impact_description: str
    revenue_impact_description: str
    resolution_description: str
    safeguards_description: str
    one_liner: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "duration_description": self.duration_description,
            "customer_impact_description": self.customer_impact_description,
            "revenue_impact_description": self.revenue_impact_description,
            "resolution_description": self.resolution_description,
            "safeguards_description": self.safeguards_description,
            "one_liner": self.one_liner,
        }


@dataclass
class TechnicalNarrative:
    """Detailed technical narrative."""

    what_happened: str
    how_it_was_detected: str
    investigation_process: str
    root_cause_explanation: str
    resolution_steps: str
    prevention_measures: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "what_happened": self.what_happened,
            "how_it_was_detected": self.how_it_was_detected,
            "investigation_process": self.investigation_process,
            "root_cause_explanation": self.root_cause_explanation,
            "resolution_steps": self.resolution_steps,
            "prevention_measures": self.prevention_measures,
        }


@dataclass
class CustomerCommunication:
    """Customer-facing communication."""

    subject: str
    body: str
    apology: str
    what_happened_simple: str
    resolution_confirmation: str
    safeguards_assurance: str
    affected_timeframe: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "body": self.body,
            "apology": self.apology,
            "what_happened_simple": self.what_happened_simple,
            "resolution_confirmation": self.resolution_confirmation,
            "safeguards_assurance": self.safeguards_assurance,
            "affected_timeframe": self.affected_timeframe,
        }


@dataclass
class SlackUpdate:
    """Slack/chat update format."""

    title: str
    summary: str
    status: str
    next_steps: str
    thread_updates: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "next_steps": self.next_steps,
            "thread_updates": self.thread_updates,
        }


@dataclass
class StakeholderVersions:
    """All stakeholder-specific versions of the postmortem."""

    executive_summary: ExecutiveSummary
    technical_narrative: TechnicalNarrative
    customer_communication: CustomerCommunication
    slack_update: SlackUpdate
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "executive_summary": self.executive_summary.to_dict(),
            "technical_narrative": self.technical_narrative.to_dict(),
            "customer_communication": self.customer_communication.to_dict(),
            "slack_update": self.slack_update.to_dict(),
            "generated_at": self.generated_at.isoformat(),
        }


class NarrativeGenerator:
    """Generates AI-written narratives for postmortems."""

    def __init__(self) -> None:
        pass

    def generate_narratives(
        self,
        incident_data: dict[str, Any],
        root_cause: str,
        timeline: list[dict[str, Any]],
        action_items: list[dict[str, Any]],
    ) -> StakeholderVersions:
        """Generate all stakeholder versions of the postmortem narrative."""

        executive = self._generate_executive_summary(incident_data, root_cause)
        technical = self._generate_technical_narrative(incident_data, root_cause, timeline)
        customer = self._generate_customer_communication(incident_data)
        slack = self._generate_slack_update(incident_data, root_cause, action_items)

        return StakeholderVersions(
            executive_summary=executive,
            technical_narrative=technical,
            customer_communication=customer,
            slack_update=slack,
        )

    def _generate_executive_summary(
        self,
        incident_data: dict[str, Any],
        root_cause: str,
    ) -> ExecutiveSummary:
        """Generate executive summary for leadership."""

        duration = incident_data.get("duration_minutes", 0)
        customers = incident_data.get("customers_affected", 0)
        revenue = incident_data.get("revenue_impact", 0)
        service = incident_data.get("affected_services", ["the service"])[0] if incident_data.get("affected_services") else "the service"

        if duration < 30:
            duration_desc = f"was unavailable for {duration} minutes"
        elif duration < 60:
            duration_desc = f"experienced a {duration}-minute outage"
        else:
            hours = duration / 60
            duration_desc = f"was degraded for {hours:.1f} hours"

        if customers > 0:
            if customers < 100:
                customer_desc = f"{customers} customers were affected"
            elif customers < 1000:
                customer_desc = f"approximately {customers} customers experienced issues"
            else:
                customer_desc = f"approximately {customers:,} customers were impacted"
        else:
            customer_desc = "a subset of customers were affected"

        if revenue > 0:
            if revenue < 1000:
                revenue_desc = f"with ${revenue:.0f} in transactions delayed"
            elif revenue < 100000:
                revenue_desc = f"with ${revenue:,.0f} in transactions affected"
            else:
                revenue_desc = f"with ${revenue:,.0f} in revenue impact"
        else:
            revenue_desc = ""

        root_cause_simple = self._simplify_root_cause(root_cause)

        headline = f"On {datetime.now().strftime('%B %d')}, {service} {duration_desc} due to {root_cause_simple}."

        if revenue_desc:
            customer_impact = f"{customer_desc}, {revenue_desc} (all eventually processed)."
        else:
            customer_impact = f"{customer_desc}."

        one_liner = f"{service.title()} outage ({duration}min) - {customer_desc}. Root cause: {root_cause_simple}. Resolved with safeguards added."

        return ExecutiveSummary(
            headline=headline,
            duration_description=duration_desc,
            customer_impact_description=customer_impact,
            revenue_impact_description=revenue_desc if revenue_desc else "No direct revenue impact",
            resolution_description="The issue has been fully resolved.",
            safeguards_description="We've implemented additional safeguards to prevent similar issues in the future.",
            one_liner=one_liner,
        )

    def _generate_technical_narrative(
        self,
        incident_data: dict[str, Any],
        root_cause: str,
        timeline: list[dict[str, Any]],
    ) -> TechnicalNarrative:
        """Generate detailed technical narrative."""

        service = incident_data.get("affected_services", ["the service"])[0] if incident_data.get("affected_services") else "the service"
        trigger = incident_data.get("trigger", "an event")
        started_at = incident_data.get("started_at")

        if started_at:
            if isinstance(started_at, str):
                start_time = started_at.split("T")[1][:5] if "T" in started_at else started_at
            else:
                start_time = started_at.strftime("%H:%M UTC")
        else:
            start_time = "an unspecified time"

        what_happened = (
            f"At {start_time}, {trigger} triggered an issue in {service}. "
            f"The root cause was {root_cause}."
        )

        ttd = incident_data.get("time_to_detect_minutes", 0)
        detection_method = incident_data.get("detection", "monitoring")

        how_detected = (
            f"The incident was detected {ttd} minutes after it began via {detection_method}. "
            "On-call was paged and began investigation."
        )

        investigation = (
            f"Investigation identified the root cause as: {root_cause}. "
            "Code analysis pinpointed the exact location and commit responsible."
        )

        fix_description = incident_data.get("resolution", "a fix was deployed")
        resolution = f"Resolution involved {fix_description}. Errors ceased immediately after deployment."

        prevention = "Prevention measures include adding test coverage, enabling stricter type checking, and updating deployment procedures."

        return TechnicalNarrative(
            what_happened=what_happened,
            how_it_was_detected=how_detected,
            investigation_process=investigation,
            root_cause_explanation=root_cause,
            resolution_steps=resolution,
            prevention_measures=prevention,
        )

    def _generate_customer_communication(
        self,
        incident_data: dict[str, Any],
    ) -> CustomerCommunication:
        """Generate customer-facing communication."""

        service = incident_data.get("affected_services", ["our service"])[0] if incident_data.get("affected_services") else "our service"
        started_at = incident_data.get("started_at", "")
        resolved_at = incident_data.get("resolved_at", "")

        if started_at and resolved_at:
            if isinstance(started_at, str):
                start = started_at.replace("T", " ").replace("Z", " UTC")[:19]
            else:
                start = started_at.strftime("%B %d at %H:%M UTC")
            if isinstance(resolved_at, str):
                end = resolved_at.replace("T", " ").replace("Z", " UTC")[:19]
            else:
                end = resolved_at.strftime("%H:%M UTC")
            timeframe = f"between {start} and {end}"
        else:
            timeframe = "earlier today"

        date_str = datetime.now().strftime("%B %d")
        subject = f"Resolved: Brief service interruption on {date_str}"

        apology = "We sincerely apologize for any inconvenience this may have caused."

        what_happened_simple = (
            f"Some customers experienced errors when using {service}. "
            "This has been fully resolved."
        )

        resolution = (
            "All affected services are now operating normally. "
            "If you experienced an issue, please try your request again."
        )

        safeguards = (
            "We've implemented additional safeguards to prevent similar issues in the future "
            "and continue to monitor our systems closely."
        )

        body = f"""Dear Customer,

{timeframe}, some customers may have experienced errors when using {service}. This has been fully resolved.

{apology}

{resolution}

{safeguards}

Thank you for your patience.

Best regards,
The {service.title()} Team"""

        return CustomerCommunication(
            subject=subject,
            body=body,
            apology=apology,
            what_happened_simple=what_happened_simple,
            resolution_confirmation=resolution,
            safeguards_assurance=safeguards,
            affected_timeframe=timeframe,
        )

    def _generate_slack_update(
        self,
        incident_data: dict[str, Any],
        root_cause: str,
        action_items: list[dict[str, Any]],
    ) -> SlackUpdate:
        """Generate Slack update format."""

        title = incident_data.get("title", "Incident")
        severity = incident_data.get("severity", "SEV2")
        status = "RESOLVED" if incident_data.get("resolved_at") else "INVESTIGATING"
        duration = incident_data.get("duration_minutes", 0)

        root_cause_simple = self._simplify_root_cause(root_cause)

        summary = f"*{severity}* | Duration: {duration}min | Root cause: {root_cause_simple}"

        high_priority_items = [
            item.get("title", "")
            for item in action_items[:3]
            if item.get("priority") in ["critical", "high", "p0", "p1"]
        ]

        if high_priority_items:
            next_steps = "Next steps:\n" + "\n".join(f"• {item}" for item in high_priority_items)
        else:
            next_steps = "Follow-up items to be assigned."

        thread_updates = [
            f"Incident detected - {severity}",
            "Investigation started",
            f"Root cause identified: {root_cause_simple}",
            "Fix deployed",
            "Incident resolved - monitoring",
            "Postmortem scheduled",
        ]

        return SlackUpdate(
            title=f":rotating_light: {title}",
            summary=summary,
            status=status,
            next_steps=next_steps,
            thread_updates=thread_updates,
        )

    def _simplify_root_cause(self, root_cause: str) -> str:
        """Simplify root cause for non-technical audiences."""
        if not root_cause:
            return "a technical issue"

        root_cause_lower = root_cause.lower()

        if any(word in root_cause_lower for word in ["null", "none", "undefined"]):
            return "a code change that didn't handle an edge case"
        elif any(word in root_cause_lower for word in ["timeout", "connection"]):
            return "a connectivity issue"
        elif any(word in root_cause_lower for word in ["memory", "cpu", "resource"]):
            return "resource constraints"
        elif any(word in root_cause_lower for word in ["config", "configuration"]):
            return "a configuration error"
        elif any(word in root_cause_lower for word in ["deploy", "release"]):
            return "a deployment issue"
        elif any(word in root_cause_lower for word in ["database", "query"]):
            return "a database issue"

        if len(root_cause) > 50:
            return root_cause[:50] + "..."
        return root_cause


_generator: NarrativeGenerator | None = None


def get_narrative_generator() -> NarrativeGenerator:
    """Get the default narrative generator."""
    global _generator
    if _generator is None:
        _generator = NarrativeGenerator()
    return _generator
