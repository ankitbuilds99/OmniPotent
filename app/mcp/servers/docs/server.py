"""Documentation Generation MCP server — auto-generate documentation.

Provides tools to automatically generate documentation from code, APIs,
infrastructure, and configurations. Supports README generation, API docs,
runbooks, and architecture documentation.

Tools:
  - generate_readme:           Generate README.md for a project
  - generate_api_docs:         Generate API documentation from OpenAPI/code
  - generate_runbook:          Generate operational runbook
  - analyze_documentation:     Analyze existing docs and suggest improvements
  - generate_changelog:        Generate changelog from git history

Run standalone:
    python -m app.mcp.servers.docs.server
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docs")

REPO_PATH = Path(os.environ.get("TARGET_REPO_PATH", "./sample_repo")).resolve()


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


def _detect_project_info(repo_path: Path) -> dict:
    """Detect project information for documentation."""
    info: dict[str, object] = {
        "name": repo_path.name,
        "description": "",
        "language": "unknown",
        "framework": "",
        "package_manager": "",
        "has_tests": False,
        "has_ci": False,
        "has_docker": False,
        "scripts": {},
        "dependencies": [],
        "entry_points": [],
    }
    scripts: dict[str, str] = {}
    dependencies: list[str] = []
    entry_points: list[str] = []

    if (repo_path / "package.json").exists():
        try:
            pkg = json.loads((repo_path / "package.json").read_text())
            info["name"] = pkg.get("name", info["name"])
            info["description"] = pkg.get("description", "")
            info["language"] = "JavaScript/TypeScript"
            info["package_manager"] = "npm"
            scripts = pkg.get("scripts", {})
            info["scripts"] = scripts
            deps = pkg.get("dependencies", {})
            if "react" in deps:
                info["framework"] = "React"
            elif "next" in deps:
                info["framework"] = "Next.js"
            elif "express" in deps:
                info["framework"] = "Express"
            elif "vue" in deps:
                info["framework"] = "Vue"
            info["has_tests"] = "test" in scripts
            dependencies = list(deps.keys())[:10]
            info["dependencies"] = dependencies
            if pkg.get("main"):
                entry_points.append(pkg["main"])
            info["entry_points"] = entry_points
        except (json.JSONDecodeError, OSError):
            pass

    elif (repo_path / "pyproject.toml").exists():
        try:
            content = (repo_path / "pyproject.toml").read_text()
            info["language"] = "Python"
            info["package_manager"] = "uv" if (repo_path / "uv.lock").exists() else "pip"
            name_match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if name_match:
                info["name"] = name_match.group(1)
            desc_match = re.search(r'description\s*=\s*"([^"]+)"', content)
            if desc_match:
                info["description"] = desc_match.group(1)
            if "fastapi" in content.lower():
                info["framework"] = "FastAPI"
            elif "flask" in content.lower():
                info["framework"] = "Flask"
            elif "django" in content.lower():
                info["framework"] = "Django"
        except OSError:
            pass

    elif (repo_path / "go.mod").exists():
        info["language"] = "Go"
        info["package_manager"] = "go mod"
        try:
            content = (repo_path / "go.mod").read_text()
            module_match = re.search(r"module\s+(\S+)", content)
            if module_match:
                info["name"] = module_match.group(1).split("/")[-1]
        except OSError:
            pass

    elif (repo_path / "Cargo.toml").exists():
        info["language"] = "Rust"
        info["package_manager"] = "cargo"
        try:
            content = (repo_path / "Cargo.toml").read_text()
            name_match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if name_match:
                info["name"] = name_match.group(1)
            desc_match = re.search(r'description\s*=\s*"([^"]+)"', content)
            if desc_match:
                info["description"] = desc_match.group(1)
        except OSError:
            pass

    info["has_tests"] = (repo_path / "tests").is_dir() or (repo_path / "test").is_dir()
    info["has_ci"] = (repo_path / ".github/workflows").is_dir() or (repo_path / ".gitlab-ci.yml").exists()
    info["has_docker"] = (repo_path / "Dockerfile").exists()

    return info


def _get_directory_structure(repo_path: Path, max_depth: int = 2) -> str:
    """Generate directory tree structure."""
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
    lines = []

    def _walk(path: Path, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return
        entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        entries = [e for e in entries if e.name not in ignore_dirs and not e.name.startswith(".")]

        for i, entry in enumerate(entries[:15]):
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                _walk(entry, prefix + extension, depth + 1)

    lines.append(f"{repo_path.name}/")
    _walk(repo_path)
    return "\n".join(lines[:30])


@mcp.tool()
def generate_readme(
    path: str = ".",
    include_badges: bool = True,
    include_toc: bool = True,
    include_contributing: bool = False,
) -> dict:
    """Generate a README.md file for a project.

    Analyzes the project structure and generates a comprehensive README
    with installation instructions, usage examples, and more.

    Args:
        path: Path to project root
        include_badges: Include status badges (CI, coverage, etc.)
        include_toc: Include table of contents
        include_contributing: Include contributing guidelines section

    Returns:
        Generated README content
    """
    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    info = _detect_project_info(target)
    structure = _get_directory_structure(target)

    sections = []

    sections.append(f"# {info['name']}\n")

    if include_badges:
        badges = []
        if info["has_ci"]:
            badges.append("![CI](https://github.com/USER/REPO/workflows/CI/badge.svg)")
        if info["language"] == "Python":
            badges.append("![Python](https://img.shields.io/badge/python-3.10+-blue.svg)")
        elif info["language"] == "JavaScript/TypeScript":
            badges.append("![Node](https://img.shields.io/badge/node-18+-green.svg)")
        if badges:
            sections.append(" ".join(badges) + "\n")

    if info["description"]:
        sections.append(f"{info['description']}\n")
    else:
        sections.append(f"A {info['language']} project{' using ' + info['framework'] if info['framework'] else ''}.\n")

    if include_toc:
        sections.append("""## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Development](#development)
