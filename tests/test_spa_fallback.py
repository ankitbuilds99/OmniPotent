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


# The two tests above assert the symptom. These assert the mechanism, because
# the symptom came back on a dependency bump with nothing else changing.


def test_the_api_segment_list_sees_routes_behind_include_router() -> None:
    """`oncall` and `billing` only exist behind `include_router`.

    FastAPI 0.137 stopped flattening included routers into `app.routes`, leaving
    one `_IncludedRouter` per call — an object with no `.path`. The segment list
    read `.path` off the top level only, so it went from covering the whole API
    to covering the 85 routes declared inline, and `/oncall/nope` started
    answering `200 text/html`. Nothing failed loudly; two parametrized cases
    above are the only thing that noticed.
    """
    from app.api import main

    served = set(main._served_paths(main.app.routes))
    # Inline routes are found by the easy path; these are the ones that need
    # the recursion, one per include style: no prefix, prefix="/api", and the
    # WebSocket router that `effective_candidates()` cannot see at all.
    assert "/oncall/schedules" in served
    assert "/api/teams" in served
    assert "/ws/chat/{thread_id}" in served
    assert len(served) > 300, len(served)

    for segment in ("oncall", "billing", "api", "ws", "auth"):
        assert segment in main._API_SEGMENTS, segment


def test_a_write_to_a_path_that_does_not_exist_is_404_not_405() -> None:
    """StaticFiles rejects non-GET verbs before it looks for the file.

    That made the catch-all mount claim every mistyped path existed. The
    GraphQL status probe distinguishes "not mounted" from "mounted" by checking
    for 404, so an unmounted schema read as reachable.
    """
    from app.api.main import app as fresh_app

    with TestClient(fresh_app) as c:
        assert c.post("/api/no-such-endpoint-at-all").status_code == 404
        assert c.put("/nowhere-at-all").status_code == 404
        # A file that is really there keeps 405: the resource exists, the verb
        # is what is wrong, and saying 404 would be the same lie in reverse.
        assert c.post("/index.html").status_code == 405
        # And so does a path a real route owns. The mount sits at "/" and so
        # matches everything, which means a wrong-verb call to a real endpoint
        # never reaches that endpoint to collect its own 405 — it arrives here.
        assert c.post("/healthz").status_code == 405
