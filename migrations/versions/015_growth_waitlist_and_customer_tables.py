"""waitlist entries, customers, trials and customer usage records

`app/waitlist/manager.py` and `app/customers/manager.py` each kept everything
they held in module-level dicts. Every early-access signup — with the position
and referral code the endpoint had already promised the visitor — and every
customer, trial and metered usage event was lost on restart, on deploy, and
between worker processes. Nine API routes sat on top of them.

`waitlist_entries` is deliberately global: a visitor joining a waitlist has no
account yet, so there is no org to scope them to. `position` is unique so two
concurrent signups cannot both be told they are #42.

`customers`, `trials` and `customer_usage_records` carry `org_id`. The dicts
were global, so on a multi-tenant deployment every tenant's
`GET /customers/dashboard` returned every other tenant's customers, contacts,
health scores and revenue.

Revision ID: 015_growth
Revises: 014_auth
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# JSONB on Postgres, JSON on SQLite.
from app.database.types import JSONB


# revision identifiers, used by Alembic.
revision: str = '015_growth'
down_revision: Union[str, None] = '014_auth'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('waitlist_entries',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('tier', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('company', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=120), nullable=False),
    sa.Column('team_size', sa.String(length=64), nullable=False),
    sa.Column('use_case', sa.Text(), nullable=False),
    sa.Column('referral_code', sa.String(length=32), nullable=False),
    sa.Column('referred_by', sa.String(length=320), nullable=False),
    sa.Column('referral_count', sa.Integer(), nullable=False),
    sa.Column('utm_source', sa.String(length=120), nullable=False),
    sa.Column('utm_medium', sa.String(length=120), nullable=False),
    sa.Column('utm_campaign', sa.String(length=120), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=200), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('tags', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('invited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email', name='uq_waitlist_email'),
    sa.UniqueConstraint('position', name='uq_waitlist_position')
    )
    op.create_index('ix_waitlist_referral_code', 'waitlist_entries', ['referral_code'], unique=False)

    op.create_table('customers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('company', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=120), nullable=False),
    sa.Column('team_size', sa.String(length=64), nullable=False),
    sa.Column('industry', sa.String(length=120), nullable=False),
    sa.Column('use_case', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('tier', sa.String(length=16), nullable=False),
    sa.Column('mrr_cents', sa.Integer(), nullable=False),
    sa.Column('lifetime_value_cents', sa.Integer(), nullable=False),
    sa.Column('total_investigations', sa.Integer(), nullable=False),
    sa.Column('total_tokens_used', sa.Integer(), nullable=False),
    sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('trial_id', sa.String(length=32), nullable=False),
    sa.Column('stripe_customer_id', sa.String(length=64), nullable=False),
    sa.Column('onboarding_completed', sa.Boolean(), nullable=False),
    sa.Column('onboarding_step', sa.Integer(), nullable=False),
    sa.Column('health_score', sa.Float(), nullable=False),
    sa.Column('nps_score', sa.Integer(), nullable=True),
    sa.Column('feedback', sa.Text(), nullable=False),
    sa.Column('tags', JSONB, nullable=False),
    sa.Column('meta', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'email', name='uq_customer_org_email')
    )
    op.create_index('ix_customers_org_created', 'customers', ['org_id', 'created_at'], unique=False)

    op.create_table('trials',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('customer_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('investigations_allowed', sa.Integer(), nullable=False),
    sa.Column('investigations_used', sa.Integer(), nullable=False),
    sa.Column('tokens_allowed', sa.Integer(), nullable=False),
    sa.Column('tokens_used', sa.Integer(), nullable=False),
    sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_trials_org_status_expires', 'trials', ['org_id', 'status', 'expires_at'], unique=False
    )

    op.create_table('customer_usage_records',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('customer_id', sa.String(length=32), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('investigation_id', sa.String(length=128), nullable=False),
    sa.Column('tokens_used', sa.Integer(), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=False),
    sa.Column('meta', JSONB, nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_customer_usage_customer_time',
        'customer_usage_records',
        ['customer_id', 'recorded_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_customer_usage_customer_time', table_name='customer_usage_records')
    op.drop_table('customer_usage_records')
    op.drop_index('ix_trials_org_status_expires', table_name='trials')
    op.drop_table('trials')
    op.drop_index('ix_customers_org_created', table_name='customers')
    op.drop_table('customers')
    op.drop_index('ix_waitlist_referral_code', table_name='waitlist_entries')
    op.drop_table('waitlist_entries')
