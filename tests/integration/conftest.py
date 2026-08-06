"""Integration test fixtures with real database and Redis."""

from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for session-scoped async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    """Get database URL from environment."""
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://omnipotent:test_password@localhost:5432/omnipotent_test",
    )


@pytest.fixture(scope="session")
def redis_url() -> str:
    """Get Redis URL from environment."""
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create test client for API testing."""
    from app.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Get authentication headers for API requests."""
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def sample_incident() -> dict:
    """Sample incident data for testing."""
    return {
        "title": "Test Incident",
        "description": "Integration test incident",
        "severity": "medium",
        "service": "test-service",
        "source": "integration-test",
    }


@pytest.fixture
def sample_investigation() -> dict:
    """Sample investigation data for testing."""
    return {
        "incident_id": "inc-test-001",
        "service": "test-service",
        "query": "What caused the error?",
    }
