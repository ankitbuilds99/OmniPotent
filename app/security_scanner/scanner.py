"""Security Scanner - Security posture assessment and vulnerability detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum

log = logging.getLogger("devcopilot.security_scanner.scanner")


class VulnerabilitySeverity(str, Enum):
    """Vulnerability severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ScanType(str, Enum):
    """Types of security scans."""
    CONTAINER_IMAGE = "container_image"
    KUBERNETES_CONFIG = "kubernetes_config"
    IAM_POLICY = "iam_policy"
    NETWORK_POLICY = "network_policy"
    SECRETS = "secrets"
    DEPENDENCIES = "dependencies"
    COMPLIANCE = "compliance"


class ComplianceFramework(str, Enum):
    """Compliance frameworks."""
    CIS_KUBERNETES = "cis_kubernetes"
    CIS_DOCKER = "cis_docker"
    NIST_800_53 = "nist_800_53"
    SOC2 = "soc2"
    PCI_DSS = "pci_dss"
    HIPAA = "hipaa"


@dataclass
class Vulnerability:
    """A security vulnerability."""
    id: str
    title: str
    description: str
    severity: VulnerabilitySeverity
    cvss_score: float | None = None
    cve_id: str | None = None
    affected_resource: str = ""
    affected_version: str = ""
    fixed_version: str | None = None
    remediation: str = ""
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "cvss_score": self.cvss_score,
            "cve_id": self.cve_id,
            "affected_resource": self.affected_resource,
            "affected_version": self.affected_version,
            "fixed_version": self.fixed_version,
            "remediation": self.remediation,
        }


@dataclass
class ComplianceCheck:
    """A compliance check result."""
    id: str
    framework: ComplianceFramework
    control: str
    title: str
    status: str  # pass, fail, warning, skip
    description: str
    remediation: str = ""
    severity: VulnerabilitySeverity = VulnerabilitySeverity.MEDIUM

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "framework": self.framework.value,
            "control": self.control,
            "title": self.title,
            "status": self.status,
            "description": self.description,
            "remediation": self.remediation,
            "severity": self.severity.value,
        }


@dataclass
class ScanResult:
    """Result of a security scan."""
    scan_id: str
    scan_type: ScanType
    target: str
    started_at: datetime
    completed_at: datetime
    vulnerabilities: list[Vulnerability]
    compliance_checks: list[ComplianceCheck]
    summary: dict[str, int]
    score: float  # 0-100 security score

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "scan_type": self.scan_type.value,
            "target": self.target,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "vulnerabilities": [v.to_dict() for v in self.vulnerabilities],
            "compliance_checks": [c.to_dict() for c in self.compliance_checks],
            "summary": self.summary,
            "score": self.score,
        }


