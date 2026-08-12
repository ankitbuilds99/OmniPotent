"""CI/CD Pipeline Generation MCP server — generate CI/CD configurations.

AI-powered generation of CI/CD pipeline configurations for GitHub Actions,
GitLab CI, CircleCI, and other platforms. Analyzes codebases to suggest
appropriate build, test, and deployment workflows.

Tools:
  - generate_github_actions:  Generate GitHub Actions workflow
  - generate_gitlab_ci:       Generate GitLab CI/CD configuration
  - generate_circleci:        Generate CircleCI configuration
  - analyze_for_cicd:         Analyze codebase and suggest CI/CD config
  - list_cicd_templates:      List available CI/CD templates

Run standalone:
    python -m app.mcp.servers.cicd.server
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from app.runtime_defaults import DEFAULT_REPO_PATH

mcp = FastMCP("cicd")

REPO_PATH = Path(os.environ.get("TARGET_REPO_PATH", DEFAULT_REPO_PATH)).resolve()


GITHUB_ACTIONS_TEMPLATES: dict[str, dict] = {
    "python": {
        "name": "Python CI",
        "description": "Python application with pytest, ruff, and mypy",
        "template": '''name: CI

on:
  push:
    branches: [{default_branch}]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      - name: Sync dependencies
        run: uv sync --extra dev
      - name: Lint (Ruff)
        run: uv run ruff check {src_dirs}
      - name: Type check (Mypy)
        run: uv run mypy {src_dirs}
      - name: Test (Pytest)
        run: uv run pytest -q
''',
    },
    "python-docker": {
        "name": "Python CI + Docker",
        "description": "Python app with tests and Docker build/push",
        "template": '''name: CI

on:
  push:
    branches: [{default_branch}]
  pull_request:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{{{ github.repository }}}}

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      - name: Sync dependencies
        run: uv sync --extra dev
      - name: Lint
        run: uv run ruff check {src_dirs}
      - name: Test
        run: uv run pytest -q

  build:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - name: Log in to Container registry
        uses: docker/login-action@v3
        with:
          registry: ${{{{ env.REGISTRY }}}}
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{{{ env.REGISTRY }}}}/${{{{ env.IMAGE_NAME }}}}
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: ${{{{ github.event_name != 'pull_request' }}}}
          tags: ${{{{ steps.meta.outputs.tags }}}}
          labels: ${{{{ steps.meta.outputs.labels }}}}
''',
    },
    "node": {
        "name": "Node.js CI",
        "description": "Node.js application with npm test and lint",
        "template": '''name: CI

on:
  push:
    branches: [{default_branch}]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "{node_version}"
          cache: npm
      - run: npm ci
      - run: npm run lint
      - run: npm run test
      - run: npm run build
''',
    },
    "node-docker": {
        "name": "Node.js CI + Docker",
        "description": "Node.js app with tests and Docker build/push",
        "template": '''name: CI

on:
  push:
    branches: [{default_branch}]
  pull_request:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{{{ github.repository }}}}

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "{node_version}"
          cache: npm
      - run: npm ci
      - run: npm run lint
      - run: npm run test

  build:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - name: Log in to Container registry
        uses: docker/login-action@v3
        with:
          registry: ${{{{ env.REGISTRY }}}}
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: ${{{{ github.event_name != 'pull_request' }}}}
          tags: ${{{{ env.REGISTRY }}}}/${{{{ env.IMAGE_NAME }}}}:${{{{ github.sha }}}}
''',
    },
    "go": {
        "name": "Go CI",
        "description": "Go application with tests and linting",
        "template": '''name: CI

on:
  push:
    branches: [{default_branch}]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: "{go_version}"
      - name: Lint
        uses: golangci/golangci-lint-action@v4
      - name: Test
        run: go test -race -coverprofile=coverage.out ./...
      - name: Build
        run: go build -v ./...
''',
    },
    "rust": {
        "name": "Rust CI",
        "description": "Rust application with cargo test and clippy",
        "template": '''name: CI

on:
  push:
    branches: [{default_branch}]
  pull_request:

env:
  CARGO_TERM_COLOR: always

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install Rust
        uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy, rustfmt
      - name: Cache cargo
        uses: Swatinem/rust-cache@v2
      - name: Format check
        run: cargo fmt --all -- --check
      - name: Clippy
        run: cargo clippy --all-targets -- -D warnings
      - name: Test
        run: cargo test --all-features
''',
    },
    "terraform": {
        "name": "Terraform CI",
        "description": "Terraform with fmt, validate, and plan",
        "template": '''name: Terraform

on:
  push:
    branches: [{default_branch}]
  pull_request:

jobs:
  terraform:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: {terraform_dir}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "{terraform_version}"
      - name: Terraform Format
        run: terraform fmt -check -recursive
      - name: Terraform Init
        run: terraform init -backend=false
      - name: Terraform Validate
        run: terraform validate
      - name: Terraform Plan
        if: github.event_name == 'pull_request'
        run: terraform plan -no-color
        env:
          TF_VAR_placeholder: "ci-check"
''',
    },
    "deploy-k8s": {
        "name": "Deploy to Kubernetes",
        "description": "Build, push, and deploy to Kubernetes cluster",
        "template": '''name: Deploy

on:
  push:
    branches: [{default_branch}]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{{{ github.repository }}}}

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to Container registry
        uses: docker/login-action@v3
        with:
          registry: ${{{{ env.REGISTRY }}}}
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{{{ env.REGISTRY }}}}/${{{{ env.IMAGE_NAME }}}}:${{{{ github.sha }}}}

      - name: Set up kubectl
        uses: azure/setup-kubectl@v4

      - name: Configure kubeconfig
        run: |
          mkdir -p ~/.kube
          echo "${{{{ secrets.KUBECONFIG }}}}" | base64 -d > ~/.kube/config

      - name: Deploy to Kubernetes
        run: |
          kubectl set image deployment/{deployment_name} \\
            {container_name}=${{{{ env.REGISTRY }}}}/${{{{ env.IMAGE_NAME }}}}:${{{{ github.sha }}}} \\
            -n {namespace}
          kubectl rollout status deployment/{deployment_name} -n {namespace}
''',
    },
}


GITLAB_CI_TEMPLATES: dict[str, dict] = {
    "python": {
        "name": "Python CI",
        "description": "Python application with pytest and linting",
        "template": '''stages:
  - lint
  - test
  - build

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.pip-cache"

cache:
  paths:
    - .pip-cache/
    - .venv/

lint:
  stage: lint
  image: python:3.12-slim
  before_script:
    - pip install uv
    - uv sync --extra dev
  script:
    - uv run ruff check {src_dirs}
    - uv run mypy {src_dirs}

test:
  stage: test
  image: python:3.12-slim
  before_script:
    - pip install uv
    - uv sync --extra dev
  script:
    - uv run pytest --cov={src_dirs} --cov-report=xml
  coverage: '/TOTAL.*\s+(\d+%)/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

build:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  rules:
    - if: $CI_COMMIT_BRANCH == "{default_branch}"
''',
    },
    "node": {
        "name": "Node.js CI",
        "description": "Node.js application with npm",
        "template": '''stages:
  - lint
  - test
  - build

cache:
  paths:
    - node_modules/

lint:
  stage: lint
  image: node:{node_version}
  script:
    - npm ci
    - npm run lint

test:
  stage: test
  image: node:{node_version}
  script:
    - npm ci
    - npm run test -- --coverage
  coverage: '/All files[^|]*\|[^|]*\s+([\d\.]+)/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml

build:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  rules:
    - if: $CI_COMMIT_BRANCH == "{default_branch}"
''',
    },
}


CIRCLECI_TEMPLATES: dict[str, dict] = {
    "python": {
        "name": "Python CI",
        "description": "Python application with pytest",
        "template": '''version: 2.1

orbs:
  python: circleci/python@2.1

jobs:
  test:
    docker:
      - image: cimg/python:3.12
    steps:
      - checkout
      - python/install-packages:
          pkg-manager: pip
      - run:
          name: Lint
          command: |
            pip install ruff
            ruff check {src_dirs}
      - run:
          name: Test
          command: pytest --cov={src_dirs}

  build:
    docker:
      - image: cimg/base:current
    steps:
      - checkout
      - setup_remote_docker
      - run:
          name: Build Docker image
          command: docker build -t $CIRCLE_PROJECT_REPONAME:$CIRCLE_SHA1 .

workflows:
  main:
    jobs:
      - test
      - build:
          requires:
            - test
          filters:
            branches:
              only: {default_branch}
''',
    },
    "node": {
        "name": "Node.js CI",
        "description": "Node.js application",
        "template": '''version: 2.1

orbs:
  node: circleci/node@5.2

jobs:
  test:
    docker:
      - image: cimg/node:{node_version}
    steps:
      - checkout
      - node/install-packages
      - run:
          name: Lint
          command: npm run lint
      - run:
          name: Test
          command: npm run test

  build:
    docker:
      - image: cimg/base:current
    steps:
      - checkout
      - setup_remote_docker
      - run:
          name: Build Docker image
          command: docker build -t $CIRCLE_PROJECT_REPONAME:$CIRCLE_SHA1 .

workflows:
  main:
    jobs:
      - test
      - build:
          requires:
            - test
          filters:
            branches:
              only: {default_branch}
''',
    },
}


def _detect_project(repo_path: Path) -> dict:
    """Detect project type and configuration."""
    result = {
        "language": "unknown",
        "has_docker": (repo_path / "Dockerfile").exists(),
        "has_tests": False,
        "src_dirs": ".",
        "node_version": "20",
        "go_version": "1.22",
        "terraform_version": "1.7",
        "terraform_dir": ".",
        "default_branch": "main",
    }

    if (repo_path / "pyproject.toml").exists() or (repo_path / "requirements.txt").exists():
        result["language"] = "python"
        result["src_dirs"] = "app" if (repo_path / "app").is_dir() else "src" if (repo_path / "src").is_dir() else "."
        result["has_tests"] = (repo_path / "tests").is_dir() or (repo_path / "test").is_dir()

    elif (repo_path / "package.json").exists():
        result["language"] = "node"
        try:
            pkg = json.loads((repo_path / "package.json").read_text())
            result["has_tests"] = "test" in pkg.get("scripts", {})
            engines = pkg.get("engines", {})
            if "node" in engines:
                node_ver = engines["node"].lstrip(">=^~")
                if node_ver[0].isdigit():
                    result["node_version"] = node_ver.split(".")[0]
        except (json.JSONDecodeError, OSError):
            pass

    elif (repo_path / "go.mod").exists():
        result["language"] = "go"
        result["has_tests"] = any(repo_path.rglob("*_test.go"))

    elif (repo_path / "Cargo.toml").exists():
        result["language"] = "rust"
        result["has_tests"] = (repo_path / "tests").is_dir() or any(repo_path.rglob("**/tests/*.rs"))

    if any(repo_path.glob("*.tf")):
        result["has_terraform"] = True
        result["terraform_dir"] = "."
    elif (repo_path / "terraform").is_dir():
        result["has_terraform"] = True
        result["terraform_dir"] = "terraform"
    elif (repo_path / "infra").is_dir() and any((repo_path / "infra").glob("*.tf")):
        result["has_terraform"] = True
        result["terraform_dir"] = "infra"

    if (repo_path / "k8s").is_dir() or (repo_path / "kubernetes").is_dir():
        result["has_kubernetes"] = True

    return result


@mcp.tool()
def list_cicd_templates() -> dict:
    """List all available CI/CD templates organized by platform."""
    return {
        "github_actions": [
            {"id": k, "name": v["name"], "description": v["description"]}
            for k, v in GITHUB_ACTIONS_TEMPLATES.items()
        ],
        "gitlab_ci": [
            {"id": k, "name": v["name"], "description": v["description"]}
            for k, v in GITLAB_CI_TEMPLATES.items()
        ],
        "circleci": [
            {"id": k, "name": v["name"], "description": v["description"]}
            for k, v in CIRCLECI_TEMPLATES.items()
        ],
    }


@mcp.tool()
def analyze_for_cicd(path: str = ".") -> dict:
    """Analyze a codebase and suggest CI/CD configuration.

    Args:
        path: Path to analyze (relative to repo root)

    Returns:
        Analysis with detected language, framework, and recommended CI/CD setup
    """
    target = REPO_PATH / path
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    detection = _detect_project(target)

    suggestions = []
    recommended_templates = []

    if detection["language"] == "python":
        recommended_templates.append("python")
        if detection["has_docker"]:
            recommended_templates.append("python-docker")
        suggestions.append("Use uv for fast dependency installation")
        suggestions.append("Include ruff for linting and mypy for type checking")

    elif detection["language"] == "node":
        recommended_templates.append("node")
        if detection["has_docker"]:
            recommended_templates.append("node-docker")
        suggestions.append("Cache node_modules for faster builds")

    elif detection["language"] == "go":
        recommended_templates.append("go")
        suggestions.append("Use golangci-lint for comprehensive linting")

    elif detection["language"] == "rust":
        recommended_templates.append("rust")
        suggestions.append("Use Swatinem/rust-cache for faster builds")

    if detection.get("has_terraform"):
        recommended_templates.append("terraform")
        suggestions.append("Run terraform plan on PRs for review")

    if detection.get("has_kubernetes"):
        recommended_templates.append("deploy-k8s")
        suggestions.append("Consider using ArgoCD for GitOps deployments")

    return {
        "language": detection["language"],
        "has_docker": detection["has_docker"],
        "has_tests": detection["has_tests"],
        "has_terraform": detection.get("has_terraform", False),
        "has_kubernetes": detection.get("has_kubernetes", False),
        "recommended_templates": recommended_templates,
        "suggestions": suggestions,
        "config": {
            "src_dirs": detection["src_dirs"],
            "node_version": detection["node_version"],
            "go_version": detection["go_version"],
            "default_branch": detection["default_branch"],
        },
    }


@mcp.tool()
def generate_github_actions(
    template: str | None = None,
    default_branch: str = "main",
    src_dirs: str = "app",
    node_version: str = "20",
    go_version: str = "1.22",
    terraform_version: str = "1.7",
    terraform_dir: str = "terraform",
    deployment_name: str = "app",
    container_name: str = "app",
    namespace: str = "default",
    auto_detect: bool = True,
) -> dict:
    """Generate a GitHub Actions workflow configuration.

    Args:
        template: Template ID (python, node, go, rust, terraform, deploy-k8s).
                  If not specified and auto_detect=True, will analyze the repo.
        default_branch: Default branch name (main/master)
        src_dirs: Source directories for linting/testing
        node_version: Node.js version
        go_version: Go version
        terraform_version: Terraform version
        terraform_dir: Directory containing Terraform files
        deployment_name: Kubernetes deployment name (for deploy-k8s)
        container_name: Container name in deployment (for deploy-k8s)
        namespace: Kubernetes namespace (for deploy-k8s)
        auto_detect: Whether to auto-detect settings

    Returns:
        Generated workflow YAML and metadata
    """
    if template is None and auto_detect:
        analysis = analyze_for_cicd()
        if analysis.get("recommended_templates"):
            template = analysis["recommended_templates"][0]
            cfg = analysis.get("config", {})
            src_dirs = cfg.get("src_dirs", src_dirs)
            node_version = cfg.get("node_version", node_version)
            go_version = cfg.get("go_version", go_version)
            default_branch = cfg.get("default_branch", default_branch)

    if template is None:
        return {"error": "Could not determine template. Specify template parameter."}

    if template not in GITHUB_ACTIONS_TEMPLATES:
        return {
            "error": f"Unknown template '{template}'",
            "available": list(GITHUB_ACTIONS_TEMPLATES.keys()),
        }

    tpl = GITHUB_ACTIONS_TEMPLATES[template]
    workflow = tpl["template"].format(
        default_branch=default_branch,
        src_dirs=src_dirs,
        node_version=node_version,
        go_version=go_version,
        terraform_version=terraform_version,
        terraform_dir=terraform_dir,
        deployment_name=deployment_name,
        container_name=container_name,
        namespace=namespace,
    )

    return {
        "template": template,
        "file_path": ".github/workflows/ci.yml",
        "workflow": workflow,
        "notes": [
            "Save to .github/workflows/ci.yml",
            "Workflow triggers on push to main and pull requests",
            "Uses GitHub Container Registry (ghcr.io) for Docker images",
        ],
    }


@mcp.tool()
def generate_gitlab_ci(
    template: str | None = None,
    default_branch: str = "main",
    src_dirs: str = "app",
    node_version: str = "20",
    auto_detect: bool = True,
) -> dict:
    """Generate a GitLab CI/CD configuration.

    Args:
        template: Template ID (python, node). Auto-detects if not specified.
        default_branch: Default branch name
        src_dirs: Source directories for linting/testing
        node_version: Node.js version
        auto_detect: Whether to auto-detect settings

    Returns:
        Generated .gitlab-ci.yml content
    """
    if template is None and auto_detect:
        analysis = analyze_for_cicd()
        if analysis.get("recommended_templates"):
            for t in analysis["recommended_templates"]:
                if t in GITLAB_CI_TEMPLATES:
                    template = t
                    break
            cfg = analysis.get("config", {})
            src_dirs = cfg.get("src_dirs", src_dirs)
            node_version = cfg.get("node_version", node_version)
            default_branch = cfg.get("default_branch", default_branch)

    if template is None:
        return {"error": "Could not determine template. Specify template parameter."}

    if template not in GITLAB_CI_TEMPLATES:
        return {
            "error": f"Unknown template '{template}'",
            "available": list(GITLAB_CI_TEMPLATES.keys()),
        }

    tpl = GITLAB_CI_TEMPLATES[template]
    config = tpl["template"].format(
        default_branch=default_branch,
        src_dirs=src_dirs,
        node_version=node_version,
    )

    return {
        "template": template,
        "file_path": ".gitlab-ci.yml",
        "config": config,
        "notes": [
            "Save to .gitlab-ci.yml in repository root",
            "Uses GitLab Container Registry for Docker images",
            "Coverage reports are parsed from test output",
        ],
    }


@mcp.tool()
def generate_circleci(
    template: str | None = None,
    default_branch: str = "main",
    src_dirs: str = "app",
    node_version: str = "20",
    auto_detect: bool = True,
) -> dict:
    """Generate a CircleCI configuration.

    Args:
        template: Template ID (python, node). Auto-detects if not specified.
        default_branch: Default branch name
        src_dirs: Source directories
        node_version: Node.js version
        auto_detect: Whether to auto-detect settings

    Returns:
        Generated .circleci/config.yml content
    """
    if template is None and auto_detect:
        analysis = analyze_for_cicd()
        if analysis.get("recommended_templates"):
            for t in analysis["recommended_templates"]:
                if t in CIRCLECI_TEMPLATES:
                    template = t
                    break
            cfg = analysis.get("config", {})
            src_dirs = cfg.get("src_dirs", src_dirs)
            node_version = cfg.get("node_version", node_version)
            default_branch = cfg.get("default_branch", default_branch)

    if template is None:
        return {"error": "Could not determine template. Specify template parameter."}

    if template not in CIRCLECI_TEMPLATES:
        return {
            "error": f"Unknown template '{template}'",
            "available": list(CIRCLECI_TEMPLATES.keys()),
        }

    tpl = CIRCLECI_TEMPLATES[template]
    config = tpl["template"].format(
        default_branch=default_branch,
        src_dirs=src_dirs,
        node_version=node_version,
    )

    return {
        "template": template,
        "file_path": ".circleci/config.yml",
        "config": config,
        "notes": [
            "Save to .circleci/config.yml",
            "Uses CircleCI orbs for language-specific setup",
            "Workflows run test before build",
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
