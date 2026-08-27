"""Durable postmortem documents.

The generator writes a draft; this is where it stays. Without it the Postmortem
page's Save button and action-item status dropdown were `setState` calls with
no server behind them — the edit survived until the next render of a different
page and no further.

Stateless, like the incident store: every method opens its own session and
filters on `org_uuid()`, so one org can never read or patch another's document.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.database.orgs import ensure_org_row, org_uuid

# What an action item's status may be. The page offers exactly these three, and
# an unchecked string here would let a client write anything into the JSON and
# render as an unstyled badge forever after.
ACTION_STATUSES = ("open", "in_progress", "completed")

# Where a document is in its life. The page badges all three; "published" is the
# one that matters, because it is the difference between a draft somebody is
# still arguing with and the account of the outage the team stands behind.
DOCUMENT_STATUSES = ("draft", "in_review", "published")

ACTION_PRIORITIES = ("P0", "P1", "P2", "P3")
ACTION_TYPES = ("prevent", "detect", "mitigate")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: Any) -> Any:
    """Read a timestamp back as UTC-aware — see the note in app/incidents/store.py."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _to_document(row: Any) -> dict[str, Any]:
    """The row as the API shape the page already consumes.

    Deliberately identical to `generate_postmortem`'s return value, so the page
    does not care whether a document was just written by the model or read back
    from the table a week later.
    """
    return {
        "id": f"pm-{row.incident_id}",
        "incident_id": str(row.incident_id),
        "title": row.title,
        "status": row.status,
        "severity": row.severity,
        "generated_at": _utc(row.generated_at).isoformat(),
        "updated_at": _utc(row.updated_at).isoformat(),
        "authors": list(row.authors or []),
        "reviewers": list(row.reviewers or []),
        "sections": list(row.sections or []),
        "action_items": list(row.action_items or []),
        "timeline": list(row.timeline or []),
        "metrics": dict(row.doc_metrics or {}),
    }


