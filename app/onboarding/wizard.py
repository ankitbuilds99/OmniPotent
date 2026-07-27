"""Onboarding wizard for new users and organizations.

Guides users through:
- Account setup
- Organization configuration
- Integration connections
- Team invitations
- Initial runbook creation
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.onboarding")


class ChecklistStatus(Enum):
    """Status of checklist items."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class OnboardingStepType(Enum):
    """Types of onboarding steps."""

    WELCOME = "welcome"
    PROFILE = "profile"
    ORGANIZATION = "organization"
    INTEGRATIONS = "integrations"
    TEAM = "team"
    RUNBOOKS = "runbooks"
    NOTIFICATIONS = "notifications"
    COMPLETE = "complete"


@dataclass
class ChecklistItem:
    """A checklist item for onboarding."""

    id: str
    title: str
    description: str
    status: ChecklistStatus = ChecklistStatus.NOT_STARTED
    required: bool = True
    action_url: str = ""
    help_url: str = ""
    estimated_minutes: int = 5
    completed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OnboardingStep:
    """A step in the onboarding wizard."""

    id: str
    title: str
    description: str
    step_type: OnboardingStepType
    order: int
    checklist: list[ChecklistItem] = field(default_factory=list)
    form_fields: list[dict[str, Any]] = field(default_factory=list)
    help_text: str = ""
    video_url: str = ""
    is_skippable: bool = True
    completed: bool = False
    skipped: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def is_complete(self) -> bool:
        """Check if step is complete."""
        if self.completed or self.skipped:
            return True

        required_items = [i for i in self.checklist if i.required]
        if not required_items:
            return self.completed

        return all(
            i.status in (ChecklistStatus.COMPLETED, ChecklistStatus.SKIPPED)
            for i in required_items
        )


@dataclass
class OnboardingProgress:
    """Overall onboarding progress."""

    user_id: str
    org_id: str
    current_step_id: str
    steps: list[OnboardingStep] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    is_complete: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_progress_percentage(self) -> int:
        """Calculate progress percentage."""
        if not self.steps:
            return 0
        completed = sum(1 for s in self.steps if s.is_complete())
        return int((completed / len(self.steps)) * 100)

    def get_current_step(self) -> OnboardingStep | None:
        """Get current step."""
        for step in self.steps:
            if step.id == self.current_step_id:
                return step
        return None

    def get_next_step(self) -> OnboardingStep | None:
        """Get next incomplete step."""
        for step in sorted(self.steps, key=lambda s: s.order):
            if not step.is_complete():
                return step
        return None


_ONBOARDING_STATES: dict[str, OnboardingProgress] = {}


