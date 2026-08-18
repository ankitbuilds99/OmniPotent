"""Evaluation Reporter - Generate reports and alerts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from app.eval.pipeline import EvalResult

log = logging.getLogger("omnipotent.eval.reporter")


class ReportFormat(str, Enum):
    """Report output formats."""
    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"
    SLACK = "slack"


@dataclass
class RegressionAlert:
    """Alert for detected regression."""
    metric_name: str
    previous_value: float
    current_value: float
    change_percent: float
    severity: str  # warning, critical
    # Set when the alert is about the check itself being unable to run (e.g. an
    # unpriced model) rather than about an observed regression in the number.
    detail: str = ""


def _usd(value: float | None, places: int = 3) -> str:
    """Format a cost for display, or "unknown" when the model had no price.

    Costs are None when no verified price exists (see eval/metrics.estimate_cost).
    Printing that as $0.000 would read as "free" in every report format.
    """
    return "unknown" if value is None else f"${value:.{places}f}"


class EvalReporter:
    """Generates evaluation reports and alerts.

    Supports:
    - Multiple output formats (JSON, Markdown, HTML, Slack)
    - Regression detection against baseline
    - Trend analysis over time
    - Alerting via Slack/PagerDuty
    """

    def __init__(
        self,
        baseline_path: Path | None = None,
        alert_on_regression: bool = True,
        regression_threshold: float = 0.1,  # 10% degradation
    ):
        """Initialize the reporter.

        Args:
            baseline_path: Path to baseline metrics for comparison
            alert_on_regression: Whether to alert on regressions
            regression_threshold: Threshold for regression detection
        """
        self.baseline_path = baseline_path
        self.alert_on_regression = alert_on_regression
        self.regression_threshold = regression_threshold

        self._baseline: dict[str, Any] | None = None
        if baseline_path and baseline_path.exists():
            with open(baseline_path) as f:
                self._baseline = json.load(f)

    def generate_report(
        self,
        result: EvalResult,
        format: ReportFormat = ReportFormat.MARKDOWN,
    ) -> str:
        """Generate a report in the specified format."""
        if format == ReportFormat.JSON:
            return self._generate_json(result)
        elif format == ReportFormat.MARKDOWN:
            return self._generate_markdown(result)
        elif format == ReportFormat.HTML:
            return self._generate_html(result)
        elif format == ReportFormat.SLACK:
            return self._generate_slack(result)
        else:
            raise ValueError(f"Unknown format: {format}")

    def detect_regressions(self, result: EvalResult) -> list[RegressionAlert]:
        """Detect regressions compared to baseline."""
        if not self._baseline:
            return []

        alerts = []
        baseline_metrics = self._baseline.get("metrics", {})

        # Check accuracy regression
        baseline_accuracy = baseline_metrics.get("accuracy", {}).get("exact_match_rate", 0)
        current_accuracy = result.metrics.accuracy.exact_match_rate
        if baseline_accuracy > 0 and current_accuracy < baseline_accuracy * (1 - self.regression_threshold):
            change = (current_accuracy - baseline_accuracy) / baseline_accuracy * 100
            alerts.append(RegressionAlert(
                metric_name="accuracy",
                previous_value=baseline_accuracy,
                current_value=current_accuracy,
                change_percent=change,
                severity="critical" if change < -20 else "warning",
            ))

        # Check latency regression
        baseline_latency = baseline_metrics.get("latency", {}).get("mean_ms", 0)
        current_latency = result.metrics.latency.mean_ms
        if baseline_latency > 0 and current_latency > baseline_latency * (1 + self.regression_threshold):
            change = (current_latency - baseline_latency) / baseline_latency * 100
            alerts.append(RegressionAlert(
                metric_name="latency",
                previous_value=baseline_latency,
                current_value=current_latency,
                change_percent=change,
                severity="critical" if change > 50 else "warning",
            ))

        # Check cost regression.
        # A run containing unpriced models has no comparable cost: its total omits
        # those scenarios, so it always looks flat or cheaper than the baseline and
        # the check below would pass no matter how much the run actually cost. Say
        # so instead of comparing a partial number against a whole one.
        baseline_cost = baseline_metrics.get("cost", {}).get("mean_cost_per_investigation_usd") or 0
        current_cost = result.metrics.cost.mean_cost_per_investigation_usd
        if not result.metrics.cost.cost_is_complete:
            alerts.append(RegressionAlert(
                metric_name="cost",
                previous_value=baseline_cost,
                current_value=current_cost if current_cost is not None else 0.0,
                change_percent=0.0,
                severity="warning",
                detail=(
                    f"cost regression not checked: {result.metrics.cost.unpriced_scenarios} "
                    "scenario(s) ran on a model with no verified price. Add it to "
                    "app/model_catalog.py to restore the check."
                ),
            ))
        elif baseline_cost > 0 and current_cost > baseline_cost * (1 + self.regression_threshold):
            change = (current_cost - baseline_cost) / baseline_cost * 100
            alerts.append(RegressionAlert(
                metric_name="cost",
                previous_value=baseline_cost,
                current_value=current_cost,
                change_percent=change,
                severity="warning",
            ))

        return alerts

    def save_as_baseline(self, result: EvalResult, path: Path) -> None:
        """Save current results as baseline for future comparisons."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(result.to_dict(), f, indent=2, default=str)
        log.info("Saved baseline to %s", path)

    def _generate_json(self, result: EvalResult) -> str:
        """Generate JSON report."""
        return json.dumps(result.to_dict(), indent=2, default=str)

    def _generate_markdown(self, result: EvalResult) -> str:
        """Generate Markdown report."""
        status = "PASSED" if result.overall_passed else "FAILED"
        status_emoji = "✅" if result.overall_passed else "❌"

        md = f"""# Evaluation Report: {result.eval_id}

{status_emoji} **Overall Status: {status}**

## Summary

| Metric | Value |
|--------|-------|
| Started | {result.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')} |
| Duration | {(result.completed_at - result.started_at).total_seconds():.1f}s |
| Scenarios | {result.scenarios_total} |
| Passed | {result.scenarios_passed} ({result.scenarios_passed/max(1,result.scenarios_total)*100:.1f}%) |
| Failed | {result.scenarios_failed} |

## Accuracy Metrics

| Metric | Value |
|--------|-------|
| Exact Match Rate | {result.metrics.accuracy.exact_match_rate:.1%} |
| Keyword Match Rate | {result.metrics.accuracy.keyword_match_rate:.1%} |
| Severity Accuracy | {result.metrics.accuracy.severity_accuracy:.1%} |
| Service F1 | {result.metrics.accuracy.service_f1:.2f} |
| Confidence ECE | {result.metrics.accuracy.confidence_ece:.3f} |
| Pass@1 | {result.metrics.accuracy.pass_at_1:.1%} |

## Latency Metrics

| Metric | Value |
|--------|-------|
| Mean | {result.metrics.latency.mean_ms:.0f}ms |
| Median | {result.metrics.latency.median_ms:.0f}ms |
| P95 | {result.metrics.latency.p95_ms:.0f}ms |
| P99 | {result.metrics.latency.p99_ms:.0f}ms |
| Within SLO | {result.metrics.latency.within_slo_rate:.1%} |

## Cost Metrics

| Metric | Value |
|--------|-------|
| Total Tokens | {result.metrics.cost.total_tokens:,} |
| Total Cost | {_usd(result.metrics.cost.total_cost_usd, 2)} |
| Mean Cost/Investigation | {_usd(result.metrics.cost.mean_cost_per_investigation_usd)} |

"""

        if result.failure_reasons:
            md += "## Failure Reasons\n\n"
            for reason in result.failure_reasons:
                md += f"- ❌ {reason}\n"
            md += "\n"

        # Regressions
        regressions = self.detect_regressions(result)
        if regressions:
            md += "## Regressions Detected\n\n"
            for r in regressions:
                emoji = "🔴" if r.severity == "critical" else "🟡"
                md += f"- {emoji} **{r.metric_name}**: {r.previous_value:.3f} → {r.current_value:.3f} ({r.change_percent:+.1f}%)\n"
            md += "\n"

        # Failed scenarios
        failed = [s for s in result.scenario_results if not s.root_cause_match]
        if failed:
            md += "## Failed Scenarios\n\n"
            for s in failed[:10]:  # Top 10
                md += f"### {s.scenario_name} ({s.scenario_id})\n\n"
                md += f"- **Expected**: {s.root_cause_expected}\n"
                md += f"- **Predicted**: {s.root_cause_predicted or '(empty)'}\n"
                md += f"- **Confidence**: {s.confidence:.1%}\n"
                if s.error:
                    md += f"- **Error**: {s.error}\n"
                md += "\n"

        return md

    def _generate_html(self, result: EvalResult) -> str:
        """Generate HTML report."""
        status_class = "success" if result.overall_passed else "failure"
        status_text = "PASSED" if result.overall_passed else "FAILED"

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Eval Report: {result.eval_id}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #333; }}
        .status {{ padding: 10px 20px; border-radius: 8px; display: inline-block; font-weight: bold; }}
        .success {{ background: #d4edda; color: #155724; }}
        .failure {{ background: #f8d7da; color: #721c24; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background: #f5f5f5; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .metric-card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin: 10px; display: inline-block; min-width: 200px; }}
        .metric-value {{ font-size: 2em; font-weight: bold; color: #333; }}
        .metric-label {{ color: #666; }}
    </style>
</head>
<body>
    <h1>Evaluation Report: {result.eval_id}</h1>
    <div class="status {status_class}">{status_text}</div>

    <h2>Summary</h2>
    <div>
        <div class="metric-card">
            <div class="metric-value">{result.scenarios_passed}/{result.scenarios_total}</div>
            <div class="metric-label">Scenarios Passed</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{result.metrics.accuracy.exact_match_rate:.0%}</div>
            <div class="metric-label">Accuracy</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{result.metrics.latency.mean_ms:.0f}ms</div>
            <div class="metric-label">Mean Latency</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{_usd(result.metrics.cost.mean_cost_per_investigation_usd)}</div>
            <div class="metric-label">Cost/Investigation</div>
        </div>
    </div>

    <h2>Accuracy Metrics</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Exact Match Rate</td><td>{result.metrics.accuracy.exact_match_rate:.1%}</td></tr>
        <tr><td>Severity Accuracy</td><td>{result.metrics.accuracy.severity_accuracy:.1%}</td></tr>
        <tr><td>Service F1</td><td>{result.metrics.accuracy.service_f1:.2f}</td></tr>
        <tr><td>Confidence ECE</td><td>{result.metrics.accuracy.confidence_ece:.3f}</td></tr>
    </table>

    <h2>Latency Metrics</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Mean</td><td>{result.metrics.latency.mean_ms:.0f}ms</td></tr>
        <tr><td>P95</td><td>{result.metrics.latency.p95_ms:.0f}ms</td></tr>
        <tr><td>P99</td><td>{result.metrics.latency.p99_ms:.0f}ms</td></tr>
        <tr><td>Within SLO</td><td>{result.metrics.latency.within_slo_rate:.1%}</td></tr>
    </table>

    <h2>Scenario Results</h2>
    <table>
        <tr><th>Scenario</th><th>Result</th><th>Confidence</th><th>Duration</th></tr>
"""

        for s in result.scenario_results:
            status = "✅" if s.root_cause_match else "❌"
            html += f"        <tr><td>{s.scenario_name}</td><td>{status}</td><td>{s.confidence:.0%}</td><td>{s.duration_ms:.0f}ms</td></tr>\n"

        html += """    </table>
</body>
</html>"""

        return html

    def _generate_slack(self, result: EvalResult) -> str:
        """Generate Slack Block Kit message."""
        status_emoji = ":white_check_mark:" if result.overall_passed else ":x:"
        status_text = "PASSED" if result.overall_passed else "FAILED"

        blocks = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Nightly Eval: {status_emoji} {status_text}",
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Eval ID:*\n{result.eval_id}"},
                        {"type": "mrkdwn", "text": f"*Duration:*\n{(result.completed_at - result.started_at).total_seconds():.0f}s"},
                        {"type": "mrkdwn", "text": f"*Scenarios:*\n{result.scenarios_passed}/{result.scenarios_total} passed"},
                        {"type": "mrkdwn", "text": f"*Accuracy:*\n{result.metrics.accuracy.exact_match_rate:.0%}"},
                        {"type": "mrkdwn", "text": f"*Mean Latency:*\n{result.metrics.latency.mean_ms:.0f}ms"},
                        {"type": "mrkdwn", "text": f"*Mean Cost:*\n{_usd(result.metrics.cost.mean_cost_per_investigation_usd)}"},
                    ]
                },
            ]
        }

        # Add failure reasons
        if result.failure_reasons:
            blocks["blocks"].append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Failure Reasons:*\n" + "\n".join(f"• {r}" for r in result.failure_reasons),
                }
            })

        # Add regressions
        regressions = self.detect_regressions(result)
        if regressions:
            regression_text = "\n".join(
                f"• *{r.metric_name}*: {r.previous_value:.3f} → {r.current_value:.3f} ({r.change_percent:+.1f}%)"
                for r in regressions
            )
            blocks["blocks"].append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":warning: *Regressions Detected:*\n{regression_text}",
                }
            })

        return json.dumps(blocks, indent=2)
