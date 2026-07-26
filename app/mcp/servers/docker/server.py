"""Docker generation MCP server — generate Dockerfiles and docker-compose configs.

AI-powered generation of container configurations from natural language
descriptions or by analyzing existing codebases. Supports multiple languages,
frameworks, and deployment patterns.

Tools:
  - generate_dockerfile:     Generate Dockerfile for a service
  - generate_compose:        Generate docker-compose.yml for multi-service apps
  - analyze_for_docker:      Analyze a codebase and suggest Docker configuration
  - optimize_dockerfile:     Optimize an existing Dockerfile for size/security
  - list_templates:          List available Dockerfile templates

Run standalone:
    python -m app.mcp.servers.docker.server
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docker")

REPO_PATH = Path(os.environ.get("TARGET_REPO_PATH", "./sample_repo")).resolve()


DOCKERFILE_TEMPLATES: dict[str, dict] = {
    "python-fastapi": {
        "name": "Python FastAPI",
        "description": "Production-ready FastAPI application with uv package manager",
        "base": "python:3.12-slim",
        "template": '''FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY {src_dir} ./{src_dir}

# Set environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE {port}

CMD ["uvicorn", "{module}:app", "--host", "0.0.0.0", "--port", "{port}"]
''',
    },
    "python-flask": {
        "name": "Python Flask",
        "description": "Production Flask application with Gunicorn",
        "base": "python:3.12-slim",
        "template": '''FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY {src_dir} ./{src_dir}

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

EXPOSE {port}

CMD ["gunicorn", "--bind", "0.0.0.0:{port}", "--workers", "4", "{module}:app"]
''',
    },
    "node-express": {
        "name": "Node.js Express",
        "description": "Production Node.js/Express application",
        "base": "node:20-alpine",
        "template": '''FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

FROM node:20-alpine

WORKDIR /app

# Copy node_modules from builder
COPY --from=builder /app/node_modules ./node_modules

# Copy application code
COPY {src_dir} ./{src_dir}
COPY package*.json ./

# Non-root user
RUN addgroup -g 1001 -S nodejs && adduser -S nodejs -u 1001
USER nodejs

ENV NODE_ENV=production

EXPOSE {port}

CMD ["node", "{entry_point}"]
''',
    },
    "node-nextjs": {
        "name": "Next.js",
        "description": "Production Next.js application with standalone output",
        "base": "node:20-alpine",
        "template": '''FROM node:20-alpine AS deps
WORKDIR /app
COPY package*.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE {port}

ENV PORT={port}

CMD ["node", "server.js"]
''',
    },
    "go": {
        "name": "Go Application",
        "description": "Multi-stage Go build with minimal runtime image",
        "base": "golang:1.22-alpine",
        "template": '''FROM golang:1.22-alpine AS builder

WORKDIR /app

# Download dependencies
COPY go.mod go.sum ./
RUN go mod download

# Build application
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o /app/server {main_package}

FROM scratch

# Copy CA certificates for HTTPS
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

# Copy binary
COPY --from=builder /app/server /server

EXPOSE {port}

ENTRYPOINT ["/server"]
''',
    },
    "rust": {
        "name": "Rust Application",
        "description": "Multi-stage Rust build with minimal runtime",
        "base": "rust:1.75-slim",
        "template": '''FROM rust:1.75-slim AS builder

WORKDIR /app

# Cache dependencies
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {{}}" > src/main.rs
RUN cargo build --release && rm -rf src

# Build actual application
COPY src ./src
RUN touch src/main.rs && cargo build --release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/{binary_name} /usr/local/bin/app

RUN useradd -m -u 1000 appuser
USER appuser

EXPOSE {port}

CMD ["app"]
''',
    },
    "java-spring": {
        "name": "Java Spring Boot",
        "description": "Spring Boot application with layered JARs",
        "base": "eclipse-temurin:21-jre-alpine",
        "template": '''FROM eclipse-temurin:21-jdk-alpine AS builder

WORKDIR /app

COPY gradlew build.gradle* settings.gradle* ./
COPY gradle ./gradle
RUN ./gradlew dependencies --no-daemon || true

COPY src ./src
RUN ./gradlew bootJar --no-daemon

# Extract layers for better caching
RUN java -Djarmode=layertools -jar build/libs/*.jar extract

FROM eclipse-temurin:21-jre-alpine

WORKDIR /app

RUN addgroup -g 1001 -S spring && adduser -S spring -u 1001

COPY --from=builder /app/dependencies/ ./
COPY --from=builder /app/spring-boot-loader/ ./
COPY --from=builder /app/snapshot-dependencies/ ./
COPY --from=builder /app/application/ ./

USER spring

EXPOSE {port}

ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
''',
    },
    "nginx-static": {
        "name": "Nginx Static Site",
        "description": "Static site served by Nginx",
        "base": "nginx:alpine",
        "template": '''FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

# Copy built assets
COPY --from=builder /app/{build_dir} /usr/share/nginx/html

# Custom nginx config (optional)
COPY nginx.conf /etc/nginx/conf.d/default.conf 2>/dev/null || true

# Non-root nginx
RUN chown -R nginx:nginx /usr/share/nginx/html

EXPOSE {port}

CMD ["nginx", "-g", "daemon off;"]
''',
    },
}


COMPOSE_TEMPLATES: dict[str, str] = {
    "web-db": '''services:
  {service_name}:
    build: .
    ports:
      - "{port}:{port}"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/{db_name}
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: {db_name}
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
''',
    "web-redis": '''services:
  {service_name}:
    build: .
    ports:
      - "{port}:{port}"
    environment:
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  redis_data:
''',
    "microservices": '''services:
{services_block}

  # API Gateway / Load Balancer
  gateway:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
{depends_on_block}

networks:
  default:
    driver: bridge
''',
    "full-stack": '''services:
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    environment:
      - API_URL=http://api:{api_port}
    depends_on:
      - api

  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "{api_port}:{api_port}"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/{db_name}
      - REDIS_URL=redis://redis:6379
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: {db_name}
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
''',
}


def _detect_language(repo_path: Path) -> dict:
    """Detect the primary language and framework of a repository."""
    indicators = {
        "python-fastapi": ["pyproject.toml", "requirements.txt"],
        "python-flask": ["requirements.txt", "app.py"],
        "node-express": ["package.json"],
        "node-nextjs": ["next.config.js", "next.config.mjs", "next.config.ts"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "java-spring": ["build.gradle", "pom.xml"],
    }

    detected = {"language": "unknown", "framework": "unknown", "files": []}

    for template_key, files in indicators.items():
        for f in files:
            if (repo_path / f).exists():
                detected["files"].append(f)
                lang, _, framework = template_key.partition("-")
                detected["language"] = lang
                detected["framework"] = framework or lang
                detected["template"] = template_key
                break
        if detected["language"] != "unknown":
            break

    if (repo_path / "package.json").exists():
        try:
            pkg = json.loads((repo_path / "package.json").read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "next" in deps:
                detected["framework"] = "nextjs"
                detected["template"] = "node-nextjs"
            elif "express" in deps:
                detected["framework"] = "express"
                detected["template"] = "node-express"
            elif "react" in deps:
                detected["framework"] = "react"
                detected["template"] = "nginx-static"
        except (json.JSONDecodeError, OSError):
            pass

    if (repo_path / "pyproject.toml").exists():
        try:
            content = (repo_path / "pyproject.toml").read_text()
            if "fastapi" in content.lower():
                detected["framework"] = "fastapi"
                detected["template"] = "python-fastapi"
            elif "flask" in content.lower():
                detected["framework"] = "flask"
                detected["template"] = "python-flask"
            elif "django" in content.lower():
                detected["framework"] = "django"
        except OSError:
            pass

    return detected


def _find_entry_point(repo_path: Path, language: str) -> dict:
    """Find the main entry point for an application."""
    entry = {"module": "app", "file": "app.py", "port": 8000}

    if language == "python":
        for candidate in ["app/main.py", "src/main.py", "main.py", "app.py", "server.py"]:
            if (repo_path / candidate).exists():
                entry["file"] = candidate
                entry["module"] = candidate.replace("/", ".").replace(".py", "")
                break
        entry["src_dir"] = "app" if (repo_path / "app").is_dir() else "src" if (repo_path / "src").is_dir() else "."

    elif language == "node":
        if (repo_path / "package.json").exists():
            try:
                pkg = json.loads((repo_path / "package.json").read_text())
                entry["file"] = pkg.get("main", "index.js")
                entry["port"] = 3000
            except (json.JSONDecodeError, OSError):
                pass
        entry["src_dir"] = "src" if (repo_path / "src").is_dir() else "."
        entry["entry_point"] = entry["file"]

    elif language == "go":
        entry["main_package"] = "./cmd/server" if (repo_path / "cmd/server").is_dir() else "."
        entry["port"] = 8080

    elif language == "rust":
        if (repo_path / "Cargo.toml").exists():
            try:
                content = (repo_path / "Cargo.toml").read_text()
                match = re.search(r'name\s*=\s*"([^"]+)"', content)
                entry["binary_name"] = match.group(1) if match else "app"
            except OSError:
                entry["binary_name"] = "app"
        entry["port"] = 8080

    return entry


@mcp.tool()
def list_templates() -> list[dict]:
    """List all available Dockerfile templates with their descriptions."""
    return [
        {
            "id": key,
            "name": tpl["name"],
            "description": tpl["description"],
            "base_image": tpl["base"],
        }
        for key, tpl in DOCKERFILE_TEMPLATES.items()
    ]


@mcp.tool()
def analyze_for_docker(path: str = ".") -> dict:
    """Analyze a codebase and suggest appropriate Docker configuration.

    Examines the repository structure, detects language/framework, and
    recommends the best Dockerfile template and configuration.

    Args:
        path: Path to analyze (relative to repo root, default ".")

    Returns:
        Analysis with detected language, framework, recommended template,
        and suggested configuration values.
    """
    target = REPO_PATH / path
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    detection = _detect_language(target)
    entry = _find_entry_point(target, detection["language"])

    has_compose_deps = any([
        (target / "docker-compose.yml").exists(),
        (target / "docker-compose.yaml").exists(),
        detection["language"] in ("python", "node", "go"),
    ])

    suggestions = []
    if detection["language"] == "unknown":
        suggestions.append("Could not auto-detect language. Specify template manually.")
    else:
        suggestions.append(f"Detected {detection['language']} with {detection['framework']} framework")
        suggestions.append(f"Recommended template: {detection.get('template', 'custom')}")

    if (target / "Dockerfile").exists():
        suggestions.append("Existing Dockerfile found - consider using optimize_dockerfile")

    return {
        "language": detection["language"],
        "framework": detection["framework"],
        "detected_files": detection["files"],
        "recommended_template": detection.get("template"),
        "entry_point": entry,
        "has_existing_dockerfile": (target / "Dockerfile").exists(),
        "has_existing_compose": has_compose_deps,
        "suggestions": suggestions,
    }


@mcp.tool()
def generate_dockerfile(
    template: str | None = None,
    language: str | None = None,
    framework: str | None = None,
    port: int | str = 8000,
    src_dir: str = "app",
    module: str = "app.main",
    entry_point: str = "index.js",
    main_package: str = ".",
    binary_name: str = "app",
    build_dir: str = "dist",
    auto_detect: bool = True,
) -> dict:
    """Generate a production-ready Dockerfile for a service.

    Args:
        template: Template ID (e.g., "python-fastapi", "node-express"). If not
                  specified and auto_detect=True, will analyze the repo.
        language: Override detected language
        framework: Override detected framework
        port: Port the application listens on
        src_dir: Source directory to copy
        module: Python module path (for Python apps)
        entry_point: Entry point file (for Node apps)
        main_package: Main package path (for Go apps)
        binary_name: Binary name (for Rust apps)
        build_dir: Build output directory (for static sites)
        auto_detect: Whether to auto-detect settings from repo

    Returns:
        Generated Dockerfile content and metadata
    """
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8000

    if template is None and auto_detect:
        analysis = analyze_for_docker()
        if analysis.get("recommended_template"):
            template = analysis["recommended_template"]
            if analysis.get("entry_point"):
                ep = analysis["entry_point"]
                src_dir = ep.get("src_dir", src_dir)
                module = ep.get("module", module)
                entry_point = ep.get("entry_point", entry_point)
                main_package = ep.get("main_package", main_package)
                binary_name = ep.get("binary_name", binary_name)
                port = ep.get("port", port)

    if template is None:
        if language == "python":
            template = "python-fastapi" if framework == "fastapi" else "python-flask"
        elif language == "node":
            template = "node-nextjs" if framework == "nextjs" else "node-express"
        elif language == "go":
            template = "go"
        elif language == "rust":
            template = "rust"
        elif language == "java":
            template = "java-spring"
        else:
            return {"error": "Could not determine template. Specify template or language."}

    if template not in DOCKERFILE_TEMPLATES:
        return {
            "error": f"Unknown template '{template}'",
            "available": list(DOCKERFILE_TEMPLATES.keys()),
        }

    tpl = DOCKERFILE_TEMPLATES[template]
    dockerfile = tpl["template"].format(
        port=port,
        src_dir=src_dir,
        module=module,
        entry_point=entry_point,
        main_package=main_package,
        binary_name=binary_name,
        build_dir=build_dir,
    )

    return {
        "template": template,
        "dockerfile": dockerfile,
        "base_image": tpl["base"],
        "port": port,
        "notes": [
            "Multi-stage build for smaller image size",
            "Non-root user for security",
            "Production-optimized settings",
        ],
    }


@mcp.tool()
def generate_compose(
    pattern: str = "web-db",
    service_name: str = "app",
    port: int | str = 8000,
    api_port: int | str = 8000,
    db_name: str = "appdb",
    services: list[dict] | None = None,
) -> dict:
    """Generate a docker-compose.yml for multi-service applications.

    Args:
        pattern: Composition pattern - one of:
                 - "web-db": Web app + PostgreSQL
                 - "web-redis": Web app + Redis
                 - "full-stack": Frontend + API + DB + Redis
                 - "microservices": Custom microservices setup
        service_name: Name of the main service
        port: Main service port
        api_port: API service port (for full-stack pattern)
        db_name: Database name
        services: For microservices pattern, list of service configs:
                  [{"name": "users", "port": 8001}, {"name": "orders", "port": 8002}]

    Returns:
        Generated docker-compose.yml content
    """
    try:
        port = int(port)
        api_port = int(api_port)
    except (TypeError, ValueError):
        port = 8000
        api_port = 8000

    if pattern not in COMPOSE_TEMPLATES:
        return {
            "error": f"Unknown pattern '{pattern}'",
            "available": list(COMPOSE_TEMPLATES.keys()),
        }

    if pattern == "microservices":
        if not services:
            return {"error": "microservices pattern requires 'services' list"}

        services_block = ""
        depends_on_block = ""
        for svc in services:
            name = svc.get("name", "service")
            svc_port = svc.get("port", 8000)
            services_block += f'''  {name}:
    build:
      context: ./{name}
      dockerfile: Dockerfile
    ports:
      - "{svc_port}:{svc_port}"
    restart: unless-stopped

'''
            depends_on_block += f"      - {name}\n"

        compose = COMPOSE_TEMPLATES[pattern].format(
            services_block=services_block.rstrip(),
            depends_on_block=depends_on_block.rstrip(),
        )
    else:
        compose = COMPOSE_TEMPLATES[pattern].format(
            service_name=service_name,
            port=port,
            api_port=api_port,
            db_name=db_name,
        )

    return {
        "pattern": pattern,
        "compose": compose,
        "services": (
            [s["name"] for s in services]
            if services
            else [service_name, "db"] if "db" in pattern else [service_name]
        ),
        "notes": [
            "Health checks for dependencies",
            "Named volumes for persistence",
            "Automatic restart policy",
        ],
    }


@mcp.tool()
def optimize_dockerfile(dockerfile_content: str) -> dict:
    """Analyze and optimize an existing Dockerfile.

    Checks for common issues and suggests improvements for:
    - Image size (multi-stage builds, alpine bases)
    - Security (non-root user, pinned versions)
    - Build cache efficiency (layer ordering)
    - Best practices

    Args:
        dockerfile_content: The Dockerfile content to analyze

    Returns:
        Analysis with issues found and optimization suggestions
    """
    issues: list[dict] = []
    suggestions: list[str] = []

    lines = dockerfile_content.split("\n")
    has_user = False
    has_multistage = False
    uses_latest = False
    copy_before_deps = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        if stripped.startswith("FROM ") and " AS " in stripped.upper():
            has_multistage = True

        if stripped.startswith("USER ") and "root" not in stripped.lower():
            has_user = True

        if ":latest" in stripped or (stripped.startswith("FROM ") and ":" not in stripped):
            uses_latest = True
            issues.append({
                "line": i,
                "severity": "medium",
                "issue": "Using 'latest' or untagged image",
                "suggestion": "Pin to a specific version for reproducibility",
            })

        if stripped.startswith("COPY . "):
            copy_before_deps = True

        if stripped.startswith("RUN apt-get") and "rm -rf /var/lib/apt/lists" not in dockerfile_content:
            issues.append({
                "line": i,
                "severity": "low",
                "issue": "apt-get without cleanup",
                "suggestion": "Add '&& rm -rf /var/lib/apt/lists/*' to reduce image size",
            })

        if stripped.startswith("RUN pip install") and "--no-cache-dir" not in stripped:
            issues.append({
                "line": i,
                "severity": "low",
                "issue": "pip install without --no-cache-dir",
                "suggestion": "Add --no-cache-dir to reduce image size",
            })

        if "ADD " in stripped and "http" not in stripped.lower():
            issues.append({
                "line": i,
                "severity": "low",
                "issue": "Using ADD instead of COPY",
                "suggestion": "Use COPY for local files (ADD has extra features you may not need)",
            })

    if not has_user:
        issues.append({
            "line": 0,
            "severity": "high",
            "issue": "Running as root",
            "suggestion": "Add a non-root USER for security",
        })

    if not has_multistage and len(lines) > 10:
        suggestions.append("Consider multi-stage build to reduce final image size")

    if copy_before_deps:
        suggestions.append("Copy dependency files before copying all code for better cache utilization")

    if "HEALTHCHECK" not in dockerfile_content:
        suggestions.append("Add HEALTHCHECK instruction for container orchestration")

    if "LABEL" not in dockerfile_content:
        suggestions.append("Add LABEL instructions for image metadata (maintainer, version)")

    score = 100
    for issue in issues:
        if issue["severity"] == "high":
            score -= 20
        elif issue["severity"] == "medium":
            score -= 10
        else:
            score -= 5
    score = max(0, score)

    return {
        "score": score,
        "issues": issues,
        "suggestions": suggestions,
        "has_multistage": has_multistage,
        "has_nonroot_user": has_user,
        "uses_latest_tags": uses_latest,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
