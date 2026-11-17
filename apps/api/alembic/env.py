from alembic import context

from openkg import models  # noqa: F401  (registers tables)
from openkg.config import get_settings
from openkg.db import Base, make_engine

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Migrations must not import application code: render our UTC decorator as plain timestamptz."""
    if type_ == "type" and obj.__class__.__name__ == "UTCDateTime":
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_online() -> None:
    engine = make_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, render_item=render_item)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
