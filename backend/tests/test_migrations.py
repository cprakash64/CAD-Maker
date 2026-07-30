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
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
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


def test_alembic_has_exactly_one_head():
    """A second, divergent head would make `alembic upgrade head` ambiguous --
    this must never happen regardless of how many people add migrations."""
    cfg = _config(_temp_db())
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected exactly one Alembic head, found: {heads}"
    assert heads[0] == "f2a8b3a98437"


def _seed_minimal_feedback_row(engine, *, rating: str = "up") -> str:
    """Insert one user -> project -> design -> feedback row using ONLY the
    columns that exist at revision 0db7d6dd7f9b (the parent of f2a8b3a98437),
    so this reproduces exactly what a real pre-migration production database
    with existing feedback looks like. Returns the feedback row's id."""
    user_id, project_id, design_id, feedback_id = (uuid.uuid4().hex for _ in range(4))
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        # data_improvement_opt_in already exists and is NOT NULL (with no
        # server_default) at revision 0db7d6dd7f9b -- it must be supplied
        # explicitly here, same as any real caller at this schema revision.
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, created_at, "
            "data_improvement_opt_in) VALUES (:id, :email, :ph, :now, :opt_in)"
        ), {"id": user_id, "email": f"{user_id}@example.com", "ph": "x", "now": now,
            "opt_in": False})
        conn.execute(text(
            "INSERT INTO projects (id, user_id, name, created_at) "
            "VALUES (:id, :uid, :name, :now)"
        ), {"id": project_id, "uid": user_id, "name": "p", "now": now})
        # can_generate_with_defaults / auto_repaired / repair_attempts all
        # have a Python-side-only default (no server_default) -- a raw
        # INSERT bypassing the ORM must supply them explicitly.
        conn.execute(text(
            "INSERT INTO designs (id, project_id, prompt, created_at, updated_at, "
            "can_generate_with_defaults, auto_repaired, repair_attempts) "
            "VALUES (:id, :pid, :prompt, :now, :now, :cgd, :ar, :ra)"
        ), {"id": design_id, "pid": project_id, "prompt": "x", "now": now,
            "cgd": False, "ar": False, "ra": 0})
        conn.execute(text(
            "INSERT INTO feedback (id, user_id, design_id, rating, created_at) "
            "VALUES (:id, :uid, :did, :rating, :now)"
        ), {"id": feedback_id, "uid": user_id, "did": design_id, "rating": rating, "now": now})
    return feedback_id


def test_feedback_migration_upgrades_a_populated_table_on_sqlite():
    """The exact scenario the original bug missed: `feedback` already has
    rows (real pre-existing user feedback) BEFORE f2a8b3a98437 runs. This
    must not raise, and existing rows must backfill to the honest historical
    value (False for both -- they predate the bad-result-report feature and
    never had consent recorded)."""
    url = _temp_db()
    cfg = _config(url)
    command.upgrade(cfg, "0db7d6dd7f9b")  # the parent revision, pre-fix schema

    engine = create_engine(url)
    fb_id = _seed_minimal_feedback_row(engine)

    command.upgrade(cfg, "f2a8b3a98437")  # must not raise on the populated table

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT is_bad_result_report, report_consent, design_version_number, "
            "prompt_version, print_success, fit_success, report_reason "
            "FROM feedback WHERE id = :id"
        ), {"id": fb_id}).one()
    engine.dispose()

    assert row[0] in (False, 0), "existing row must backfill is_bad_result_report=False"
    assert row[1] in (False, 0), "existing row must backfill report_consent=False"
    # Nullable columns added alongside them stay NULL for pre-existing rows --
    # no invented history for fields that genuinely don't apply.
    assert row[2] is None
    assert row[3] is None
    assert row[4] is None
    assert row[5] is None
    assert row[6] is None


