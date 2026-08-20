"""Comprehensive tests for OAuth2 authentication endpoints.

Tests cover:
- OAuth provider listing
- OAuth login flow initiation
- OAuth callback handling
- Session management (logout)
- Current user retrieval
- Account linking
- Token extraction utilities
- Error handling and security validations
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.auth.endpoints import (
    AuthProviderInfo,
    LoginResponse,
    PROVIDER_INFO,
    TokenResponse,
    UserInfo,
    _extract_token,
    _get_or_create_org_for_user,
)
from app.auth.oauth import (
    OAuthAccount,
    OAuthUser,
    create_session_token,
    find_account_by_email,
    find_account_by_provider,
    generate_oauth_state,
    get_user_accounts,
    link_oauth_account,
    verify_session_token,
)
from app import config as cfg
from app.database.engine import close_db


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    Replaces the three-dict clear these tests used to do between cases: linked
    accounts and pending login states are rows now (migration 008_oauth), so
    isolating a test means giving it its own database file.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    asyncio.run(close_db())
    yield
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


async def _state_row(state: str):
    """The stored row for a state, or None once it has been consumed."""
    from sqlalchemy import select

    from app.database.engine import ensure_schema, get_session
    from app.database.models import OAuthStateRecord

    await ensure_schema()
    async with get_session() as session:
        return (
            await session.execute(
                select(OAuthStateRecord).where(OAuthStateRecord.state == state)
            )
        ).scalar_one_or_none()


async def _backdate_state(state: str, *, minutes: int) -> None:
    """Age a state past its TTL by writing the column directly.

    Goes around `generate_oauth_state` on purpose: the point is to check what
    `verify_oauth_state` does with a row that is already too old, which no
    sequence of public calls can produce without waiting.
    """
    from sqlalchemy import update

    from app.database.engine import ensure_schema, get_session
    from app.database.models import OAuthStateRecord

    await ensure_schema()
    async with get_session() as session:
        await session.execute(
            update(OAuthStateRecord)
            .where(OAuthStateRecord.state == state)
            .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes))
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def _backdate_last_login(account_id: str, *, days: int) -> None:
    """Age an account's last_login so a refresh of it is observable."""
    from sqlalchemy import update

    from app.database.engine import ensure_schema, get_session
    from app.database.models import OAuthAccountRecord

    await ensure_schema()
    async with get_session() as session:
        await session.execute(
            update(OAuthAccountRecord)
            .where(OAuthAccountRecord.id == account_id)
            .values(last_login=datetime.now(timezone.utc) - timedelta(days=days))
            .execution_options(synchronize_session=False)
        )
        await session.commit()


# -----------------------------------------------------------------------------
# Pydantic Model Tests
# -----------------------------------------------------------------------------


class TestAuthProviderInfo:
    """Tests for AuthProviderInfo model."""

    def test_model_fields(self):
        """AuthProviderInfo has correct fields."""
        info = AuthProviderInfo(
            id="google",
            name="Google",
            icon="https://google.com/favicon.ico",
        )

        assert info.id == "google"
        assert info.name == "Google"
        assert info.icon == "https://google.com/favicon.ico"


class TestUserInfo:
    """Tests for UserInfo model."""

    def test_model_fields(self):
        """UserInfo has correct fields."""
        user = UserInfo(
            id="user_123",
            email="test@example.com",
            name="Test User",
            avatar_url="https://example.com/avatar.png",
            org_id="org_abc",
            role="admin",
            providers=["google", "github"],
        )

        assert user.id == "user_123"
        assert user.email == "test@example.com"
        assert user.name == "Test User"
        assert user.avatar_url == "https://example.com/avatar.png"
        assert user.org_id == "org_abc"
        assert user.role == "admin"
        assert user.providers == ["google", "github"]

    def test_optional_avatar_url(self):
        """UserInfo allows None avatar_url."""
        user = UserInfo(
            id="user_123",
            email="test@example.com",
            name="Test User",
            avatar_url=None,
            org_id=None,
            role="viewer",
            providers=[],
        )

        assert user.avatar_url is None
        assert user.org_id is None


class TestLoginResponse:
    """Tests for LoginResponse model."""

    def test_model_fields(self):
        """LoginResponse has redirect_url field."""
        response = LoginResponse(redirect_url="https://accounts.google.com/oauth")

        assert response.redirect_url == "https://accounts.google.com/oauth"


class TestTokenResponse:
    """Tests for TokenResponse model."""

    def test_model_fields(self):
        """TokenResponse has correct fields and defaults."""
        user = UserInfo(
            id="user_123",
            email="test@example.com",
            name="Test",
            avatar_url=None,
            org_id="org_1",
            role="viewer",
            providers=[],
        )
        response = TokenResponse(
            access_token="token_abc",
            expires_in=3600,
            user=user,
        )

        assert response.access_token == "token_abc"
        assert response.token_type == "bearer"
        assert response.expires_in == 3600
        assert response.user.id == "user_123"


