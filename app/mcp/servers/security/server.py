"""Security Scanning MCP server — vulnerability detection and security analysis.

Provides security scanning capabilities for dependencies, container images,
secrets detection, and code security analysis. Integrates with common security
tools and vulnerability databases.

Tools:
  - scan_dependencies:      Scan dependencies for known vulnerabilities
  - scan_secrets:           Detect hardcoded secrets and credentials
  - scan_dockerfile:        Security audit of Dockerfile
  - scan_kubernetes:        Security audit of Kubernetes manifests
  - get_cve_details:        Get details about a specific CVE
  - security_summary:       Get overall security posture summary

Run standalone:
    python -m app.mcp.servers.security.server
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("security")

REPO_PATH = Path(os.environ.get("TARGET_REPO_PATH", "./sample_repo")).resolve()

SECRET_PATTERNS: list[tuple[str, str, str]] = [
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID", "high"),
    (r"(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]", "AWS Secret Key", "critical"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token", "high"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token", "high"),
    (r"ghu_[a-zA-Z0-9]{36}", "GitHub User Token", "high"),
    (r"ghs_[a-zA-Z0-9]{36}", "GitHub Server Token", "high"),
    (r"ghr_[a-zA-Z0-9]{36}", "GitHub Refresh Token", "high"),
    (r"xox[baprs]-[0-9a-zA-Z-]{10,}", "Slack Token", "high"),
    (r"sk-[a-zA-Z0-9]{48}", "OpenAI API Key", "high"),
    (r"sk-ant-[a-zA-Z0-9-]{40,}", "Anthropic API Key", "high"),
    (r"(?i)stripe.{0,20}sk_live_[0-9a-zA-Z]{24}", "Stripe Live Key", "critical"),
    (r"sq0atp-[0-9A-Za-z\-_]{22}", "Square Access Token", "high"),
    (r"(?i)postgres://[^:]+:[^@]+@", "PostgreSQL Connection String", "high"),
    (r"(?i)mysql://[^:]+:[^@]+@", "MySQL Connection String", "high"),
    (r"(?i)mongodb(\+srv)?://[^:]+:[^@]+@", "MongoDB Connection String", "high"),
    (r"(?i)redis://:[^@]+@", "Redis Connection String", "medium"),
    (r"-----BEGIN (RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----", "Private Key", "critical"),
    (r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{8,}['\"]", "Hardcoded Password", "high"),
    (r"(?i)api[_-]?key\s*[=:]\s*['\"][a-zA-Z0-9]{20,}['\"]", "API Key", "medium"),
    (r"(?i)secret[_-]?key\s*[=:]\s*['\"][a-zA-Z0-9]{20,}['\"]", "Secret Key", "high"),
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer Token", "medium"),
]

DOCKERFILE_ISSUES: list[tuple[str, str, str, str]] = [
    (r"FROM\s+\S+:latest", "Using :latest tag", "Pin to a specific version for reproducibility", "medium"),
    (r"FROM\s+\S+\s*$", "Untagged base image", "Specify a version tag", "medium"),
    (r"USER\s+root", "Running as root", "Use a non-root user", "high"),
    (r"chmod\s+777", "World-writable permissions", "Use restrictive permissions (755 or less)", "high"),
    (r"curl.*\|\s*sh", "Piping curl to shell", "Download and verify before executing", "high"),
    (r"wget.*\|\s*sh", "Piping wget to shell", "Download and verify before executing", "high"),
    (r"ADD\s+https?://", "Using ADD for remote files", "Use curl/wget + COPY for better security", "medium"),
    (r"(?i)password\s*=", "Hardcoded password in Dockerfile", "Use secrets or build args", "critical"),
    (r"ARG\s+\S*PASSWORD", "Password in build arg", "Build args are visible in image history", "high"),
    (r"ENV\s+\S*PASSWORD", "Password in ENV", "Use runtime secrets instead", "high"),
    (r"EXPOSE\s+22\b", "SSH port exposed", "SSH in containers is usually unnecessary", "medium"),
    (r"apt-get\s+install(?!.*--no-install-recommends)", "apt-get without --no-install-recommends",
     "Add --no-install-recommends to reduce attack surface", "low"),
]

K8S_ISSUES: list[tuple[str, str, str, str]] = [
    (r"privileged:\s*true", "Privileged container", "Avoid privileged mode unless absolutely necessary", "critical"),
    (r"hostNetwork:\s*true", "Host network access", "Use pod networking instead", "high"),
    (r"hostPID:\s*true", "Host PID namespace", "Isolate from host PID namespace", "high"),
    (r"hostIPC:\s*true", "Host IPC namespace", "Isolate from host IPC namespace", "high"),
    (r"allowPrivilegeEscalation:\s*true", "Privilege escalation allowed",
     "Set allowPrivilegeEscalation: false", "high"),
    (r"readOnlyRootFilesystem:\s*false", "Writable root filesystem",
     "Use readOnlyRootFilesystem: true", "medium"),
    (r"runAsNonRoot:\s*false", "Running as root", "Set runAsNonRoot: true", "high"),
    (r"capabilities:\s*\n\s*add:\s*\n\s*-\s*ALL", "All capabilities added",
     "Only add required capabilities", "critical"),
    (r"image:\s*\S+:latest", "Using :latest tag", "Pin to specific image digest or version", "medium"),
    (r"imagePullPolicy:\s*Always", "Always pull policy", "Consider IfNotPresent for stability", "low"),
    (r'password:\s*["\']?[^"\'$]+["\']?', "Hardcoded password", "Use Secrets for sensitive data", "critical"),
    (r"secretKeyRef:\s*\n\s*name:\s*\S+\s*\n\s*key:\s*\S+\s*\n\s*optional:\s*true",
     "Optional secret reference", "Required secrets should not be optional", "medium"),
]

KNOWN_VULNS: dict[str, dict] = {
    "CVE-2021-44228": {
        "id": "CVE-2021-44228",
        "name": "Log4Shell",
        "severity": "critical",
        "cvss": 10.0,
        "description": "Apache Log4j2 JNDI features do not protect against attacker controlled LDAP/RMI",
        "affected": ["log4j-core < 2.17.0"],
        "fixed_in": "2.17.0",
        "references": ["https://nvd.nist.gov/vuln/detail/CVE-2021-44228"],
    },
    "CVE-2023-44487": {
        "id": "CVE-2023-44487",
        "name": "HTTP/2 Rapid Reset",
        "severity": "high",
        "cvss": 7.5,
        "description": "HTTP/2 protocol allows denial of service via rapid stream resets",
        "affected": ["Various HTTP/2 implementations"],
        "fixed_in": "Varies by implementation",
        "references": ["https://nvd.nist.gov/vuln/detail/CVE-2023-44487"],
    },
    "CVE-2024-3094": {
        "id": "CVE-2024-3094",
        "name": "XZ Utils Backdoor",
        "severity": "critical",
        "cvss": 10.0,
        "description": "Malicious code in xz/liblzma library allowing unauthorized access",
        "affected": ["xz-utils 5.6.0", "xz-utils 5.6.1"],
        "fixed_in": "5.6.1+really5.4.5",
        "references": ["https://nvd.nist.gov/vuln/detail/CVE-2024-3094"],
    },
}


def _safe_path(relative: str) -> Path:
    """Resolve path and ensure it stays inside REPO_PATH."""
    root = Path(os.path.realpath(REPO_PATH))
    candidate = Path(os.path.realpath(REPO_PATH / relative))
    if candidate != root:
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError(f"path '{relative}' escapes the repository sandbox")
    return candidate


def _safe_glob(pattern: str) -> str:
    """Reject glob patterns that would steer a scan outside REPO_PATH.

    Only the `path` argument used to be sandboxed; the caller-supplied glob
    went straight to `Path.glob`, so "../../*.yaml" read manifests from outside
    the repo and an absolute pattern crashed the tool with pathlib's
    NotImplementedError instead of reporting a refusal. Patterns must be
    relative and free of ".." segments before they reach the filesystem.
    """
    if os.path.isabs(pattern) or pattern.startswith("~"):
        raise ValueError(f"pattern '{pattern}' must be relative to the repository root")
    if ".." in Path(pattern).parts:
        raise ValueError(f"pattern '{pattern}' escapes the repository sandbox")
    return pattern


def _contained(file_path: Path) -> str | None:
    """Return the repo-relative path of a file, or None if it is outside.

    `Path.relative_to` is purely lexical, so it relabelled an escaped file as
    "../creds.yaml" rather than raising. Resolving with realpath first closes
    that and also catches the remaining route into another tree: a file inside
    the repo that is itself a symlink to somewhere else.
    """
    root = Path(os.path.realpath(REPO_PATH))
    try:
        return str(Path(os.path.realpath(file_path)).relative_to(root))
    except ValueError:
        return None


# Keys whose value is a credential rather than a setting; matched per line so a
# manifest finding can point at the problem without quoting the secret itself.
_SENSITIVE_ASSIGNMENT = re.compile(
    r"""^(?P<prefix>\s*[\w.\-/]*(?:password|passwd|pwd|token|secret|api[_-]?key)[\w.\-/]*\s*:\s*)
         (?P<quote>["']?)(?P<value>.*?)(?P=quote)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _redact_manifest_line(line: str) -> str:
    """Mask the value of a credential assignment before reporting the line.

    scan_secrets already funnels every match through _redact_secret, but
    scan_kubernetes echoed `line.strip()[:100]` verbatim, so a "Hardcoded
    password" finding published the password it was complaining about. The
    key, quoting and surrounding text are kept so the finding still reads as
    the offending line.
    """
    stripped = line.strip()
    match = _SENSITIVE_ASSIGNMENT.match(stripped)
    if not match or not match.group("value"):
        return stripped
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}{_redact_secret(match.group('value'))}{quote}"


def _scan_file_for_secrets(file_path: Path) -> list[dict]:
    """Scan a single file for secret patterns."""
    findings = []
    try:
        content = file_path.read_text(errors="ignore")
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            for pattern, name, severity in SECRET_PATTERNS:
                if re.search(pattern, line):
                    match = re.search(pattern, line)
                    findings.append({
                        "file": str(file_path.relative_to(REPO_PATH)),
                        "line": i,
                        "type": name,
                        "severity": severity,
                        "match": _redact_secret(match.group(0) if match else line.strip()),
                    })
    except (OSError, UnicodeDecodeError):
        pass
    return findings


def _redact_secret(secret: str) -> str:
    """Redact most of a secret, showing only prefix."""
    if len(secret) > 8:
        return secret[:4] + "*" * (len(secret) - 8) + secret[-4:]
    return "*" * len(secret)


def _parse_requirements(content: str) -> list[tuple[str, str]]:
    """Parse requirements.txt format into (package, version) tuples."""
    deps = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"([a-zA-Z0-9_-]+)\s*([=<>!~]+\s*[\d.]+)?", line)
        if match:
            pkg = match.group(1).lower()
            ver = match.group(2).strip() if match.group(2) else "any"
            deps.append((pkg, ver))
    return deps


