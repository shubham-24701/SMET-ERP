from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str, **kw):
    # pool_pre_ping: a connection dropped by the server (restart, idle timeout) is replaced
    # instead of failing the next request. statement_timeout stops a runaway query at 30 s.
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
        **kw,
    )


_s = get_settings()
engine = make_engine(_s.database_url, pool_size=_s.db_pool_size, max_overflow=_s.db_pool_size)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db