class TestProviderInfo:
    """Tests for PROVIDER_INFO constant."""

    def test_google_provider_info(self):
        """PROVIDER_INFO includes Google with correct data."""
        assert "google" in PROVIDER_INFO
        assert PROVIDER_INFO["google"].id == "google"
        assert PROVIDER_INFO["google"].name == "Google"
        assert "google.com" in PROVIDER_INFO["google"].icon

    def test_github_provider_info(self):
        """PROVIDER_INFO includes GitHub with correct data."""
        assert "github" in PROVIDER_INFO
        assert PROVIDER_INFO["github"].id == "github"
        assert PROVIDER_INFO["github"].name == "GitHub"
        assert "github.com" in PROVIDER_INFO["github"].icon


# -----------------------------------------------------------------------------
# Helper Function Tests
# -----------------------------------------------------------------------------


class TestGetOrCreateOrgForUser:
    """Tests for _get_or_create_org_for_user helper."""

    def test_extracts_domain_from_email(self):
        """Creates org ID based on email domain."""
        org_id = _get_or_create_org_for_user("user_123", "alice@acme.com")

        assert org_id == "org_acme_com"

    def test_handles_subdomain(self):
        """Handles email with subdomain correctly."""
        org_id = _get_or_create_org_for_user("user_123", "bob@mail.example.co.uk")

        assert org_id == "org_mail_example_co_uk"

    def test_handles_missing_at_sign(self):
        """Returns 'personal' for invalid email format."""
        org_id = _get_or_create_org_for_user("user_123", "invalidemailformat")

        assert org_id == "org_personal"


class TestExtractToken:
    """Tests for _extract_token helper."""

    def test_extract_from_cookie(self):
        """Extracts token from session cookie."""
        mock_request = MagicMock()
        mock_request.cookies = {"session": "cookie_token_abc"}
        mock_request.headers = {}

        token = _extract_token(mock_request)

        assert token == "cookie_token_abc"

    def test_extract_from_bearer_header(self):
        """Extracts token from Authorization Bearer header."""
        mock_request = MagicMock()
        mock_request.cookies = {}
        mock_request.headers = {"authorization": "Bearer header_token_xyz"}

        token = _extract_token(mock_request)

        assert token == "header_token_xyz"

    def test_cookie_takes_precedence(self):
        """Cookie token takes precedence over header."""
        mock_request = MagicMock()
        mock_request.cookies = {"session": "cookie_token"}
        mock_request.headers = {"authorization": "Bearer header_token"}

        token = _extract_token(mock_request)

        assert token == "cookie_token"

    def test_returns_none_when_no_token(self):
        """Returns None when no token is present."""
        mock_request = MagicMock()
        mock_request.cookies = {}
        mock_request.headers = {}

        token = _extract_token(mock_request)

        assert token is None

    def test_returns_none_for_non_bearer_header(self):
        """Returns None for non-Bearer Authorization header."""
        mock_request = MagicMock()
        mock_request.cookies = {}
        mock_request.headers = {"authorization": "Basic dXNlcjpwYXNz"}

        token = _extract_token(mock_request)

        assert token is None


# -----------------------------------------------------------------------------
# OAuth Functions Tests
# -----------------------------------------------------------------------------


class TestOAuthStateFunctions:
    """Tests for OAuth state generation and verification."""

    async def test_generate_oauth_state_creates_token(self):
        """generate_oauth_state creates a secure state token."""
        state = await generate_oauth_state("google")

        assert state is not None
        assert len(state) > 20  # URL-safe base64 encoded
        assert await _state_row(state) is not None

    async def test_generate_oauth_state_stores_provider(self):
        """generate_oauth_state stores provider with state."""
        state = await generate_oauth_state("github")

        row = await _state_row(state)
        assert row is not None
        assert row.provider == "github"
        assert isinstance(row.created_at, datetime)

    async def test_verify_oauth_state_valid(self):
        """verify_oauth_state returns provider for valid state."""
        state = await generate_oauth_state("google")

        from app.auth.oauth import verify_oauth_state

        provider = await verify_oauth_state(state)

        assert provider == "google"
        assert await _state_row(state) is None  # State is consumed

    async def test_verify_oauth_state_invalid(self):
        """verify_oauth_state returns None for invalid state."""
        from app.auth.oauth import verify_oauth_state

        result = await verify_oauth_state("invalid_state_token")

        assert result is None

    async def test_verify_oauth_state_expired(self):
        """verify_oauth_state returns None for expired state."""
        from app.auth.oauth import verify_oauth_state

        state = await generate_oauth_state("google")
        await _backdate_state(state, minutes=20)

        result = await verify_oauth_state(state)

        assert result is None

    async def test_verify_oauth_state_consumes_exactly_once(self):
        """A replayed callback cannot reuse a state another request consumed.

        The dict version popped, which was already single-use within one pod.
        Across pods there was no shared record at all, so this is the property
        the table has to keep now that there is one.
        """
        from app.auth.oauth import verify_oauth_state

        state = await generate_oauth_state("google")

        assert await verify_oauth_state(state) == "google"
        assert await verify_oauth_state(state) is None


