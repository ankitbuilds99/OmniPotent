"""Security auditor for static code analysis.

Scans Python code for:
- Secrets and credentials
- SQL injection patterns
- Command injection patterns
- XSS vulnerabilities
- Insecure deserialization
- Weak cryptography
- Debug mode exposure
- Sensitive data logging
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger("devcopilot.security_audit.auditor")


class Severity(str, Enum):
    """Finding severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(str, Enum):
    """Finding categories."""
    SECRETS = "secrets"
    INJECTION = "injection"
    XSS = "xss"
    DESERIALIZATION = "deserialization"
    CRYPTO = "crypto"
    CONFIGURATION = "configuration"
    LOGGING = "logging"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"


@dataclass
class Finding:
    """A security finding."""
    id: str
    severity: Severity
    category: Category
    title: str
    description: str
    file_path: str
    line_number: int | None = None
    code_snippet: str = ""
    recommendation: str = ""
    cwe_id: str | None = None
    owasp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "category": self.category.value,
            "title": self.title,
            "description": self.description,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "code_snippet": self.code_snippet,
            "recommendation": self.recommendation,
            "cwe_id": self.cwe_id,
            "owasp": self.owasp,
        }


@dataclass
class AuditResult:
    """Result of a security audit."""
    audit_id: str
    target_path: str
    started_at: datetime
    completed_at: datetime
    files_scanned: int
    findings: list[Finding] = field(default_factory=list)
    passed: bool = True

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.findings),
            "critical": len([f for f in self.findings if f.severity == Severity.CRITICAL]),
            "high": len([f for f in self.findings if f.severity == Severity.HIGH]),
            "medium": len([f for f in self.findings if f.severity == Severity.MEDIUM]),
            "low": len([f for f in self.findings if f.severity == Severity.LOW]),
            "info": len([f for f in self.findings if f.severity == Severity.INFO]),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "target_path": self.target_path,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "files_scanned": self.files_scanned,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
            "passed": self.passed,
        }