class PostmortemStore:
    """Postmortem persistence for the org on the current request."""

    async def _fetch(self, session: Any, incident_id: UUID) -> Any:
        from app.database.models import Postmortem as PostmortemRow

        return (
            await session.execute(
                select(PostmortemRow).where(
                    PostmortemRow.incident_id == incident_id,
                    PostmortemRow.org_id == org_uuid(),
                )
            )
        ).scalar_one_or_none()

    async def get(self, incident_id: UUID) -> dict[str, Any] | None:
        """The stored document for `incident_id`, or None if never generated."""
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            return _to_document(row) if row else None

    async def save(
        self, incident_id: UUID, document: dict[str, Any], *, sections_only: bool = False
    ) -> dict[str, Any]:
        """Write a freshly generated document, or merge a regenerated subset.

        `sections_only` is the per-section Regenerate path. It replaces only the
        sections the generator was asked for and leaves everything else — the
        operator's edits to *other* sections, and every action item's status —
        exactly as it was. A blind overwrite here would mean regenerating the
        "Impact" paragraph silently reset the whole document.
        """
        from app.database.engine import get_session
        from app.database.models import Postmortem as PostmortemRow

        async with get_session() as session:
            org = await ensure_org_row(session)
            row = await self._fetch(session, incident_id)
            incoming = list(document.get("sections") or [])

            if row is None:
                row = PostmortemRow(
                    org_id=org,
                    incident_id=incident_id,
                    title=document.get("title") or "",
                    status=document.get("status") or "draft",
                    severity=str(document.get("severity") or ""),
                    generated_at=_now(),
                    updated_at=_now(),
                    authors=list(document.get("authors") or []),
                    reviewers=list(document.get("reviewers") or []),
                    sections=incoming,
                    action_items=list(document.get("action_items") or []),
                    timeline=list(document.get("timeline") or []),
                    doc_metrics=dict(document.get("metrics") or {}),
                )
                session.add(row)
            elif sections_only:
                fresh = {s["id"]: s for s in incoming if isinstance(s, dict) and s.get("id")}
                merged = [fresh.pop(s["id"], s) for s in (row.sections or []) if s.get("id")]
                # A section the stored document never had — the generator gained
                # one, or the first draft was a partial. Append rather than drop.
                merged.extend(fresh.values())
                row.sections = merged
                row.updated_at = _now()
            else:
                row.title = document.get("title") or row.title
                row.severity = str(document.get("severity") or row.severity)
                row.authors = list(document.get("authors") or [])
                row.reviewers = list(document.get("reviewers") or [])
                row.sections = incoming
                row.action_items = list(document.get("action_items") or [])
                row.timeline = list(document.get("timeline") or [])
                row.doc_metrics = dict(document.get("metrics") or {})
                row.generated_at = _now()
                row.updated_at = _now()

            await session.commit()
            await session.refresh(row)
            return _to_document(row)

    async def update_section(
        self, incident_id: UUID, section_id: str, content: str
    ) -> dict[str, Any] | None:
        """Persist an operator's rewrite of one section.

        Returns None when there is no stored document, and raises KeyError when
        the document has no such section — two different 404s for the caller.
        """
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if row is None:
                return None

            sections = list(row.sections or [])
            index = next(
                (i for i, s in enumerate(sections) if s.get("id") == section_id), None
            )
            if index is None:
                raise KeyError(section_id)

            # `source: "edited"` is what the page badges as human-written, and
            # what tells a later reader this paragraph is not model output.
            sections[index] = {**sections[index], "content": content, "source": "edited"}
            # Rebinding, not mutating: SQLAlchemy does not track in-place edits
            # of a plain JSON value, so assigning into `row.sections[i]` would
            # be dropped at flush.
            row.sections = sections
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _to_document(row)

    async def update_action_item(
        self, incident_id: UUID, item_id: str, status: str
    ) -> dict[str, Any] | None:
        """Persist an action item's status. Same None/KeyError contract as above."""
        from app.database.engine import get_session

        if status not in ACTION_STATUSES:
            raise ValueError(
                f"unknown status {status!r}; supported: {', '.join(ACTION_STATUSES)}"
            )

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if row is None:
                return None

            items = list(row.action_items or [])
            index = next((i for i, it in enumerate(items) if it.get("id") == item_id), None)
            if index is None:
                raise KeyError(item_id)

            items[index] = {**items[index], "status": status}
            row.action_items = items
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _to_document(row)

    async def add_action_item(
        self,
        incident_id: UUID,
        *,
        title: str,
        owner: str = "",
        priority: str = "P2",
        item_type: str = "prevent",
        due_date: str | None = None,
    ) -> dict[str, Any] | None:
        """Append a follow-up somebody agreed to in the review.

        The two "+ Add Item" buttons on the page had no handler and no endpoint
        behind them, so the only action items a postmortem could ever have were
        the ones the model proposed when the draft was written — and a review
        meeting's whole output is the items nobody thought of beforehand.
        """
        from app.database.engine import get_session

        title = title.strip()
        if not title:
            raise ValueError("an action item needs a title")
        if priority not in ACTION_PRIORITIES:
            raise ValueError(
                f"unknown priority {priority!r}; supported: {', '.join(ACTION_PRIORITIES)}"
            )
        if item_type not in ACTION_TYPES:
            raise ValueError(
                f"unknown type {item_type!r}; supported: {', '.join(ACTION_TYPES)}"
            )

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if row is None:
                return None

            items = list(row.action_items or [])
            # Ids are positional (`<incident>-action-N`), so this counts from the
            # highest existing suffix rather than the length: deleting item 2 of
            # 3 and adding one must not reuse id 3.
            used = {str(it.get("id")) for it in items}
            n = len(items) + 1
            while f"{incident_id}-action-{n}" in used:
                n += 1

            items.append({
                "id": f"{incident_id}-action-{n}",
                "title": title,
                "owner": owner.strip(),
                "priority": priority,
                "status": "open",
                # Null unless a date was actually agreed. The page used to stamp
                # now+7d on every item as if one had been.
                "due_date": due_date,
                "type": item_type,
            })
            row.action_items = items
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _to_document(row)

    async def set_status(self, incident_id: UUID, status: str) -> dict[str, Any] | None:
        """Move the document between draft, in_review and published."""
        from app.database.engine import get_session

        if status not in DOCUMENT_STATUSES:
            raise ValueError(
                f"unknown status {status!r}; supported: {', '.join(DOCUMENT_STATUSES)}"
            )

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if row is None:
                return None

            row.status = status
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _to_document(row)


_STORE = PostmortemStore()


def get_postmortem_store() -> PostmortemStore:
    """The postmortem store. Stateless — the org comes from the request context."""
    return _STORE