def _parse_package_json(content: str) -> list[tuple[str, str]]:
    """Parse package.json dependencies."""
    deps = []
    try:
        data = json.loads(content)
        for dep_type in ("dependencies", "devDependencies"):
            for pkg, ver in data.get(dep_type, {}).items():
                deps.append((pkg.lower(), ver))
    except json.JSONDecodeError:
        pass
    return deps


@mcp.tool()
def scan_dependencies(path: str = ".") -> dict:
    """Scan project dependencies for known vulnerabilities.

    Analyzes requirements.txt, package.json, go.mod, Cargo.toml, etc.
    for packages with known security issues.

    Args:
        path: Path to scan (relative to repo root)

    Returns:
        List of vulnerabilities found with severity and remediation
    """
    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    findings: list[dict] = []
    scanned_files: list[str] = []

    vuln_packages = {
        "log4j-core": [("< 2.17.0", "CVE-2021-44228", "critical")],
        "lodash": [("< 4.17.21", "CVE-2021-23337", "high")],
        "minimist": [("< 1.2.6", "CVE-2021-44906", "critical")],
        "node-fetch": [("< 2.6.7", "CVE-2022-0235", "high")],
        "axios": [("< 0.21.2", "CVE-2021-3749", "high")],
        "django": [("< 3.2.25", "CVE-2024-27351", "high")],
        "flask": [("< 2.2.5", "CVE-2023-30861", "high")],
        "requests": [("< 2.31.0", "CVE-2023-32681", "medium")],
        "cryptography": [("< 41.0.0", "CVE-2023-38325", "high")],
        "pillow": [("< 10.0.1", "CVE-2023-44271", "high")],
    }

    for req_file in target.rglob("requirements*.txt"):
        scanned_files.append(str(req_file.relative_to(REPO_PATH)))
        try:
            content = req_file.read_text()
            deps = _parse_requirements(content)
            for pkg, ver in deps:
                if pkg in vuln_packages:
                    for vuln_ver, cve, severity in vuln_packages[pkg]:
                        findings.append({
                            "file": str(req_file.relative_to(REPO_PATH)),
                            "package": pkg,
                            "installed_version": ver,
                            "vulnerable_range": vuln_ver,
                            "cve": cve,
                            "severity": severity,
                            "recommendation": f"Upgrade {pkg} to latest version",
                        })
        except OSError:
            pass

    for pkg_file in target.rglob("package.json"):
        if "node_modules" in str(pkg_file):
            continue
        scanned_files.append(str(pkg_file.relative_to(REPO_PATH)))
        try:
            content = pkg_file.read_text()
            deps = _parse_package_json(content)
            for pkg, ver in deps:
                if pkg in vuln_packages:
                    for vuln_ver, cve, severity in vuln_packages[pkg]:
                        findings.append({
                            "file": str(pkg_file.relative_to(REPO_PATH)),
                            "package": pkg,
                            "installed_version": ver,
                            "vulnerable_range": vuln_ver,
                            "cve": cve,
                            "severity": severity,
                            "recommendation": f"Run: npm audit fix or upgrade {pkg}",
                        })
        except OSError:
            pass

    for lock_file in ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]:
        if (target / lock_file).exists():
            scanned_files.append(lock_file)

    critical = sum(1 for f in findings if f["severity"] == "critical")
    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")

    return {
        "scanned_files": scanned_files,
        "total_vulnerabilities": len(findings),
        "by_severity": {"critical": critical, "high": high, "medium": medium},
        "findings": findings,
        "recommendations": [
            "Run 'npm audit' or 'pip-audit' for comprehensive scanning",
            "Consider using Dependabot or Renovate for automated updates",
            "Review and update dependencies regularly",
        ] if findings else ["No known vulnerabilities detected in scanned files"],
    }