class TestSessionTokenFunctions:
    """Tests for session token creation and verification."""

    def test_create_session_token_returns_jwt(self):
        """create_session_token returns a JWT string."""
        token = create_session_token(
            user_id="user_123",
            org_id="org_abc",
            email="test@example.com",
            role="admin",
            expires_hours=24,
        )

        assert token is not None
        assert isinstance(token, str)
        # JWT has 3 parts separated by dots
        assert token.count(".") == 2

    def test_verify_session_token_valid(self):
        """verify_session_token decodes valid token."""
        token = create_session_token(
            user_id="user_123",
            org_id="org_abc",
            email="test@example.com",
            role="viewer",
        )

        payload = verify_session_token(token)

        assert payload is not None
        assert payload["sub"] == "user_123"
        assert payload["org_id"] == "org_abc"
        assert payload["email"] == "test@example.com"
        assert payload["role"] == "viewer"
        assert payload["type"] == "session"

    def test_verify_session_token_expired(self):
        """verify_session_token returns None for expired token."""
        # Create token that's already expired
        token = create_session_token(
            user_id="user_123",
            org_id="org_abc",
            email="test@example.com",
            expires_hours=-1,  # Expired an hour ago
        )

        payload = verify_session_token(token)

        assert payload is None

    def test_verify_session_token_invalid(self):
        """verify_session_token returns None for invalid token."""
        payload = verify_session_token("invalid.token.here")

        assert payload is None

    def test_verify_session_token_wrong_type(self):
        """verify_session_token returns None for non-session token type."""
        import jwt
        from app.config import get_settings

        s = get_settings()
        secret = s.copilot_secret_key or "dev-secret-key"

        # Create token with wrong type
        token = jwt.encode(
            {"sub": "user_123", "type": "api_key"},
            secret,
            algorithm="HS256",
        )

        payload = verify_session_token(token)

        assert payload is None


