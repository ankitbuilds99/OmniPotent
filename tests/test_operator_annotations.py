from app.operator_annotations import append_revision, latest_value, verify_history


def test_annotations_are_append_only_and_verifiable():
    history = []
    first = append_revision(history, incident_id="inc-1", field="root_cause", value="deploy", author="alice", created_at=1)
    second = append_revision(history, incident_id="inc-1", field="root_cause", value="config", author="bob", created_at=2)

    assert first.revision == 1
    assert second.previous_hash == first.digest
    assert latest_value(history, "root_cause") == "config"
    assert verify_history(history)


def test_tampering_or_reordering_fails_verification():
    history = []
    append_revision(history, incident_id="inc-1", field="severity", value="high", author="alice", created_at=1)
    append_revision(history, incident_id="inc-1", field="severity", value="low", author="alice", created_at=2)
    history[0] = history[0].__class__(**{**history[0].__dict__, "value": "critical"})
    assert not verify_history(history)
