"""Deterministic 1,000-event deduplication/restart benchmark.

This benchmark intentionally reports restart continuity instead of assuming it.
Without ``COPILOT_DEDUP_STATE_URL`` it measures the process-local failure mode;
with a configured shared store, use the companion shared-state test/production
run to verify continuity against the actual Redis deployment.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.deduplication.models import AlertSeverity, RawAlert
from app.deduplication.pipeline import DeduplicationPipeline, PipelineConfig


def _config() -> PipelineConfig:
    return PipelineConfig(
        enable_layer2_semantic=False, enable_layer3_temporal=False,
        enable_layer4_topology=False, enable_layer5_ml=False,
    )


def _alerts(count: int) -> list[RawAlert]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [RawAlert(
        alert_name="http_5xx_rate", service=f"service-{index % 20}",
        environment="production", message="elevated 5xx", severity=AlertSeverity.HIGH,
        timestamp=start + timedelta(seconds=index),
    ) for index in range(count)]


def run(count: int = 1000, restart_at: int = 500) -> dict:
    if count < 1 or not 0 < restart_at < count:
        raise ValueError("restart_at must be between 1 and count - 1")
    alerts = _alerts(count)
    timings: list[float] = []
    first = DeduplicationPipeline(_config())
    for alert in alerts[:restart_at]:
        started = time.perf_counter()
        first.process_alert(alert)
        timings.append((time.perf_counter() - started) * 1000)

    # A new process/worker has no access to the first pipeline's in-memory state.
    second = DeduplicationPipeline(_config())
    post_restart_decisions = []
    for alert in alerts[restart_at:]:
        started = time.perf_counter()
        decision = second.process_alert(alert)
        timings.append((time.perf_counter() - started) * 1000)
        post_restart_decisions.append(decision)

    first_state = first.get_active_incidents()
    second_state = second.get_active_incidents()
    shared_state_configured = bool(first.config.shared_state_url.strip())
    continuity_preserved = shared_state_configured and not any(
        d.decision.value == "new_incident" for d in post_restart_decisions
    )
    return {
        "schema": "omnipotent.dedup-restart-benchmark.v1",
        "events": count,
        "restart_at": restart_at,
        "workers": 2,
        "elapsed_ms": round(sum(timings), 3),
        "p50_ms": round(statistics.median(timings), 3),
        "p95_ms": round(sorted(timings)[int(len(timings) * 0.95) - 1], 3),
        "pre_restart_incidents": len(first_state),
        "post_restart_incidents": len(second_state),
        "post_restart_new_incidents": sum(1 for d in post_restart_decisions if d.decision.value == "new_incident"),
        "shared_state_configured": shared_state_configured,
        "continuity_preserved": continuity_preserved,
        "continuity_reason": (
            "shared state restored the active fingerprint index"
            if continuity_preserved
            else "pipeline state is process-local or the shared snapshot did not cover the restart"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--restart-at", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.events, args.restart_at)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
