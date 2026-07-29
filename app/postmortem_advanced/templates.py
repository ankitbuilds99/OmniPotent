"""Postmortem templates and customization."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

log = logging.getLogger("devcopilot.postmortem_advanced.templates")


class SectionType(str, Enum):
    AI_GENERATED = "ai_generated"
    MANUAL = "manual"
    HYBRID = "hybrid"


@dataclass
class TemplateSection:
    """A section in a postmortem template."""

    section_id: str
    name: str
    title: str
    section_type: SectionType
    required: bool = True
    order: int = 0
    description: str | None = None
    default_content: str | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.section_id,
            "name": self.name,
            "title": self.title,
            "type": self.section_type.value,
            "required": self.required,
            "order": self.order,
            "description": self.description,
            "default_content": self.default_content,
            "fields": self.fields,
        }


@dataclass
class PostmortemTemplate:
    """A postmortem template."""

    template_id: UUID
    name: str
    description: str
    sections: list[TemplateSection]
    created_by: str
    created_at: datetime
    is_builtin: bool = False
    auto_apply_rules: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.template_id),
            "name": self.name,
            "description": self.description,
            "sections": [s.to_dict() for s in self.sections],
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "is_builtin": self.is_builtin,
            "auto_apply_rules": self.auto_apply_rules,
            "tags": self.tags,
        }


class TemplateLibrary:
    """Manages postmortem templates."""

    def __init__(self) -> None:
        self._templates: dict[UUID, PostmortemTemplate] = {}
        self._load_builtin_templates()

    def _load_builtin_templates(self) -> None:
        """Load built-in templates."""
        standard = self._create_standard_template()
        self._templates[standard.template_id] = standard

        quick = self._create_quick_template()
        self._templates[quick.template_id] = quick

        executive = self._create_executive_template()
        self._templates[executive.template_id] = executive

        customer = self._create_customer_template()
        self._templates[customer.template_id] = customer

        compliance = self._create_compliance_template()
        self._templates[compliance.template_id] = compliance

    def _create_standard_template(self) -> PostmortemTemplate:
        """Create the standard SRE template."""
        sections = [
            TemplateSection(
                section_id="executive_summary",
                name="Executive Summary",
                title="Executive Summary",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=1,
                description="High-level summary of the incident and its impact",
            ),
            TemplateSection(
                section_id="impact",
                name="Impact Analysis",
                title="Impact Analysis",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=2,
                description="Customer, business, and SLA impact metrics",
            ),
            TemplateSection(
                section_id="timeline",
                name="Timeline",
                title="Timeline",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=3,
                description="Chronological sequence of events",
            ),
            TemplateSection(
                section_id="root_cause",
                name="Root Cause Analysis",
                title="Root Cause Analysis",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=4,
                description="Technical analysis including Five Whys",
            ),
            TemplateSection(
                section_id="contributing_factors",
                name="Contributing Factors",
                title="Contributing Factors",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=5,
                description="Process, technical, and human factors",
            ),
            TemplateSection(
                section_id="what_went_well",
                name="What Went Well",
                title="What Went Well",
                section_type=SectionType.HYBRID,
                required=True,
                order=6,
                description="Positive aspects of incident response",
            ),
            TemplateSection(
                section_id="what_could_improve",
                name="What Could Be Improved",
                title="What Could Be Improved",
                section_type=SectionType.HYBRID,
                required=True,
                order=7,
                description="Areas for improvement",
            ),
            TemplateSection(
                section_id="action_items",
                name="Action Items",
                title="Action Items",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=8,
                description="Immediate, short-term, and long-term action items",
            ),
            TemplateSection(
                section_id="similar_incidents",
                name="Similar Incidents",
                title="Similar Past Incidents",
                section_type=SectionType.AI_GENERATED,
                required=False,
                order=9,
                description="Related incidents and patterns",
            ),
            TemplateSection(
                section_id="lessons",
                name="Lessons Learned",
                title="Lessons Learned",
                section_type=SectionType.HYBRID,
                required=True,
                order=10,
                description="Technical and organizational lessons",
            ),
        ]

        return PostmortemTemplate(
            template_id=uuid4(),
            name="Standard SRE Template",
            description="Full postmortem with all sections",
            sections=sections,
            created_by="system",
            created_at=datetime.now(timezone.utc),
            is_builtin=True,
            auto_apply_rules={"severity": ["P1", "P2"]},
            tags=["standard", "full"],
        )

    def _create_quick_template(self) -> PostmortemTemplate:
        """Create the quick retrospective template."""
        sections = [
            TemplateSection(
                section_id="summary",
                name="Summary",
                title="Summary",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=1,
                description="Brief summary of what happened",
            ),
            TemplateSection(
                section_id="root_cause",
                name="Root Cause",
                title="Root Cause",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=2,
                description="What caused the incident",
            ),
            TemplateSection(
                section_id="resolution",
                name="Resolution",
                title="Resolution",
                section_type=SectionType.HYBRID,
                required=True,
                order=3,
                description="How it was fixed",
            ),
            TemplateSection(
                section_id="action_items",
                name="Action Items",
                title="Follow-ups",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=4,
                description="Key action items (max 3)",
            ),
        ]

        return PostmortemTemplate(
            template_id=uuid4(),
            name="Quick Retrospective",
            description="Lightweight template for minor incidents",
            sections=sections,
            created_by="system",
            created_at=datetime.now(timezone.utc),
            is_builtin=True,
            auto_apply_rules={"severity": ["P3", "P4"]},
            tags=["quick", "lightweight"],
        )

    def _create_executive_template(self) -> PostmortemTemplate:
        """Create the executive summary template."""
        sections = [
            TemplateSection(
                section_id="summary",
                name="Summary",
                title="Summary",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=1,
                description="One paragraph summary",
            ),
            TemplateSection(
                section_id="business_impact",
                name="Business Impact",
                title="Business Impact",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=2,
                description="Revenue, customers, and SLA impact",
            ),
            TemplateSection(
                section_id="root_cause",
                name="Root Cause",
                title="Root Cause",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=3,
                description="Non-technical explanation",
            ),
            TemplateSection(
                section_id="action_items",
                name="Top Actions",
                title="Top 3 Actions",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=4,
                description="Most important action items",
            ),
            TemplateSection(
                section_id="risk_assessment",
                name="Risk Assessment",
                title="Risk Assessment",
                section_type=SectionType.MANUAL,
                required=True,
                order=5,
                description="Likelihood of recurrence",
            ),
        ]

        return PostmortemTemplate(
            template_id=uuid4(),
            name="Executive Summary",
            description="One-pager for leadership",
            sections=sections,
            created_by="system",
            created_at=datetime.now(timezone.utc),
            is_builtin=True,
            tags=["executive", "summary", "leadership"],
        )

    def _create_customer_template(self) -> PostmortemTemplate:
        """Create the customer incident report template."""
        sections = [
            TemplateSection(
                section_id="incident_overview",
                name="Incident Overview",
                title="Incident Overview",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=1,
                description="Customer-friendly description",
            ),
            TemplateSection(
                section_id="impact",
                name="Impact",
                title="Service Impact",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=2,
                description="What customers experienced",
            ),
            TemplateSection(
                section_id="resolution",
                name="Resolution",
                title="Resolution",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=3,
                description="How we fixed it",
            ),
            TemplateSection(
                section_id="prevention",
                name="Prevention",
                title="Prevention Measures",
                section_type=SectionType.MANUAL,
                required=True,
                order=4,
                description="What we're doing to prevent recurrence",
            ),
        ]

        return PostmortemTemplate(
            template_id=uuid4(),
            name="Customer Incident Report",
            description="Sanitized external communication",
            sections=sections,
            created_by="system",
            created_at=datetime.now(timezone.utc),
            is_builtin=True,
            tags=["customer", "external", "status-page"],
        )

    def _create_compliance_template(self) -> PostmortemTemplate:
        """Create the compliance/audit template."""
        sections = [
            TemplateSection(
                section_id="incident_classification",
                name="Incident Classification",
                title="Incident Classification",
                section_type=SectionType.MANUAL,
                required=True,
                order=1,
                description="Severity, type, and regulatory category",
                fields=[
                    {"name": "severity", "type": "select", "options": ["P1", "P2", "P3", "P4"]},
                    {"name": "incident_type", "type": "select", "options": [
                        "Availability", "Security", "Data Loss", "Performance"
                    ]},
                    {"name": "reportable", "type": "boolean", "label": "Regulatory Reportable"},
                ],
            ),
            TemplateSection(
                section_id="timeline",
                name="Timeline",
                title="Detailed Timeline",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=2,
                description="Complete timeline with evidence",
            ),
            TemplateSection(
                section_id="root_cause",
                name="Root Cause Analysis",
                title="Root Cause Analysis",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=3,
                description="Formal root cause analysis",
            ),
            TemplateSection(
                section_id="data_impact",
                name="Data Impact Assessment",
                title="Data Impact Assessment",
                section_type=SectionType.MANUAL,
                required=True,
                order=4,
                description="Assessment of data exposure or loss",
                fields=[
                    {"name": "data_exposed", "type": "boolean", "label": "Was customer data exposed?"},
                    {"name": "data_classification", "type": "select", "options": [
                        "Public", "Internal", "Confidential", "Restricted"
                    ]},
                    {"name": "records_affected", "type": "number", "label": "Records Affected"},
                ],
            ),
            TemplateSection(
                section_id="corrective_actions",
                name="Corrective Actions",
                title="Corrective Actions",
                section_type=SectionType.AI_GENERATED,
                required=True,
                order=5,
                description="Actions with tracking IDs",
            ),
            TemplateSection(
                section_id="approvals",
                name="Approvals",
                title="Approvals",
                section_type=SectionType.MANUAL,
                required=True,
                order=6,
                description="Required sign-offs",
                fields=[
                    {"name": "tech_lead_approval", "type": "signature"},
                    {"name": "security_approval", "type": "signature"},
                    {"name": "compliance_approval", "type": "signature"},
                ],
            ),
        ]

        return PostmortemTemplate(
            template_id=uuid4(),
            name="Compliance/Audit Template",
            description="SOC2/ISO 27001 compliant format",
            sections=sections,
            created_by="system",
            created_at=datetime.now(timezone.utc),
            is_builtin=True,
            auto_apply_rules={"tags": ["security", "compliance", "data-breach"]},
            tags=["compliance", "audit", "soc2", "iso27001"],
        )

    def get_template(self, template_id: UUID) -> PostmortemTemplate | None:
        """Get a template by ID."""
        return self._templates.get(template_id)

    def get_template_by_name(self, name: str) -> PostmortemTemplate | None:
        """Get a template by name."""
        for template in self._templates.values():
            if template.name.lower() == name.lower():
                return template
        return None

    def list_templates(
        self,
        include_custom: bool = True,
        tag: str | None = None,
    ) -> list[PostmortemTemplate]:
        """List available templates."""
        templates = list(self._templates.values())

        if not include_custom:
            templates = [t for t in templates if t.is_builtin]

        if tag:
            templates = [t for t in templates if tag in t.tags]

        return sorted(templates, key=lambda t: (not t.is_builtin, t.name))

    def create_template(
        self,
        name: str,
        description: str,
        sections: list[TemplateSection],
        created_by: str,
        auto_apply_rules: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> PostmortemTemplate:
        """Create a new custom template."""
        template = PostmortemTemplate(
            template_id=uuid4(),
            name=name,
            description=description,
            sections=sections,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            is_builtin=False,
            auto_apply_rules=auto_apply_rules or {},
            tags=tags or [],
        )

        self._templates[template.template_id] = template
        return template

    def clone_template(
        self,
        template_id: UUID,
        new_name: str,
        created_by: str,
    ) -> PostmortemTemplate | None:
        """Clone an existing template."""
        original = self._templates.get(template_id)
        if not original:
            return None

        sections = [
            TemplateSection(
                section_id=s.section_id,
                name=s.name,
                title=s.title,
                section_type=s.section_type,
                required=s.required,
                order=s.order,
                description=s.description,
                default_content=s.default_content,
                fields=s.fields.copy(),
            )
            for s in original.sections
        ]

        return self.create_template(
            name=new_name,
            description=f"Cloned from {original.name}",
            sections=sections,
            created_by=created_by,
            auto_apply_rules=original.auto_apply_rules.copy(),
            tags=original.tags.copy(),
        )

    def update_template(
        self,
        template_id: UUID,
        updates: dict[str, Any],
    ) -> PostmortemTemplate | None:
        """Update a template."""
        template = self._templates.get(template_id)
        if not template or template.is_builtin:
            return None

        if "name" in updates:
            template.name = updates["name"]
        if "description" in updates:
            template.description = updates["description"]
        if "sections" in updates:
            template.sections = updates["sections"]
        if "auto_apply_rules" in updates:
            template.auto_apply_rules = updates["auto_apply_rules"]
        if "tags" in updates:
            template.tags = updates["tags"]

        return template

    def delete_template(self, template_id: UUID) -> bool:
        """Delete a custom template."""
        template = self._templates.get(template_id)
        if not template or template.is_builtin:
            return False

        del self._templates[template_id]
        return True

    def select_template_for_incident(
        self,
        incident_data: dict[str, Any],
    ) -> PostmortemTemplate:
        """Select the best template for an incident based on auto-apply rules."""
        severity = incident_data.get("severity", "P2")
        tags = incident_data.get("tags", [])

        for template in self._templates.values():
            rules = template.auto_apply_rules

            if "severity" in rules and severity in rules["severity"]:
                return template

            if "tags" in rules:
                if any(tag in rules["tags"] for tag in tags):
                    return template

        return self.get_template_by_name("Standard SRE Template") or list(
            self._templates.values()
        )[0]

    def add_section_to_template(
        self,
        template_id: UUID,
        section: TemplateSection,
    ) -> bool:
        """Add a section to a template."""
        template = self._templates.get(template_id)
        if not template or template.is_builtin:
            return False

        section.order = len(template.sections) + 1
        template.sections.append(section)
        return True

    def remove_section_from_template(
        self,
        template_id: UUID,
        section_id: str,
    ) -> bool:
        """Remove a section from a template."""
        template = self._templates.get(template_id)
        if not template or template.is_builtin:
            return False

        template.sections = [
            s for s in template.sections if s.section_id != section_id
        ]

        for i, section in enumerate(template.sections):
            section.order = i + 1

        return True

    def reorder_sections(
        self,
        template_id: UUID,
        section_order: list[str],
    ) -> bool:
        """Reorder sections in a template."""
        template = self._templates.get(template_id)
        if not template or template.is_builtin:
            return False

        section_map = {s.section_id: s for s in template.sections}

        new_sections = []
        for i, section_id in enumerate(section_order):
            if section_id in section_map:
                section = section_map[section_id]
                section.order = i + 1
                new_sections.append(section)

        for section in template.sections:
            if section.section_id not in section_order:
                section.order = len(new_sections) + 1
                new_sections.append(section)

        template.sections = new_sections
        return True


_library: TemplateLibrary | None = None


def get_template_library() -> TemplateLibrary:
    """Get the default template library."""
    global _library
    if _library is None:
        _library = TemplateLibrary()
    return _library
