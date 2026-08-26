"""The static mount must serve the SPA for client-side routes and only those.

Two failure modes this guards against, both of which shipped:

  * `StaticFiles(html=True)` alone serves index.html for *directory* paths only,
    so /dashboard, /login and /settings/billing 404'd on reload or a shared
    link — including the URL Stripe sends users back to after checkout.
  * A naive catch-all fixes that but makes every mistyped API path answer
    `200 text/html`, so a client that only checks the status code reads a typo
    as success.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not (ROOT / "frontend" / "dist" / "index.html").is_file(),
    reason="frontend is not built; the SPA mount is skipped entirely",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# Every path App.tsx routes, plus an unknown one — react-router redirects that
# one home, which it can only do if it is served in the first place.
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/login",
        "/auth/callback",
        "/onboarding",
        "/console",
        "/dashboard",
        "/settings/billing",
        "/profile",
        "/history",
        "/status",
        "/team",
        "/incidents",
        "/on-call",
        "/service-map",
        "/notifications",
        "/postmortem",
        "/analytics",
        "/slo",
        "/runbooks",
        "/marketplace",
        "/knowledge",
        "/api-docs",
        "/some-page-that-does-not-exist",
    ],
)
def test_client_routes_serve_the_app(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200, path
    assert response.headers["content-type"].startswith("text/html"), path


@pytest.mark.parametrize(
    "path",
    [
        "/api/nonexistent",
        "/oncall/nope",
        "/billing/nope",
        # `auth` is an API segment, so an unknown path under it must 404 even
        # though /auth/callback right next to it is a client-side route.
        "/auth/nope",
        # A missing asset must not receive an HTML body: the browser would
        # report a MIME-type error instead of the real 404.
        "/assets/nope.js",
    ],
)
def test_unknown_api_paths_still_404(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404, path
    assert "text/html" not in response.headers.get("content-type", ""), path
