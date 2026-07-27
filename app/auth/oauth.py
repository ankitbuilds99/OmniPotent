"""OAuth2/SSO Authentication with Google and GitHub.

Provides:
- Google OAuth2 login
- GitHub OAuth2 login
- Session management with JWT tokens
- Account linking (multiple providers)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.config import get_settings

log = logging.getLogger("devcopilot.auth.oauth")


@dataclass
class OAuthConfig:
    """OAuth provider configuration."""

    provider: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: list[str]
    redirect_uri: str = ""


@dataclass
class OAuthUser:
    """Authenticated user from OAuth provider."""

    provider: str
    provider_id: str
    email: str
    name: str
    avatar_url: str | None = None
    access_token: str = ""
    refresh_token: str | None = None
    raw_data: dict[str, Any] | None = None


OAUTH_CONFIGS: dict[str, OAuthConfig] = {}


def _load_oauth_configs() -> dict[str, OAuthConfig]:
    """Load OAuth configurations from environment."""
    import os

    configs: dict[str, OAuthConfig] = {}

    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if google_client_id and google_client_secret:
        configs["google"] = OAuthConfig(
            provider="google",
            client_id=google_client_id,
            client_secret=google_client_secret,
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            userinfo_url="https://www.googleapis.com/oauth2/v2/userinfo",
            scopes=["openid", "email", "profile"],
        )

    github_client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
    github_client_secret = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "")
    if github_client_id and github_client_secret:
        configs["github"] = OAuthConfig(
            provider="github",
            client_id=github_client_id,
            client_secret=github_client_secret,
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            userinfo_url="https://api.github.com/user",
            scopes=["read:user", "user:email"],
        )

    return configs


def get_oauth_config(provider: str) -> OAuthConfig | None:
    """Get OAuth config for a provider."""
    global OAUTH_CONFIGS
    if not OAUTH_CONFIGS:
        OAUTH_CONFIGS = _load_oauth_configs()
    return OAUTH_CONFIGS.get(provider)


def get_available_providers() -> list[str]:
    """Get list of configured OAuth providers."""
    global OAUTH_CONFIGS
    if not OAUTH_CONFIGS:
        OAUTH_CONFIGS = _load_oauth_configs()
    return list(OAUTH_CONFIGS.keys())


_OAUTH_STATES: dict[str, tuple[str, datetime]] = {}


def generate_oauth_state(provider: str) -> str:
    """Generate a secure state parameter for CSRF protection."""
    state = secrets.token_urlsafe(32)
    _OAUTH_STATES[state] = (provider, datetime.now(timezone.utc))

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    expired = [k for k, (_, ts) in _OAUTH_STATES.items() if ts < cutoff]
    for k in expired:
        del _OAUTH_STATES[k]

    return state


def verify_oauth_state(state: str) -> str | None:
    """Verify and consume OAuth state. Returns provider if valid."""
    if state not in _OAUTH_STATES:
        return None

    provider, ts = _OAUTH_STATES.pop(state)
    if datetime.now(timezone.utc) - ts > timedelta(minutes=15):
        return None

    return provider


def get_authorization_url(provider: str, redirect_uri: str) -> str | None:
    """Get OAuth authorization URL for a provider."""
    config = get_oauth_config(provider)
    if not config:
        return None

    state = generate_oauth_state(provider)

    params = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scopes),
        "state": state,
    }

    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent"

    return f"{config.authorize_url}?{urlencode(params)}"


async def exchange_code_for_token(
    provider: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any] | None:
    """Exchange authorization code for access token."""
    config = get_oauth_config(provider)
    if not config:
        return None

    data = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    headers = {"Accept": "application/json"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                config.token_url,
                data=data,
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                log.error("Token exchange failed: %s", resp.text)
                return None
            return resp.json()
    except Exception as e:
        log.error("Token exchange error: %s", e)
        return None


async def get_user_info(provider: str, access_token: str) -> OAuthUser | None:
    """Get user info from OAuth provider."""
    config = get_oauth_config(provider)
    if not config:
        return None

    headers = {"Authorization": f"Bearer {access_token}"}

    if provider == "github":
        headers["Authorization"] = f"token {access_token}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                config.userinfo_url,
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                log.error("Userinfo request failed: %s", resp.text)
                return None

            data = resp.json()

            if provider == "google":
                return OAuthUser(
                    provider="google",
                    provider_id=str(data["id"]),
                    email=data["email"],
                    name=data.get("name", data["email"]),
                    avatar_url=data.get("picture"),
                    access_token=access_token,
                    raw_data=data,
                )
            elif provider == "github":
                email = data.get("email")
                if not email:
                    email_resp = await client.get(
                        "https://api.github.com/user/emails",
                        headers=headers,
                        timeout=30,
                    )
                    if email_resp.status_code == 200:
                        emails = email_resp.json()
                        primary = next(
                            (e for e in emails if e.get("primary")),
                            emails[0] if emails else None,
                        )
                        if primary:
                            email = primary["email"]

                return OAuthUser(
                    provider="github",
                    provider_id=str(data["id"]),
                    email=email or f"{data['login']}@github.local",
                    name=data.get("name") or data["login"],
                    avatar_url=data.get("avatar_url"),
                    access_token=access_token,
                    raw_data=data,
                )

            return None
    except Exception as e:
        log.error("Userinfo error: %s", e)
        return None


def create_session_token(
    user_id: str,
    org_id: str,
    email: str,
    role: str = "viewer",
    expires_hours: int = 24,
) -> str:
    """Create a JWT session token."""
    s = get_settings()
    secret = s.copilot_secret_key or "dev-secret-key"

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
        "type": "session",
    }

    return jwt.encode(payload, secret, algorithm="HS256")


def verify_session_token(token: str) -> dict[str, Any] | None:
    """Verify and decode a session token."""
    s = get_settings()
    secret = s.copilot_secret_key or "dev-secret-key"

    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "session":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        log.debug("Session token expired")
        return None
    except jwt.InvalidTokenError as e:
        log.debug("Invalid session token: %s", e)
        return None


@dataclass
class OAuthAccount:
    """Linked OAuth account."""

    id: str
    user_id: str
    provider: str
    provider_id: str
    email: str
    name: str
    avatar_url: str | None
    created_at: datetime
    last_login: datetime


_OAUTH_ACCOUNTS: dict[str, OAuthAccount] = {}
_USER_ACCOUNTS: dict[str, list[str]] = {}


def link_oauth_account(
    user_id: str,
    oauth_user: OAuthUser,
) -> OAuthAccount:
    """Link an OAuth account to a user."""
    account_id = f"{oauth_user.provider}:{oauth_user.provider_id}"

    account = OAuthAccount(
        id=account_id,
        user_id=user_id,
        provider=oauth_user.provider,
        provider_id=oauth_user.provider_id,
        email=oauth_user.email,
        name=oauth_user.name,
        avatar_url=oauth_user.avatar_url,
        created_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
    )

    _OAUTH_ACCOUNTS[account_id] = account

    if user_id not in _USER_ACCOUNTS:
        _USER_ACCOUNTS[user_id] = []
    if account_id not in _USER_ACCOUNTS[user_id]:
        _USER_ACCOUNTS[user_id].append(account_id)

    return account


def find_account_by_provider(provider: str, provider_id: str) -> OAuthAccount | None:
    """Find an OAuth account by provider and provider ID."""
    account_id = f"{provider}:{provider_id}"
    return _OAUTH_ACCOUNTS.get(account_id)


def find_account_by_email(email: str) -> OAuthAccount | None:
    """Find an OAuth account by email."""
    for account in _OAUTH_ACCOUNTS.values():
        if account.email.lower() == email.lower():
            return account
    return None


def get_user_accounts(user_id: str) -> list[OAuthAccount]:
    """Get all OAuth accounts linked to a user."""
    account_ids = _USER_ACCOUNTS.get(user_id, [])
    return [_OAUTH_ACCOUNTS[aid] for aid in account_ids if aid in _OAUTH_ACCOUNTS]
