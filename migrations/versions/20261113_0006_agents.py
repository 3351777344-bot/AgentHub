"""Persist Agent runs and install the pinned LangGraph PostgreSQL saver schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261113_0006"
down_revision = "20261112_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agents_owner_created", "agents", ["owner_id", "created_at", "id"], unique=False
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_target", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("recoverable", sa.Boolean(), nullable=False),
        sa.Column("remaining_ms", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("active_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'running') = (active_since IS NOT NULL) "
            "AND (status = 'running') = (claim_id IS NOT NULL) "
            "AND (status = 'waiting_approval') = (approval_id IS NOT NULL) "
            "AND (status = 'failed') = (error_code IS NOT NULL) "
            "AND (status = 'completed') = (answer IS NOT NULL) "
            "AND (status = 'running' OR NOT pause_requested) "
            "AND (status = 'failed' OR NOT recoverable)",
            name="ck_agent_runs_state",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'waiting_approval', "
            "'completed', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "remaining_ms >= 0 AND event_seq >= 0 AND completion_tokens >= 0 "
            "AND prompt_tokens >= 0",
            name="ck_agent_runs_counters",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index(
        "ix_agent_runs_owner_created", "agent_runs", ["owner_id", "created_at", "id"], unique=False
    )
    op.create_index(
        "ix_agent_runs_worker", "agent_runs", ["status", "updated_at", "id"], unique=False
    )
    op.create_table(
        "agent_events",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "sequence"),
    )
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column(
            "input", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "output", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("tool_call_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('model', 'tool')", name="ck_agent_steps_kind"),
        sa.CheckConstraint(
            "status IN ('planned', 'started', 'completed', 'rejected', 'failed', 'uncertain')",
            name="ck_agent_steps_status",
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_agent_steps_ordinal"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_agent_steps_run_ordinal"),
    )
    op.create_table(
        "agent_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=8), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(decision IS NULL AND decided_by IS NULL AND decided_at IS NULL) OR "
            "(decision IN ('approve', 'reject') AND decision IS NOT NULL "
            "AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_agent_approvals_decision",
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_steps.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id"),
    )
    op.add_column(
        "tool_registrations",
        sa.Column("agent_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        "ck_tool_registrations_agent_effect",
        "tool_registrations",
        "NOT agent_enabled OR effect IN ('write', 'high')",
    )
    # Final schema of langgraph-checkpoint-postgres 3.2.0 migrations 0 through 9.
    # The tables are empty on install, so their indexes can be created transactionally.
    op.execute("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)")
    op.execute("""
        CREATE TABLE checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint JSONB NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
    """)
    op.execute("""
        CREATE TABLE checkpoint_blobs (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            version TEXT NOT NULL,
            type TEXT NOT NULL,
            blob BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
        )
    """)
    op.execute("""
        CREATE TABLE checkpoint_writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT NOT NULL,
            blob BYTEA NOT NULL,
            task_path TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
    """)
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        op.create_index(f"{table}_thread_id_idx", table, ["thread_id"])
    op.execute("INSERT INTO checkpoint_migrations (v) SELECT generate_series(0, 9)")


def downgrade() -> None:
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
        op.drop_table(table)
    op.drop_constraint("ck_tool_registrations_agent_effect", "tool_registrations", type_="check")
    op.drop_column("tool_registrations", "agent_enabled")
    op.drop_table("agent_approvals")
    op.drop_table("agent_steps")
    op.drop_table("agent_events")
    op.drop_index("ix_agent_runs_worker", table_name="agent_runs")
    op.drop_index("ix_agent_runs_owner_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_agents_owner_created", table_name="agents")
    op.drop_table("agents")
