"""Layer 6: Policy-based suppression engine."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.deduplication.models import (
    RawAlert,
    SuppressionRule,
    SuppressionType,
    MaintenanceWindow,
    KnownIssue,
    FlappingAlert,
)

log = logging.getLogger("omnipotent.deduplication.suppression")

# Whether a freshly constructed `SuppressionEngine` has anywhere to send a digest.
#
# It does not, and cannot decide otherwise on its own: the transport in
# `app/notifications/delivery.py` is async and per-organization, while
# `check_suppression` runs synchronously inside `DeduplicationPipeline.process_alert`
# and knows nothing about the tenant. So the connection is made from the outside —
# `set_digest_sink()` hands the engine an async callable, and the async caller that
# already resolved the org (`app/deduplication/api_router.py`) awaits
# `flush_digests()`. An engine with a sink attached reports `digest_delivery: wired`
# and counts what it actually delivered; one without keeps reporting the backlog as
# undelivered, which is the truth for a pipeline nobody connected.
#
# Either way a rule whose action is "digest" never drops its alert: it accumulates a
# digest entry and lets the alert continue through the correlation layers, so the
# alert still lands on an incident the operator can see.
DIGEST_DELIVERY_WIRED = False

#: What `set_digest_sink` accepts: given the digest groups whose window has closed,
#: say whether they were handed off. Returning False (or raising) puts them back in
#: the buffer, because a transport that did not take them has not made them
#: somebody else's problem yet.
DigestSink = Callable[[list["DigestEntry"]], Awaitable[bool]]


@dataclass
class SuppressionConfig:
    """Configuration for suppression engine."""

    flapping_window_minutes: int = 30
    flapping_min_state_changes: int = 3
    flapping_hysteresis_minutes: int = 5
    max_suppress_duration_hours: int = 24
    auto_suppress_after_n_alerts: int = 3
    digest_interval_minutes: int = 60


@dataclass
class SuppressionResult:
    """Result of suppression check."""

    should_suppress: bool
    reason: str
    rule_id: UUID | None = None
    rule_name: str | None = None
    suppression_type: SuppressionType | None = None
    expires_at: datetime | None = None


class FlappingDetector:
    """Detect and handle flapping alerts."""

    def __init__(self, config: SuppressionConfig | None = None):
        self.config = config or SuppressionConfig()
        self._alert_states: dict[str, FlappingAlert] = {}

    def record_state_change(
        self,
        fingerprint: str,
        timestamp: datetime,
        state: str,
        service: str = "",
        alert_name: str = "",
    ) -> FlappingAlert:
        """Record a state change for an alert."""
        if fingerprint not in self._alert_states:
            self._alert_states[fingerprint] = FlappingAlert(
                alert_fingerprint=fingerprint,
                service=service,
                alert_name=alert_name,
                first_seen=timestamp,
            )

        flapping = self._alert_states[fingerprint]
        flapping.add_state_change(timestamp, state)

        if not flapping.service and service:
            flapping.service = service
        if not flapping.alert_name and alert_name:
            flapping.alert_name = alert_name

        return flapping

    def is_flapping(self, fingerprint: str) -> bool:
        """Check if alert is currently flapping."""
        flapping = self._alert_states.get(fingerprint)
        return flapping.is_flapping if flapping else False

    def get_flapping_alert(self, fingerprint: str) -> FlappingAlert | None:
        """Get flapping alert state."""
        return self._alert_states.get(fingerprint)

    def suppress_flapping(
        self,
        fingerprint: str,
        duration_minutes: int = 60,
    ) -> bool:
        """Suppress a flapping alert."""
        flapping = self._alert_states.get(fingerprint)
        if not flapping:
            return False

        flapping.suppressed_until = datetime.utcnow() + timedelta(
            minutes=duration_minutes
        )
        return True

    def is_suppressed(self, fingerprint: str) -> bool:
        """Check if flapping alert is suppressed."""
        flapping = self._alert_states.get(fingerprint)
        if not flapping or not flapping.suppressed_until:
            return False
        return datetime.utcnow() < flapping.suppressed_until

    def get_all_flapping(self) -> list[FlappingAlert]:
        """Get all currently flapping alerts."""
        return [f for f in self._alert_states.values() if f.is_flapping]

    def suggest_threshold_adjustment(
        self,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        """Suggest threshold adjustment for flapping alert."""
        flapping = self._alert_states.get(fingerprint)
        if not flapping or not flapping.is_flapping:
            return None

        return {
            "alert_fingerprint": fingerprint,
            "current_pattern": flapping.pattern_description,
            "suggestion": "Consider increasing threshold or adding hysteresis",
            "flapping_score": flapping.flapping_score,
        }

    def cleanup_old_entries(self, before: datetime) -> int:
        """Remove old entries."""
        to_remove = [
            fp
            for fp, f in self._alert_states.items()
            if f.last_seen < before and not f.is_flapping
        ]
        for fp in to_remove:
            del self._alert_states[fp]
        return len(to_remove)


class MaintenanceWindowManager:
    """Manage maintenance windows."""

    def __init__(self):
        self._windows: dict[UUID, MaintenanceWindow] = {}
        self._recurring_windows: list[MaintenanceWindow] = []

    def create_window(
        self,
        name: str,
        services: list[str],
        starts_at: datetime,
        ends_at: datetime,
        description: str = "",
        regions: list[str] | None = None,
        created_by: str | None = None,
    ) -> MaintenanceWindow:
        """Create a maintenance window."""
        window = MaintenanceWindow(
            name=name,
            description=description,
            services=services,
            regions=regions or [],
            starts_at=starts_at,
            ends_at=ends_at,
            created_by=created_by,
        )
        self._windows[window.window_id] = window
        log.info("Created maintenance window: %s", name)
        return window

    def create_recurring_window(
        self,
        name: str,
        services: list[str],
        recurrence_pattern: str,
        duration_minutes: int,
        description: str = "",
        created_by: str | None = None,
    ) -> MaintenanceWindow:
        """Create a recurring maintenance window."""
        now = datetime.utcnow()
        window = MaintenanceWindow(
            name=name,
            description=description,
            services=services,
            starts_at=now,
            ends_at=now + timedelta(minutes=duration_minutes),
            created_by=created_by,
            is_recurring=True,
            recurrence_pattern=recurrence_pattern,
        )
        self._recurring_windows.append(window)
        log.info("Created recurring maintenance window: %s", name)
        return window

    def get_active_windows(
        self,
        at_time: datetime | None = None,
    ) -> list[MaintenanceWindow]:
        """Get all active maintenance windows."""
        now = at_time or datetime.utcnow()
        active = [w for w in self._windows.values() if w.is_active(now)]

        for recurring in self._recurring_windows:
            if self._is_recurring_active(recurring, now):
                active.append(recurring)

        return active

    def _is_recurring_active(
        self,
        window: MaintenanceWindow,
        at_time: datetime,
    ) -> bool:
        """Check if recurring window is active at given time."""
        pattern = window.recurrence_pattern
        if not pattern:
            return False

        if pattern == "daily":
            start_time = window.starts_at.time()
            end_time = window.ends_at.time()
            current_time = at_time.time()
            return start_time <= current_time <= end_time

        if pattern == "weekly":
            if at_time.weekday() == window.starts_at.weekday():
                start_time = window.starts_at.time()
                end_time = window.ends_at.time()
                current_time = at_time.time()
                return start_time <= current_time <= end_time

        return False

    def is_service_in_maintenance(
        self,
        service: str,
        at_time: datetime | None = None,
    ) -> tuple[bool, MaintenanceWindow | None]:
        """Check if a service is in maintenance."""
        active_windows = self.get_active_windows(at_time)

        for window in active_windows:
            if window.matches_service(service):
                return True, window

        return False, None

    def end_window_early(self, window_id: UUID) -> bool:
        """End a maintenance window early."""
        window = self._windows.get(window_id)
        if not window:
            return False

        window.ended_early = True
        log.info("Ended maintenance window early: %s", window.name)
        return True

    def extend_window(
        self,
        window_id: UUID,
        additional_minutes: int,
    ) -> bool:
        """Extend a maintenance window."""
        window = self._windows.get(window_id)
        if not window:
            return False

        window.extended_until = window.ends_at + timedelta(minutes=additional_minutes)
        log.info(
            "Extended maintenance window %s by %d minutes",
            window.name,
            additional_minutes,
        )
        return True

    def get_window(self, window_id: UUID) -> MaintenanceWindow | None:
        """Get maintenance window by ID."""
        return self._windows.get(window_id)

    def get_scheduled_windows(self) -> list[MaintenanceWindow]:
        """Get all scheduled (future) windows."""
        now = datetime.utcnow()
        return [w for w in self._windows.values() if w.starts_at > now]

    def delete_window(self, window_id: UUID) -> bool:
        """Delete a maintenance window."""
        if window_id in self._windows:
            del self._windows[window_id]
            return True
        return False


class KnownIssueTracker:
    """Track known issues for alert suppression."""

    def __init__(self):
        self._issues: dict[UUID, KnownIssue] = {}

    def create_issue(
        self,
        title: str,
        alert_pattern: dict[str, Any],
        description: str = "",
        jira_ticket: str | None = None,
        vendor_status_url: str | None = None,
        expires_at: datetime | None = None,
        created_by: str | None = None,
    ) -> KnownIssue:
        """Create a known issue."""
        issue = KnownIssue(
            title=title,
            description=description,
            alert_pattern=alert_pattern,
            jira_ticket=jira_ticket,
            vendor_status_url=vendor_status_url,
            expires_at=expires_at,
            created_by=created_by,
        )
        self._issues[issue.issue_id] = issue
        log.info("Created known issue: %s", title)
        return issue

    def find_matching_issue(
        self,
        alert: RawAlert,
    ) -> KnownIssue | None:
        """Find known issue matching an alert."""
        for issue in self._issues.values():
            if not issue.is_active():
                continue

            if self._matches_pattern(alert, issue.alert_pattern):
                return issue

        return None

    def _matches_pattern(
        self,
        alert: RawAlert,
        pattern: dict[str, Any],
    ) -> bool:
        """Check if alert matches pattern."""
        for key, value in pattern.items():
            alert_value = getattr(alert, key, None)
            if alert_value is None:
                alert_value = alert.labels.get(key)

            if alert_value is None:
                return False

            if hasattr(alert_value, "value"):
                alert_value = alert_value.value

            if isinstance(value, str):
                if value.startswith("contains:"):
                    if value[9:] not in str(alert_value):
                        return False
                elif value.startswith("regex:"):
                    if not re.match(value[6:], str(alert_value)):
                        return False
                elif str(alert_value) != value:
                    return False
            elif isinstance(value, list):
                if str(alert_value) not in value:
                    return False
            elif alert_value != value:
                return False

        return True

    def acknowledge_issue(
        self,
        issue_id: UUID,
        user: str,
    ) -> bool:
        """Acknowledge a known issue."""
        issue = self._issues.get(issue_id)
        if not issue:
            return False

        if user not in issue.acknowledged_by:
            issue.acknowledged_by.append(user)
        return True

    def extend_issue(
        self,
        issue_id: UUID,
        additional_days: int,
    ) -> bool:
        """Extend known issue expiration."""
        issue = self._issues.get(issue_id)
        if not issue:
            return False

        current_expiry = issue.expires_at or datetime.utcnow()
        issue.expires_at = current_expiry + timedelta(days=additional_days)
        return True

    def close_issue(self, issue_id: UUID) -> bool:
        """Close a known issue."""
        if issue_id in self._issues:
            del self._issues[issue_id]
            return True
        return False

    def get_issue(self, issue_id: UUID) -> KnownIssue | None:
        """Get known issue by ID."""
        return self._issues.get(issue_id)

    def get_active_issues(self) -> list[KnownIssue]:
        """Get all active known issues."""
        return [i for i in self._issues.values() if i.is_active()]

    def get_expiring_soon(self, days: int = 3) -> list[KnownIssue]:
        """Get issues expiring within specified days."""
        cutoff = datetime.utcnow() + timedelta(days=days)
        return [
            i
            for i in self._issues.values()
            if i.expires_at and i.expires_at < cutoff and i.is_active()
        ]


@dataclass
class BusinessRule:
    """Business rule for alert suppression."""

    rule_id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    condition: str = ""
    action: str = "suppress"
    parameters: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)


class BusinessRuleEngine:
    """Engine for business-rule based suppression."""

    def __init__(self, clock: Callable[[], datetime] | None = None):
        """`clock` supplies the moment conditions are evaluated against.

        Two of the shipped rules read the calendar — `hour BETWEEN 23 AND 7` and
        `day IN (sat, sun)` — so what this engine answers depends on when it is
        asked. That is correct in production and intolerable in a test: a test that
        reads the wall clock asserts a different thing on Saturday than on Tuesday,
        which is how a suite goes green in CI and red in production week. Callers
        that need a stable answer pass their own clock; everything else gets
        `datetime.utcnow` and behaves exactly as before.
        """
        self.clock = clock or datetime.utcnow
        self._rules: list[BusinessRule] = []
        self._unsupported_conditions: set[str] = set()
        self._load_default_rules()

    def _load_default_rules(self) -> None:
        """Load default business rules.

        The repetition rule used to read `alert_count > 3 AND window = 1h` with
        `action="suppress"` and `digest_instead=True`, and nothing ever read
        `digest_instead`. Because Layer 6 runs before every correlation layer, that
        rule dropped the 4th and every later occurrence of *any* alert in a rolling
        hour — criticals included — with no incident, no digest and no way for an
        operator to turn it off. Worse, the pipeline records an occurrence for
        suppressed alerts too, so a continuously firing alert could never fall back
        under the threshold, and a critical that re-fired after its incident was
        resolved stayed invisible.

        Repetition is now a digest rule, not a drop, and it excludes critical and
        high severity outright. Exact repeats are already folded into one incident
        by Layer 1 fingerprint matching, which is where deduplication belongs; the
        only thing outright suppression added here was lost signal.
        """
        self._rules.extend([
            BusinessRule(
                name="Digest repeated non-critical alerts",
                description=(
                    "Collect non-critical alerts into a digest after 3 repeats in 1 hour"
                ),
                condition=(
                    "severity NOT IN (critical, high) AND alert_count > 3 AND window = 1h"
                ),
                action="digest",
                parameters={"digest_interval_hours": 1},
            ),
            BusinessRule(
                name="Low severity night suppression",
                description="Suppress low severity alerts during night hours",
                condition="severity = low AND hour BETWEEN 23 AND 7",
                action="suppress",
                parameters={"notify_channel": "low-priority"},
            ),
            BusinessRule(
                name="Weekend non-critical suppression",
                description="Reduce noise for non-critical alerts on weekends",
                condition="severity NOT IN (critical, high) AND day IN (sat, sun)",
                action="digest",
                parameters={"digest_interval_hours": 4},
            ),
        ])

    def add_rule(self, rule: BusinessRule) -> None:
        """Add a business rule."""
        self._rules.append(rule)

    def remove_rule(self, rule_id: UUID) -> bool:
        """Remove a business rule."""
        for i, rule in enumerate(self._rules):
            if rule.rule_id == rule_id:
                del self._rules[i]
                return True
        return False

    def evaluate(
        self,
        alert: RawAlert,
        context: dict[str, Any],
    ) -> tuple[BusinessRule | None, str]:
        """Evaluate rules against an alert."""
        for rule in self._rules:
            if not rule.enabled:
                continue

            if self._evaluate_condition(rule.condition, alert, context):
                return rule, rule.action

        return None, ""

    def _evaluate_condition(
        self,
        condition: str,
        alert: RawAlert,
        context: dict[str, Any],
    ) -> bool:
        """Evaluate a rule condition, failing closed on anything unrecognised.

        The previous implementation scanned for a handful of substrings and
        returned True at the end, so a condition it did not understand matched
        every alert. `severity NOT IN (critical, high)` was one of those: the
        clause was written into a shipped rule and silently ignored, which is
        exactly how a rule about non-critical noise came to match criticals. An
        empty condition matched everything too, so `BusinessRule(action="suppress")`
        built without a condition suppressed the whole alert stream.

        Every clause must now be understood and satisfied for the rule to match.
        An unparseable clause makes the rule not match at all — the direction that
        loses no alerts — and is logged once so the bad rule is visible rather than
        quietly over-broad.
        """
        now = self.clock()
        variables: dict[str, Any] = {
            "severity": alert.severity.value.lower(),
            "service": (alert.service or "").lower(),
            "hour": now.hour,
            "day": now.strftime("%a").lower(),
            "alert_count": context.get("alert_count", 1),
            "window": str(context.get("window", "1h")).lower(),
        }

        normalized = " ".join(condition.lower().split())
        if not normalized:
            self._warn_unsupported(condition, "empty condition")
            return False

        for clause in self._split_clauses(normalized):
            result = self._evaluate_clause(clause, variables)
            if result is None:
                self._warn_unsupported(condition, clause)
                return False
            if not result:
                return False

        return True

    @staticmethod
    def _split_clauses(condition: str) -> list[str]:
        """Split a normalised condition on AND.

        `hour between 23 and 7` carries its own `and`, so the range is collapsed to
        a single token before the conjunction split and expanded again by the
        clause matcher.
        """
        protected = re.sub(
            r"between (\d+) and (\d+)",
            lambda m: f"between {m.group(1)}..{m.group(2)}",
            condition,
        )
        return [c.strip() for c in re.split(r"\band\b", protected) if c.strip()]

    @staticmethod
    def _evaluate_clause(clause: str, variables: dict[str, Any]) -> bool | None:
        """Evaluate one clause. None means "not understood", never "no match"."""
        match = re.fullmatch(r"(severity|service|window) (?:=|==) ([\w.:-]+)", clause)
        if match:
            return str(variables[match.group(1)]) == match.group(2)

        match = re.fullmatch(r"(severity|service|window) (?:!=|<>) ([\w.:-]+)", clause)
        if match:
            return str(variables[match.group(1)]) != match.group(2)

        match = re.fullmatch(
            r"(severity|service|day) (not )?in \(([^)]*)\)", clause
        )
        if match:
            members = {m.strip() for m in match.group(3).split(",") if m.strip()}
            present = str(variables[match.group(1)]) in members
            return not present if match.group(2) else present

        match = re.fullmatch(r"hour between (\d+)\.\.(\d+)", clause)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            hour = int(variables["hour"])
            if start > end:
                return hour >= start or hour <= end
            return start <= hour <= end

        match = re.fullmatch(r"alert_count (>|>=|<|<=|=|==) (\d+)", clause)
        if match:
            operator, threshold = match.group(1), int(match.group(2))
            count = int(variables["alert_count"])
            return {
                ">": count > threshold,
                ">=": count >= threshold,
                "<": count < threshold,
                "<=": count <= threshold,
                "=": count == threshold,
                "==": count == threshold,
            }[operator]

        return None

    def _warn_unsupported(self, condition: str, clause: str) -> None:
        """Log an unparseable rule clause once, not once per alert."""
        if condition in self._unsupported_conditions:
            return
        self._unsupported_conditions.add(condition)
        log.warning(
            "Business rule condition %r has a clause this engine cannot evaluate "
            "(%r); the rule will never match. Fix the condition or remove the rule.",
            condition,
            clause,
        )

    def get_all_rules(self) -> list[BusinessRule]:
        """Get all business rules."""
        return self._rules.copy()


@dataclass
class DigestEntry:
    """A repeating alert accumulated for a digest rather than dropped."""

    fingerprint: str
    rule_name: str
    service: str
    alert_name: str
    severity: str
    count: int
    first_seen: datetime
    last_seen: datetime
    #: How long the rule wants this group collected for before it is sent. Taken
    #: from the rule's `digest_interval_hours`, falling back to the engine's
    #: `digest_interval_minutes`. Without it every drain would send groups of one
    #: and the "digest" would be the alert stream again, one message at a time.
    interval_minutes: int = 60

    def is_due(self, now: datetime) -> bool:
        """Whether this group's collection window has closed."""
        return now - self.first_seen >= timedelta(minutes=self.interval_minutes)

    def summary(self) -> str:
        """One line an operator can read in a digest message."""
        return (
            f"{self.service}: {self.alert_name} ({self.severity}) "
            f"x{self.count} since {self.first_seen.isoformat(timespec='seconds')}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "fingerprint": self.fingerprint,
            "rule_name": self.rule_name,
            "service": self.service,
            "alert_name": self.alert_name,
            "severity": self.severity,
            "count": self.count,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "interval_minutes": self.interval_minutes,
        }


