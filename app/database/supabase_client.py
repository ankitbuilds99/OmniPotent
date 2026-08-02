"""Supabase client for OmniPotent.

Provides both the Supabase JS-style client (for RLS-aware queries) and direct
PostgreSQL access via SQLAlchemy (for admin operations and migrations).

Usage:
    # For user-facing queries (respects RLS):
    client = get_supabase_client()
    result = client.table("incidents").select("*").execute()

    # For admin operations (bypasses RLS):
    admin = get_supabase_admin_client()
    result = admin.table("incidents").insert({"title": "..."}).execute()

    # Check if Supabase is configured:
    if is_supabase_configured():
        ...
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from app.config import get_settings

log = logging.getLogger("omnipotent.database.supabase")

_HAS_SUPABASE = False
try:
    from supabase import Client, create_client
    _HAS_SUPABASE = True
except ImportError:
    pass

if TYPE_CHECKING:
    from supabase import Client


def is_supabase_configured() -> bool:
    """Check if Supabase credentials are configured."""
    s = get_settings()
    return bool(s.supabase_url and s.supabase_anon_key)


def _require_supabase() -> None:
    """Raise ImportError if supabase package is not installed."""
    if not _HAS_SUPABASE:
        raise ImportError(
            "Supabase client is required. Install with: pip install -e '.[supabase]'"
        )


@lru_cache(maxsize=1)
def get_supabase_client() -> "Client":
    """Get the Supabase client using the anon key.

    This client respects Row Level Security (RLS) policies and should be used
    for user-facing operations where the user's JWT is passed for auth.

    Returns:
        Supabase client configured with the anon key.

    Raises:
        ImportError: If supabase package is not installed.
        ValueError: If Supabase URL or anon key is not configured.
    """
    _require_supabase()

    s = get_settings()

    if not s.supabase_url:
        raise ValueError(
            "SUPABASE_URL is not configured. Set it in your .env file or environment."
        )
    if not s.supabase_anon_key:
        raise ValueError(
            "SUPABASE_ANON_KEY is not configured. Set it in your .env file or environment."
        )

    log.info("Creating Supabase client for %s", s.supabase_url)
    return create_client(s.supabase_url, s.supabase_anon_key)


@lru_cache(maxsize=1)
def get_supabase_admin_client() -> "Client":
    """Get the Supabase admin client using the service role key.

    This client BYPASSES Row Level Security (RLS) and should only be used for
    admin operations, background jobs, and server-to-server communication.

    WARNING: Never expose this client to user-facing code paths. The service
    role key has full access to all data regardless of RLS policies.

    Returns:
        Supabase client configured with the service role key.

    Raises:
        ImportError: If supabase package is not installed.
        ValueError: If Supabase URL or service role key is not configured.
    """
    _require_supabase()

    s = get_settings()

    if not s.supabase_url:
        raise ValueError(
            "SUPABASE_URL is not configured. Set it in your .env file or environment."
        )
    if not s.supabase_service_role_key:
        raise ValueError(
            "SUPABASE_SERVICE_ROLE_KEY is not configured. This is required for "
            "admin operations. Set it in your .env file or environment."
        )

    log.info("Creating Supabase ADMIN client for %s (RLS bypassed)", s.supabase_url)
    return create_client(s.supabase_url, s.supabase_service_role_key)


def get_supabase_database_url() -> str | None:
    """Get the direct PostgreSQL connection URL for Supabase.

    This is used for SQLAlchemy/Alembic migrations and raw SQL queries.
    The connection string should use the pooler endpoint for serverless
    environments (port 6543 for transaction mode, 5432 for session mode).

    Returns:
        PostgreSQL connection URL, or None if not configured.
    """
    s = get_settings()
    url = s.supabase_database_url

    if not url:
        return None

    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return url

    log.warning("Invalid SUPABASE_DATABASE_URL format: %s", url[:20] + "...")
    return None


def clear_supabase_clients() -> None:
    """Clear cached Supabase clients.

    Useful for testing or when credentials change at runtime.
    """
    get_supabase_client.cache_clear()
    get_supabase_admin_client.cache_clear()
    log.info("Supabase client caches cleared")
