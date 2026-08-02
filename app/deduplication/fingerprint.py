"""Layer 1: Fingerprint-based exact deduplication."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from app.deduplication.models import (
    RawAlert,
    AlertFingerprint,
    FingerprintRule,
)

log = logging.getLogger("omnipotent.deduplication.fingerprint")


@dataclass
class FingerprintConfig:
    """Configuration for fingerprint calculation."""

    default_fields: list[str] = field(
        default_factory=lambda: ["source", "alert_name", "service", "environment"]
    )
    excluded_fields: list[str] = field(
        default_factory=lambda: [
            "timestamp",
            "value",
            "pod_name",
            "trace_id",
            "current_value",
        ]
    )
    dedup_window_minutes: int = 5
    hash_algorithm: str = "sha256"
    hash_length: int = 16


class FingerprintCalculator:
    """Calculate alert fingerprints for deduplication."""

    def __init__(self, config: FingerprintConfig | None = None):
        self.config = config or FingerprintConfig()
        self._rule_engine = FingerprintRuleEngine()

    def calculate(
        self,
        alert: RawAlert,
        custom_fields: list[str] | None = None,
    ) -> AlertFingerprint:
        """Calculate fingerprint for an alert."""
        matching_rule = self._rule_engine.find_matching_rule(alert)

        if matching_rule:
            fields = matching_rule.fingerprint_fields
            rule_name = matching_rule.name
        elif custom_fields:
            fields = custom_fields
            rule_name = "custom"
        else:
            fields = self.config.default_fields
            rule_name = None

        return AlertFingerprint.compute(alert, fields, rule_name)

    def calculate_normalized(
        self,
        alert: RawAlert,
    ) -> AlertFingerprint:
        """Calculate fingerprint with value normalization."""
        matching_rule = self._rule_engine.find_matching_rule(alert)

        if matching_rule:
            fields = matching_rule.fingerprint_fields
            rule_name = matching_rule.name
        else:
            fields = self.config.default_fields
            rule_name = None

        values: dict[str, str] = {}
        for fld in sorted(fields):
            val = getattr(alert, fld, None)
            if val is None:
                val = alert.labels.get(fld, "")
            if isinstance(val, Enum):
                val = val.value
            val_str = str(val) if val else ""
            val_str = self._normalize_value(fld, val_str)
            values[fld] = val_str

        fingerprint_parts = [f"{k}={v}" for k, v in sorted(values.items())]
        fingerprint_string = "|".join(fingerprint_parts)
        fp_hash = hashlib.sha256(fingerprint_string.encode()).hexdigest()[
            : self.config.hash_length
        ]

        return AlertFingerprint(
            fingerprint=fp_hash,
            fields_used=fields,
            rule_applied=rule_name,
            raw_values=values,
        )

    def _normalize_value(self, field_name: str, value: str) -> str:
        """Normalize value for consistent fingerprinting."""
        if field_name == "pod_name":
            match = re.match(r"^(.+)-[a-z0-9]{5,10}-[a-z0-9]{5}$", value)
            if match:
                return match.group(1) + "-*"

        if field_name == "host" and re.match(r"^ip-\d+-\d+-\d+-\d+", value):
            return "ip-*"

        return value.lower().strip()

    def add_rule(self, rule: FingerprintRule) -> None:
        """Add custom fingerprint rule."""
        self._rule_engine.add_rule(rule)

    def remove_rule(self, rule_id: UUID) -> bool:
        """Remove fingerprint rule."""
        return self._rule_engine.remove_rule(rule_id)

    def test_fingerprint(
        self,
        alert: RawAlert,
    ) -> dict[str, Any]:
        """Test fingerprint calculation with detailed explanation."""
        fingerprint = self.calculate(alert)
        matching_rule = self._rule_engine.find_matching_rule(alert)

        excluded = []
        for fld in self.config.excluded_fields:
            val = getattr(alert, fld, None) or alert.labels.get(fld)
            if val:
                excluded.append({"field": fld, "reason": "volatile/ephemeral"})

        return {
            "fingerprint": fingerprint.fingerprint,
            "fields_used": fingerprint.fields_used,
            "raw_values": fingerprint.raw_values,
            "rule_applied": matching_rule.name if matching_rule else None,
            "rule_description": matching_rule.description if matching_rule else None,
            "excluded_fields": excluded,
        }


class FingerprintRuleEngine:
    """Manage custom fingerprint rules."""

    def __init__(self):
        self._rules: list[FingerprintRule] = []
        self._load_default_rules()

    def _load_default_rules(self) -> None:
        """Load default fingerprint rules."""
        self._rules.extend([
            FingerprintRule(
                name="kubernetes_pod_alerts",
                description="Group by deployment instead of ephemeral pod names",
                match_criteria={
                    "source": "contains:kubernetes",
                    "alert_type": "pod",
                },
                fingerprint_fields=["namespace", "alert_name", "environment"],
                priority=100,
            ),
            FingerprintRule(
                name="database_connection_alerts",
                description="Group all connection issues per database host",
                match_criteria={
                    "alert_name": "contains:connection",
                },
                fingerprint_fields=["service", "alert_name", "host"],
                priority=90,
            ),
            FingerprintRule(
                name="aws_instance_alerts",
                description="Group by auto-scaling group instead of instance ID",
                match_criteria={
                    "source": "cloudwatch",
                },
                fingerprint_fields=["service", "alert_name", "region", "environment"],
                priority=80,
            ),
            FingerprintRule(
                name="prometheus_alerts",
                description="Standard Prometheus alert fingerprinting",
                match_criteria={
                    "source": "prometheus",
                },
                fingerprint_fields=[
                    "alert_name",
                    "service",
                    "namespace",
                    "environment",
                ],
                priority=70,
            ),
        ])

    def add_rule(self, rule: FingerprintRule) -> None:
        """Add a custom rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        log.info("Added fingerprint rule: %s", rule.name)

    def remove_rule(self, rule_id: UUID) -> bool:
        """Remove a rule by ID."""
        for i, rule in enumerate(self._rules):
            if rule.rule_id == rule_id:
                del self._rules[i]
                log.info("Removed fingerprint rule: %s", rule.name)
                return True
        return False

    def find_matching_rule(self, alert: RawAlert) -> FingerprintRule | None:
        """Find the highest priority matching rule."""
        for rule in self._rules:
            if rule.enabled and rule.matches(alert):
                return rule
        return None

    def get_all_rules(self) -> list[FingerprintRule]:
        """Get all rules."""
        return self._rules.copy()

    def test_rules(self, alert: RawAlert) -> list[dict[str, Any]]:
        """Test all rules against an alert."""
        results = []
        for rule in self._rules:
            results.append({
                "rule_name": rule.name,
                "priority": rule.priority,
                "enabled": rule.enabled,
                "matches": rule.matches(alert),
                "fingerprint_fields": rule.fingerprint_fields,
            })
        return results


