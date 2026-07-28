"""Benchmark comparisons.

Compare organization metrics against industry benchmarks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("devcopilot.roi.benchmarks")


@dataclass
class Benchmark:
    """A benchmark metric."""

    metric_name: str
    your_value: float
    industry_average: float
    top_10_percent: float
    unit: str = ""
    lower_is_better: bool = True

    @property
    def percentile(self) -> int:
        """Calculate which percentile the user is in."""
        if self.lower_is_better:
            if self.your_value <= self.top_10_percent:
                return 95
            elif self.your_value <= self.industry_average * 0.7:
                return 85
            elif self.your_value <= self.industry_average:
                return 50
            else:
                return 25
        else:
            if self.your_value >= self.top_10_percent:
                return 95
            elif self.your_value >= self.industry_average * 1.3:
                return 85
            elif self.your_value >= self.industry_average:
                return 50
            else:
                return 25

    @property
    def badge(self) -> str:
        """Get achievement badge."""
        pct = self.percentile
        if pct >= 90:
            return "Top 10%"
        elif pct >= 75:
            return "Top 25%"
        elif pct >= 50:
            return "Above Average"
        else:
            return "Below Average"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name,
            "your_value": self.your_value,
            "industry_average": self.industry_average,
            "top_10_percent": self.top_10_percent,
            "unit": self.unit,
            "percentile": self.percentile,
            "badge": self.badge,
        }


@dataclass
class BenchmarkComparison:
    """Full benchmark comparison for an organization."""

    org_id: str
    team_size_bucket: str
    industry: str
    benchmarks: list[Benchmark]
    overall_percentile: int
    overall_badge: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "team_size": self.team_size_bucket,
            "industry": self.industry,
            "benchmarks": [b.to_dict() for b in self.benchmarks],
            "overall_percentile": self.overall_percentile,
            "overall_badge": self.overall_badge,
        }


class BenchmarkEngine:
    """Calculates benchmark comparisons."""

    def __init__(self) -> None:
        self._industry_benchmarks = {
            "default": {
                "mttr_minutes": {"average": 120, "top_10": 25},
                "mttd_minutes": {"average": 45, "top_10": 5},
                "incidents_per_month": {"average": 62, "top_10": 20},
                "root_cause_accuracy_percent": {"average": 67, "top_10": 95},
                "oncall_pages_per_month": {"average": 89, "top_10": 15},
                "postmortem_completion_percent": {"average": 45, "top_10": 95},
            },
            "fintech": {
                "mttr_minutes": {"average": 90, "top_10": 15},
                "mttd_minutes": {"average": 30, "top_10": 3},
                "incidents_per_month": {"average": 45, "top_10": 12},
                "root_cause_accuracy_percent": {"average": 75, "top_10": 98},
                "oncall_pages_per_month": {"average": 60, "top_10": 10},
                "postmortem_completion_percent": {"average": 60, "top_10": 98},
            },
            "ecommerce": {
                "mttr_minutes": {"average": 100, "top_10": 20},
                "mttd_minutes": {"average": 35, "top_10": 4},
                "incidents_per_month": {"average": 55, "top_10": 18},
                "root_cause_accuracy_percent": {"average": 70, "top_10": 96},
                "oncall_pages_per_month": {"average": 75, "top_10": 12},
                "postmortem_completion_percent": {"average": 50, "top_10": 95},
            },
        }

        self._team_size_multipliers = {
            "1-10": 1.2,
            "11-50": 1.0,
            "51-100": 0.9,
            "100+": 0.8,
        }

    def compare(
        self,
        org_id: str,
        metrics: dict[str, float],
        team_size: int = 50,
        industry: str = "default",
    ) -> BenchmarkComparison:
        """Compare organization metrics against benchmarks."""
        team_bucket = self._get_team_bucket(team_size)
        multiplier = self._team_size_multipliers.get(team_bucket, 1.0)

        industry_data = self._industry_benchmarks.get(
            industry,
            self._industry_benchmarks["default"]
        )

        benchmarks = []

        if "mttr_minutes" in metrics:
            avg = industry_data["mttr_minutes"]["average"] * multiplier
            top = industry_data["mttr_minutes"]["top_10"] * multiplier
            benchmarks.append(Benchmark(
                "MTTR",
                metrics["mttr_minutes"],
                avg,
                top,
                "minutes",
                lower_is_better=True,
            ))

        if "mttd_minutes" in metrics:
            avg = industry_data["mttd_minutes"]["average"] * multiplier
            top = industry_data["mttd_minutes"]["top_10"] * multiplier
            benchmarks.append(Benchmark(
                "MTTD",
                metrics["mttd_minutes"],
                avg,
                top,
                "minutes",
                lower_is_better=True,
            ))

        if "incidents_per_month" in metrics:
            avg = industry_data["incidents_per_month"]["average"] / multiplier
            top = industry_data["incidents_per_month"]["top_10"] / multiplier
            benchmarks.append(Benchmark(
                "Incidents/Month",
                metrics["incidents_per_month"],
                avg,
                top,
                "",
                lower_is_better=True,
            ))

        if "root_cause_accuracy_percent" in metrics:
            avg = industry_data["root_cause_accuracy_percent"]["average"]
            top = industry_data["root_cause_accuracy_percent"]["top_10"]
            benchmarks.append(Benchmark(
                "Root Cause Accuracy",
                metrics["root_cause_accuracy_percent"],
                avg,
                top,
                "%",
                lower_is_better=False,
            ))

        if "oncall_pages_per_month" in metrics:
            avg = industry_data["oncall_pages_per_month"]["average"] / multiplier
            top = industry_data["oncall_pages_per_month"]["top_10"] / multiplier
            benchmarks.append(Benchmark(
                "On-Call Pages",
                metrics["oncall_pages_per_month"],
                avg,
                top,
                "/month",
                lower_is_better=True,
            ))

        if benchmarks:
            overall = sum(b.percentile for b in benchmarks) // len(benchmarks)
        else:
            overall = 50

        if overall >= 90:
            badge = "Top 10% of incident response teams"
        elif overall >= 75:
            badge = "Top 25% of incident response teams"
        elif overall >= 50:
            badge = "Above average incident response"
        else:
            badge = "Room for improvement"

        return BenchmarkComparison(
            org_id=org_id,
            team_size_bucket=team_bucket,
            industry=industry,
            benchmarks=benchmarks,
            overall_percentile=overall,
            overall_badge=badge,
        )

    def _get_team_bucket(self, team_size: int) -> str:
        """Get team size bucket."""
        if team_size <= 10:
            return "1-10"
        elif team_size <= 50:
            return "11-50"
        elif team_size <= 100:
            return "51-100"
        else:
            return "100+"

    def get_improvement_suggestions(
        self,
        comparison: BenchmarkComparison,
    ) -> list[dict[str, Any]]:
        """Get improvement suggestions based on benchmarks."""
        suggestions = []

        for benchmark in comparison.benchmarks:
            if benchmark.percentile < 50:
                if benchmark.metric_name == "MTTR":
                    suggestions.append({
                        "metric": "MTTR",
                        "current": f"{benchmark.your_value} {benchmark.unit}",
                        "target": f"{benchmark.industry_average} {benchmark.unit}",
                        "suggestion": "Enable AI-assisted investigation for faster root cause",
                        "impact": "Could reduce MTTR by 50%+",
                    })
                elif benchmark.metric_name == "Root Cause Accuracy":
                    suggestions.append({
                        "metric": "Root Cause Accuracy",
                        "current": f"{benchmark.your_value}%",
                        "target": f"{benchmark.industry_average}%",
                        "suggestion": "Use AI root cause analysis to improve accuracy",
                        "impact": "More accurate RCA = fewer repeat incidents",
                    })
                elif benchmark.metric_name == "On-Call Pages":
                    suggestions.append({
                        "metric": "On-Call Pages",
                        "current": f"{benchmark.your_value}/month",
                        "target": f"{benchmark.industry_average}/month",
                        "suggestion": "Enable smart alert routing and noise reduction",
                        "impact": "Reduce page fatigue and improve on-call quality",
                    })

        return suggestions


_engine: BenchmarkEngine | None = None


def get_benchmark_engine() -> BenchmarkEngine:
    """Get the default benchmark engine."""
    global _engine
    if _engine is None:
        _engine = BenchmarkEngine()
    return _engine
