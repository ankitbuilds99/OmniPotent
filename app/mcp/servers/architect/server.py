"""Architecture Generation MCP server — generate architecture diagrams and IaC.

Provides tools to generate architecture diagrams (Mermaid, PlantUML), analyze
existing codebases to extract architecture, and generate Infrastructure as Code
blueprints from natural language descriptions.

Tools:
  - generate_architecture_diagram:  Generate Mermaid/PlantUML diagram
  - analyze_architecture:           Analyze codebase and extract architecture
  - generate_iac_blueprint:         Generate IaC from architecture description
  - list_architecture_patterns:     List common architecture patterns

Run standalone:
    python -m app.mcp.servers.architect.server
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("architect")

REPO_PATH = Path(os.environ.get("TARGET_REPO_PATH", "./sample_repo")).resolve()

ARCHITECTURE_PATTERNS: dict[str, dict] = {
    "microservices": {
        "name": "Microservices Architecture",
        "description": "Loosely coupled services communicating via APIs",
        "components": ["API Gateway", "Service Discovery", "Load Balancer", "Services", "Message Queue", "Database per Service"],
        "pros": ["Independent deployment", "Technology diversity", "Scalability"],
        "cons": ["Complexity", "Network latency", "Data consistency challenges"],
        "use_cases": ["Large teams", "Complex domains", "High scalability needs"],
    },
    "monolith": {
        "name": "Monolithic Architecture",
        "description": "Single deployable unit containing all functionality",
        "components": ["Web Layer", "Business Logic", "Data Access", "Database"],
        "pros": ["Simple deployment", "Easy debugging", "Lower latency"],
        "cons": ["Scaling challenges", "Technology lock-in", "Large codebase"],
        "use_cases": ["Small teams", "MVPs", "Simple domains"],
    },
    "serverless": {
        "name": "Serverless Architecture",
        "description": "Event-driven functions with managed infrastructure",
        "components": ["API Gateway", "Functions", "Event Bus", "Managed Services", "Object Storage"],
        "pros": ["No server management", "Pay per use", "Auto-scaling"],
        "cons": ["Cold starts", "Vendor lock-in", "Debugging complexity"],
        "use_cases": ["Event-driven workloads", "Variable traffic", "Quick iterations"],
    },
    "event-driven": {
        "name": "Event-Driven Architecture",
        "description": "Components communicate through events/messages",
        "components": ["Event Producers", "Event Bus/Broker", "Event Consumers", "Event Store"],
        "pros": ["Loose coupling", "Scalability", "Real-time processing"],
        "cons": ["Event ordering", "Eventual consistency", "Debugging"],
        "use_cases": ["Real-time systems", "IoT", "Async workflows"],
    },
    "three-tier": {
        "name": "Three-Tier Architecture",
        "description": "Presentation, Business Logic, and Data tiers",
        "components": ["Presentation Tier", "Application Tier", "Data Tier"],
        "pros": ["Separation of concerns", "Maintainability", "Reusability"],
        "cons": ["Performance overhead", "Tight coupling between tiers"],
        "use_cases": ["Web applications", "Enterprise apps", "CRUD applications"],
    },
    "cqrs": {
        "name": "CQRS (Command Query Responsibility Segregation)",
        "description": "Separate models for reading and writing data",
        "components": ["Command Model", "Query Model", "Event Store", "Projections"],
        "pros": ["Optimized reads/writes", "Scalability", "Complex domain support"],
        "cons": ["Complexity", "Eventual consistency", "Learning curve"],
        "use_cases": ["Complex domains", "High-read/low-write ratios", "Event sourcing"],
    },
}


MERMAID_TEMPLATES: dict[str, str] = {
    "microservices": '''flowchart TB
    subgraph Internet
        Client[Client]
    end

    subgraph Cloud["Cloud Infrastructure"]
        LB[Load Balancer]

        subgraph Gateway["API Gateway"]
            AG[API Gateway]
            Auth[Auth Service]
        end

        subgraph Services["Microservices"]
            {services_block}
        end

        subgraph Data["Data Layer"]
            MQ[Message Queue]
            Cache[Redis Cache]
            {databases_block}
        end
    end

    Client --> LB
    LB --> AG
    AG --> Auth
    {service_connections}
    {data_connections}
''',
    "serverless": '''flowchart TB
    subgraph Client
        Web[Web App]
        Mobile[Mobile App]
    end

    subgraph AWS["AWS Cloud"]
        APIGW[API Gateway]

        subgraph Lambda["Lambda Functions"]
            {functions_block}
        end

        subgraph Storage["Storage"]
            S3[S3 Bucket]
            DDB[DynamoDB]
        end

        subgraph Events["Events"]
            SNS[SNS Topics]
            SQS[SQS Queues]
        end
    end

    Web --> APIGW
    Mobile --> APIGW
    APIGW --> Lambda
    {lambda_connections}
''',
    "three-tier": '''flowchart TB
    subgraph Presentation["Presentation Tier"]
        Web[Web Server]
        CDN[CDN]
    end

    subgraph Application["Application Tier"]
        LB[Load Balancer]
        App1[App Server 1]
        App2[App Server 2]
    end

    subgraph Data["Data Tier"]
        Primary[(Primary DB)]
        Replica[(Read Replica)]
        Cache[Redis Cache]
    end

    CDN --> Web
    Web --> LB
    LB --> App1
    LB --> App2
    App1 --> Primary
    App2 --> Primary
    Primary --> Replica
    App1 --> Cache
    App2 --> Cache
''',
}


TERRAFORM_TEMPLATES: dict[str, str] = {
    "vpc": '''# VPC Configuration
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.project}-vpc"
    Environment = var.environment
  }
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project}-public-${count.index + 1}"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project}-private-${count.index + 1}"
  }
}
''',
    "eks": '''# EKS Cluster
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"

  cluster_name    = "${var.project}-cluster"
  cluster_version = var.kubernetes_version

  vpc_id     = aws_vpc.main.id
  subnet_ids = aws_subnet.private[*].id

  eks_managed_node_groups = {
    default = {
      min_size     = var.min_nodes
      max_size     = var.max_nodes
      desired_size = var.desired_nodes

      instance_types = var.instance_types
      capacity_type  = "ON_DEMAND"
    }
  }

  tags = {
    Environment = var.environment
  }
}
''',
    "rds": '''# RDS PostgreSQL
resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db-subnet"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier             = "${var.project}-db"
  engine                 = "postgres"
  engine_version         = var.postgres_version
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_storage_gb
  storage_encrypted      = true

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  backup_retention_period = 7
  multi_az               = var.environment == "production"
  skip_final_snapshot    = var.environment != "production"

  tags = {
    Environment = var.environment
  }
}
''',
    "lambda": '''# Lambda Function
resource "aws_lambda_function" "main" {
  function_name = "${var.project}-${var.function_name}"
  role          = aws_iam_role.lambda.arn
  handler       = var.handler
  runtime       = var.runtime
  timeout       = var.timeout
  memory_size   = var.memory_size

  filename         = var.deployment_package
  source_code_hash = filebase64sha256(var.deployment_package)

  environment {
    variables = var.environment_variables
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_iam_role" "lambda" {
  name = "${var.project}-${var.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}
''',
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


def _detect_services(repo_path: Path) -> list[dict]:
    """Detect services in a repository."""
    services = []

    for docker_compose in repo_path.rglob("docker-compose*.y*ml"):
        if "node_modules" in str(docker_compose):
            continue
        try:
            content = docker_compose.read_text()
            service_matches = re.findall(r"^\s{2}([a-zA-Z0-9_-]+):\s*$", content, re.MULTILINE)
            for svc in service_matches:
                if svc not in ("version", "services", "networks", "volumes"):
                    services.append({
                        "name": svc,
                        "source": str(docker_compose.relative_to(repo_path)),
                        "type": "container",
                    })
        except OSError:
            pass

    for k8s_dir in ["k8s", "kubernetes", "manifests", "deploy"]:
        k8s_path = repo_path / k8s_dir
        if k8s_path.exists():
            for manifest in k8s_path.rglob("*.y*ml"):
                try:
                    content = manifest.read_text()
                    if "kind: Deployment" in content or "kind: Service" in content:
                        name_match = re.search(r"name:\s*([a-zA-Z0-9_-]+)", content)
                        if name_match:
                            svc_name = name_match.group(1)
                            if not any(s["name"] == svc_name for s in services):
                                services.append({
                                    "name": svc_name,
                                    "source": str(manifest.relative_to(repo_path)),
                                    "type": "kubernetes",
                                })
                except OSError:
                    pass

    service_dirs = ["services", "apps", "packages", "modules"]
    for svc_dir in service_dirs:
        svc_path = repo_path / svc_dir
        if svc_path.exists() and svc_path.is_dir():
            for child in svc_path.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    if any((child / f).exists() for f in ["package.json", "pyproject.toml", "go.mod", "Cargo.toml"]):
                        if not any(s["name"] == child.name for s in services):
                            services.append({
                                "name": child.name,
                                "source": str(child.relative_to(repo_path)),
                                "type": "code",
                            })

    return services


def _detect_databases(repo_path: Path) -> list[dict]:
    """Detect database usage in a repository."""
    databases = []

    db_indicators = {
        "postgresql": ["psycopg2", "asyncpg", "pg", "postgres"],
        "mysql": ["mysql", "mysqlclient", "pymysql"],
        "mongodb": ["pymongo", "mongoose", "mongodb"],
        "redis": ["redis", "ioredis"],
        "elasticsearch": ["elasticsearch"],
        "dynamodb": ["boto3", "aws-sdk"],
    }

    files_to_check = ["requirements.txt", "package.json", "go.mod", "Cargo.toml"]

    for fname in files_to_check:
        for fpath in repo_path.rglob(fname):
            if "node_modules" in str(fpath) or ".venv" in str(fpath):
                continue
            try:
                content = fpath.read_text().lower()
                for db_type, indicators in db_indicators.items():
                    if any(ind in content for ind in indicators):
                        if not any(d["type"] == db_type for d in databases):
                            databases.append({"type": db_type, "source": str(fpath.relative_to(repo_path))})
            except OSError:
                pass

    return databases


@mcp.tool()
def list_architecture_patterns() -> list[dict]:
    """List common architecture patterns with descriptions and use cases."""
    return [
        {
            "id": key,
            "name": pattern["name"],
            "description": pattern["description"],
            "components": pattern["components"],
            "pros": pattern["pros"],
            "cons": pattern["cons"],
            "use_cases": pattern["use_cases"],
        }
        for key, pattern in ARCHITECTURE_PATTERNS.items()
    ]


@mcp.tool()
def analyze_architecture(path: str = ".") -> dict:
    """Analyze a codebase and extract its architecture.

    Detects services, databases, infrastructure patterns, and
    generates a summary of the system architecture.

    Args:
        path: Path to analyze (relative to repo root)

    Returns:
        Architecture analysis with detected components and patterns
    """
    target = _safe_path(path)
    if not target.exists():
        return {"error": f"path '{path}' not found"}

    services = _detect_services(target)
    databases = _detect_databases(target)

    has_docker = (target / "Dockerfile").exists() or any(target.rglob("Dockerfile*"))
    has_k8s = any((target / d).exists() for d in ["k8s", "kubernetes", "manifests"])
    has_terraform = any(target.rglob("*.tf"))
    has_compose = any(target.rglob("docker-compose*.y*ml"))
    has_serverless = (target / "serverless.yml").exists() or (target / "serverless.yaml").exists()

    if len(services) > 3:
        detected_pattern = "microservices"
    elif has_serverless:
        detected_pattern = "serverless"
    elif len(services) <= 1:
        detected_pattern = "monolith"
    else:
        detected_pattern = "three-tier"

    infra_components = []
    if has_docker:
        infra_components.append("Docker")
    if has_k8s:
        infra_components.append("Kubernetes")
    if has_terraform:
        infra_components.append("Terraform")
    if has_compose:
        infra_components.append("Docker Compose")
    if has_serverless:
        infra_components.append("Serverless Framework")

    return {
        "detected_pattern": detected_pattern,
        "pattern_info": ARCHITECTURE_PATTERNS.get(detected_pattern, {}),
        "services": services,
        "service_count": len(services),
        "databases": databases,
        "infrastructure": {
            "containerized": has_docker,
            "orchestrated": has_k8s,
            "iac_managed": has_terraform,
            "components": infra_components,
        },
        "recommendations": _get_architecture_recommendations(
            detected_pattern, len(services), has_k8s, has_terraform
        ),
    }


def _get_architecture_recommendations(pattern: str, service_count: int,
                                       has_k8s: bool, has_terraform: bool) -> list[str]:
    """Generate architecture recommendations."""
    recs = []

    if pattern == "microservices":
        if not has_k8s:
            recs.append("Consider Kubernetes for orchestrating microservices")
        recs.append("Implement service mesh (Istio/Linkerd) for observability")
        recs.append("Use distributed tracing (Jaeger/Zipkin) for debugging")

    if service_count > 1 and not has_terraform:
        recs.append("Adopt Infrastructure as Code (Terraform) for reproducibility")

    if pattern == "monolith" and service_count == 0:
        recs.append("Consider modular architecture for better maintainability")

    return recs


@mcp.tool()
def generate_architecture_diagram(
    pattern: str | None = None,
    services: list[str] | None = None,
    databases: list[str] | None = None,
    format: str = "mermaid",
    auto_detect: bool = True,
) -> dict:
    """Generate an architecture diagram in Mermaid or PlantUML format.

    Args:
        pattern: Architecture pattern (microservices, serverless, three-tier).
                 Auto-detected if not specified.
        services: List of service names. Auto-detected if not specified.
        databases: List of database names. Auto-detected if not specified.
        format: Output format ("mermaid" or "plantuml")
        auto_detect: Whether to auto-detect from repository

    Returns:
        Architecture diagram code that can be rendered
    """
    if auto_detect and (pattern is None or services is None):
        analysis = analyze_architecture()
        if pattern is None:
            pattern = analysis.get("detected_pattern", "three-tier")
        if services is None:
            services = [s["name"] for s in analysis.get("services", [])]
        if databases is None:
            databases = [d["type"] for d in analysis.get("databases", [])]

    if not services:
        services = ["api", "web", "worker"]
    if not databases:
        databases = ["postgresql"]

    pattern = pattern or "three-tier"

    if format == "mermaid":
        if pattern == "microservices":
            services_block = "\n            ".join(
                f'{svc.replace("-", "_")}[{svc}]' for svc in services
            )
            databases_block = "\n            ".join(
                f'{db}[({db})]' for db in databases
            )
            service_connections = "\n    ".join(
                f"AG --> {svc.replace('-', '_')}" for svc in services
            )
            data_connections = "\n    ".join(
                f"{services[0].replace('-', '_')} --> {db}" for db in databases
            )

            diagram = MERMAID_TEMPLATES["microservices"].format(
                services_block=services_block,
                databases_block=databases_block,
                service_connections=service_connections,
                data_connections=data_connections,
            )
        elif pattern == "serverless":
            functions_block = "\n            ".join(
                f'{svc.replace("-", "_")}[{svc} Function]' for svc in services
            )
            lambda_connections = "\n    ".join(
                f"{svc.replace('-', '_')} --> DDB" for svc in services[:2]
            )

            diagram = MERMAID_TEMPLATES["serverless"].format(
                functions_block=functions_block,
                lambda_connections=lambda_connections,
            )
        else:
            diagram = MERMAID_TEMPLATES["three-tier"]

    else:
        diagram = f'''@startuml
title {pattern.title()} Architecture

package "Services" {{
{chr(10).join(f"  [{svc}]" for svc in services)}
}}

database "Databases" {{
{chr(10).join(f"  [{db}]" for db in databases)}
}}

{chr(10).join(f"[{services[0]}] --> [{db}]" for db in databases)}
@enduml
'''

    return {
        "pattern": pattern,
        "format": format,
        "diagram": diagram,
        "services": services,
        "databases": databases,
        "render_url": (
            f"https://mermaid.live/edit#pako:{diagram[:100]}..."
            if format == "mermaid"
            else "https://www.plantuml.com/plantuml/uml/"
        ),
        "notes": [
            f"Diagram generated in {format.upper()} format",
            "Copy to a Markdown file or use online renderer",
            "Customize service names and connections as needed",
        ],
    }


@mcp.tool()
def generate_iac_blueprint(
    components: list[str],
    cloud: str = "aws",
    environment: str = "production",
) -> dict:
    """Generate Infrastructure as Code blueprint from component list.

    Args:
        components: List of infrastructure components needed
                    (e.g., ["vpc", "eks", "rds", "lambda"])
        cloud: Cloud provider ("aws", "gcp", "azure")
        environment: Target environment (production, staging, development)

    Returns:
        Terraform code for the requested infrastructure
    """
    if cloud != "aws":
        return {
            "error": f"Cloud provider '{cloud}' not yet supported",
            "supported": ["aws"],
        }

    available = list(TERRAFORM_TEMPLATES.keys())
    invalid = [c for c in components if c not in available]
    if invalid:
        return {
            "error": f"Unknown components: {invalid}",
            "available": available,
        }

    terraform_code = f'''# Generated Infrastructure as Code
# Environment: {environment}
# Components: {", ".join(components)}

terraform {{
  required_version = ">= 1.0"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{
  region = var.aws_region
}}

# Variables
variable "project" {{
  description = "Project name"
  type        = string
}}

variable "environment" {{
  description = "Environment (production, staging, development)"
  type        = string
  default     = "{environment}"
}}

variable "aws_region" {{
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}}

variable "vpc_cidr" {{
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}}

variable "availability_zones" {{
  description = "Availability zones"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}}

'''

    for component in components:
        terraform_code += f"\n# {component.upper()} Resources\n"
        terraform_code += TERRAFORM_TEMPLATES.get(component, f"# Template for {component} not found\n")
        terraform_code += "\n"

    terraform_code += '''
# Outputs
output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}
'''

    return {
        "cloud": cloud,
        "environment": environment,
        "components": components,
        "terraform": terraform_code,
        "files": {
            "main.tf": terraform_code,
            "terraform.tfvars": f'''project     = "my-project"
environment = "{environment}"
aws_region  = "us-east-1"
''',
        },
        "notes": [
            "Review and customize variable values before applying",
            "Run 'terraform init' then 'terraform plan' to preview changes",
            "Consider using remote state backend for team collaboration",
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
