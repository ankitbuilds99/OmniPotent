from datetime import datetime, timedelta, timezone

from app.dependency_graph.discovery import (
    DiscoveredService,
    DiscoverySource,
    ServiceType,
    validate_catalog,
)


def _service(name: str, at: datetime) -> DiscoveredService:
    return DiscoveredService(name=name, namespace="prod", service_type=ServiceType.API,
                             discovered_at=at, discovery_source=DiscoverySource.MANUAL)


def test_catalog_rejects_duplicate_identity():
    now = datetime.now(timezone.utc)
    result = validate_catalog([_service("checkout", now), _service("checkout", now)], now=now)
    assert not result.valid
    assert result.duplicate_fqdns == ("checkout.prod",)


def test_catalog_rejects_stale_entries_and_accepts_fresh_entries():
    now = datetime.now(timezone.utc)
    stale = _service("old", now - timedelta(hours=1))
    fresh = _service("new", now)
    result = validate_catalog([stale, fresh], now=now, max_age_seconds=300)
    assert not result.valid
    assert result.stale_fqdns == ("old.prod",)
    assert validate_catalog([fresh], now=now, max_age_seconds=300).valid