- [License](#license)
""")

    sections.append("""## Features

- Feature 1: Description
- Feature 2: Description
- Feature 3: Description
""")

    install_section = "## Installation\n\n"
    if info["package_manager"] == "npm":
        install_section += """```bash
# Clone the repository
git clone https://github.com/USER/REPO.git
cd REPO

# Install dependencies
npm install
```
"""
    elif info["package_manager"] == "uv":
        install_section += """```bash
# Clone the repository
git clone https://github.com/USER/REPO.git
cd REPO

# Install dependencies using uv
uv sync
```
"""
    elif info["package_manager"] == "pip":
        install_section += """```bash
# Clone the repository
git clone https://github.com/USER/REPO.git
cd REPO

# Create virtual environment and install
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
"""
    elif info["package_manager"] == "go mod":
        install_section += """```bash
# Clone the repository
git clone https://github.com/USER/REPO.git
cd REPO

# Download dependencies
go mod download

# Build
go build ./...
```
"""
    else:
        install_section += "See project documentation for installation instructions.\n"
    sections.append(install_section)

    usage_section = "## Usage\n\n"
    if info["framework"] == "FastAPI":
        usage_section += """```bash
# Start the development server
uvicorn app.main:app --reload

# Access the API documentation
open http://localhost:8000/docs
```
"""
    elif info["framework"] in ("React", "Next.js"):
        usage_section += """```bash
# Start development server
npm run dev

# Build for production
npm run build
```
"""
    elif "start" in info.get("scripts", {}):
        usage_section += """```bash
npm start
```
"""
    else:
        usage_section += "See documentation for usage instructions.\n"
    sections.append(usage_section)

    dev_section = "## Development\n\n"
    dev_section += f"### Project Structure\n\n```\n{structure}\n```\n\n"
    if info["has_tests"]:
        if info["language"] == "Python":
            dev_section += """### Running Tests

```bash
pytest
```
"""
        elif info["language"] == "JavaScript/TypeScript":
            dev_section += """### Running Tests

```bash
npm test
```
"""
    sections.append(dev_section)

    if include_contributing:
        sections.append("""## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
""")

    sections.append("""## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
""")

    readme_content = "\n".join(sections)

    return {
        "project_info": info,
        "readme": readme_content,
        "file_path": "README.md",
        "notes": [
            "Replace USER/REPO with actual GitHub path",
            "Update feature descriptions",
            "Add specific usage examples",
        ],
    }


@mcp.tool()
def generate_api_docs(
    path: str = ".",
    format: str = "markdown",
) -> dict:
    """Generate API documentation from code or OpenAPI spec.

    Extracts API endpoints, request/response schemas, and generates
    comprehensive API documentation.

    Args:
        path: Path to project or OpenAPI spec file
        format: Output format ("markdown" or "html")

    Returns:
        Generated API documentation
    """
    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    for spec_name in ["openapi.json", "openapi.yaml", "openapi.yml", "swagger.json"]:
        spec_path = target / spec_name
        if spec_path.exists():
            try:
                content = spec_path.read_text()
                if spec_name.endswith(".json"):
                    spec = json.loads(content)
                else:
                    return {
                        "message": f"Found {spec_name} - use a YAML parser for full parsing",
                        "file": spec_name,
                    }

                docs = _generate_openapi_docs(spec)
                return {
                    "source": spec_name,
                    "format": format,
                    "documentation": docs,
                }
            except (json.JSONDecodeError, OSError) as e:
                return {"error": f"Failed to parse {spec_name}: {e}"}

    endpoints = []
    for py_file in target.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text()
            route_matches = re.findall(
                r'@(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
                content,
                re.IGNORECASE
            )
            for method, path in route_matches:
                endpoints.append({
                    "method": method.upper(),
                    "path": path,
                    "file": str(py_file.relative_to(target)),
                })
        except (OSError, UnicodeDecodeError):
            pass

    if not endpoints:
        return {
            "message": "No API endpoints detected",
            "suggestion": "Add an OpenAPI spec or use FastAPI for auto-documentation",
        }

    docs = "# API Documentation\n\n"
    docs += "## Endpoints\n\n"

    for ep in sorted(endpoints, key=lambda x: (x["path"], x["method"])):
        docs += f"### `{ep['method']}` {ep['path']}\n\n"
        docs += f"**Source:** `{ep['file']}`\n\n"
        docs += "**Parameters:** TBD\n\n"
        docs += "**Response:** TBD\n\n"
        docs += "---\n\n"

    return {
        "source": "code_analysis",
        "format": format,
        "endpoints_found": len(endpoints),
        "documentation": docs,
        "notes": [
            "Add OpenAPI spec for complete documentation",
            "Consider using FastAPI for automatic OpenAPI generation",
        ],
    }


def _generate_openapi_docs(spec: dict) -> str:
    """Generate Markdown docs from OpenAPI spec."""
    docs = f"# {spec.get('info', {}).get('title', 'API Documentation')}\n\n"
    docs += f"{spec.get('info', {}).get('description', '')}\n\n"
    docs += f"**Version:** {spec.get('info', {}).get('version', '1.0.0')}\n\n"

    docs += "## Endpoints\n\n"

    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method.startswith("x-"):
                continue
            docs += f"### `{method.upper()}` {path}\n\n"
            docs += f"{details.get('summary', '')}\n\n"
            if details.get("description"):
                docs += f"{details['description']}\n\n"

            params = details.get("parameters", [])
            if params:
                docs += "**Parameters:**\n\n"
                docs += "| Name | In | Type | Required | Description |\n"
                docs += "|------|----|----|----------|-------------|\n"
                for p in params:
                    req = "Yes" if p.get("required") else "No"
                    ptype = p.get("schema", {}).get("type", "string")
                    docs += f"| {p.get('name')} | {p.get('in')} | {ptype} | {req} | {p.get('description', '')} |\n"
                docs += "\n"

            responses = details.get("responses", {})
            if responses:
                docs += "**Responses:**\n\n"
                for code, resp in responses.items():
                    docs += f"- `{code}`: {resp.get('description', '')}\n"
                docs += "\n"

            docs += "---\n\n"

    return docs


@mcp.tool()
def generate_runbook(
    service_name: str,
    include_sections: list[str] | None = None,
) -> dict:
    """Generate an operational runbook for a service.

    Creates a runbook with common operational procedures,
    troubleshooting steps, and emergency contacts.

    Args:
        service_name: Name of the service
        include_sections: Sections to include (default: all)
                          Options: overview, dependencies, alerts, troubleshooting,
                          deployment, rollback, contacts

    Returns:
        Generated runbook content
    """
    all_sections = ["overview", "dependencies", "alerts", "troubleshooting", "deployment", "rollback", "contacts"]
    sections = include_sections or all_sections

    runbook = f"# {service_name} Runbook\n\n"
    runbook += f"**Last Updated:** {__import__('datetime').date.today()}\n\n"
    runbook += "**Owner:** Platform Team\n\n"

    runbook += "## Table of Contents\n\n"
    for s in sections:
        runbook += f"- [{s.title()}](#{s})\n"
    runbook += "\n"

    if "overview" in sections:
        runbook += f"""## Overview

**Service:** {service_name}
**Purpose:** [Brief description of what this service does]
**Repository:** [Link to repository]
**Dashboard:** [Link to monitoring dashboard]

### Architecture

[Brief description of the service architecture]

### Key Metrics

- Request rate: X req/sec
- Latency P99: X ms
- Error rate: < X%

"""

    if "dependencies" in sections:
        runbook += """## Dependencies

### Upstream (services that call us)
- Service A
- Service B

### Downstream (services we call)
- Database (PostgreSQL)
- Cache (Redis)
- External API

### Infrastructure
- Kubernetes cluster: prod-cluster
- Namespace: production
- Load Balancer: ALB

"""

    if "alerts" in sections:
        runbook += f"""## Alerts

### High Error Rate
**Severity:** P1
**Condition:** Error rate > 1% for 5 minutes
**Impact:** Users experiencing failures
**Response:**
1. Check recent deployments
2. Check downstream dependencies
3. Review error logs
4. Consider rollback if deployment-related

### High Latency
**Severity:** P2
**Condition:** P99 latency > 500ms for 5 minutes
**Impact:** Degraded user experience
**Response:**
1. Check database query performance
2. Check cache hit rates
3. Review CPU/memory usage
4. Check for traffic spikes

### Pod Crashloop
**Severity:** P1
**Condition:** Pods restarting > 3 times in 5 minutes
**Impact:** Service degradation or outage
**Response:**
1. Check pod logs: `kubectl logs -n production {service_name}-xxx`
2. Check events: `kubectl describe pod -n production {service_name}-xxx`
3. Check recent config changes
4. Rollback if necessary

"""

    if "troubleshooting" in sections:
        runbook += f"""## Troubleshooting

### Common Issues

#### Service not responding
```bash
# Check pod status
kubectl get pods -n production -l app={service_name}

# Check pod logs
kubectl logs -n production -l app={service_name} --tail=100

# Check service endpoints
kubectl get endpoints -n production {service_name}
```

#### Database connection issues
```bash
# Check if database is reachable
kubectl exec -it {service_name}-xxx -- nc -zv db-host 5432

# Check connection pool
kubectl exec -it {service_name}-xxx -- curl localhost:8080/health/db
```

#### Memory issues
```bash
# Check memory usage
kubectl top pods -n production -l app={service_name}

# Get memory details
kubectl describe pod {service_name}-xxx | grep -A5 "Limits\\|Requests"
```

"""

    if "deployment" in sections:
        runbook += f"""## Deployment

### Standard Deployment
```bash
# Deploy new version
kubectl set image deployment/{service_name} \\
  {service_name}=registry.example.com/{service_name}:$VERSION \\
  -n production

# Watch rollout
kubectl rollout status deployment/{service_name} -n production
```

### Canary Deployment
1. Deploy to canary (10% traffic)
2. Monitor metrics for 15 minutes
3. If healthy, promote to 50%
4. If healthy, promote to 100%
5. If issues, rollback immediately

### Pre-deployment Checklist
- [ ] All tests passing
- [ ] Database migrations applied (if any)
- [ ] Feature flags configured
- [ ] Alerts configured
- [ ] Runbook updated

"""

    if "rollback" in sections:
        runbook += f"""## Rollback

### Quick Rollback
```bash
# Rollback to previous version
kubectl rollout undo deployment/{service_name} -n production

# Verify rollback
kubectl rollout status deployment/{service_name} -n production
```

### Rollback to Specific Version
```bash
# List rollout history
kubectl rollout history deployment/{service_name} -n production

# Rollback to specific revision
kubectl rollout undo deployment/{service_name} --to-revision=N -n production
```

### Post-rollback Actions
1. Verify service is healthy
2. Notify team in Slack
3. Create incident ticket if needed
4. Investigate root cause

"""

    if "contacts" in sections:
        runbook += """## Contacts

### Primary Oncall
- PagerDuty: [Link to escalation policy]
- Slack: #team-channel

### Escalation Path
1. Primary oncall engineer
2. Secondary oncall engineer
3. Team lead
4. Engineering manager

### External Dependencies
- Database team: #database-support
- Infrastructure team: #infra-support
- Security team: #security

"""

    return {
        "service": service_name,
        "sections": sections,
        "runbook": runbook,
        "file_path": f"docs/runbooks/{service_name}.md",
        "notes": [
            "Fill in placeholders with actual values",
            "Update contacts with real team information",
            "Add service-specific troubleshooting steps",
        ],
    }


@mcp.tool()
def analyze_documentation(path: str = ".") -> dict:
    """Analyze existing documentation and suggest improvements.

    Checks for missing documentation, outdated content, and
    suggests improvements.

    Args:
        path: Path to analyze

    Returns:
        Documentation analysis with recommendations
    """
    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    files_found: list[str] = []
    missing: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    score = 100
    recommendations: list[str] = []

    expected_files = [
        ("README.md", "Project overview and getting started"),
        ("CONTRIBUTING.md", "Contribution guidelines"),
        ("LICENSE", "License information"),
        ("CHANGELOG.md", "Version history"),
        ("docs/", "Documentation directory"),
    ]

    for fname, desc in expected_files:
        fpath = target / fname
        if fpath.exists():
            files_found.append(fname)
        else:
            missing.append({"file": fname, "purpose": desc})
            score -= 10

    readme_path = target / "README.md"
    if readme_path.exists():
        try:
            content = readme_path.read_text()

            if len(content) < 500:
                issues.append({
                    "file": "README.md",
                    "issue": "README is too short",
                    "suggestion": "Add more detailed documentation",
                })
                score -= 10

            if "## Installation" not in content and "# Installation" not in content:
                issues.append({
                    "file": "README.md",
                    "issue": "Missing installation instructions",
                    "suggestion": "Add installation section",
                })
                score -= 5

            if "## Usage" not in content and "# Usage" not in content:
                issues.append({
                    "file": "README.md",
                    "issue": "Missing usage instructions",
                    "suggestion": "Add usage examples",
                })
                score -= 5

        except OSError:
            pass

    if (target / "package.json").exists() or (target / "pyproject.toml").exists():
        api_docs_indicators = ["openapi", "swagger", "api-docs", "redoc"]
        has_api_docs = any(
            any(ind in str(f).lower() for ind in api_docs_indicators)
            for f in target.rglob("*")
        )
        if not has_api_docs:
            recommendations.append(
                "Consider adding OpenAPI/Swagger documentation for API endpoints"
            )

    if not (target / "docs").is_dir():
        recommendations.append(
            "Create a docs/ directory for detailed documentation"
        )

    score = max(0, score)

    if score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"

    return {
        "files_found": files_found,
        "missing": missing,
        "issues": issues,
        "score": score,
        "recommendations": recommendations,
        "grade": grade,
    }


@mcp.tool()
def generate_changelog(
    path: str = ".",
    since_tag: str | None = None,
    format: str = "keepachangelog",
) -> dict:
    """Generate changelog from git history.

    Analyzes commit history and generates a changelog following
    the Keep a Changelog format.

    Args:
        path: Path to repository
        since_tag: Generate changelog since this tag (default: last 50 commits)
        format: Output format ("keepachangelog" or "simple")

    Returns:
        Generated changelog content
    """
    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    try:
        if since_tag:
            cmd = ["git", "log", f"{since_tag}..HEAD", "--pretty=format:%s|%h|%an|%ad", "--date=short"]
        else:
            cmd = ["git", "log", "-50", "--pretty=format:%s|%h|%an|%ad", "--date=short"]

        result = subprocess.run(
            cmd,
            cwd=target,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return {"error": "Failed to get git log", "detail": result.stderr}

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                commits.append({
                    "message": parts[0],
                    "hash": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                })

    except subprocess.TimeoutExpired:
        return {"error": "Git command timed out"}
    except FileNotFoundError:
        return {"error": "Git not found or not a git repository"}

    if not commits:
        return {"message": "No commits found", "commits": []}

    categorized: dict[str, list[dict[str, str]]] = {
        "Added": [],
        "Changed": [],
        "Fixed": [],
        "Removed": [],
        "Security": [],
        "Other": [],
    }

    for commit in commits:
        msg = commit["message"].lower()
        if any(kw in msg for kw in ["add", "feat", "new", "create"]):
            categorized["Added"].append(commit)
        elif any(kw in msg for kw in ["fix", "bug", "patch", "resolve"]):
            categorized["Fixed"].append(commit)
        elif any(kw in msg for kw in ["remove", "delete", "drop"]):
            categorized["Removed"].append(commit)
        elif any(kw in msg for kw in ["security", "cve", "vuln"]):
            categorized["Security"].append(commit)
        elif any(kw in msg for kw in ["change", "update", "refactor", "improve"]):
            categorized["Changed"].append(commit)
        else:
            categorized["Other"].append(commit)

    if format == "keepachangelog":
        changelog = "# Changelog\n\n"
        changelog += "All notable changes to this project will be documented in this file.\n\n"
        changelog += "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).\n\n"

        changelog += "## [Unreleased]\n\n"

        for category, items in categorized.items():
            if items:
                changelog += f"### {category}\n\n"
                for item in items:
                    changelog += f"- {item['message']} ({item['hash']})\n"
                changelog += "\n"
    else:
        changelog = "# Changelog\n\n"
        for commit in commits:
            changelog += f"- [{commit['hash']}] {commit['message']} ({commit['date']})\n"

    return {
        "format": format,
        "commits_analyzed": len(commits),
        "changelog": changelog,
        "categorized_counts": {k: len(v) for k, v in categorized.items()},
        "file_path": "CHANGELOG.md",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