@mcp.tool()
def scan_secrets(path: str = ".", max_files: int | str = 100) -> dict:
    """Scan repository for hardcoded secrets and credentials.

    Searches for API keys, passwords, tokens, and other sensitive data
    that should not be committed to version control.

    Args:
        path: Path to scan (relative to repo root)
        max_files: Maximum number of files to scan

    Returns:
        List of potential secrets found with location and type
    """
    try:
        max_files = int(max_files)
    except (TypeError, ValueError):
        max_files = 100

    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    findings: list[dict] = []
    scanned_count = 0
    skipped_extensions = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                          ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".exe", ".dll",
                          ".so", ".pyc", ".pyo", ".class", ".jar"}
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

    for file_path in target.rglob("*"):
        if scanned_count >= max_files:
            break
        if not file_path.is_file():
            continue
        if any(d in file_path.parts for d in skip_dirs):
            continue
        if file_path.suffix.lower() in skipped_extensions:
            continue

        scanned_count += 1
        findings.extend(_scan_file_for_secrets(file_path))

    critical = sum(1 for f in findings if f["severity"] == "critical")
    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")

    return {
        "files_scanned": scanned_count,
        "secrets_found": len(findings),
        "by_severity": {"critical": critical, "high": high, "medium": medium},
        "findings": findings[:50],
        "truncated": len(findings) > 50,
        "recommendations": [
            "Remove secrets and rotate compromised credentials immediately",
            "Use environment variables or secret management services",
            "Add .gitignore rules for sensitive files",
            "Consider using git-secrets or pre-commit hooks",
        ] if findings else ["No hardcoded secrets detected"],
    }


