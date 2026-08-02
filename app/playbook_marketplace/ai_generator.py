"""AI Playbook Generator.

Generates playbooks from natural language descriptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.playbook_marketplace.catalog import (
    Playbook,
    PlaybookCategory,
    PlaybookStatus,
    PlaybookStep,
)

log = logging.getLogger("omnipotent.playbook_marketplace.ai_generator")


@dataclass
class GenerationRequest:
    """Request to generate a playbook."""

    request_id: str
    user_id: str
    org_id: str
    description: str
    category: PlaybookCategory | None = None
    target_services: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.request_id,
            "description": self.description,
            "category": self.category.value if self.category else None,
            "services": self.target_services,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class GenerationResult:
    """Result of generating a playbook."""

    request_id: str
    playbook: Playbook | None
    success: bool
    confidence: float
    suggestions: list[str] = field(default_factory=list)
    generation_time_seconds: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "success": self.success,
            "confidence": round(self.confidence, 2),
            "playbook": self.playbook.to_dict() if self.playbook else None,
            "suggestions": self.suggestions,
            "generation_time": round(self.generation_time_seconds, 2),
        }


class AIPlaybookGenerator:
    """Generates playbooks using AI."""

    def __init__(self) -> None:
        self._templates: dict[str, dict[str, Any]] = {}
        self._generation_history: dict[str, list[GenerationResult]] = {}
        self._init_templates()

    def _init_templates(self) -> None:
        """Initialize playbook templates for common scenarios."""
        self._templates["webhook_recovery"] = {
            "patterns": ["webhook", "endpoint", "retry", "failed"],
            "category": PlaybookCategory.ALERTING,
            "steps": [
                {
                    "title": "Check Webhook Status",
                    "action_type": "http_check",
                    "description": "Verify webhook endpoint is responding",
                },
                {
                    "title": "Check Recent Failures",
                    "action_type": "log_query",
                    "description": "Query logs for failed webhook deliveries",
                },
                {
                    "title": "Retry Failed Webhooks",
                    "action_type": "webhook_retry",
                    "description": "Retry delivery of failed webhooks",
                },
                {
                    "title": "Notify Team",
                    "action_type": "slack_notify",
                    "description": "Notify team of webhook issues",
                },
            ],
        }

        self._templates["cache_clear"] = {
            "patterns": ["cache", "clear", "purge", "stale", "invalidate"],
            "category": PlaybookCategory.CACHE,
            "steps": [
                {
                    "title": "Identify Stale Keys",
                    "action_type": "cache_scan",
                    "description": "Scan for stale or corrupted cache keys",
                },
                {
                    "title": "Backup Hot Keys",
                    "action_type": "cache_backup",
                    "description": "Backup frequently accessed keys",
                },
                {
                    "title": "Clear Cache",
                    "action_type": "cache_purge",
                    "description": "Purge specified cache keys",
                },
                {
                    "title": "Verify Recovery",
                    "action_type": "health_check",
                    "description": "Verify cache is rebuilding correctly",
                },
            ],
        }

        self._templates["elasticsearch_yellow"] = {
            "patterns": ["elasticsearch", "cluster", "yellow", "red", "shard"],
            "category": PlaybookCategory.DATABASE,
            "steps": [
                {
                    "title": "Check Cluster Health",
                    "action_type": "elasticsearch_query",
                    "description": "Get cluster health status",
                },
                {
                    "title": "Identify Unassigned Shards",
                    "action_type": "elasticsearch_query",
                    "description": "Find shards that are not assigned",
                },
                {
                    "title": "Reallocate Shards",
                    "action_type": "elasticsearch_action",
                    "description": "Attempt to reallocate unassigned shards",
                },
                {
                    "title": "Check Node Status",
                    "action_type": "elasticsearch_query",
                    "description": "Verify all nodes are healthy",
                },
            ],
        }

        self._templates["api_rate_limit"] = {
            "patterns": ["rate", "limit", "429", "throttle", "api"],
            "category": PlaybookCategory.ALERTING,
            "steps": [
                {
                    "title": "Identify Rate Limited Clients",
                    "action_type": "log_query",
                    "description": "Find clients hitting rate limits",
                },
                {
                    "title": "Check Current Limits",
                    "action_type": "config_query",
                    "description": "Get current rate limit configuration",
                },
                {
                    "title": "Adjust Limits",
                    "action_type": "config_update",
                    "description": "Temporarily increase limits if appropriate",
                },
                {
                    "title": "Notify Affected Clients",
                    "action_type": "notification",
                    "description": "Notify clients of rate limit issues",
                },
            ],
        }

    def generate(
        self,
        request: GenerationRequest,
    ) -> GenerationResult:
        """Generate a playbook from a description."""
        start_time = datetime.now(timezone.utc)

        description_lower = request.description.lower()

        best_match = None
        best_score = 0

        for template_name, template in self._templates.items():
            patterns = template["patterns"]
            score = sum(1 for p in patterns if p in description_lower)

            if score > best_score:
                best_score = score
                best_match = template_name

        if best_match and best_score >= 2:
            template = self._templates[best_match]
            playbook = self._create_playbook_from_template(
                request,
                template,
                best_match,
            )
            confidence = min(0.95, 0.5 + (best_score * 0.15))
        else:
            playbook = self._create_generic_playbook(request)
            confidence = 0.6

        end_time = datetime.now(timezone.utc)
        generation_time = (end_time - start_time).total_seconds()

        suggestions = self._generate_suggestions(request, playbook)

        result = GenerationResult(
            request_id=request.request_id,
            playbook=playbook,
            success=True,
            confidence=confidence,
            suggestions=suggestions,
            generation_time_seconds=generation_time,
        )

        if request.user_id not in self._generation_history:
            self._generation_history[request.user_id] = []
        self._generation_history[request.user_id].append(result)

        log.info(
            "Generated playbook: request=%s confidence=%.2f time=%.2fs",
            request.request_id,
            confidence,
            generation_time,
        )

        return result

    def _create_playbook_from_template(
        self,
        request: GenerationRequest,
        template: dict[str, Any],
        template_name: str,
    ) -> Playbook:
        """Create playbook from matched template."""
        words = request.description.split()[:5]
        name = " ".join(words).title()

        steps = []
        for i, step_def in enumerate(template["steps"]):
            step = PlaybookStep(
                step_id=f"step_{i + 1}",
                title=step_def["title"],
                description=step_def["description"],
                action_type=step_def["action_type"],
                action_config={},
            )
            steps.append(step)

        category = request.category or template["category"]

        return Playbook(
            playbook_id=f"ai_{request.request_id}",
            name=name,
            description=request.description,
            category=category,
            author=request.user_id,
            version="1.0.0",
            steps=steps,
            tags=template["patterns"][:4],
            status=PlaybookStatus.DRAFT,
        )

    def _create_generic_playbook(self, request: GenerationRequest) -> Playbook:
        """Create a generic playbook structure."""
        words = request.description.split()[:5]
        name = " ".join(words).title()

        steps = [
            PlaybookStep(
                step_id="step_1",
                title="Initial Assessment",
                description="Gather initial information about the issue",
                action_type="assessment",
                action_config={},
            ),
            PlaybookStep(
                step_id="step_2",
                title="Check Logs",
                description="Query relevant logs for the issue",
                action_type="log_query",
                action_config={"query": "error OR exception"},
            ),
            PlaybookStep(
                step_id="step_3",
                title="Check Metrics",
                description="Review relevant metrics",
                action_type="metrics_query",
                action_config={},
            ),
            PlaybookStep(
                step_id="step_4",
                title="Take Action",
                description="Apply remediation steps",
                action_type="manual_action",
                action_config={"prompt": "Review and take appropriate action"},
            ),
            PlaybookStep(
                step_id="step_5",
                title="Verify Resolution",
                description="Confirm the issue is resolved",
                action_type="health_check",
                action_config={},
            ),
        ]

        category = request.category or PlaybookCategory.GENERAL

        return Playbook(
            playbook_id=f"ai_{request.request_id}",
            name=name,
            description=request.description,
            category=category,
            author=request.user_id,
            version="1.0.0",
            steps=steps,
            status=PlaybookStatus.DRAFT,
        )

    def _generate_suggestions(
        self,
        request: GenerationRequest,
        playbook: Playbook,
    ) -> list[str]:
        """Generate suggestions for improving the playbook."""
        suggestions = []

        if len(playbook.steps) < 4:
            suggestions.append("Consider adding more detailed steps")

        if not playbook.tags:
            suggestions.append("Add tags to improve discoverability")

        if not playbook.required_integrations:
            suggestions.append("Specify required integrations for better compatibility")

        if len(request.description) < 50:
            suggestions.append("Provide a more detailed description for better generation")

        if "test" not in [s.action_type for s in playbook.steps]:
            suggestions.append("Consider adding a verification/test step")

        return suggestions[:3]

    def get_history(
        self,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get generation history for a user."""
        history = self._generation_history.get(user_id, [])
        return [r.to_dict() for r in history[-limit:]]

    def suggest_from_incident(
        self,
        incident_title: str,
        root_cause: str,
        services: list[str],
    ) -> GenerationRequest:
        """Create a generation request from incident data."""
        description = (
            f"Create a runbook for when {incident_title}. "
            f"Root cause: {root_cause}. "
            f"Services involved: {', '.join(services)}."
        )

        category = None
        if any(s in services for s in ["kubernetes", "k8s", "pod"]):
            category = PlaybookCategory.KUBERNETES
        elif any(s in services for s in ["postgres", "mysql", "database", "db"]):
            category = PlaybookCategory.DATABASE
        elif any(s in services for s in ["aws", "s3", "ec2", "rds"]):
            category = PlaybookCategory.AWS

        return GenerationRequest(
            request_id=f"inc_{datetime.now(timezone.utc).timestamp()}",
            user_id="system",
            org_id="system",
            description=description,
            category=category,
            target_services=services,
        )


_generator: AIPlaybookGenerator | None = None


def get_ai_generator() -> AIPlaybookGenerator:
    """Get the default AI generator."""
    global _generator
    if _generator is None:
        _generator = AIPlaybookGenerator()
    return _generator
