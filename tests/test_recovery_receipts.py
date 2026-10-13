from app.verification.recovery_receipts import RecoveryState, assess_recovery


def test_accepted_operation_without_fresh_metrics_is_inconclusive():
    result = assess_recovery(
        action_id="a1", provider_accepted=True, provider_operation_id="op1",
        workload_ready=True, fresh_sli_samples=0, stable_window=False,
        user_signal_healthy=None,
    )
    assert result.state is RecoveryState.INCONCLUSIVE


def test_verified_requires_fresh_stable_user_signal():
    result = assess_recovery(
        action_id="a1", provider_accepted=True, provider_operation_id="op1",
        workload_ready=True, fresh_sli_samples=12, stable_window=True,
        user_signal_healthy=True,
    )
    assert result.state is RecoveryState.VERIFIED


def test_unhealthy_signal_fails_even_when_provider_accepted():
    result = assess_recovery(
        action_id="a1", provider_accepted=True, provider_operation_id="op1",
        workload_ready=True, fresh_sli_samples=12, stable_window=True,
        user_signal_healthy=False,
    )
    assert result.state is RecoveryState.FAILED
