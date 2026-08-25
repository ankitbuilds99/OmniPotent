"""knowledge graph nodes and edges

`Neo4jKnowledgeGraph.connect()` falls back to `self._in_memory_nodes = {}` when
no Neo4j server is configured, which is the default deployment. Every service,
deployment, incident, change and alert the sync engine discovered lived in that
dict: it vanished on restart, and with more than one worker a blast radius
computed by one process disagreed with the next.

Two generic tables — `label` + `properties` — rather than one per node type,
because that is what the graph is: labelled property bags joined by typed
edges, traversed by code that does not care which type it is walking.

Revision ID: 010_graph
Revises: 009_trigger
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

# revision identifiers, used by Alembic.
revision: str = '010_graph'
down_revision: Union[str, None] = '009_trigger'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('graph_nodes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=64), nullable=False),
    sa.Column('node_id', sa.String(length=256), nullable=False),
    sa.Column('properties', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_graph_nodes_org_label', 'graph_nodes', ['org_id', 'label'], unique=False)
    op.create_index('ix_graph_nodes_org_label_node', 'graph_nodes', ['org_id', 'label', 'node_id'], unique=True)

    op.create_table('graph_edges',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.String(length=256), nullable=False),
    sa.Column('target_id', sa.String(length=256), nullable=False),
    sa.Column('rel_type', sa.String(length=64), nullable=False),
    sa.Column('properties', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_graph_edges_org_target', 'graph_edges', ['org_id', 'target_id'], unique=False)
    op.create_index('ix_graph_edges_org_triple', 'graph_edges', ['org_id', 'source_id', 'target_id', 'rel_type'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_graph_edges_org_triple', table_name='graph_edges')
    op.drop_index('ix_graph_edges_org_target', table_name='graph_edges')
    op.drop_table('graph_edges')
    op.drop_index('ix_graph_nodes_org_label_node', table_name='graph_nodes')
    op.drop_index('ix_graph_nodes_org_label', table_name='graph_nodes')
    op.drop_table('graph_nodes')
