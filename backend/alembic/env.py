"""Alembic migration environment for the SourceCAD backend.

Resolves the database URL from app settings (so migrations hit the same DB the
app uses), targets ``Base.metadata`` for autogenerate, and enables SQLite-safe
batch ALTERs (also harmless on Postgres).
"""
from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the backend package importable when alembic runs from backend/.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app import models  # noqa: E402,F401  (register all tables on Base.metadata)

config = context.config

# Configure Python logging from alembic.ini, if present. Never disable existing
# loggers — running migrations from inside the app/tests must not clobber the
# app's "sourcecad" logger.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Resolve the URL: an explicit override (set_main_option / -x) wins, else the
# app's configured DATABASE_URL (or the dev SQLite default).
_url = config.get_main_option("sqlalchemy.url") or settings.database_url
config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Emit SQL without a live DB connection (`alembic upgrade --sql`)."""
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=_is_sqlite(_url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url
    connect_args = {"check_same_thread": False} if _is_sqlite(_url) else {}
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=_is_sqlite(_url),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
