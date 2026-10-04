import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from cmr_leads.db.models import Base

config = context.config
target_metadata = Base.metadata

# `cmr-leads init` pasa la URL explícitamente; desde la CLI de Alembic se usa DATABASE_URL.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option(
        "sqlalchemy.url", os.environ.get("DATABASE_URL", "sqlite:///data/cmr_leads.db")
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # render_as_batch: permite ALTER TABLE en SQLite.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