@mcp.tool()
def scan_dockerfile(dockerfile_path: str = "Dockerfile") -> dict:
    """Security audit of a Dockerfile.

    Checks for common security issues like running as root,
    using :latest tags, exposing sensitive information, etc.

    Args:
        dockerfile_path: Path to Dockerfile (relative to repo root)

    Returns:
        Security findings with severity and recommendations
    """
    target = _safe_path(dockerfile_path)
    if not target.exists():
        return {"error": f"Dockerfile not found at '{dockerfile_path}'"}

    try:
        content = target.read_text()
    except OSError as e:
        return {"error": f"Failed to read Dockerfile: {e}"}

    findings: list[dict] = []
    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        for pattern, issue, recommendation, severity in DOCKERFILE_ISSUES:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append({
                    "line": i,
                    "issue": issue,
                    "content": line.strip()[:100],
                    "severity": severity,
                    "recommendation": recommendation,
                })

    has_user = any("USER" in line and "root" not in line.lower()
                   for line in lines if line.strip() and not line.strip().startswith("#"))
    has_healthcheck = any("HEALTHCHECK" in line for line in lines)

    if not has_user:
        findings.append({
            "line": 0,
            "issue": "No non-root USER specified",
            "severity": "high",
            "recommendation": "Add a non-root USER instruction for security",
        })

    critical = sum(1 for f in findings if f["severity"] == "critical")
    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")

    score = max(0, 100 - (critical * 25) - (high * 15) - (medium * 5))

    return {
        "file": dockerfile_path,
        "security_score": score,
        "has_nonroot_user": has_user,
        "has_healthcheck": has_healthcheck,
        "by_severity": {"critical": critical, "high": high, "medium": medium},
        "findings": findings,
        "recommendations": [
            "Use multi-stage builds to reduce attack surface",
            "Pin base image versions with SHA digests",
            "Scan built images with Trivy or Snyk",
        ],
    }