class TestOAuthAccountFunctions:
    """Tests for OAuth account management functions."""

    async def test_link_oauth_account_creates_account(self):
        """link_oauth_account creates and stores an account."""
        oauth_user = OAuthUser(
            provider="google",
            provider_id="google_12345",
            email="alice@example.com",
            name="Alice Smith",
            avatar_url="https://example.com/alice.png",
        )

        account = await link_oauth_account("user_alice", oauth_user)

        assert account.user_id == "user_alice"
        assert account.provider == "google"
        assert account.provider_id == "google_12345"
        assert account.email == "alice@example.com"
        assert account.name == "Alice Smith"

    async def test_link_oauth_account_adds_to_user_accounts(self):
        """link_oauth_account associates account with user."""
        oauth_user = OAuthUser(
            provider="github",
            provider_id="github_67890",
            email="bob@example.com",
            name="Bob Jones",
        )

        await link_oauth_account("user_bob", oauth_user)

        # Asserted through the query that replaced the _USER_ACCOUNTS index
        # rather than against the index itself, which no longer exists.
        accounts = await get_user_accounts("user_bob")
        assert [a.id for a in accounts] == ["github:github_67890"]

    async def test_relinking_keeps_the_user_id_already_on_the_identity(self):
        """A second link of the same identity does not repoint it.

        The callback mints a fresh `user_id` before it links, so two first-ever
        logins racing each other both arrive here with different ids. Last write
        wins would hand the account to whichever committed second and strand
        whatever the first had already been given.
        """
        oauth_user = OAuthUser(
            provider="google",
            provider_id="g_race",
            email="race@example.com",
            name="Race",
        )

        first = await link_oauth_account("user_first", oauth_user)
        second = await link_oauth_account("user_second", oauth_user)

        assert first.user_id == "user_first"
        assert second.user_id == "user_first"
        assert await get_user_accounts("user_second") == []

    async def test_relinking_refreshes_the_profile(self):
        """Re-linking picks up a changed display name or avatar."""
        await link_oauth_account(
            "user_p",
            OAuthUser(provider="google", provider_id="g_p", email="p@example.com", name="Old"),
        )
        await link_oauth_account(
            "user_p",
            OAuthUser(
                provider="google",
                provider_id="g_p",
                email="p@example.com",
                name="New",
                avatar_url="https://example.com/new.png",
            ),
        )

        account = await find_account_by_provider("google", "g_p")
        assert account is not None
        assert account.name == "New"
        assert account.avatar_url == "https://example.com/new.png"

    async def test_find_account_by_provider_found(self):
        """find_account_by_provider returns matching account."""
        oauth_user = OAuthUser(
            provider="google",
            provider_id="google_111",
            email="test@example.com",
            name="Test User",
        )
        await link_oauth_account("user_test", oauth_user)

        account = await find_account_by_provider("google", "google_111")

        assert account is not None
        assert account.email == "test@example.com"

    async def test_find_account_by_provider_not_found(self):
        """find_account_by_provider returns None for non-existent account."""
        account = await find_account_by_provider("google", "nonexistent")

        assert account is None

    async def test_find_account_by_email_found(self):
        """find_account_by_email returns matching account."""
        oauth_user = OAuthUser(
            provider="github",
            provider_id="gh_222",
            email="unique@example.com",
            name="Unique User",
        )
        await link_oauth_account("user_unique", oauth_user)

        account = await find_account_by_email("unique@example.com")

        assert account is not None
        assert account.provider == "github"

    async def test_find_account_by_email_case_insensitive(self):
        """find_account_by_email is case-insensitive."""
        oauth_user = OAuthUser(
            provider="google",
            provider_id="g_333",
            email="CaSeSeNsItIvE@Example.COM",
            name="Case Test",
        )
        await link_oauth_account("user_case", oauth_user)

        account = await find_account_by_email("casesensitive@example.com")

        assert account is not None
        # Folded on the way in, so the stored value is directly indexable
        # instead of needing a scan that lower-cases every row.
        assert account.email == "casesensitive@example.com"

    async def test_find_account_by_email_not_found(self):
        """find_account_by_email returns None for non-existent email."""
        account = await find_account_by_email("notfound@example.com")

        assert account is None

    async def test_find_account_by_email_is_stable_across_calls(self):
        """With several identities on one address, the same one comes back.

        It decides which existing user id a newly-seen provider gets folded
        into, so an unordered query would silently make that a coin flip.
        """
        for i, provider in enumerate(("google", "github")):
            await link_oauth_account(
                f"user_{provider}",
                OAuthUser(
                    provider=provider,
                    provider_id=f"id_{i}",
                    email="shared@example.com",
                    name="Shared",
                ),
            )

        first = await find_account_by_email("shared@example.com")
        assert first is not None
        for _ in range(5):
            again = await find_account_by_email("shared@example.com")
            assert again is not None and again.id == first.id

    async def test_get_user_accounts_returns_all(self):
        """get_user_accounts returns all linked accounts for user."""
        oauth_google = OAuthUser(
            provider="google",
            provider_id="g_multi_1",
            email="multi@example.com",
            name="Multi User",
        )
        oauth_github = OAuthUser(
            provider="github",
            provider_id="gh_multi_2",
            email="multi@example.com",
            name="Multi User",
        )
        await link_oauth_account("user_multi", oauth_google)
        await link_oauth_account("user_multi", oauth_github)

        accounts = await get_user_accounts("user_multi")

        assert len(accounts) == 2
        providers = [a.provider for a in accounts]
        assert "google" in providers
        assert "github" in providers

    async def test_get_user_accounts_empty(self):
        """get_user_accounts returns empty list for unknown user."""
        accounts = await get_user_accounts("nonexistent_user")

        assert accounts == []

    async def test_touch_last_login_persists(self):
        """last_login is written back, not assigned to a detached copy.

        The callback used to do `existing.last_login = now` on the object the
        lookup returned. That object is a copy of a row now, so the assignment
        would update nothing and every account would report the timestamp of
        its first sign-in forever.
        """
        from app.auth.oauth import touch_last_login

        await link_oauth_account(
            "user_t",
            OAuthUser(provider="google", provider_id="g_t", email="t@example.com", name="T"),
        )
        await _backdate_last_login("google:g_t", days=3)

        stale = await find_account_by_provider("google", "g_t")
        await touch_last_login("google:g_t")
        fresh = await find_account_by_provider("google", "g_t")

        assert stale is not None and fresh is not None
        assert fresh.last_login > stale.last_login


# -----------------------------------------------------------------------------
# Endpoint Tests (using mocks)
# -----------------------------------------------------------------------------


class TestListProvidersEndpoint:
    """Tests for GET /auth/providers endpoint."""

    def test_list_providers_returns_configured(self):
        """list_providers returns configured providers."""
        from app.auth.endpoints import list_providers

        async def scenario():
            with patch("app.auth.endpoints.get_available_providers") as mock_get:
                mock_get.return_value = ["google", "github"]

                result = await list_providers()

                assert len(result) == 2
                assert result[0].id == "google"
                assert result[1].id == "github"

        _run(scenario())

    def test_list_providers_filters_unknown(self):
        """list_providers filters out unknown providers."""
        from app.auth.endpoints import list_providers

        async def scenario():
            with patch("app.auth.endpoints.get_available_providers") as mock_get:
                mock_get.return_value = ["google", "unknown_provider", "github"]

                result = await list_providers()

                # Only google and github should be returned
                assert len(result) == 2
                ids = [p.id for p in result]
                assert "unknown_provider" not in ids

        _run(scenario())

    def test_list_providers_empty(self):
        """list_providers returns empty list when no providers configured."""
        from app.auth.endpoints import list_providers

        async def scenario():
            with patch("app.auth.endpoints.get_available_providers") as mock_get:
                mock_get.return_value = []

                result = await list_providers()

                assert result == []

        _run(scenario())


