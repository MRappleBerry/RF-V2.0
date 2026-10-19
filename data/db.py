"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from data.models import Base

_engine = None
_SessionFactory = None


def init(connection_string: str) -> None:
    """
    Initialise the database engine. Call once after config.load().

    connection_string examples:
      mssql+pyodbc://@server.database.windows.net/redflagged
          ?driver=ODBC+Driver+18+for+SQL+Server
          &authentication=ActiveDirectoryServicePrincipal
          &UID=client-id&PWD=client-secret
    """
    global _engine, _SessionFactory

    if connection_string.startswith("sqlite"):
        # SQLite — no pool config, allow multi-thread access
        _engine = create_engine(
            connection_string,
            connect_args={"check_same_thread": False},
            echo=False,
        )
    else:
        _engine = create_engine(
            connection_string,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            echo=False,
        )

    # Ensure tables exist on first run
    Base.metadata.create_all(_engine)

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    if _SessionFactory is None:
        raise RuntimeError("db.init() has not been called.")
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
