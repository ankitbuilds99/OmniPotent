"""Feature Store for Event Intelligence.

Manages time-series statistics and features for ML-based
noise reduction and severity prediction.

Storage
-------
With ``COPILOT_REDIS_URL`` set, every metric point, alert, deployment marker and
maintenance window recorded here lives in Redis under an ``ei:<org>:`` key
prefix: it survives a restart and every worker process sees the same data.

Without it the store keeps its history in process memory. That history is real -
the statistics below are computed from it and nothing is invented - but it dies
with the process and is invisible to sibling workers, so every write reports
``durable: false`` and says where the data actually went instead of implying a
database that is not there.

When Redis *is* configured and a command fails, the operation raises
``FeatureStoreUnavailable`` rather than logging a warning and reporting success:
a caller that was told "recorded" has to be able to believe it.
"""

from __future__ import annotations

import json
import logging
import statistics
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional
from urllib.parse import quote

log = logging.getLogger("omnipotent.event_intelligence.feature_store")

# How many indexed keys GET /stats will count before it stops and says so,
# rather than issuing an unbounded number of round trips from a stats endpoint.
STATS_KEY_LIMIT = 500

# Service-level alert timestamps are only ever read over a 1h window; a day of
# history is plenty and keeps the key small.
SERVICE_ALERT_RETENTION_HOURS = 24

# A deployment marker older than this cannot make any window check true.
DEPLOYMENT_RETENTION_HOURS = 24


class FeatureStoreUnavailable(RuntimeError):
    """Redis is configured for the feature store but the command failed.

    Callers (the HTTP layer) turn this into a 503. It is deliberately not
    swallowed: "recorded" must mean recorded.
    """


def _as_text(value: Any) -> str:
    """Redis clients return bytes or str depending on decode_responses."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return str(value)


def _ensure_utc(value: datetime) -> datetime:
    """Treat a naive datetime as UTC.

    Maintenance windows arrive as ISO strings from the API; without a timezone
    suffix they parse naive, and comparing those against an aware ``now()``
    raises TypeError - which surfaced as a 500 from /maintenance/{service}.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class AlertFeatures:
    """Features extracted for an alert."""
    fingerprint: str
    service: str
    metric_name: str

    # Time-series statistics
    hourly_avg: float = 0.0
    hourly_stddev: float = 0.0
    hourly_min: float = 0.0
    hourly_max: float = 0.0
    daily_avg: float = 0.0
    daily_stddev: float = 0.0
    weekly_avg: float = 0.0

    # How many data points each statistic was computed from. Without these a
    # consumer cannot tell "the average is 0.0" from "there is no data", and
    # the noise engine was silently treating the second as the first.
    hourly_sample_count: int = 0
    daily_sample_count: int = 0
    weekly_sample_count: int = 0

    # Alert patterns
    alert_count_1h: int = 0
    alert_count_24h: int = 0
    alert_count_7d: int = 0
    flap_count_1h: int = 0
    auto_resolve_rate_7d: float = 0.0
    avg_resolution_time_seconds: float = 0.0

    # Service patterns
    service_alert_count_1h: int = 0
    service_incident_count_24h: int = 0

    # Temporal patterns
    hour_of_day: int = 0
    day_of_week: int = 0
    is_business_hours: bool = False
    is_weekend: bool = False

    # Context
    deployment_in_progress: bool = False
    maintenance_active: bool = False

    def to_vector(self) -> list[float]:
        """Convert features to ML-ready vector."""
        return [
            self.hourly_avg,
            self.hourly_stddev,
            self.daily_avg,
            self.daily_stddev,
            float(self.alert_count_1h),
            float(self.alert_count_24h),
            float(self.flap_count_1h),
            self.auto_resolve_rate_7d,
            self.avg_resolution_time_seconds / 3600,  # Normalize to hours
            float(self.service_alert_count_1h),
            float(self.hour_of_day) / 24,  # Normalize
            float(self.day_of_week) / 7,  # Normalize
            float(self.is_business_hours),
            float(self.is_weekend),
            float(self.deployment_in_progress),
            float(self.maintenance_active),
        ]


