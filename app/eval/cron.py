"""Nightly Eval Cron - Scheduled evaluation automation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, time as dt_time
from pathlib import Path
from typing import Any

import httpx

from app.eval.pipeline import EvalPipeline, EvalConfig, EvalResult
from app.eval.reporter import EvalReporter, ReportFormat

log = logging.getLogger("omnipotent.eval.cron")


@dataclass
class CronConfig:
    """Configuration for the nightly cron job."""
    # Schedule
    run_hour_utc: int = 3  # 3 AM UTC
    run_minute: int = 0
    run_days: list[int] | None = None  # None = every day, [0,1,2,3,4] = weekdays

    # Evaluation
    eval_config: EvalConfig | None = None

    # Reporting
    baseline_path: Path = Path("eval_results/baseline.json")
    update_baseline_on_pass: bool = True

    # Notifications
    slack_webhook_url: str = ""
    pagerduty_routing_key: str = ""
    alert_on_failure: bool = True
    alert_on_regression: bool = True

    # GitHub
    github_token: str = ""
    github_repo: str = ""
    create_issue_on_failure: bool = True


class NightlyEvalCron:
    """Nightly evaluation cron job.

    Runs evaluations on a schedule:
    - Loads scenarios and runs the evaluation pipeline
    - Compares against baseline for regression detection
    - Generates reports in multiple formats
    - Sends alerts via Slack/PagerDuty on failures
    - Creates GitHub issues for regressions
    - Updates baseline on successful runs
    """

    def __init__(self, config: CronConfig | None = None):
        """Initialize the cron job.

        Args:
            config: Cron configuration
        """
        self.config = config or CronConfig()

        # Load from environment if not configured
        if not self.config.slack_webhook_url:
            self.config.slack_webhook_url = os.getenv("EVAL_SLACK_WEBHOOK", "")
        if not self.config.pagerduty_routing_key:
            self.config.pagerduty_routing_key = os.getenv("EVAL_PAGERDUTY_KEY", "")
        if not self.config.github_token:
            self.config.github_token = os.getenv("GITHUB_TOKEN", "")
        if not self.config.github_repo:
            self.config.github_repo = os.getenv("GITHUB_REPOSITORY", "")

        self._running = False
        self._last_run: datetime | None = None
        self._last_result: EvalResult | None = None

    async def run_once(self) -> EvalResult:
        """Run the evaluation once (for manual triggering or CI)."""
        log.info("Starting nightly evaluation")

        # Initialize pipeline
        eval_config = self.config.eval_config or EvalConfig()
        pipeline = EvalPipeline(config=eval_config)

        # Initialize reporter
        reporter = EvalReporter(
            baseline_path=self.config.baseline_path,
            alert_on_regression=self.config.alert_on_regression,
        )

        # Run evaluation
        result = await pipeline.run()
        self._last_result = result
        self._last_run = datetime.now(timezone.utc)

        # Generate reports
        await self._generate_reports(result, reporter)

        # Handle results
        if not result.overall_passed:
            await self._handle_failure(result, reporter)
        else:
            await self._handle_success(result, reporter)

        return result

    async def start(self) -> None:
        """Start the cron scheduler."""
        self._running = True
        log.info(
            "Nightly eval cron started: runs at %02d:%02d UTC",
            self.config.run_hour_utc,
            self.config.run_minute,
        )

        while self._running:
            try:
                await self._wait_for_next_run()
                if self._should_run_today():
                    await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception("Cron job failed: %s", e)
                await asyncio.sleep(3600)  # Wait an hour on error

    async def stop(self) -> None:
        """Stop the cron scheduler."""
        self._running = False
        log.info("Nightly eval cron stopped")

    async def _wait_for_next_run(self) -> None:
        """Wait until the next scheduled run time."""
        now = datetime.now(timezone.utc)
        target_time = dt_time(self.config.run_hour_utc, self.config.run_minute)

        # Calculate next run
        next_run = datetime.combine(now.date(), target_time, tzinfo=timezone.utc)
        if next_run <= now:
            # Already passed today, run tomorrow
            next_run = next_run.replace(day=now.day + 1)

        wait_seconds = (next_run - now).total_seconds()
        log.info("Next eval scheduled in %.0f seconds", wait_seconds)

        await asyncio.sleep(wait_seconds)

    def _should_run_today(self) -> bool:
        """Check if we should run today based on configured days."""
        if self.config.run_days is None:
            return True
        today = datetime.now(timezone.utc).weekday()
        return today in self.config.run_days

    async def _generate_reports(
        self,
        result: EvalResult,
        reporter: EvalReporter,
    ) -> None:
        """Generate and save reports."""
        output_dir = result.config.output_dir

        # Markdown report
        md_report = reporter.generate_report(result, ReportFormat.MARKDOWN)
        md_path = output_dir / f"{result.eval_id}_report.md"
        md_path.write_text(md_report)

        # HTML report
        html_report = reporter.generate_report(result, ReportFormat.HTML)
        html_path = output_dir / f"{result.eval_id}_report.html"
        html_path.write_text(html_report)

        log.info("Reports saved to %s", output_dir)

    async def _handle_failure(
        self,
        result: EvalResult,
        reporter: EvalReporter,
    ) -> None:
        """Handle evaluation failure."""
        log.warning("Evaluation FAILED: %s", result.failure_reasons)

        # Detect regressions
        regressions = reporter.detect_regressions(result)

        # Send alerts
        if self.config.alert_on_failure:
            await self._send_slack_alert(result, reporter)

            if regressions and self.config.alert_on_regression:
                await self._send_pagerduty_alert(result, regressions)

        # Create GitHub issue
        if self.config.create_issue_on_failure:
            await self._create_github_issue(result, regressions)

    async def _handle_success(
        self,
        result: EvalResult,
        reporter: EvalReporter,
    ) -> None:
        """Handle evaluation success."""
        log.info("Evaluation PASSED")

        # Update baseline
        if self.config.update_baseline_on_pass:
            reporter.save_as_baseline(result, self.config.baseline_path)

        # Send success notification
        if self.config.slack_webhook_url:
            await self._send_slack_alert(result, reporter)

    async def _send_slack_alert(
        self,
        result: EvalResult,
        reporter: EvalReporter,
    ) -> None:
        """Send Slack notification."""
        if not self.config.slack_webhook_url:
            return

        try:
            payload = json.loads(reporter.generate_report(result, ReportFormat.SLACK))

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.slack_webhook_url,
                    json=payload,
                    timeout=30,
                )
                response.raise_for_status()

            log.info("Slack alert sent")

        except Exception as e:
            log.error("Failed to send Slack alert: %s", e)

    async def _send_pagerduty_alert(
        self,
        result: EvalResult,
        regressions: list,
    ) -> None:
        """Send PagerDuty alert for critical regressions."""
        if not self.config.pagerduty_routing_key:
            return

        try:
            payload = {
                "routing_key": self.config.pagerduty_routing_key,
                "event_action": "trigger",
                "payload": {
                    "summary": f"Agent Eval Regression: {len(regressions)} metrics degraded",
                    "severity": "warning",
                    "source": "omnipotent-eval",
                    "custom_details": {
                        "eval_id": result.eval_id,
                        "accuracy": result.metrics.accuracy.exact_match_rate,
                        "regressions": [
                            {
                                "metric": r.metric_name,
                                "change": f"{r.change_percent:+.1f}%",
                            }
                            for r in regressions
                        ],
                    },
                },
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload,
                    timeout=30,
                )
                response.raise_for_status()

            log.info("PagerDuty alert sent")

        except Exception as e:
            log.error("Failed to send PagerDuty alert: %s", e)

    async def _create_github_issue(
        self,
        result: EvalResult,
        regressions: list,
    ) -> None:
        """Create GitHub issue for failures."""
        if not self.config.github_token or not self.config.github_repo:
            return

        try:
            title = f"[Eval Failure] {result.eval_id}: {len(result.failure_reasons)} issues"

            body = f"""## Nightly Evaluation Failed

