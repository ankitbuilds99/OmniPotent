"""Integration tests for API endpoints with real database."""

from __future__ import annotations

import hashlib

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestHealthEndpoints:
    """Test health check endpoints."""

    async def test_healthz(self, client: AsyncClient):
        """Health check should return 200."""
        response = await client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "ok"]

    async def test_readyz(self, client: AsyncClient):
        """Readiness check should return 200 when services are ready."""
        response = await client.get("/readyz")
        assert response.status_code == 200

    async def test_slo_metrics_are_scrapeable_without_a_token(self, client: AsyncClient):
        """`/metrics/slo` is in `_OPEN_PATHS` so Prometheus can scrape it.

        deploy/prometheus/prometheus.yml points at this path for exactly that
        reason; if it stopped being open the scrape would start returning 401
        and the dashboards would quietly go flat.
        """
        response = await client.get("/metrics/slo")
        assert response.status_code == 200, response.text

    async def test_general_metrics_requires_a_token(self, client: AsyncClient, api_token: str):
        """`/metrics` is deliberately *not* open -- only the SLO subset is."""
        assert (await client.get("/metrics")).status_code in (401, 403)
        authed = await client.get("/metrics", headers={"Authorization": f"Bearer {api_token}"})
        assert authed.status_code == 200, authed.text


class TestInvestigationAPI:
    """Integration tests for investigation endpoints."""

    # These three posted to `/api/v1/investigations`. There is no `/api/v1`
    # prefix in this app -- the real routes are `GET /api/investigations` and
    # `POST /api/coordinator/investigations` -- so every request fell through to
    # the SPA catch-all, which serves GET only and therefore answered 405. Each
    # assertion listed 404 as acceptable ("endpoint not implemented"), so two of
    # the three would have passed against an app with no investigation API at
    # all. The point of an integration test is to catch exactly that.

    async def test_list_investigations_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/investigations")
        assert response.status_code in (401, 403), response.text

    async def test_list_investigations_authorized(self, client: AsyncClient, api_token: str):
        response = await client.get(
            "/api/investigations",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert response.status_code == 200, response.text
        assert isinstance(response.json()["investigations"], list)

    async def test_create_investigation_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/api/coordinator/investigations",
            json={"incident_id": "INC-1", "affected_services": ["checkout-api"]},
        )
        # Auth is checked before the body is validated, so this must not be 422.
        assert response.status_code in (401, 403), response.text

    async def test_an_unknown_key_is_not_accepted(self, client: AsyncClient, test_api_key: str):
        """A key row in Postgres is not by itself a credential.

        `test_api_key` inserts into the `api_keys` table, but `resolve()` reads
        the tenancy store (aiosqlite, `dcp_<prefix>_<secret>`), which is a
        different store with a different key format. Asserting the rejection
        keeps that documented rather than leaving a fixture that looks like it
        mints working credentials.
        """
        response = await client.get(
            "/api/investigations",
            headers={"Authorization": f"Bearer {test_api_key}"},
        )
        assert response.status_code in (401, 403), response.text