@dataclass
class MetricDataPoint:
    """A single metric data point."""
    value: float
    timestamp: datetime
    labels: dict[str, str] = field(default_factory=dict)


class FeatureStore:
    """Manages features and time-series data for event intelligence.

    Features:
    - Stores metric time-series for statistical analysis
    - Tracks alert history for pattern detection
    - Computes rolling statistics (mean, stddev, percentiles)
    - Uses Redis for durable, worker-shared state when it is configured

    `namespace` is the org the instance belongs to. Redis is shared by every
    tenant, so it is part of every key: without it one org's metric history
    would be the history another org's alerts are scored against.
    """

    def __init__(
        self,
        redis_client: Any = None,
        retention_hours: int = 168,  # 7 days
        namespace: str = "",
    ):
        self.redis = redis_client
        self.retention_hours = retention_hours
        self.namespace = namespace or "-"

        # In-memory storage (used when Redis is not configured)
        self._metric_history: dict[str, list[MetricDataPoint]] = defaultdict(list)
        self._alert_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._service_alerts: dict[str, list[datetime]] = defaultdict(list)
        self._deployments: dict[str, datetime] = {}
        self._maintenance_windows: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Where the data lives
    # ------------------------------------------------------------------

    @property
    def is_durable(self) -> bool:
        """True when writes survive a restart and are shared between workers."""
        return self.redis is not None

    @property
    def storage_description(self) -> str:
        """Where recorded data actually goes, in words a reader can act on."""
        if self.redis is not None:
            return f"redis (key prefix: {self._key()}:)"
        return (
            "process memory: recorded metrics, alerts, deployments and "
            "maintenance windows are lost on restart and are not shared between "
            "worker processes. Set COPILOT_REDIS_URL to persist them."
        )

    def _key(self, *parts: str) -> str:
        return ":".join(
            ["ei", quote(self.namespace, safe="")] + [quote(p, safe="") for p in parts]
        )

    def _metric_key(self, service: str, metric_name: str) -> str:
        return self._key("metrics", service, metric_name)

    def _alert_key(self, fingerprint: str) -> str:
        return self._key("alerts", fingerprint)

    def _service_alert_key(self, service: str) -> str:
        return self._key("service_alerts", service)

    @contextmanager
    def _redis_errors(self, what: str) -> Iterator[None]:
        try:
            yield
        except FeatureStoreUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 - re-raised as a typed error below
            raise FeatureStoreUnavailable(
                "Redis is configured for the event-intelligence feature store "
                f"(COPILOT_REDIS_URL) but {what} failed: {e}"
            ) from e

    def _retention_cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def record_metric(
        self,
        service: str,
        metric_name: str,
        value: float,
        timestamp: Optional[datetime] = None,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Record a metric data point."""
        ts = _ensure_utc(timestamp or datetime.now(timezone.utc))

        if self.redis is None:
            key = f"{service}:{metric_name}"
            self._metric_history[key].append(
                MetricDataPoint(value=value, timestamp=ts, labels=labels or {})
            )
            self._cleanup_old_data(key)
            return

        redis_key = self._metric_key(service, metric_name)
        member = json.dumps(
            {
                "t": ts.timestamp(),
                "v": float(value),
                "labels": labels or {},
                # Two identical values at the same instant are two data points,
                # not one; a sorted-set member has to be unique to survive.
                "u": uuid.uuid4().hex[:8],
            },
            sort_keys=True,
        )
        index = self._key("metric_keys")
        ttl = self.retention_hours * 3600

        with self._redis_errors("recording a metric"):
            await self.redis.zadd(redis_key, {member: ts.timestamp()})
            await self.redis.zremrangebyscore(
                redis_key, "-inf", self._retention_cutoff().timestamp()
            )
            await self.redis.expire(redis_key, ttl)
            await self.redis.sadd(index, redis_key)
            await self.redis.expire(index, ttl)

    async def record_alert(
        self,
        fingerprint: str,
        service: str,
        metric_name: str,
        severity: str,
        timestamp: Optional[datetime] = None,
        resolved: bool = False,
        resolution_time_seconds: Optional[int] = None,
        auto_resolved: bool = False,
    ) -> None:
        """Record an alert occurrence."""
        ts = _ensure_utc(timestamp or datetime.now(timezone.utc))

        alert_record: dict[str, Any] = {
            "timestamp": ts,
            "service": service,
            "metric_name": metric_name,
            "severity": severity,
            "resolved": resolved,
            "resolution_time_seconds": resolution_time_seconds,
            "auto_resolved": auto_resolved,
        }

        if self.redis is None:
            self._alert_history[fingerprint].append(alert_record)
            self._service_alerts[service].append(ts)
            self._cleanup_alert_history(fingerprint)
            self._cleanup_service_alerts(service)
            return

        alert_key = self._alert_key(fingerprint)
        service_key = self._service_alert_key(service)
        member = json.dumps(
            {**alert_record, "timestamp": ts.timestamp(), "u": uuid.uuid4().hex[:8]},
            sort_keys=True,
        )
        service_member = json.dumps(
            {"t": ts.timestamp(), "fp": fingerprint, "u": uuid.uuid4().hex[:8]},
            sort_keys=True,
        )
        ttl = self.retention_hours * 3600
        service_ttl = SERVICE_ALERT_RETENTION_HOURS * 3600
        service_cutoff = (
            datetime.now(timezone.utc)
            - timedelta(hours=SERVICE_ALERT_RETENTION_HOURS)
        ).timestamp()

        with self._redis_errors("recording an alert"):
            await self.redis.zadd(alert_key, {member: ts.timestamp()})
            await self.redis.zremrangebyscore(
                alert_key, "-inf", self._retention_cutoff().timestamp()
            )
            await self.redis.expire(alert_key, ttl)
            await self.redis.sadd(self._key("alert_keys"), alert_key)
            await self.redis.expire(self._key("alert_keys"), ttl)

            await self.redis.zadd(service_key, {service_member: ts.timestamp()})
            await self.redis.zremrangebyscore(service_key, "-inf", service_cutoff)
            await self.redis.expire(service_key, service_ttl)
            await self.redis.sadd(self._key("service_keys"), service_key)
            await self.redis.expire(self._key("service_keys"), ttl)

    async def record_deployment(
        self,
        service: str,
        started_at: Optional[datetime] = None,
    ) -> None:
        """Record a deployment start."""
        ts = _ensure_utc(started_at or datetime.now(timezone.utc))

        if self.redis is None:
            self._deployments[service] = ts
            return

        key = self._key("deployments")
        with self._redis_errors("recording a deployment"):
            await self.redis.hset(key, mapping={service: repr(ts.timestamp())})
            await self.redis.expire(key, DEPLOYMENT_RETENTION_HOURS * 3600)
            await self._prune_deployments(key)

    async def clear_deployment(self, service: str) -> None:
        """Clear deployment flag for a service."""
        if self.redis is None:
            self._deployments.pop(service, None)
            return

        with self._redis_errors("clearing a deployment"):
            await self.redis.hdel(self._key("deployments"), service)

    async def add_maintenance_window(
        self,
        services: list[str],
        start: datetime,
        end: datetime,
        description: str = "",
    ) -> None:
        """Add a maintenance window."""
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)

        if self.redis is None:
            self._maintenance_windows.append({
                "services": list(services),
                "start": start_utc,
                "end": end_utc,
                "description": description,
            })
            return

        key = self._key("maintenance")
        member = json.dumps(
            {
                "services": list(services),
                "start": start_utc.timestamp(),
                "end": end_utc.timestamp(),
                "description": description,
                "u": uuid.uuid4().hex[:8],
            },
            sort_keys=True,
        )
        # Scored by end time, so "windows that have not ended" is a range query.
        # Windows that ended more than a day ago can never match again.
        stale = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()

        with self._redis_errors("adding a maintenance window"):
            await self.redis.zadd(key, {member: end_utc.timestamp()})
            await self.redis.zremrangebyscore(key, "-inf", stale)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def is_deployment_in_progress(
        self, service: str, window_minutes: int = 30
    ) -> bool:
        """Check if deployment is in progress for a service."""
        started = await self._deployment_started(service)
        if started is None:
            return False

        elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60
        return elapsed < window_minutes

    async def is_in_maintenance(self, service: str) -> bool:
        """Check if service is currently in maintenance."""
        now = datetime.now(timezone.utc)

        for window in await self._active_maintenance_windows(now):
            if service in window["services"] or "*" in window["services"]:
                return True

        return False

    async def get_features(
        self,
        fingerprint: str,
        service: str,
        metric_name: str,
    ) -> AlertFeatures:
        """Get features for an alert."""
        now = datetime.now(timezone.utc)

        # Calculate time-series statistics
        hourly_stats = await self._calculate_stats(service, metric_name, hours=1)
        daily_stats = await self._calculate_stats(service, metric_name, hours=24)
        weekly_stats = await self._calculate_stats(service, metric_name, hours=168)

        # Calculate alert patterns
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        alert_history = await self._alert_records(fingerprint, since=week_ago)

        alert_count_1h = sum(1 for a in alert_history if a["timestamp"] > hour_ago)
        alert_count_24h = sum(1 for a in alert_history if a["timestamp"] > day_ago)
        alert_count_7d = len(alert_history)

        # Flap count: alerts in the last hour, as a proxy for state changes.
        flap_count = alert_count_1h

        # Calculate auto-resolve rate
        resolved_alerts = [a for a in alert_history if a.get("resolved")]
        auto_resolved = [a for a in resolved_alerts if a.get("auto_resolved")]
        auto_resolve_rate = (
            len(auto_resolved) / len(resolved_alerts) if resolved_alerts else 0.0
        )

        # Calculate average resolution time
        resolution_times = [
            float(a["resolution_time_seconds"])
            for a in alert_history
            if a.get("resolution_time_seconds") is not None
        ]
        avg_resolution_time = (
            statistics.mean(resolution_times) if resolution_times else 0.0
        )

        # Service-level patterns
        service_alert_count_1h = await self._service_alert_count(service, hour_ago)

        # Temporal features
        hour_of_day = now.hour
        day_of_week = now.weekday()
        is_business_hours = 9 <= hour_of_day <= 18 and day_of_week < 5
        is_weekend = day_of_week >= 5

        return AlertFeatures(
            fingerprint=fingerprint,
            service=service,
            metric_name=metric_name,
            hourly_avg=hourly_stats["mean"],
            hourly_stddev=hourly_stats["stddev"],
            hourly_min=hourly_stats["min"],
            hourly_max=hourly_stats["max"],
            daily_avg=daily_stats["mean"],
            daily_stddev=daily_stats["stddev"],
            weekly_avg=weekly_stats["mean"],
            hourly_sample_count=int(hourly_stats["count"]),
            daily_sample_count=int(daily_stats["count"]),
            weekly_sample_count=int(weekly_stats["count"]),
            alert_count_1h=alert_count_1h,
            alert_count_24h=alert_count_24h,
            alert_count_7d=alert_count_7d,
            flap_count_1h=flap_count,
            auto_resolve_rate_7d=auto_resolve_rate,
            avg_resolution_time_seconds=avg_resolution_time,
            service_alert_count_1h=service_alert_count_1h,
            service_incident_count_24h=0,  # Would need incident tracking
            hour_of_day=hour_of_day,
            day_of_week=day_of_week,
            is_business_hours=is_business_hours,
            is_weekend=is_weekend,
            deployment_in_progress=await self.is_deployment_in_progress(service),
            maintenance_active=await self.is_in_maintenance(service),
        )

    async def get_stats(self) -> dict[str, Any]:
        """Get feature store statistics."""
        stats: dict[str, Any]

        if self.redis is None:
            stats = {
                "metric_keys": len(self._metric_history),
                "total_metrics": sum(len(v) for v in self._metric_history.values()),
                "alert_fingerprints": len(self._alert_history),
                "total_alerts": sum(len(v) for v in self._alert_history.values()),
                "active_deployments": len(self._deployments),
                "maintenance_windows": len(self._maintenance_windows),
                "counts_truncated": False,
            }
        else:
            with self._redis_errors("reading feature store statistics"):
                metric_keys, total_metrics, metrics_truncated = await self._count_index(
                    self._key("metric_keys")
                )
                alert_keys, total_alerts, alerts_truncated = await self._count_index(
                    self._key("alert_keys")
                )
                deployments = await self.redis.hlen(self._key("deployments"))
                maintenance = await self.redis.zcard(self._key("maintenance"))

            stats = {
                "metric_keys": metric_keys,
                "total_metrics": total_metrics,
                "alert_fingerprints": alert_keys,
                "total_alerts": total_alerts,
                "active_deployments": int(deployments or 0),
                "maintenance_windows": int(maintenance or 0),
                "counts_truncated": metrics_truncated or alerts_truncated,
            }

        stats["durable"] = self.is_durable
        stats["storage"] = self.storage_description
        return stats

    async def clear(self) -> None:
        """Clear all stored data for this org."""
        self._metric_history.clear()
        self._alert_history.clear()
        self._service_alerts.clear()
        self._deployments.clear()
        self._maintenance_windows.clear()

        if self.redis is None:
            return

        with self._redis_errors("clearing the feature store"):
            for index_name in ("metric_keys", "alert_keys", "service_keys"):
                index = self._key(index_name)
                members = await self.redis.smembers(index)
                for member in members or []:
                    await self.redis.delete(_as_text(member))
                await self.redis.delete(index)
            await self.redis.delete(self._key("deployments"))
            await self.redis.delete(self._key("maintenance"))

    # ------------------------------------------------------------------
    # Backend-agnostic accessors
    # ------------------------------------------------------------------

    async def _metric_values(
        self, service: str, metric_name: str, hours: int
    ) -> list[float]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        if self.redis is None:
            points = self._metric_history.get(f"{service}:{metric_name}", [])
            return [p.value for p in points if p.timestamp > cutoff]

        with self._redis_errors("reading metric history"):
            raw = await self.redis.zrangebyscore(
                self._metric_key(service, metric_name), cutoff.timestamp(), "+inf"
            )

        values: list[float] = []
        for member in raw or []:
            try:
                values.append(float(json.loads(_as_text(member))["v"]))
            except Exception as e:  # noqa: BLE001 - one bad member is not fatal
                log.debug("Skipping unreadable metric sample: %s", e)
        return values

    async def _alert_records(
        self, fingerprint: str, since: datetime
    ) -> list[dict[str, Any]]:
        """Alert records newer than `since`, timestamps as aware datetimes."""
        if self.redis is None:
            return [
                a
                for a in self._alert_history.get(fingerprint, [])
                if a["timestamp"] > since
            ]

        with self._redis_errors("reading alert history"):
            raw = await self.redis.zrangebyscore(
                self._alert_key(fingerprint), since.timestamp(), "+inf"
            )

        records: list[dict[str, Any]] = []
        for member in raw or []:
            try:
                record = json.loads(_as_text(member))
                record["timestamp"] = datetime.fromtimestamp(
                    float(record["timestamp"]), tz=timezone.utc
                )
                records.append(record)
            except Exception as e:  # noqa: BLE001 - one bad member is not fatal
                log.debug("Skipping unreadable alert record: %s", e)
        return records

    async def _service_alert_count(self, service: str, since: datetime) -> int:
        if self.redis is None:
            return sum(
                1 for t in self._service_alerts.get(service, []) if t > since
            )

        with self._redis_errors("counting service alerts"):
            count = await self.redis.zcount(
                self._service_alert_key(service), since.timestamp(), "+inf"
            )
        return int(count or 0)

    async def _deployment_started(self, service: str) -> Optional[datetime]:
        if self.redis is None:
            return self._deployments.get(service)

        with self._redis_errors("reading deployment state"):
            raw = await self.redis.hget(self._key("deployments"), service)

        if raw is None:
            return None
        try:
            return datetime.fromtimestamp(float(_as_text(raw)), tz=timezone.utc)
        except (TypeError, ValueError) as e:
            log.debug("Unreadable deployment marker for %s: %s", service, e)
            return None

    async def _active_maintenance_windows(
        self, now: datetime
    ) -> list[dict[str, Any]]:
        """Windows that have started and have not yet ended."""
        if self.redis is None:
            return [
                w
                for w in self._maintenance_windows
                if w["start"] <= now <= w["end"]
            ]

        with self._redis_errors("reading maintenance windows"):
            raw = await self.redis.zrangebyscore(
                self._key("maintenance"), now.timestamp(), "+inf"
            )

        windows: list[dict[str, Any]] = []
        for member in raw or []:
            try:
                window = json.loads(_as_text(member))
                if float(window["start"]) <= now.timestamp():
                    windows.append(window)
            except Exception as e:  # noqa: BLE001 - one bad member is not fatal
                log.debug("Skipping unreadable maintenance window: %s", e)
        return windows

    async def _count_index(self, index: str) -> tuple[int, int, bool]:
        """(key count, total members, truncated) for an index set of zset keys."""
        members = await self.redis.smembers(index)
        keys = sorted(_as_text(m) for m in members or [])
        truncated = len(keys) > STATS_KEY_LIMIT

        total = 0
        for key in keys[:STATS_KEY_LIMIT]:
            count = int(await self.redis.zcard(key) or 0)
            if count == 0:
                # The data expired; drop the dangling index entry.
                await self.redis.srem(index, key)
                continue
            total += count

        live_keys = len(keys) if truncated else int(await self.redis.scard(index) or 0)
        return live_keys, total, truncated

    async def _prune_deployments(self, key: str) -> None:
        """Drop deployment markers too old to matter."""
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(hours=DEPLOYMENT_RETENTION_HOURS)
        ).timestamp()

        entries = await self.redis.hgetall(key)
        stale = []
        for field_name, value in (entries or {}).items():
            try:
                if float(_as_text(value)) < cutoff:
                    stale.append(_as_text(field_name))
            except (TypeError, ValueError):
                stale.append(_as_text(field_name))

        if stale:
            await self.redis.hdel(key, *stale)

    async def _calculate_stats(
        self, service: str, metric_name: str, hours: int
    ) -> dict[str, float]:
        """Calculate statistics for a metric over a time window."""
        values = await self._metric_values(service, metric_name, hours)

        if not values:
            return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0, "count": 0.0}

        return {
            "mean": statistics.mean(values),
            "stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
            "count": float(len(values)),
        }

    # ------------------------------------------------------------------
    # In-memory retention
    # ------------------------------------------------------------------

    def _cleanup_old_data(self, key: str) -> None:
        """Remove data older than retention period."""
        cutoff = self._retention_cutoff()
        self._metric_history[key] = [
            p for p in self._metric_history.get(key, [])
            if p.timestamp > cutoff
        ]

    def _cleanup_alert_history(self, fingerprint: str) -> None:
        """Remove old alert history."""
        cutoff = self._retention_cutoff()
        self._alert_history[fingerprint] = [
            a for a in self._alert_history.get(fingerprint, [])
            if a["timestamp"] > cutoff
        ]

    def _cleanup_service_alerts(self, service: str) -> None:
        """Remove old service alert timestamps."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=SERVICE_ALERT_RETENTION_HOURS
        )
        self._service_alerts[service] = [
            t for t in self._service_alerts.get(service, [])
            if t > cutoff
        ]
