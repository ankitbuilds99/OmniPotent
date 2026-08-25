"""Integration test fixtures with real database and Redis."""

from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url() -> str:
    """Get database URL from environment."""
    url = os.getenv("COPILOT_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set - skipping integration tests")
    return url


@pytest.fixture(scope="session")
def redis_url() -> str:
    """Get Redis URL from environment."""
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set - skipping integration tests")
    return url


def asyncpg_dsn(url: str) -> str:
    """Strip the SQLAlchemy driver from a DSN so asyncpg will take it.

    `COPILOT_DATABASE_URL` is a SQLAlchemy URL — `postgresql+asyncpg://…` — and
    `asyncpg.connect` rejects the `+asyncpg` suffix outright with
    ClientConfigurationError. Every db-backed test in this directory errored at
    fixture setup on that, which nobody saw because CI never ran the directory.
    """
    scheme, sep, rest = url.partition("://")
    return f"{scheme.split('+')[0]}{sep}{rest}"


@pytest.fixture(scope="session")
def _schema(database_url: str) -> None:
    """Migrate the database once for the session.

    Nothing here ever created a schema: ASGITransport does not run the app's
    lifespan, and `init_db()` only calls `create_all` for SQLite — on Postgres
    it logs "use Alembic for migrations" and returns. So every db-backed test
    skipped on "organizations table does not exist" and the suite reported
    success while running almost nothing.

    Running the real migration chain rather than `create_all` is deliberate:
    it is what production does, so a revision that does not apply fails here.
    """
    from alembic import command
    from alembic.config import Config

    os.environ["COPILOT_DATABASE_URL"] = database_url
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")


API_TOKEN = "integration-api-token"


@pytest.fixture
def app(database_url: str, redis_url: str, _schema: None, monkeypatch: pytest.MonkeyPatch):
    """Create application with real database.

    `COPILOT_API_TOKEN` is set because `require_auth` returns early when it is
    empty -- that is the documented dev behaviour, but it meant every "should
    require auth" assertion in this directory was being made against an app with
    authentication switched off entirely.

    The engine is reset per test: it caches a connection pool bound to the loop
    that created it, and pytest-asyncio gives each test a fresh loop, so a reused
    pool raises "attached to a different loop" on the second db-backed test.
    """
    import app.config as cfg
    from app.database import engine as db_engine

    monkeypatch.setenv("COPILOT_DATABASE_URL", database_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("COPILOT_API_TOKEN", API_TOKEN)
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None

    from app.api.main import app as _app

    yield _app

    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def api_token() -> str:
    """The bearer token the app fixture configures."""
    return API_TOKEN


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """Create authenticated test client."""
    client.headers["Authorization"] = "Bearer test-integration-token"
    return client


@pytest_asyncio.fixture
async def db_conn(database_url: str, _schema: None):
    """Get database connection."""
    import asyncpg

    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    yield conn
    await conn.close()


@pytest.fixture
def pagerduty_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure the secret the PagerDuty webhook verifies deliveries against.

    The route fails closed when it is unset, so without this a test can only
    ever exercise the reject path.
    """
    import app.config as cfg

    secret = "integration_pagerduty_secret"
    monkeypatch.setenv("PAGERDUTY_WEBHOOK_SECRET", secret)
    cfg.get_settings.cache_clear()
    yield secret
    cfg.get_settings.cache_clear()


SLACK_TEST_SECRET = "integration_signing_secret"


@pytest.fixture
def slack_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure the signing secret the Slack routes verify against."""
    import app.config as cfg

    monkeypatch.setenv("SLACK_SIGNING_SECRET", SLACK_TEST_SECRET)
    cfg.get_settings.cache_clear()
    yield SLACK_TEST_SECRET
    cfg.get_settings.cache_clear()


def slack_headers(body: bytes, secret: str = SLACK_TEST_SECRET) -> dict[str, str]:
    """The headers Slack itself sends: v0 HMAC over `v0:<ts>:<raw body>`.

    Every Slack route carries a router-wide signature dependency that fails
    closed, so a request without these is rejected — which is the point. Tests
    that want the handler to run have to sign like Slack does.
    """
    import hashlib
    import hmac
    import time

    timestamp = str(int(time.time()))
    basestring = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={digest}",
    }


@pytest_asyncio.fixture
async def redis_client(redis_url: str):
    """Create Redis client for each test."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=False)
    yield client
    try:
        await client.flushdb()
    except Exception:
        pass
    try:
        await client.aclose()
    except Exception:
        pass


@pytest_asyncio.fixture
async def test_org(db_conn) -> dict:
    """Create or get test organization."""
    import uuid

    try:
        # First try to get existing org
        existing = await db_conn.fetchrow(
            "SELECT id, slug, name FROM organizations WHERE slug = $1",
            "test-org",
        )
        if existing:
            return {
                "id": str(existing["id"]),
                "slug": existing["slug"],
                "name": existing["name"],
            }

        # Create new org if doesn't exist
        org_id = uuid.uuid4()
        # `settings` is NOT NULL with no server default -- the default lives on
        # the ORM column, so a raw INSERT that omits it fails. Every db-backed
        # test in this directory errored here.
        await db_conn.execute(
            """
            INSERT INTO organizations (id, slug, name, plan, settings)
            VALUES ($1, $2, $3, $4, $5)
            """,
            org_id,
            "test-org",
            "Test Organization",
            "enterprise",
            "{}",
        )
        return {
            "id": str(org_id),
            "slug": "test-org",
            "name": "Test Organization",
        }
    except Exception as e:
        if "does not exist" in str(e):
            pytest.skip("organizations table does not exist")
        raise


@pytest_asyncio.fixture
async def test_api_key(db_conn, test_org: dict) -> str:
    """Create test API key."""
    import hashlib
    import uuid

    key_id = uuid.uuid4()
    raw_key = f"omni_test_{uuid.uuid4().hex}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    try:
        # `is_active` is NOT NULL with the default on the ORM column, same as
        # `organizations.settings` -- a raw INSERT has to supply it.
        await db_conn.execute(
            """
            INSERT INTO api_keys (id, org_id, key_hash, key_prefix, name, role, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING
            """,
            key_id,
            uuid.UUID(test_org["id"]),
            key_hash,
            raw_key[:12],
            "Integration Test Key",
            "admin",
            True,
        )
    except Exception as e:
        if "does not exist" in str(e) or "foreign key" in str(e).lower():
            pytest.skip(f"api_keys table issue: {e}")
        raise

    return raw_key