class TestOAuthLoginEndpoint:
    """Tests for GET /auth/{provider}/login endpoint."""

    def test_oauth_login_returns_redirect_url(self):
        """oauth_login returns authorization URL."""
        from app.auth.endpoints import oauth_login

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.get_available_providers") as mock_providers, \
                 patch("app.auth.endpoints.get_authorization_url") as mock_auth_url:
                mock_providers.return_value = ["google"]
                mock_auth_url.return_value = "https://accounts.google.com/oauth?state=abc"

                result = await oauth_login("google", mock_request)

                assert isinstance(result, LoginResponse)
                assert "accounts.google.com" in result.redirect_url

        _run(scenario())

    def test_oauth_login_rejects_unconfigured_provider(self):
        """oauth_login raises 400 for unconfigured provider."""
        from app.auth.endpoints import oauth_login

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.get_available_providers") as mock_providers:
                mock_providers.return_value = ["google"]

                with pytest.raises(HTTPException) as exc_info:
                    await oauth_login("slack", mock_request)

                assert exc_info.value.status_code == 400
                assert "not configured" in exc_info.value.detail

        _run(scenario())

    def test_oauth_login_handles_auth_url_failure(self):
        """oauth_login raises 500 when auth URL generation fails."""
        from app.auth.endpoints import oauth_login

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.get_available_providers") as mock_providers, \
                 patch("app.auth.endpoints.get_authorization_url") as mock_auth_url:
                mock_providers.return_value = ["google"]
                mock_auth_url.return_value = None

                with pytest.raises(HTTPException) as exc_info:
                    await oauth_login("google", mock_request)

                assert exc_info.value.status_code == 500

        _run(scenario())

    def test_oauth_login_with_redirect_uri(self):
        """oauth_login includes redirect_uri in callback URL."""
        from app.auth.endpoints import oauth_login

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.get_available_providers") as mock_providers, \
                 patch("app.auth.endpoints.get_authorization_url") as mock_auth_url:
                mock_providers.return_value = ["github"]
                mock_auth_url.return_value = "https://github.com/login/oauth"

                await oauth_login("github", mock_request, redirect_uri="https://myapp.com")

                # Verify callback URL includes redirect_uri parameter
                call_args = mock_auth_url.call_args
                assert "redirect_uri=https://myapp.com" in call_args[0][1]

        _run(scenario())


