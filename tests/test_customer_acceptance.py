from pathlib import Path

from scripts.customer_acceptance import REQUIRED_GATES, build_report


def test_customer_acceptance_fails_closed_without_external_artifacts(monkeypatch):
    for variable in REQUIRED_GATES.values():
        monkeypatch.delenv(variable, raising=False)
    report = build_report()
    assert report["ready_to_run"] is False
    assert all(not gate["available"] for gate in report["required_gates"].values())


def test_customer_acceptance_accepts_only_existing_files(monkeypatch, tmp_path: Path):
    evidence = tmp_path / "connector.json"
    evidence.write_text("independently captured\n", encoding="utf-8")
    first = next(iter(REQUIRED_GATES.values()))
    monkeypatch.setenv(first, str(evidence))
    report = build_report()
    assert report["required_gates"][next(iter(REQUIRED_GATES))]["available"] is True
    assert report["ready_to_run"] is False
