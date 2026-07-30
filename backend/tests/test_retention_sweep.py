"""Artifact retention sweep: reclaims old export storage, never the design
row itself (docs/ops/backup-and-retention.md).

The test DB is shared across the whole session (no per-test reset), so
assertions are scoped to THIS test's own design rather than the sweep's
global counters, which other tests' leftover rows can also contribute to.
"""
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import Design, ExportFile
from app.ops.retention_sweep import sweep


def _age_design(design_id: str, days: int) -> None:
    db = SessionLocal()
    try:
        design = db.get(Design, design_id)
        design.updated_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()
    finally:
        db.close()


def _export_ids(design_id: str) -> set[str]:
    db = SessionLocal()
    try:
        return {
            e.id for e in db.query(ExportFile).filter(ExportFile.design_id == design_id)
        }
    finally:
        db.close()


def test_sweep_disabled_when_retention_days_is_zero(client, auth):
    d = client.post(
        "/api/designs/create", json={"prompt": "a bracket 80x40x6mm"}, headers=auth["headers"]
    ).json()
    _age_design(d["id"], days=365)
    before = _export_ids(d["id"])
    assert before  # sanity: it has exports to protect
    result = sweep(retention_days=0, dry_run=False)
    assert result.designs_swept == 0
    assert _export_ids(d["id"]) == before  # untouched


def test_dry_run_reports_without_deleting(client, auth):
    d = client.post(
        "/api/designs/create", json={"prompt": "a bracket 80x40x6mm"}, headers=auth["headers"]
    ).json()
    _age_design(d["id"], days=365)
    before = _export_ids(d["id"])
    assert before

    result = sweep(retention_days=30, dry_run=True)
    assert result.designs_swept >= 1
    assert result.bytes_reclaimed > 0
    assert _export_ids(d["id"]) == before  # dry run never deletes


def test_sweep_reclaims_exports_but_preserves_the_design(client, auth):
    d = client.post(
        "/api/designs/create", json={"prompt": "a bracket 80x40x6mm"}, headers=auth["headers"]
    ).json()
    design_id = d["id"]
    _age_design(design_id, days=365)
    assert _export_ids(design_id)  # has exports before the sweep

    result = sweep(retention_days=30, dry_run=False)
    assert result.designs_swept >= 1
    assert result.bytes_reclaimed > 0

    db = SessionLocal()
    try:
        design = db.get(Design, design_id)
        assert design is not None, "the design row must never be deleted by the sweep"
        assert design.prompt  # prompt/spec data preserved
    finally:
        db.close()
    assert _export_ids(design_id) == set()  # this design's exports are gone


def test_recently_updated_designs_are_not_swept(client, auth):
    d = client.post(
        "/api/designs/create", json={"prompt": "a bracket 80x40x6mm"}, headers=auth["headers"]
    ).json()
    # No aging applied -- updated_at is "now".
    before = _export_ids(d["id"])
    assert before
    sweep(retention_days=30, dry_run=False)
    assert _export_ids(d["id"]) == before  # untouched
