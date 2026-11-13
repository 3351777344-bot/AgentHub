"""Use structured settings; never interpolate/log passwords into alembic.ini."""

from alembic import context

from agenthub.agents import models as agent_models  # noqa: F401
from agenthub.chat import models as chat_models  # noqa: F401
from agenthub.core.config import Settings
from agenthub.db.session import Base, create_database_engine, database_url
from agenthub.evaluations import models as evaluation_models  # noqa: F401
from agenthub.identity import models  # noqa: F401 -- register all identity tables
from agenthub.knowledge import models as knowledge_models  # noqa: F401
from agenthub.rag import models as rag_models  # noqa: F401
from agenthub.tools import models as tool_models  # noqa: F401

config = context.config
target_metadata = Base.metadata
CHECKPOINT_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


def include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    # The pinned saver schema is installed explicitly by versioned migrations.
    return type_ != "table" or name not in CHECKPOINT_TABLES


def run_migrations() -> None:
    settings = config.attributes.get("settings") or Settings()
    if context.is_offline_mode():
        context.configure(
            url=database_url(settings),
            target_metadata=target_metadata,
            include_name=include_name,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                include_name=include_name,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
