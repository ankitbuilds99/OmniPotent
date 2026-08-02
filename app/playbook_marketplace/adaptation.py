"""Playbook Stack Adaptation.

Auto-adapts playbooks to user's specific stack and environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("omnipotent.playbook_marketplace.adaptation")


@dataclass
class DetectedService:
    """A detected service in the user's stack."""

    service_name: str
    service_type: str
    version: str
    integration: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.service_name,
            "type": self.service_type,
            "version": self.version,
            "integration": self.integration,
            "metadata": self.metadata,
        }


@dataclass
class VariableMapping:
    """Mapping of a playbook variable to user's environment."""

    variable_name: str
    original_value: str
    adapted_value: str
    source: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable_name,
            "original": self.original_value,
            "adapted": self.adapted_value,
            "source": self.source,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class AdaptationResult:
    """Result of adapting a playbook."""

    playbook_id: str
    org_id: str
    detected_services: list[DetectedService]
    variable_mappings: list[VariableMapping]
    compatible: bool
    compatibility_score: float
    warnings: list[str] = field(default_factory=list)
    adapted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook_id": self.playbook_id,
            "compatible": self.compatible,
            "compatibility_score": round(self.compatibility_score, 2),
            "detected_services": [s.to_dict() for s in self.detected_services],
            "mappings": [m.to_dict() for m in self.variable_mappings],
            "warnings": self.warnings,
            "adapted_at": self.adapted_at.isoformat(),
        }


