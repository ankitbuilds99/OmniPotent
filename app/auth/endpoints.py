"""OAuth2 authentication API endpoints.

Provides:
- GET /auth/providers - List available OAuth providers
- GET /auth/{provider}/login - Initiate OAuth flow
- GET /auth/{provider}/callback - Handle OAuth callback
- POST /auth/logout - End session
- GET /auth/me - Get current user info
"""

from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.oauth import (
    get_available_providers,
    get_authorization_url,
    exchange_code_for_token,
    get_user_info,
    peek_oauth_link_user,
    revoke_session,
    verify_oauth_state,
    create_session_token,
    verify_session_token,
    link_oauth_account,
    find_account_by_provider,
    find_account_by_email,
    get_user_accounts,
    touch_account_login,
)
from app.config import get_settings

log = logging.getLogger("omnipotent.auth.endpoints")

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _own_origin(request: Request | None) -> str:
    """The origin this request arrived on, e.g. "https://copilot.example.com"."""
    if request is None:
        return ""
    parsed = urlsplit(str(request.base_url))
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _validate_redirect_uri(redirect_uri: str, request: Request | None = None) -> str:
    """Reject redirect targets that are not an allowed origin.

    The callback appends the session token to this URI, so an unvalidated value
    hands a valid session to any attacker-controlled domain. Relative paths are
    allowed; absolute URLs must match a configured origin exactly.

    The app's own origin counts as allowed. `allowed_origins` is derived from
    CORS_ORIGINS/FRONTEND_URL, which default to localhost:3000 and which neither
    render.yaml nor the k8s manifests set — and the shipped Dockerfile builds the
    SPA with VITE_API_URL="" and serves it from the API image, so the browser
    origin *is* the API origin. Without this case every same-origin deployment
    answered "Sign in with GitHub" with 400 "redirect_uri is not an allowed
    origin"; only a separate Vite dev server on :5173 ever worked.
    """
    if not redirect_uri:
        return ""

    # Relative paths stay on our own origin. Reject protocol-relative ("//evil.com")
    # and backslash variants, which browsers normalise to absolute URLs.
    if redirect_uri.startswith("/"):
        if redirect_uri.startswith(("//", "/\\")):
            raise HTTPException(400, "Invalid redirect_uri")
        return redirect_uri

    parsed = urlsplit(redirect_uri)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, "Invalid redirect_uri")

    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = {o.rstrip("/") for o in get_settings().allowed_origins}
    own = _own_origin(request)
    if own:
        allowed.add(own.rstrip("/"))
    if origin not in allowed:
        log.warning("Rejected redirect_uri to non-allowed origin: %s", origin)
        raise HTTPException(400, "redirect_uri is not an allowed origin")

    return redirect_uri


class AuthProviderInfo(BaseModel):
    """OAuth provider information."""

    id: str
    name: str
    icon: str


class UserInfo(BaseModel):
    """Current user information."""

    id: str
    email: str
    name: str
    avatar_url: str | None
    org_id: str | None
    role: str
    providers: list[str]


class LoginResponse(BaseModel):
    """Login response with redirect URL."""

    redirect_url: str


