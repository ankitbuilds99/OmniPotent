"""Authentication module.

Provides OAuth2/SSO authentication with multiple providers:
- Google OAuth2
- GitHub OAuth2
- Session management with JWT tokens
"""

from app.auth.oauth import (
    OAuthConfig,
    OAuthUser,
    OAuthAccount,
    get_oauth_config,
    get_available_providers,
    get_authorization_url,
    exchange_code_for_token,
    get_user_info,
    create_session_token,
    decode_session_token,
    verify_session_token,
    revoke_session,
    verify_oauth_state,
    link_oauth_account,
    find_account_by_provider,
    find_account_by_email,
    get_user_accounts,
)

__all__ = [
    "OAuthConfig",
    "OAuthUser",
    "OAuthAccount",
    "get_oauth_config",
    "get_available_providers",
    "get_authorization_url",
    "exchange_code_for_token",
    "get_user_info",
    "create_session_token",
    "decode_session_token",
    "verify_session_token",
    "revoke_session",
    "verify_oauth_state",
    "link_oauth_account",
    "find_account_by_provider",
    "find_account_by_email",
    "get_user_accounts",
]
