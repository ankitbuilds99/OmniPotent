"""Reads and writes for the persisted LLM spend ledger.

Everything the model-router API used to answer from `CostOptimizer`'s in-process
lists is answered from `llm_usage_records` / `llm_customer_budgets` here, so two
workers agree with each other and a restart does not zero the bill.

Money is scoped by `org_id`, which is taken from the authenticated request's
tenant context and never from a request body. `customer_id` is the org's own
label for a sub-customer and defaults to the org itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, NamedTuple
from uuid import UUID

from sqlalchemy import func, select

from app.database.orgs import ensure_org, org_uuid
from app.model_router.cost_optimizer import evaluate_budget
from app.model_router.models import DEFAULT_CUSTOMER, CustomerBudget, LLMUsageRecord

log = logging.getLogger("omnipotent.model_router.usage")


class PendingUsage(NamedTuple):
    """One priced call waiting to be written, captured where it happened.

    `org` is resolved at capture time rather than at write time: the graph runs
    LLM calls in worker threads and the flush happens later on the request's
    own task, so reading the tenant contextvar at the far end would attribute
    the spend to whoever happened to be flushing.
    """

    org: UUID
    customer_id: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost: float
    request_type: str
    at: datetime


def day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _customer(customer_id: str | None) -> str:
    return customer_id or DEFAULT_CUSTOMER


async def write(records: Iterable[PendingUsage]) -> int:
    """Persist priced calls. Returns how many rows were written."""
    from app.database.engine import get_session

    batch = list(records)
    if not batch:
        return 0

    async with get_session() as session:
        for org in {r.org for r in batch}:
            await ensure_org(session, org)
        session.add_all(
            [
                LLMUsageRecord(
                    org_id=r.org,
                    customer_id=r.customer_id,
                    model_id=r.model_id,
                    input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens,
                    cost=r.cost,
                    request_type=r.request_type,
                    created_at=r.at,
                )
                for r in batch
            ]
        )
        await session.commit()
    return len(batch)


async def record(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    *,
    customer_id: str | None = None,
    request_type: str = "inference",
    org: UUID | None = None,
) -> PendingUsage:
    """Persist one priced call for the current org."""
    pending = PendingUsage(
        org=org or org_uuid(),
        customer_id=_customer(customer_id),
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        request_type=request_type,
        at=datetime.now(timezone.utc),
    )
    await write([pending])
    return pending


def _scope(stmt: Any, org: UUID, customer_id: str | None, since: datetime) -> Any:
    stmt = stmt.where(
        LLMUsageRecord.org_id == org, LLMUsageRecord.created_at >= since
    )
    # No customer means "everything this org spent", which is what the
    # org-wide cap and the /stats page are about.
    if customer_id is not None:
        stmt = stmt.where(LLMUsageRecord.customer_id == _customer(customer_id))
    return stmt


async def usage_since(
    since: datetime,
    customer_id: str | None = None,
    *,
    period: str = "daily",
    org: UUID | None = None,
) -> dict[str, Any]:
    """Aggregate spend since `since`, grouped by model.

    Same shape `CostOptimizer._aggregate_usage` returned, so the endpoints and
    their clients did not have to change — only where the numbers come from.
    """
    from app.database.engine import get_session

    org = org or org_uuid()
    stmt = _scope(
        select(
            LLMUsageRecord.model_id,
            func.coalesce(func.sum(LLMUsageRecord.cost), 0.0),
            func.count(LLMUsageRecord.id),
            func.coalesce(func.sum(LLMUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(LLMUsageRecord.output_tokens), 0),
        ),
        org,
        customer_id,
        since,
    ).group_by(LLMUsageRecord.model_id)

    async with get_session() as session:
        rows = (await session.execute(stmt)).all()

    by_model: dict[str, dict[str, float | int]] = {
        row[0]: {"cost": float(row[1]), "requests": int(row[2])} for row in rows
    }
    return {
        "period": period,
        "total_cost": sum(float(row[1]) for row in rows),
        "total_requests": sum(int(row[2]) for row in rows),
        "total_input_tokens": sum(int(row[3]) for row in rows),
        "total_output_tokens": sum(int(row[4]) for row in rows),
        "by_model": by_model,
    }


async def daily_usage(
    customer_id: str | None = None, *, org: UUID | None = None
) -> dict[str, Any]:
    return await usage_since(day_start(), customer_id, period="daily", org=org)


async def monthly_usage(
    customer_id: str | None = None, *, org: UUID | None = None
) -> dict[str, Any]:
    return await usage_since(month_start(), customer_id, period="monthly", org=org)


async def _spent_since(
    session: Any, org: UUID, customer_id: str | None, since: datetime
) -> float:
    stmt = _scope(
        select(func.coalesce(func.sum(LLMUsageRecord.cost), 0.0)),
        org,
        customer_id,
        since,
    )
    return float((await session.execute(stmt)).scalar_one() or 0.0)


async def get_budget(
    customer_id: str | None = None, *, org: UUID | None = None
) -> dict[str, Any] | None:
    """The caps set for this customer, or None if none were ever set."""
    from app.database.engine import get_session

    org = org or org_uuid()
    async with get_session() as session:
        row = await _budget_row(session, org, customer_id)
        if row is None:
            return None
        return {
            "customer_id": row.customer_id,
            "daily_limit": row.daily_limit,
            "monthly_limit": row.monthly_limit,
        }


async def _budget_row(
    session: Any, org: UUID, customer_id: str | None
) -> CustomerBudget | None:
    stmt = select(CustomerBudget).where(
        CustomerBudget.org_id == org,
        CustomerBudget.customer_id == _customer(customer_id),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_budget(
    customer_id: str | None = None,
    *,
    daily_limit: float | None = None,
    monthly_limit: float | None = None,
    org: UUID | None = None,
) -> dict[str, Any]:
    """Set (or clear, by passing None) this customer's caps."""
    from app.database.engine import get_session

    org = org or org_uuid()
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        await ensure_org(session, org)
        row = await _budget_row(session, org, customer_id)
        if row is None:
            row = CustomerBudget(
                org_id=org,
                customer_id=_customer(customer_id),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        row.daily_limit = daily_limit
        row.monthly_limit = monthly_limit
        row.updated_at = now
        await session.commit()
        return {
            "customer_id": row.customer_id,
            "daily_limit": daily_limit,
            "monthly_limit": monthly_limit,
        }


async def check_budget(
    customer_id: str | None = None,
    additional_cost: float = 0.0,
    *,
    org: UUID | None = None,
) -> dict[str, Any]:
    """Would `additional_cost` more dollars fit under this customer's caps?

    A customer with no budget row has no cap and always proceeds — which is the
    honest answer, not a made-up limit.
    """
    from app.database.engine import get_session

    org = org or org_uuid()
    async with get_session() as session:
        row = await _budget_row(session, org, customer_id)
        daily_spent = await _spent_since(session, org, customer_id, day_start())
        monthly_spent = await _spent_since(session, org, customer_id, month_start())

    verdict = evaluate_budget(
        daily_spent=daily_spent,
        monthly_spent=monthly_spent,
        daily_limit=row.daily_limit if row else None,
        monthly_limit=row.monthly_limit if row else None,
        additional_cost=additional_cost,
    )
    return {
        "customer_id": _customer(customer_id),
        **verdict,
        # The pre-existing key for "what is left of this customer's cap". It
        # was the only non-null number the endpoint ever returned; kept so the
        # response shape does not change under callers.
        "customer_remaining": verdict["monthly_remaining"],
    }


async def over_budget(additional_cost: float = 0.0) -> str:
    """Why this org must not start new work right now, or "" if it may.

    Called on the investigation entrypoint. `check_budget` existed but had no
    callers anywhere in `app/` outside its own HTTP handler, so `can_proceed:
    false` was an opinion nothing acted on and a customer could blow through
    their cap indefinitely.

    Costs one indexed lookup when no cap is set, which is the common case.
    A cap that cannot be READ does not block: the ledger being unreachable is
    an outage of the accounting system, and refusing every investigation for
    every deployment that never set a budget would be a worse failure than
    briefly over-spending. It is logged at error level.
    """
    from app.database.engine import get_session

    org = org_uuid()
    try:
        async with get_session() as session:
            row = await _budget_row(session, org, None)
            if row is None or (row.daily_limit is None and row.monthly_limit is None):
                return ""
            daily_spent = await _spent_since(session, org, None, day_start())
            monthly_spent = await _spent_since(session, org, None, month_start())
    except Exception:  # noqa: BLE001 — see the docstring: caps fail open on read
        log.exception("could not read the spend ledger; not enforcing the budget")
        return ""

    verdict = evaluate_budget(
        daily_spent=daily_spent,
        monthly_spent=monthly_spent,
        daily_limit=row.daily_limit,
        monthly_limit=row.monthly_limit,
        additional_cost=additional_cost,
    )
    if verdict["can_proceed"]:
        return ""
    return "; ".join(verdict["reasons"])


async def cost_analytics(days: int = 7, *, org: UUID | None = None) -> dict[str, Any]:
    """Spend for this org over the last `days`, by model, customer and day.

    Bucketing by day happens here rather than in SQL because `date(...)` means
    different things to SQLite and Postgres for a timestamptz column, and the
    number of rows in a week of one org's traffic is small.
    """
    from app.database.engine import get_session

    org = org or org_uuid()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = _scope(
        select(
            LLMUsageRecord.model_id,
            LLMUsageRecord.customer_id,
            LLMUsageRecord.cost,
            LLMUsageRecord.input_tokens,
            LLMUsageRecord.output_tokens,
            LLMUsageRecord.created_at,
        ),
        org,
        None,
        cutoff,
    )

    async with get_session() as session:
        rows = (await session.execute(stmt)).all()

    by_model: dict[str, dict[str, float | int]] = {}
    by_customer: dict[str, dict[str, float | int]] = {}
    daily: dict[str, float] = {}
    for model_id, cust, cost, tin, tout, at in rows:
        model = by_model.setdefault(
            model_id, {"cost": 0.0, "requests": 0, "tokens": 0}
        )
        model["cost"] += cost
        model["requests"] += 1
        model["tokens"] += tin + tout
        who = by_customer.setdefault(cust, {"cost": 0.0, "requests": 0})
        who["cost"] += cost
        who["requests"] += 1
        day = at.strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + cost

    return {
        "period_days": days,
        "total_cost": sum(r[2] for r in rows),
        "total_requests": len(rows),
        "total_tokens": sum(r[3] + r[4] for r in rows),
        "by_model": by_model,
        "by_customer": by_customer,
        "daily_breakdown": [
            {"date": k, "cost": v} for k, v in sorted(daily.items())
        ],
    }
