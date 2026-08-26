"""Audit trail for security-relevant actions.

Emits structured, append-only audit events (who/what/when) on a dedicated
`omnipotent.audit` logger AND keeps them queryable: a Postgres table backs a
read API (GET /audit), and — when COPILOT_AUDIT_LOG is set — every event is also
appended to a JSONL file for durable, tamper-evident retention. Records carry
the request-id (via observability) so an action can be correlated end-to-end.
Exposing a queryable audit read API is the commonly-forgotten half of the SOC2 /
compliance requirement, so it's built in here.

The events used to live in a `deque(maxlen=2000)` and the chain in a
module-level `_LAST_HASH`. That combination was wrong in both directions:

*   Past 2000 events, on a single pod, GET /audit/verify reported
    `{"valid": false, "broken_at": 0}` forever. The deque had evicted the
    genesis entry, so the oldest survivor's `prev_hash` no longer matched the
    empty string verification starts from. A tamper-evidence control that cries
    wolf permanently is worse than none: the first real detection reads as the
    same known false alarm.
*   Below 2000 events, on more than one replica, it reported `valid: true`
    about one pod's fraction of the trail, because each replica held its own
    buffer and its own running hash.

`record()` is deliberately still synchronous. Of its 27 call sites, five sit in
sync functions (`sanitize_query`, three `_audit_log` helpers, `record_feedback`)
that would each have to become async and drag their own callers with them, and
a synchronous database write from the other 22 would block the event loop. So
recording stays a fast, non-blocking append to `_PENDING`, and a drain task
started by the API lifespan writes to the chain.

That leaves one honest gap: events sitting in `_PENDING` when a process is
killed are lost. `_PENDING` is a hand-off queue, not the store -- it is not the
in-memory audit trail this module used to keep, and `flush()` empties it on
shutdown -- but a crash between record and drain loses those events. Making that
window zero needs an await at the call site, which is the change the five sync
callers block.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from typing import Any

from app import observability, tenant_context

_audit = logging.getLogger("omnipotent.audit")

# Events recorded but not yet written to the chain. A hand-off queue between the
# synchronous callers and the async drain, NOT a store: it is drained on a timer
# and on shutdown, and nothing reads from it. Bounded so a drain that has stopped
# (no loop running, database down) costs fixed memory rather than the process.
_PENDING: deque[dict] = deque(maxlen=10000)

# Optional durable sink — one JSON object per line.
_LOG_PATH = os.environ.get("COPILOT_AUDIT_LOG", "").strip()

# How often the drain task writes whatever has accumulated.
_DRAIN_INTERVAL_SECONDS = 1.0

_drain_task: asyncio.Task[None] | None = None
_dropped = 0


def _canonical(entry: dict) -> str:
    """Stable serialization of an entry EXCLUDING the chain fields, for hashing."""
    body = {k: v for k, v in entry.items() if k not in ("hash", "prev_hash")}
    return json.dumps(body, default=str, sort_keys=True)


def chain_hash(entry: dict, prev_hash: str) -> str:
    """Fold an entry and its predecessor's hash into this entry's hash.

    Public because the chain position is assigned at insert time now, so
    `app.audit.store` -- which is the only thing that knows an entry's real
    predecessor -- computes this rather than the caller.
    """
    return hashlib.sha256((_canonical(entry) + prev_hash).encode()).hexdigest()


# Retained under the old private name: this module is re-exported through
# app/audit/__init__.py and the name appears in tests.
_chain_hash = chain_hash


def record(event: str, **fields) -> None:
    """Record one audit event, e.g. record("approval.decided", thread="t", approved=True).
    Each entry is hash-chained to the previous, making the trail tamper-evident.

    Returns as soon as the event is queued. The chain position and hash are
    assigned when it is written, because only the writer knows which entry it
    actually follows -- with several replicas recording at once, that cannot be
    decided here.
    """
    global _dropped
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "request_id": observability.request_id_var.get(),
        # Tenant-stamped from day one ('-' when single-tenant) so every audit
        # line is attributable to an org + actor in a multi-tenant deployment.
        "org_id": tenant_context.tenant_id(),
        "actor": tenant_context.get_actor(),
        **fields,
    }
    _audit.info("audit event=%s %s", event, json.dumps(fields, default=str, sort_keys=True))

    if len(_PENDING) == _PENDING.maxlen:
        # The append below would silently evict the oldest. Say so: a dropped
        # audit event is exactly the thing that must not disappear quietly.
        _dropped += 1
        _audit.error(
            "audit queue full (%d); dropping oldest event to record %s (%d dropped so far)",
            _PENDING.maxlen,
            event,
            _dropped,
        )
    _PENDING.append(entry)


def dropped_count() -> int:
    """How many events the queue has evicted undrained since process start."""
    return _dropped


async def flush() -> int:
    """Write everything queued to the chain. Returns the number written.

    Called on shutdown and by the drain loop. Entries are taken off the queue
    before the write and put back on failure, so a database blip retries rather
    than loses them.
    """
    from app.audit import store

    if not _PENDING:
        return 0

    batch = list(_PENDING)
    _PENDING.clear()
    try:
        written = await store.append(batch)
    except store.ContendedAppend as exc:
        # Some landed, some could not claim a chain position. Requeue only the
        # remainder: the ones that landed are in the chain already, and writing
        # them a second time would duplicate them. Requeueing the whole batch
        # here -- or treating a short result as success, which is what this did
        # before -- is how audit events went missing without anything saying so.
        written = exc.written
        _requeue(exc.unwritten)
        _audit.warning(
            "%d audit event(s) could not claim a chain position; requeued for "
            "the next drain",
            len(exc.unwritten),
        )
    except Exception:
        # Put them back at the front, preserving order, and let the next drain
        # retry. Losing audit events to a transient database error would be a
        # worse failure than the one this module was rewritten to fix.
        _requeue(batch)
        _audit.exception("could not write %d audit events; will retry", len(batch))
        return 0

    if _LOG_PATH and written:
        _append_jsonl(written)
    return len(written)


def _requeue(entries: list[dict[str, Any]]) -> None:
    """Put entries back at the front of the queue, preserving their order.

    The queue is bounded, so a requeue that does not fit evicts from the far
    end. Those losses go through the same counter as an overflow on the way in:
    the queue is allowed to shed under sustained backpressure, but never
    quietly.
    """
    global _dropped

    capacity = _PENDING.maxlen
    if capacity is not None:
        overflow = len(entries) + len(_PENDING) - capacity
        if overflow > 0:
            _dropped += overflow
            _audit.error(
                "audit queue full while requeueing; dropped %d event(s) "
                "(%d total)",
                overflow,
                _dropped,
            )
    _PENDING.extendleft(reversed(entries))


def _append_jsonl(entries: list[dict[str, Any]]) -> None:
    """Append written entries to the optional durable sink.

    Written after the chain rather than in `record()`, because the hash is only
    known once the entry's predecessor is. Point COPILOT_AUDIT_LOG at a
    WORM/object-lock volume for an immutable trail.
    """
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        _audit.warning("could not append to audit log %s", _LOG_PATH)


async def _drain_loop() -> None:
    while True:
        await asyncio.sleep(_DRAIN_INTERVAL_SECONDS)
        try:
            await flush()
        except asyncio.CancelledError:
            raise
        except Exception:
            _audit.exception("audit drain failed; continuing")


def start_drain() -> None:
    """Start the background drain. Idempotent; called from the API lifespan."""
    global _drain_task
    if _drain_task is not None and not _drain_task.done():
        return
    _drain_task = asyncio.create_task(_drain_loop())


async def stop_drain() -> None:
    """Stop the drain and write whatever is left. Called on shutdown."""
    global _drain_task
    task, _drain_task = _drain_task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    await flush()


def verify_chain(events: list[dict], org_id: str | None = None) -> dict:
    """Recompute the hash chain (oldest→newest) over an explicit list of events
    and report the first broken link — the artifact auditors ask for.

    `events` has no default. It briefly defaulted to None-means-empty, which
    made a caller that forgot to read the chain get `{"valid": true, "count": 0}`
    — a tamper-evidence control answering "all clear" about nothing it looked
    at. Use `verify_org_chain` to check what is stored.

    `org_id` scopes the *report* to one tenant. Each entry's hash folds in the
    entry before it, whoever produced it, so the walk still has to cover every
    entry in the list — filtering first would report a false "tampered" the
    moment two orgs' entries interleave. Only the answer is scoped: `count` is
    the caller's own events, and a broken link is named only when the entry that
    broke it is theirs. Without this, a verify over a mixed list returned the
    fleet-wide event count and could surface another tenant's event name. The
    stored chains are already per-org (see `app.audit.store`), so this is
    defence in depth for any caller that hands in a list of its own.
    """

    def mine(entry: dict) -> bool:
        return org_id is None or entry.get("org_id", "-") == org_id

    total = sum(1 for e in events if mine(e))
    prev = ""
    seen = 0
    for e in events:
        expected = chain_hash(e, prev)
        if e.get("hash") != expected or e.get("prev_hash") != prev:
            theirs = mine(e)
            return {"valid": False, "checked": seen,
                    "broken_at": seen if theirs else None,
                    "event": e.get("event") if theirs else None,
                    "count": total}
        if mine(e):
            seen += 1
        prev = e["hash"]
    return {"valid": True, "count": total}


async def verify_org_chain(org_id: str) -> dict:
    """Verify the stored chain for one org, from its genesis entry.

    Unlike the buffer this replaces, this reads every event ever recorded for
    the org, so `valid: true` means the whole trail verifies rather than
    whatever a bounded window happened to hold.
    """
    from app.audit import store

    await flush()
    # `store.chain` already returns only this org's entries; passing `org_id`
    # through keeps the report scoped even if that ever stops being true.
    verdict = verify_chain(await store.chain(org_id), org_id=org_id)
    verdict["org_id"] = org_id
    return verdict


async def verify_all_chains() -> dict:
    """Verify every org's chain and summarise.

    Reported per org because the chains are per org: a single `valid` flag over
    a multi-tenant deployment would hide which tenant's trail is broken.
    """
    from app.audit import store

    await flush()
    results = [await verify_org_chain(org) for org in await store.orgs()]
    broken = [r for r in results if not r["valid"]]
    return {
        "valid": not broken,
        "count": sum(r["count"] for r in results),
        "orgs_checked": len(results),
        "broken_orgs": [r["org_id"] for r in broken],
        "chains": results,
    }


async def recent(
    limit: int = 100, event_prefix: str = "", org_id: str | None = None
) -> list[dict]:
    """Return the most recent audit events (newest first), optionally filtered by
    an event-name prefix (e.g. "approval", "security", "feedback").

    Scoped to one org. The buffer version was not scoped at all: it handed any
    authenticated tenant every other tenant's audit trail — actor email
    addresses, thread ids, approval decisions and security events. Pass `org_id`
    to read a specific org's trail; the default is the caller's, which in a
    single-tenant deployment is '-' for every entry and so changes nothing.
    """
    from app.audit import store

    await flush()
    org = tenant_context.tenant_id() if org_id is None else org_id
    return await store.recent(org, limit=limit, event_prefix=event_prefix)


async def clear() -> None:
    """Drop queued and stored events (used by tests)."""
    global _dropped
    from app.audit import store

    _PENDING.clear()
    _dropped = 0
    await store.clear()
