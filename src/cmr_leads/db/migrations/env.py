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


SCHEMA = config.get_main_option("db_schema") or os.environ.get("DATABASE_SCHEMA", "cmr_leads")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        version_table_schema=SCHEMA
        if "postgresql" in config.get_main_option("sqlalchemy.url")
        else None,
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
        if connection.dialect.name == "postgresql":
            # Todo va en un schema propio dentro del proyecto de Supabase.
            from sqlalchemy import text

            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
            connection.execute(text(f'SET search_path TO "{SCHEMA}"'))
            connection.commit()
        # render_as_batch: permite ALTER TABLE en SQLite.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            version_table_schema=SCHEMA if connection.dialect.name == "postgresql" else None,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
