"""An unknown LLM price must never read as "cheap".

The pricing tables this replaced fell back to an invented rate for any model
they didn't recognise. Removing the invention created a second, subtler risk:
if "no price" becomes 0.0, every downstream cost check sees a free run and
passes. These tests pin the direction — unknown propagates as None, and the two
gates that consume it refuse to pass rather than pass quietly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.eval.metrics import CostMetrics, EvalResultData, MetricsCalculator
from app.eval.pipeline import EvalConfig, EvalMetrics, EvalPipeline, EvalResult
from app.eval.reporter import EvalReporter, _usd


def _row(scenario_id: str, cost: float | None) -> EvalResultData:
    return EvalResultData(
        scenario_id=scenario_id,
        root_cause_predicted="x",
        root_cause_expected="x",
        is_correct=True,
        confidence=0.9,
        severity_predicted="high",
        severity_expected="high",
        services_predicted=["a"],
        services_expected=["a"],
        duration_ms=10.0,
        tokens_used=1000,
        cost_usd=cost,
    )


# --- estimate_cost ---------------------------------------------------------


def test_unpriced_model_estimates_none_not_zero():
    assert MetricsCalculator().estimate_cost(1_000_000, "no-such-model") is None


def test_priced_model_still_estimates_normally():
    # 1M tokens at the default 70/30 split, gpt-4o at $2.50/$10 per 1M.
    cost = MetricsCalculator().estimate_cost(1_000_000, "gpt-4o")
    assert cost == pytest.approx(0.7 * 2.50 + 0.3 * 10.00, abs=0.01)


# --- aggregation -----------------------------------------------------------


def test_all_unpriced_run_reports_no_total_rather_than_zero():
    cost = MetricsCalculator().calculate_cost([_row("s1", None), _row("s2", None)])
    assert cost.total_cost_usd is None
    assert cost.mean_cost_per_investigation_usd is None
    assert cost.unpriced_scenarios == 2
    assert cost.cost_is_complete is False


def test_partially_priced_run_averages_only_the_priced_scenarios():
    # The unpriced scenario must not be averaged in as a zero — that would halve
    # the reported mean and read as a 50% cost improvement.
    cost = MetricsCalculator().calculate_cost([_row("s1", 2.0), _row("s2", None)])
    assert cost.total_cost_usd == pytest.approx(2.0)
    assert cost.mean_cost_per_investigation_usd == pytest.approx(2.0)
    assert cost.unpriced_scenarios == 1
    assert cost.cost_is_complete is False


def test_fully_priced_run_is_complete():
    cost = MetricsCalculator().calculate_cost([_row("s1", 1.0), _row("s2", 3.0)])
    assert cost.total_cost_usd == pytest.approx(4.0)
    assert cost.mean_cost_per_investigation_usd == pytest.approx(2.0)
    assert cost.cost_is_complete is True


# --- the two gates ---------------------------------------------------------


def _result(cost: CostMetrics) -> EvalResult:
    now = datetime.now(timezone.utc)
    calc = MetricsCalculator()
    rows = [_row("s1", 1.0)]
    return EvalResult(
        eval_id="e1",
        started_at=now - timedelta(seconds=1),
        completed_at=now,
        config=EvalConfig(),
        scenarios_total=1,
        scenarios_passed=1,
        scenarios_failed=0,
        metrics=EvalMetrics(
            accuracy=calc.calculate_accuracy(rows),
            latency=calc.calculate_latency(rows),
            cost=cost,
        ),
        scenario_results=[],
        overall_passed=True,
    )


def _reporter_with_baseline(tmp_path: Path, mean_cost: float) -> EvalReporter:
    import json

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"metrics": {"cost": {"mean_cost_per_investigation_usd": mean_cost}}})
    )
    return EvalReporter(baseline_path=baseline)


def test_regression_check_flags_an_incomplete_cost_instead_of_passing(tmp_path):
    """The gate must not stay silent when it cannot see what the run cost.

    An unpriced run's total omits those scenarios, so it can only ever look flat
    or cheaper than the baseline — the comparison would pass no matter the true
    spend.
    """
    reporter = _reporter_with_baseline(tmp_path, mean_cost=1.0)
    alerts = reporter.detect_regressions(
        _result(CostMetrics(total_cost_usd=None, mean_cost_per_investigation_usd=None,
                            unpriced_scenarios=3))
    )
    cost_alerts = [a for a in alerts if a.metric_name == "cost"]
    assert len(cost_alerts) == 1
    assert "not checked" in cost_alerts[0].detail
    assert "3 scenario" in cost_alerts[0].detail


def test_regression_check_still_catches_a_real_increase(tmp_path):
    reporter = _reporter_with_baseline(tmp_path, mean_cost=1.0)
    alerts = reporter.detect_regressions(
        _result(CostMetrics(total_cost_usd=5.0, mean_cost_per_investigation_usd=5.0))
    )
    cost_alerts = [a for a in alerts if a.metric_name == "cost"]
    assert len(cost_alerts) == 1
    assert cost_alerts[0].change_percent == pytest.approx(400.0)
    assert cost_alerts[0].detail == ""


def test_regression_check_is_quiet_when_cost_is_complete_and_flat(tmp_path):
    reporter = _reporter_with_baseline(tmp_path, mean_cost=1.0)
    alerts = reporter.detect_regressions(
        _result(CostMetrics(total_cost_usd=1.0, mean_cost_per_investigation_usd=1.0))
    )
    assert [a for a in alerts if a.metric_name == "cost"] == []


def test_budget_gate_fails_when_the_run_cost_is_unknown():
    """"No cost" must not be read as "under budget"."""
    pipeline = EvalPipeline(EvalConfig(max_cost_per_investigation_usd=1.0))
    calc = MetricsCalculator()
    rows = [_row("s1", 1.0)]
    metrics = EvalMetrics(
        accuracy=calc.calculate_accuracy(rows),
        latency=calc.calculate_latency(rows),
        cost=CostMetrics(total_cost_usd=None, mean_cost_per_investigation_usd=None,
                         unpriced_scenarios=1),
    )
    passed, failures = pipeline._evaluate_thresholds(metrics, [])
    assert passed is False
    assert any("not checked" in f for f in failures)


def test_budget_gate_passes_a_complete_run_under_budget():
    pipeline = EvalPipeline(EvalConfig(max_cost_per_investigation_usd=10.0))
    calc = MetricsCalculator()
    rows = [_row("s1", 1.0)]
    metrics = EvalMetrics(
        accuracy=calc.calculate_accuracy(rows),
        latency=calc.calculate_latency(rows),
        cost=CostMetrics(total_cost_usd=1.0, mean_cost_per_investigation_usd=1.0),
    )
    passed, failures = pipeline._evaluate_thresholds(metrics, [])
    assert not any("cost" in f.lower() for f in failures)


# --- report rendering ------------------------------------------------------


def test_reports_print_unknown_rather_than_a_dollar_zero():
    assert _usd(None) == "unknown"
    assert _usd(None, 2) == "unknown"
    assert _usd(0.0) == "$0.000"
    assert _usd(1.5, 2) == "$1.50"


def test_every_report_format_renders_an_unpriced_run_without_crashing():
    from app.eval.reporter import ReportFormat

    result = _result(CostMetrics(total_cost_usd=None,
                                 mean_cost_per_investigation_usd=None,
                                 unpriced_scenarios=1))
    reporter = EvalReporter()
    for fmt in ReportFormat:
        rendered = reporter.generate_report(result, fmt)
        assert isinstance(rendered, str) and rendered
    # ...and the number the operator sees is not a fabricated zero.
    assert "unknown" in reporter.generate_report(result, ReportFormat.MARKDOWN)
