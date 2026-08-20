"""Secrets detection and prevention.

Scans code and configuration for accidentally committed secrets:
- API keys (AWS, GCP, GitHub, etc.)
- Database credentials
- Private keys
- JWT tokens
- Generic high-entropy strings

Used for:
- Pre-commit hooks
- CI/CD pipelines
- Runtime content validation
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger("omnipotent.security.secrets")


class SecretsScanError(RuntimeError):
    """A scan could not be completed, so its result says nothing.

    Exists so that "found nothing" and "never looked" are different outcomes.
    A detector that reports clean when it failed is worse than no detector: it
    converts an unknown into an all-clear, and the caller stops looking.
    """


class SecretType(Enum):
    """Types of secrets that can be detected."""

    AWS_ACCESS_KEY = "aws_access_key"
    AWS_SECRET_KEY = "aws_secret_key"
    GITHUB_TOKEN = "github_token"
    GITHUB_PAT = "github_pat"
    GOOGLE_API_KEY = "google_api_key"
    GCP_SERVICE_ACCOUNT = "gcp_service_account"
    SLACK_TOKEN = "slack_token"
    SLACK_WEBHOOK = "slack_webhook"
    STRIPE_KEY = "stripe_key"
    SENDGRID_KEY = "sendgrid_key"
    TWILIO_KEY = "twilio_key"
    DATABASE_URL = "database_url"
    PRIVATE_KEY = "private_key"
    JWT_TOKEN = "jwt_token"
    GENERIC_API_KEY = "generic_api_key"
    GENERIC_SECRET = "generic_secret"
    PASSWORD_IN_URL = "password_in_url"
    ANTHROPIC_KEY = "anthropic_key"
    OPENAI_KEY = "openai_key"
    DATADOG_KEY = "datadog_key"
    PAGERDUTY_KEY = "pagerduty_key"
    SENTRY_DSN = "sentry_dsn"


@dataclass
class SecretPattern:
    """A pattern for detecting secrets."""

    secret_type: SecretType
    pattern: re.Pattern[str]
    description: str
    severity: str = "high"
    false_positive_hints: list[str] | None = None

    def __post_init__(self) -> None:
        if self.false_positive_hints is None:
            self.false_positive_hints = []


@dataclass
class SecretFinding:
    """A detected secret in content."""

    secret_type: SecretType
    severity: str
    line_number: int
    line_content: str
    matched_text: str
    file_path: str = ""
    description: str = ""
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.secret_type.value,
            "severity": self.severity,
            "line": self.line_number,
            "file": self.file_path,
            "description": self.description,
            "remediation": self.remediation,
            "preview": self._masked_preview(),
        }

    def _masked_preview(self) -> str:
        """Return line content with the secret masked."""
        if len(self.matched_text) <= 8:
            masked = "***"
        else:
            masked = self.matched_text[:4] + "..." + self.matched_text[-4:]
        return self.line_content.replace(self.matched_text, f"[{masked}]")


SECRET_PATTERNS: list[SecretPattern] = [
    SecretPattern(
        SecretType.AWS_ACCESS_KEY,
        re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE),
        "AWS Access Key ID",
        "critical",
    ),
    SecretPattern(
        SecretType.AWS_SECRET_KEY,
        re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
        "Possible AWS Secret Access Key",
        "high",
        ["base64", "sha256", "hash"],
    ),
    SecretPattern(
        SecretType.GITHUB_TOKEN,
        re.compile(r"ghp_[0-9a-zA-Z]{36}"),
        "GitHub Personal Access Token",
        "critical",
    ),
    SecretPattern(
        SecretType.GITHUB_PAT,
        re.compile(r"github_pat_[0-9a-zA-Z]{22}_[0-9a-zA-Z]{59}"),
        "GitHub Fine-grained PAT",
        "critical",
    ),
    SecretPattern(
        SecretType.GOOGLE_API_KEY,
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
        "Google API Key",
        "high",
    ),
    SecretPattern(
        SecretType.GCP_SERVICE_ACCOUNT,
        re.compile(r'"type"\s*:\s*"service_account"'),
        "GCP Service Account JSON",
        "critical",
    ),
    SecretPattern(
        SecretType.SLACK_TOKEN,
        re.compile(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*"),
        "Slack API Token",
        "critical",
    ),
    SecretPattern(
        SecretType.SLACK_WEBHOOK,
        re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[a-zA-Z0-9]+"),
        "Slack Webhook URL",
        "high",
    ),
    SecretPattern(
        SecretType.STRIPE_KEY,
        re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
        "Stripe Live Secret Key",
        "critical",
    ),
    SecretPattern(
        SecretType.SENDGRID_KEY,
        re.compile(r"SG\.[0-9A-Za-z\-_]{22}\.[0-9A-Za-z\-_]{43}"),
        "SendGrid API Key",
        "critical",
    ),
    SecretPattern(
        SecretType.TWILIO_KEY,
        re.compile(r"SK[0-9a-fA-F]{32}"),
        "Twilio API Key",
        "high",
    ),
    SecretPattern(
        SecretType.DATABASE_URL,
        re.compile(
            r"(postgres|mysql|mongodb|redis)://[^:]+:[^@]+@[^/]+",
            re.IGNORECASE,
        ),
        "Database Connection URL with credentials",
        "critical",
    ),
    SecretPattern(
        SecretType.PASSWORD_IN_URL,
        re.compile(r"://[^:]+:([^@]{8,})@"),
        "Password embedded in URL",
        "high",
    ),
    SecretPattern(
        SecretType.PRIVATE_KEY,
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        "Private Key",
        "critical",
    ),
    SecretPattern(
        SecretType.JWT_TOKEN,
        re.compile(r"eyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_.+/=]*"),
        "JWT Token",
        "medium",
        ["example", "test", "mock"],
    ),
    SecretPattern(
        SecretType.ANTHROPIC_KEY,
        re.compile(r"sk-ant-[a-zA-Z0-9\-_]{32,}"),
        "Anthropic API Key",
        "critical",
    ),
    SecretPattern(
        SecretType.OPENAI_KEY,
        re.compile(r"sk-[a-zA-Z0-9]{32,}"),
        "OpenAI API Key",
        "critical",
        ["sk-ant"],  # Avoid matching Anthropic keys
    ),
    SecretPattern(
        SecretType.DATADOG_KEY,
        re.compile(r"[a-f0-9]{32}", re.IGNORECASE),
        "Possible Datadog API/App Key",
        "medium",
        ["hash", "checksum", "md5", "uuid"],
    ),
    SecretPattern(
        SecretType.PAGERDUTY_KEY,
        re.compile(r"[a-zA-Z0-9+/]{20}", re.IGNORECASE),
        "Possible PagerDuty Integration Key",
        "medium",
        ["base64", "encoded"],
    ),
    SecretPattern(
        SecretType.SENTRY_DSN,
        re.compile(r"https://[a-f0-9]+@[a-z0-9.]+\.ingest\.sentry\.io/[0-9]+"),
        "Sentry DSN",
        "medium",
    ),
    SecretPattern(
        SecretType.GENERIC_API_KEY,
        re.compile(
            r'(?i)(api[_-]?key|apikey|api[_-]?secret|auth[_-]?token)\s*[=:]\s*["\']?([a-zA-Z0-9_\-]{20,})["\']?'
        ),
        "Generic API Key assignment",
        "medium",
    ),
    SecretPattern(
        SecretType.GENERIC_SECRET,
        re.compile(
            r'(?i)(secret|password|passwd|pwd|token|credential)\s*[=:]\s*["\']?([^"\'\s]{8,})["\']?'
        ),
        "Generic secret assignment",
        "medium",
        ["example", "placeholder", "your_", "xxx", "***"],
    ),
]

EXCLUDED_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".rar",
    ".pdf", ".doc", ".docx",
    ".lock", ".sum",
})

EXCLUDED_FILES = frozenset({
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "go.sum",
    "Cargo.lock",
})

EXCLUDED_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
})


def get_secret_patterns() -> list[SecretPattern]:
    """Get all secret detection patterns."""
    return SECRET_PATTERNS.copy()


class SecretsScanner:
    """Scans content and files for secrets."""

    def __init__(
        self,
        patterns: list[SecretPattern] | None = None,
        exclude_extensions: frozenset[str] | None = None,
        exclude_files: frozenset[str] | None = None,
        exclude_dirs: frozenset[str] | None = None,
    ):
        self.patterns = patterns or SECRET_PATTERNS
        self.exclude_extensions = exclude_extensions or EXCLUDED_EXTENSIONS
        self.exclude_files = exclude_files or EXCLUDED_FILES
        self.exclude_dirs = exclude_dirs or EXCLUDED_DIRS

    def scan_content(
        self,
        content: str,
        file_path: str = "",
    ) -> list[SecretFinding]:
        """Scan text content for secrets.

        Args:
            content: The text content to scan
            file_path: Optional file path for context

        Returns:
            List of SecretFinding objects
        """
        findings: list[SecretFinding] = []
        lines = content.split("\n")

        for line_num, line in enumerate(lines, 1):
            line_lower = line.lower()

            if any(hint in line_lower for hint in ["example", "test", "mock", "fake", "dummy"]):
                continue

            for pattern in self.patterns:
                matches = pattern.pattern.finditer(line)

                for match in matches:
                    matched_text = match.group(0)

                    if pattern.false_positive_hints:
                        if any(hint in line_lower for hint in pattern.false_positive_hints):
                            continue

                    finding = SecretFinding(
                        secret_type=pattern.secret_type,
                        severity=pattern.severity,
                        line_number=line_num,
                        line_content=line.strip(),
                        matched_text=matched_text,
                        file_path=file_path,
                        description=pattern.description,
                        remediation=self._get_remediation(pattern.secret_type),
                    )
                    findings.append(finding)

        return findings

    def scan_file(self, file_path: Path | str) -> list[SecretFinding]:
        """Scan a file for secrets.

        Args:
            file_path: Path to the file

        Returns:
            List of SecretFinding objects
        """
        path = Path(file_path)

        if not path.exists() or not path.is_file():
            return []

        if path.suffix.lower() in self.exclude_extensions:
            return []

        if path.name in self.exclude_files:
            return []

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            log.debug("Could not read %s: %s", path, e)
            return []

        return self.scan_content(content, str(path))

    def scan_directory(
        self,
        directory: Path | str,
        recursive: bool = True,
    ) -> list[SecretFinding]:
        """Scan a directory for secrets.

        Args:
            directory: Path to the directory
            recursive: Whether to scan subdirectories

        Returns:
            List of SecretFinding objects
        """
        path = Path(directory)
        findings: list[SecretFinding] = []

        if not path.exists() or not path.is_dir():
            return findings

        pattern_str = "**/*" if recursive else "*"
        for file_path in path.glob(pattern_str):
            if any(excluded in file_path.parts for excluded in self.exclude_dirs):
                continue

            if file_path.is_file():
                findings.extend(self.scan_file(file_path))

        return findings

    def scan_git_staged(self, repo_path: Path | str = ".") -> list[SecretFinding]:
        """Scan the staged content of a git repository for secrets.

        Args:
            repo_path: Path to the git repository

        Returns:
            List of SecretFinding objects

        Raises:
            SecretsScanError: if the scan could not be performed. It must not
                return ``[]`` for that: an empty list is how the caller spells
                "clean", and this is the check standing between a credential and
                a public remote. This previously caught every exception and
                returned ``[]``, so a scan that never ran was indistinguishable
                from one that found nothing -- and the hook printed "No secrets
                detected" either way.

        Two further corrections over the version this replaces:

        *   It reads each file's **staged blob** (``git show :path``) rather than
            the copy on disk. Those differ whenever a file is edited after being
            staged, and the staged bytes are the ones about to be committed --
            ``git add`` a key, then delete the line from the working tree, and
            the old code scanned the clean copy and passed the key through.
        *   Paths come back NUL-separated (``-z``). Without it git quotes and
            escapes any path containing a newline, quote, or non-ASCII byte, and
            splitting that on newlines yields paths that do not exist -- which
            `scan_file` skipped silently, again as "clean".
        """
        import subprocess

        # ACMR: added, copied, modified, renamed -- everything whose content is
        # entering the commit. Deletions are excluded because there is no staged
        # blob to read, and removing a file cannot introduce a secret.
        try:
            listing = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
                capture_output=True,
                text=True,
                cwd=repo_path,
            )
        except OSError as exc:  # git missing, cwd gone
            raise SecretsScanError(f"could not run git in {repo_path}: {exc}") from exc

        if listing.returncode != 0:
            raise SecretsScanError(
                f"git diff --cached failed in {repo_path} "
                f"(exit {listing.returncode}): {listing.stderr.strip()}"
            )

        findings: list[SecretFinding] = []
        for name in listing.stdout.split("\0"):
            if not name:
                continue

            path = Path(name)
            if path.suffix.lower() in self.exclude_extensions:
                continue
            if path.name in self.exclude_files:
                continue
            if any(excluded in path.parts for excluded in self.exclude_dirs):
                continue

            blob = subprocess.run(
                ["git", "show", f":{name}"],
                capture_output=True,
                cwd=repo_path,
            )
            if blob.returncode != 0:
                raise SecretsScanError(
                    f"could not read staged content of {name!r}: "
                    f"{blob.stderr.decode('utf-8', 'replace').strip()}"
                )

            # errors="ignore" so a binary blob degrades to whatever text it
            # contains instead of aborting the scan of every other file.
            content = blob.stdout.decode("utf-8", errors="ignore")
            findings.extend(self.scan_content(content, name))

        return findings

    def _get_remediation(self, secret_type: SecretType) -> str:
        """Get remediation advice for a secret type."""
        remediations = {
            SecretType.AWS_ACCESS_KEY: "Rotate the AWS access key immediately via IAM console",
            SecretType.AWS_SECRET_KEY: "Rotate AWS credentials and use environment variables or AWS Secrets Manager",
            SecretType.GITHUB_TOKEN: "Revoke the token at github.com/settings/tokens and create a new one",
            SecretType.GITHUB_PAT: "Revoke the PAT at github.com/settings/tokens and create a new fine-grained token",
            SecretType.GOOGLE_API_KEY: "Restrict or regenerate the key at console.cloud.google.com",
            SecretType.GCP_SERVICE_ACCOUNT: "Delete and recreate the service account key, use Workload Identity if possible",
            SecretType.SLACK_TOKEN: "Revoke the token in your Slack app settings",
            SecretType.STRIPE_KEY: "Roll the API key in the Stripe dashboard",
            SecretType.DATABASE_URL: "Change database password and use environment variables",
            SecretType.PRIVATE_KEY: "Generate a new key pair and revoke the compromised one",
            SecretType.ANTHROPIC_KEY: "Regenerate API key at console.anthropic.com",
            SecretType.OPENAI_KEY: "Regenerate API key at platform.openai.com",
        }
        return remediations.get(
            secret_type,
            "Remove the secret from code and use environment variables or a secrets manager",
        )


_scanner: SecretsScanner | None = None


def _get_scanner() -> SecretsScanner:
    """Get or create the global scanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = SecretsScanner()
    return _scanner


def scan_for_secrets(
    content: str,
    file_path: str = "",
) -> list[SecretFinding]:
    """Scan content for secrets using the default scanner.

    Args:
        content: Text content to scan
        file_path: Optional file path for context

    Returns:
        List of SecretFinding objects
    """
    return _get_scanner().scan_content(content, file_path)


def is_safe_to_commit(
    repo_path: Path | str = ".",
    block_on_severity: str = "high",
) -> tuple[bool, list[SecretFinding]]:
    """Check if staged files are safe to commit.

    Args:
        repo_path: Path to the git repository
        block_on_severity: Minimum severity to block commit

    Returns:
        Tuple of (is_safe, findings)

    Raises:
        SecretsScanError: if the staged content could not be read. Deliberately
            propagated rather than reported as safe -- see `scan_git_staged`.
            Callers gating a commit on this should treat the exception as a
            block, not as a pass.
    """
    findings = _get_scanner().scan_git_staged(repo_path)

    severity_levels = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    block_level = severity_levels.get(block_on_severity, 3)

    blocking_findings = [
        f for f in findings
        if severity_levels.get(f.severity, 0) >= block_level
    ]

    return len(blocking_findings) == 0, findings
