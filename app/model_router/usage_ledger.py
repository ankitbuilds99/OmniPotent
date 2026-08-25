"""The bridge between the LLM calls the graph makes and the spend ledger.

`app/graph/nodes.py:_log_usage` read `usage_metadata` off every model response,
wrote one `log.info` line and threw the numbers away. Nothing in the product
ever called `CostOptimizer.record_usage`, so the only rows the cost analytics
ever had were the ones a client POSTed by hand: the dashboard was reporting on
a scratchpad, not on what the product had spent.

Nodes are synchronous and LangGraph runs them in a worker thread, so they
cannot await a database write. `note()` prices the call and parks it on a
bounded in-memory queue; `flush()` writes whatever has accumulated from the
request's own task at the end of a turn, next to the existing metering. The
org is captured in `note()` — see `PendingUsage` — so a flush never misfiles
one tenant's spend under another.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.database.orgs import org_uuid
from app.model_router.cost_optimizer import CostOptimizer
from app.model_router.usage_store import PendingUsage

log = logging.getLogger("omnipotent.model_router.ledger")

# A turn makes a handful of calls; this is ~1000 unflushed investigations. The
# bound exists so a deployment with no database (or a long-running CLI session,
# which never flushes) cannot grow this without limit.
MAX_PENDING = 10_000

_pending: deque[PendingUsage] = deque(maxlen=MAX_PENDING)
_lock = threading.Lock()

# List prices only — no per-tenant state, so one instance is correct here even
# though the API's optimizers are per-org.
_pricer = CostOptimizer()


def model_id_of(resp: Any) -> str:
    """The model that produced `resp`, as the provider named it.

    Providers put it in `response_metadata` under different keys. An empty
    string is left as "unknown" rather than guessed at: `CostOptimizer` prices
    an unknown model at the most expensive known rate and says so in the logs,
    which is the honest failure, whereas guessing a model id would file real
    spend under a model that was never called.
    """
    meta = getattr(resp, "response_metadata", None) or {}
    if not isinstance(meta, dict):
        return "unknown"
    for key in ("model_name", "model", "model_id"):
        value = meta.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def note(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    *,
    request_type: str = "inference",
    customer_id: str | None = None,
) -> PendingUsage:
    """Price one completed LLM call and queue it for persistence.

    Safe to call from a graph worker thread — the queue is lock-guarded and
    nothing here touches the event loop. The caller (`_log_usage`) swallows
    failures, because a bookkeeping problem must not fail the investigation it
    is bookkeeping for.
    """
    pending = PendingUsage(
        org=org_uuid(),
        customer_id=customer_id or "-",
        model_id=model_id or "unknown",
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cost=_pricer.price(model_id or "unknown", input_tokens or 0, output_tokens or 0),
        request_type=request_type,
        at=datetime.now(timezone.utc),
    )
    with _lock:
        if len(_pending) == MAX_PENDING:
            dropped = _pending[0]
            log.warning(
                "LLM usage queue is full (%d unflushed calls); dropping the oldest "
                "record (model=%s cost=%.6f). Spend is being recorded faster than "
                "it can be written to the database.",
                MAX_PENDING,
                dropped.model_id,
                dropped.cost,
            )
        _pending.append(pending)
    return pending


def drain() -> list[PendingUsage]:
    """Take everything queued so far."""
    with _lock:
        taken = list(_pending)
        _pending.clear()
    return taken


def pending_count() -> int:
    with _lock:
        return len(_pending)


def reset() -> None:
    """Forget everything queued (tests, and shutdown after a failed flush)."""
    with _lock:
        _pending.clear()


async def flush() -> int:
    """Write queued spend to `llm_usage_records`. Returns rows written.

    Best-effort, like the rest of the end-of-turn bookkeeping: an investigation
    that ran and answered is not turned into an error because its cost row
    could not be written. A failed batch is logged with its size and dollar
    total rather than re-queued — a batch that fails deterministically would
    otherwise block every later flush behind it.
    """
    from app.model_router import usage_store

    batch = drain()
    if not batch:
        return 0
    try:
        return await usage_store.write(batch)
    except Exception:  # noqa: BLE001 — spend accounting must not fail a turn
        log.exception(
            "could not persist %d LLM usage record(s) totalling $%.6f",
            len(batch),
            sum(r.cost for r in batch),
        )
        return 0
