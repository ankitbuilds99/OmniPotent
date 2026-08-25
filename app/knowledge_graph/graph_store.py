"""Durable storage for the knowledge graph's non-Neo4j mode.

`Neo4jKnowledgeGraph.connect()` used to answer an absent neo4j driver with
`self._in_memory_nodes = {}`. That dict was the whole knowledge graph for the
default deployment: services, deployments, incidents, changes, alerts and every
relationship between them, all discarded on restart and never shared between
workers. Worse, nothing downstream could tell — `find_blast_radius` returned
`affected_count: 0` with HTTP 200, which reads as "nothing depends on this
service" rather than "the graph is gone".

This module is that mode's real storage: the `graph_nodes` / `graph_edges`
tables, scoped to the requesting org by `org_uuid()` exactly like
`app/knowledge_base/store.py`. It is not a Neo4j replacement — there is no
Cypher here — but it is durable, shared across workers, and isolated per org.

Node identity is (org, label, node_id), matching the `MERGE (s:Service {id})`
key the Cypher branch uses. Edge endpoints are bare node ids because
`create_relationship` matches on `(a {id: $source_id})` without a label.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.orgs import ensure_org_row, org_uuid


def _now() -> datetime:
    return datetime.now(timezone.utc)


def node_key(label: str, node_id: str) -> str:
    """The `"Label:id"` key the in-memory store used, kept as the traversal key."""
    return f"{label}:{node_id}"


async def put_node(label: str, node_id: str, props: dict[str, Any]) -> None:
    """Upsert one node. Mirrors `MERGE (n:Label {id: $id}) SET n += $props`."""
    from app.database.engine import get_session
    from app.database.models import GraphNode

    async with get_session() as session:
        org = await ensure_org_row(session)
        row = (
            await session.execute(
                select(GraphNode).where(
                    GraphNode.org_id == org,
                    GraphNode.label == label,
                    GraphNode.node_id == node_id,
                )
            )
        ).scalar_one_or_none()

        now = _now()
        if row is None:
            session.add(
                GraphNode(
                    id=uuid.uuid4(),
                    org_id=org,
                    label=label,
                    node_id=node_id,
                    properties=dict(props),
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            # `SET n += $props` merges rather than replaces, and a JSON column is
            # only flushed when the attribute is rebound, not mutated in place.
            row.properties = {**(row.properties or {}), **props}
            row.updated_at = now

        try:
            await session.commit()
        except IntegrityError:
            # Two writers created the same node concurrently; the other won and
            # its properties are equivalent, so there is nothing to redo.
            await session.rollback()


async def get_node(label: str, node_id: str) -> dict[str, Any] | None:
    from app.database.engine import get_session
    from app.database.models import GraphNode

    async with get_session() as session:
        row = (
            await session.execute(
                select(GraphNode).where(
                    GraphNode.org_id == org_uuid(),
                    GraphNode.label == label,
                    GraphNode.node_id == node_id,
                )
            )
        ).scalar_one_or_none()
    return {"type": label, **(row.properties or {})} if row else None


async def list_nodes(label: str) -> list[dict[str, Any]]:
    from app.database.engine import get_session
    from app.database.models import GraphNode

    async with get_session() as session:
        rows = (
            await session.execute(
                select(GraphNode)
                .where(GraphNode.org_id == org_uuid(), GraphNode.label == label)
                .order_by(GraphNode.created_at, GraphNode.node_id)
            )
        ).scalars().all()
    return [{"type": row.label, **(row.properties or {})} for row in rows]


async def put_edge(
    source_id: str,
    target_id: str,
    rel_type: str,
    props: dict[str, Any] | None = None,
) -> None:
    """Upsert one relationship. Mirrors `MERGE (a)-[r:TYPE]->(b) SET r += $props`."""
    from app.database.engine import get_session
    from app.database.models import GraphEdge

    async with get_session() as session:
        org = await ensure_org_row(session)
        row = (
            await session.execute(
                select(GraphEdge).where(
                    GraphEdge.org_id == org,
                    GraphEdge.source_id == source_id,
                    GraphEdge.target_id == target_id,
                    GraphEdge.rel_type == rel_type,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            session.add(
                GraphEdge(
                    id=uuid.uuid4(),
                    org_id=org,
                    source_id=source_id,
                    target_id=target_id,
                    rel_type=rel_type,
                    properties=dict(props or {}),
                    created_at=_now(),
                )
            )
        else:
            row.properties = {**(row.properties or {}), **(props or {})}

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


async def list_edges() -> list[dict[str, Any]]:
    """Every relationship this org owns, as `{source, target, type, properties}`."""
    from app.database.engine import get_session
    from app.database.models import GraphEdge

    async with get_session() as session:
        rows = (
            await session.execute(
                select(GraphEdge)
                .where(GraphEdge.org_id == org_uuid())
                .order_by(GraphEdge.created_at, GraphEdge.source_id)
            )
        ).scalars().all()
    return [
        {
            "source": row.source_id,
            "target": row.target_id,
            "type": row.rel_type,
            "properties": dict(row.properties or {}),
        }
        for row in rows
    ]


async def snapshot() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """This org's whole graph, as the traversals want it.

    Returns `(nodes, edges)` where `nodes` is keyed `"Label:id"`. A traversal
    needs the connected component around a service, and the depth is not known
    before walking, so it reads the org's graph once rather than issuing a query
    per hop. One org's infrastructure graph is thousands of nodes at the outside.
    """
    from app.database.engine import get_session
    from app.database.models import GraphNode

    async with get_session() as session:
        rows = (
            await session.execute(
                select(GraphNode)
                .where(GraphNode.org_id == org_uuid())
                .order_by(GraphNode.created_at, GraphNode.node_id)
            )
        ).scalars().all()

    nodes = {
        node_key(row.label, row.node_id): {"type": row.label, **(row.properties or {})}
        for row in rows
    }
    return nodes, await list_edges()


async def counts() -> tuple[dict[str, int], int]:
    """(nodes per label, total edges) for this org."""
    from app.database.engine import get_session
    from app.database.models import GraphEdge, GraphNode
    from sqlalchemy import func

    async with get_session() as session:
        node_rows = (
            await session.execute(
                select(GraphNode.label, func.count())
                .where(GraphNode.org_id == org_uuid())
                .group_by(GraphNode.label)
            )
        ).all()
        edge_count = (
            await session.execute(
                select(func.count()).select_from(GraphEdge).where(
                    GraphEdge.org_id == org_uuid()
                )
            )
        ).scalar_one()

    return {label: int(count) for label, count in node_rows}, int(edge_count)
