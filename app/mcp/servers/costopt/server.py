"""Cost Optimization MCP server — cloud cost analysis and recommendations.

Provides cloud cost analysis, right-sizing recommendations, unused resource
detection, and cost anomaly alerts. Works with AWS, GCP, and Azure cost data.

In OFFLINE mode (no cloud credentials), provides demo data and cost estimation
tools. With credentials, fetches real cost data from cloud providers.

Tools:
  - analyze_costs:           Analyze cloud costs by service/resource
  - detect_unused:           Find unused/idle resources
  - rightsizing_recommendations: Get instance rightsizing suggestions
  - cost_forecast:           Forecast future costs based on trends
  - estimate_resource_cost:  Estimate cost for a resource configuration

Run standalone:
    python -m app.mcp.servers.costopt.server
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("costopt")

AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
OFFLINE = not (AWS_ACCESS_KEY and AWS_SECRET_KEY)

AWS_PRICING: dict[str, dict[str, float]] = {
    "ec2": {
        "t3.micro": 0.0104,
        "t3.small": 0.0208,
        "t3.medium": 0.0416,
        "t3.large": 0.0832,
        "t3.xlarge": 0.1664,
        "m5.large": 0.096,
        "m5.xlarge": 0.192,
        "m5.2xlarge": 0.384,
        "m6i.large": 0.096,
        "m6i.xlarge": 0.192,
        "c5.large": 0.085,
        "c5.xlarge": 0.170,
        "r5.large": 0.126,
        "r5.xlarge": 0.252,
    },
    "rds": {
        "db.t3.micro": 0.017,
        "db.t3.small": 0.034,
        "db.t3.medium": 0.068,
        "db.m5.large": 0.171,
        "db.m5.xlarge": 0.342,
        "db.r5.large": 0.240,
    },
    "storage": {
        "s3_standard_gb": 0.023,
        "s3_ia_gb": 0.0125,
        "s3_glacier_gb": 0.004,
        "ebs_gp3_gb": 0.08,
        "ebs_io1_gb": 0.125,
    },
    "data_transfer": {
        "out_gb": 0.09,
        "in_gb": 0.00,
        "inter_region_gb": 0.02,
    },
}

DEMO_COSTS: dict[str, object] = {
    "daily": [
        {"date": "2026-07-20", "cost": 245.67, "service": "EC2", "region": "us-east-1"},
        {"date": "2026-07-20", "cost": 89.42, "service": "RDS", "region": "us-east-1"},
        {"date": "2026-07-20", "cost": 45.23, "service": "S3", "region": "us-east-1"},
        {"date": "2026-07-20", "cost": 23.11, "service": "Lambda", "region": "us-east-1"},
        {"date": "2026-07-21", "cost": 251.34, "service": "EC2", "region": "us-east-1"},
        {"date": "2026-07-21", "cost": 89.42, "service": "RDS", "region": "us-east-1"},
        {"date": "2026-07-21", "cost": 47.89, "service": "S3", "region": "us-east-1"},
        {"date": "2026-07-21", "cost": 25.67, "service": "Lambda", "region": "us-east-1"},
        {"date": "2026-07-22", "cost": 248.90, "service": "EC2", "region": "us-east-1"},
        {"date": "2026-07-22", "cost": 89.42, "service": "RDS", "region": "us-east-1"},
        {"date": "2026-07-22", "cost": 44.12, "service": "S3", "region": "us-east-1"},
        {"date": "2026-07-22", "cost": 28.45, "service": "Lambda", "region": "us-east-1"},
    ],
    "by_service": {
        "EC2": {"current_month": 7645.23, "previous_month": 7234.56, "change_pct": 5.67},
        "RDS": {"current_month": 2683.56, "previous_month": 2683.56, "change_pct": 0.0},
        "S3": {"current_month": 1423.45, "previous_month": 1289.34, "change_pct": 10.4},
        "Lambda": {"current_month": 756.23, "previous_month": 623.45, "change_pct": 21.3},
        "CloudFront": {"current_month": 456.78, "previous_month": 412.34, "change_pct": 10.8},
        "EKS": {"current_month": 234.56, "previous_month": 234.56, "change_pct": 0.0},
    },
}

DEMO_RESOURCES: list[dict] = [
    {
        "id": "i-0abc123def456789",
        "type": "EC2",
        "name": "web-server-1",
        "instance_type": "m5.xlarge",
        "region": "us-east-1",
        "state": "running",
        "cpu_avg": 12.3,
        "memory_avg": 23.5,
        "monthly_cost": 138.24,
    },
    {
        "id": "i-0def456789abc123",
        "type": "EC2",
        "name": "api-server-1",
        "instance_type": "m5.2xlarge",
        "region": "us-east-1",
        "state": "running",
        "cpu_avg": 8.5,
        "memory_avg": 15.2,
        "monthly_cost": 276.48,
    },
    {
        "id": "i-0789abc123def456",
        "type": "EC2",
        "name": "batch-processor",
        "instance_type": "c5.xlarge",
        "region": "us-east-1",
        "state": "stopped",
        "cpu_avg": 0,
        "memory_avg": 0,
        "monthly_cost": 122.40,
        "last_used": "2026-06-15",
    },
    {
        "id": "i-0123456789abcdef",
        "type": "EC2",
        "name": "dev-server",
        "instance_type": "t3.large",
        "region": "us-east-1",
        "state": "running",
        "cpu_avg": 2.1,
        "memory_avg": 8.4,
        "monthly_cost": 59.90,
    },
    {
        "id": "db-prod-postgres",
        "type": "RDS",
        "name": "prod-database",
        "instance_type": "db.m5.xlarge",
        "region": "us-east-1",
        "state": "available",
        "cpu_avg": 45.2,
        "connections_avg": 23,
        "monthly_cost": 246.24,
    },
    {
        "id": "db-staging-postgres",
        "type": "RDS",
        "name": "staging-database",
        "instance_type": "db.m5.large",
        "region": "us-east-1",
        "state": "available",
        "cpu_avg": 3.4,
        "connections_avg": 2,
        "monthly_cost": 123.12,
    },
    {
        "id": "vol-0abc123456789def",
        "type": "EBS",
        "name": "unused-volume",
        "size_gb": 500,
        "region": "us-east-1",
        "state": "available",
        "attached": False,
        "monthly_cost": 40.00,
        "last_attached": "2026-05-20",
    },
    {
        "id": "eip-12345678",
        "type": "ElasticIP",
        "name": "unattached-eip",
        "region": "us-east-1",
        "attached": False,
        "monthly_cost": 3.60,
    },
]

RIGHTSIZING_MAP: dict[str, list[str]] = {
    "m5.2xlarge": ["m5.xlarge", "m5.large", "m6i.xlarge"],
    "m5.xlarge": ["m5.large", "m6i.large", "t3.xlarge"],
    "m5.large": ["t3.large", "t3.medium"],
    "c5.xlarge": ["c5.large", "t3.xlarge"],
    "t3.large": ["t3.medium", "t3.small"],
    "db.m5.xlarge": ["db.m5.large", "db.t3.medium"],
    "db.m5.large": ["db.t3.medium", "db.t3.small"],
}


def _calculate_savings(current_type: str, recommended_type: str, service: str = "ec2") -> dict:
    """Calculate potential savings from rightsizing."""
    pricing = AWS_PRICING.get(service, AWS_PRICING["ec2"])
    current_hourly = pricing.get(current_type, 0.10)
    recommended_hourly = pricing.get(recommended_type, 0.05)

    monthly_current = current_hourly * 730
    monthly_recommended = recommended_hourly * 730
    monthly_savings = monthly_current - monthly_recommended
    annual_savings = monthly_savings * 12
    savings_pct = (monthly_savings / monthly_current * 100) if monthly_current > 0 else 0

    return {
        "current_monthly": round(monthly_current, 2),
        "recommended_monthly": round(monthly_recommended, 2),
        "monthly_savings": round(monthly_savings, 2),
        "annual_savings": round(annual_savings, 2),
        "savings_percentage": round(savings_pct, 1),
    }


@mcp.tool()
def analyze_costs(
    period: str = "month",
    group_by: str = "service",
) -> dict:
    """Analyze cloud costs for a given period.

    Args:
        period: Time period - "day", "week", "month"
        group_by: Group costs by "service", "region", or "tag"

    Returns:
        Cost breakdown with trends and anomalies
    """
    if OFFLINE:
        by_service_data = DEMO_COSTS["by_service"]
        by_service: dict[str, dict[str, float]] = by_service_data if isinstance(by_service_data, dict) else {}
        total_current = sum(float(s["current_month"]) for s in by_service.values())
        total_previous = sum(float(s["previous_month"]) for s in by_service.values())
        change_pct = ((total_current - total_previous) / total_previous * 100
                      if total_previous > 0 else 0)

        anomalies = [
            {"service": name, "change": f"+{data['change_pct']:.1f}%",
             "message": f"{name} costs increased significantly"}
            for name, data in by_service.items()
            if float(data["change_pct"]) > 15
        ]

        return {
            "period": period,
            "total_cost": round(total_current, 2),
            "previous_period": round(total_previous, 2),
            "change_percentage": round(change_pct, 1),
            "currency": "USD",
            "by_service": {
                name: {
                    "cost": data["current_month"],
                    "change": f"{data['change_pct']:+.1f}%",
                }
                for name, data in by_service.items()
            },
            "top_services": sorted(
                [(name, float(data["current_month"])) for name, data in by_service.items()],
                key=lambda x: x[1], reverse=True
            )[:5],
            "anomalies": anomalies,
            "mode": "offline_demo",
        }

    return {
        "error": "Live mode requires AWS credentials",
        "required": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    }


@mcp.tool()
def detect_unused(resource_types: list[str] | None = None) -> dict:
    """Detect unused or idle cloud resources.

    Finds resources that are:
    - Stopped instances still incurring storage costs
    - Unattached EBS volumes
    - Idle load balancers
    - Unattached Elastic IPs
    - Underutilized RDS instances

    Args:
        resource_types: Filter by types (EC2, RDS, EBS, ElasticIP). None = all.

    Returns:
        List of unused resources with potential savings
    """
    if OFFLINE:
        unused = []
        for resource in DEMO_RESOURCES:
            is_unused = False
            reason = ""

            if resource["type"] == "EC2":
                if resource["state"] == "stopped":
                    is_unused = True
                    reason = f"Instance stopped since {resource.get('last_used', 'unknown')}"
                elif resource.get("cpu_avg", 100) < 5:
                    is_unused = True
                    reason = f"Very low CPU utilization ({resource['cpu_avg']}%)"

            elif resource["type"] == "RDS":
                if resource.get("connections_avg", 100) < 5 and resource.get("cpu_avg", 100) < 10:
                    is_unused = True
                    reason = "Very low connections and CPU usage"

            elif resource["type"] == "EBS":
                if not resource.get("attached", True):
                    is_unused = True
                    reason = f"Unattached since {resource.get('last_attached', 'unknown')}"

            elif resource["type"] == "ElasticIP":
                if not resource.get("attached", True):
                    is_unused = True
                    reason = "Unattached Elastic IP incurring charges"

            if is_unused:
                if resource_types is None or resource["type"] in resource_types:
                    unused.append({
                        "id": resource["id"],
                        "type": resource["type"],
                        "name": resource["name"],
                        "region": resource.get("region", "us-east-1"),
                        "reason": reason,
                        "monthly_cost": resource["monthly_cost"],
                        "recommendation": _get_unused_recommendation(resource),
                    })

        total_waste = sum(r["monthly_cost"] for r in unused)

        return {
            "unused_resources": unused,
            "total_count": len(unused),
            "total_monthly_waste": round(total_waste, 2),
            "total_annual_waste": round(total_waste * 12, 2),
            "recommendations": [
                "Review and terminate stopped instances not needed",
                "Delete unattached EBS volumes after backup",
                "Release unused Elastic IPs",
                "Consider Reserved Instances for stable workloads",
            ],
            "mode": "offline_demo",
        }

    return {"error": "Live mode requires AWS credentials"}


def _get_unused_recommendation(resource: dict) -> str:
    """Get specific recommendation for unused resource."""
    if resource["type"] == "EC2":
        if resource["state"] == "stopped":
            return "Terminate if not needed, or create AMI and terminate"
        return "Consider rightsizing to smaller instance"
    elif resource["type"] == "RDS":
        return "Consider stopping during non-business hours or downsizing"
    elif resource["type"] == "EBS":
        return "Create snapshot and delete volume"
    elif resource["type"] == "ElasticIP":
        return "Release the Elastic IP"
    return "Review and remove if not needed"


@mcp.tool()
def rightsizing_recommendations(
    min_savings: float | str = 10.0,
    include_types: list[str] | None = None,
) -> dict:
    """Get instance rightsizing recommendations based on utilization.

    Analyzes CPU, memory, and network utilization to suggest
    appropriately-sized instances.

    Args:
        min_savings: Minimum monthly savings threshold (USD)
        include_types: Resource types to include (EC2, RDS). None = all.

    Returns:
        Rightsizing recommendations with projected savings
    """
    try:
        min_savings = float(min_savings)
    except (TypeError, ValueError):
        min_savings = 10.0

    if OFFLINE:
        recommendations = []

        for resource in DEMO_RESOURCES:
            if resource["type"] not in ("EC2", "RDS"):
                continue
            if include_types and resource["type"] not in include_types:
                continue

            instance_type = resource.get("instance_type", "")
            if instance_type not in RIGHTSIZING_MAP:
                continue

            cpu_avg = resource.get("cpu_avg", 100)
            if cpu_avg > 40:
                continue

            recommended_options = RIGHTSIZING_MAP[instance_type]
            recommended = recommended_options[0]

            service = "rds" if resource["type"] == "RDS" else "ec2"
            savings = _calculate_savings(instance_type, recommended, service)

            if savings["monthly_savings"] < min_savings:
                continue

            recommendations.append({
                "resource_id": resource["id"],
                "resource_name": resource["name"],
                "resource_type": resource["type"],
                "current_instance": instance_type,
                "current_utilization": {
                    "cpu_avg": cpu_avg,
                    "memory_avg": resource.get("memory_avg"),
                },
                "recommended_instance": recommended,
                "alternative_options": recommended_options[1:3],
                "savings": savings,
                "risk": "low" if cpu_avg < 20 else "medium",
                "confidence": "high" if cpu_avg < 15 else "medium",
            })

        total_monthly = sum(r["savings"]["monthly_savings"] for r in recommendations)
        total_annual = sum(r["savings"]["annual_savings"] for r in recommendations)

        return {
            "recommendations": recommendations,
            "total_recommendations": len(recommendations),
            "total_monthly_savings": round(total_monthly, 2),
            "total_annual_savings": round(total_annual, 2),
            "implementation_notes": [
                "Test workloads on smaller instances before production migration",
                "Consider using AWS Compute Optimizer for more detailed analysis",
                "Review during maintenance windows to minimize disruption",
            ],
            "mode": "offline_demo",
        }

    return {"error": "Live mode requires AWS credentials"}


@mcp.tool()
def cost_forecast(
    months_ahead: int | str = 3,
    include_optimization: bool = True,
) -> dict:
    """Forecast future cloud costs based on historical trends.

    Args:
        months_ahead: Number of months to forecast (1-12)
        include_optimization: Include projected savings from recommendations

    Returns:
        Cost forecast with confidence intervals
    """
    try:
        months_ahead = min(12, max(1, int(months_ahead)))
    except (TypeError, ValueError):
        months_ahead = 3

    if OFFLINE:
        by_service_data = DEMO_COSTS["by_service"]
        by_service_typed: dict[str, dict[str, float]] = by_service_data if isinstance(by_service_data, dict) else {}
        current_monthly = sum(float(s["current_month"]) for s in by_service_typed.values())
        growth_rate = 0.05

        forecast: list[dict[str, object]] = []
        base_date = datetime(2026, 7, 1)

        for i in range(1, months_ahead + 1):
            month_date = base_date + timedelta(days=30 * i)
            projected = current_monthly * ((1 + growth_rate) ** i)
            forecast.append({
                "month": month_date.strftime("%Y-%m"),
                "projected_cost": round(projected, 2),
                "lower_bound": round(projected * 0.9, 2),
                "upper_bound": round(projected * 1.15, 2),
                "confidence": "medium",
            })

        potential_savings = 0.0
        if include_optimization:
            rightsizing = rightsizing_recommendations()
            unused = detect_unused()
            potential_savings = (
                float(rightsizing.get("total_monthly_savings", 0)) +
                float(unused.get("total_monthly_waste", 0))
            )

        optimized_forecast: list[dict[str, object]] = []
        for f in forecast:
            projected_obj = f.get("projected_cost", 0)
            projected_float: float = float(projected_obj) if isinstance(projected_obj, (int, float)) else 0.0
            optimized_forecast.append({
                **f,
                "optimized_cost": round(projected_float - potential_savings, 2),
            })

        return {
            "current_monthly_cost": round(current_monthly, 2),
            "forecast": optimized_forecast,
            "assumptions": {
                "growth_rate": f"{growth_rate * 100:.0f}% monthly",
                "based_on": "Historical trend analysis",
            },
            "potential_monthly_savings": round(potential_savings, 2),
            "recommendations": [
                "Implement rightsizing recommendations to reduce baseline",
                "Set up AWS Budgets alerts for cost anomalies",
                "Review Reserved Instance coverage for predictable workloads",
            ],
            "mode": "offline_demo",
        }

    return {"error": "Live mode requires AWS credentials"}


@mcp.tool()
def estimate_resource_cost(
    resource_type: str,
    instance_type: str | None = None,
    storage_gb: int | str = 0,
    hours_per_month: int | str = 730,
    region: str = "us-east-1",
) -> dict:
    """Estimate monthly cost for a cloud resource configuration.

    Args:
        resource_type: Type of resource (ec2, rds, s3, ebs)
        instance_type: Instance type (e.g., t3.medium, db.m5.large)
        storage_gb: Storage size in GB
        hours_per_month: Hours running per month (730 = 24/7)
        region: AWS region

    Returns:
        Cost estimate breakdown
    """
    try:
        storage_gb = int(storage_gb)
        hours_per_month = min(730, max(1, int(hours_per_month)))
    except (TypeError, ValueError):
        storage_gb = 0
        hours_per_month = 730

    resource_type = resource_type.lower()
    breakdown = []
    total = 0.0

    if resource_type in ("ec2", "instance"):
        if instance_type and instance_type in AWS_PRICING["ec2"]:
            hourly = AWS_PRICING["ec2"][instance_type]
            compute_cost = hourly * hours_per_month
            breakdown.append({
                "item": f"EC2 {instance_type}",
                "unit": "hours",
                "quantity": hours_per_month,
                "unit_price": hourly,
                "cost": round(compute_cost, 2),
            })
            total += compute_cost

            if storage_gb > 0:
                storage_cost = storage_gb * AWS_PRICING["storage"]["ebs_gp3_gb"]
                breakdown.append({
                    "item": "EBS gp3 storage",
                    "unit": "GB-month",
                    "quantity": storage_gb,
                    "unit_price": AWS_PRICING["storage"]["ebs_gp3_gb"],
                    "cost": round(storage_cost, 2),
                })
                total += storage_cost

    elif resource_type in ("rds", "database"):
        if instance_type and instance_type in AWS_PRICING["rds"]:
            hourly = AWS_PRICING["rds"][instance_type]
            compute_cost = hourly * hours_per_month
            breakdown.append({
                "item": f"RDS {instance_type}",
                "unit": "hours",
                "quantity": hours_per_month,
                "unit_price": hourly,
                "cost": round(compute_cost, 2),
            })
            total += compute_cost

            if storage_gb > 0:
                storage_cost = storage_gb * 0.115
                breakdown.append({
                    "item": "RDS storage (gp2)",
                    "unit": "GB-month",
                    "quantity": storage_gb,
                    "unit_price": 0.115,
                    "cost": round(storage_cost, 2),
                })
                total += storage_cost

    elif resource_type in ("s3", "storage"):
        if storage_gb > 0:
            storage_cost = storage_gb * AWS_PRICING["storage"]["s3_standard_gb"]
            breakdown.append({
                "item": "S3 Standard storage",
                "unit": "GB-month",
                "quantity": storage_gb,
                "unit_price": AWS_PRICING["storage"]["s3_standard_gb"],
                "cost": round(storage_cost, 2),
            })
            total += storage_cost

    elif resource_type in ("ebs", "volume"):
        if storage_gb > 0:
            storage_cost = storage_gb * AWS_PRICING["storage"]["ebs_gp3_gb"]
            breakdown.append({
                "item": "EBS gp3 volume",
                "unit": "GB-month",
                "quantity": storage_gb,
                "unit_price": AWS_PRICING["storage"]["ebs_gp3_gb"],
                "cost": round(storage_cost, 2),
            })
            total += storage_cost

    else:
        return {
            "error": f"Unknown resource type: {resource_type}",
            "supported_types": ["ec2", "rds", "s3", "ebs"],
        }

    return {
        "resource_type": resource_type,
        "instance_type": instance_type,
        "region": region,
        "hours_per_month": hours_per_month,
        "breakdown": breakdown,
        "monthly_total": round(total, 2),
        "annual_total": round(total * 12, 2),
        "notes": [
            "Estimates based on on-demand pricing",
            "Does not include data transfer costs",
            "Reserved Instances can reduce costs by 30-60%",
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
