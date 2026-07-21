"""Alembic migration tests — prove the migration chain builds the real schema
on a clean database and never drifts from the SQLAlchemy models.

These run against an isolated temp SQLite file (never the dev/test app DB).

The Postgres test is OPT-IN: it is skipped unless CADMAKER_TEST_PG_URL points at a
disposable Postgres database, e.g.
  CADMAKER_TEST_PG_URL=postgresql+psycopg://user@127.0.0.1:5432/cadmaker_test pytest
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import models  # noqa: F401  (register tables on Base.metadata)
from app.database import Base

_BACKEND = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "users", "projects", "designs", "export_files",
    "feedback", "manufacturing_checks",
}


def _config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _temp_db():
    tmp = Path(tempfile.mkdtemp(prefix="cadmaker-mig-")) / "m.db"
    return f"sqlite:///{tmp}"


def test_upgrade_head_creates_full_schema():
    url = _temp_db()
    command.upgrade(_config(url), "head")

    engine = create_engine(url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert EXPECTED_TABLES <= tables, f"missing tables: {EXPECTED_TABLES - tables}"
    assert "alembic_version" in tables, "alembic did not stamp the version table"

    # Spot-check the wide Design table's columns survived autogenerate.
    design_cols = {c["name"] for c in insp.get_columns("designs")}
    for col in ("spec_json", "semantic_json", "repair_attempts",
                "route", "bounding_box", "thumbnail_key"):
        assert col in design_cols, f"designs.{col} missing from migration"
    # The legacy model-authored-source column must be gone at head.
    assert "program_code" not in design_cols, (
        "designs.program_code is back — executable model output must never be "
        "persisted (migration b1c4e7a92f38)"
    )

    # The unique email index exists on users.
    user_indexes = {ix["name"] for ix in insp.get_indexes("users")}
    assert any("email" in (n or "") for n in user_indexes)
    engine.dispose()


def test_migration_matches_models_no_drift():
    """After `upgrade head`, the live schema must equal Base.metadata exactly —
    i.e. the initial migration is a faithful snapshot of the models."""
    url = _temp_db()
    command.upgrade(_config(url), "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diffs = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diffs == [], f"schema drift between models and migration: {diffs}"


def _design_columns(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {c["name"] for c in inspect(engine).get_columns("designs")}
    finally:
        engine.dispose()


def test_program_code_present_at_baseline_then_dropped_by_upgrade():
    """Prove the drop actually migrates an EXISTING database, not just a fresh one.

    Upgrade to the baseline revision (which still has the column), then upgrade
    to head and confirm it is gone. This is the path a production database takes.
    """
    url = _temp_db()
    cfg = _config(url)

    command.upgrade(cfg, "7fbf0c6446aa")
    assert "program_code" in _design_columns(url), "baseline should still have it"

    command.upgrade(cfg, "head")
    assert "program_code" not in _design_columns(url), "upgrade did not drop the column"


def test_downgrade_recreates_only_an_inert_nullable_column():
    """Downgrade support is retained, but it restores schema shape only — an
    empty nullable column, never data, an executor, or an API field."""
    url = _temp_db()
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "7fbf0c6446aa")

    engine = create_engine(url)
    try:
        cols = {c["name"]: c for c in inspect(engine).get_columns("designs")}
        assert "program_code" in cols
        assert cols["program_code"]["nullable"] is True
        # Nothing was resurrected into it.
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT program_code FROM designs")).fetchall()
        assert all(r[0] is None for r in rows)
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_round_trips():
    """The migration is re-runnable: head → baseline → head must not error."""
    url = _temp_db()
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "7fbf0c6446aa")
    command.upgrade(cfg, "head")
    assert "program_code" not in _design_columns(url)


def test_orm_declares_no_program_column():
    """The model and the migration must agree that the column is gone."""
    from app.models import Design

    assert not hasattr(Design, "program_code")
    assert "program_code" not in Design.__table__.columns


def test_downgrade_base_is_clean():
    url = _temp_db()
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    # Only Alembic's bookkeeping table remains; all app tables are gone.
    assert not (EXPECTED_TABLES & tables), f"tables left after downgrade: {tables}"


# --- Postgres (opt-in: requires a disposable Postgres via CADMAKER_TEST_PG_URL) ---
_PG_URL = os.environ.get("CADMAKER_TEST_PG_URL")


@pytest.mark.skipif(not _PG_URL, reason="set CADMAKER_TEST_PG_URL to test Postgres migrations")
def test_postgres_upgrade_check_downgrade():
    """Full migration lifecycle against a real Postgres server.

    CADMAKER_TEST_PG_URL must point at a DISPOSABLE database (this runs upgrade /
    check / downgrade and removes the tables + alembic_version afterwards)."""
    cfg = _config(_PG_URL)
    engine = create_engine(_PG_URL)
    try:
        command.upgrade(cfg, "head")
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= tables, f"missing on Postgres: {EXPECTED_TABLES - tables}"
        assert engine.dialect.name == "postgresql"

        # No drift vs the models on Postgres either.
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(ctx, Base.metadata)
        assert diffs == [], f"Postgres schema drift: {diffs}"

        command.downgrade(cfg, "base")
        left = set(inspect(engine).get_table_names())
        assert not (EXPECTED_TABLES & left), f"tables left after downgrade: {left}"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()
