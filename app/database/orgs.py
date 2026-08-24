"""Mapping the request's tenant onto the `organizations` table.

Tenants are registered in the separate tenant-auth store (`app/tenancy`), which
never writes `organizations`. Every DB-backed feature table carries a foreign key
to it, so the first insert for a new tenant is a foreign-key violation on
Postgres — SQLite only hides it because it does not enforce foreign keys by
default. `ensure_org_row()` closes that gap on the write path.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.tenant_context import tenant_id

log = logging.getLogger("omnipotent.database.orgs")

# Namespace for deriving a stable org UUID from a tenant's string org id.
ORG_NAMESPACE = UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")
# Single-tenant deployments have no org; everything they own lives under this.
SINGLE_TENANT_ORG = UUID("00000000-0000-0000-0000-000000000000")


def org_uuid() -> UUID:
    """The org that owns rows written by, and visible to, this request.

    Tenant org ids are opaque strings and the columns are UUIDs, so a tenant id
    that is already a UUID is used directly and anything else is hashed into one
    (uuid5, stable across restarts and processes).
    """
    org = tenant_id()
    if org in ("", "-"):
        return SINGLE_TENANT_ORG
    try:
        return UUID(org)
    except ValueError:
        return uuid5(ORG_NAMESPACE, org)


async def ensure_org_row(db: Any) -> UUID:
    """Return the current org's id, creating its `organizations` row if absent."""
    from app.database.models import Organization

    org = org_uuid()
    result = await db.execute(select(Organization.id).where(Organization.id == org))
    if result.scalar_one_or_none() is not None:
        return org

    tenant = tenant_id()
    slug = "default" if tenant in ("", "-") else tenant[:64]
    db.add(Organization(id=org, slug=slug, name=slug))
    try:
        await db.commit()
    except IntegrityError:
        # Two first-requests for the same tenant raced; the other one won.
        await db.rollback()
    else:
        log.info("created organizations row for tenant %s", slug)
    return org
