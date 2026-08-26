"""OAuth2/SSO Authentication with Google and GitHub.

Provides:
- Google OAuth2 login
- GitHub OAuth2 login
- Session management with JWT tokens
- Account linking (multiple providers)

Linked accounts, pending login states and revoked sessions live in the database,
not in this module. They used to be the dicts `_OAUTH_ACCOUNTS`,
`_USER_ACCOUNTS` and `_OAUTH_STATES`, which made identity per-process: the
callback in app/auth/endpoints.py mints a fresh `user_id` whenever the account
lookup misses, and after a restart every lookup missed, so returning users came
back as new people and left their memberships and history behind. The state
dict additionally broke login outright on any deployment with more than one
replica, since the redirect and the callback need not reach the same pod.

* `oauth_accounts`   -> `OAuthAccountDB` in app/database/auth_models.py
* `oauth_states`     -> `OAuthStateRecord` in app/database/models.py
* `revoked_sessions` -> `RevokedSessionDB` in app/database/auth_models.py

`oauth_accounts` is keyed by a surrogate uuid with a unique constraint on
(`provider`, `provider_id`); origin/main declared the same table as
`OAuthAccountRecord` keyed on the natural key `"{provider}:{provider_id}"`. Only
one declaration of a table can exist on the shared metadata, so the surrogate
one wins (it is the one migration 014_auth creates and the one
`revoked_sessions` sits beside). The natural key survives as `OAuthAccount.id`,
which is what `touch_last_login` and the endpoints pass around.

Session tokens remain JWTs, but they are no longer purely stateless: each one
carries a `jti` and `verify_session_token` refuses any `jti` that `logout` has
written to `revoked_sessions`. Without that, a token that had already been
copied out of a browser could not be withdrawn short of rotating
COPILOT_SECRET_KEY -- which is also the secrets-vault KEK.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, AsyncGenerator, cast
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.auth_models import OAuthAccountDB, RevokedSessionDB
from app.database.engine import ensure_schema, get_session
from app.database.models import OAuthStateRecord

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

#: Separates the random half of a state from the id of the user an account-link
#: flow was started by. `secrets.token_urlsafe` emits only [A-Za-z0-9_-], so a
#: "~" can never appear in the random half and the split is unambiguous.
_STATE_LINK_SEP = "~"


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


def _state_link_user(state: str) -> str:
    """The link target encoded in a state string, or "" for a login flow.

    Parsing alone proves nothing — anyone can append `~user_victim` to a string.
    Every caller checks the state against the stored row first; see
    `peek_oauth_link_user`.
    """
    _random, sep, link_user_id = state.partition(_STATE_LINK_SEP)
    return link_user_id if sep else ""


async def generate_oauth_state(provider: str, link_user_id: str = "") -> str:
    """Generate a secure state parameter for CSRF protection.

    Stored in the `oauth_states` table rather than in this process: the callback
    that presents this state back is a separate request arriving from the
    provider's redirect, which load-balances independently of the request being
    served now. Held in a dict, any deployment with more than one replica failed
    every login whose callback happened to land on a different pod.

    `link_user_id` is set when the flow was started by POST /auth/{provider}/link
    rather than by a login: it is the id of the already-authenticated user the
    new provider should attach to. It rides on the state rather than on the
    callback URL because the callback URL comes back from the browser and is
    attacker-suppliable, while a state has to match a row only this server can
    write. (`oauth_states.state` is 128 chars and the random half is 43, leaving
    84 for the id; ids minted here are `user_<16 hex>`.)
    """
    state = secrets.token_urlsafe(32)
    if link_user_id:
        state = f"{state}{_STATE_LINK_SEP}{link_user_id}"

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


async def peek_oauth_link_user(state: str) -> str:
    """The user id an account-link flow was started by, without consuming the state.

    Non-destructive on purpose: the callback still calls `verify_oauth_state` to
    check and consume the state, and that has to stay the one place a state is
    spent. Returns "" for a login flow, an unknown state or an expired one, so a
    forged `state=anything~user_victim` reads as a plain login and then fails
    verification like any other unknown state.
    """
    link_user_id = _state_link_user(state)
    if not link_user_id:
        return ""

    async with _session() as session:
        row = (
            await session.execute(
                select(OAuthStateRecord).where(OAuthStateRecord.state == state)
            )
        ).scalar_one_or_none()

    if row is None:
        return ""

    created_at = _utc(row.created_at) or datetime.now(timezone.utc)
    if datetime.now(timezone.utc) - created_at > STATE_TTL:
        return ""

    return link_user_id


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


async def get_authorization_url(
    provider: str, redirect_uri: str, link_user_id: str = ""
) -> str | None:
    """Get OAuth authorization URL for a provider."""
    config = get_oauth_config(provider)
    if not config:
        return None

    state = await generate_oauth_state(provider, link_user_id=link_user_id)

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

    Both branches agreed the old `or "dev-secret-key"` literal had to go; they
    disagreed on what replaces it. origin/main degraded to a random
    per-process key (`_ephemeral_session_secret`) so a local run without
    COPILOT_SECRET_KEY still issues tokens, leaning on app/config.py to refuse
    production without one. That is dropped here in favour of refusing outright:
    an ephemeral key silently invalidates every session on restart and across
    replicas, which reads as "randomly logged out" rather than as the
    misconfiguration it is. Callers that must not raise use
    `decode_session_token`, which turns the RuntimeError into a rejected token.
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
    try:
        async with _session() as session:
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
        async with _session() as session:
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


def _account_key(provider: str, provider_id: str) -> str:
    """The natural key `"{provider}:{provider_id}"` exposed as `OAuthAccount.id`.

    The table's own primary key is a surrogate uuid, but callers (and the
    endpoints' `touch_last_login(existing.id)`) address an account by the
    identity the provider asserts, which is what this spells.
    """
    return f"{provider}:{provider_id}"


def _split_account_key(account_id: str) -> tuple[str, str]:
    """Inverse of `_account_key`. Splits on the first ":" only."""
    provider, _sep, provider_id = account_id.partition(":")
    return provider, provider_id


def _to_account(row: OAuthAccountDB) -> OAuthAccount:
    """Read a row back as the dataclass the endpoints work in.

    The result is a detached copy: assigning to it does not write anything back,
    which is why refreshing `last_login` is `touch_last_login()` below rather
    than an attribute assignment at the call site.

    `created_at`/`last_login` are re-attached to UTC: SQLite, the default
    database, has no timezone type, so a `DateTime(timezone=True)` column round
    trips naive and `.isoformat()` would claim a local time it never stored.
    """
    now = datetime.now(timezone.utc)
    return OAuthAccount(
        # Not `row.id` — that is the surrogate uuid. Callers key on the identity
        # the provider asserted.
        id=_account_key(row.provider, row.provider_id),
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
    provider_id = str(oauth_user.provider_id)
    now = datetime.now(timezone.utc)

    async with _session() as session:
        row = OAuthAccountDB(
            user_id=user_id,
            provider=oauth_user.provider,
            provider_id=provider_id,
            # Lower-cased on the way in so the email lookup can use its index
            # rather than scanning and folding case in Python. `or ""` because
            # a provider may hand back no address at all and the column is NOT
            # NULL — GitHub does exactly this when the address is private.
            email=(oauth_user.email or "").strip().lower(),
            name=oauth_user.name or "",
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
        except IntegrityError as exc:
            await session.rollback()
            # Bound to a name that outlives the handler: `exc` is unbound at the
            # end of the `except` block, and a bare `raise` below would be
            # outside any handler and so raise "No active exception to re-raise"
            # instead of the failure we actually want to surface.
            conflict = exc
        else:
            return _to_account(row)

        existing = (
            await session.execute(
                select(OAuthAccountDB).where(
                    OAuthAccountDB.provider == oauth_user.provider,
                    OAuthAccountDB.provider_id == provider_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            # The conflict was not this row after all; nothing sensible to
            # return, so let the caller see the original failure.
            raise conflict

        # The profile fields are refreshed, `user_id` is not: an existing
        # provider identity keeps the local user it was first bound to.
        # Re-pointing it at whoever is logging in now would let a second login
        # silently take over another user's account.
        existing.email = (oauth_user.email or "").strip().lower()
        existing.name = oauth_user.name or ""
        existing.avatar_url = oauth_user.avatar_url
        existing.last_login = now
        await session.commit()
        return _to_account(existing)


async def touch_last_login(account_id: str) -> None:
    """Record that a linked account was just used to sign in.

    Its own function because the dict version updated this by mutating the
    object `find_account_by_provider` handed back. That object is now a detached
    copy, so the same assignment would be silently discarded.

    `account_id` is the natural key `"{provider}:{provider_id}"` — i.e. exactly
    what `OAuthAccount.id` carries.
    """
    provider, provider_id = _split_account_key(account_id)
    async with _session() as session:
        await session.execute(
            update(OAuthAccountDB)
            .where(
                OAuthAccountDB.provider == provider,
                OAuthAccountDB.provider_id == provider_id,
            )
            .values(last_login=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def find_account_by_provider(provider: str, provider_id: str) -> OAuthAccount | None:
    """Find an OAuth account by provider and provider ID."""
    async with _session() as session:
        row = (
            await session.execute(
                select(OAuthAccountDB).where(
                    OAuthAccountDB.provider == provider,
                    OAuthAccountDB.provider_id == str(provider_id),
                )
            )
        ).scalar_one_or_none()
    return _to_account(row) if row else None


async def find_account_by_email(email: str) -> OAuthAccount | None:
    """Find an OAuth account by email.

    Ordered by creation: the dict version returned whichever match came first in
    insertion order, and an unordered query would let the answer -- and so which
    user id a new provider gets folded into -- vary between identical calls.
    The (provider, provider_id) tie-break is there because the primary key is a
    random uuid, which would make same-instant rows order differently per run.
    """
    async with _session() as session:
        row = (
            await session.execute(
                select(OAuthAccountDB)
                .where(OAuthAccountDB.email == (email or "").strip().lower())
                .order_by(
                    OAuthAccountDB.created_at,
                    OAuthAccountDB.provider,
                    OAuthAccountDB.provider_id,
                )
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
                    select(OAuthAccountDB)
                    .where(OAuthAccountDB.user_id == user_id)
                    .order_by(
                        OAuthAccountDB.created_at,
                        OAuthAccountDB.provider,
                        OAuthAccountDB.provider_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    return [_to_account(row) for row in rows]


async def clear() -> None:
    """Drop every linked account, pending state and revoked session. For tests."""
    async with _session() as session:
        await session.execute(delete(OAuthAccountDB))
        await session.execute(delete(OAuthStateRecord))
        try:
            await session.execute(delete(RevokedSessionDB))
        except Exception as exc:  # noqa: BLE001 - a test helper, not a gate
            log.debug("Could not clear revoked sessions: %s", exc)
        await session.commit()