@dataclass
class FingerprintMatch:
    """Result of fingerprint matching."""

    fingerprint: str
    incident_id: UUID
    alert_count: int
    first_seen: datetime
    last_seen: datetime
    services: list[str]


class FingerprintMatcher:
    """Match alerts by fingerprint for deduplication."""

    def __init__(
        self,
        dedup_window: timedelta | None = None,
    ):
        self.dedup_window = dedup_window or timedelta(minutes=5)
        self._fingerprint_index: dict[str, list[tuple[datetime, UUID, UUID]]] = {}
        self._incident_fingerprints: dict[UUID, set[str]] = {}

    def register_alert(
        self,
        fingerprint: AlertFingerprint,
        alert: RawAlert,
        incident_id: UUID,
    ) -> None:
        """Register an alert's fingerprint."""
        fp = fingerprint.fingerprint

        if fp not in self._fingerprint_index:
            self._fingerprint_index[fp] = []

        self._fingerprint_index[fp].append(
            (alert.timestamp, alert.alert_id, incident_id)
        )

        if incident_id not in self._incident_fingerprints:
            self._incident_fingerprints[incident_id] = set()
        self._incident_fingerprints[incident_id].add(fp)

    def find_match(
        self,
        fingerprint: AlertFingerprint,
        timestamp: datetime,
    ) -> FingerprintMatch | None:
        """Find existing incident with matching fingerprint."""
        fp = fingerprint.fingerprint
        entries = self._fingerprint_index.get(fp, [])

        if not entries:
            return None

        cutoff = timestamp - self.dedup_window

        recent_entries = [
            (ts, alert_id, incident_id)
            for ts, alert_id, incident_id in entries
            if ts > cutoff
        ]

        if not recent_entries:
            return None

        recent_entries.sort(key=lambda x: x[0], reverse=True)
        _, _, incident_id = recent_entries[0]

        return FingerprintMatch(
            fingerprint=fp,
            incident_id=incident_id,
            alert_count=len(recent_entries),
            first_seen=recent_entries[-1][0],
            last_seen=recent_entries[0][0],
            services=[],
        )

    def find_similar_fingerprints(
        self,
        fingerprint: AlertFingerprint,
        max_distance: int = 2,
    ) -> list[FingerprintMatch]:
        """Find fingerprints within edit distance."""
        fp = fingerprint.fingerprint
        matches = []

        for existing_fp, entries in self._fingerprint_index.items():
            if existing_fp == fp:
                continue

            distance = self._hamming_distance(fp, existing_fp)
            if distance <= max_distance and entries:
                recent = entries[-1]
                matches.append(
                    FingerprintMatch(
                        fingerprint=existing_fp,
                        incident_id=recent[2],
                        alert_count=len(entries),
                        first_seen=entries[0][0],
                        last_seen=recent[0],
                        services=[],
                    )
                )

        return matches

    def _hamming_distance(self, s1: str, s2: str) -> int:
        """Calculate Hamming distance between two hex strings."""
        if len(s1) != len(s2):
            return max(len(s1), len(s2))
        return sum(c1 != c2 for c1, c2 in zip(s1, s2))

    def cleanup_old_entries(self, before: datetime) -> int:
        """Remove entries older than specified time."""
        removed = 0
        for fp in list(self._fingerprint_index.keys()):
            entries = self._fingerprint_index[fp]
            new_entries = [(ts, aid, iid) for ts, aid, iid in entries if ts >= before]

            removed += len(entries) - len(new_entries)

            if new_entries:
                self._fingerprint_index[fp] = new_entries
            else:
                del self._fingerprint_index[fp]

        return removed

    def get_stats(self) -> dict[str, Any]:
        """Get fingerprint index statistics."""
        total_entries = sum(
            len(entries) for entries in self._fingerprint_index.values()
        )
        return {
            "unique_fingerprints": len(self._fingerprint_index),
            "total_entries": total_entries,
            "incidents_tracked": len(self._incident_fingerprints),
        }


