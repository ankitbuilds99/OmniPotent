"""Integration SDK for Partners.

Enables partners to build and publish custom integrations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.integrations_advanced.sdk")


class IntegrationCategory(str, Enum):
    MONITORING = "monitoring"
    LOGGING = "logging"
    ALERTING = "alerting"
    TICKETING = "ticketing"
    COMMUNICATION = "communication"
    CI_CD = "ci_cd"
    CLOUD = "cloud"
    DATABASE = "database"
    SECURITY = "security"
    CUSTOM = "custom"


class PublishStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass
class IntegrationCapabilities:
    """Capabilities provided by an integration."""

    logs: bool = False
    metrics: bool = False
    events: bool = False
    alerts: bool = False
    traces: bool = False
    write_back: bool = False
    webhooks: bool = False
    realtime: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "logs": self.logs,
            "metrics": self.metrics,
            "events": self.events,
            "alerts": self.alerts,
            "traces": self.traces,
            "write_back": self.write_back,
            "webhooks": self.webhooks,
            "realtime": self.realtime,
        }


@dataclass
class IntegrationManifest:
    """Manifest describing an integration."""

    integration_id: str
    name: str
    version: str
    description: str
    author: str
    category: IntegrationCategory
    capabilities: IntegrationCapabilities
    icon_url: str = ""
    docs_url: str = ""
    support_email: str = ""
    oauth_scopes: list[str] = field(default_factory=list)
    required_config: list[dict[str, str]] = field(default_factory=list)
    status: PublishStatus = PublishStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    installs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.integration_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category.value,
            "capabilities": self.capabilities.to_dict(),
            "icon": self.icon_url,
            "docs": self.docs_url,
            "oauth_scopes": self.oauth_scopes,
            "required_config": self.required_config,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "installs": self.installs,
        }


@dataclass
class IntegrationHandler:
    """Handler methods for an integration."""

    fetch_logs: str = ""
    fetch_metrics: str = ""
    fetch_events: str = ""
    fetch_alerts: str = ""
    write_action: str = ""
    handle_webhook: str = ""
    test_connection: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "fetch_logs": self.fetch_logs,
            "fetch_metrics": self.fetch_metrics,
            "fetch_events": self.fetch_events,
            "fetch_alerts": self.fetch_alerts,
            "write_action": self.write_action,
            "handle_webhook": self.handle_webhook,
            "test_connection": self.test_connection,
        }


class IntegrationSDK:
    """SDK for building and managing custom integrations."""

    def __init__(self) -> None:
        self._integrations: dict[str, IntegrationManifest] = {}
        self._handlers: dict[str, IntegrationHandler] = {}
        self._installations: dict[str, list[str]] = {}
        self._init_example_integrations()

    def _init_example_integrations(self) -> None:
        """Initialize example integrations for reference."""
        self._integrations["my-monitoring-tool"] = IntegrationManifest(
            integration_id="my-monitoring-tool",
            name="My Monitoring Tool",
            version="1.0.0",
            description="Example custom monitoring integration",
            author="partner@example.com",
            category=IntegrationCategory.MONITORING,
            capabilities=IntegrationCapabilities(
                logs=True,
                metrics=True,
                events=True,
                write_back=True,
            ),
            required_config=[
                {"name": "api_url", "type": "url", "description": "API endpoint URL"},
                {"name": "api_key", "type": "secret", "description": "API key"},
            ],
            status=PublishStatus.PUBLISHED,
            installs=47,
        )

    def create_integration(
        self,
        name: str,
        version: str,
        description: str,
        author: str,
        category: IntegrationCategory,
        capabilities: IntegrationCapabilities,
    ) -> IntegrationManifest:
        """Create a new integration definition."""
        integration_id = name.lower().replace(" ", "-")

        manifest = IntegrationManifest(
            integration_id=integration_id,
            name=name,
            version=version,
            description=description,
            author=author,
            category=category,
            capabilities=capabilities,
        )

        self._integrations[integration_id] = manifest

        log.info(
            "Created integration: id=%s author=%s",
            integration_id,
            author,
        )

        return manifest

    def register_handlers(
        self,
        integration_id: str,
        handlers: IntegrationHandler,
    ) -> bool:
        """Register handler code for an integration."""
        if integration_id not in self._integrations:
            return False

        self._handlers[integration_id] = handlers

        log.info("Registered handlers: integration=%s", integration_id)
        return True

    def update_manifest(
        self,
        integration_id: str,
        updates: dict[str, Any],
    ) -> IntegrationManifest | None:
        """Update integration manifest."""
        manifest = self._integrations.get(integration_id)
        if not manifest:
            return None

        for key, value in updates.items():
            if hasattr(manifest, key):
                setattr(manifest, key, value)

        return manifest

    def submit_for_review(self, integration_id: str) -> dict[str, Any]:
        """Submit integration for review."""
        manifest = self._integrations.get(integration_id)
        if not manifest:
            return {"error": "Integration not found"}

        if manifest.status != PublishStatus.DRAFT:
            return {"error": "Can only submit draft integrations"}

        errors = self._validate_integration(integration_id)
        if errors:
            return {"error": "Validation failed", "errors": errors}

        manifest.status = PublishStatus.PENDING_REVIEW

        log.info("Submitted for review: integration=%s", integration_id)

        return {
            "success": True,
            "status": manifest.status.value,
            "estimated_review_days": 3,
        }

    def _validate_integration(self, integration_id: str) -> list[str]:
        """Validate integration before submission."""
        errors: list[str] = []
        manifest = self._integrations.get(integration_id)

        if not manifest:
            errors.append("Integration not found")
            return errors

        if not manifest.name:
            errors.append("Name is required")

        if not manifest.description or len(manifest.description) < 20:
            errors.append("Description must be at least 20 characters")

        if not manifest.author:
            errors.append("Author email is required")

        handlers = self._handlers.get(integration_id)
        if not handlers:
            errors.append("No handlers registered")
        elif not handlers.test_connection:
            errors.append("test_connection handler is required")

        caps = manifest.capabilities
        if caps.logs and handlers and not handlers.fetch_logs:
            errors.append("fetch_logs handler required when logs capability enabled")

        return errors

    def approve_integration(self, integration_id: str) -> dict[str, Any]:
        """Approve and publish an integration (admin only)."""
        manifest = self._integrations.get(integration_id)
        if not manifest:
            return {"error": "Integration not found"}

        manifest.status = PublishStatus.PUBLISHED
        manifest.published_at = datetime.now(timezone.utc)

        log.info("Approved integration: id=%s", integration_id)

        return {
            "success": True,
            "status": manifest.status.value,
            "published_at": manifest.published_at.isoformat(),
        }

    def install_integration(
        self,
        org_id: str,
        integration_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Install an integration for an org."""
        manifest = self._integrations.get(integration_id)
        if not manifest:
            return {"error": "Integration not found"}

        if manifest.status != PublishStatus.PUBLISHED:
            return {"error": "Integration not published"}

        missing = []
        for req in manifest.required_config:
            if req["name"] not in config:
                missing.append(req["name"])

        if missing:
            return {"error": "Missing required config", "fields": missing}

        if org_id not in self._installations:
            self._installations[org_id] = []

        if integration_id not in self._installations[org_id]:
            self._installations[org_id].append(integration_id)
            manifest.installs += 1

        log.info(
            "Installed integration: org=%s integration=%s",
            org_id,
            integration_id,
        )

        return {
            "success": True,
            "integration": manifest.to_dict(),
        }

    def uninstall_integration(
        self,
        org_id: str,
        integration_id: str,
    ) -> dict[str, Any]:
        """Uninstall an integration."""
        if org_id in self._installations:
            if integration_id in self._installations[org_id]:
                self._installations[org_id].remove(integration_id)
                return {"success": True}

        return {"error": "Integration not installed"}

    def get_marketplace(
        self,
        category: IntegrationCategory | None = None,
        search: str = "",
    ) -> list[dict[str, Any]]:
        """Get marketplace of published integrations."""
        results = []
        for manifest in self._integrations.values():
            if manifest.status != PublishStatus.PUBLISHED:
                continue

            if category and manifest.category != category:
                continue

            if search and search.lower() not in manifest.name.lower():
                continue

            results.append(manifest.to_dict())

        results.sort(key=lambda x: x["installs"], reverse=True)
        return results

    def get_installed(self, org_id: str) -> list[dict[str, Any]]:
        """Get installed integrations for an org."""
        installed_ids = self._installations.get(org_id, [])
        results = []
        for iid in installed_ids:
            manifest = self._integrations.get(iid)
            if manifest:
                results.append(manifest.to_dict())
        return results

    def get_sdk_template(self) -> str:
        """Get SDK code template for building integrations."""
        return '''
// DevOps Copilot Integration SDK
// TypeScript Example

import { IntegrationSDK, Integration, Query, Action } from "@devopscopilot/sdk";

class MyToolIntegration implements Integration {
  name = 'my-tool';
  version = '1.0.0';

  capabilities = {
    logs: true,
    metrics: true,
    events: true,
    writeBack: true,
  };

  private client: MyToolClient;

  constructor(config: { apiUrl: string; apiKey: string }) {
    this.client = new MyToolClient(config);
  }

  async testConnection(): Promise<boolean> {
    return await this.client.ping();
  }

  async fetchLogs(query: Query): Promise<Log[]> {
    const response = await this.client.get('/logs', {
      timeRange: query.timeRange,
      filter: query.filter,
    });
    return this.transformLogs(response);
  }

  async fetchMetrics(query: Query): Promise<Metric[]> {
    const response = await this.client.get('/metrics', {
      timeRange: query.timeRange,
      names: query.metricNames,
    });
    return this.transformMetrics(response);
  }

  async writeAction(action: Action): Promise<void> {
    if (action.type === 'createTicket') {
      await this.client.post('/tickets', {
        title: action.payload.title,
        description: action.payload.description,
        priority: action.payload.priority,
      });
    }
  }

  async handleWebhook(payload: any): Promise<void> {
    const event = this.parseWebhook(payload);
    await this.emitEvent(event);
  }

  private transformLogs(response: any): Log[] {
    return response.data.map((item: any) => ({
      timestamp: new Date(item.ts),
      level: item.level,
      message: item.msg,
      metadata: item.meta,
    }));
  }
}

// Register with DevOps Copilot
IntegrationSDK.register(MyToolIntegration);
'''

    def get_documentation(self) -> dict[str, Any]:
        """Get SDK documentation."""
        return {
            "overview": "Build custom integrations for DevOps Copilot",
            "getting_started": [
                "Install the SDK: npm install @devopscopilot/sdk",
                "Create an integration class implementing the Integration interface",
                "Register handlers for your capabilities",
                "Test locally with the SDK simulator",
                "Submit for review via the partner portal",
            ],
            "capabilities": {
                "logs": "Provide log data for correlation and analysis",
                "metrics": "Provide metric data for anomaly detection",
                "events": "Provide events (deploys, alerts, etc.)",
                "alerts": "Provide alerting integration",
                "traces": "Provide distributed tracing data",
                "write_back": "Support write operations (create tickets, etc.)",
                "webhooks": "Receive real-time updates via webhooks",
                "realtime": "Support real-time streaming",
            },
            "required_handlers": {
                "testConnection": "Verify connection to the service",
            },
            "optional_handlers": {
                "fetchLogs": "Fetch logs (required if logs capability)",
                "fetchMetrics": "Fetch metrics (required if metrics capability)",
                "fetchEvents": "Fetch events (required if events capability)",
                "writeAction": "Execute write actions (required if write_back)",
                "handleWebhook": "Process incoming webhooks",
            },
            "partner_program": {
                "benefits": [
                    "Priority listing in marketplace",
                    "Co-marketing opportunities",
                    "Revenue sharing for premium integrations",
                    "Dedicated partner support",
                ],
                "requirements": [
                    "Published at least one integration",
                    "Maintained 99% uptime",
                    "Responded to support within 48 hours",
                ],
            },
        }


_sdk: IntegrationSDK | None = None


def get_integration_sdk() -> IntegrationSDK:
    """Get the default integration SDK."""
    global _sdk
    if _sdk is None:
        _sdk = IntegrationSDK()
    return _sdk
