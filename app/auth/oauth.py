"""OAuth2/SSO Authentication with Google and GitHub.

Provides:
- Google OAuth2 login
- GitHub OAuth2 login
- Session management with JWT tokens
- Account linking (multiple providers)
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.config import get_settings

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


# state -> (provider, issued_at, link_user_id). `link_user_id` is set when the
# flow was started by POST /auth/{provider}/link rather than by a login: it is
# the id of the already-authenticated user the new provider should attach to.
# Carrying it here rather than in the callback URL matters — the callback URL is
# attacker-suppliable, the state is not.
_OAUTH_STATES: dict[str, tuple[str, datetime, str]] = {}

STATE_TTL = timedelta(minutes=15)


def generate_oauth_state(provider: str, link_user_id: str = "") -> str:
    """Generate a secure state parameter for CSRF protection."""
    state = secrets.token_urlsafe(32)
    _OAUTH_STATES[state] = (provider, datetime.now(timezone.utc), link_user_id)

    cutoff = datetime.now(timezone.utc) - STATE_TTL
    expired = [k for k, (_, ts, _u) in _OAUTH_STATES.items() if ts < cutoff]
    for k in expired:
        del _OAUTH_STATES[k]

    return state


def peek_oauth_link_user(state: str) -> str:
    """The user id an account-link flow was started by, without consuming the state.

    Non-destructive on purpose: the callback still calls `verify_oauth_state` to
    check and consume the state, and that has to stay the one place a state is
    spent. Returns "" for a login flow, an unknown state or an expired one.
    """
    entry = _OAUTH_STATES.get(state)
    if not entry:
        return ""
    _provider, ts, link_user_id = entry
    if datetime.now(timezone.utc) - ts > STATE_TTL:
        return ""
    return link_user_id


def verify_oauth_state(state: str) -> str | None:
    """Verify and consume OAuth state. Returns provider if valid."""
    if state not in _OAUTH_STATES:
        return None

    provider, ts, _link_user_id = _OAUTH_STATES.pop(state)
    if datetime.now(timezone.utc) - ts > STATE_TTL:
        return None

    return provider


def get_authorization_url(
    provider: str, redirect_uri: str, link_user_id: str = ""
) -> str | None:
    """Get OAuth authorization URL for a provider."""
    config = get_oauth_config(provider)
    if not config:
        return None

    state = generate_oauth_state(provider, link_user_id=link_user_id)

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


def _session_signing_key() -> str:
    """Return the key used to sign/verify OAuth session JWTs.

    Fails closed: there is deliberately no development fallback. A hardcoded
    default here would let anyone forge an owner session, and the same value is
    the KEK that wraps every tenant's integration secrets (app/secrets_vault.py).
    """
    secret = get_settings().copilot_secret_key
    if not secret:
        raise RuntimeError(
            "COPILOT_SECRET_KEY must be set to issue or verify OAuth session tokens. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return secret


def create_session_token(
    user_id: str,
    org_id: str,
    email: str,
    role: str = "viewer",
    expires_hours: int = 24,
) -> str:
    """Create a JWT session token.

    The `jti` is what makes logout mean something: `revoke_session` writes it to
    the `revoked_sessions` table and `verify_session_token` refuses any token
    carrying a revoked one. Without it a token could only be withdrawn by
    rotating COPILOT_SECRET_KEY, which would also invalidate every tenant's
    encrypted integration secrets.
    """
    secret = _session_signing_key()

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
        "jti": secrets.token_urlsafe(16),
        "type": "session",
    }

    return jwt.encode(payload, secret, algorithm="HS256")


def decode_session_token(token: str) -> dict[str, Any] | None:
    """Signature/expiry/type check only — no revocation lookup.

    Callers that gate access want `verify_session_token`; this exists for the
    paths that only need the claims of a token they have already accepted.
    """
    try:
        secret = _session_signing_key()
    except RuntimeError:
        log.error("Cannot verify session token: COPILOT_SECRET_KEY is not set")
        return None

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


async def verify_session_token(token: str) -> dict[str, Any] | None:
    """Verify a session token: signature, expiry, type, and not revoked.

    Async because the revocation list is a table rather than process memory —
    logout on one pod has to be visible to every other pod, and to the next
    process after a restart.
    """
    payload = decode_session_token(token)
    if payload is None:
        return None
    jti = payload.get("jti")
    if jti and await is_session_revoked(str(jti)):
        log.debug("Session token %s was revoked", jti)
        return None
    return payload


async def is_session_revoked(jti: str) -> bool:
    """Whether this token id has been logged out.

    Fails *open* when the database is unreachable: the alternative is that a
    database blip logs every signed-in user out of an incident-response tool
    mid-incident. The token is still signature- and expiry-checked.
    """
    from sqlalchemy import select

    try:
        from app.database.auth_models import RevokedSessionDB
        from app.database.engine import get_session

        async with get_session() as session:
            found = await session.scalar(
                select(RevokedSessionDB.jti).where(RevokedSessionDB.jti == jti)
            )
        return found is not None
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("Revocation check unavailable (%s); accepting on signature alone", exc)
        return False


async def revoke_session(token: str) -> bool:
    """Record this token's `jti` as revoked. Returns True if anything was revoked.

    Also sweeps rows whose token would have expired anyway, so the table tracks
    live sessions rather than growing without bound.
    """
    from sqlalchemy import delete

    payload = decode_session_token(token)
    if not payload:
        return False
    jti = payload.get("jti")
    if not jti:
        # Tokens minted before `jti` existed cannot be revoked individually.
        return False

    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(float(exp), tz=timezone.utc)
        if exp
        else datetime.now(timezone.utc) + timedelta(hours=24)
    )

    try:
        from app.database.auth_models import RevokedSessionDB
        from app.database.engine import get_session

        async with get_session() as session:
            await session.merge(
                RevokedSessionDB(
                    jti=str(jti),
                    user_id=str(payload.get("sub") or ""),
                    expires_at=expires_at,
                )
            )
            await session.execute(
                delete(RevokedSessionDB).where(
                    RevokedSessionDB.expires_at < datetime.now(timezone.utc)
                )
            )
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Could not record session revocation: %s", exc)
        return False


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


def _to_account(row: Any) -> OAuthAccount:
    """Read a row back as the dataclass the endpoints work in.

    `created_at`/`last_login` are re-attached to UTC: SQLite, the default
    database, has no timezone type, so a `DateTime(timezone=True)` column round
    trips naive and `.isoformat()` would claim a local time it never stored.
    """

    def _utc(value: datetime | None) -> datetime:
        value = value or datetime.now(timezone.utc)
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    return OAuthAccount(
        id=f"{row.provider}:{row.provider_id}",
        user_id=row.user_id,
        provider=row.provider,
        provider_id=row.provider_id,
        email=row.email,
        name=row.name,
        avatar_url=row.avatar_url,
        created_at=_utc(row.created_at),
        last_login=_utc(row.last_login),
    )


async def link_oauth_account(user_id: str, oauth_user: OAuthUser) -> OAuthAccount:
    """Link an OAuth account to a user, or refresh the existing link."""
    from sqlalchemy import select

    from app.database.auth_models import OAuthAccountDB
    from app.database.engine import get_session

    now = datetime.now(timezone.utc)
    async with get_session() as session:
        row = await session.scalar(
            select(OAuthAccountDB).where(
                OAuthAccountDB.provider == oauth_user.provider,
                OAuthAccountDB.provider_id == str(oauth_user.provider_id),
            )
        )
        if row is None:
            row = OAuthAccountDB(
                provider=oauth_user.provider,
                provider_id=str(oauth_user.provider_id),
                user_id=user_id,
                email=(oauth_user.email or "").strip().lower(),
                name=oauth_user.name or "",
                avatar_url=oauth_user.avatar_url,
                created_at=now,
                last_login=now,
            )
            session.add(row)
        else:
            # An existing provider identity keeps the local user it was first
            # bound to. Re-pointing it at whoever is logging in now would let a
            # second login silently take over another user's account.
            row.email = (oauth_user.email or "").strip().lower()
            row.name = oauth_user.name or ""
            row.avatar_url = oauth_user.avatar_url
            row.last_login = now
        await session.commit()
        return _to_account(row)


async def find_account_by_provider(provider: str, provider_id: str) -> OAuthAccount | None:
    """Find an OAuth account by provider and provider ID."""
    from sqlalchemy import select

    from app.database.auth_models import OAuthAccountDB
    from app.database.engine import get_session

    async with get_session() as session:
        row = await session.scalar(
            select(OAuthAccountDB).where(
                OAuthAccountDB.provider == provider,
                OAuthAccountDB.provider_id == str(provider_id),
            )
        )
        return _to_account(row) if row else None


async def find_account_by_email(email: str) -> OAuthAccount | None:
    """Find an OAuth account by email (case-insensitive)."""
    from sqlalchemy import select

    from app.database.auth_models import OAuthAccountDB
    from app.database.engine import get_session

    async with get_session() as session:
        row = await session.scalar(
            select(OAuthAccountDB)
            .where(OAuthAccountDB.email == (email or "").strip().lower())
            .order_by(OAuthAccountDB.created_at)
        )
        return _to_account(row) if row else None


async def get_user_accounts(user_id: str) -> list[OAuthAccount]:
    """Get all OAuth accounts linked to a user."""
    from sqlalchemy import select

    from app.database.auth_models import OAuthAccountDB
    from app.database.engine import get_session

    async with get_session() as session:
        rows = (
            await session.scalars(
                select(OAuthAccountDB)
                .where(OAuthAccountDB.user_id == user_id)
                .order_by(OAuthAccountDB.created_at)
            )
        ).all()
        return [_to_account(r) for r in rows]


async def touch_account_login(provider: str, provider_id: str) -> None:
    """Stamp `last_login` on an existing link."""
    from sqlalchemy import update

    from app.database.auth_models import OAuthAccountDB
    from app.database.engine import get_session

    async with get_session() as session:
        await session.execute(
            update(OAuthAccountDB)
            .where(
                OAuthAccountDB.provider == provider,
                OAuthAccountDB.provider_id == str(provider_id),
            )
            .values(last_login=datetime.now(timezone.utc))
        )
        await session.commit()