**Eval ID:** {result.eval_id}
**Time:** {result.started_at.strftime('%Y-%m-%d %H:%M UTC')}

### Summary
- Scenarios: {result.scenarios_passed}/{result.scenarios_total} passed
- Accuracy: {result.metrics.accuracy.exact_match_rate:.0%}
- Mean Latency: {result.metrics.latency.mean_ms:.0f}ms

### Failure Reasons
"""
            for reason in result.failure_reasons:
                body += f"- {reason}\n"

            if regressions:
                body += "\n### Regressions\n"
                for r in regressions:
                    body += f"- **{r.metric_name}**: {r.previous_value:.3f} → {r.current_value:.3f} ({r.change_percent:+.1f}%)\n"

            body += "\n---\n*Created automatically by DevOps Copilot eval cron*"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.github.com/repos/{self.config.github_repo}/issues",
                    headers={
                        "Authorization": f"Bearer {self.config.github_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    json={
                        "title": title,
                        "body": body,
                        "labels": ["eval-failure", "automated"],
                    },
                    timeout=30,
                )
                response.raise_for_status()

            log.info("GitHub issue created")

        except Exception as e:
            log.error("Failed to create GitHub issue: %s", e)

    def get_status(self) -> dict[str, Any]:
        """Get cron job status."""
        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_result": {
                "eval_id": self._last_result.eval_id,
                "passed": self._last_result.overall_passed,
                "accuracy": self._last_result.metrics.accuracy.exact_match_rate,
            } if self._last_result else None,
            "next_run_utc": f"{self.config.run_hour_utc:02d}:{self.config.run_minute:02d}",
            "run_days": self.config.run_days,
        }


async def run_nightly_eval(config: CronConfig | None = None) -> EvalResult:
    """Convenience function to run the nightly eval once."""
    cron = NightlyEvalCron(config)
    return await cron.run_once()