class FingerprintTester:
    """Test and debug fingerprint calculations."""

    def __init__(
        self,
        calculator: FingerprintCalculator,
        matcher: FingerprintMatcher,
    ):
        self.calculator = calculator
        self.matcher = matcher

    def test_alert(self, alert: RawAlert) -> dict[str, Any]:
        """Comprehensive test of alert fingerprinting."""
        fingerprint = self.calculator.calculate(alert)
        normalized = self.calculator.calculate_normalized(alert)
        test_result = self.calculator.test_fingerprint(alert)

        match = self.matcher.find_match(fingerprint, alert.timestamp)
        similar = self.matcher.find_similar_fingerprints(fingerprint)

        return {
            "input_alert": alert.to_dict(),
            "fingerprint": {
                "hash": fingerprint.fingerprint,
                "normalized_hash": normalized.fingerprint,
                "fields_used": fingerprint.fields_used,
                "raw_values": fingerprint.raw_values,
            },
            "rule_analysis": test_result,
            "matching": {
                "exact_match": match is not None,
                "match_incident_id": str(match.incident_id) if match else None,
                "match_alert_count": match.alert_count if match else 0,
                "similar_fingerprints": len(similar),
            },
            "recommendation": self._get_recommendation(match, similar),
        }

    def _get_recommendation(
        self,
        match: FingerprintMatch | None,
        similar: list[FingerprintMatch],
    ) -> str:
        """Generate recommendation based on matching results."""
        if match:
            return f"DEDUPLICATE: Merge into existing incident {match.incident_id}"
        if similar:
            return f"REVIEW: {len(similar)} similar fingerprints found - may be related"
        return "NEW_INCIDENT: No matching fingerprints found"

    def compare_alerts(
        self,
        alert1: RawAlert,
        alert2: RawAlert,
    ) -> dict[str, Any]:
        """Compare fingerprints of two alerts."""
        fp1 = self.calculator.calculate(alert1)
        fp2 = self.calculator.calculate(alert2)

        common_fields = set(fp1.fields_used) & set(fp2.fields_used)
        different_values = {}

        for fld in common_fields:
            v1 = fp1.raw_values.get(fld)
            v2 = fp2.raw_values.get(fld)
            if v1 != v2:
                different_values[fld] = {"alert1": v1, "alert2": v2}

        return {
            "fingerprint1": fp1.fingerprint,
            "fingerprint2": fp2.fingerprint,
            "match": fp1.fingerprint == fp2.fingerprint,
            "fields_compared": list(common_fields),
            "different_values": different_values,
            "hamming_distance": sum(
                c1 != c2 for c1, c2 in zip(fp1.fingerprint, fp2.fingerprint)
            ),
        }