class SecurityAuditor:
    """Static security analyzer for Python code.

    Detects OWASP Top 10 vulnerabilities and common security issues.
    """

    SECRET_PATTERNS = [
        (r'(?i)(api[_-]?key|apikey)\s*[=:]\s*["\']([a-zA-Z0-9_\-]{20,})["\']', "API Key"),
        (r'sk-ant-[a-zA-Z0-9_\-]{20,}', "Anthropic API Key"),
        (r'sk_live_[a-zA-Z0-9]{20,}', "Stripe Live Key"),
        (r'sk_test_[a-zA-Z0-9]{20,}', "Stripe Test Key"),
        (r'SG\.[a-zA-Z0-9_\-]{20,}', "SendGrid API Key"),
        (r'xoxb-[0-9]+-[0-9]+-[a-zA-Z0-9]+', "Slack Bot Token"),
        (r'xoxp-[0-9]+-[0-9]+-[a-zA-Z0-9]+', "Slack User Token"),
        (r'AKIA[0-9A-Z]{16}', "AWS Access Key"),
        (r'(?i)aws_secret_access_key\s*[=:]\s*["\']([a-zA-Z0-9/+=]{40})["\']', "AWS Secret Key"),
        (r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----', "Private Key"),
        (r'-----BEGIN OPENSSH PRIVATE KEY-----', "SSH Private Key"),
        (r'(?i)(password|passwd|pwd)\s*[=:]\s*["\']([^"\']{8,})["\']', "Hardcoded Password"),
        (r'(?i)bearer\s+[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+', "JWT Token"),
        (r'eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', "JWT Token"),
        (r'ghp_[a-zA-Z0-9]{36}', "GitHub Personal Token"),
        (r'gho_[a-zA-Z0-9]{36}', "GitHub OAuth Token"),
        (r'glpat-[a-zA-Z0-9\-]{20}', "GitLab Personal Token"),
    ]

    SQL_INJECTION_PATTERNS = [
        r'execute\s*\(\s*f["\'].*{.*}.*["\']',
        r'execute\s*\(\s*["\'].*%s.*["\'].*%',
        r'execute\s*\(\s*["\'].*\+.*["\']',
        r'cursor\.execute\s*\(\s*f["\']',
        r'\.raw\s*\(\s*f["\']',
        r'\.extra\s*\(\s*where\s*=.*\+',
    ]

    COMMAND_INJECTION_PATTERNS = [
        (r'subprocess\.(call|run|Popen)\s*\([^)]*shell\s*=\s*True', "shell=True in subprocess"),
        (r'os\.system\s*\(', "os.system()"),
        (r'os\.popen\s*\(', "os.popen()"),
        (r'commands\.(getoutput|getstatusoutput)\s*\(', "commands module"),
        (r'eval\s*\(', "eval()"),
        (r'exec\s*\(', "exec()"),
    ]

    DESERIALIZATION_PATTERNS = [
        (r'pickle\.loads?\s*\(', "pickle"),
        (r'yaml\.load\s*\([^)]*\)(?!.*Loader)', "yaml.load without Loader"),
        (r'yaml\.unsafe_load\s*\(', "yaml.unsafe_load"),
        (r'marshal\.loads?\s*\(', "marshal"),
        (r'shelve\.open\s*\(', "shelve"),
    ]

    WEAK_CRYPTO_PATTERNS = [
        (r'hashlib\.md5\s*\(', "MD5"),
        (r'hashlib\.sha1\s*\(', "SHA1"),
        (r'DES\s*\(', "DES"),
        (r'RC4\s*\(', "RC4"),
        (r'random\.random\s*\(', "random.random for security"),
        (r'random\.randint\s*\(', "random.randint for security"),
    ]

    DEBUG_PATTERNS = [
        r'DEBUG\s*=\s*True',
        r'debug\s*=\s*True',
        r'app\.run\s*\([^)]*debug\s*=\s*True',
    ]

    SENSITIVE_LOGGING_PATTERNS = [
        r'log(ger)?\.(info|debug|warning|error)\s*\([^)]*password',
        r'log(ger)?\.(info|debug|warning|error)\s*\([^)]*token',
        r'log(ger)?\.(info|debug|warning|error)\s*\([^)]*secret',
        r'log(ger)?\.(info|debug|warning|error)\s*\([^)]*api_key',
        r'print\s*\([^)]*password',
        r'print\s*\([^)]*token',
    ]

    def __init__(self, exclude_patterns: list[str] | None = None):
        """Initialize the auditor.

        Args:
            exclude_patterns: File patterns to exclude (e.g., ["*test*", "*migration*"])
        """
        self.exclude_patterns = exclude_patterns or ["*test*", "*migration*", "__pycache__"]
        self._finding_id = 0

    def _next_id(self) -> str:
        self._finding_id += 1
        return f"SEC-{self._finding_id:04d}"

    def _should_exclude(self, path: Path) -> bool:
        """Check if path should be excluded."""
        path_str = str(path).lower()
        for pattern in self.exclude_patterns:
            if pattern.strip("*") in path_str:
                return True
        return False

    async def audit(self, target_path: str) -> AuditResult:
        """Run security audit on target path.

        Args:
            target_path: Path to scan (file or directory)

        Returns:
            AuditResult
        """
        log.info("Starting security audit: %s", target_path)
        started = datetime.now(timezone.utc)

        path = Path(target_path)
        findings: list[Finding] = []
        files_scanned = 0

        if path.is_file():
            if path.suffix == ".py":
                file_findings = await self._scan_file(path)
                findings.extend(file_findings)
                files_scanned = 1
        else:
            for py_file in path.rglob("*.py"):
                if self._should_exclude(py_file):
                    continue
                file_findings = await self._scan_file(py_file)
                findings.extend(file_findings)
                files_scanned += 1

        completed = datetime.now(timezone.utc)

        # Sort by severity
        severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3, Severity.INFO: 4}
        findings.sort(key=lambda f: severity_order[f.severity])

        passed = not any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings)

        result = AuditResult(
            audit_id=f"audit-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            target_path=target_path,
            started_at=started,
            completed_at=completed,
            files_scanned=files_scanned,
            findings=findings,
            passed=passed,
        )

        log.info("Audit complete: %d files, %d findings", files_scanned, len(findings))
        return result

    async def _scan_file(self, file_path: Path) -> list[Finding]:
        """Scan a single file."""
        findings: list[Finding] = []

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            log.warning("Failed to read %s: %s", file_path, e)
            return findings

        rel_path = str(file_path)

        # Check for secrets
        findings.extend(self._check_secrets(content, rel_path))

        # Check for SQL injection
        findings.extend(self._check_sql_injection(content, rel_path))

        # Check for command injection
        findings.extend(self._check_command_injection(content, rel_path))

        # Check for insecure deserialization
        findings.extend(self._check_deserialization(content, rel_path))

        # Check for weak crypto
        findings.extend(self._check_weak_crypto(content, rel_path))

        # Check for debug mode
        findings.extend(self._check_debug_mode(content, rel_path))

        # Check for sensitive logging
        findings.extend(self._check_sensitive_logging(content, rel_path))

        return findings

    def _get_line_number(self, content: str, match_start: int) -> int:
        """Get line number from match position."""
        return content[:match_start].count('\n') + 1

    def _get_code_snippet(self, content: str, line_num: int, context: int = 1) -> str:
        """Get code snippet around line."""
        lines = content.split('\n')
        start = max(0, line_num - 1 - context)
        end = min(len(lines), line_num + context)
        return '\n'.join(lines[start:end])

    def _check_secrets(self, content: str, file_path: str) -> list[Finding]:
        findings = []
        for pattern, secret_type in self.SECRET_PATTERNS:
            for match in re.finditer(pattern, content):
                line_num = self._get_line_number(content, match.start())
                findings.append(Finding(
                    id=self._next_id(),
                    severity=Severity.CRITICAL,
                    category=Category.SECRETS,
                    title=f"{secret_type} exposed in code",
                    description=f"Potential {secret_type} found in source code. Secrets should never be committed to version control.",
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=self._get_code_snippet(content, line_num),
                    recommendation="Remove the secret and use environment variables or a secrets manager. Rotate the exposed credential immediately.",
                    cwe_id="CWE-798",
                    owasp="A3:2017 - Sensitive Data Exposure",
                ))
        return findings

    def _check_sql_injection(self, content: str, file_path: str) -> list[Finding]:
        findings = []
        for pattern in self.SQL_INJECTION_PATTERNS:
            for match in re.finditer(pattern, content):
                line_num = self._get_line_number(content, match.start())
                findings.append(Finding(
                    id=self._next_id(),
                    severity=Severity.CRITICAL,
                    category=Category.INJECTION,
                    title="Potential SQL injection",
                    description="Unsafe SQL query construction detected. Using string formatting or concatenation in SQL queries can lead to SQL injection attacks.",
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=self._get_code_snippet(content, line_num),
                    recommendation="Use parameterized queries with placeholders instead of string formatting.",
                    cwe_id="CWE-89",
                    owasp="A1:2017 - Injection",
                ))
        return findings

    def _check_command_injection(self, content: str, file_path: str) -> list[Finding]:
        findings = []
        for pattern, issue in self.COMMAND_INJECTION_PATTERNS:
            for match in re.finditer(pattern, content):
                line_num = self._get_line_number(content, match.start())
                findings.append(Finding(
                    id=self._next_id(),
                    severity=Severity.HIGH,
                    category=Category.INJECTION,
                    title=f"Potential command injection: {issue}",
                    description=f"Dangerous command execution pattern detected ({issue}). This can allow arbitrary command execution if user input is involved.",
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=self._get_code_snippet(content, line_num),
                    recommendation="Avoid shell=True in subprocess. Use subprocess with list arguments. Never pass unsanitized user input to command execution.",
                    cwe_id="CWE-78",
                    owasp="A1:2017 - Injection",
                ))
        return findings

    def _check_deserialization(self, content: str, file_path: str) -> list[Finding]:
        findings = []
        for pattern, lib in self.DESERIALIZATION_PATTERNS:
            # Skip yaml.load check if Loader is specified
            if "yaml.load" in pattern and "Loader=" in content:
                continue

            for match in re.finditer(pattern, content):
                line_num = self._get_line_number(content, match.start())
                findings.append(Finding(
                    id=self._next_id(),
                    severity=Severity.HIGH,
                    category=Category.DESERIALIZATION,
                    title=f"Insecure deserialization: {lib}",
                    description=f"Using {lib} for deserialization can lead to remote code execution if the data source is untrusted.",
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=self._get_code_snippet(content, line_num),
                    recommendation="Use safe alternatives. For YAML, use yaml.safe_load(). Avoid pickle/marshal with untrusted data.",
                    cwe_id="CWE-502",
                    owasp="A8:2017 - Insecure Deserialization",
                ))
        return findings

    def _check_weak_crypto(self, content: str, file_path: str) -> list[Finding]:
        findings = []
        for pattern, algo in self.WEAK_CRYPTO_PATTERNS:
            for match in re.finditer(pattern, content):
                line_num = self._get_line_number(content, match.start())
                findings.append(Finding(
                    id=self._next_id(),
                    severity=Severity.MEDIUM,
                    category=Category.CRYPTO,
                    title=f"Weak cryptography: {algo}",
                    description=f"Using {algo} which is cryptographically weak or inappropriate for security purposes.",
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=self._get_code_snippet(content, line_num),
                    recommendation="Use SHA-256 or SHA-3 for hashing. Use secrets module for cryptographic randomness. Use AES-GCM for encryption.",
                    cwe_id="CWE-327",
                    owasp="A6:2017 - Security Misconfiguration",
                ))
        return findings

    def _check_debug_mode(self, content: str, file_path: str) -> list[Finding]:
        findings: list[Finding] = []

        # Skip if it's environment-based
        if "os.getenv" in content or "environ" in content:
            return findings

        for pattern in self.DEBUG_PATTERNS:
            for match in re.finditer(pattern, content):
                line_num = self._get_line_number(content, match.start())
                findings.append(Finding(
                    id=self._next_id(),
                    severity=Severity.HIGH,
                    category=Category.CONFIGURATION,
                    title="Debug mode enabled",
                    description="Debug mode is hardcoded to True. This exposes sensitive information and should not be enabled in production.",
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=self._get_code_snippet(content, line_num),
                    recommendation="Use environment variables to control debug mode: DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'",
                    cwe_id="CWE-215",
                    owasp="A6:2017 - Security Misconfiguration",
                ))
        return findings

    def _check_sensitive_logging(self, content: str, file_path: str) -> list[Finding]:
        findings = []
        for pattern in self.SENSITIVE_LOGGING_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                line_num = self._get_line_number(content, match.start())
                findings.append(Finding(
                    id=self._next_id(),
                    severity=Severity.MEDIUM,
                    category=Category.LOGGING,
                    title="Sensitive data in logs",
                    description="Potentially sensitive data (password, token, secret) is being logged. This can expose credentials in log files.",
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=self._get_code_snippet(content, line_num),
                    recommendation="Redact sensitive data before logging. Never log passwords, tokens, or API keys.",
                    cwe_id="CWE-532",
                    owasp="A3:2017 - Sensitive Data Exposure",
                ))
        return findings

    def generate_report(self, result: AuditResult, format: str = "text") -> str:
        """Generate audit report.

        Args:
            result: AuditResult to report
            format: Output format (text, json, sarif)

        Returns:
            Formatted report
        """
        if format == "json":
            import json
            return json.dumps(result.to_dict(), indent=2)

        # Text format
        lines = [
            "=" * 60,
            "SECURITY AUDIT REPORT",
            "=" * 60,
            f"Target: {result.target_path}",
            f"Time: {result.started_at.isoformat()}",
            f"Files Scanned: {result.files_scanned}",
            f"Status: {'PASSED' if result.passed else 'FAILED'}",
            "",
            "SUMMARY:",
            f"  Critical: {result.summary['critical']}",
            f"  High:     {result.summary['high']}",
            f"  Medium:   {result.summary['medium']}",
            f"  Low:      {result.summary['low']}",
            f"  Info:     {result.summary['info']}",
            "",
        ]

        if result.findings:
            lines.append("FINDINGS:")
            lines.append("-" * 60)

            severity_icons = {
                Severity.CRITICAL: "[CRITICAL]",
                Severity.HIGH: "[HIGH]",
                Severity.MEDIUM: "[MEDIUM]",
                Severity.LOW: "[LOW]",
                Severity.INFO: "[INFO]",
            }

            for i, finding in enumerate(result.findings, 1):
                lines.append(f"\n{i}. {severity_icons[finding.severity]} {finding.title}")
                lines.append(f"   File: {finding.file_path}:{finding.line_number or '?'}")
                lines.append(f"   {finding.description}")
                if finding.cwe_id:
                    lines.append(f"   CWE: {finding.cwe_id}")
                lines.append(f"   Fix: {finding.recommendation}")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


def create_auditor(exclude_patterns: list[str] | None = None) -> SecurityAuditor:
    """Factory function to create a security auditor."""
    return SecurityAuditor(exclude_patterns=exclude_patterns)
