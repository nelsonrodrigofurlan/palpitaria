from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from palpitaria.config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///./"):
        from pathlib import Path

        path = url.replace("sqlite:///./", "")
        Path(path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.db_url)
engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False} if settings.db_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from palpitaria import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
