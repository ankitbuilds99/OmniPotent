"""One-Click OAuth Setup.

Provides OAuth flows for seamless integration connections.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.integrations_advanced.oauth")


class OAuthProvider(str, Enum):
    DATADOG = "datadog"
    GITHUB = "github"
    PAGERDUTY = "pagerduty"
    SLACK = "slack"
    SPLUNK = "splunk"
    NEW_RELIC = "newrelic"
    JIRA = "jira"
    SENTRY = "sentry"
    OPSGENIE = "opsgenie"
    LINEAR = "linear"


@dataclass
class OAuthConfig:
    """OAuth provider configuration."""

    provider: OAuthProvider
    display_name: str
    authorize_url: str
    token_url: str
    scopes: list[str]
    permissions_description: list[str]
    icon_url: str = ""
    supports_refresh: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "name": self.display_name,
            "scopes": self.scopes,
            "permissions": self.permissions_description,
            "icon": self.icon_url,
        }


@dataclass
class OAuthFlow:
    """An active OAuth flow."""

    flow_id: str
    provider: OAuthProvider
    org_id: str
    user_id: str
    state: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    completed: bool = False

    def __post_init__(self) -> None:
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(minutes=10)

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > (self.expires_at or self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "provider": self.provider.value,
            "state": self.state,
            "redirect_uri": self.redirect_uri,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "completed": self.completed,
        }


@dataclass
class OAuthToken:
    """OAuth token for an integration."""

    token_id: str
    provider: OAuthProvider
    org_id: str
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_refresh_at: datetime | None = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def needs_refresh(self) -> bool:
        if self.expires_at is None:
            return False
        buffer = timedelta(minutes=5)
        return datetime.now(timezone.utc) > (self.expires_at - buffer)


class OAuthManager:
    """Manages OAuth flows and tokens."""

    def __init__(self) -> None:
        self._configs: dict[OAuthProvider, OAuthConfig] = {}
        self._flows: dict[str, OAuthFlow] = {}
        self._tokens: dict[str, OAuthToken] = {}
        self._init_default_providers()

    def _init_default_providers(self) -> None:
        """Initialize default OAuth provider configs."""
        self._configs[OAuthProvider.DATADOG] = OAuthConfig(
            provider=OAuthProvider.DATADOG,
            display_name="Datadog",
            authorize_url="https://app.datadoghq.com/oauth2/v1/authorize",
            token_url="https://api.datadoghq.com/oauth2/v1/token",
            scopes=["metrics_read", "logs_read", "events_read", "monitors_read"],
            permissions_description=[
                "Read: Metrics and dashboards",
                "Read: Logs and events",
                "Read: Monitors and alerts",
            ],
        )

        self._configs[OAuthProvider.GITHUB] = OAuthConfig(
            provider=OAuthProvider.GITHUB,
            display_name="GitHub",
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            scopes=["repo", "read:org", "read:user"],
            permissions_description=[
                "Read: Repositories and commits",
                "Read: Pull requests",
                "Read: Organization members",
            ],
        )

        self._configs[OAuthProvider.PAGERDUTY] = OAuthConfig(
            provider=OAuthProvider.PAGERDUTY,
            display_name="PagerDuty",
            authorize_url="https://app.pagerduty.com/oauth/authorize",
            token_url="https://app.pagerduty.com/oauth/token",
            scopes=["read", "write:incidents"],
            permissions_description=[
                "Read: Services and schedules",
                "Read: Incidents and alerts",
                "Write: Acknowledge/Resolve incidents",
            ],
        )

        self._configs[OAuthProvider.SLACK] = OAuthConfig(
            provider=OAuthProvider.SLACK,
            display_name="Slack",
            authorize_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",
            scopes=[
                "channels:read",
                "chat:write",
                "users:read",
                "reactions:write",
            ],
            permissions_description=[
                "Read: Channel list",
                "Write: Post messages to channels",
                "Read: User profiles",
                "Write: Add reactions",
            ],
        )

        self._configs[OAuthProvider.SPLUNK] = OAuthConfig(
            provider=OAuthProvider.SPLUNK,
            display_name="Splunk",
            authorize_url="https://{instance}/oauth/authorize",
            token_url="https://{instance}/oauth/token",
            scopes=["search", "dashboards"],
            permissions_description=[
                "Read: Search logs and events",
                "Read: Dashboards and saved searches",
                "Read: Alerts and reports",
            ],
        )

        self._configs[OAuthProvider.JIRA] = OAuthConfig(
            provider=OAuthProvider.JIRA,
            display_name="Jira",
            authorize_url="https://auth.atlassian.com/authorize",
            token_url="https://auth.atlassian.com/oauth/token",
            scopes=["read:jira-work", "write:jira-work", "read:jira-user"],
            permissions_description=[
                "Read: Issues and projects",
                "Write: Create and update issues",
                "Read: User assignments",
            ],
        )

        self._configs[OAuthProvider.SENTRY] = OAuthConfig(
            provider=OAuthProvider.SENTRY,
            display_name="Sentry",
            authorize_url="https://sentry.io/oauth/authorize/",
            token_url="https://sentry.io/oauth/token/",
            scopes=["event:read", "project:read", "team:read"],
            permissions_description=[
                "Read: Error events and issues",
                "Read: Projects and releases",
                "Read: Team information",
            ],
        )

    def get_providers(self) -> list[dict[str, Any]]:
        """Get all available OAuth providers."""
        return [c.to_dict() for c in self._configs.values()]

    def get_provider_config(self, provider: OAuthProvider) -> OAuthConfig | None:
        """Get config for a provider."""
        return self._configs.get(provider)

    def start_flow(
        self,
        provider: OAuthProvider,
        org_id: str,
        user_id: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Start an OAuth flow."""
        config = self._configs.get(provider)
        if not config:
            return {"error": f"Unknown provider: {provider}"}

        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = hashlib.sha256(code_verifier.encode()).hexdigest()

        flow = OAuthFlow(
            flow_id=f"flow_{secrets.token_hex(8)}",
            provider=provider,
            org_id=org_id,
            user_id=user_id,
            state=state,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )

        self._flows[flow.flow_id] = flow
        self._flows[state] = flow

        authorize_url = (
            f"{config.authorize_url}"
            f"?client_id=devops_copilot"
            f"&response_type=code"
            f"&redirect_uri={redirect_uri}"
            f"&scope={'+'.join(config.scopes)}"
            f"&state={state}"
            f"&code_challenge={code_challenge}"
            f"&code_challenge_method=S256"
        )

        log.info(
            "Started OAuth flow: provider=%s org=%s flow=%s",
            provider.value,
            org_id,
            flow.flow_id,
        )

        return {
            "flow_id": flow.flow_id,
            "authorize_url": authorize_url,
            "state": state,
            "expires_in_seconds": 600,
            "provider": config.to_dict(),
        }

    def complete_flow(
        self,
        state: str,
        code: str,
    ) -> dict[str, Any]:
        """Complete an OAuth flow with authorization code."""
        flow = self._flows.get(state)
        if not flow:
            return {"error": "Invalid or expired flow"}

        if flow.is_expired():
            return {"error": "Flow has expired"}

        if flow.completed:
            return {"error": "Flow already completed"}

        token = OAuthToken(
            token_id=f"token_{secrets.token_hex(8)}",
            provider=flow.provider,
            org_id=flow.org_id,
            access_token=f"mock_access_{secrets.token_hex(16)}",
            refresh_token=f"mock_refresh_{secrets.token_hex(16)}",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=self._configs[flow.provider].scopes,
        )

        key = f"{flow.org_id}:{flow.provider.value}"
        self._tokens[key] = token

        flow.completed = True

        log.info(
            "Completed OAuth flow: provider=%s org=%s",
            flow.provider.value,
            flow.org_id,
        )

        return {
            "success": True,
            "provider": flow.provider.value,
            "org_id": flow.org_id,
            "connected_at": token.created_at.isoformat(),
            "scopes": token.scopes,
        }

    def get_token(self, org_id: str, provider: OAuthProvider) -> OAuthToken | None:
        """Get token for an org and provider."""
        key = f"{org_id}:{provider.value}"
        return self._tokens.get(key)

    def is_connected(self, org_id: str, provider: OAuthProvider) -> bool:
        """Check if an integration is connected."""
        token = self.get_token(org_id, provider)
        if not token:
            return False
        return not token.is_expired()

    def refresh_token(self, org_id: str, provider: OAuthProvider) -> dict[str, Any]:
        """Refresh an OAuth token."""
        token = self.get_token(org_id, provider)
        if not token:
            return {"error": "No token found"}

        if not token.refresh_token:
            return {"error": "No refresh token available"}

        token.access_token = f"mock_access_{secrets.token_hex(16)}"
        token.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        token.last_refresh_at = datetime.now(timezone.utc)

        log.info("Refreshed token: provider=%s org=%s", provider.value, org_id)

        return {
            "success": True,
            "expires_at": token.expires_at.isoformat(),
        }

    def revoke_token(self, org_id: str, provider: OAuthProvider) -> dict[str, Any]:
        """Revoke an OAuth token."""
        key = f"{org_id}:{provider.value}"
        if key in self._tokens:
            del self._tokens[key]
            log.info("Revoked token: provider=%s org=%s", provider.value, org_id)
            return {"success": True}
        return {"error": "No token found"}

    def get_connected_integrations(self, org_id: str) -> list[dict[str, Any]]:
        """Get all connected integrations for an org."""
        connected = []
        for provider in OAuthProvider:
            token = self.get_token(org_id, provider)
            if token and not token.is_expired():
                config = self._configs.get(provider)
                connected.append({
                    "provider": provider.value,
                    "name": config.display_name if config else provider.value,
                    "connected_at": token.created_at.isoformat(),
                    "scopes": token.scopes,
                    "needs_refresh": token.needs_refresh(),
                })
        return connected


_manager: OAuthManager | None = None


def get_oauth_manager() -> OAuthManager:
    """Get the default OAuth manager."""
    global _manager
    if _manager is None:
        _manager = OAuthManager()
    return _manager
