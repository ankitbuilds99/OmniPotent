"""Database-backed storage for planning task graphs.

Replaces `app/planning/api_router.py:_graphs`, a module-level dict. A graph is a
user-authored artifact built up over many requests — create it, add tasks, add
dependencies, checkpoint it, execute it — so losing it on restart, deploy or
scale-down loses work the caller did by hand. With more than one replica it was
worse than lossy: the graph existed on whichever pod happened to serve the create
call, and every subsequent request round-robined to a pod that returned 404.

Everything here is scoped by org_id and the table's primary key is (id, org_id).
The routes take a caller-supplied graph id, so scoping is not optional: without
it, `POST /api/planning/graphs {"graph_id": "x"}` from one tenant would collide
with another tenant's "x" and hand over its tasks.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import ensure_schema, get_session
from app.database.models import PlanningGraph
from app.planning.task_graph import TaskGraph

log = logging.getLogger("omnipotent.planning.store")


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    """A session against the configured database, schema guaranteed to exist."""
    await ensure_schema()
    async with get_session() as session:
        yield session


async def save_graph(org_id: str, graph: TaskGraph) -> None:
    """Insert or update a graph's stored state.

    Called after every mutation rather than on a timer: the routes hand back a
    response that claims the change was made, so the change has to be durable by
    the time that response is sent.
    """
    payload = graph.serialize()

    async with _session() as session:
        row = await session.get(PlanningGraph, {"id": graph.id, "org_id": org_id})
        if row is None:
            session.add(
                PlanningGraph(
                    id=graph.id,
                    org_id=org_id,
                    state=payload,
                    task_count=len(graph.nodes),
                )
            )
        else:
            # Reassigned, not mutated in place: SQLAlchemy only marks a JSON
            # column dirty on assignment.
            row.state = payload
            row.task_count = len(graph.nodes)
        await session.commit()


async def load_graph(org_id: str, graph_id: str) -> TaskGraph | None:
    """Load one graph, or None if this org has no graph by that id."""
    async with _session() as session:
        row = await session.get(PlanningGraph, {"id": graph_id, "org_id": org_id})
        if row is None:
            return None
        return _deserialize(row)


async def list_graphs(org_id: str) -> list[TaskGraph]:
    """Every graph belonging to one org, most recently updated first."""
    async with _session() as session:
        rows = (
            await session.execute(
                select(PlanningGraph)
                .where(PlanningGraph.org_id == org_id)
                .order_by(PlanningGraph.updated_at.desc())
            )
        ).scalars()
        return [g for g in (_deserialize(row) for row in rows) if g is not None]


async def delete_graph(org_id: str, graph_id: str) -> bool:
    """Delete a graph. Returns whether one was actually removed."""
    async with _session() as session:
        # A DELETE always yields a CursorResult; the declared Result type has no
        # rowcount, so the cast is what lets the caller be told whether the id
        # existed instead of always claiming success.
        result = cast(
            CursorResult[Any],
            await session.execute(
                delete(PlanningGraph).where(
                    PlanningGraph.id == graph_id, PlanningGraph.org_id == org_id
                )
            ),
        )
        await session.commit()
        return bool(result.rowcount)


async def count_graphs(org_id: str) -> int:
    """How many graphs this org has. Used to name decomposed goals."""
    async with _session() as session:
        rows = (
            await session.execute(
                select(PlanningGraph.id).where(PlanningGraph.org_id == org_id)
            )
        ).all()
        return len(rows)


async def clear_graphs(org_id: str | None = None) -> None:
    """Delete stored graphs. Test/support helper, deliberately explicit."""
    async with _session() as session:
        stmt = delete(PlanningGraph)
        if org_id is not None:
            stmt = stmt.where(PlanningGraph.org_id == org_id)
        await session.execute(stmt)
        await session.commit()


def _deserialize(row: PlanningGraph) -> TaskGraph | None:
    """Rebuild a graph, logging rather than raising on a payload we can't read.

    One graph written by a newer version, or hand-edited, must not take out the
    listing endpoint for every other graph the org owns.
    """
    try:
        return TaskGraph.deserialize(row.state)
    except Exception:  # noqa: BLE001 — one bad row must not fail the request
        log.exception("could not deserialize planning graph %s (org=%s)", row.id, row.org_id)
        return None
