from scripts.benchmark_dedup_restart import run


def test_restart_benchmark_is_auditable_and_does_not_claim_continuity():
    result = run(count=1000, restart_at=500)
    assert result["schema"] == "omnipotent.dedup-restart-benchmark.v1"
    assert result["events"] == 1000
    assert result["restart_at"] == 500
    assert result["workers"] == 2
    assert result["continuity_preserved"] is False
    assert result["post_restart_new_incidents"] > 0
