"""OAuth2/SSO Authentication with Google and GitHub.

Provides:
- Google OAuth2 login
- GitHub OAuth2 login
- Session management with JWT tokens
- Account linking (multiple providers)

Linked accounts and pending login states live in Postgres (migration
008_oauth), not in this module. They used to be the dicts `_OAUTH_ACCOUNTS`,
`_USER_ACCOUNTS` and `_OAUTH_STATES`, which made identity per-process: the
callback in app/auth/endpoints.py mints a fresh `user_id` whenever the account
lookup misses, and after a restart every lookup missed, so returning users came
back as new people and left their memberships and history behind. The state
dict additionally broke login outright on any deployment with more than one
replica, since the redirect and the callback need not reach the same pod.

Session tokens are still stateless JWTs -- there is nothing to store for those,
and `create_session_token`/`verify_session_token` are unchanged.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import TYPE_CHECKING, Any, AsyncGenerator, cast
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.engine import ensure_schema, get_session
from app.database.models import OAuthAccountRecord, OAuthStateRecord

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

log = logging.getLogger("omnipotent.auth.oauth")


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


#: How long an issued state stays usable. Unchanged from the dict version; it
#: bounds how long a captured login redirect is worth replaying.
STATE_TTL = timedelta(minutes=15)


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    await ensure_schema()
    async with get_session() as session:
        yield session


def _utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a value read back from SQLite, which drops the offset."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def generate_oauth_state(provider: str) -> str:
    """Generate a secure state parameter for CSRF protection.

    Stored in Postgres rather than in this process: the callback that presents
    this state back is a separate request arriving from the provider's redirect,
    which load-balances independently of the request being served now.
    """
    state = secrets.token_urlsafe(32)

    async with _session() as session:
        session.add(OAuthStateRecord(state=state, provider=provider))
        # Opportunistic sweep, as the dict version did on every issue. Nothing
        # else deletes from this table, so without it expired rows accumulate
        # for as long as the process runs.
        await session.execute(
            delete(OAuthStateRecord).where(
                OAuthStateRecord.created_at < datetime.now(timezone.utc) - STATE_TTL
            )
        )
        await session.commit()

    return state


async def verify_oauth_state(state: str) -> str | None:
    """Verify and consume OAuth state. Returns provider if valid.

    The row is deleted as part of the check rather than after it: two replays of
    the same captured callback can both pass a SELECT, but only one can be the
    DELETE that reports a row.
    """
    async with _session() as session:
        row = (
            await session.execute(
                select(OAuthStateRecord).where(OAuthStateRecord.state == state)
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        provider = row.provider
        created_at = _utc(row.created_at) or datetime.now(timezone.utc)

        deleted = await session.execute(
            delete(OAuthStateRecord).where(OAuthStateRecord.state == state)
        )
        await session.commit()

        # Somebody else consumed it between the read and the delete.
        if cast("CursorResult[Any]", deleted).rowcount == 0:
            return None

    if datetime.now(timezone.utc) - created_at > STATE_TTL:
        return None

    return provider


async def get_authorization_url(provider: str, redirect_uri: str) -> str | None:
    """Get OAuth authorization URL for a provider."""
    config = get_oauth_config(provider)
    if not config:
        return None

    state = await generate_oauth_state(provider)

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
                timeout=get_settings().http_timeout_default,
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
                timeout=get_settings().http_timeout_default,
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
                        timeout=get_settings().http_timeout_default,
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


@lru_cache(maxsize=1)
def _ephemeral_session_secret() -> str:
    """Random per-process HMAC key used when COPILOT_SECRET_KEY is unset.

    Cached so the whole process signs and verifies with the same value, and so
    the warning below is emitted exactly once.
    """
    log.warning(
        "COPILOT_SECRET_KEY not set — signing session tokens with an ephemeral "
        "per-process key; sessions will NOT survive a restart and will not "
        "validate across replicas. Set one in production."
    )
    return secrets.token_urlsafe(32)


def _session_secret() -> str:
    """Resolve the HMAC key for session JWTs — never a fixed literal.

    A shared default here would let anyone who has read the source mint a valid
    session for any org, so an unset key falls back to random bytes rather than
    a guessable constant. app/config.py already refuses to boot production
    without COPILOT_SECRET_KEY, so the fallback only ever applies to local runs.
    """
    return get_settings().copilot_secret_key.strip() or _ephemeral_session_secret()


def create_session_token(
    user_id: str,
    org_id: str,
    email: str,
    role: str = "viewer",
    expires_hours: int = 24,
) -> str:
    """Create a JWT session token."""
    secret = _session_secret()

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
    secret = _session_secret()

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


def _to_account(row: OAuthAccountRecord) -> OAuthAccount:
    """Build the dataclass callers already expect from a row.

    The result is a detached copy: assigning to it does not write anything back,
    which is why refreshing `last_login` is `touch_last_login()` below rather
    than an attribute assignment at the call site.
    """
    now = datetime.now(timezone.utc)
    return OAuthAccount(
        id=row.id,
        user_id=row.user_id,
        provider=row.provider,
        provider_id=row.provider_id,
        email=row.email,
        name=row.name,
        avatar_url=row.avatar_url,
        created_at=_utc(row.created_at) or now,
        last_login=_utc(row.last_login) or now,
    )


async def link_oauth_account(
    user_id: str,
    oauth_user: OAuthUser,
) -> OAuthAccount:
    """Link an OAuth account to a user.

    Idempotent on `{provider}:{provider_id}`: re-linking an identity that is
    already claimed keeps the user id it is already attached to instead of
    repointing it at `user_id`. Two sign-ins racing on a first-ever login would
    otherwise each mint an id and the loser's would win the write, handing one
    person's account to whichever request committed last.
    """
    account_id = f"{oauth_user.provider}:{oauth_user.provider_id}"
    now = datetime.now(timezone.utc)

    async with _session() as session:
        row = OAuthAccountRecord(
            id=account_id,
            user_id=user_id,
            provider=oauth_user.provider,
            provider_id=oauth_user.provider_id,
            # Lower-cased on the way in so the email lookup can use its index
            # rather than scanning and folding case in Python.
            email=oauth_user.email.lower(),
            name=oauth_user.name,
            # Passed explicitly even though it is nullable: a column left out of
            # the INSERT is not loaded on the instance, and reading it back to
            # build the response would emit a lazy SELECT that raises here.
            avatar_url=oauth_user.avatar_url,
            created_at=now,
            last_login=now,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            return _to_account(row)

        existing = (
            await session.execute(
                select(OAuthAccountRecord).where(OAuthAccountRecord.id == account_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            # The conflict was not this row after all; nothing sensible to
            # return, so let the caller see the original failure.
            raise

        existing.email = oauth_user.email.lower()
        existing.name = oauth_user.name
        existing.avatar_url = oauth_user.avatar_url
        existing.last_login = now
        await session.commit()
        return _to_account(existing)


async def touch_last_login(account_id: str) -> None:
    """Record that a linked account was just used to sign in.

    Its own function because the dict version updated this by mutating the
    object `find_account_by_provider` handed back. That object is now a detached
    copy, so the same assignment would be silently discarded.
    """
    async with _session() as session:
        await session.execute(
            update(OAuthAccountRecord)
            .where(OAuthAccountRecord.id == account_id)
            .values(last_login=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def find_account_by_provider(provider: str, provider_id: str) -> OAuthAccount | None:
    """Find an OAuth account by provider and provider ID."""
    account_id = f"{provider}:{provider_id}"
    async with _session() as session:
        row = (
            await session.execute(
                select(OAuthAccountRecord).where(OAuthAccountRecord.id == account_id)
            )
        ).scalar_one_or_none()
    return _to_account(row) if row else None


async def find_account_by_email(email: str) -> OAuthAccount | None:
    """Find an OAuth account by email.

    Ordered by creation: the dict version returned whichever match came first in
    insertion order, and an unordered query would let the answer -- and so which
    user id a new provider gets folded into -- vary between identical calls.
    """
    async with _session() as session:
        row = (
            await session.execute(
                select(OAuthAccountRecord)
                .where(OAuthAccountRecord.email == email.lower())
                .order_by(OAuthAccountRecord.created_at, OAuthAccountRecord.id)
                .limit(1)
            )
        ).scalar_one_or_none()
    return _to_account(row) if row else None


async def get_user_accounts(user_id: str) -> list[OAuthAccount]:
    """Get all OAuth accounts linked to a user.

    Derived from the accounts themselves. The `_USER_ACCOUNTS` index this
    replaces was maintained by hand on every link and could disagree with the
    records it pointed at.
    """
    async with _session() as session:
        rows = (
            (
                await session.execute(
                    select(OAuthAccountRecord)
                    .where(OAuthAccountRecord.user_id == user_id)
                    .order_by(OAuthAccountRecord.created_at, OAuthAccountRecord.id)
                )
            )
            .scalars()
            .all()
        )
    return [_to_account(row) for row in rows]


async def clear() -> None:
    """Drop every linked account and pending state. For tests."""
    async with _session() as session:
        await session.execute(delete(OAuthAccountRecord))
        await session.execute(delete(OAuthStateRecord))
        await session.commit()
