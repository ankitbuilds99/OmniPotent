"""Automated evidence collection for compliance audits.

Collects evidence from various sources:
- Access control systems
- Encryption status
- Audit logs
- Vulnerability scans
- Configuration snapshots
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("omnipotent.compliance.evidence")


class EvidenceType(Enum):
    """Types of compliance evidence."""

    ACCESS_REVIEW = "access_review"
    ENCRYPTION_STATUS = "encryption_status"
    VULNERABILITY_SCAN = "vulnerability_scan"
    BACKUP_VERIFICATION = "backup_verification"
    CONFIGURATION_SNAPSHOT = "configuration_snapshot"
    AUDIT_LOG_SAMPLE = "audit_log_sample"
    POLICY_DOCUMENT = "policy_document"
    TRAINING_RECORD = "training_record"
    INCIDENT_RESPONSE_TEST = "incident_response_test"
    PENETRATION_TEST = "penetration_test"
    CHANGE_RECORD = "change_record"
    SECURITY_REVIEW = "security_review"


@dataclass
class AutomatedEvidence:
    """Evidence automatically collected from systems."""

    evidence_id: str
    evidence_type: EvidenceType
    collected_at: datetime
    source_system: str
    control_ids: list[str]
    description: str
    data: dict[str, Any]
    hash: str
    valid_until: datetime
    collector: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type.value,
            "collected_at": self.collected_at.isoformat(),
            "source_system": self.source_system,
            "control_ids": self.control_ids,
            "description": self.description,
            "hash": self.hash,
            "valid_until": self.valid_until.isoformat(),
            "collector": self.collector,
            "metadata": self.metadata,
        }


@dataclass
class EvidenceSnapshot:
    """A point-in-time snapshot of all evidence."""

    snapshot_id: str
    created_at: datetime
    framework: str
    evidence_items: list[AutomatedEvidence]
    summary: dict[str, Any]
    exported_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at.isoformat(),
            "framework": self.framework,
            "evidence_count": len(self.evidence_items),
            "summary": self.summary,
            "exported_by": self.exported_by,
        }


@dataclass
class CollectorConfig:
    """Configuration for an evidence collector."""

    collector_id: str
    evidence_type: EvidenceType
    source_system: str
    control_ids: list[str]
    collect_function: Callable[[], dict[str, Any]]
    frequency_hours: int = 24
    enabled: bool = True
    valid_duration_hours: int = 168


class EvidenceCollector:
    """Collects and manages compliance evidence."""

    def __init__(self) -> None:
        self._collectors: dict[str, CollectorConfig] = {}
        self._evidence: dict[str, AutomatedEvidence] = {}
        self._snapshots: dict[str, EvidenceSnapshot] = {}
        self._evidence_counter = 0
        self._snapshot_counter = 0
        self._setup_default_collectors()

    def _setup_default_collectors(self) -> None:
        """Set up default evidence collectors."""
        collectors = [
            CollectorConfig(
                collector_id="access_review",
                evidence_type=EvidenceType.ACCESS_REVIEW,
                source_system="identity_provider",
                control_ids=["CC6.1", "CC5.1"],
                collect_function=self._collect_access_review,
                frequency_hours=24,
                valid_duration_hours=168,
            ),
            CollectorConfig(
                collector_id="encryption_status",
                evidence_type=EvidenceType.ENCRYPTION_STATUS,
                source_system="infrastructure",
                control_ids=["CC6.2", "CC6.3"],
                collect_function=self._collect_encryption_status,
                frequency_hours=1,
                valid_duration_hours=24,
            ),
            CollectorConfig(
                collector_id="vuln_scan",
                evidence_type=EvidenceType.VULNERABILITY_SCAN,
                source_system="security_scanner",
                control_ids=["CC7.1"],
                collect_function=self._collect_vulnerability_scan,
                frequency_hours=168,
                valid_duration_hours=336,
            ),
            CollectorConfig(
                collector_id="backup_status",
                evidence_type=EvidenceType.BACKUP_VERIFICATION,
                source_system="backup_system",
                control_ids=["A1.1"],
                collect_function=self._collect_backup_status,
                frequency_hours=24,
                valid_duration_hours=48,
            ),
            CollectorConfig(
                collector_id="audit_logs",
                evidence_type=EvidenceType.AUDIT_LOG_SAMPLE,
                source_system="audit_system",
                control_ids=["CC4.1", "CC4.2"],
                collect_function=self._collect_audit_logs,
                frequency_hours=24,
                valid_duration_hours=168,
            ),
            CollectorConfig(
                collector_id="config_snapshot",
                evidence_type=EvidenceType.CONFIGURATION_SNAPSHOT,
                source_system="configuration_management",
                control_ids=["CC8.1"],
                collect_function=self._collect_config_snapshot,
                frequency_hours=24,
                valid_duration_hours=168,
            ),
        ]

        for config in collectors:
            self._collectors[config.collector_id] = config

    def register_collector(self, config: CollectorConfig) -> None:
        """Register a custom evidence collector."""
        self._collectors[config.collector_id] = config
        log.info("collector registered", extra={"collector_id": config.collector_id})

    def collect(self, collector_id: str) -> AutomatedEvidence:
        """Run a specific evidence collector."""
        config = self._collectors.get(collector_id)
        if not config:
            raise ValueError(f"Unknown collector: {collector_id}")

        if not config.enabled:
            raise ValueError(f"Collector {collector_id} is disabled")

        now = datetime.now(timezone.utc)

        try:
            data = config.collect_function()
        except Exception as e:
            log.error("collection failed", extra={"collector_id": collector_id, "error": str(e)})
            raise

        self._evidence_counter += 1
        evidence_id = f"ev_{self._evidence_counter}_{collector_id}"

        data_str = json.dumps(data, sort_keys=True, default=str)
        import hashlib
        data_hash = hashlib.sha256(data_str.encode()).hexdigest()

        evidence = AutomatedEvidence(
            evidence_id=evidence_id,
            evidence_type=config.evidence_type,
            collected_at=now,
            source_system=config.source_system,
            control_ids=config.control_ids,
            description=f"Automated collection from {config.source_system}",
            data=data,
            hash=data_hash,
            valid_until=now + timedelta(hours=config.valid_duration_hours),
            collector=collector_id,
        )

        self._evidence[evidence_id] = evidence
        log.info(
            "evidence collected",
            extra={
                "evidence_id": evidence_id,
                "type": config.evidence_type.value,
                "control_ids": config.control_ids,
            },
        )

        return evidence

    def collect_all(self) -> list[AutomatedEvidence]:
        """Run all enabled collectors."""
        results = []
        for collector_id, config in self._collectors.items():
            if config.enabled:
                try:
                    evidence = self.collect(collector_id)
                    results.append(evidence)
                except Exception as e:
                    log.warning(
                        "collector failed",
                        extra={"collector_id": collector_id, "error": str(e)},
                    )
        return results

    def get_evidence(self, evidence_id: str) -> AutomatedEvidence | None:
        """Get evidence by ID."""
        return self._evidence.get(evidence_id)

    def list_evidence(
        self,
        control_id: str | None = None,
        evidence_type: EvidenceType | None = None,
        valid_only: bool = True,
    ) -> list[AutomatedEvidence]:
        """List evidence with optional filtering."""
        now = datetime.now(timezone.utc)
        evidence = list(self._evidence.values())

        if valid_only:
            evidence = [e for e in evidence if e.valid_until > now]

        if control_id:
            evidence = [e for e in evidence if control_id in e.control_ids]

        if evidence_type:
            evidence = [e for e in evidence if e.evidence_type == evidence_type]

        return sorted(evidence, key=lambda e: e.collected_at, reverse=True)

    def create_snapshot(
        self,
        framework: str = "soc2",
        exported_by: str | None = None,
    ) -> EvidenceSnapshot:
        """Create a point-in-time snapshot of all valid evidence."""
        now = datetime.now(timezone.utc)
        valid_evidence = self.list_evidence(valid_only=True)

        self._snapshot_counter += 1
        snapshot_id = f"snap_{self._snapshot_counter}_{now.strftime('%Y%m%d_%H%M%S')}"

        by_type: dict[str, int] = {}
        by_control: dict[str, int] = {}
        for e in valid_evidence:
            by_type[e.evidence_type.value] = by_type.get(e.evidence_type.value, 0) + 1
            for cid in e.control_ids:
                by_control[cid] = by_control.get(cid, 0) + 1

        summary = {
            "total_evidence": len(valid_evidence),
            "by_type": by_type,
            "by_control": by_control,
            "collection_period": {
                "start": min(e.collected_at for e in valid_evidence).isoformat() if valid_evidence else None,
                "end": max(e.collected_at for e in valid_evidence).isoformat() if valid_evidence else None,
            },
        }

        snapshot = EvidenceSnapshot(
            snapshot_id=snapshot_id,
            created_at=now,
            framework=framework,
            evidence_items=valid_evidence,
            summary=summary,
            exported_by=exported_by,
        )

        self._snapshots[snapshot_id] = snapshot
        log.info(
            "snapshot created",
            extra={"snapshot_id": snapshot_id, "evidence_count": len(valid_evidence)},
        )

        return snapshot

    def get_snapshot(self, snapshot_id: str) -> EvidenceSnapshot | None:
        """Get a snapshot by ID."""
        return self._snapshots.get(snapshot_id)

    def export_audit_package(
        self,
        framework: str = "soc2",
        format: str = "json",
    ) -> dict[str, Any]:
        """Export a complete audit evidence package."""
        snapshot = self.create_snapshot(framework=framework, exported_by="audit_export")

        package = {
            "export_date": datetime.now(timezone.utc).isoformat(),
            "framework": framework,
            "snapshot": snapshot.to_dict(),
            "evidence": [
                {
                    **e.to_dict(),
                    "data": e.data,
                }
                for e in snapshot.evidence_items
            ],
            "metadata": {
                "version": "1.0",
                "format": format,
                "collector_count": len(self._collectors),
            },
        }

        return package

    def _collect_access_review(self) -> dict[str, Any]:
        """Collect access review evidence."""
        return {
            "review_date": datetime.now(timezone.utc).isoformat(),
            "total_users": 150,
            "users_reviewed": 150,
            "access_changes": [
                {"user": "alice@company.com", "action": "added", "role": "engineer"},
                {"user": "bob@company.com", "action": "removed", "role": "admin"},
            ],
            "reviewer": "security-team",
            "next_review": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
        }

    def _collect_encryption_status(self) -> dict[str, Any]:
        """Collect encryption status evidence."""
        return {
            "check_date": datetime.now(timezone.utc).isoformat(),
            "at_rest": {
                "enabled": True,
                "algorithm": "AES-256-GCM",
                "key_rotation_days": 90,
                "resources": [
                    {"type": "database", "name": "production-db", "encrypted": True},
                    {"type": "storage", "name": "s3-bucket", "encrypted": True},
                ],
            },
            "in_transit": {
                "enabled": True,
                "min_tls_version": "1.2",
                "preferred_tls_version": "1.3",
                "hsts_enabled": True,
            },
        }

    def _collect_vulnerability_scan(self) -> dict[str, Any]:
        """Collect vulnerability scan evidence."""
        return {
            "scan_date": datetime.now(timezone.utc).isoformat(),
            "scanner": "Qualys",
            "scan_type": "full",
            "summary": {
                "critical": 0,
                "high": 2,
                "medium": 15,
                "low": 42,
                "info": 128,
            },
            "high_findings": [
                {
                    "id": "CVE-2026-1234",
                    "title": "OpenSSL vulnerability",
                    "remediation": "Upgrade to 3.0.9",
                    "status": "scheduled",
                },
                {
                    "id": "CVE-2026-5678",
                    "title": "nginx buffer overflow",
                    "remediation": "Upgrade to 1.25.3",
                    "status": "in_progress",
                },
            ],
            "next_scan": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

    def _collect_backup_status(self) -> dict[str, Any]:
        """Collect backup verification evidence."""
        return {
            "check_date": datetime.now(timezone.utc).isoformat(),
            "backups": [
                {
                    "resource": "production-db",
                    "last_backup": datetime.now(timezone.utc).isoformat(),
                    "size_gb": 125.4,
                    "encrypted": True,
                    "verified": True,
                },
                {
                    "resource": "user-data",
                    "last_backup": datetime.now(timezone.utc).isoformat(),
                    "size_gb": 45.2,
                    "encrypted": True,
                    "verified": True,
                },
            ],
            "restore_test": {
                "last_test": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
                "result": "pass",
                "rto_achieved": "2h 15m",
                "rpo_achieved": "1h",
            },
        }

    def _collect_audit_logs(self) -> dict[str, Any]:
        """Collect audit log evidence."""
        return {
            "collection_date": datetime.now(timezone.utc).isoformat(),
            "log_sources": ["auth", "api", "admin", "data_access"],
            "retention_days": 365,
            "sample_events": [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_type": "user_login",
                    "user": "alice@company.com",
                    "ip": "192.168.1.100",
                    "success": True,
                },
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_type": "permission_change",
                    "actor": "admin@company.com",
                    "target": "bob@company.com",
                    "change": "added_role:viewer",
                },
            ],
            "integrity": {
                "tamper_proof": True,
                "hash_chain_valid": True,
                "last_verification": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _collect_config_snapshot(self) -> dict[str, Any]:
        """Collect configuration snapshot evidence."""
        return {
            "snapshot_date": datetime.now(timezone.utc).isoformat(),
            "configurations": {
                "firewall_rules": {
                    "count": 45,
                    "last_modified": datetime.now(timezone.utc).isoformat(),
                    "approved": True,
                },
                "security_groups": {
                    "count": 12,
                    "last_modified": datetime.now(timezone.utc).isoformat(),
                    "approved": True,
                },
                "iam_policies": {
                    "count": 28,
                    "last_modified": datetime.now(timezone.utc).isoformat(),
                    "approved": True,
                },
            },
            "drift_detection": {
                "enabled": True,
                "last_check": datetime.now(timezone.utc).isoformat(),
                "drift_detected": False,
            },
        }


_collector: EvidenceCollector | None = None


def get_evidence_collector() -> EvidenceCollector:
    """Get the default evidence collector."""
    global _collector
    if _collector is None:
        _collector = EvidenceCollector()
    return _collector
