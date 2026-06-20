"""SQLAlchemy engine/session. SQLite in dev; set DATABASE_URL for Postgres."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
# pool_pre_ping recycles connections that a Postgres server (or a proxy/idle
# timeout) has dropped, so a long-running worker never serves a dead connection.
# Harmless for SQLite.
engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Ensure the schema exists.

    Dev/test convenience ONLY: create tables directly from the models so the app
    and the test suite run with zero setup. In staging/production this is a no-op
    — the schema is owned by Alembic migrations (`alembic upgrade head`), which
    must be run before boot. This keeps create_all from silently diverging from
    the migration history in production.
    """
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    if settings.is_production_like and not settings.testing:
        from app.observability import log_event

        log_event(
            "db_init_skipped",
            reason="production schema is managed by Alembic; run `alembic upgrade head`",
        )
        return

    Base.metadata.create_all(bind=engine)