class TestOAuthCallbackEndpoint:
    """Tests for GET /auth/{provider}/callback endpoint."""

    def test_callback_handles_oauth_error(self):
        """oauth_callback handles OAuth provider error."""
        from app.auth.endpoints import oauth_callback

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with pytest.raises(HTTPException) as exc_info:
                await oauth_callback(
                    provider="google",
                    request=mock_request,
                    error="access_denied",
                )

            assert exc_info.value.status_code == 400
            assert "access_denied" in exc_info.value.detail

        _run(scenario())

    def test_callback_redirects_on_error_with_redirect_uri(self):
        """oauth_callback redirects to redirect_uri on error."""
        from app.auth.endpoints import oauth_callback
        from fastapi.responses import RedirectResponse

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            result = await oauth_callback(
                provider="google",
                request=mock_request,
                error="access_denied",
                redirect_uri="https://myapp.com/callback",
            )

            assert isinstance(result, RedirectResponse)
            assert "error=access_denied" in str(result.headers.get("location", ""))

        _run(scenario())

    def test_callback_rejects_missing_code(self):
        """oauth_callback raises 400 when code is missing."""
        from app.auth.endpoints import oauth_callback

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with pytest.raises(HTTPException) as exc_info:
                await oauth_callback(
                    provider="google",
                    request=mock_request,
                    code="",
                    state="some_state",
                )

            assert exc_info.value.status_code == 400
            assert "code" in exc_info.value.detail.lower()

        _run(scenario())

    def test_callback_rejects_invalid_state(self):
        """oauth_callback raises 400 for invalid state."""
        from app.auth.endpoints import oauth_callback

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.verify_oauth_state") as mock_verify:
                mock_verify.return_value = None

                with pytest.raises(HTTPException) as exc_info:
                    await oauth_callback(
                        provider="google",
                        request=mock_request,
                        code="auth_code_123",
                        state="invalid_state",
                    )

                assert exc_info.value.status_code == 400
                assert "state" in exc_info.value.detail.lower()

        _run(scenario())

    def test_callback_rejects_state_mismatch(self):
        """oauth_callback raises 400 when state provider doesn't match."""
        from app.auth.endpoints import oauth_callback

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.verify_oauth_state") as mock_verify:
                # State was for github but callback is for google
                mock_verify.return_value = "github"

                with pytest.raises(HTTPException) as exc_info:
                    await oauth_callback(
                        provider="google",
                        request=mock_request,
                        code="auth_code_123",
                        state="valid_state",
                    )

                assert exc_info.value.status_code == 400
                assert "mismatch" in exc_info.value.detail.lower()

        _run(scenario())

    def test_callback_handles_token_exchange_failure(self):
        """oauth_callback raises 500 when token exchange fails."""
        from app.auth.endpoints import oauth_callback

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.verify_oauth_state") as mock_verify, \
                 patch("app.auth.endpoints.exchange_code_for_token") as mock_exchange:
                mock_verify.return_value = "google"
                mock_exchange.return_value = None

                with pytest.raises(HTTPException) as exc_info:
                    await oauth_callback(
                        provider="google",
                        request=mock_request,
                        code="auth_code_123",
                        state="valid_state",
                    )

                assert exc_info.value.status_code == 500

        _run(scenario())

    def test_callback_handles_missing_access_token(self):
        """oauth_callback raises 500 when access token is missing."""
        from app.auth.endpoints import oauth_callback

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.verify_oauth_state") as mock_verify, \
                 patch("app.auth.endpoints.exchange_code_for_token") as mock_exchange:
                mock_verify.return_value = "google"
                mock_exchange.return_value = {"token_type": "bearer"}  # No access_token

                with pytest.raises(HTTPException) as exc_info:
                    await oauth_callback(
                        provider="google",
                        request=mock_request,
                        code="auth_code_123",
                        state="valid_state",
                    )

                assert exc_info.value.status_code == 500

        _run(scenario())

    def test_callback_handles_userinfo_failure(self):
        """oauth_callback raises 500 when user info retrieval fails."""
        from app.auth.endpoints import oauth_callback

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.verify_oauth_state") as mock_verify, \
                 patch("app.auth.endpoints.exchange_code_for_token") as mock_exchange, \
                 patch("app.auth.endpoints.get_user_info") as mock_userinfo:
                mock_verify.return_value = "google"
                mock_exchange.return_value = {"access_token": "token_xyz"}
                mock_userinfo.return_value = None

                with pytest.raises(HTTPException) as exc_info:
                    await oauth_callback(
                        provider="google",
                        request=mock_request,
                        code="auth_code_123",
                        state="valid_state",
                    )

                assert exc_info.value.status_code == 500

        _run(scenario())

    def test_callback_success_new_user(self):
        """oauth_callback creates session for new user."""
        from app.auth.endpoints import oauth_callback
        from fastapi.responses import RedirectResponse

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            oauth_user = OAuthUser(
                provider="google",
                provider_id="google_new_user",
                email="newuser@example.com",
                name="New User",
            )

            with patch("app.auth.endpoints.verify_oauth_state") as mock_verify, \
                 patch("app.auth.endpoints.exchange_code_for_token") as mock_exchange, \
                 patch("app.auth.endpoints.get_user_info") as mock_userinfo, \
                 patch("app.auth.endpoints.find_account_by_provider") as mock_find_provider, \
                 patch("app.auth.endpoints.find_account_by_email") as mock_find_email, \
                 patch("app.auth.endpoints.link_oauth_account") as mock_link, \
                 patch("app.auth.endpoints.create_session_token") as mock_create_token, \
                 patch("app.auth.endpoints.get_settings") as mock_settings:
                mock_verify.return_value = "google"
                mock_exchange.return_value = {"access_token": "token_xyz"}
                mock_userinfo.return_value = oauth_user
                mock_find_provider.return_value = None
                mock_find_email.return_value = None
                mock_create_token.return_value = "session_token_abc"
                mock_settings.return_value.is_production = False

                result = await oauth_callback(
                    provider="google",
                    request=mock_request,
                    code="auth_code_123",
                    state="valid_state",
                )

                assert isinstance(result, RedirectResponse)
                mock_link.assert_called_once()

        _run(scenario())

    def test_callback_success_existing_user(self):
        """oauth_callback creates session for existing user."""
        from app.auth.endpoints import oauth_callback
        from fastapi.responses import RedirectResponse

        async def scenario():
            mock_request = MagicMock()
            mock_request.base_url = "https://app.example.com/"

            oauth_user = OAuthUser(
                provider="google",
                provider_id="google_existing",
                email="existing@example.com",
                name="Existing User",
            )

            existing_account = OAuthAccount(
                id="google:google_existing",
                user_id="user_existing",
                provider="google",
                provider_id="google_existing",
                email="existing@example.com",
                name="Existing User",
                avatar_url=None,
                created_at=datetime.now(timezone.utc),
                last_login=datetime.now(timezone.utc),
            )

            with patch("app.auth.endpoints.verify_oauth_state") as mock_verify, \
                 patch("app.auth.endpoints.exchange_code_for_token") as mock_exchange, \
                 patch("app.auth.endpoints.get_user_info") as mock_userinfo, \
                 patch("app.auth.endpoints.find_account_by_provider") as mock_find_provider, \
                 patch("app.auth.endpoints.create_session_token") as mock_create_token, \
                 patch("app.auth.endpoints.get_settings") as mock_settings:
                mock_verify.return_value = "google"
                mock_exchange.return_value = {"access_token": "token_xyz"}
                mock_userinfo.return_value = oauth_user
                mock_find_provider.return_value = existing_account
                mock_create_token.return_value = "session_token_abc"
                mock_settings.return_value.is_production = False

                result = await oauth_callback(
                    provider="google",
                    request=mock_request,
                    code="auth_code_123",
                    state="valid_state",
                )

                assert isinstance(result, RedirectResponse)
                # Session should use existing user_id
                call_args = mock_create_token.call_args
                assert call_args[1]["user_id"] == "user_existing"

        _run(scenario())


