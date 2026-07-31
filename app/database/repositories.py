"""Repository layer for database operations.

Provides high-level APIs for CRUD operations using either Supabase client
or direct SQLAlchemy, depending on configuration.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.config import get_settings
from app.database.supabase_client import (
    get_supabase_admin_client,
    is_supabase_configured,
)

log = logging.getLogger("devcopilot.database.repositories")


class InvestigationRepository:
    """Repository for investigation operations."""

    @staticmethod
    async def create(
        org_id: UUID | str,
        thread_id: str,
        query: str,
    ) -> dict[str, Any]:
        """Create a new investigation."""
        if not is_supabase_configured():
            return {
                "id": str(uuid4()),
                "org_id": str(org_id),
                "thread_id": thread_id,
                "query": query,
                "status": "active",
                "created_at": datetime.utcnow().isoformat(),
            }

        client = get_supabase_admin_client()
        result = (
            client.table("investigations")
            .insert({
                "org_id": str(org_id),
                "thread_id": thread_id,
                "query": query,
                "status": "active",
            })
            .execute()
        )
        return result.data[0] if result.data else {}

    @staticmethod
    async def get_by_thread_id(org_id: UUID | str, thread_id: str) -> dict[str, Any] | None:
        """Get investigation by thread ID."""
        if not is_supabase_configured():
            return None

        client = get_supabase_admin_client()
        result = (
            client.table("investigations")
            .select("*")
            .eq("org_id", str(org_id))
            .eq("thread_id", thread_id)
            .single()
            .execute()
        )
        return result.data

    @staticmethod
    async def update(
        investigation_id: UUID | str,
        **updates: Any,
    ) -> dict[str, Any] | None:
        """Update an investigation."""
        if not is_supabase_configured():
            return {"id": str(investigation_id), **updates}

        client = get_supabase_admin_client()
        result = (
            client.table("investigations")
            .update(updates)
            .eq("id", str(investigation_id))
            .execute()
        )
        return result.data[0] if result.data else None

    @staticmethod
    async def complete(
        investigation_id: UUID | str,
        summary: str,
        report: dict[str, Any],
        tokens_used: int,
        latency_ms: int,
    ) -> dict[str, Any] | None:
        """Mark investigation as complete with results."""
        return await InvestigationRepository.update(
            investigation_id,
            status="completed",
            summary=summary,
            report=report,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            completed_at=datetime.utcnow().isoformat(),
        )

    @staticmethod
    async def list_recent(
        org_id: UUID | str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent investigations for an org."""
        if not is_supabase_configured():
            return []

        client = get_supabase_admin_client()
        result = (
            client.table("investigations")
            .select("*")
            .eq("org_id", str(org_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []


class IncidentRepository:
    """Repository for incident operations."""

    @staticmethod
    async def create(
        org_id: UUID | str | None,
        title: str,
        severity: str = "medium",
        description: str | None = None,
        service: str | None = None,
        source: str = "manual",
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new incident."""
        if not is_supabase_configured():
            return {
                "id": str(uuid4()),
                "org_id": str(org_id) if org_id else None,
                "title": title,
                "severity": severity,
                "status": "triggered",
                "created_at": datetime.utcnow().isoformat(),
            }

        client = get_supabase_admin_client()
        data = {
            "title": title,
            "severity": severity,
            "status": "triggered",
            "source": source,
        }
        if org_id:
            data["org_id"] = str(org_id)
        if description:
            data["description"] = description
        if service:
            data["service"] = service
        if external_id:
            data["external_id"] = external_id
        if metadata:
            data["metadata"] = metadata

        result = client.table("incidents").insert(data).execute()
        return result.data[0] if result.data else {}

    @staticmethod
    async def get_by_id(incident_id: UUID | str) -> dict[str, Any] | None:
        """Get incident by ID."""
        if not is_supabase_configured():
            return None

        client = get_supabase_admin_client()
        result = (
            client.table("incidents")
            .select("*")
            .eq("id", str(incident_id))
            .single()
            .execute()
        )
        return result.data

    @staticmethod
    async def update_status(
        incident_id: UUID | str,
        status: str,
    ) -> dict[str, Any] | None:
        """Update incident status."""
        if not is_supabase_configured():
            return {"id": str(incident_id), "status": status}

        client = get_supabase_admin_client()
        updates: dict[str, Any] = {"status": status}

        if status == "acknowledged":
            updates["acknowledged_at"] = datetime.utcnow().isoformat()
        elif status == "resolved":
            updates["resolved_at"] = datetime.utcnow().isoformat()

        result = (
            client.table("incidents")
            .update(updates)
            .eq("id", str(incident_id))
            .execute()
        )
        return result.data[0] if result.data else None

    @staticmethod
    async def link_investigation(
        incident_id: UUID | str,
        investigation_id: UUID | str,
    ) -> dict[str, Any] | None:
        """Link an investigation to an incident."""
        if not is_supabase_configured():
            return {"id": str(incident_id), "investigation_id": str(investigation_id)}

        client = get_supabase_admin_client()
        result = (
            client.table("incidents")
            .update({"investigation_id": str(investigation_id)})
            .eq("id", str(incident_id))
            .execute()
        )
        return result.data[0] if result.data else None

    @staticmethod
    async def list_active(
        org_id: UUID | str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List active (non-resolved) incidents."""
        if not is_supabase_configured():
            return []

        client = get_supabase_admin_client()
        query = (
            client.table("incidents")
            .select("*")
            .neq("status", "resolved")
            .order("created_at", desc=True)
            .limit(limit)
        )

        if org_id:
            query = query.eq("org_id", str(org_id))

        result = query.execute()
        return result.data or []


class OrganizationRepository:
    """Repository for organization operations."""

    @staticmethod
    async def create(
        slug: str,
        name: str,
        plan: str = "free",
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new organization."""
        if not is_supabase_configured():
            return {
                "id": str(uuid4()),
                "slug": slug,
                "name": name,
                "plan": plan,
                "settings": settings or {},
                "created_at": datetime.utcnow().isoformat(),
            }

        client = get_supabase_admin_client()
        result = (
            client.table("organizations")
            .insert({
                "slug": slug,
                "name": name,
                "plan": plan,
                "settings": settings or {},
            })
            .execute()
        )
        return result.data[0] if result.data else {}

    @staticmethod
    async def get_by_slug(slug: str) -> dict[str, Any] | None:
        """Get organization by slug."""
        if not is_supabase_configured():
            return None

        client = get_supabase_admin_client()
        result = (
            client.table("organizations")
            .select("*")
            .eq("slug", slug)
            .single()
            .execute()
        )
        return result.data

    @staticmethod
    async def get_by_id(org_id: UUID | str) -> dict[str, Any] | None:
        """Get organization by ID."""
        if not is_supabase_configured():
            return None

        client = get_supabase_admin_client()
        result = (
            client.table("organizations")
            .select("*")
            .eq("id", str(org_id))
            .single()
            .execute()
        )
        return result.data

    @staticmethod
    async def update_plan(
        org_id: UUID | str,
        plan: str,
    ) -> dict[str, Any] | None:
        """Update organization plan."""
        if not is_supabase_configured():
            return {"id": str(org_id), "plan": plan}

        client = get_supabase_admin_client()
        result = (
            client.table("organizations")
            .update({"plan": plan})
            .eq("id", str(org_id))
            .execute()
        )
        return result.data[0] if result.data else None


class AuditLogRepository:
    """Repository for audit log operations."""

    @staticmethod
    async def append(
        event_type: str,
        data: dict[str, Any],
        org_id: UUID | str | None = None,
        actor: str | None = None,
        prev_hash: str = "",
    ) -> dict[str, Any]:
        """Append an audit log entry."""
        import hashlib
        import json

        entry_data = json.dumps(data, sort_keys=True, default=str)
        entry_hash = hashlib.sha256(
            f"{prev_hash}{event_type}{entry_data}".encode()
        ).hexdigest()

        if not is_supabase_configured():
            return {
                "id": 1,
                "event_type": event_type,
                "data": data,
                "entry_hash": entry_hash,
                "created_at": datetime.utcnow().isoformat(),
            }

        client = get_supabase_admin_client()
        insert_data: dict[str, Any] = {
            "event_type": event_type,
            "data": data,
            "prev_hash": prev_hash or "genesis",
            "entry_hash": entry_hash,
        }
        if org_id:
            insert_data["org_id"] = str(org_id)
        if actor:
            insert_data["actor"] = actor

        result = client.table("audit_logs").insert(insert_data).execute()
        return result.data[0] if result.data else {}

    @staticmethod
    async def list_recent(
        org_id: UUID | str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List recent audit log entries."""
        if not is_supabase_configured():
            return []

        client = get_supabase_admin_client()
        query = (
            client.table("audit_logs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )

        if org_id:
            query = query.eq("org_id", str(org_id))
        if event_type:
            query = query.eq("event_type", event_type)

        result = query.execute()
        return result.data or []