def test_feedback_migration_new_rows_satisfy_model_and_db_constraints():
    """After the migration, both the ORM (ordinary insert relying on the
    Python-side default) and a raw explicit insert must succeed and round-trip
    correctly -- the server_default removal after backfill must not have left
    the column impossible to satisfy."""
    url = _temp_db()
    cfg = _config(url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with engine.begin() as conn:
        # Explicit True/True -- the actual "report a bad result, with consent" shape.
        user_id, project_id, design_id, fb_id = (uuid.uuid4().hex for _ in range(4))
        now = datetime.now(timezone.utc)
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, created_at, "
            "data_improvement_opt_in) VALUES (:id, :email, :ph, :now, :opt_in)"
        ), {"id": user_id, "email": f"{user_id}@example.com", "ph": "x", "now": now,
            "opt_in": False})
        conn.execute(text(
            "INSERT INTO projects (id, user_id, name, created_at) "
            "VALUES (:id, :uid, :name, :now)"
        ), {"id": project_id, "uid": user_id, "name": "p", "now": now})
        # can_generate_with_defaults / auto_repaired / repair_attempts all
        # have a Python-side-only default (no server_default) -- a raw
        # INSERT bypassing the ORM must supply them explicitly.
        conn.execute(text(
            "INSERT INTO designs (id, project_id, prompt, created_at, updated_at, "
            "can_generate_with_defaults, auto_repaired, repair_attempts) "
            "VALUES (:id, :pid, :prompt, :now, :now, :cgd, :ar, :ra)"
        ), {"id": design_id, "pid": project_id, "prompt": "x", "now": now,
            "cgd": False, "ar": False, "ra": 0})
        conn.execute(text(
            "INSERT INTO feedback (id, user_id, design_id, rating, created_at, "
            "is_bad_result_report, report_consent, report_reason) "
            "VALUES (:id, :uid, :did, 'down', :now, :bad, :consent, :reason)"
        ), {"id": fb_id, "uid": user_id, "did": design_id, "now": now,
            "bad": True, "consent": True, "reason": "walls too thin"})
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT is_bad_result_report, report_consent, report_reason "
            "FROM feedback WHERE id = :id"
        ), {"id": fb_id}).one()
    engine.dispose()
    assert row[0] in (True, 1)
    assert row[1] in (True, 1)
    assert row[2] == "walls too thin"

    # And the ORM path: Feedback(...) with neither field set explicitly must
    # rely on the Python-side default (False), not fail a NOT NULL constraint.
    from sqlalchemy.orm import sessionmaker

    from app.models import Design, Feedback, Project, User

    Session = sessionmaker(bind=create_engine(url))
    db = Session()
    try:
        u = User(email="orm@example.com", password_hash="x")
        db.add(u)
        db.flush()
        p = Project(user_id=u.id, name="p")
        db.add(p)
        db.flush()
        d = Design(project_id=p.id, prompt="x")
        db.add(d)
        db.flush()
        fb = Feedback(user_id=u.id, design_id=d.id, rating="up")
        db.add(fb)
        db.commit()
        db.refresh(fb)
        assert fb.is_bad_result_report is False
        assert fb.report_consent is False
    finally:
        db.close()


def test_feedback_migration_downgrade_removes_columns_without_erroring():
    """Downgrade support is retained and tested -- restores schema shape only,
    same convention as the program_code downgrade tests above."""
    url = _temp_db()
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0db7d6dd7f9b")

    engine = create_engine(url)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("feedback")}
        for c in ("is_bad_result_report", "design_version_number", "prompt_version",
                  "validation_snapshot", "print_success", "fit_success",
                  "report_consent", "report_reason"):
            assert c not in cols, f"feedback.{c} should be gone after downgrade"
    finally:
        engine.dispose()

    # Re-upgrading afterward must still work (re-runnable, same convention as
    # test_upgrade_downgrade_upgrade_round_trips above).
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    cols = {c["name"] for c in inspect(engine).get_columns("feedback")}
    engine.dispose()
    assert "is_bad_result_report" in cols


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


@pytest.mark.skipif(not _PG_URL, reason="set CADMAKER_TEST_PG_URL to test Postgres migrations")
def test_postgres_feedback_migration_upgrades_a_populated_table():
    """The real regression this migration had: on Postgres specifically,
    `ALTER TABLE feedback ADD COLUMN ... NOT NULL` with no server_default
    fails outright against a table that already has rows -- SQLite's more
    permissive batch-table-rebuild path let this slip past local dev/test
    (see the SQLite-only equivalent test above). This is the test that
    actually proves the fix on the target production database engine, not
    just SQLite.

    CADMAKER_TEST_PG_URL must point at a DISPOSABLE database."""
    cfg = _config(_PG_URL)
    engine = create_engine(_PG_URL)
    try:
        command.upgrade(cfg, "0db7d6dd7f9b")
        fb_id = _seed_minimal_feedback_row(engine)

        # This is the exact operation that failed before the fix: ADD COLUMN
        # ... NOT NULL on a `feedback` table that already has a row.
        command.upgrade(cfg, "f2a8b3a98437")

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT is_bad_result_report, report_consent "
                "FROM feedback WHERE id = :id"
            ), {"id": fb_id}).one()
        assert row[0] is False
        assert row[1] is False

        command.downgrade(cfg, "base")
        left = set(inspect(engine).get_table_names())
        assert not (EXPECTED_TABLES & left), f"tables left after downgrade: {left}"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()