class OnboardingWizard:
    """Manages the onboarding wizard flow."""

    def __init__(self):
        self.default_steps = self._create_default_steps()

    def _create_default_steps(self) -> list[OnboardingStep]:
        """Create default onboarding steps."""
        return [
            OnboardingStep(
                id="welcome",
                title="Welcome to DevOps Copilot",
                description="Let's get you set up in just a few minutes",
                step_type=OnboardingStepType.WELCOME,
                order=1,
                help_text="DevOps Copilot helps you investigate incidents faster with AI-powered root cause analysis.",
                is_skippable=False,
            ),
            OnboardingStep(
                id="profile",
                title="Set up your profile",
                description="Tell us about yourself",
                step_type=OnboardingStepType.PROFILE,
                order=2,
                form_fields=[
                    {
                        "name": "display_name",
                        "label": "Display Name",
                        "type": "text",
                        "required": True,
                    },
                    {
                        "name": "role",
                        "label": "Your Role",
                        "type": "select",
                        "options": [
                            "SRE / Platform Engineer",
                            "DevOps Engineer",
                            "Software Engineer",
                            "Engineering Manager",
                            "Other",
                        ],
                        "required": True,
                    },
                    {
                        "name": "timezone",
                        "label": "Timezone",
                        "type": "timezone",
                        "required": True,
                    },
                ],
                is_skippable=False,
            ),
            OnboardingStep(
                id="organization",
                title="Organization Setup",
                description="Configure your organization settings",
                step_type=OnboardingStepType.ORGANIZATION,
                order=3,
                form_fields=[
                    {
                        "name": "org_name",
                        "label": "Organization Name",
                        "type": "text",
                        "required": True,
                    },
                    {
                        "name": "org_slug",
                        "label": "URL Slug",
                        "type": "text",
                        "required": True,
                        "placeholder": "my-company",
                    },
                    {
                        "name": "industry",
                        "label": "Industry",
                        "type": "select",
                        "options": [
                            "Technology / SaaS",
                            "E-commerce",
                            "Financial Services",
                            "Healthcare",
                            "Media / Entertainment",
                            "Other",
                        ],
                    },
                    {
                        "name": "team_size",
                        "label": "Engineering Team Size",
                        "type": "select",
                        "options": ["1-10", "11-50", "51-200", "200+"],
                    },
                ],
                is_skippable=False,
            ),
            OnboardingStep(
                id="integrations",
                title="Connect Your Tools",
                description="Connect monitoring and alerting tools",
                step_type=OnboardingStepType.INTEGRATIONS,
                order=4,
                checklist=[
                    ChecklistItem(
                        id="prometheus",
                        title="Connect Prometheus",
                        description="Pull metrics for incident correlation",
                        required=False,
                        action_url="/settings/integrations/prometheus",
                        estimated_minutes=5,
                    ),
                    ChecklistItem(
                        id="datadog",
                        title="Connect Datadog",
                        description="Import metrics, logs, and APM traces",
                        required=False,
                        action_url="/settings/integrations/datadog",
                        estimated_minutes=5,
                    ),
                    ChecklistItem(
                        id="pagerduty",
                        title="Connect PagerDuty",
                        description="Sync incidents and on-call schedules",
                        required=False,
                        action_url="/settings/integrations/pagerduty",
                        estimated_minutes=3,
                    ),
                    ChecklistItem(
                        id="slack",
                        title="Connect Slack",
                        description="Send notifications and interact via Slack",
                        required=False,
                        action_url="/settings/integrations/slack",
                        estimated_minutes=3,
                    ),
                    ChecklistItem(
                        id="github",
                        title="Connect GitHub",
                        description="Track deployments and code changes",
                        required=False,
                        action_url="/settings/integrations/github",
                        estimated_minutes=3,
                    ),
                ],
                help_text="You can always add more integrations later from Settings.",
                is_skippable=True,
            ),
            OnboardingStep(
                id="team",
                title="Invite Your Team",
                description="Add team members to collaborate",
                step_type=OnboardingStepType.TEAM,
                order=5,
                form_fields=[
                    {
                        "name": "invites",
                        "label": "Email Addresses",
                        "type": "email_list",
                        "placeholder": "Enter email addresses, one per line",
                        "required": False,
                    },
                ],
                help_text="Team members will receive an email invitation to join.",
                is_skippable=True,
            ),
            OnboardingStep(
                id="runbooks",
                title="Create Your First Runbook",
                description="Set up automated responses to common incidents",
                step_type=OnboardingStepType.RUNBOOKS,
                order=6,
                checklist=[
                    ChecklistItem(
                        id="view_templates",
                        title="Explore Runbook Templates",
                        description="Browse pre-built runbook templates",
                        required=False,
                        action_url="/runbooks/templates",
                        estimated_minutes=3,
                    ),
                    ChecklistItem(
                        id="create_runbook",
                        title="Create a Runbook",
                        description="Create your first custom runbook",
                        required=False,
                        action_url="/runbooks/new",
                        estimated_minutes=10,
                    ),
                ],
                is_skippable=True,
            ),
            OnboardingStep(
                id="notifications",
                title="Configure Notifications",
                description="Set up how you want to be notified",
                step_type=OnboardingStepType.NOTIFICATIONS,
                order=7,
                form_fields=[
                    {
                        "name": "email_notifications",
                        "label": "Email Notifications",
                        "type": "toggle",
                        "default": True,
                    },
                    {
                        "name": "slack_notifications",
                        "label": "Slack Notifications",
                        "type": "toggle",
                        "default": True,
                    },
                    {
                        "name": "mobile_push",
                        "label": "Mobile Push Notifications",
                        "type": "toggle",
                        "default": False,
                    },
                    {
                        "name": "notification_level",
                        "label": "Minimum Severity",
                        "type": "select",
                        "options": ["All", "Warning and above", "Critical only"],
                        "default": "Warning and above",
                    },
                ],
                is_skippable=True,
            ),
            OnboardingStep(
                id="complete",
                title="You're All Set!",
                description="Your DevOps Copilot is ready",
                step_type=OnboardingStepType.COMPLETE,
                order=8,
                help_text="Start investigating incidents or explore the dashboard.",
                is_skippable=False,
            ),
        ]

    def create_progress(
        self,
        user_id: str,
        org_id: str,
    ) -> OnboardingProgress:
        """Create new onboarding progress for a user."""
        steps = [
            OnboardingStep(
                id=s.id,
                title=s.title,
                description=s.description,
                step_type=s.step_type,
                order=s.order,
                checklist=[
                    ChecklistItem(
                        id=c.id,
                        title=c.title,
                        description=c.description,
                        required=c.required,
                        action_url=c.action_url,
                        help_url=c.help_url,
                        estimated_minutes=c.estimated_minutes,
                    )
                    for c in s.checklist
                ],
                form_fields=s.form_fields.copy(),
                help_text=s.help_text,
                video_url=s.video_url,
                is_skippable=s.is_skippable,
            )
            for s in self.default_steps
        ]

        progress = OnboardingProgress(
            user_id=user_id,
            org_id=org_id,
            current_step_id=steps[0].id if steps else "",
            steps=steps,
        )

        _ONBOARDING_STATES[user_id] = progress
        log.info("Created onboarding progress for user %s", user_id)

        return progress

    def get_progress(self, user_id: str) -> OnboardingProgress | None:
        """Get onboarding progress for a user."""
        return _ONBOARDING_STATES.get(user_id)

    def update_step(
        self,
        user_id: str,
        step_id: str,
        data: dict[str, Any],
    ) -> OnboardingStep | None:
        """Update a step with form data."""
        progress = self.get_progress(user_id)
        if not progress:
            return None

        for step in progress.steps:
            if step.id == step_id:
                step.data.update(data)
                log.info("Updated onboarding step %s for user %s", step_id, user_id)
                return step

        return None

    def complete_step(
        self,
        user_id: str,
        step_id: str,
    ) -> OnboardingProgress | None:
        """Mark a step as completed and advance."""
        progress = self.get_progress(user_id)
        if not progress:
            return None

        for step in progress.steps:
            if step.id == step_id:
                step.completed = True
                for item in step.checklist:
                    if item.status == ChecklistStatus.NOT_STARTED:
                        item.status = ChecklistStatus.COMPLETED
                        item.completed_at = datetime.now(timezone.utc)
                break

        next_step = progress.get_next_step()
        if next_step:
            progress.current_step_id = next_step.id
        else:
            progress.is_complete = True
            progress.completed_at = datetime.now(timezone.utc)
            log.info("Onboarding completed for user %s", user_id)

        return progress

    def skip_step(
        self,
        user_id: str,
        step_id: str,
    ) -> OnboardingProgress | None:
        """Skip a step if allowed."""
        progress = self.get_progress(user_id)
        if not progress:
            return None

        for step in progress.steps:
            if step.id == step_id:
                if not step.is_skippable:
                    return None
                step.skipped = True
                break

        next_step = progress.get_next_step()
        if next_step:
            progress.current_step_id = next_step.id
        else:
            progress.is_complete = True
            progress.completed_at = datetime.now(timezone.utc)

        return progress

    def update_checklist_item(
        self,
        user_id: str,
        step_id: str,
        item_id: str,
        status: ChecklistStatus,
    ) -> ChecklistItem | None:
        """Update a checklist item status."""
        progress = self.get_progress(user_id)
        if not progress:
            return None

        for step in progress.steps:
            if step.id == step_id:
                for item in step.checklist:
                    if item.id == item_id:
                        item.status = status
                        if status == ChecklistStatus.COMPLETED:
                            item.completed_at = datetime.now(timezone.utc)
                        return item

        return None

    def get_estimated_time_remaining(self, user_id: str) -> int:
        """Get estimated time remaining in minutes."""
        progress = self.get_progress(user_id)
        if not progress:
            return 0

        total = 0
        for step in progress.steps:
            if not step.is_complete():
                total += 3
                for item in step.checklist:
                    if item.status not in (
                        ChecklistStatus.COMPLETED,
                        ChecklistStatus.SKIPPED,
                    ):
                        total += item.estimated_minutes

        return total