class SecurityScanner:
    """Security posture scanner for Kubernetes and cloud environments.

    Features:
    - Container image vulnerability scanning
    - Kubernetes config analysis
    - IAM policy review
    - Network policy assessment
    - Secret detection
    - Dependency scanning
    - Compliance checking
    """

    def __init__(self):
        """Initialize the security scanner."""
        self._scan_history: list[ScanResult] = []
        log.info("SecurityScanner initialized")

    async def scan_image(self, image: str) -> ScanResult:
        """Scan a container image for vulnerabilities.

        Args:
            image: Container image reference (e.g., nginx:1.19)

        Returns:
            ScanResult
        """
        log.info("Scanning container image: %s", image)
        started = datetime.now(timezone.utc)

        # In production: use Trivy, Grype, or Clair
        vulnerabilities = await self._scan_image_vulnerabilities(image)

        completed = datetime.now(timezone.utc)

        result = self._create_result(
            ScanType.CONTAINER_IMAGE,
            image,
            started,
            completed,
            vulnerabilities,
            [],
        )

        self._scan_history.append(result)
        return result

    async def _scan_image_vulnerabilities(self, image: str) -> list[Vulnerability]:
        """Scan image for vulnerabilities."""
        # Simulated vulnerabilities
        return [
            Vulnerability(
                id="vuln-001",
                title="Outdated OpenSSL version",
                description="The base image contains OpenSSL 1.1.1k with known vulnerabilities",
                severity=VulnerabilitySeverity.HIGH,
                cvss_score=7.5,
                cve_id="CVE-2024-1234",
                affected_resource="openssl",
                affected_version="1.1.1k",
                fixed_version="1.1.1w",
                remediation="Update base image to latest version",
            ),
            Vulnerability(
                id="vuln-002",
                title="Vulnerable curl version",
                description="curl version has potential RCE vulnerability",
                severity=VulnerabilitySeverity.CRITICAL,
                cvss_score=9.8,
                cve_id="CVE-2024-5678",
                affected_resource="curl",
                affected_version="7.84.0",
                fixed_version="7.88.0",
                remediation="Update curl package or use patched base image",
            ),
        ]

    async def scan_kubernetes(
        self,
        namespace: str = "default",
        frameworks: list[ComplianceFramework] | None = None,
    ) -> ScanResult:
        """Scan Kubernetes configuration.

        Args:
            namespace: Namespace to scan
            frameworks: Compliance frameworks to check against

        Returns:
            ScanResult
        """
        log.info("Scanning Kubernetes config: namespace=%s", namespace)
        started = datetime.now(timezone.utc)

        frameworks = frameworks or [ComplianceFramework.CIS_KUBERNETES]

        vulnerabilities = await self._scan_k8s_config(namespace)
        compliance = await self._check_k8s_compliance(namespace, frameworks)

        completed = datetime.now(timezone.utc)

        result = self._create_result(
            ScanType.KUBERNETES_CONFIG,
            f"namespace/{namespace}",
            started,
            completed,
            vulnerabilities,
            compliance,
        )

        self._scan_history.append(result)
        return result

    async def _scan_k8s_config(self, namespace: str) -> list[Vulnerability]:
        """Scan Kubernetes configuration for issues."""
        return [
            Vulnerability(
                id="k8s-001",
                title="Container running as root",
                description="Pods are running containers as root user",
                severity=VulnerabilitySeverity.HIGH,
                affected_resource=f"{namespace}/web-deployment",
                remediation="Set securityContext.runAsNonRoot: true",
            ),
            Vulnerability(
                id="k8s-002",
                title="Missing resource limits",
                description="Containers without CPU/memory limits can impact cluster stability",
                severity=VulnerabilitySeverity.MEDIUM,
                affected_resource=f"{namespace}/api-deployment",
                remediation="Set resources.limits for all containers",
            ),
            Vulnerability(
                id="k8s-003",
                title="Privileged container",
                description="Container running in privileged mode",
                severity=VulnerabilitySeverity.CRITICAL,
                affected_resource=f"{namespace}/debug-pod",
                remediation="Remove privileged: true from securityContext",
            ),
        ]

    async def _check_k8s_compliance(
        self,
        namespace: str,
        frameworks: list[ComplianceFramework],
    ) -> list[ComplianceCheck]:
        """Check Kubernetes compliance."""
        checks = []

        for framework in frameworks:
            if framework == ComplianceFramework.CIS_KUBERNETES:
                checks.extend([
                    ComplianceCheck(
                        id="cis-5.1.1",
                        framework=framework,
                        control="5.1.1",
                        title="Ensure that the cluster-admin role is only used where required",
                        status="fail",
                        description="Found 3 users with cluster-admin binding",
                        remediation="Review and remove unnecessary cluster-admin bindings",
                        severity=VulnerabilitySeverity.HIGH,
                    ),
                    ComplianceCheck(
                        id="cis-5.2.1",
                        framework=framework,
                        control="5.2.1",
                        title="Minimize the admission of privileged containers",
                        status="fail",
                        description="No PodSecurityPolicy restricting privileged containers",
                        remediation="Implement Pod Security Standards",
                        severity=VulnerabilitySeverity.HIGH,
                    ),
                    ComplianceCheck(
                        id="cis-5.3.1",
                        framework=framework,
                        control="5.3.1",
                        title="Ensure that the CNI in use supports Network Policies",
                        status="pass",
                        description="Calico CNI supports NetworkPolicy",
                    ),
                ])

        return checks

    async def scan_secrets(self, path: str) -> ScanResult:
        """Scan for exposed secrets.

        Args:
            path: Path or repo to scan

        Returns:
            ScanResult
        """
        log.info("Scanning for secrets: %s", path)
        started = datetime.now(timezone.utc)

        vulnerabilities = await self._scan_secrets(path)

        completed = datetime.now(timezone.utc)

        result = self._create_result(
            ScanType.SECRETS,
            path,
            started,
            completed,
            vulnerabilities,
            [],
        )

        self._scan_history.append(result)
        return result

    async def _scan_secrets(self, path: str) -> list[Vulnerability]:
        """Scan for hardcoded secrets."""
        # In production: use gitleaks, truffleHog
        return [
            Vulnerability(
                id="secret-001",
                title="AWS Access Key exposed",
                description="Hardcoded AWS access key found in source code",
                severity=VulnerabilitySeverity.CRITICAL,
                affected_resource="config/settings.py:42",
                remediation="Rotate the key immediately and use environment variables",
            ),
        ]

    async def scan_dependencies(self, manifest_path: str) -> ScanResult:
        """Scan dependency manifests.

        Args:
            manifest_path: Path to package manifest

        Returns:
            ScanResult
        """
        log.info("Scanning dependencies: %s", manifest_path)
        started = datetime.now(timezone.utc)

        vulnerabilities = await self._scan_dependencies(manifest_path)

        completed = datetime.now(timezone.utc)

        result = self._create_result(
            ScanType.DEPENDENCIES,
            manifest_path,
            started,
            completed,
            vulnerabilities,
            [],
        )

        self._scan_history.append(result)
        return result

    async def _scan_dependencies(self, path: str) -> list[Vulnerability]:
        """Scan package dependencies."""
        # In production: use npm audit, pip-audit, etc.
        return [
            Vulnerability(
                id="dep-001",
                title="Vulnerable dependency: lodash",
                description="lodash < 4.17.21 has prototype pollution vulnerability",
                severity=VulnerabilitySeverity.HIGH,
                cvss_score=7.4,
                cve_id="CVE-2021-23337",
                affected_resource="lodash",
                affected_version="4.17.19",
                fixed_version="4.17.21",
                remediation="npm update lodash",
            ),
        ]

    async def full_scan(
        self,
        images: list[str] | None = None,
        namespaces: list[str] | None = None,
        scan_secrets: bool = True,
    ) -> dict[str, Any]:
        """Run a comprehensive security scan.

        Args:
            images: Container images to scan
            namespaces: Kubernetes namespaces to scan
            scan_secrets: Whether to scan for secrets

        Returns:
            Combined scan results
        """
        log.info("Running full security scan")

        results: list[ScanResult] = []

        # Scan images
        if images:
            for image in images:
                result = await self.scan_image(image)
                results.append(result)

        # Scan Kubernetes
        if namespaces:
            for ns in namespaces:
                result = await self.scan_kubernetes(ns)
                results.append(result)

        # Aggregate results
        all_vulns = []
        all_compliance = []
        for r in results:
            all_vulns.extend(r.vulnerabilities)
            all_compliance.extend(r.compliance_checks)

        summary = {
            "total_scans": len(results),
            "total_vulnerabilities": len(all_vulns),
            "critical": len([v for v in all_vulns if v.severity == VulnerabilitySeverity.CRITICAL]),
            "high": len([v for v in all_vulns if v.severity == VulnerabilitySeverity.HIGH]),
            "medium": len([v for v in all_vulns if v.severity == VulnerabilitySeverity.MEDIUM]),
            "low": len([v for v in all_vulns if v.severity == VulnerabilitySeverity.LOW]),
            "compliance_failed": len([c for c in all_compliance if c.status == "fail"]),
        }

        # Calculate overall security score
        score = self._calculate_score(all_vulns, all_compliance)

        return {
            "scan_id": f"full-scan-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "results": [r.to_dict() for r in results],
            "summary": summary,
            "overall_score": score,
            "risk_level": self._get_risk_level(score),
        }

    def _create_result(
        self,
        scan_type: ScanType,
        target: str,
        started: datetime,
        completed: datetime,
        vulnerabilities: list[Vulnerability],
        compliance: list[ComplianceCheck],
    ) -> ScanResult:
        """Create a scan result."""
        summary = {
            "total": len(vulnerabilities),
            "critical": len([v for v in vulnerabilities if v.severity == VulnerabilitySeverity.CRITICAL]),
            "high": len([v for v in vulnerabilities if v.severity == VulnerabilitySeverity.HIGH]),
            "medium": len([v for v in vulnerabilities if v.severity == VulnerabilitySeverity.MEDIUM]),
            "low": len([v for v in vulnerabilities if v.severity == VulnerabilitySeverity.LOW]),
        }

        score = self._calculate_score(vulnerabilities, compliance)

        return ScanResult(
            scan_id=f"{scan_type.value}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            scan_type=scan_type,
            target=target,
            started_at=started,
            completed_at=completed,
            vulnerabilities=vulnerabilities,
            compliance_checks=compliance,
            summary=summary,
            score=score,
        )

    def _calculate_score(
        self,
        vulnerabilities: list[Vulnerability],
        compliance: list[ComplianceCheck],
    ) -> float:
        """Calculate security score (0-100)."""
        score = 100.0

        # Deduct for vulnerabilities
        severity_weights = {
            VulnerabilitySeverity.CRITICAL: 20,
            VulnerabilitySeverity.HIGH: 10,
            VulnerabilitySeverity.MEDIUM: 5,
            VulnerabilitySeverity.LOW: 2,
        }

        for vuln in vulnerabilities:
            score -= severity_weights.get(vuln.severity, 1)

        # Deduct for compliance failures
        for check in compliance:
            if check.status == "fail":
                score -= 5

        return max(0, score)

    def _get_risk_level(self, score: float) -> str:
        """Get risk level from score."""
        if score >= 90:
            return "low"
        elif score >= 70:
            return "medium"
        elif score >= 50:
            return "high"
        else:
            return "critical"


def create_security_scanner() -> SecurityScanner:
    """Factory function to create a security scanner."""
    return SecurityScanner()
