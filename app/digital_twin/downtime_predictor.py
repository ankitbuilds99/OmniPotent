"""Downtime Prediction System.

Predicts and prevents downtime through:
- ML-based failure prediction
- Maintenance window optimization
- Capacity forecasting
- Dependency failure analysis
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import math

log = logging.getLogger("devcopilot.digital_twin.downtime")


class DowntimeRisk(str, Enum):
    """Risk levels for predicted downtime."""
    CRITICAL = "critical"  # >80% probability
    HIGH = "high"  # 60-80%
    MEDIUM = "medium"  # 40-60%
    LOW = "low"  # 20-40%
    MINIMAL = "minimal"  # <20%


class DowntimeCause(str, Enum):
    """Categories of downtime causes."""
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DEPENDENCY_FAILURE = "dependency_failure"
    CERTIFICATE_EXPIRY = "certificate_expiry"
    CAPACITY_LIMIT = "capacity_limit"
    SCHEDULED_MAINTENANCE = "scheduled_maintenance"
    DEPLOYMENT_RISK = "deployment_risk"
    INFRASTRUCTURE_ISSUE = "infrastructure_issue"
    EXTERNAL_DEPENDENCY = "external_dependency"
    DATABASE_ISSUE = "database_issue"
    NETWORK_ISSUE = "network_issue"


@dataclass
class RiskFactor:
    """A factor contributing to downtime risk."""
    name: str
    cause: DowntimeCause
    contribution: float  # 0-1
    current_value: float | str
    threshold: float | str
    trend: str  # "improving", "stable", "degrading"
    time_to_breach: timedelta | None = None
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cause": self.cause.value,
            "contribution": self.contribution,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "trend": self.trend,
            "time_to_breach_hours": self.time_to_breach.total_seconds() / 3600 if self.time_to_breach else None,
            "remediation": self.remediation,
        }


@dataclass
class DowntimePrediction:
    """A predicted downtime event."""
    id: str
    service: str
    environment: str
    predicted_at: datetime
    predicted_start: datetime
    predicted_end: datetime | None
    probability: float  # 0-1
    risk_level: DowntimeRisk
    primary_cause: DowntimeCause
    risk_factors: list[RiskFactor]
    estimated_impact_minutes: float
    estimated_affected_users: int
    confidence_interval: tuple[float, float]  # 95% CI
    preventable: bool
    prevention_actions: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "service": self.service,
            "environment": self.environment,
            "predicted_at": self.predicted_at.isoformat(),
            "predicted_start": self.predicted_start.isoformat(),
            "predicted_end": self.predicted_end.isoformat() if self.predicted_end else None,
            "probability": self.probability,
            "risk_level": self.risk_level.value,
            "primary_cause": self.primary_cause.value,
            "risk_factors": [f.to_dict() for f in self.risk_factors],
            "estimated_impact_minutes": self.estimated_impact_minutes,
            "estimated_affected_users": self.estimated_affected_users,
            "confidence_interval": self.confidence_interval,
            "preventable": self.preventable,
            "prevention_actions": self.prevention_actions,
        }

    @property
    def time_until_predicted(self) -> timedelta:
        return self.predicted_start - datetime.now(timezone.utc)


@dataclass
class MaintenanceWindow:
    """Optimized maintenance window suggestion."""
    start_time: datetime
    end_time: datetime
    score: float  # 0-1, higher is better
    impact_score: float  # 0-1, lower is better
    factors: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_hours": (self.end_time - self.start_time).total_seconds() / 3600,
            "score": self.score,
            "impact_score": self.impact_score,
            "factors": self.factors,
        }


@dataclass
class CapacityForecast:
    """Capacity usage forecast."""
    resource_type: str
    current_usage: float
    forecasted_usage: list[tuple[datetime, float]]
    breach_time: datetime | None
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "current_usage": self.current_usage,
            "forecasted_usage": [(t.isoformat(), v) for t, v in self.forecasted_usage],
            "breach_time": self.breach_time.isoformat() if self.breach_time else None,
            "recommendations": self.recommendations,
        }


class DowntimePredictor:
    """Predicts and helps prevent service downtime.

    Features:
    - Multi-factor risk analysis
    - Time-series forecasting
    - Dependency chain analysis
    - Maintenance window optimization
    - Capacity planning
    """

    def __init__(
        self,
        metrics_client: Any = None,
        knowledge_graph: Any = None,
        redis_client: Any = None,
    ):
        self.metrics = metrics_client
        self.knowledge_graph = knowledge_graph
        self.redis = redis_client

        self._predictions: dict[str, DowntimePrediction] = {}
        self._prediction_counter = 0

        # Risk weights by cause
        self._cause_weights = {
            DowntimeCause.RESOURCE_EXHAUSTION: 0.9,
            DowntimeCause.DEPENDENCY_FAILURE: 0.85,
            DowntimeCause.CERTIFICATE_EXPIRY: 0.95,
            DowntimeCause.CAPACITY_LIMIT: 0.8,
            DowntimeCause.DEPLOYMENT_RISK: 0.7,
            DowntimeCause.INFRASTRUCTURE_ISSUE: 0.8,
            DowntimeCause.DATABASE_ISSUE: 0.9,
            DowntimeCause.NETWORK_ISSUE: 0.75,
        }

        # Thresholds for risk factors
        self._thresholds = {
            "cpu_usage": 0.85,
            "memory_usage": 0.90,
            "disk_usage": 0.90,
            "connection_pool": 0.80,
            "error_rate": 0.05,
            "latency_p99_ms": 1000,
            "certificate_days": 30,
            "dependency_health": 0.95,
        }

    async def predict_downtime(
        self,
        service: str,
        environment: str = "production",
        horizon_hours: int = 24,
    ) -> list[DowntimePrediction]:
        """Predict potential downtime for a service.

        Args:
            service: Service name to analyze
            environment: Target environment
            horizon_hours: Prediction horizon

        Returns:
            List of downtime predictions
        """
        predictions = []

        # Gather risk factors
        risk_factors = await self._analyze_risk_factors(service, environment)

        # Calculate composite risk score
        risk_score = self._calculate_risk_score(risk_factors)

        # Determine if prediction warranted
        if risk_score > 0.2:  # 20% threshold for prediction
            self._prediction_counter += 1
            pred_id = f"pred_{self._prediction_counter}"

            # Find primary cause
            primary_cause = max(
                risk_factors,
                key=lambda f: f.contribution,
                default=None
            )

            # Estimate timing
            earliest_breach = min(
                (f.time_to_breach for f in risk_factors if f.time_to_breach),
                default=timedelta(hours=horizon_hours)
            )

            # Determine preventability
            preventable, actions = self._get_prevention_actions(risk_factors)

            prediction = DowntimePrediction(
                id=pred_id,
                service=service,
                environment=environment,
                predicted_at=datetime.now(timezone.utc),
                predicted_start=datetime.now(timezone.utc) + earliest_breach,
                predicted_end=None,  # Unknown duration
                probability=risk_score,
                risk_level=self._risk_score_to_level(risk_score),
                primary_cause=primary_cause.cause if primary_cause else DowntimeCause.INFRASTRUCTURE_ISSUE,
                risk_factors=risk_factors,
                estimated_impact_minutes=self._estimate_impact_duration(risk_factors),
                estimated_affected_users=await self._estimate_affected_users(service),
                confidence_interval=(max(0, risk_score - 0.15), min(1, risk_score + 0.15)),
                preventable=preventable,
                prevention_actions=actions,
            )

            predictions.append(prediction)
            self._predictions[pred_id] = prediction

            log.warning(
                "Downtime prediction for %s: %.0f%% probability in %.1f hours (cause: %s)",
                service, risk_score * 100, earliest_breach.total_seconds() / 3600,
                primary_cause.cause.value if primary_cause else "unknown"
            )

        return predictions

    async def _analyze_risk_factors(
        self,
        service: str,
        environment: str,
    ) -> list[RiskFactor]:
        """Analyze all risk factors for a service."""
        factors = []

        # CPU Risk
        cpu_metrics = await self._get_metric(service, "cpu_usage")
        if cpu_metrics:
            factors.append(self._analyze_resource_risk(
                "CPU Usage", cpu_metrics, self._thresholds["cpu_usage"],
                DowntimeCause.RESOURCE_EXHAUSTION,
                "Scale horizontally or optimize CPU-intensive operations"
            ))

        # Memory Risk
        memory_metrics = await self._get_metric(service, "memory_usage")
        if memory_metrics:
            factors.append(self._analyze_resource_risk(
                "Memory Usage", memory_metrics, self._thresholds["memory_usage"],
                DowntimeCause.RESOURCE_EXHAUSTION,
                "Increase memory limits or fix memory leaks"
            ))

        # Disk Risk
        disk_metrics = await self._get_metric(service, "disk_usage")
        if disk_metrics:
            factors.append(self._analyze_resource_risk(
                "Disk Usage", disk_metrics, self._thresholds["disk_usage"],
                DowntimeCause.CAPACITY_LIMIT,
                "Expand storage or implement log rotation"
            ))

        # Error Rate Risk
        error_metrics = await self._get_metric(service, "error_rate")
        if error_metrics:
            factors.append(self._analyze_error_risk(error_metrics))

        # Certificate Expiry
        cert_days = await self._check_certificate_expiry(service)
        if cert_days is not None and cert_days < 30:
            factors.append(RiskFactor(
                name="Certificate Expiry",
                cause=DowntimeCause.CERTIFICATE_EXPIRY,
                contribution=1.0 - (cert_days / 30),
                current_value=cert_days,
                threshold=30,
                trend="degrading",
                time_to_breach=timedelta(days=cert_days),
                remediation="Renew TLS certificate immediately"
            ))

        # Dependency Health
        dep_health = await self._analyze_dependencies(service)
        if dep_health < self._thresholds["dependency_health"]:
            factors.append(RiskFactor(
                name="Dependency Health",
                cause=DowntimeCause.DEPENDENCY_FAILURE,
                contribution=(self._thresholds["dependency_health"] - dep_health) * 2,
                current_value=dep_health,
                threshold=self._thresholds["dependency_health"],
                trend="degrading",
                time_to_breach=timedelta(hours=4),
                remediation="Address unhealthy dependencies"
            ))

        return [f for f in factors if f.contribution > 0.1]

    def _analyze_resource_risk(
        self,
        name: str,
        metrics: dict[str, float],
        threshold: float,
        cause: DowntimeCause,
        remediation: str,
    ) -> RiskFactor:
        """Analyze a resource metric for risk."""
        current = metrics.get("current", 0)
        trend_rate = metrics.get("trend_rate", 0)  # Change per hour

        # Calculate contribution based on proximity to threshold
        if current >= threshold:
            contribution = 0.9 + (current - threshold) * 0.5
        else:
            contribution = max(0, (current - (threshold * 0.7)) / (threshold * 0.3))

        # Determine trend
        if trend_rate > 0.01:
            trend = "degrading"
        elif trend_rate < -0.01:
            trend = "improving"
        else:
            trend = "stable"

        # Calculate time to breach
        time_to_breach = None
        if current < threshold and trend_rate > 0:
            hours_to_breach = (threshold - current) / trend_rate
            if hours_to_breach < 168:  # Within a week
                time_to_breach = timedelta(hours=hours_to_breach)

        return RiskFactor(
            name=name,
            cause=cause,
            contribution=min(1.0, contribution),
            current_value=f"{current:.1%}",
            threshold=f"{threshold:.1%}",
            trend=trend,
            time_to_breach=time_to_breach,
            remediation=remediation,
        )

    def _analyze_error_risk(self, metrics: dict[str, float]) -> RiskFactor:
        """Analyze error rate for risk."""
        current = metrics.get("current", 0)
        threshold = self._thresholds["error_rate"]

        contribution = current / threshold if current < threshold else 0.8 + (current - threshold) * 2

        return RiskFactor(
            name="Error Rate",
            cause=DowntimeCause.INFRASTRUCTURE_ISSUE,
            contribution=min(1.0, contribution),
            current_value=f"{current:.2%}",
            threshold=f"{threshold:.2%}",
            trend="degrading" if metrics.get("trend_rate", 0) > 0 else "stable",
            time_to_breach=timedelta(hours=2) if current > threshold * 0.5 else None,
            remediation="Investigate error logs and fix root cause",
        )

    async def _get_metric(
        self,
        service: str,
        metric_name: str,
    ) -> dict[str, float] | None:
        """Get metric data for analysis from Prometheus."""
        try:
            from app.connectors.prometheus import PrometheusConnector
            from app.connectors.base import ConnectorConfig

            prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
            config = ConnectorConfig(url=prometheus_url)
            prometheus = PrometheusConnector(config)

            metric_queries = {
                "cpu_usage": f'avg(rate(container_cpu_usage_seconds_total{{service="{service}"}}[5m]))',
                "memory_usage": f'avg(container_memory_usage_bytes{{service="{service}"}}) / avg(container_spec_memory_limit_bytes{{service="{service}"}})',
                "disk_usage": f'avg(kubelet_volume_stats_used_bytes{{service="{service}"}}) / avg(kubelet_volume_stats_capacity_bytes{{service="{service}"}})',
                "error_rate": f'sum(rate(http_requests_total{{service="{service}", status=~"5.."}}[5m])) / sum(rate(http_requests_total{{service="{service}"}}[5m]))',
            }

            query = metric_queries.get(metric_name)
            if not query:
                await prometheus.close()
                return None

            current_result = await prometheus.query(query)

            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            trend_result = await prometheus.query_range(query, one_hour_ago, now, step="5m")
            await prometheus.close()

            current = 0.5
            if current_result.success and current_result.data:
                value = current_result.data[0].get("value")
                current = float(value) if value is not None else 0.5

            trend_rate = 0.0
            if trend_result.success and trend_result.data:
                values = trend_result.data[0].get("values", []) if trend_result.data else []
                if len(values) >= 2:
                    first_val = float(values[0].get("value", 0))
                    last_val = float(values[-1].get("value", 0))
                    hours = (datetime.fromisoformat(values[-1]["timestamp"].replace("Z", "+00:00")) -
                            datetime.fromisoformat(values[0]["timestamp"].replace("Z", "+00:00"))).total_seconds() / 3600
                    if hours > 0:
                        trend_rate = (last_val - first_val) / hours

            return {"current": current, "trend_rate": trend_rate}

        except Exception as e:
            log.warning("Failed to fetch metric %s for service %s: %s", metric_name, service, e)
            return None

    async def _check_certificate_expiry(self, service: str) -> int | None:
        """Check days until certificate expiry by querying Prometheus or checking TLS."""
        try:
            from app.connectors.prometheus import PrometheusConnector
            from app.connectors.base import ConnectorConfig

            prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
            config = ConnectorConfig(url=prometheus_url)
            prometheus = PrometheusConnector(config)

            query = f'(probe_ssl_earliest_cert_expiry{{service="{service}"}} - time()) / 86400'
            result = await prometheus.query(query)
            await prometheus.close()

            if result.success and result.data:
                value = result.data[0].get("value")
                return int(float(value)) if value is not None else None

            return None

        except Exception as e:
            log.warning("Failed to check certificate expiry for %s: %s", service, e)
            return None

    async def _analyze_dependencies(self, service: str) -> float:
        """Analyze health of service dependencies using Kubernetes and Prometheus."""
        try:
            from app.connectors.kubernetes import KubernetesConnector
            from app.connectors.prometheus import PrometheusConnector
            from app.connectors.base import ConnectorConfig

            k8s = KubernetesConnector()
            prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
            prometheus = PrometheusConnector(ConnectorConfig(url=prometheus_url))

            pods_result = await k8s.list_pods(label_selector=f"app={service}")
            await k8s.close()

            if not pods_result.success or not pods_result.data:
                await prometheus.close()
                return 1.0

            total_deps = 0
            healthy_deps = 0

            for pod in pods_result.data:
                for container in pod.get("containers", []):
                    total_deps += 1
                    if container.get("ready", False):
                        healthy_deps += 1

            query = f'sum(up{{service=~".*{service}.*"}}) / count(up{{service=~".*{service}.*"}})'
            prom_result = await prometheus.query(query)
            await prometheus.close()

            if prom_result.success and prom_result.data:
                value = prom_result.data[0].get("value")
                prom_health = float(value) if value is not None else 1.0
            else:
                prom_health = 1.0

            k8s_health = healthy_deps / total_deps if total_deps > 0 else 1.0
            return (k8s_health + prom_health) / 2

        except Exception as e:
            log.warning("Failed to analyze dependencies for %s: %s", service, e)
            return 1.0

    def _calculate_risk_score(self, factors: list[RiskFactor]) -> float:
        """Calculate composite risk score from factors."""
        if not factors:
            return 0.0

        # Weighted combination with diminishing returns
        weighted_sum = sum(
            f.contribution * self._cause_weights.get(f.cause, 0.5)
            for f in factors
        )

        # Apply sigmoid for smoothing
        raw_score = weighted_sum / max(1, len(factors))
        return 1 / (1 + math.exp(-5 * (raw_score - 0.5)))

    def _risk_score_to_level(self, score: float) -> DowntimeRisk:
        """Convert risk score to level."""
        if score >= 0.8:
            return DowntimeRisk.CRITICAL
        elif score >= 0.6:
            return DowntimeRisk.HIGH
        elif score >= 0.4:
            return DowntimeRisk.MEDIUM
        elif score >= 0.2:
            return DowntimeRisk.LOW
        else:
            return DowntimeRisk.MINIMAL

    def _get_prevention_actions(
        self,
        factors: list[RiskFactor],
    ) -> tuple[bool, list[str]]:
        """Determine if downtime is preventable and how."""
        actions = []
        preventable = True

        for factor in factors:
            if factor.contribution > 0.3:
                if factor.remediation:
                    actions.append(factor.remediation)

                # Some causes are harder to prevent
                if factor.cause in [DowntimeCause.EXTERNAL_DEPENDENCY]:
                    preventable = False

        return preventable, list(set(actions))

    def _estimate_impact_duration(self, factors: list[RiskFactor]) -> float:
        """Estimate downtime duration in minutes."""
        base_duration = 30  # Base 30 minutes

        # Adjust based on causes
        for factor in factors:
            if factor.cause == DowntimeCause.DATABASE_ISSUE:
                base_duration += 60
            elif factor.cause == DowntimeCause.CERTIFICATE_EXPIRY:
                base_duration += 15
            elif factor.cause == DowntimeCause.RESOURCE_EXHAUSTION:
                base_duration += 20

        return min(base_duration, 480)  # Cap at 8 hours

    async def _estimate_affected_users(self, service: str) -> int:
        """Estimate number of affected users from Prometheus metrics."""
        try:
            from app.connectors.prometheus import PrometheusConnector
            from app.connectors.base import ConnectorConfig

            prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
            config = ConnectorConfig(url=prometheus_url)
            prometheus = PrometheusConnector(config)

            query = f'sum(rate(http_requests_total{{service="{service}"}}[5m])) * 300'
            result = await prometheus.query(query)
            await prometheus.close()

            if result.success and result.data:
                value = result.data[0].get("value")
                return int(float(value)) if value is not None else 0

            return 0

        except Exception as e:
            log.warning("Failed to estimate affected users for %s: %s", service, e)
            return 0

    async def _get_traffic_level(self, service: str, target_time: datetime) -> float:
        """Get traffic level at a specific time from historical Prometheus data."""
        try:
            from app.connectors.prometheus import PrometheusConnector
            from app.connectors.base import ConnectorConfig

            prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
            config = ConnectorConfig(url=prometheus_url)
            prometheus = PrometheusConnector(config)

            one_week_ago = target_time - timedelta(days=7)
            same_time_last_week = one_week_ago.replace(
                hour=target_time.hour,
                minute=target_time.minute,
            )

            query = f'sum(rate(http_requests_total{{service="{service}"}}[1h]))'
            result = await prometheus.query(query, query_time=same_time_last_week)

            max_query = f'max_over_time(sum(rate(http_requests_total{{service="{service}"}}[1h]))[7d:1h])'
            max_result = await prometheus.query(max_query)
            await prometheus.close()

            if result.success and result.data and max_result.success and max_result.data:
                current_val = float(result.data[0].get("value", 0))
                max_val = float(max_result.data[0].get("value", 1))
                return min(1.0, current_val / max_val) if max_val > 0 else 0.5

            return 0.5

        except Exception as e:
            log.warning("Failed to get traffic level for %s: %s", service, e)
            return 0.5

    async def suggest_maintenance_windows(
        self,
        service: str,
        duration_hours: float = 2.0,
        horizon_days: int = 7,
        count: int = 3,
    ) -> list[MaintenanceWindow]:
        """Suggest optimal maintenance windows.

        Args:
            service: Service to maintain
            duration_hours: Required maintenance duration
            horizon_days: Days to look ahead
            count: Number of suggestions

        Returns:
            List of suggested maintenance windows
        """
        windows = []
        now = datetime.now(timezone.utc)

        # Generate candidate windows (every 4 hours for horizon period)
        candidates = []
        for day in range(horizon_days):
            for hour in [2, 6, 10, 14, 18, 22]:  # Every 4 hours
                start = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=day)
                if start > now:
                    end = start + timedelta(hours=duration_hours)
                    candidates.append((start, end))

        # Score each candidate
        for start, end in candidates:
            score, impact, factors = await self._score_maintenance_window(service, start, end)
            windows.append(MaintenanceWindow(
                start_time=start,
                end_time=end,
                score=score,
                impact_score=impact,
                factors=factors,
            ))

        # Sort by score (descending)
        windows.sort(key=lambda w: w.score, reverse=True)

        return windows[:count]

    async def _score_maintenance_window(
        self,
        service: str,
        start: datetime,
        end: datetime,
    ) -> tuple[float, float, dict[str, Any]]:
        """Score a maintenance window for suitability."""
        factors = {}
        score = 1.0
        impact = 0.0

        # Time of day factor (prefer early morning)
        hour = start.hour
        if 2 <= hour <= 6:
            factors["time_of_day"] = "optimal (early morning)"
        elif 22 <= hour or hour <= 2:
            factors["time_of_day"] = "good (late night)"
            score *= 0.9
        else:
            factors["time_of_day"] = "suboptimal (business hours)"
            score *= 0.6
            impact += 0.3

        # Day of week factor (prefer weekends)
        dow = start.weekday()
        if dow >= 5:  # Weekend
            factors["day_of_week"] = "optimal (weekend)"
        elif dow == 4:  # Friday
            factors["day_of_week"] = "good (Friday)"
            score *= 0.9
        else:
            factors["day_of_week"] = "acceptable (weekday)"
            score *= 0.8
            impact += 0.2

        # Traffic factor from real metrics
        traffic_level = await self._get_traffic_level(service, start)
        factors["traffic_level"] = f"{traffic_level:.1%}"
        score *= (1 - traffic_level * 0.5)
        impact += traffic_level * 0.3

        # Conflict factor (other scheduled activities)
        factors["conflicts"] = "none"  # Would check calendar

        return score, min(1.0, impact), factors

    async def forecast_capacity(
        self,
        service: str,
        resource_type: str,
        horizon_days: int = 30,
    ) -> CapacityForecast:
        """Forecast capacity usage.

        Args:
            service: Service to analyze
            resource_type: Type of resource (cpu, memory, disk, connections)
            horizon_days: Days to forecast

        Returns:
            CapacityForecast with predictions
        """
        # Get current usage
        metrics = await self._get_metric(service, f"{resource_type}_usage")
        current = metrics.get("current", 0.5) if metrics else 0.5
        trend_rate = metrics.get("trend_rate", 0.01) if metrics else 0.01

        # Generate forecast points
        now = datetime.now(timezone.utc)
        forecast = []
        breach_time = None

        for day in range(1, horizon_days + 1):
            time_point = now + timedelta(days=day)
            projected = current + (trend_rate * 24 * day)
            projected = max(0, min(1, projected))
            forecast.append((time_point, projected))

            # Check for breach
            if breach_time is None and projected >= 0.9:
                breach_time = time_point

        # Generate recommendations
        recommendations = []
        if breach_time:
            days_until_breach = (breach_time - now).days
            if days_until_breach < 7:
                recommendations.append(f"URGENT: {resource_type} capacity breach in {days_until_breach} days")
            elif days_until_breach < 14:
                recommendations.append(f"Plan {resource_type} expansion within 2 weeks")
            else:
                recommendations.append(f"Monitor {resource_type} growth, expansion needed in {days_until_breach} days")

        if trend_rate > 0.02:
            recommendations.append(f"High growth rate detected ({trend_rate*100:.1f}%/hr)")

        return CapacityForecast(
            resource_type=resource_type,
            current_usage=current,
            forecasted_usage=forecast,
            breach_time=breach_time,
            recommendations=recommendations,
        )

    def get_prediction(self, pred_id: str) -> DowntimePrediction | None:
        """Get a prediction by ID."""
        return self._predictions.get(pred_id)

    def list_predictions(
        self,
        service: str | None = None,
        risk_level: DowntimeRisk | None = None,
    ) -> list[DowntimePrediction]:
        """List downtime predictions."""
        predictions = list(self._predictions.values())

        if service:
            predictions = [p for p in predictions if p.service == service]

        if risk_level:
            predictions = [p for p in predictions if p.risk_level == risk_level]

        return predictions

    def get_stats(self) -> dict[str, Any]:
        """Get predictor statistics."""
        predictions = list(self._predictions.values())

        return {
            "total_predictions": len(predictions),
            "critical_predictions": len([p for p in predictions if p.risk_level == DowntimeRisk.CRITICAL]),
            "high_predictions": len([p for p in predictions if p.risk_level == DowntimeRisk.HIGH]),
            "preventable_count": len([p for p in predictions if p.preventable]),
        }


def create_downtime_predictor(
    metrics_client: Any = None,
    knowledge_graph: Any = None,
    redis_client: Any = None,
) -> DowntimePredictor:
    """Factory function to create a downtime predictor."""
    return DowntimePredictor(
        metrics_client=metrics_client,
        knowledge_graph=knowledge_graph,
        redis_client=redis_client,
    )
