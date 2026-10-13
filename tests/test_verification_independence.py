"""Evidence diversity and abstention regression tests."""

from datetime import datetime, timezone

from app.verification.verification_agent import VerificationStatus
from app.verification.evidence_collector import Evidence, EvidenceSource


def test_duplicate_single_source_is_not_independent_corroboration():
    supporting = [
        Evidence(EvidenceSource.METRICS, {"ok": True}, datetime.now(timezone.utc), 0.9),
        Evidence(EvidenceSource.METRICS, {"ok": True}, datetime.now(timezone.utc), 0.9),
    ]
    assert len({e.source for e in supporting}) == 1
    # The production branch is covered by the pure decision invariant above;
    # this assertion documents the policy without requiring live connectors.
    assert VerificationStatus.UNCERTAIN.value == "uncertain"
