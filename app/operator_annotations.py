"""Append-only operator annotations for incident evidence.

Annotations are corrections or context, never silent mutations of collected
evidence.  Each revision carries its author, timestamp, and predecessor hash so
the UI and postmortem pipeline can distinguish observed evidence from operator
judgement.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
import json
import time
from typing import Any


@dataclass(frozen=True)
class AnnotationRevision:
    incident_id: str
    field: str
    value: str
    author: str
    created_at: float
    revision: int
    previous_hash: str | None
    digest: str


def append_revision(
    history: list[AnnotationRevision],
    *,
    incident_id: str,
    field: str,
    value: str,
    author: str,
    created_at: float | None = None,
) -> AnnotationRevision:
    """Create a new revision without modifying earlier annotations."""
    if not incident_id.strip() or not field.strip() or not author.strip():
        raise ValueError("incident_id, field, and author are required")
    previous = history[-1] if history else None
    revision = (previous.revision + 1) if previous else 1
    payload: dict[str, Any] = {
        "incident_id": incident_id,
        "field": field,
        "value": value,
        "author": author,
        "created_at": time.time() if created_at is None else created_at,
        "revision": revision,
        "previous_hash": previous.digest if previous else None,
    }
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    item = AnnotationRevision(**payload, digest=digest)
    history.append(item)
    return item


def verify_history(history: list[AnnotationRevision]) -> bool:
    """Verify ordering, predecessor links, and content digests."""
    previous: AnnotationRevision | None = None
    for expected, item in enumerate(history, start=1):
        if item.revision != expected or item.previous_hash != (previous.digest if previous else None):
            return False
        payload = asdict(item)
        payload.pop("digest")
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != item.digest:
            return False
        previous = item
    return True


def latest_value(history: list[AnnotationRevision], field: str) -> str | None:
    """Return the latest operator value for a field, if one exists."""
    for item in reversed(history):
        if item.field == field:
            return item.value
    return None
