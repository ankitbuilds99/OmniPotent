"""Integration test fixtures with real database and Redis."""

from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Skip if required services aren't available
pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for session-scoped fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    """Get database URL from environment."""
    url = os.getenv("DATABASE_URL")
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


@pytest_asyncio.fixture(scope="session")
async def app(database_url: str, redis_url: str):
    """Create application with real database."""
    os.environ["DATABASE_URL"] = database_url
    os.environ["REDIS_URL"] = redis_url
    os.environ["TESTING"] = "true"

    from app.api.main import app as _app
    yield _app


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


@pytest_asyncio.fixture(scope="session")
async def db_pool(database_url: str):
    """Create database connection pool."""
    import asyncpg

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def db_conn(db_pool):
    """Get database connection."""
    async with db_pool.acquire() as conn:
        yield conn


@pytest_asyncio.fixture(scope="session")
async def redis_client(redis_url: str):
    """Create Redis client."""
    import redis.asyncio as redis

    client = redis.from_url(redis_url)
    yield client
    await client.close()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_redis(redis_client):
    """Clean up Redis between tests."""
    yield
    await redis_client.flushdb()


@pytest_asyncio.fixture
async def test_org(db_conn) -> dict:
    """Create test organization."""
    import uuid

    org_id = str(uuid.uuid4())
    await db_conn.execute(
        """
        INSERT INTO organizations (id, slug, name, plan)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (slug) DO NOTHING
        """,
        uuid.UUID(org_id),
        "test-org",
        "Test Organization",
        "enterprise",
    )

    return {
        "id": org_id,
        "slug": "test-org",
        "name": "Test Organization",
    }


@pytest_asyncio.fixture
async def test_api_key(db_conn, test_org: dict) -> str:
    """Create test API key."""
    import hashlib
    import uuid

    key_id = str(uuid.uuid4())
    raw_key = f"omni_test_{uuid.uuid4().hex}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await db_conn.execute(
        """
        INSERT INTO api_keys (id, org_id, key_hash, key_prefix, name, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        uuid.UUID(key_id),
        uuid.UUID(test_org["id"]),
        key_hash,
        raw_key[:12],
        "Integration Test Key",
        "admin",
    )

    return raw_key
