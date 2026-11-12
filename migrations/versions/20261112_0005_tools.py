"""Persist MCP service pins, reviewed tools and invocation audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261112_0005"
down_revision = "20261112_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_services",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("transport", sa.String(12), nullable=False),
        sa.Column("target_digest", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("transport IN ('unix_http', 'https')", name="ck_mcp_services_transport"),
    )
    op.create_table(
        "tool_registrations",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("service_key", sa.String(40), nullable=False),
        sa.Column("remote_name", sa.String(128), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("required_role", sa.String(16), nullable=False),
        sa.Column("effect", sa.String(12), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("input_schema", JSONB(), nullable=False),
        sa.Column("output_schema", JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("version BETWEEN 1 AND 1000000", name="ck_tool_registrations_version"),
        sa.CheckConstraint(
            "effect IN ('read_only', 'write', 'high')", name="ck_tool_registrations_effect"
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 60", name="ck_tool_registrations_timeout"
        ),
        sa.ForeignKeyConstraint(["service_key"], ["mcp_services.key"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["required_role"], ["roles.name"], ondelete="RESTRICT"),
    )
    op.create_index("ix_tool_registrations_service_key", "tool_registrations", ["service_key"])
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(64)),
        sa.Column("tool_version", sa.Integer()),
        sa.Column("service_key", sa.String(40)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(status = 'started' AND finished_at IS NULL AND duration_ms IS NULL "
            "AND error_code IS NULL) OR "
            "(status IN ('completed', 'rejected', 'failed', 'cancelled') "
            "AND finished_at IS NOT NULL AND duration_ms >= 0 AND duration_ms IS NOT NULL "
            "AND ((status = 'completed') = (error_code IS NULL)))",
            name="ck_tool_calls_state",
        ),
        sa.CheckConstraint(
            "(tool_name IS NULL) = (tool_version IS NULL)", name="ck_tool_calls_tool_identity"
        ),
    )
    op.create_index("ix_tool_calls_actor_created", "tool_calls", ["actor_id", "created_at"])
    op.create_index("ix_tool_calls_request_id", "tool_calls", ["request_id"])


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("tool_registrations")
    op.drop_table("mcp_services")
