"""billing tables

app/billing/ shipped without a single table: subscriptions, invoices, saved
cards, usage counters and feature overrides were dicts on an
`InMemoryBillingStore` singleton. A restart cancelled every plan, and with more
than one worker an org's plan depended on which process answered the request.

Seven tables, one per entity, written by app/billing/db_store.py. `metadata` is
the column name in each of them; the ORM attribute is `meta`, because
`metadata` is taken on a declarative class.

Revision ID: 005_billing
Revises: 004_runbooks
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

# revision identifiers, used by Alembic.
revision: str = '005_billing'
down_revision: Union[str, None] = '004_runbooks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('billing_feature_overrides',
    sa.Column('override_id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('feature_key', sa.String(length=128), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('limit_value', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('override_id')
    )
    op.create_index('ix_billing_override_org_key', 'billing_feature_overrides', ['org_id', 'feature_key'], unique=True)
    op.create_table('billing_invoices',
    sa.Column('invoice_id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('subscription_id', sa.Uuid(), nullable=True),
    sa.Column('stripe_invoice_id', sa.String(length=128), nullable=False),
    sa.Column('invoice_number', sa.String(length=64), nullable=False),
    sa.Column('amount_cents', sa.Integer(), nullable=False),
    sa.Column('amount_paid_cents', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('invoice_pdf_url', sa.Text(), nullable=False),
    sa.Column('hosted_invoice_url', sa.Text(), nullable=False),
    sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('line_items', JSONB, nullable=False),
    sa.Column('metadata', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('invoice_id')
    )
    op.create_index('ix_billing_invoices_org_created', 'billing_invoices', ['org_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_billing_invoices_stripe_invoice_id'), 'billing_invoices', ['stripe_invoice_id'], unique=False)
    op.create_table('billing_organizations',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('slug', sa.String(length=128), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=True),
    sa.Column('stripe_customer_id', sa.String(length=128), nullable=False),
    sa.Column('billing_email', sa.String(length=320), nullable=False),
    sa.Column('tax_id', sa.String(length=64), nullable=False),
    sa.Column('billing_address', JSONB, nullable=False),
    sa.Column('metadata', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id')
    )
    op.create_index(op.f('ix_billing_organizations_stripe_customer_id'), 'billing_organizations', ['stripe_customer_id'], unique=False)
    op.create_table('billing_payment_methods',
    sa.Column('payment_method_id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('stripe_payment_method_id', sa.String(length=128), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('card_brand', sa.String(length=32), nullable=False),
    sa.Column('card_last4', sa.String(length=4), nullable=False),
    sa.Column('card_exp_month', sa.Integer(), nullable=False),
    sa.Column('card_exp_year', sa.Integer(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('billing_details', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('payment_method_id')
    )
    op.create_index(op.f('ix_billing_payment_methods_org_id'), 'billing_payment_methods', ['org_id'], unique=False)
    op.create_table('billing_subscriptions',
    sa.Column('subscription_id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('billing_cycle', sa.String(length=16), nullable=False),
    sa.Column('stripe_subscription_id', sa.String(length=128), nullable=False),
    sa.Column('stripe_customer_id', sa.String(length=128), nullable=False),
    sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('trial_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('trial_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False),
    sa.Column('cancel_reason', sa.Text(), nullable=False),
    sa.Column('pause_collection', JSONB, nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('metadata', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('subscription_id'),
    sa.UniqueConstraint('org_id')
    )
    op.create_index(op.f('ix_billing_subscriptions_stripe_subscription_id'), 'billing_subscriptions', ['stripe_subscription_id'], unique=False)
    op.create_table('billing_usage_records',
    sa.Column('record_id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('metric_type', sa.String(length=64), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('metadata', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('record_id')
    )
    op.create_index('ix_billing_usage_org_period', 'billing_usage_records', ['org_id', 'period_start'], unique=False)
    op.create_table('billing_usage_summaries',
    sa.Column('summary_id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('year_month', sa.String(length=7), nullable=False),
    sa.Column('ai_investigations_count', sa.Integer(), nullable=False),
    sa.Column('alerts_processed_count', sa.Integer(), nullable=False),
    sa.Column('api_calls_count', sa.Integer(), nullable=False),
    sa.Column('postmortems_generated_count', sa.Integer(), nullable=False),
    sa.Column('auto_fix_prs_count', sa.Integer(), nullable=False),
    sa.Column('similar_searches_count', sa.Integer(), nullable=False),
    sa.Column('active_users_count', sa.Integer(), nullable=False),
    sa.Column('storage_bytes_used', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('summary_id')
    )
    op.create_index('ix_billing_summary_org_month', 'billing_usage_summaries', ['org_id', 'year_month'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_billing_summary_org_month', table_name='billing_usage_summaries')
    op.drop_table('billing_usage_summaries')
    op.drop_index('ix_billing_usage_org_period', table_name='billing_usage_records')
    op.drop_table('billing_usage_records')
    op.drop_index(op.f('ix_billing_subscriptions_stripe_subscription_id'), table_name='billing_subscriptions')
    op.drop_table('billing_subscriptions')
    op.drop_index(op.f('ix_billing_payment_methods_org_id'), table_name='billing_payment_methods')
    op.drop_table('billing_payment_methods')
    op.drop_index(op.f('ix_billing_organizations_stripe_customer_id'), table_name='billing_organizations')
    op.drop_table('billing_organizations')
    op.drop_index(op.f('ix_billing_invoices_stripe_invoice_id'), table_name='billing_invoices')
    op.drop_index('ix_billing_invoices_org_created', table_name='billing_invoices')
    op.drop_table('billing_invoices')
    op.drop_index('ix_billing_override_org_key', table_name='billing_feature_overrides')
    op.drop_table('billing_feature_overrides')