@mcp.tool()
def scan_kubernetes(path: str = ".", manifest_pattern: str = "*.yaml") -> dict:
    """Security audit of Kubernetes manifests.

    Checks for security misconfigurations like privileged containers,
    missing security contexts, exposed secrets, etc.

    Args:
        path: Path to scan for manifests
        manifest_pattern: Glob pattern for manifest files. Must be relative and
            free of ".." — a pattern that would leave the repo is refused.

    Returns:
        Security findings organized by manifest file, with credential values
        redacted
    """
    manifest_pattern = _safe_glob(manifest_pattern)
    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    findings: list[dict] = []
    scanned_files: list[str] = []

    k8s_dirs = ["k8s", "kubernetes", "manifests", "deploy", "deployments", "."]
    manifest_files: list[tuple[Path, str]] = []

    for k8s_dir in k8s_dirs:
        dir_path = target / k8s_dir if k8s_dir != "." else target
        if dir_path.exists():
            globbed = list(dir_path.glob(manifest_pattern)) + list(dir_path.glob("*.yml"))
            # Filter before the 20-file cap, so out-of-repo matches cannot push
            # the real manifests out of the budget.
            for candidate in globbed:
                rel_path = _contained(candidate)
                if rel_path is not None:
                    manifest_files.append((candidate, rel_path))

    for manifest, rel_path in manifest_files[:20]:
        try:
            content = manifest.read_text()
            if "kind:" not in content:
                continue
            scanned_files.append(rel_path)

            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                for pattern, issue, recommendation, severity in K8S_ISSUES:
                    if re.search(pattern, line, re.IGNORECASE):
                        findings.append({
                            "file": rel_path,
                            "line": i,
                            "issue": issue,
                            "content": _redact_manifest_line(line)[:100],
                            "severity": severity,
                            "recommendation": recommendation,
                        })
        except OSError:
            pass

    critical = sum(1 for f in findings if f["severity"] == "critical")
    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")

    return {
        "scanned_files": scanned_files,
        "total_issues": len(findings),
        "by_severity": {"critical": critical, "high": high, "medium": medium},
        "findings": findings,
        "recommendations": [
            "Use Pod Security Standards (restricted profile)",
            "Implement NetworkPolicies to limit pod communication",
            "Use OPA Gatekeeper or Kyverno for policy enforcement",
            "Scan manifests with kubesec or Checkov",
        ],
    }


