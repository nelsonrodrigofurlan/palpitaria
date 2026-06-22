from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from palpitaria.config import settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _ensure_engine() -> Engine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    if settings.database_config_error:
        raise RuntimeError(settings.database_config_error)
    _engine = create_engine(
        settings.db_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )
    _session_factory = sessionmaker(bind=_engine, autocommit=False, autoflush=True)
    return _engine


class _EngineProxy:
    """Lazy engine — import do app não exige DATABASE_URL (Cloud Run lê env no runtime)."""

    def __getattr__(self, name: str):
        return getattr(_ensure_engine(), name)


engine = _EngineProxy()


class _SessionLocalFactory:
    def __call__(self) -> Session:
        _ensure_engine()
        assert _session_factory is not None
        return _session_factory()

    def __getattr__(self, name: str):
        _ensure_engine()
        assert _session_factory is not None
        return getattr(_session_factory, name)


SessionLocal = _SessionLocalFactory()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def apply_schema_migrations() -> None:
    """Migrações incrementais — preserva dados existentes (ADD COLUMN + defaults)."""
    if settings.database_config_error:
        return
    engine = _ensure_engine()
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(
                text("ALTER TABLE branches ADD COLUMN IF NOT EXISTS side VARCHAR(10) DEFAULT 'BACK'")
            )
            conn.execute(
                text("ALTER TABLE competitions ADD COLUMN IF NOT EXISTS odds_json TEXT")
            )
            conn.execute(
                text("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_deposits FLOAT DEFAULT 0.0")
            )
            conn.execute(
                text("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_withdrawals FLOAT DEFAULT 0.0")
            )
            conn.execute(
                text("ALTER TABLE users ADD COLUMN IF NOT EXISTS favorite_comp_code VARCHAR(10)")
            )
            conn.execute(
                text(
                    "UPDATE branches SET side = 'LAY' WHERE "
                    "lower(name) LIKE '%correct score%' OR lower(slug) LIKE '%correct%score%' "
                    "OR lower(coalesce(description, '')) LIKE '%correct score%' "
                    "OR lower(name) LIKE '%placar exato%'"
                )
            )
            conn.execute(text("UPDATE branches SET side = 'BACK' WHERE side IS NULL"))
        elif dialect == "sqlite":
            branch_cols = {c["name"] for c in inspect(engine).get_columns("branches")}
            if "side" not in branch_cols:
                conn.execute(text("ALTER TABLE branches ADD COLUMN side VARCHAR(10) DEFAULT 'BACK'"))

            comp_cols = {c["name"] for c in inspect(engine).get_columns("competitions")}
            if "odds_json" not in comp_cols:
                conn.execute(text("ALTER TABLE competitions ADD COLUMN odds_json TEXT"))
            conn.execute(
                text(
                    "UPDATE branches SET side = 'LAY' WHERE "
                    "lower(name) LIKE '%correct score%' OR lower(slug) LIKE '%correct%score%' "
                    "OR lower(coalesce(description, '')) LIKE '%correct score%' "
                    "OR lower(name) LIKE '%placar exato%'"
                )
            )
            conn.execute(text("UPDATE branches SET side = 'BACK' WHERE side IS NULL"))

        _migrate_pipeline_daily_per_comp(conn, dialect, engine)


def _migrate_pipeline_daily_per_comp(conn, dialect: str, engine: Engine) -> None:
    """Trava diária do pipeline passa a ser por campeonato (run_day + comp_code)."""
    if not inspect(engine).has_table("remote_pipeline_daily"):
        return
    cols = {c["name"] for c in inspect(engine).get_columns("remote_pipeline_daily")}
    if "comp_code" in cols:
        return

    if dialect == "postgresql":
        conn.execute(
            text(
                "ALTER TABLE remote_pipeline_daily "
                "ADD COLUMN IF NOT EXISTS comp_code VARCHAR(10) NOT NULL DEFAULT 'WC'"
            )
        )
        conn.execute(
            text(
                "UPDATE remote_pipeline_daily AS r "
                "SET comp_code = COALESCE(p.comp_code, 'WC') "
                "FROM pipeline_runs AS p "
                "WHERE p.id = r.pipeline_run_id"
            )
        )
        conn.execute(text("ALTER TABLE remote_pipeline_daily DROP CONSTRAINT IF EXISTS remote_pipeline_daily_pkey"))
        conn.execute(text("ALTER TABLE remote_pipeline_daily ADD PRIMARY KEY (run_day, comp_code)"))
    elif dialect == "sqlite":
        conn.execute(
            text(
                """
                CREATE TABLE remote_pipeline_daily_new (
                    run_day VARCHAR(10) NOT NULL,
                    comp_code VARCHAR(10) NOT NULL DEFAULT 'WC',
                    pipeline_run_id INTEGER NOT NULL,
                    created_at DATETIME,
                    PRIMARY KEY (run_day, comp_code),
                    FOREIGN KEY(pipeline_run_id) REFERENCES pipeline_runs(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT OR REPLACE INTO remote_pipeline_daily_new
                    (run_day, comp_code, pipeline_run_id, created_at)
                SELECT r.run_day, COALESCE(p.comp_code, 'WC'), r.pipeline_run_id, r.created_at
                FROM remote_pipeline_daily r
                LEFT JOIN pipeline_runs p ON p.id = r.pipeline_run_id
                """
            )
        )
        conn.execute(text("DROP TABLE remote_pipeline_daily"))
        conn.execute(text("ALTER TABLE remote_pipeline_daily_new RENAME TO remote_pipeline_daily"))


def init_db() -> None:
    if settings.database_config_error:
        return
    from palpitaria import models  # noqa: F401

    Base.metadata.create_all(bind=_ensure_engine())
    apply_schema_migrations()
