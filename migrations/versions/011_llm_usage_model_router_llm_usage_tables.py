"""model router llm usage tables

`CostOptimizer` kept the spend ledger in `self._usage_history` (a list) and the
budgets in `self._customer_budgets` (a dict). Both were process memory: a
restart zeroed the bill, a second worker disagreed with the first about what
had been spent, and a budget set through the API bound only the process that
happened to serve that request. /usage/daily, /usage/monthly, /stats and
/budget/check all read those structures, so the cost dashboard reported on
whatever the current process had seen since it booted.

`llm_usage_records` is not the existing `usage_records` table: that one is the
per-org billing meter (period / investigations / tokens_used) and has neither a
model id nor a cost column, so it cannot say what was spent on which model.

Revision ID: 011_llm_usage
Revises: 010_graph
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '011_llm_usage'
down_revision: Union[str, None] = '010_graph'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('llm_customer_budgets',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('customer_id', sa.String(length=128), nullable=False),
    sa.Column('daily_limit', sa.Float(), nullable=True),
    sa.Column('monthly_limit', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_llm_budget_org_customer', 'llm_customer_budgets', ['org_id', 'customer_id'], unique=True)
    op.create_table('llm_usage_records',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('customer_id', sa.String(length=128), nullable=False),
    sa.Column('model_id', sa.String(length=128), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost', sa.Float(), nullable=False),
    sa.Column('request_type', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_llm_usage_org_created', 'llm_usage_records', ['org_id', 'created_at'], unique=False)
    op.create_index('ix_llm_usage_org_customer_created', 'llm_usage_records', ['org_id', 'customer_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_llm_usage_org_customer_created', table_name='llm_usage_records')
    op.drop_index('ix_llm_usage_org_created', table_name='llm_usage_records')
    op.drop_table('llm_usage_records')
    op.drop_index('ix_llm_budget_org_customer', table_name='llm_customer_budgets')
    op.drop_table('llm_customer_budgets')