class TokenResponse(BaseModel):
    """Token response after successful authentication."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


PROVIDER_INFO = {
    "google": AuthProviderInfo(
        id="google",
        name="Google",
        icon="https://www.google.com/favicon.ico",
    ),
    "github": AuthProviderInfo(
        id="github",
        name="GitHub",
        icon="https://github.com/favicon.ico",
    ),
}


@router.get("/providers", response_model=list[AuthProviderInfo])
async def list_providers() -> list[AuthProviderInfo]:
    """List available OAuth providers.

    Returns the OAuth providers that are configured and available for login.
    """
    available = get_available_providers()
    return [PROVIDER_INFO[p] for p in available if p in PROVIDER_INFO]


# response_model=None because the return type is a union of a Pydantic model
# and a Response: FastAPI would otherwise try to validate the RedirectResponse
# against LoginResponse.
@router.get("/{provider}/login", response_model=None)
async def oauth_login(
    provider: str,
    request: Request,
    redirect_uri: str = "",
) -> LoginResponse | RedirectResponse:
    """Initiate OAuth login flow.

    Args:
        provider: OAuth provider (google, github)
        redirect_uri: Where to redirect after auth (for SPAs)

    Returns:
        Redirect URL to OAuth provider
    """
    available = get_available_providers()
    if provider not in available:
        raise HTTPException(400, f"Provider '{provider}' is not configured")

    redirect_uri = _validate_redirect_uri(redirect_uri, request)

    base_url = str(request.base_url).rstrip("/")
    callback_url = f"{base_url}/auth/{provider}/callback"

    if redirect_uri:
        callback_url += f"?redirect_uri={quote(redirect_uri, safe='')}"

    auth_url = get_authorization_url(provider, callback_url)
    if not auth_url:
        raise HTTPException(500, "Failed to generate authorization URL")

    # This endpoint is navigated to directly by the browser (window.location.href),
    # so it must redirect. Returning a JSON body renders raw JSON at the user and
    # the OAuth flow never starts. Clients that want the URL can send Accept: json.
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return LoginResponse(redirect_url=auth_url)
    return RedirectResponse(auth_url, status_code=302)


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    redirect_uri: str = "",
) -> Response:
    """Handle OAuth callback from provider.

    This endpoint receives the authorization code from the OAuth provider
    and exchanges it for an access token. On success, creates a session
    and redirects to the app.
    """
    redirect_uri = _validate_redirect_uri(redirect_uri, request)

    if error:
        log.warning("OAuth error from %s: %s", provider, error)
        if redirect_uri:
            return RedirectResponse(f"{redirect_uri}?error={quote(error, safe='')}")
        raise HTTPException(400, f"OAuth error: {error}")

    if not code:
        raise HTTPException(400, "Missing authorization code")

    # Read the link intent before the state is consumed. It lives in the state
    # rather than in the callback URL because the callback URL comes back from
    # the browser: `?link=true` on it (the previous design) is attacker-settable
    # and, being merely inert, meant "Link another provider" quietly created a
    # second unrelated account and swapped the session over to it.
    link_user_id = peek_oauth_link_user(state)

    verified_provider = verify_oauth_state(state)
    if not verified_provider:
        raise HTTPException(400, "Invalid or expired state parameter")

    if verified_provider != provider:
        raise HTTPException(400, "State mismatch")

    base_url = str(request.base_url).rstrip("/")
    callback_url = f"{base_url}/auth/{provider}/callback"
    if redirect_uri:
        callback_url += f"?redirect_uri={quote(redirect_uri, safe='')}"

    token_data = await exchange_code_for_token(provider, code, callback_url)
    if not token_data:
        raise HTTPException(500, "Failed to exchange code for token")

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(500, "No access token in response")

    oauth_user = await get_user_info(provider, access_token)
    if not oauth_user:
        raise HTTPException(500, "Failed to get user info")

    if link_user_id:
        # Attaching a second provider to an account that is already signed in:
        # the local user is the one that started the flow, never a new one.
        existing = await find_account_by_provider(provider, oauth_user.provider_id)
        if existing and existing.user_id != link_user_id:
            raise HTTPException(
                409, "That account is already linked to a different user."
            )
        user_id = link_user_id
        await link_oauth_account(user_id, oauth_user)
    else:
        existing = await find_account_by_provider(provider, oauth_user.provider_id)
        if existing:
            user_id = existing.user_id
            await touch_account_login(provider, oauth_user.provider_id)
        else:
            email_account = await find_account_by_email(oauth_user.email)
            if email_account:
                user_id = email_account.user_id
            else:
                user_id = f"user_{secrets.token_hex(8)}"

            await link_oauth_account(user_id, oauth_user)

    org_id, role = await _get_or_create_org_for_user(user_id, oauth_user.email)

    session_token = create_session_token(
        user_id=user_id,
        org_id=org_id,
        email=oauth_user.email,
        role=role,
        expires_hours=24,
    )

    # The token rides in the URL fragment, not the query string. Fragments are
    # never sent to a server, so the session stays out of access logs, proxy
    # logs and the Referer header on the next navigation. The SPA reads it from
    # location.hash and clears it.
    target = redirect_uri or "/"
    separator = "&" if "#" in target else "#"
    response = RedirectResponse(f"{target}{separator}token={session_token}")

    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=get_settings().is_production,
        samesite="lax",
        max_age=86400,
    )

    log.info("User %s logged in via %s", oauth_user.email, provider)
    return response


_NOT_PROVISIONED = (
    "This account is not provisioned for this deployment. "
    "Ask an administrator to add it."
)


def _refuse_unprovisioned(email: str, reason: str) -> None:
    log.warning("Refused OAuth login for %s: %s", email, reason)
    raise HTTPException(403, _NOT_PROVISIONED)


async def _get_or_create_org_for_user(user_id: str, email: str) -> tuple[str, str]:
    """Resolve the (org_id, role) this login should act as, or refuse the login.

    Completing OAuth proves the person controls an account *at GitHub or Google*.
    It says nothing about whether they should have access here, so this is the
    point where that is decided:

    * Single-tenant. There is no membership table to consult and `require_auth`
      accepts the resulting token on its signature alone, so an unrestricted
      login is a full bypass of COPILOT_API_TOKEN — any GitHub account on the
      internet would read /audit and spend the operator's LLM budget. A login is
      allowed only when it matches COPILOT_OAUTH_ALLOWED_EMAILS/_DOMAINS, or
      when there is no API token to bypass in the first place (the local-dev
      default, where every route is already open).
    * Multi-tenant. An existing member keeps their stored role — an invited
      viewer must not be promoted to owner by logging in. Provisioning a *new*
      org is self-serve signup by another name, so it honours the same
      COPILOT_SIGNUP_ENABLED switch that POST /signup does, and the allowlist
      overrides it in both directions.
    """
    s = get_settings()
    # Lowercased: providers do not normalise the domain, and `Alice@ACME.com`
    # deriving `org_ACME_com` while `alice@acme.com` derives `org_acme_com` puts
    # the same person in two different orgs depending on how they typed it.
    domain = email.lower().split("@")[1] if "@" in email else "personal"
    allowlisted = s.oauth_allowlist_configured and s.oauth_email_allowed(email)

    if not s.copilot_multi_tenant:
        if s.oauth_allowlist_configured:
            if not allowlisted:
                _refuse_unprovisioned(email, "not on the OAuth allowlist")
        elif s.copilot_api_token.strip():
            _refuse_unprovisioned(
                email,
                "single-tenant deployment has COPILOT_API_TOKEN set but no OAuth "
                "allowlist; set COPILOT_OAUTH_ALLOWED_EMAILS or "
                "COPILOT_OAUTH_ALLOWED_DOMAINS to say who may sign in",
            )
        # Single-tenant: there is no org table to join, and the token is accepted
        # on its signature alone. The id is informational only.
        return f"org_{domain.replace('.', '_')}", "owner"

    from app.tenancy.auth import get_store

    store = get_store()
    membership = await store.get_membership_by_email(email)
    if membership:
        return membership

    if s.oauth_allowlist_configured:
        if not allowlisted:
            _refuse_unprovisioned(email, "not on the OAuth allowlist")
    elif not s.copilot_signup_enabled:
        _refuse_unprovisioned(email, "self-serve signup is disabled")

    org = await store.create_org(name=domain, owner_email=email)
    log.info("Provisioned org %s for first login of %s", org.id, email)
    return org.id, "owner"


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    """End the current session.

    Clears the session cookie *and* revokes the token. Deleting the cookie alone
    left the JWT valid for its full 24 hours — a token that had already been
    copied out of the browser (or out of a URL fragment in a log) could not be
    withdrawn at all short of rotating COPILOT_SECRET_KEY, which is also the
    secrets-vault KEK.
    """
    token = _extract_token(request)
    revoked = await revoke_session(token) if token else False
    response.delete_cookie("session")
    return {"status": "logged_out", "revoked": "true" if revoked else "false"}


@router.get("/me", response_model=UserInfo)
async def get_current_user(request: Request) -> UserInfo:
    """Get the current authenticated user.

    Returns user info from the session token (cookie or Authorization header).
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "Not authenticated")

    payload = await verify_session_token(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")

    user_id = payload["sub"]
    accounts = await get_user_accounts(user_id)

    return UserInfo(
        id=user_id,
        email=payload["email"],
        name=payload.get("name", payload["email"]),
        avatar_url=accounts[0].avatar_url if accounts else None,
        org_id=payload.get("org_id"),
        role=payload.get("role", "viewer"),
        providers=[a.provider for a in accounts],
    )


@router.get("/accounts")
async def list_linked_accounts(request: Request) -> list[dict[str, Any]]:
    """List OAuth accounts linked to the current user."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "Not authenticated")

    payload = await verify_session_token(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")

    accounts = await get_user_accounts(payload["sub"])
    return [
        {
            "provider": a.provider,
            "email": a.email,
            "name": a.name,
            "avatar_url": a.avatar_url,
            "created_at": a.created_at.isoformat(),
            "last_login": a.last_login.isoformat(),
        }
        for a in accounts
    ]


@router.post("/{provider}/link")
async def link_provider(
    provider: str,
    request: Request,
) -> LoginResponse:
    """Link an additional OAuth provider to the current account.

    Returns a redirect URL to initiate the OAuth flow for linking.
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "Not authenticated")

    payload = await verify_session_token(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")

    available = get_available_providers()
    if provider not in available:
        raise HTTPException(400, f"Provider '{provider}' is not configured")

    base_url = str(request.base_url).rstrip("/")
    callback_url = f"{base_url}/auth/{provider}/callback"

    # The link intent travels in the signed/served-side state, not on the
    # callback URL: the browser hands the callback URL back to us, so a marker
    # there proves nothing about who started the flow.
    auth_url = get_authorization_url(
        provider, callback_url, link_user_id=str(payload["sub"])
    )
    if not auth_url:
        raise HTTPException(500, "Failed to generate authorization URL")

    return LoginResponse(redirect_url=auth_url)


def _extract_token(request: Request) -> str | None:
    """Extract session token from request."""
    cookie_token = request.cookies.get("session")
    if cookie_token:
        return cookie_token

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None