class TestLogoutEndpoint:
    """Tests for POST /auth/logout endpoint."""

    def test_logout_clears_cookie(self):
        """logout clears session cookie."""
        from app.auth.endpoints import logout

        async def scenario():
            mock_response = MagicMock()

            result = await logout(mock_response)

            mock_response.delete_cookie.assert_called_once_with("session")
            assert result == {"status": "logged_out"}

        _run(scenario())


class TestGetCurrentUserEndpoint:
    """Tests for GET /auth/me endpoint."""

    def test_get_current_user_no_token(self):
        """get_current_user raises 401 when no token present."""
        from app.auth.endpoints import get_current_user

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {}
            mock_request.headers = {}

            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(mock_request)

            assert exc_info.value.status_code == 401
            assert "Not authenticated" in exc_info.value.detail

        _run(scenario())

    def test_get_current_user_invalid_token(self):
        """get_current_user raises 401 for invalid token."""
        from app.auth.endpoints import get_current_user

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {"session": "invalid_token"}
            mock_request.headers = {}

            with patch("app.auth.endpoints.verify_session_token") as mock_verify:
                mock_verify.return_value = None

                with pytest.raises(HTTPException) as exc_info:
                    await get_current_user(mock_request)

                assert exc_info.value.status_code == 401

        _run(scenario())

    def test_get_current_user_success(self):
        """get_current_user returns user info for valid token."""
        from app.auth.endpoints import get_current_user

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {"session": "valid_token"}
            mock_request.headers = {}

            mock_account = OAuthAccount(
                id="google:g123",
                user_id="user_123",
                provider="google",
                provider_id="g123",
                email="test@example.com",
                name="Test User",
                avatar_url="https://example.com/avatar.png",
                created_at=datetime.now(timezone.utc),
                last_login=datetime.now(timezone.utc),
            )

            with patch("app.auth.endpoints.verify_session_token") as mock_verify, \
                 patch("app.auth.endpoints.get_user_accounts") as mock_accounts:
                mock_verify.return_value = {
                    "sub": "user_123",
                    "email": "test@example.com",
                    "name": "Test User",
                    "org_id": "org_abc",
                    "role": "admin",
                }
                mock_accounts.return_value = [mock_account]

                result = await get_current_user(mock_request)

                assert result.id == "user_123"
                assert result.email == "test@example.com"
                assert result.avatar_url == "https://example.com/avatar.png"
                assert result.org_id == "org_abc"
                assert result.role == "admin"
                assert "google" in result.providers

        _run(scenario())


class TestListLinkedAccountsEndpoint:
    """Tests for GET /auth/accounts endpoint."""

    def test_list_linked_accounts_no_token(self):
        """list_linked_accounts raises 401 when no token present."""
        from app.auth.endpoints import list_linked_accounts

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {}
            mock_request.headers = {}

            with pytest.raises(HTTPException) as exc_info:
                await list_linked_accounts(mock_request)

            assert exc_info.value.status_code == 401

        _run(scenario())

    def test_list_linked_accounts_success(self):
        """list_linked_accounts returns all linked accounts."""
        from app.auth.endpoints import list_linked_accounts

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {"session": "valid_token"}
            mock_request.headers = {}

            now = datetime.now(timezone.utc)
            mock_accounts = [
                OAuthAccount(
                    id="google:g123",
                    user_id="user_123",
                    provider="google",
                    provider_id="g123",
                    email="test@gmail.com",
                    name="Test Gmail",
                    avatar_url=None,
                    created_at=now,
                    last_login=now,
                ),
                OAuthAccount(
                    id="github:gh456",
                    user_id="user_123",
                    provider="github",
                    provider_id="gh456",
                    email="test@github.com",
                    name="Test GitHub",
                    avatar_url="https://github.com/avatar.png",
                    created_at=now,
                    last_login=now,
                ),
            ]

            with patch("app.auth.endpoints.verify_session_token") as mock_verify, \
                 patch("app.auth.endpoints.get_user_accounts") as mock_get_accounts:
                mock_verify.return_value = {"sub": "user_123"}
                mock_get_accounts.return_value = mock_accounts

                result = await list_linked_accounts(mock_request)

                assert len(result) == 2
                assert result[0]["provider"] == "google"
                assert result[1]["provider"] == "github"

        _run(scenario())


