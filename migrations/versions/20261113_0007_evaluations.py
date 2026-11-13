"""Add versioned evaluation datasets, batch runs and case results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261113_0007"
down_revision = "20261113_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("corpus_digest", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", "version", name="uq_evaluation_dataset_version"),
    )
    op.create_index(
        "ix_evaluation_datasets_owner_created",
        "evaluation_datasets",
        ["owner_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_digest", sa.String(length=64), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "report", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status IN ('completed','failed','cancelled')) = (finished_at IS NOT NULL) "
            "AND (finished_at IS NOT NULL) = (report IS NOT NULL) "
            "AND (status = 'failed') = (error_code IS NOT NULL) "
            "AND (status <> 'running' OR started_at IS NOT NULL) "
            "AND reserved_tokens >= 0",
            name="ck_evaluation_runs_state",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_evaluation_runs_status",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["evaluation_datasets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_runs_owner_created",
        "evaluation_runs",
        ["owner_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_runs_worker", "evaluation_runs", ["status", "created_at", "id"], unique=False
    )
    op.create_table(
        "evaluation_results",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("failure_stage", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "observation", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "score", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "review", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','passed','failed','skipped') AND ordinal > 0",
            name="ck_evaluation_results_state",
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "key"),
        sa.UniqueConstraint("agent_run_id"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_evaluation_result_ordinal"),
    )


def downgrade() -> None:
    op.drop_table("evaluation_results")
    op.drop_index("ix_evaluation_runs_worker", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_owner_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluation_datasets_owner_created", table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")