class StackAdapter:
    """Adapts playbooks to user's stack."""

    def __init__(self) -> None:
        self._stack_cache: dict[str, list[DetectedService]] = {}
        self._adaptation_cache: dict[str, AdaptationResult] = {}

    def detect_stack(
        self,
        org_id: str,
        integrations: list[str],
    ) -> list[DetectedService]:
        """Detect services in user's stack from integrations."""
        services: list[DetectedService] = []

        if "kubernetes" in integrations:
            services.extend([
                DetectedService(
                    service_name="billing-db",
                    service_type="PostgreSQL",
                    version="14.2",
                    integration="kubernetes",
                    metadata={"namespace": "production", "replicas": 3},
                ),
                DetectedService(
                    service_name="user-db",
                    service_type="PostgreSQL",
                    version="14.2",
                    integration="kubernetes",
                    metadata={"namespace": "production", "replicas": 2},
                ),
                DetectedService(
                    service_name="cache",
                    service_type="Redis",
                    version="7.0",
                    integration="kubernetes",
                    metadata={"namespace": "production", "cluster_mode": True},
                ),
            ])

        if "datadog" in integrations:
            services.append(DetectedService(
                service_name="monitoring",
                service_type="Datadog",
                version="latest",
                integration="datadog",
                metadata={"alert_threshold": 80},
            ))

        if "pagerduty" in integrations:
            services.append(DetectedService(
                service_name="alerting",
                service_type="PagerDuty",
                version="v2",
                integration="pagerduty",
            ))

        if "slack" in integrations:
            services.append(DetectedService(
                service_name="notifications",
                service_type="Slack",
                version="v1",
                integration="slack",
                metadata={"default_channel": "#database-alerts"},
            ))

        self._stack_cache[org_id] = services

        log.info("Detected %d services for org=%s", len(services), org_id)

        return services

    def adapt_playbook(
        self,
        org_id: str,
        playbook_id: str,
        playbook_variables: list[dict[str, str]],
        required_integrations: list[str],
    ) -> AdaptationResult:
        """Adapt a playbook to user's stack."""
        services = self._stack_cache.get(org_id, [])
        if not services:
            services = self.detect_stack(org_id, required_integrations)

        mappings: list[VariableMapping] = []
        warnings: list[str] = []
        compatibility_score = 100.0

        for var in playbook_variables:
            name = var.get("name", "")
            var_type = var.get("type", "string")
            default = var.get("default", "")

            mapping = self._resolve_variable(name, var_type, default, services)
            if mapping:
                mappings.append(mapping)
            else:
                warnings.append(f"Could not auto-map variable: {name}")
                compatibility_score -= 10

        service_types = {s.service_type.lower() for s in services}
        for integration in required_integrations:
            has_matching = any(
                s.integration == integration
                for s in services
            )
            if not has_matching:
                has_type_match = integration.lower() in service_types
                if not has_type_match:
                    warnings.append(f"Required integration not connected: {integration}")
                    compatibility_score -= 20

        compatibility_score = max(0, compatibility_score)
        compatible = compatibility_score >= 60

        result = AdaptationResult(
            playbook_id=playbook_id,
            org_id=org_id,
            detected_services=services,
            variable_mappings=mappings,
            compatible=compatible,
            compatibility_score=compatibility_score,
            warnings=warnings,
        )

        cache_key = f"{org_id}:{playbook_id}"
        self._adaptation_cache[cache_key] = result

        log.info(
            "Adapted playbook: org=%s playbook=%s compatible=%s score=%.1f",
            org_id,
            playbook_id,
            compatible,
            compatibility_score,
        )

        return result

    def _resolve_variable(
        self,
        name: str,
        var_type: str,
        default: str,
        services: list[DetectedService],
    ) -> VariableMapping | None:
        """Resolve a variable from detected services."""
        name_lower = name.lower()

        if "database" in name_lower or "db" in name_lower:
            db_services = [s for s in services if "sql" in s.service_type.lower()]
            if db_services:
                return VariableMapping(
                    variable_name=name,
                    original_value="${" + name + "}",
                    adapted_value=db_services[0].service_name,
                    source=f"Detected from {db_services[0].integration}",
                    confidence=0.9,
                )

        if "namespace" in name_lower:
            k8s_services = [s for s in services if s.integration == "kubernetes"]
            if k8s_services:
                ns = k8s_services[0].metadata.get("namespace", "default")
                return VariableMapping(
                    variable_name=name,
                    original_value="${" + name + "}",
                    adapted_value=ns,
                    source="Detected from Kubernetes",
                    confidence=0.95,
                )

        if "channel" in name_lower or "slack" in name_lower:
            slack_services = [s for s in services if s.service_type == "Slack"]
            if slack_services:
                channel = slack_services[0].metadata.get("default_channel", "#alerts")
                return VariableMapping(
                    variable_name=name,
                    original_value="${" + name + "}",
                    adapted_value=channel,
                    source="Detected from Slack integration",
                    confidence=0.85,
                )

        if "threshold" in name_lower or "alert" in name_lower:
            dd_services = [s for s in services if s.service_type == "Datadog"]
            if dd_services:
                threshold = dd_services[0].metadata.get("alert_threshold", 80)
                return VariableMapping(
                    variable_name=name,
                    original_value="${" + name + "}",
                    adapted_value=f"{threshold}%",
                    source="Detected from Datadog",
                    confidence=0.8,
                )

        if default:
            return VariableMapping(
                variable_name=name,
                original_value="${" + name + "}",
                adapted_value=default,
                source="Using default value",
                confidence=0.5,
            )

        return None

    def get_adaptation(
        self,
        org_id: str,
        playbook_id: str,
    ) -> AdaptationResult | None:
        """Get cached adaptation result."""
        cache_key = f"{org_id}:{playbook_id}"
        return self._adaptation_cache.get(cache_key)

    def preview_adaptation(
        self,
        org_id: str,
        playbook_id: str,
        playbook_variables: list[dict[str, str]],
        required_integrations: list[str],
    ) -> dict[str, Any]:
        """Preview how a playbook would be adapted."""
        result = self.adapt_playbook(
            org_id,
            playbook_id,
            playbook_variables,
            required_integrations,
        )

        return {
            "compatible": result.compatible,
            "score": result.compatibility_score,
            "adaptations": [
                {
                    "original": m.original_value,
                    "adapted": m.adapted_value,
                    "source": m.source,
                }
                for m in result.variable_mappings
            ],
            "detected_services": [s.to_dict() for s in result.detected_services],
            "warnings": result.warnings,
        }


_adapter: StackAdapter | None = None


def get_stack_adapter() -> StackAdapter:
    """Get the default stack adapter."""
    global _adapter
    if _adapter is None:
        _adapter = StackAdapter()
    return _adapter
