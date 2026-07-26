"""Multi-Cloud Deployment MCP server — AWS, GCP, and Azure support.

Provides tools for deploying and managing resources across multiple cloud
providers. Supports unified resource management, cross-cloud comparison,
and multi-cloud deployment strategies.

Tools:
  - list_cloud_resources:     List resources across clouds
  - compare_cloud_services:   Compare equivalent services across clouds
  - generate_multicloud_iac:  Generate Terraform for multiple clouds
  - estimate_multicloud_cost: Estimate costs across cloud providers
  - get_cloud_regions:        List available regions per cloud

Run standalone:
    python -m app.mcp.servers.multicloud.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("multicloud")

AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "").strip()
AZURE_SUBSCRIPTION = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()

OFFLINE = not any([AWS_ACCESS_KEY, GCP_PROJECT, AZURE_SUBSCRIPTION])

SERVICE_MAPPING: dict[str, dict[str, str]] = {
    "compute": {
        "aws": "EC2",
        "gcp": "Compute Engine",
        "azure": "Virtual Machines",
    },
    "kubernetes": {
        "aws": "EKS",
        "gcp": "GKE",
        "azure": "AKS",
    },
    "serverless": {
        "aws": "Lambda",
        "gcp": "Cloud Functions",
        "azure": "Azure Functions",
    },
    "container_registry": {
        "aws": "ECR",
        "gcp": "Artifact Registry",
        "azure": "Container Registry",
    },
    "object_storage": {
        "aws": "S3",
        "gcp": "Cloud Storage",
        "azure": "Blob Storage",
    },
    "relational_db": {
        "aws": "RDS",
        "gcp": "Cloud SQL",
        "azure": "Azure SQL Database",
    },
    "nosql_db": {
        "aws": "DynamoDB",
        "gcp": "Firestore",
        "azure": "Cosmos DB",
    },
    "cache": {
        "aws": "ElastiCache",
        "gcp": "Memorystore",
        "azure": "Azure Cache for Redis",
    },
    "message_queue": {
        "aws": "SQS",
        "gcp": "Pub/Sub",
        "azure": "Service Bus",
    },
    "cdn": {
        "aws": "CloudFront",
        "gcp": "Cloud CDN",
        "azure": "Azure CDN",
    },
    "dns": {
        "aws": "Route 53",
        "gcp": "Cloud DNS",
        "azure": "Azure DNS",
    },
    "load_balancer": {
        "aws": "ALB/NLB",
        "gcp": "Cloud Load Balancing",
        "azure": "Azure Load Balancer",
    },
    "api_gateway": {
        "aws": "API Gateway",
        "gcp": "API Gateway / Apigee",
        "azure": "API Management",
    },
    "secrets": {
        "aws": "Secrets Manager",
        "gcp": "Secret Manager",
        "azure": "Key Vault",
    },
    "logging": {
        "aws": "CloudWatch Logs",
        "gcp": "Cloud Logging",
        "azure": "Azure Monitor Logs",
    },
    "monitoring": {
        "aws": "CloudWatch",
        "gcp": "Cloud Monitoring",
        "azure": "Azure Monitor",
    },
}

CLOUD_REGIONS: dict[str, list[dict]] = {
    "aws": [
        {"id": "us-east-1", "name": "N. Virginia", "location": "USA"},
        {"id": "us-east-2", "name": "Ohio", "location": "USA"},
        {"id": "us-west-1", "name": "N. California", "location": "USA"},
        {"id": "us-west-2", "name": "Oregon", "location": "USA"},
        {"id": "eu-west-1", "name": "Ireland", "location": "Europe"},
        {"id": "eu-west-2", "name": "London", "location": "Europe"},
        {"id": "eu-central-1", "name": "Frankfurt", "location": "Europe"},
        {"id": "ap-southeast-1", "name": "Singapore", "location": "Asia Pacific"},
        {"id": "ap-northeast-1", "name": "Tokyo", "location": "Asia Pacific"},
        {"id": "ap-south-1", "name": "Mumbai", "location": "Asia Pacific"},
    ],
    "gcp": [
        {"id": "us-central1", "name": "Iowa", "location": "USA"},
        {"id": "us-east1", "name": "South Carolina", "location": "USA"},
        {"id": "us-west1", "name": "Oregon", "location": "USA"},
        {"id": "europe-west1", "name": "Belgium", "location": "Europe"},
        {"id": "europe-west2", "name": "London", "location": "Europe"},
        {"id": "europe-west3", "name": "Frankfurt", "location": "Europe"},
        {"id": "asia-east1", "name": "Taiwan", "location": "Asia Pacific"},
        {"id": "asia-southeast1", "name": "Singapore", "location": "Asia Pacific"},
        {"id": "asia-northeast1", "name": "Tokyo", "location": "Asia Pacific"},
    ],
    "azure": [
        {"id": "eastus", "name": "East US", "location": "USA"},
        {"id": "eastus2", "name": "East US 2", "location": "USA"},
        {"id": "westus", "name": "West US", "location": "USA"},
        {"id": "westus2", "name": "West US 2", "location": "USA"},
        {"id": "westeurope", "name": "West Europe", "location": "Europe"},
        {"id": "northeurope", "name": "North Europe", "location": "Europe"},
        {"id": "uksouth", "name": "UK South", "location": "Europe"},
        {"id": "southeastasia", "name": "Southeast Asia", "location": "Asia Pacific"},
        {"id": "japaneast", "name": "Japan East", "location": "Asia Pacific"},
    ],
}

INSTANCE_COMPARISON: dict[str, dict[str, dict]] = {
    "small": {
        "aws": {"type": "t3.small", "vcpu": 2, "memory_gb": 2, "hourly": 0.0208},
        "gcp": {"type": "e2-small", "vcpu": 2, "memory_gb": 2, "hourly": 0.0168},
        "azure": {"type": "B2s", "vcpu": 2, "memory_gb": 4, "hourly": 0.0416},
    },
    "medium": {
        "aws": {"type": "t3.medium", "vcpu": 2, "memory_gb": 4, "hourly": 0.0416},
        "gcp": {"type": "e2-medium", "vcpu": 2, "memory_gb": 4, "hourly": 0.0335},
        "azure": {"type": "B2ms", "vcpu": 2, "memory_gb": 8, "hourly": 0.0832},
    },
    "large": {
        "aws": {"type": "m5.large", "vcpu": 2, "memory_gb": 8, "hourly": 0.096},
        "gcp": {"type": "e2-standard-2", "vcpu": 2, "memory_gb": 8, "hourly": 0.067},
        "azure": {"type": "D2s_v3", "vcpu": 2, "memory_gb": 8, "hourly": 0.096},
    },
    "xlarge": {
        "aws": {"type": "m5.xlarge", "vcpu": 4, "memory_gb": 16, "hourly": 0.192},
        "gcp": {"type": "e2-standard-4", "vcpu": 4, "memory_gb": 16, "hourly": 0.134},
        "azure": {"type": "D4s_v3", "vcpu": 4, "memory_gb": 16, "hourly": 0.192},
    },
}

DEMO_RESOURCES: dict[str, list[dict]] = {
    "aws": [
        {"id": "i-0abc123", "type": "EC2", "name": "web-server-1", "region": "us-east-1", "status": "running"},
        {"id": "i-0def456", "type": "EC2", "name": "api-server-1", "region": "us-east-1", "status": "running"},
        {"id": "eks-prod", "type": "EKS", "name": "prod-cluster", "region": "us-east-1", "status": "active"},
        {"id": "rds-prod", "type": "RDS", "name": "prod-db", "region": "us-east-1", "status": "available"},
    ],
    "gcp": [
        {"id": "gce-web-1", "type": "Compute Engine", "name": "web-server-1", "region": "us-central1", "status": "running"},
        {"id": "gke-prod", "type": "GKE", "name": "prod-cluster", "region": "us-central1", "status": "running"},
        {"id": "sql-prod", "type": "Cloud SQL", "name": "prod-db", "region": "us-central1", "status": "runnable"},
    ],
    "azure": [
        {"id": "vm-web-1", "type": "Virtual Machine", "name": "web-server-1", "region": "eastus", "status": "running"},
        {"id": "aks-prod", "type": "AKS", "name": "prod-cluster", "region": "eastus", "status": "running"},
        {"id": "sql-prod", "type": "Azure SQL", "name": "prod-db", "region": "eastus", "status": "online"},
    ],
}


TERRAFORM_MULTICLOUD: dict[str, dict[str, str]] = {
    "aws": {
        "provider": '''terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
''',
        "kubernetes": '''module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"

  cluster_name    = var.cluster_name
  cluster_version = var.kubernetes_version
  vpc_id          = aws_vpc.main.id
  subnet_ids      = aws_subnet.private[*].id

  eks_managed_node_groups = {
    default = {
      min_size       = var.min_nodes
      max_size       = var.max_nodes
      desired_size   = var.desired_nodes
      instance_types = var.instance_types
    }
  }
}
''',
        "database": '''resource "aws_db_instance" "main" {
  identifier           = var.db_name
  engine               = "postgres"
  engine_version       = var.postgres_version
  instance_class       = var.db_instance_class
  allocated_storage    = var.db_storage_gb
  storage_encrypted    = true
  db_name              = var.db_name
  username             = var.db_username
  password             = var.db_password
  skip_final_snapshot  = var.environment != "production"
}
''',
    },
    "gcp": {
        "provider": '''terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}
''',
        "kubernetes": '''resource "google_container_cluster" "primary" {
  name     = var.cluster_name
  location = var.gcp_region

  initial_node_count       = 1
  remove_default_node_pool = true

  network    = google_compute_network.vpc.name
  subnetwork = google_compute_subnetwork.private.name
}

resource "google_container_node_pool" "primary_nodes" {
  name       = "${var.cluster_name}-node-pool"
  location   = var.gcp_region
  cluster    = google_container_cluster.primary.name
  node_count = var.desired_nodes

  node_config {
    machine_type = var.machine_type
    disk_size_gb = 50

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }

  autoscaling {
    min_node_count = var.min_nodes
    max_node_count = var.max_nodes
  }
}
''',
        "database": '''resource "google_sql_database_instance" "main" {
  name             = var.db_name
  database_version = "POSTGRES_15"
  region           = var.gcp_region

  settings {
    tier = var.db_tier

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled = true
    }
  }

  deletion_protection = var.environment == "production"
}
''',
    },
    "azure": {
        "provider": '''terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.azure_subscription_id
}

resource "azurerm_resource_group" "main" {
  name     = "${var.project}-rg"
  location = var.azure_region
}
''',
        "kubernetes": '''resource "azurerm_kubernetes_cluster" "main" {
  name                = var.cluster_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.cluster_name

  default_node_pool {
    name       = "default"
    node_count = var.desired_nodes
    vm_size    = var.vm_size

    enable_auto_scaling = true
    min_count           = var.min_nodes
    max_count           = var.max_nodes
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin = "azure"
    network_policy = "calico"
  }
}
''',
        "database": '''resource "azurerm_postgresql_flexible_server" "main" {
  name                   = var.db_name
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  version                = "15"
  administrator_login    = var.db_username
  administrator_password = var.db_password
  storage_mb             = var.db_storage_mb
  sku_name               = var.db_sku

  backup_retention_days = 7
  geo_redundant_backup_enabled = var.environment == "production"
}
''',
    },
}


@mcp.tool()
def get_cloud_regions(cloud: str | None = None) -> dict:
    """Get available regions for cloud providers.

    Args:
        cloud: Specific cloud provider (aws, gcp, azure) or None for all

    Returns:
        Available regions organized by cloud provider
    """
    if cloud:
        cloud = cloud.lower()
        if cloud not in CLOUD_REGIONS:
            return {
                "error": f"Unknown cloud provider: {cloud}",
                "available": list(CLOUD_REGIONS.keys()),
            }
        return {cloud: CLOUD_REGIONS[cloud]}

    return CLOUD_REGIONS


@mcp.tool()
def compare_cloud_services(service_type: str) -> dict:
    """Compare equivalent services across cloud providers.

    Args:
        service_type: Type of service to compare (e.g., compute, kubernetes,
                      serverless, object_storage, relational_db)

    Returns:
        Comparison of service offerings across AWS, GCP, and Azure
    """
    service_type = service_type.lower().replace(" ", "_").replace("-", "_")

    if service_type not in SERVICE_MAPPING:
        return {
            "error": f"Unknown service type: {service_type}",
            "available": list(SERVICE_MAPPING.keys()),
        }

    mapping = SERVICE_MAPPING[service_type]

    comparison = {
        "service_type": service_type,
        "services": {
            "aws": {
                "name": mapping["aws"],
                "docs": f"https://aws.amazon.com/{mapping['aws'].lower().replace(' ', '-')}/",
            },
            "gcp": {
                "name": mapping["gcp"],
                "docs": f"https://cloud.google.com/{mapping['gcp'].lower().replace(' ', '-')}/",
            },
            "azure": {
                "name": mapping["azure"],
                "docs": f"https://azure.microsoft.com/services/{mapping['azure'].lower().replace(' ', '-')}/",
            },
        },
    }

    if service_type == "compute":
        comparison["instance_comparison"] = INSTANCE_COMPARISON

    if service_type == "kubernetes":
        comparison["notes"] = [
            "EKS: Pay for control plane ($0.10/hr) + nodes",
            "GKE: Free control plane for zonal, $0.10/hr for regional",
            "AKS: Free control plane, pay only for nodes",
        ]

    return comparison


@mcp.tool()
def list_cloud_resources(clouds: list[str] | None = None) -> dict:
    """List resources across cloud providers.

    Args:
        clouds: List of clouds to query (aws, gcp, azure). None = all.

    Returns:
        Resources organized by cloud provider
    """
    if OFFLINE:
        clouds_to_query = clouds or ["aws", "gcp", "azure"]
        clouds_to_query = [c.lower() for c in clouds_to_query]

        results = {}
        for cloud in clouds_to_query:
            if cloud in DEMO_RESOURCES:
                results[cloud] = {
                    "resources": DEMO_RESOURCES[cloud],
                    "count": len(DEMO_RESOURCES[cloud]),
                }

        return {
            "mode": "offline_demo",
            "clouds": results,
            "total_resources": sum(r["count"] for r in results.values()),
        }

    return {
        "error": "Live mode requires cloud credentials",
        "required": {
            "aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            "gcp": ["GCP_PROJECT_ID", "GOOGLE_APPLICATION_CREDENTIALS"],
            "azure": ["AZURE_SUBSCRIPTION_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"],
        },
    }


@mcp.tool()
def estimate_multicloud_cost(
    instance_size: str = "medium",
    count: int | str = 1,
    hours_per_month: int | str = 730,
) -> dict:
    """Estimate and compare costs across cloud providers.

    Args:
        instance_size: Instance size (small, medium, large, xlarge)
        count: Number of instances
        hours_per_month: Hours running per month (730 = 24/7)

    Returns:
        Cost comparison across AWS, GCP, and Azure
    """
    try:
        count = int(count)
        hours_per_month = min(730, max(1, int(hours_per_month)))
    except (TypeError, ValueError):
        count = 1
        hours_per_month = 730

    if instance_size not in INSTANCE_COMPARISON:
        return {
            "error": f"Unknown instance size: {instance_size}",
            "available": list(INSTANCE_COMPARISON.keys()),
        }

    comparison = INSTANCE_COMPARISON[instance_size]
    results = {}

    for cloud, specs in comparison.items():
        monthly = specs["hourly"] * hours_per_month * count
        annual = monthly * 12

        results[cloud] = {
            "instance_type": specs["type"],
            "vcpu": specs["vcpu"],
            "memory_gb": specs["memory_gb"],
            "hourly_rate": specs["hourly"],
            "monthly_cost": round(monthly, 2),
            "annual_cost": round(annual, 2),
        }

    costs = [(cloud, data["monthly_cost"]) for cloud, data in results.items()]
    costs.sort(key=lambda x: x[1])
    cheapest = costs[0][0]
    most_expensive = costs[-1][0]

    savings = results[most_expensive]["monthly_cost"] - results[cheapest]["monthly_cost"]
    savings_pct = (savings / results[most_expensive]["monthly_cost"] * 100
                   if results[most_expensive]["monthly_cost"] > 0 else 0)

    return {
        "instance_size": instance_size,
        "count": count,
        "hours_per_month": hours_per_month,
        "comparison": results,
        "recommendation": {
            "cheapest": cheapest,
            "monthly_savings_vs_most_expensive": round(savings, 2),
            "savings_percentage": round(savings_pct, 1),
        },
        "notes": [
            "Prices are on-demand rates; reserved instances can reduce costs 30-60%",
            "GCP offers sustained use discounts automatically",
            "Azure offers hybrid benefit for Windows/SQL Server licenses",
        ],
    }


@mcp.tool()
def generate_multicloud_iac(
    clouds: list[str],
    components: list[str],
    environment: str = "production",
) -> dict:
    """Generate Terraform code for multiple cloud providers.

    Args:
        clouds: Target clouds (aws, gcp, azure)
        components: Components to generate (kubernetes, database, vpc/network)
        environment: Target environment

    Returns:
        Terraform configurations for each cloud provider
    """
    clouds = [c.lower() for c in clouds]
    invalid_clouds = [c for c in clouds if c not in TERRAFORM_MULTICLOUD]
    if invalid_clouds:
        return {
            "error": f"Unknown cloud providers: {invalid_clouds}",
            "available": list(TERRAFORM_MULTICLOUD.keys()),
        }

    results = {}

    for cloud in clouds:
        cloud_tf = TERRAFORM_MULTICLOUD[cloud]
        terraform = f"# {cloud.upper()} Infrastructure\n"
        terraform += f"# Environment: {environment}\n\n"
        terraform += cloud_tf["provider"]

        for component in components:
            component = component.lower().replace("-", "_").replace(" ", "_")
            if component in ("vpc", "network"):
                component = "network"
            if component in cloud_tf:
                terraform += f"\n# {component.title()}\n"
                terraform += cloud_tf[component]

        variables = f'''
variable "environment" {{
  default = "{environment}"
}}

variable "cluster_name" {{
  default = "app-cluster"
}}

variable "min_nodes" {{
  default = 1
}}

variable "max_nodes" {{
  default = 5
}}

variable "desired_nodes" {{
  default = 2
}}
'''
        terraform += variables

        results[cloud] = {
            "terraform": terraform,
            "file_name": f"{cloud}-main.tf",
        }

    return {
        "clouds": clouds,
        "components": components,
        "environment": environment,
        "configurations": results,
        "structure": {
            "recommended_layout": [
                "infrastructure/",
                "  aws/",
                "    main.tf",
                "    variables.tf",
                "  gcp/",
                "    main.tf",
                "    variables.tf",
                "  azure/",
                "    main.tf",
                "    variables.tf",
                "  modules/",
                "    shared-configs/",
            ],
        },
        "notes": [
            "Use Terraform workspaces for environment separation",
            "Consider Terragrunt for DRY multi-cloud configs",
            "Use remote state backend for team collaboration",
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
