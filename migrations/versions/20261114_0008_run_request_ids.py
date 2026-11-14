"""Keep the API request ID with durable background runs."""

import sqlalchemy as sa
from alembic import op

revision = "20261114_0008"
down_revision = "20261113_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("agent_runs", "evaluation_runs"):
        op.add_column(table, sa.Column("request_id", sa.String(64), nullable=True))


def downgrade() -> None:
    for table in ("evaluation_runs", "agent_runs"):
        op.drop_column(table, "request_id")
