from pathlib import Path

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from cmr_leads.db.models import Workspace

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


DEFAULT_SCHEMA = "cmr_leads"


def normalize_url(database_url: str) -> str:
    """Supabase da "postgresql://..." o "postgres://...": se usa el driver psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def make_engine(database_url: str, schema: str = DEFAULT_SCHEMA) -> Engine:
    database_url = normalize_url(database_url)
    if database_url.startswith("postgresql"):
        # Schema propio dentro del proyecto de Supabase (separado de la Landing y de Nexo).
        # prepare_threshold=None: el pooler de Supabase (modo transacción) no admite prepared
        # statements. En la nube cada llamada abre y cierra su conexión (NullPool).
        import os

        from sqlalchemy.pool import NullPool

        kwargs = {"poolclass": NullPool} if os.environ.get("VERCEL") else {"pool_pre_ping": True}
        return create_engine(
            database_url,
            connect_args={"options": f"-c search_path={schema}", "prepare_threshold": None},
            **kwargs,
        )
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    is_sqlite = database_url.startswith("sqlite")
    # Varios hilos escriben (panel, Instagram, publicidad): si la base está ocupada, esperar hasta
    # 30 s en vez de fallar enseguida con "database is locked".
    engine = create_engine(database_url, connect_args={"timeout": 30} if is_sqlite else {})
    if engine.dialect.name == "sqlite":
        file_db = ":memory:" not in database_url and database_url not in ("sqlite://", "sqlite:///")

        @event.listens_for(engine, "connect")
        def _enable_fks(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            if file_db:
                # WAL: se puede leer mientras otro hilo escribe.
                dbapi_conn.execute("PRAGMA journal_mode=WAL")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def run_migrations(database_url: str, schema: str = DEFAULT_SCHEMA) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", normalize_url(database_url).replace("%", "%%"))
    cfg.set_main_option("db_schema", schema)
    command.upgrade(cfg, "head")


def get_or_create_workspace(session: Session, slug: str) -> Workspace:
    workspace = session.scalar(select(Workspace).where(Workspace.slug == slug))
    if workspace is None:
        workspace = Workspace(slug=slug, name=slug.upper())
        session.add(workspace)
        session.flush()
    return workspace
