from __future__ import annotations

from datetime import datetime, timezone

from app.deduplication.models import AlertSeverity, RawAlert, Decision
from app.deduplication.pipeline import DeduplicationPipeline, PipelineConfig


class MemoryStateStore:
    def __init__(self) -> None:
        self.value = None

    def load(self):
        return self.value

    def save(self, state):
        # Simulate serialization across a worker boundary.
        import json
        self.value = json.loads(json.dumps(state))


def _config() -> PipelineConfig:
    return PipelineConfig(
        enable_layer2_semantic=False,
        enable_layer3_temporal=False,
        enable_layer4_topology=False,
        enable_layer5_ml=False,
    )


def test_shared_snapshot_preserves_fingerprint_dedup_across_worker_restart():
    store = MemoryStateStore()
    first_alert = RawAlert(
        alert_name="http_5xx_rate",
        service="payments",
        severity=AlertSeverity.HIGH,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    first = DeduplicationPipeline(_config(), state_store=store)
    created = first.process_alert(first_alert)
    assert created.decision is Decision.NEW_INCIDENT

    restarted = DeduplicationPipeline(_config(), state_store=store)
    second = restarted.process_alert(
        RawAlert(
            alert_name="http_5xx_rate",
            service="payments",
            severity=AlertSeverity.HIGH,
            timestamp=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )
    )

    assert second.decision is Decision.ADD_TO_EXISTING
    assert second.incident_id == created.incident_id
    assert len(restarted.get_active_incidents()) == 1


def test_required_shared_state_fails_closed_without_store(monkeypatch):
    monkeypatch.delenv("COPILOT_DEDUP_STATE_URL", raising=False)
    config = PipelineConfig(require_shared_state=True)
    try:
        DeduplicationPipeline(config)
    except RuntimeError as exc:
        assert "shared deduplication state is required" in str(exc)
    else:
        raise AssertionError("required shared deduplication state was silently disabled")

