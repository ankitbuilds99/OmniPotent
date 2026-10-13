"""Fail-closed recovery assessment and typed receipts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecoveryState(StrEnum):
    TRANSPORT_ACCEPTED = "transport_accepted"
    WORKLOAD_READY = "workload_ready"
    VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


@dataclass(frozen=True)
class RecoveryReceipt:
    action_id: str
    state: RecoveryState
    provider_operation_id: str | None
    workload_ready: bool | None
    fresh_sli_samples: int
    stable_window: bool
    reason: str


def assess_recovery(
    *,
    action_id: str,
    provider_accepted: bool,
    provider_operation_id: str | None,
    workload_ready: bool | None,
    fresh_sli_samples: int,
    stable_window: bool,
    user_signal_healthy: bool | None,
) -> RecoveryReceipt:
    """Never report VERIFIED without fresh, sustained user-facing evidence."""
    if not provider_accepted:
        return RecoveryReceipt(action_id, RecoveryState.FAILED, provider_operation_id,
                               workload_ready, fresh_sli_samples, stable_window,
                               "provider did not accept the operation")
    if workload_ready is False or user_signal_healthy is False:
        return RecoveryReceipt(action_id, RecoveryState.FAILED, provider_operation_id,
                               workload_ready, fresh_sli_samples, stable_window,
                               "workload or user-facing signal remains unhealthy")
    if workload_ready is None or user_signal_healthy is None or fresh_sli_samples <= 0 or not stable_window:
        state = RecoveryState.WORKLOAD_READY if workload_ready is True else RecoveryState.TRANSPORT_ACCEPTED
        return RecoveryReceipt(action_id, state if state != RecoveryState.WORKLOAD_READY else RecoveryState.INCONCLUSIVE,
                               provider_operation_id, workload_ready, fresh_sli_samples, stable_window,
                               "fresh stable user-facing recovery evidence is incomplete")
    return RecoveryReceipt(action_id, RecoveryState.VERIFIED, provider_operation_id,
                           workload_ready, fresh_sli_samples, stable_window,
                           "fresh user-facing signal remained healthy for the observation window")