@mcp.tool()
def get_cve_details(cve_id: str) -> dict:
    """Get details about a specific CVE.

    Args:
        cve_id: CVE identifier (e.g., CVE-2021-44228)

    Returns:
        CVE details including severity, description, and remediation
    """
    cve_id = cve_id.upper().strip()
    if not cve_id.startswith("CVE-"):
        cve_id = f"CVE-{cve_id}"

    if cve_id in KNOWN_VULNS:
        return KNOWN_VULNS[cve_id]

    return {
        "id": cve_id,
        "status": "not_in_cache",
        "message": "CVE not found in local cache. Check NVD for details.",
        "nvd_url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
        "note": "For production use, integrate with NVD API or vulnerability databases",
    }


@mcp.tool()
def security_summary(path: str = ".") -> dict:
    """Get overall security posture summary for a repository.

    Runs all security scans and provides a consolidated report.

    Args:
        path: Path to scan

    Returns:
        Consolidated security summary with overall score and top issues
    """
    deps = scan_dependencies(path)
    secrets = scan_secrets(path, max_files=50)

    dockerfile_result = None
    target = _safe_path(path)
    if (target / "Dockerfile").exists():
        dockerfile_result = scan_dockerfile("Dockerfile")

    k8s_result = None
    for k8s_dir in ["k8s", "kubernetes", "manifests"]:
        if (target / k8s_dir).exists():
            k8s_result = scan_kubernetes(path)
            break

    total_critical = (
        deps.get("by_severity", {}).get("critical", 0) +
        secrets.get("by_severity", {}).get("critical", 0) +
        (dockerfile_result.get("by_severity", {}).get("critical", 0) if dockerfile_result else 0) +
        (k8s_result.get("by_severity", {}).get("critical", 0) if k8s_result else 0)
    )

    total_high = (
        deps.get("by_severity", {}).get("high", 0) +
        secrets.get("by_severity", {}).get("high", 0) +
        (dockerfile_result.get("by_severity", {}).get("high", 0) if dockerfile_result else 0) +
        (k8s_result.get("by_severity", {}).get("high", 0) if k8s_result else 0)
    )

    score = max(0, 100 - (total_critical * 20) - (total_high * 10))

    if score >= 80:
        grade = "A"
        status = "Good"
    elif score >= 60:
        grade = "B"
        status = "Fair"
    elif score >= 40:
        grade = "C"
        status = "Needs Improvement"
    else:
        grade = "D"
        status = "Critical Issues"

    top_issues = []
    for finding in deps.get("findings", [])[:3]:
        top_issues.append(f"Dependency: {finding.get('package')} - {finding.get('cve')}")
    for finding in secrets.get("findings", [])[:2]:
        top_issues.append(f"Secret: {finding.get('type')} in {finding.get('file')}")

    return {
        "overall_score": score,
        "grade": grade,
        "status": status,
        "summary": {
            "total_critical": total_critical,
            "total_high": total_high,
            "dependencies_scanned": len(deps.get("scanned_files", [])),
            "files_scanned_for_secrets": secrets.get("files_scanned", 0),
            "has_dockerfile": dockerfile_result is not None,
            "has_kubernetes": k8s_result is not None,
        },
        "top_issues": top_issues,
        "recommendations": [
            "Address all critical and high severity issues immediately",
            "Set up automated security scanning in CI/CD pipeline",
            "Enable Dependabot or similar for dependency updates",
            "Use pre-commit hooks for secret detection",
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
