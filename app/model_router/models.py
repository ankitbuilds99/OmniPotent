"""Persistent storage for LLM spend and per-customer budgets.

`CostOptimizer` kept both of these in process memory — `self._usage_history`
is a list and `self._customer_budgets` a dict — so every restart wiped the
ledger, a second worker disagreed with the first about what had been spent,
and a budget set through the API applied only to whichever process happened to
serve that request. A spend tracker that forgets is not a spend tracker.

These tables are the storage those two structures should always have had. They
are declared here rather than in `app/database/models.py` for the same reason
`app/oncall/models.py` exists: they belong to the feature, and both `init_db()`
and `migrations/env.py` import this module so the tables land on the shared
`Base.metadata` either way.

`llm_usage_records` is deliberately NOT the `usage_records` table in
`app/database/models.py`: that one is the per-org billing meter (period,
investigations, tokens_used) and carries neither a model id nor a cost, so it
cannot answer "what did we spend on which model".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base
from app.database.types import UUID

# Usage that is not attributed to a sub-customer belongs to the org itself.
# A sentinel rather than NULL keeps the (org_id, customer_id) index usable for
# both cases — NULL does not compare equal to NULL in SQL.
DEFAULT_CUSTOMER = "-"


class LLMUsageRecord(Base):
    """One priced LLM call.

    Written by `app/model_router/usage_ledger.py` for every model call the
    graph makes, and by POST /api/model-router/record-usage for callers that
    meter their own traffic. Read by /usage/daily, /usage/monthly, /stats and
    the budget check.
    """

    __tablename__ = "llm_usage_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The org's own label for whoever the call was made on behalf of. Scoped
    # inside org_id, which comes from the authenticated request and never from
    # a request body.
    customer_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default=DEFAULT_CUSTOMER
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Dollars, priced by CostOptimizer at the time of the call: model list
    # prices change, and a re-priced history would silently rewrite old bills.
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # "inference" for API-reported usage, or the graph node that made the call
    # ("plan", "agent", "reflect", "report", "verify", ...).
    request_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="inference"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_llm_usage_org_created", "org_id", "created_at"),
        Index(
            "ix_llm_usage_org_customer_created", "org_id", "customer_id", "created_at"
        ),
    )


class CustomerBudget(Base):
    """A spend cap for one customer of one org.

    Both limits are nullable and independently settable: `check_budget`
    reported `daily_remaining: null` and `monthly_remaining: null` forever
    because the only way to set a daily cap was a constructor argument no
    endpoint passed.
    """

    __tablename__ = "llm_customer_budgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default=DEFAULT_CUSTOMER
    )
    daily_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_llm_budget_org_customer", "org_id", "customer_id", unique=True
        ),
    )
