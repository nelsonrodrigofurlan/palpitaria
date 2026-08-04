import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from palpitaria.config import settings
from palpitaria.database import Base, apply_schema_migrations


def _require_supabase_engine():
    if not settings.uses_postgres:
        pytest.skip("DATABASE_URL Supabase (PostgreSQL) necessária para testes")
    engine = create_engine(settings.db_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Supabase indisponível: {exc}")
    Base.metadata.create_all(engine)
    # create_all só cria tabelas novas; colunas adicionadas em tabelas existentes
    # (ex.: users.is_admin) só entram via apply_schema_migrations — mesma função
    # que o app roda no startup real. Sem isso o schema de teste desalinha do prod.
    apply_schema_migrations()
    return engine


@pytest.fixture(scope="session")
def engine():
    return _require_supabase_engine()


@pytest.fixture(scope="session")
def tables(engine):
    yield


@pytest.fixture
def db_session(engine, tables):
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
