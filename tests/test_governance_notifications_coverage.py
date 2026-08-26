"""Governance, status-page and notification defects that made the product lie.

Four things were wrong, and each of them is the same shape: a number, a switch
or a promise the UI showed with confidence and nothing behind it.

1. The Notification Preferences page stored an org's channels, credentials,
   routing matrix, quiet hours and digest mode — and nothing read any of it.
   `channels_for_event` had exactly one caller in the whole codebase and it was
   a test. Creating an incident sent nothing anywhere.
2. The public status-page subscribe form accepted any string as a webhook URL
   and stored it as verified, `http://169.254.169.254/` included, and the server
   really did POST to it on the next incident. Delivery was also awaited inline,
   so one dead subscriber held the incident-creation request open for the
   webhook timeout plus its retry backoff.
3. `copilot_investigations_failed_total` could not move. Only successful
   terminal states were ever sampled, so the exposed error rate read a permanent
   0% while users got error responses, and the shipped CopilotHighFailureRate
   alert could not fire under any traffic.
4. The SLO Tracker page invented every number it showed: a hardcoded 43.2-minute
   budget for every SLO whatever its target or window, a "current" figure
   computed as `target - burn_rate * 0.1` (which can only ever land just under
   the target), and a fallback that manufactured SLOs out of the incident list
   when none were defined.

These tests pin the behaviours that make the replacements real.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine

PREFS = "/api/notifications/preferences"


# --------------------------------------------------------------------------- #
# A webhook receiver that records what it was sent.
# --------------------------------------------------------------------------- #
class _Collector(HTTPServer):
    received: list[dict]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"_raw": raw}
        self.server.received.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def collector():
    server = _Collector(("127.0.0.1", 0), _Handler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _url(server: _Collector, path: str = "/hook") -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/gov.db")
    # The collector runs on loopback, which is exactly what the SSRF guard
    # refuses. Allow-listing it here is what an on-prem operator would do, and
    # it keeps the tests exercising the real guard rather than a disabled one.
    monkeypatch.setenv("COPILOT_WEBHOOK_ALLOWED_HOSTS", "127.0.0.1")
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None

    with TestClient(api.app) as test_client:
        yield test_client

    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


def _route_webhook_only(client, url: str, event: str) -> None:
    """Point one event at the webhook channel and nothing else."""
    body = {
        "channels": [
            {"id": "webhook", "enabled": True, "config": {"url": url}},
            {"id": "email", "enabled": False},
            {"id": "slack", "enabled": False},
            {"id": "push", "enabled": False},
        ],
        "rules": [
            {
                "event": event,
                "channels": {
                    "webhook": True,
                    "email": False,
                    "slack": False,
                    "push": False,
                    "pagerduty": False,
                    "sms": False,
                    "teams": False,
                    "discord": False,
                    "jira": False,
                },
            }
        ],
    }
    resp = client.put(PREFS, json=body)
    assert resp.status_code == 200, resp.text


# =========================================================================== #
# 1. The preferences page has a consumer
# =========================================================================== #
def test_incident_creation_delivers_to_the_configured_channel(client, collector):
    """The defect in one line: an org configures a channel, routes an event to
    it, the event happens, and nothing is sent."""
    _route_webhook_only(client, _url(collector), "incident_created")

    resp = client.post(
        "/api/incidents",
        json={
            "title": "Checkout returning 500s",
            "description": "Error rate above 20% on the payments path.",
            "severity": "high",
            "service": "checkout",
        },
    )
    assert resp.status_code in (200, 201), resp.text

    assert collector.received, "the configured webhook received nothing"
    payload = collector.received[-1]["body"]
    assert payload["event"] == "incident_created"
    assert "Checkout returning 500s" in payload["title"]
    assert payload["data"]["service"] == "checkout"


def test_a_channel_switched_off_receives_nothing(client, collector):
    """The master switch outranks the routing matrix. Otherwise switching a
    channel off at the top of the page is decorative."""
    client.put(
        PREFS,
        json={
            "channels": [{"id": "webhook", "enabled": False, "config": {"url": _url(collector)}}],
            "rules": [{"event": "incident_created", "channels": {"webhook": True}}],
        },
    )

    client.post("/api/incidents", json={"title": "Quiet", "severity": "low", "service": "svc"})
    assert collector.received == []


def test_delivery_reports_the_missing_field_rather_than_dropping_the_channel(client):
    """An enabled, routed channel with no credentials is a target that fails
    with the field it is missing — not a channel that silently disappears.

    Dropping it would make "nobody configured this" and "nobody routed this"
    indistinguishable, and the mistake would stay invisible for exactly as long
    as nothing important happened.
    """
    from app.notifications.delivery import notify

    client.put(
        PREFS,
        json={
            "channels": [{"id": "slack", "enabled": True}],
            "rules": [{"event": "incident_created", "channels": {"slack": True}}],
        },
    )

    with TestClient(api.app):
        result = asyncio.run(notify("incident_created", "t", "b"))

    assert result["status"] == "failed"
    slack = [r for r in result["results"] if r["channel"] == "slack"]
    assert slack and slack[0]["delivered"] is False
    assert "webhook" in slack[0]["detail"]


def test_push_is_reported_unavailable_rather_than_configured(client):
    """This build has no endpoint through which a browser could register a
    device, so `send_push_notification` has nothing to send to. Reporting the
    channel as configured would be the untruth the page was written to remove.
    """
    prefs = client.get(PREFS).json()
    push = next(c for c in prefs["channels"] if c["id"] == "push")
    assert push["configured"] is False
    assert push["unavailable"], "the page has nothing to explain the switch with"


def test_events_nothing_can_raise_say_so(client):
    """Two of the eight rows on the routing matrix have no producer anywhere in
    the build. A switch that can never fire is the same lie as a Save button
    that saves nothing, only harder to notice."""
    rules = {r["event"]: r for r in client.get(PREFS).json()["rules"]}

    assert rules["deployment_started"]["unproduced"]
    assert rules["deployment_failed"]["unproduced"]
    # ...and the ones that were wired do not claim to be broken.
    for event in ("incident_created", "incident_resolved", "alert_triggered",
                  "oncall_changed", "escalation", "ai_action_pending"):
        assert rules[event]["unproduced"] == ""


def test_quiet_hours_suppress_non_critical_but_never_critical(client, collector):
    """A muted page at 3am is precisely when a SEV1 matters."""
    from app.notifications.delivery import notify

    _route_webhook_only(client, _url(collector), "incident_created")
    # A window covering the whole day bar one minute, so the test does not
    # depend on when it runs.
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=30)).strftime("%H:%M")
    end = (now + timedelta(minutes=30)).strftime("%H:%M")
    client.put(PREFS, json={"quiet_hours": {"enabled": True, "start": start, "end": end}})

    with TestClient(api.app):
        quiet = asyncio.run(notify("incident_created", "routine", "body"))
        assert quiet["status"] == "quiet_hours"
        assert collector.received == []

        loud = asyncio.run(notify("incident_created", "sev1", "body", critical=True))

    assert loud["status"] == "sent"
    assert len(collector.received) == 1


def test_a_wrapping_quiet_window_covers_the_night():
    """22:00–08:00 is only a window at all if "start after end" means overnight."""
    from app.notifications.delivery import in_quiet_hours

    window = {"enabled": True, "start": "22:00", "end": "08:00"}
    midnight = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    noon = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert in_quiet_hours(window, midnight) is True
    assert in_quiet_hours(window, noon) is False
    # Equal endpoints mean no window, not the whole day: the page offers no way
    # to say "mute everything", so reading it that way would silence an org that
    # set both fields the same by accident.
    assert in_quiet_hours({"enabled": True, "start": "09:00", "end": "09:00"}, noon) is False


def test_digest_holds_a_notification_and_the_flush_sends_one_message(client, collector):
    """"Batch non-critical notifications into periodic digests" was a select
    element nothing read. Ignoring it would have been a silent drop."""
    from app.notifications.delivery import flush_due_digests, notify

    _route_webhook_only(client, _url(collector), "incident_created")
    client.put(PREFS, json={"digest_mode": "hourly"})

    with TestClient(api.app):
        first = asyncio.run(notify("incident_created", "one", "first body"))
        second = asyncio.run(notify("incident_created", "two", "second body"))
        assert first["status"] == "digest"
        assert second["status"] == "digest"
        assert collector.received == [], "held notifications must not be sent yet"

        flushed = asyncio.run(flush_due_digests())

    assert len(collector.received) == 1, "a digest is one message, not one per entry"
    assert sum(r.get("sent", 0) for r in flushed) == 2
    body = collector.received[0]["body"]["body"]
    assert "one" in body and "two" in body


def test_a_critical_notification_is_never_held_for_a_digest(client, collector):
    from app.notifications.delivery import notify

    _route_webhook_only(client, _url(collector), "incident_created")
    client.put(PREFS, json={"digest_mode": "daily"})

    with TestClient(api.app):
        result = asyncio.run(notify("incident_created", "sev1", "b", critical=True))

    assert result["status"] == "sent"
    assert len(collector.received) == 1


def test_an_unknown_event_is_refused_rather_than_silently_dropped(client):
    from app.notifications.delivery import notify

    with TestClient(api.app):
        result = asyncio.run(notify("not_an_event", "t", "b"))
    assert result["status"] == "unknown_event"


# =========================================================================== #
# 2. Status-page webhooks: SSRF and inline delivery
# =========================================================================== #
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "http://127.0.0.1:8000/admin",                # loopback
        "http://10.0.0.5/internal",                   # private
        "http://[::ffff:169.254.169.254]/",           # the same metadata IP in IPv6
        "http://0.0.0.0/",                            # unspecified
        "file:///etc/passwd",                         # not even a fetchable scheme
        "not-a-url",                                  # not a URL at all
    ],
)
def test_the_public_subscribe_form_refuses_internal_webhook_targets(client, url, monkeypatch):
    """Anything at all used to be accepted and stored as verified, and the
    server really did POST to it on the next incident — a public,
    unauthenticated request-forgery primitive pointed at the metadata service.
    """
    # The allow-list the fixture sets for the collector must not be what saves
    # these; check the guard itself.
    monkeypatch.setenv("COPILOT_WEBHOOK_ALLOWED_HOSTS", "")
    cfg.get_settings.cache_clear()

    resp = client.post(
        "/api/status/subscribe",
        json={"subscription_type": "webhook", "webhook_url": url},
    )
    assert resp.status_code == 400, f"{url} was accepted: {resp.text}"


def test_the_guard_unwraps_ipv6_spellings_of_ipv4():
    """`::ffff:169.254.169.254` and `2002:a9fe:a9fe::` are the metadata address
    written as IPv6. `is_private` and friends are False for both, so a check
    that does not unwrap them lets the whole thing through."""
    from app.url_guard import BlockedURL, assert_public_url

    for spelling in ("http://[::ffff:169.254.169.254]/", "http://[2002:a9fe:a9fe::]/"):
        with pytest.raises(BlockedURL):
            asyncio.run(assert_public_url(spelling))


def test_a_host_that_does_not_resolve_is_accepted(monkeypatch):
    """Deliberate. Refusing would make acceptance depend on DNS being up and
    would break an air-gapped install; the re-check at delivery time is what
    closes the hole."""
    from app.url_guard import assert_public_url

    monkeypatch.setenv("COPILOT_WEBHOOK_ALLOWED_HOSTS", "")
    cfg.get_settings.cache_clear()
    # .invalid is reserved by RFC 2606 precisely so it never resolves.
    asyncio.run(assert_public_url("https://nothing.invalid/hook"))


def test_delivery_rechecks_the_target(monkeypatch):
    """The subscribe-time check can be beaten by a host that resolves publicly
    then rebinds. `_send_webhook` re-checks, so the window closes at send."""
    from app.status_page.notifications import StatusNotifier

    monkeypatch.setenv("COPILOT_WEBHOOK_ALLOWED_HOSTS", "")
    cfg.get_settings.cache_clear()

    notifier = StatusNotifier()
    sent = asyncio.run(
        notifier._send_webhook("http://169.254.169.254/", {"incident": "x"})
    )
    assert sent is False


def test_creating_an_incident_does_not_wait_for_a_dead_subscriber(client):
    """Delivery was awaited inline, so one unreachable subscriber held the
    request open for the webhook timeout plus its retry backoff — measured at
    ~11 seconds for a single dead URL, on the endpoint an operator reaches for
    mid-outage.

    A closed port on loopback is the fastest possible failure, so this asserts
    the structural property (the response does not carry delivery's latency)
    rather than a wall-clock number that would be flaky on a loaded machine.
    """
    import inspect

    from app.status_page import api_router

    source = inspect.getsource(api_router.create_incident)
    assert "background.add_task" in source
    assert "await notifier." not in source, "delivery is back on the request path"


# =========================================================================== #
# 3. SLO failure metrics
# =========================================================================== #
def _scrape(client) -> dict[str, float]:
    text = client.get("/metrics/slo").text
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, value = line.partition(" ")
        try:
            out[name] = float(value)
        except ValueError:
            continue
    return out


def test_a_failed_turn_moves_the_failure_counter(client):
    """`copilot_investigations_failed_total` was pinned at zero: only successful
    terminal states were ever sampled, so the exposed error rate read a
    permanent 0% while users were getting error responses back, and the
    CopilotHighFailureRate alert in deploy/observability/prometheus-alerts.yaml
    could not fire under any traffic.
    """
    before = _scrape(client)

    api._record_slo_failure(0.25)

    after = _scrape(client)
    assert after["copilot_investigations_failed_total"] == pytest.approx(
        before.get("copilot_investigations_failed_total", 0) + 1
    )
    assert after["copilot_investigations_total"] == pytest.approx(
        before.get("copilot_investigations_total", 0) + 1
    ), "the total must count the errored turn too, or the rate is still wrong"


def test_a_client_rejection_is_not_recorded_as_an_agent_failure(client):
    """An over-long message never starts an investigation. Counting it would
    make the agent's own error rate track user mistakes."""
    before = _scrape(client)

    resp = client.post("/chat", json={"thread_id": "t1", "message": "x" * 200_000})
    assert resp.status_code == 413

    after = _scrape(client)
    assert after.get("copilot_investigations_failed_total", 0) == pytest.approx(
        before.get("copilot_investigations_failed_total", 0)
    )


def test_every_turn_terminus_records_a_sample():
    """The five paths a turn can end on all have to sample, or the SLO is
    computed over whichever subset happened to be instrumented."""
    import inspect

    source = inspect.getsource(api)
    # /chat, /approve, /chat/stream, /approve/stream, and the triggered path.
    assert source.count("_record_slo_failure(") >= 5


# =========================================================================== #
# 4. SLO tracker: real numbers
# =========================================================================== #
def test_the_budget_endpoint_returns_the_sli_and_its_sample_counts(client):
    """The page could not show a "current" figure because the response did not
    carry one — so it computed `target - burn_rate * 0.1`, an expression that
    can only ever land just below the target however badly the service is doing.
    """
    created = client.post(
        "/api/slos",
        json={
            "name": "Checkout availability",
            "service": "checkout",
            "sli_type": "availability",
            "target": 0.999,
            "window": "rolling_30d",
        },
    )
    assert created.status_code in (200, 201), created.text
    slo_id = created.json()["id"]

    # 990 good out of 1000 is 99.0% against a 99.9% target: the budget is spent
    # ten times over, which is a case the old arithmetic could not represent.
    recorded = client.post(
        f"/api/slos/{slo_id}/sli",
        json={"good_count": 990, "total_count": 1000},
    )
    assert recorded.status_code in (200, 201), recorded.text

    budget = client.get(f"/api/slos/{slo_id}/budget")
    assert budget.status_code == 200, budget.text
    body = budget.json()

    assert body["current_sli"] == pytest.approx(0.99, abs=1e-6)
    assert body["good_count"] == 990
    assert body["total_count"] == 1000
    assert body["remaining"] == 0, "a 99.0% service against a 99.9% target has nothing left"


def test_budget_minutes_follow_the_slo_rather_than_a_constant(client):
    """43.2 minutes is the budget of a 99.9% monthly SLO and of nothing else.
    The page printed it for every SLO whatever its target or its window."""
    def _make(target: float, window: str) -> dict:
        resp = client.post(
            "/api/slos",
            json={
                "name": f"{target}-{window}",
                "service": "svc",
                "sli_type": "availability",
                "target": target,
                "window": window,
            },
        )
        assert resp.status_code in (200, 201), resp.text
        return client.get(f"/api/slos/{resp.json()['id']}/budget").json()

    monthly_three_nines = _make(0.999, "rolling_30d")
    monthly_two_nines = _make(0.99, "rolling_30d")
    weekly_three_nines = _make(0.999, "rolling_7d")

    # 30 days * 24 * 60 * 0.001
    assert monthly_three_nines["budget_minutes_total"] == pytest.approx(43.2, abs=0.05)
    # A looser target buys ten times the budget...
    assert monthly_two_nines["budget_minutes_total"] == pytest.approx(432.0, abs=0.5)
    # ...and a shorter window a fraction of it.
    assert weekly_three_nines["budget_minutes_total"] == pytest.approx(10.08, abs=0.05)


def test_an_slo_with_no_data_is_not_reported_as_healthy(client):
    """The page's fallback manufactured a healthy-looking SLO per service out of
    the incident list. A service with no SLO defined looked like a service
    meeting one."""
    created = client.post(
        "/api/slos",
        json={
            "name": "Fresh",
            "service": "svc",
            "sli_type": "availability",
            "target": 0.999,
            "window": "rolling_30d",
        },
    )
    body = client.get(f"/api/slos/{created.json()['id']}/budget").json()

    assert body["total_count"] == 0
    assert body["status"] == "no_data", "no measurements is its own state, not health"
