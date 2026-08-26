"""Per-tenant instances of the in-process feature stores.

The feature routers (incidents, teams, runbooks, planning, status pages, …) each
kept their state in a module-level singleton created once per process. With
multi-tenancy on, every org shared that one object: any authenticated tenant
could list, mutate and delete every other tenant's incidents and runbooks, and
seeded demo data was writable by all of them. There is no org column to filter
on — the state is a plain dict — so the isolation has to come from giving each
org its own instance.

`scoped(factory)` wraps a store constructor in a registry keyed by the org on the
current request. Call `.get()` where the old code called the singleton getter.
Requests with no tenant (single-tenant deployments, offline demo, background
jobs) share one "-" bucket, so the existing non-tenant behaviour is unchanged.

This is a seam, not a destination: the stores are still process memory and still
vanish on restart. Moving them to real per-org tables is the follow-up; until
then this is what stops a paying customer from reading another one's incidents.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Callable, Generic, TypeVar

from app.tenant_context import tenant_id

log = logging.getLogger("omnipotent.tenancy.scoping")

T = TypeVar("T")

# How many distinct orgs may hold a live instance of any one store. Without a
# bound, signing up orgs in a loop is an unauthenticated way to exhaust process
# memory. Evicting is lossy for these in-memory stores, so it is logged loudly
# rather than done quietly.
DEFAULT_MAX_TENANTS = 512


class ScopedRegistry(Generic[T]):
    """One lazily-built instance of `factory` per org, LRU-bounded."""

    def __init__(
        self, factory: Callable[[], T], max_tenants: int = DEFAULT_MAX_TENANTS
    ) -> None:
        self._factory = factory
        self._max = max(1, max_tenants)
        self._instances: OrderedDict[str, T] = OrderedDict()
        # Routers are async but the stores are sync, and a store can be built
        # from a thread-pool worker (`run_in_threadpool`), so guard the dict.
        self._lock = threading.Lock()

    def get(self) -> T:
        """The instance for the org on the current request."""
        return self.for_org(tenant_id())

    def for_org(self, org_id: str) -> T:
        key = org_id or "-"
        with self._lock:
            existing = self._instances.get(key)
            if existing is not None:
                self._instances.move_to_end(key)
                return existing

        # Build outside the lock: factories seed demo data and can be slow, and
        # nothing here is harmed by two racing builds — one is discarded below.
        built = self._factory()

        with self._lock:
            existing = self._instances.get(key)
            if existing is not None:
                self._instances.move_to_end(key)
                return existing
            self._instances[key] = built
            while len(self._instances) > self._max:
                evicted, _ = self._instances.popitem(last=False)
                log.warning(
                    "scoped store at capacity (%d orgs): evicted in-memory state for "
                    "org %s; its data is gone until it is recreated",
                    self._max,
                    evicted,
                )
            return built

    def set_for_org(self, org_id: str, instance: T) -> None:
        """Override one org's instance (tests, or a database-backed swap-in)."""
        with self._lock:
            self._instances[org_id or "-"] = instance
            self._instances.move_to_end(org_id or "-")

    def drop(self, org_id: str) -> None:
        """Forget one org's instance."""
        with self._lock:
            self._instances.pop(org_id or "-", None)

    def clear(self) -> None:
        """Forget every org's instance (tests rely on this for a clean slate)."""
        with self._lock:
            self._instances.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._instances)


_REGISTRIES: list[ScopedRegistry] = []


def scoped(factory: Callable[[], T], max_tenants: int = DEFAULT_MAX_TENANTS) -> ScopedRegistry[T]:
    """Build a per-org registry and record it so tests can reset everything."""
    registry = ScopedRegistry(factory, max_tenants)
    _REGISTRIES.append(registry)
    return registry


def reset_all() -> None:
    """Clear every registry. Test-only: leaks state between cases otherwise."""
    for registry in _REGISTRIES:
        registry.clear()