_wizard: OnboardingWizard | None = None


def _get_wizard() -> OnboardingWizard:
    """Get or create wizard singleton."""
    global _wizard
    if _wizard is None:
        _wizard = OnboardingWizard()
    return _wizard


def get_onboarding_state(user_id: str) -> OnboardingProgress | None:
    """Get onboarding state for a user."""
    wizard = _get_wizard()
    return wizard.get_progress(user_id)


def update_onboarding_step(
    user_id: str,
    step_id: str,
    data: dict[str, Any],
) -> OnboardingStep | None:
    """Update onboarding step data."""
    wizard = _get_wizard()
    return wizard.update_step(user_id, step_id, data)


def complete_onboarding_step(user_id: str, step_id: str) -> OnboardingProgress | None:
    """Complete an onboarding step."""
    wizard = _get_wizard()
    return wizard.complete_step(user_id, step_id)


def get_onboarding_checklist(user_id: str) -> list[dict[str, Any]]:
    """Get flattened checklist for user."""
    progress = get_onboarding_state(user_id)
    if not progress:
        return []

    items = []
    for step in progress.steps:
        for item in step.checklist:
            items.append({
                "step_id": step.id,
                "step_title": step.title,
                "item_id": item.id,
                "title": item.title,
                "description": item.description,
                "status": item.status.value,
                "required": item.required,
                "action_url": item.action_url,
            })

    return items


def create_organization_setup(
    user_id: str,
    org_name: str,
    org_slug: str,
) -> OnboardingProgress:
    """Create organization and start onboarding."""
    org_id = f"org_{secrets.token_hex(8)}"

    wizard = _get_wizard()
    progress = wizard.create_progress(user_id, org_id)

    wizard.update_step(user_id, "organization", {
        "org_name": org_name,
        "org_slug": org_slug,
        "org_id": org_id,
    })

    return progress
