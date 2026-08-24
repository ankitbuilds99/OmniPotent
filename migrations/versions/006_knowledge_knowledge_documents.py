"""knowledge documents

The knowledge base kept every document in `KnowledgeBase._documents`, a dict on
a per-org object in process memory: runbook write-ups, postmortems and
architecture notes all disappeared on restart, and with more than one worker a
document existed only for the process that happened to answer.

One table, written by app/knowledge_base/store.py. `metadata` is the column
name; the ORM attribute is `meta`, because `metadata` is taken on a declarative
class. There is deliberately no embedding column — see `KnowledgeDocument`.

Revision ID: 006_knowledge
Revises: 005_billing
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

# revision identifiers, used by Alembic.
revision: str = '006_knowledge'
down_revision: Union[str, None] = '005_billing'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('knowledge_documents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('doc_id', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('doc_type', sa.String(length=64), nullable=False),
    sa.Column('tags', JSONB, nullable=False),
    sa.Column('service', sa.String(length=128), nullable=True),
    sa.Column('author', sa.String(length=256), nullable=False),
    sa.Column('metadata', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_knowledge_documents_org_doc', 'knowledge_documents', ['org_id', 'doc_id'], unique=True)
    op.create_index('ix_knowledge_documents_org_updated', 'knowledge_documents', ['org_id', 'updated_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_knowledge_documents_org_updated', table_name='knowledge_documents')
    op.drop_index('ix_knowledge_documents_org_doc', table_name='knowledge_documents')
    op.drop_table('knowledge_documents')
