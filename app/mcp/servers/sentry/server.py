"""Sentry MCP server — application error tracking as an investigable source.

Sentry pinpoints the exact exception, the offending stack frame, and the release
that introduced a regression — the bridge from a symptom (5xx rate) to the precise
line of code and the change that caused it. That feeds both the GitHub fix loop and
deploy correlation.

When `SENTRY_API_TOKEN` is set it queries the real Sentry API (scoped by
`SENTRY_ORG`/`SENTRY_PROJECT`); otherwise it runs in OFFLINE DEMO mode with
fixtures tying into the bundled checkout-svc incident — the same live/offline
pattern as the other connectors.

Live API (per https://docs.sentry.io/api/):
  - GET https://sentry.io/api/0/projects/{org}/{project}/issues/?query=...
  - GET https://sentry.io/api/0/issues/{issue_id}/
  - GET https://sentry.io/api/0/issues/{issue_id}/events/latest/
  - auth: Authorization: Bearer <token>

Tools: list_issues, get_issue, get_latest_event.

Run standalone:
    SENTRY_API_TOKEN=... SENTRY_ORG=... SENTRY_PROJECT=... python -m app.mcp.servers.sentry.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

SENTRY_API_TOKEN = os.environ.get("SENTRY_API_TOKEN", "").strip()
SENTRY_ORG = os.environ.get("SENTRY_ORG", "").strip()
SENTRY_PROJECT = os.environ.get("SENTRY_PROJECT", "").strip()
SENTRY_BASE = os.environ.get("SENTRY_BASE_URL", "https://sentry.io").strip() or "https://sentry.io"
OFFLINE = not SENTRY_API_TOKEN

mcp = FastMCP("sentry")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _headers() -> dict:
    return {"Authorization": f"Bearer {SENTRY_API_TOKEN}"}


def _demo(payload):
    """Label fixture data so the agent can't mistake it for a live sentry read.

    The write tools already say "(simulated)"; the read tools returned fixtures
    shaped exactly like real API responses, so an investigation built on invented
    data looked identical to one built on a live system.
    """
    from app.mcp.fixtures import mark_demo
    return mark_demo(payload, "sentry")


def _gate() -> dict | None:
    """Fail closed when credentials are absent outside demo mode: fixtures are a
    demo feature, not a production fallback (app/config.py's contract)."""
    from app.mcp.fixtures import offline_gate
    return offline_gate("Sentry", "SENTRY_API_TOKEN")


# --- Offline fixtures (multi-scenario support) ----------------------------- #
def _get_issues() -> list:
    from app.mcp.fixtures import get_fixture
    return get_fixture("sentry", "issues") or []


def _get_latest_event() -> dict | None:
    from app.mcp.fixtures import get_fixture
    return get_fixture("sentry", "latest_event")


@mcp.tool()
def list_issues(query: str = "is:unresolved", limit: int | str = 10) -> list[dict]:
    """List Sentry issues (most impactful first) with title, culprit, level, event
    & user counts, release, and whether it's a regression.

    Args:
        query: Sentry search (e.g. "is:unresolved", "level:error").
        limit: max issues to return.
    """
    limit = _as_int(limit, 10)
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        items = _get_issues()
        if "level:error" in query:
            items = [i for i in items if i.get("level") == "error"]
        return _demo(items[:limit])

    import httpx

    resp = httpx.get(
        f"{SENTRY_BASE}/api/0/projects/{SENTRY_ORG}/{SENTRY_PROJECT}/issues/",
        headers=_headers(), params={"query": query, "limit": min(limit, 100)}, timeout=20,
    )
    resp.raise_for_status()
    out = []
    for it in resp.json():
        out.append({
            "id": it.get("id"),
            "title": it.get("title"),
            "culprit": it.get("culprit"),
            "level": it.get("level"),
            "status": it.get("status"),
            "count": _as_int(it.get("count"), 0),
            "user_count": it.get("userCount"),
            "first_seen": it.get("firstSeen"),
            "last_seen": it.get("lastSeen"),
        })
    return out[:limit]


@mcp.tool()
def get_issue(issue_id: str) -> dict:
    """Return one issue's details by id."""
    if OFFLINE:
        err = _gate()
        if err:
            return err
        issues = _get_issues()
        found = next((i for i in issues if i.get("id") == issue_id), None)
        if not found:
            return {"error": f"unknown issue '{issue_id}' (offline demo has "
                             f"{[i.get('id') for i in issues]})"}
        return _demo(found)

    import httpx

    resp = httpx.get(f"{SENTRY_BASE}/api/0/issues/{issue_id}/", headers=_headers(), timeout=20)
    resp.raise_for_status()
    it = resp.json()
    return {
        "id": it.get("id"), "title": it.get("title"), "culprit": it.get("culprit"),
        "level": it.get("level"), "status": it.get("status"),
        "count": _as_int(it.get("count"), 0), "user_count": it.get("userCount"),
        "first_seen": it.get("firstSeen"), "last_seen": it.get("lastSeen"),
    }


@mcp.tool()
def get_latest_event(issue_id: str) -> dict:
    """Return the latest event for an issue — the stack trace (file/function/line),
    release, environment, and tags. This is the exact code location to fix."""
    if OFFLINE:
        err = _gate()
        if err:
            return err
        event = _get_latest_event()
        if not event:
            return {"error": "no offline event available for this scenario"}
        if issue_id not in ("", event.get("issue_id", "")):
            return {"error": f"no offline event for issue '{issue_id}' "
                    f"(demo has {event.get('issue_id')})"}
        return _demo(dict(event))

    import httpx

    resp = httpx.get(
        f"{SENTRY_BASE}/api/0/issues/{issue_id}/events/latest/", headers=_headers(), timeout=20
    )
    resp.raise_for_status()
    ev = resp.json()
    frames = []
    for entry in ev.get("entries", []):
        if entry.get("type") == "exception":
            for val in entry.get("data", {}).get("values", []):
                for fr in (val.get("stacktrace") or {}).get("frames", []):
                    frames.append({"filename": fr.get("filename"),
                                   "function": fr.get("function"), "lineno": fr.get("lineNo")})
    return {
        "issue_id": issue_id, "event_id": ev.get("eventID"),
        "release": ev.get("release"), "environment": ev.get("environment"),
        "message": ev.get("message") or ev.get("title"),
        "stacktrace": frames[-10:], "tags": {t.get("key"): t.get("value") for t in ev.get("tags", [])},
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