class SuppressionEngine:
    """Main suppression engine combining all suppression sources."""

    def __init__(
        self,
        config: SuppressionConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        """`clock` is threaded through so the whole engine can be asked "what would
        you decide at this moment" instead of only "what do you decide now".

        The business rules read the hour and the weekday, and the digest buffer
        opens and closes windows against the same clock, so a test that cannot move
        the clock can only assert whatever today happens to be.
        """
        self.config = config or SuppressionConfig()
        self._clock = clock or datetime.utcnow
        self._custom_rules: dict[UUID, SuppressionRule] = {}
        self._flapping_detector = FlappingDetector(config)
        self._maintenance_manager = MaintenanceWindowManager()
        self._known_issues = KnownIssueTracker()
        self._business_rules = BusinessRuleEngine(self._clock)
        self._alert_counts: dict[str, list[datetime]] = {}
        self._digests: dict[str, DigestEntry] = {}
        self._digest_warned: set[str] = set()
        self._digest_sink: DigestSink | None = None
        self._digest_wired = DIGEST_DELIVERY_WIRED
        self._digest_groups_delivered = 0
        self._digest_alerts_delivered = 0
        self._digest_delivery_failures = 0
        self._last_digest_delivery_error: str | None = None

    def set_clock(self, clock: Callable[[], datetime]) -> None:
        """Point the engine and its business rules at the same clock.

        One call rather than two attributes, because a rule evaluated at one moment
        and a digest window opened at another is a bug waiting to be written.
        """
        self._clock = clock
        self._business_rules.clock = clock

    def check_suppression(
        self,
        alert: RawAlert,
        fingerprint: str,
        context: dict[str, Any] | None = None,
    ) -> SuppressionResult:
        """Check if alert should be suppressed."""
        context = context or {}

        in_maintenance, window = self._maintenance_manager.is_service_in_maintenance(
            alert.service
        )
        if in_maintenance and window:
            window.alerts_suppressed += 1
            return SuppressionResult(
                should_suppress=True,
                reason=f"Service in maintenance: {window.name}",
                rule_id=window.window_id,
                rule_name=window.name,
                suppression_type=SuppressionType.MAINTENANCE,
                expires_at=window.extended_until or window.ends_at,
            )

        known_issue = self._known_issues.find_matching_issue(alert)
        if known_issue:
            known_issue.alerts_suppressed += 1
            return SuppressionResult(
                should_suppress=True,
                reason=f"Known issue: {known_issue.title}",
                rule_id=known_issue.issue_id,
                rule_name=known_issue.title,
                suppression_type=SuppressionType.KNOWN_ISSUE,
                expires_at=known_issue.expires_at,
            )

        if self._flapping_detector.is_suppressed(fingerprint):
            return SuppressionResult(
                should_suppress=True,
                reason="Flapping alert suppressed",
                suppression_type=SuppressionType.FLAPPING,
            )

        for rule in self._custom_rules.values():
            if rule.is_active() and rule.matches(alert):
                rule.alerts_suppressed += 1
                return SuppressionResult(
                    should_suppress=True,
                    reason=f"Custom rule: {rule.name}",
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    suppression_type=rule.suppression_type,
                    expires_at=rule.expires_at,
                )

        alert_count = self._get_alert_count(fingerprint)
        context["alert_count"] = alert_count

        business_rule, action = self._business_rules.evaluate(alert, context)
        if business_rule and action == "digest":
            # A digest rule collects the alert; it does not decide the alert never
            # happened. The alert keeps travelling through the pipeline so it still
            # joins (or opens) an incident, and the accumulated entry is what a
            # digest channel would send once one exists.
            self._record_digest(alert, fingerprint, business_rule)
            return SuppressionResult(
                should_suppress=False,
                reason=f"Collected for digest by business rule: {business_rule.name}",
                rule_id=business_rule.rule_id,
                rule_name=business_rule.name,
            )

        if business_rule and action == "suppress":
            return SuppressionResult(
                should_suppress=True,
                reason=f"Business rule: {business_rule.name}",
                rule_id=business_rule.rule_id,
                rule_name=business_rule.name,
                suppression_type=SuppressionType.BUSINESS_RULE,
            )

        return SuppressionResult(
            should_suppress=False,
            reason="No suppression rules matched",
        )

    def _record_digest(
        self,
        alert: RawAlert,
        fingerprint: str,
        rule: BusinessRule,
    ) -> None:
        """Accumulate a digest entry for a repeating alert."""
        now = self._clock()
        self.expire_digests()
        entry = self._digests.get(fingerprint)
        if entry is None:
            self._digests[fingerprint] = DigestEntry(
                fingerprint=fingerprint,
                rule_name=rule.name,
                service=alert.service,
                alert_name=alert.alert_name,
                severity=alert.severity.value,
                count=1,
                first_seen=now,
                last_seen=now,
                interval_minutes=self._interval_for(rule),
            )
        else:
            entry.count += 1
            entry.last_seen = now
            entry.rule_name = rule.name
            entry.interval_minutes = self._interval_for(rule)

        if not self._digest_wired and rule.name not in self._digest_warned:
            self._digest_warned.add(rule.name)
            log.warning(
                "Business rule %r asks for a digest, but nothing drains this "
                "engine's digest buffer into the notification digest queue. The "
                "alert is being passed through to correlation instead of dropped; "
                "pending digests are readable via pending_digests()/drain_digests(), "
                "and set_digest_sink() connects a transport.",
                rule.name,
            )

    def _interval_for(self, rule: BusinessRule) -> int:
        """How long `rule` wants its digest collected for, in minutes."""
        hours = rule.parameters.get("digest_interval_hours")
        try:
            minutes = int(float(hours) * 60) if hours is not None else 0
        except (TypeError, ValueError):
            log.warning(
                "Business rule %r has a non-numeric digest_interval_hours (%r); "
                "falling back to the engine default of %d minutes.",
                rule.name,
                hours,
                self.config.digest_interval_minutes,
            )
            minutes = 0
        return minutes if minutes > 0 else self.config.digest_interval_minutes

    def expire_digests(self, before: datetime | None = None) -> int:
        """Drop digest entries whose last occurrence predates `before`.

        Bounded growth belongs on the write path, which is why `_record_digest`
        calls this: a fingerprint that stopped firing should not sit in memory
        forever waiting for a digest channel that may never arrive.
        """
        cutoff = before or (self._clock() - timedelta(hours=24))
        stale = [fp for fp, e in self._digests.items() if e.last_seen < cutoff]
        for fp in stale:
            del self._digests[fp]
        return len(stale)

    def pending_digests(self) -> list[DigestEntry]:
        """Digest entries collected but not yet delivered.

        A pure read. This used to expire stale entries as a side effect, and
        `get_suppression_stats()` calls it, so `GET /deduplication/stats` — a
        request that promises to observe the engine and change nothing — silently
        destroyed part of the digest backlog. Whoever eventually delivers a digest
        must be the one to consume it, via `drain_digests()`; expiry is now the
        write path's job.
        """
        return list(self._digests.values())

    def drain_digests(self) -> list[DigestEntry]:
        """Return the pending digest entries and clear the buffer."""
        self.expire_digests()
        entries = list(self._digests.values())
        self._digests.clear()
        return entries

    def has_due_digests(self, now: datetime | None = None) -> bool:
        """Whether any group's collection window has closed.

        A cheap check the request path can make before reaching for a transport
        that costs a database round trip.
        """
        stamp = now or self._clock()
        return any(e.is_due(stamp) for e in self._digests.values())

    def drain_due_digests(self, now: datetime | None = None) -> list[DigestEntry]:
        """Remove and return only the groups whose window has closed.

        Draining everything on every alert would defeat the point of a digest —
        each group would be sent the instant it opened, one alert at a time, which
        is the alert stream the rule was written to batch. Groups still collecting
        stay in the buffer.
        """
        stamp = now or self._clock()
        due = [e for e in self._digests.values() if e.is_due(stamp)]
        for entry in due:
            self._digests.pop(entry.fingerprint, None)
        return due

    def restore_digests(self, entries: list[DigestEntry]) -> None:
        """Put drained groups back after a failed hand-off.

        A transport that refused them has not taken responsibility for them, and
        dropping them here would be the silent alert loss this engine exists to
        avoid. A group that fired again while it was out is merged rather than
        overwritten, so neither the older count nor the newer one is lost.
        """
        for entry in entries:
            current = self._digests.get(entry.fingerprint)
            if current is None:
                self._digests[entry.fingerprint] = entry
                continue
            current.count += entry.count
            current.first_seen = min(current.first_seen, entry.first_seen)
            current.last_seen = max(current.last_seen, entry.last_seen)

    def set_digest_sink(self, sink: DigestSink | None) -> None:
        """Connect (or disconnect) the transport `flush_digests` hands groups to.

        This is the seam the engine could not cross on its own: delivery is async
        and per-organization, `check_suppression` is neither. Once a sink is
        attached the engine stops describing its backlog as undelivered and starts
        counting what actually went out.
        """
        self._digest_sink = sink
        self._digest_wired = sink is not None or DIGEST_DELIVERY_WIRED

    async def flush_digests(self, now: datetime | None = None) -> dict[str, Any]:
        """Hand every due digest group to the attached sink.

        Never raises: a broken webhook must not fail the alert that triggered the
        flush. Anything the sink did not take is put back and counted as a failure
        with its reason, rather than reported as a successful delivery of nothing.
        """
        if self._digest_sink is None:
            return {"status": "not_wired", "groups": 0, "alerts": 0}

        due = self.drain_due_digests(now)
        if not due:
            return {"status": "nothing_due", "groups": 0, "alerts": 0}

        alerts = sum(e.count for e in due)
        try:
            delivered = await self._digest_sink(due)
        except Exception as exc:  # noqa: BLE001 — one failed digest is not fatal
            self.restore_digests(due)
            self._digest_delivery_failures += 1
            self._last_digest_delivery_error = f"{type(exc).__name__}: {exc}"
            log.warning("digest delivery raised; %d group(s) put back", len(due),
                        exc_info=True)
            return {
                "status": "failed",
                "groups": len(due),
                "alerts": alerts,
                "detail": self._last_digest_delivery_error,
            }

        if not delivered:
            self.restore_digests(due)
            self._digest_delivery_failures += 1
            self._last_digest_delivery_error = "the digest transport did not accept the groups"
            return {
                "status": "failed",
                "groups": len(due),
                "alerts": alerts,
                "detail": self._last_digest_delivery_error,
            }

        self._digest_groups_delivered += len(due)
        self._digest_alerts_delivered += alerts
        return {"status": "sent", "groups": len(due), "alerts": alerts}

    def _get_alert_count(self, fingerprint: str) -> int:
        """Get recent alert count for fingerprint."""
        if fingerprint not in self._alert_counts:
            self._alert_counts[fingerprint] = []

        now = self._clock()
        cutoff = now - timedelta(hours=1)
        self._alert_counts[fingerprint] = [
            t for t in self._alert_counts[fingerprint] if t > cutoff
        ]

        return len(self._alert_counts[fingerprint])

    def record_alert(self, fingerprint: str) -> None:
        """Record alert occurrence."""
        if fingerprint not in self._alert_counts:
            self._alert_counts[fingerprint] = []
        self._alert_counts[fingerprint].append(self._clock())

    def record_state_change(
        self,
        fingerprint: str,
        state: str,
        alert: RawAlert,
    ) -> FlappingAlert:
        """Record alert state change for flapping detection."""
        return self._flapping_detector.record_state_change(
            fingerprint=fingerprint,
            timestamp=alert.timestamp,
            state=state,
            service=alert.service,
            alert_name=alert.alert_name,
        )

    def add_custom_rule(self, rule: SuppressionRule) -> None:
        """Add custom suppression rule."""
        self._custom_rules[rule.rule_id] = rule

    def remove_custom_rule(self, rule_id: UUID) -> bool:
        """Remove custom suppression rule."""
        if rule_id in self._custom_rules:
            del self._custom_rules[rule_id]
            return True
        return False

    @property
    def maintenance_windows(self) -> MaintenanceWindowManager:
        """Get maintenance window manager."""
        return self._maintenance_manager

    @property
    def known_issues(self) -> KnownIssueTracker:
        """Get known issue tracker."""
        return self._known_issues

    @property
    def flapping(self) -> FlappingDetector:
        """Get flapping detector."""
        return self._flapping_detector

    @property
    def business_rules(self) -> BusinessRuleEngine:
        """Get business rule engine."""
        return self._business_rules

    def get_suppression_stats(self) -> dict[str, Any]:
        """Get suppression statistics."""
        active_maintenance = self._maintenance_manager.get_active_windows()
        active_issues = self._known_issues.get_active_issues()
        flapping_alerts = self._flapping_detector.get_all_flapping()
        active_rules = [r for r in self._custom_rules.values() if r.is_active()]

        total_suppressed = (
            sum(w.alerts_suppressed for w in active_maintenance)
            + sum(i.alerts_suppressed for i in active_issues)
            + sum(r.alerts_suppressed for r in active_rules)
        )

        digests = self.pending_digests()

        return {
            "active_maintenance_windows": len(active_maintenance),
            "active_known_issues": len(active_issues),
            "flapping_alerts": len(flapping_alerts),
            "active_custom_rules": len(active_rules),
            "total_alerts_suppressed": total_suppressed,
            # Digested alerts are deliberately not folded into
            # `total_alerts_suppressed`: they were not suppressed, they were passed
            # through and counted. `digest_delivery` describes this engine, not the
            # build: "not_wired" means nobody attached a sink to *this* instance, so
            # its backlog has gone nowhere. The delivered counts are what a sink
            # actually accepted, so a wired engine that is failing to deliver reads
            # as failing rather than as quiet success.
            "pending_digest_groups": len(digests),
            "pending_digest_alerts": sum(d.count for d in digests),
            "digest_delivery": "wired" if self._digest_wired else "not_wired",
            "digest_groups_delivered": self._digest_groups_delivered,
            "digest_alerts_delivered": self._digest_alerts_delivered,
            "digest_delivery_failures": self._digest_delivery_failures,
            "last_digest_delivery_error": self._last_digest_delivery_error,
        }