class TestLinkProviderEndpoint:
    """Tests for POST /auth/{provider}/link endpoint."""

    def test_link_provider_no_token(self):
        """link_provider raises 401 when no token present."""
        from app.auth.endpoints import link_provider

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {}
            mock_request.headers = {}

            with pytest.raises(HTTPException) as exc_info:
                await link_provider("github", mock_request)

            assert exc_info.value.status_code == 401

        _run(scenario())

    def test_link_provider_invalid_token(self):
        """link_provider raises 401 for invalid token."""
        from app.auth.endpoints import link_provider

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {"session": "invalid_token"}
            mock_request.headers = {}

            with patch("app.auth.endpoints.verify_session_token") as mock_verify:
                mock_verify.return_value = None

                with pytest.raises(HTTPException) as exc_info:
                    await link_provider("github", mock_request)

                assert exc_info.value.status_code == 401

        _run(scenario())

    def test_link_provider_unconfigured_provider(self):
        """link_provider raises 400 for unconfigured provider."""
        from app.auth.endpoints import link_provider

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {"session": "valid_token"}
            mock_request.headers = {}
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.verify_session_token") as mock_verify, \
                 patch("app.auth.endpoints.get_available_providers") as mock_providers:
                mock_verify.return_value = {"sub": "user_123"}
                mock_providers.return_value = ["google"]  # github not available

                with pytest.raises(HTTPException) as exc_info:
                    await link_provider("github", mock_request)

                assert exc_info.value.status_code == 400
                assert "not configured" in exc_info.value.detail

        _run(scenario())

    def test_link_provider_success(self):
        """link_provider returns redirect URL for valid request."""
        from app.auth.endpoints import link_provider

        async def scenario():
            mock_request = MagicMock()
            mock_request.cookies = {"session": "valid_token"}
            mock_request.headers = {}
            mock_request.base_url = "https://app.example.com/"

            with patch("app.auth.endpoints.verify_session_token") as mock_verify, \
                 patch("app.auth.endpoints.get_available_providers") as mock_providers, \
                 patch("app.auth.endpoints.get_authorization_url") as mock_auth_url:
                mock_verify.return_value = {"sub": "user_123"}
                mock_providers.return_value = ["google", "github"]
                mock_auth_url.return_value = "https://github.com/login/oauth?state=xyz"

                result = await link_provider("github", mock_request)

                assert isinstance(result, LoginResponse)
                assert "github.com" in result.redirect_url

        _run(scenario())


# -----------------------------------------------------------------------------
# Security Tests
# -----------------------------------------------------------------------------


class TestSecurityValidations:
    """Tests for security-related validations."""

    async def test_csrf_state_protection(self):
        """OAuth state provides CSRF protection."""
        from app.auth.oauth import generate_oauth_state, verify_oauth_state

        # Generate state for google
        state = await generate_oauth_state("google")

        # Attacker can't reuse state for different provider
        result = await verify_oauth_state(state)
        assert result == "google"

        # State can only be used once
        result2 = await verify_oauth_state(state)
        assert result2 is None

    async def test_state_expiration(self):
        """OAuth state expires after timeout."""
        from app.auth.oauth import verify_oauth_state

        state = await generate_oauth_state("google")
        await _backdate_state(state, minutes=20)

        result = await verify_oauth_state(state)
        assert result is None

    def test_session_token_expiration(self):
        """Session tokens properly expire."""
        token = create_session_token(
            user_id="user_123",
            org_id="org_abc",
            email="test@example.com",
            expires_hours=-1,  # Already expired
        )

        payload = verify_session_token(token)
        assert payload is None

    def test_session_token_type_validation(self):
        """Session tokens must have correct type."""
        import jwt
        from app.config import get_settings

        s = get_settings()
        secret = s.copilot_secret_key or "dev-secret-key"

        # Create token with wrong type
        wrong_type_token = jwt.encode(
            {
                "sub": "user_123",
                "type": "refresh",  # Wrong type
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            secret,
            algorithm="HS256",
        )

        payload = verify_session_token(wrong_type_token)
        assert payload is None

    def test_cookie_httponly_setting(self):
        """Session cookie should be httponly."""
        # This is a documentation test - the actual behavior is in oauth_callback
        # The cookie is set with httponly=True to prevent XSS attacks
        from app.auth.endpoints import oauth_callback

        # Verify the cookie settings in the code
        import inspect
        source = inspect.getsource(oauth_callback)
        assert "httponly=True" in source