class TestWebhookAPI:
    """Integration tests for webhook endpoints."""

    async def test_pagerduty_webhook_rejects_an_unsigned_delivery(self, client: AsyncClient):
        """An unverified delivery must not open an incident."""
        response = await client.post(
            "/webhooks/pagerduty",
            json={"event": {"event_type": "incident.triggered", "data": {"id": "P1"}}},
            headers={"X-PagerDuty-Signature": "v1=not_the_right_digest"},
        )
        assert response.status_code == 401

    async def test_pagerduty_webhook(self, client: AsyncClient, pagerduty_secret: str):
        """A signed PagerDuty v3 delivery should be accepted.

        This posted to `/api/v1/webhooks/pagerduty` — there is no `/api/v1`
        prefix anywhere in the app, so it hit the SPA catch-all and got 405.
        The assertion allowed 404, i.e. it would have passed against a route
        that did not exist; the whole point of the test is that one does. It
        also sent a fixed string as the signature, which the handler correctly
        refuses, so it could only ever have exercised the reject path.
        """
        import hashlib
        import hmac
        import json

        body = json.dumps(
            {
                "event": {
                    "event_type": "incident.triggered",
                    "data": {
                        "id": "P123456",
                        "title": "Integration Test Incident",
                        "service": {"summary": "test-service"},
                        "html_url": "https://example.com",
                    },
                },
            }
        ).encode()
        digest = hmac.new(pagerduty_secret.encode(), body, hashlib.sha256).hexdigest()

        response = await client.post(
            "/webhooks/pagerduty",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-PagerDuty-Signature": f"v1={digest}",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] != "error"


class TestDatabaseOperations:
    """Test database operations."""

    async def test_organization_exists(self, db_conn, test_org: dict):
        """Verify test organization was created."""
        try:
            result = await db_conn.fetchrow(
                "SELECT * FROM organizations WHERE slug = $1",
                test_org["slug"],
            )
            assert result is not None
            assert result["name"] == test_org["name"]
        except Exception as e:
            if "does not exist" in str(e):
                pytest.skip("organizations table does not exist")
            raise

    async def test_api_key_exists(self, db_conn, test_api_key: str):
        """Verify test API key was created."""
        try:
            result = await db_conn.fetchrow(
                "SELECT * FROM api_keys WHERE key_prefix = $1",
                test_api_key[:12],
            )
            assert result is not None
        except Exception as e:
            if "does not exist" in str(e):
                pytest.skip("api_keys table does not exist")
            raise


class TestRedisOperations:
    """Test Redis operations."""

    async def test_redis_connection(self, redis_client):
        """Verify Redis connection works."""
        await redis_client.set("test_key", "test_value")
        value = await redis_client.get("test_key")
        assert value == b"test_value"

    async def test_redis_caching(self, redis_client):
        """Test cache operations."""
        import json

        cache_key = "integration:test:cache"
        cache_data = {"foo": "bar", "count": 42}

        await redis_client.setex(
            cache_key,
            60,  # 60 second TTL
            json.dumps(cache_data),
        )

        raw = await redis_client.get(cache_key)
        retrieved = json.loads(raw)
        assert retrieved == cache_data


class TestConcurrentOperations:
    """Test concurrent database/API operations."""

    async def test_concurrent_health_checks(self, client: AsyncClient):
        """Multiple concurrent health checks should succeed."""
        import asyncio

        tasks = [client.get("/healthz") for _ in range(10)]
        responses = await asyncio.gather(*tasks)

        for response in responses:
            assert response.status_code == 200

    async def test_sequential_db_writes(self, db_conn, test_org: dict):
        """Sequential database writes should work correctly.

        This inserted `action`/`details` into `audit_logs`, which has
        `event_type`/`data` plus the `prev_hash`/`entry_hash` chain -- those are
        `AuditLogDB`'s columns, from `app/database/team_models.py`, a module
        that cannot be imported and that no migration backs. Postgres answered
        `column "action" ... does not exist`, and the blanket
        `if "does not exist" in str(e): skip` caught it. A test that skips on
        any schema mismatch can never fail, which is why this survived.

        The writes are scoped to an actor unique to this run. `test_org` reuses
        the `test-org` row when it already exists and `audit_logs` is append-only,
        so selecting on `org_id` alone returned every earlier run's rows too and
        the test failed the second time it was ever run against a given database.
        """
        import uuid

        org_id = uuid.UUID(test_org["id"])
        actor = f"integration_test:{uuid.uuid4()}"
        prev = "0" * 64
        for i in range(3):
            entry = hashlib.sha256(f"{prev}:test_action_{i}".encode()).hexdigest()
            await db_conn.execute(
                """
                INSERT INTO audit_logs (org_id, actor, event_type, data, prev_hash, entry_hash)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                org_id,
                actor,
                f"test_action_{i}",
                "{}",
                prev,
                entry,
            )
            prev = entry

        rows = await db_conn.fetch(
            "SELECT event_type, prev_hash, entry_hash FROM audit_logs "
            "WHERE org_id = $1 AND actor = $2 ORDER BY id",
            org_id,
            actor,
        )
        assert [r["event_type"] for r in rows] == [f"test_action_{i}" for i in range(3)]
        # The chain is what makes the log tamper-evident: each entry commits to
        # the one before it.
        assert all(rows[i]["prev_hash"] == rows[i - 1]["entry_hash"] for i in range(1, 3))
