"""Persist retrieval metadata and citations for RAG turns."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261112_0004"
down_revision = "20261110_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_runs",
        sa.Column("message_id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("citations", JSONB(), nullable=False),
        sa.Column("embedding_ms", sa.Integer(), nullable=True),
        sa.Column("embedding_tokens", sa.Integer(), nullable=True),
        sa.Column("search_ms", sa.Integer(), nullable=True),
        sa.Column("retrieved_count", sa.Integer(), nullable=True),
        sa.Column("context_count", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('answered', 'insufficient_evidence')", name="ck_rag_runs_outcome"
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
    )


def downgrade() -> None:
    op.drop_table("rag_runs")
