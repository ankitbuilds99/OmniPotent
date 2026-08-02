"""Usage analytics tracking for feature insights."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from dataclasses import dataclass, field

log = logging.getLogger("omnipotent.analytics.usage")


@dataclass
class FeatureUsage:
    """Track usage of a specific feature."""
    name: str
    count: int = 0
    last_used: datetime | None = None
    users: set[str] = field(default_factory=set)
    metadata: dict[str, int] = field(default_factory=dict)


@dataclass
class SessionMetrics:
    """Metrics for a user session."""
    session_id: str
    user_id: str | None
    started_at: datetime
    ended_at: datetime | None = None
    page_views: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    queries: int = 0
    exports: int = 0
    errors: int = 0


class UsageTracker:
    """Track feature usage and user behavior for analytics."""

    def __init__(self) -> None:
        self._features: dict[str, FeatureUsage] = {}
        self._sessions: dict[str, SessionMetrics] = {}
        self._daily_active_users: dict[str, set[str]] = defaultdict(set)
        self._hourly_queries: dict[str, int] = defaultdict(int)
        self._error_counts: dict[str, int] = defaultdict(int)

    def _today(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d")

    def _hour(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d-%H")

    # Feature tracking
    def track_feature(
        self,
        feature: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track usage of a feature."""
        if feature not in self._features:
            self._features[feature] = FeatureUsage(name=feature)

        f = self._features[feature]
        f.count += 1
        f.last_used = datetime.utcnow()

        if user_id:
            f.users.add(user_id)
            self._daily_active_users[self._today()].add(user_id)

        if metadata:
            for key, value in metadata.items():
                if isinstance(value, (int, float)):
                    f.metadata[key] = f.metadata.get(key, 0) + int(value)

    def get_feature_stats(self, feature: str) -> dict[str, Any] | None:
        """Get stats for a specific feature."""
        f = self._features.get(feature)
        if not f:
            return None

        return {
            "name": f.name,
            "total_uses": f.count,
            "unique_users": len(f.users),
            "last_used": f.last_used.isoformat() if f.last_used else None,
            "metadata": f.metadata,
        }

    def get_all_features(self) -> list[dict[str, Any]]:
        """Get stats for all tracked features."""
        return [
            {
                "name": f.name,
                "total_uses": f.count,
                "unique_users": len(f.users),
                "last_used": f.last_used.isoformat() if f.last_used else None,
            }
            for f in sorted(
                self._features.values(), key=lambda x: x.count, reverse=True
            )
        ]

    # Session tracking
    def start_session(self, session_id: str, user_id: str | None = None) -> None:
        """Start tracking a new session."""
        self._sessions[session_id] = SessionMetrics(
            session_id=session_id,
            user_id=user_id,
            started_at=datetime.utcnow(),
        )
        if user_id:
            self._daily_active_users[self._today()].add(user_id)

    def end_session(self, session_id: str) -> SessionMetrics | None:
        """End a session and return its metrics."""
        session = self._sessions.get(session_id)
        if session:
            session.ended_at = datetime.utcnow()
        return session

    def track_page_view(self, session_id: str, page: str) -> None:
        """Track a page view in a session."""
        session = self._sessions.get(session_id)
        if session:
            session.page_views.append(page)
            self.track_feature(f"page:{page}", session.user_id)

    def track_action(
        self,
        session_id: str,
        action: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track a user action in a session."""
        session = self._sessions.get(session_id)
        if session:
            session.actions.append({
                "action": action,
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": metadata or {},
            })
            self.track_feature(f"action:{action}", session.user_id, metadata)

    # Query tracking
    def track_query(self, user_id: str | None = None, tokens: int = 0) -> None:
        """Track an investigation query."""
        self._hourly_queries[self._hour()] += 1
        self.track_feature("query", user_id, {"tokens": tokens})

    def track_export(self, format: str, user_id: str | None = None) -> None:
        """Track a report export."""
        self.track_feature(f"export:{format}", user_id)
        self.track_feature("export:total", user_id)

    def track_error(self, error_type: str, user_id: str | None = None) -> None:
        """Track an error occurrence."""
        self._error_counts[error_type] += 1
        self.track_feature(f"error:{error_type}", user_id)

    # Analytics queries
    def get_daily_active_users(self, days: int = 7) -> dict[str, int]:
        """Get DAU for the past N days."""
        result = {}
        today = datetime.utcnow().date()
        for i in range(days):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            result[date] = len(self._daily_active_users.get(date, set()))
        return result

    def get_hourly_queries(self, hours: int = 24) -> dict[str, int]:
        """Get query count per hour for the past N hours."""
        result = {}
        now = datetime.utcnow()
        for i in range(hours):
            hour = (now - timedelta(hours=i)).strftime("%Y-%m-%d-%H")
            result[hour] = self._hourly_queries.get(hour, 0)
        return result

    def get_top_features(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get most used features."""
        features = sorted(
            self._features.values(), key=lambda x: x.count, reverse=True
        )[:limit]
        return [
            {
                "name": f.name,
                "count": f.count,
                "unique_users": len(f.users),
            }
            for f in features
        ]

    def get_error_summary(self) -> dict[str, int]:
        """Get error counts by type."""
        return dict(self._error_counts)

    def get_summary(self) -> dict[str, Any]:
        """Get overall analytics summary."""
        total_queries = sum(self._hourly_queries.values())
        total_users = set()
        for users in self._daily_active_users.values():
            total_users.update(users)

        return {
            "total_queries": total_queries,
            "total_unique_users": len(total_users),
            "dau_today": len(self._daily_active_users.get(self._today(), set())),
            "top_features": self.get_top_features(5),
            "error_count": sum(self._error_counts.values()),
        }


# Global tracker instance
_tracker: UsageTracker | None = None


def get_usage_tracker() -> UsageTracker:
    """Get the global usage tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker


# Convenience functions
def track(feature: str, user_id: str | None = None, **metadata: Any) -> None:
    """Track a feature usage event."""
    get_usage_tracker().track_feature(feature, user_id, metadata if metadata else None)


def track_query(user_id: str | None = None, tokens: int = 0) -> None:
    """Track an investigation query."""
    get_usage_tracker().track_query(user_id, tokens)


def track_export(format: str, user_id: str | None = None) -> None:
    """Track a report export."""
    get_usage_tracker().track_export(format, user_id)


def track_error(error_type: str, user_id: str | None = None) -> None:
    """Track an error occurrence."""
    get_usage_tracker().track_error(error_type, user_id)
