"""Proof-carrying execution receipt."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from app.contracts.action import ActionContract
from app.contracts.common import DomainContract, require_aware_datetime, utc_now
from app.contracts.trust import TrustDecision
from app.contracts.verification import ExecutionResult, VerificationResult


class ExecutionReceipt(DomainContract):
    receipt_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = Field(min_length=1, max_length=256)
    action_contract: ActionContract
    trust_decision: TrustDecision
    execution_result: ExecutionResult
    verification_result: VerificationResult
    created_at: datetime = Field(default_factory=utc_now)
    audit_reference: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("created_at")
    @classmethod
    def receipt_time_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)

    @field_validator("trust_decision")
    @classmethod
    def trust_binds_to_action(cls, value: TrustDecision, info: Any) -> TrustDecision:
        contract = info.data.get("action_contract")
        if contract is not None and (value.action_id != contract.action_id or value.tenant_id != contract.tenant_id):
            raise ValueError("trust decision must bind to the receipt action and tenant")
        return value

    @field_validator("execution_result")
    @classmethod
    def execution_binds_to_action(cls, value: ExecutionResult, info: Any) -> ExecutionResult:
        contract = info.data.get("action_contract")
        if contract is not None and value.action_id != contract.action_id:
            raise ValueError("execution result must bind to the receipt action")
        return value

    @field_validator("verification_result")
    @classmethod
    def verification_binds_to_action(cls, value: VerificationResult, info: Any) -> VerificationResult:
        contract = info.data.get("action_contract")
        if contract is not None and value.action_id != contract.action_id:
            raise ValueError("verification result must bind to the receipt action")
        return value
