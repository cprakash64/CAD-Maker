"""Hardening added for tenant isolation / upload / export-artifact security.

Covers the specific gaps closed in this phase (not a re-test of the extensive
existing cross-user coverage in test_phase2_authorization.py /
test_phase1_security_regressions.py / test_named_regressions.py):

  * ownership enforced by a JOIN-filtered query (the DB query boundary),
    not a fetch-then-check in application code
  * a design owned by an orphaned (user_id=None) project is unreachable
  * every geometry pipeline (templates, CadPlan compiler/assemblies) refuses
    to export a solid that fails a BRep validity check, not just the
    crankshaft template
  * upload parsing runs under a hard wall-clock timeout
  * the S3 signed-URL default is short-lived, and the DB never persists a raw
    signed URL (only the owner-checked app route)
"""
from __future__ import annotations

import time

import pytest

from app.cad.base import CadGenerationError


# --- ownership at the query boundary --------------------------------------

def test_get_owned_design_returns_none_for_wrong_owner():
    from app.database import SessionLocal
    from app.models import Design, Project
    from app.services import design_service

    db = SessionLocal()
    try:
        owner = Project(name="mine", user_id="user-a")
        other = Project(name="not-mine", user_id="user-b")
        db.add_all([owner, other])
        db.flush()
        design = Design(project_id=owner.id, prompt="x")
        db.add(design)
        db.commit()

        assert design_service.get_owned_design(db, design.id, "user-a") is not None
        assert design_service.get_owned_design(db, design.id, "user-b") is None
        assert design_service.get_owned_design(db, design.id, "nonexistent-user") is None
        assert design_service.get_owned_design(db, "not-a-real-id", "user-a") is None
    finally:
        db.close()


def test_null_owner_project_design_is_unreachable_by_any_user(client, auth):
    """Project.user_id is nullable; an orphaned project's design must not be
    adoptable/visible to any authenticated user (query-boundary join can never
    match Project.user_id == user.id when it is None)."""
    from app.database import SessionLocal
    from app.models import Design, Project

    db = SessionLocal()
    try:
        orphan = Project(name="orphan", user_id=None)
        db.add(orphan)
        db.flush()
        design = Design(project_id=orphan.id, prompt="orphaned")
        db.add(design)
        db.commit()
        design_id = design.id
    finally:
        db.close()

    r = client.get(f"/api/designs/{design_id}", headers=auth["headers"])
    assert r.status_code == 404


def test_sketch_debug_routes_use_the_shared_ownership_helper(client, auth, auth2, monkeypatch):
    """Regression guard: the two drawing debug routes must 404 a non-owner via
    the same query-boundary helper as every other design route (they used to
    duplicate the fetch-then-check pattern ad hoc)."""
    from app.config import settings

    monkeypatch.setattr(settings, "dev_mode", True)
    r = client.post("/api/designs/create", json={"prompt": "a 40mm cube"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    design_id = r.json()["id"]

    for path in (f"/api/drawings/debug/{design_id}",
                f"/api/drawings/debug/{design_id}/overlay"):
        r2 = client.get(path, headers=auth2["headers"])
        assert r2.status_code == 404, (path, r2.text)


# --- universal pre-write solid-validity gate ------------------------------

class _InvalidShape:
    def isValid(self) -> bool:
        return False


class _InvalidSolid:
    """Stands in for a cq.Workplane whose built shape fails BRep validity."""

    def val(self):
        return _InvalidShape()


class _CrashingShape:
    def isValid(self):
        raise RuntimeError("kernel exploded")


class _CrashingSolid:
    def val(self):
        return _CrashingShape()


def test_assert_valid_solid_rejects_invalid_brep():
    from app.export.exporter import _assert_valid_solid

    with pytest.raises(CadGenerationError):
        _assert_valid_solid(_InvalidSolid(), "widget")


def test_assert_valid_solid_treats_a_crash_as_invalid():
    from app.export.exporter import _assert_valid_solid

    with pytest.raises(CadGenerationError):
        _assert_valid_solid(_CrashingSolid(), "widget")


def test_assert_valid_solid_passes_a_real_valid_solid():
    import cadquery as cq

    from app.export.exporter import _assert_valid_solid

    solid = cq.Workplane("XY").box(10, 10, 10)
    _assert_valid_solid(solid, "box")  # must not raise


def test_generate_rejects_invalid_solid_before_any_bytes_are_produced(monkeypatch):
    """Every object type goes through generate(); an invalid BRep must be
    caught immediately after build_solid(), before STL/STEP export ever runs
    -- this used to be true ONLY for the crankshaft template."""
    from app.export import exporter

    monkeypatch.setattr(exporter, "build_solid", lambda spec: _InvalidSolid())

    called = {"export": False}

    def _spy_export_bytes(*a, **k):
        called["export"] = True
        raise AssertionError("should never reach export")

    monkeypatch.setattr(exporter, "_export_bytes", _spy_export_bytes)

    class _FakeSpec:
        object_type = "generic_mechanical_part"
        dimensions: dict = {}

    with pytest.raises(CadGenerationError):
        exporter.generate(_FakeSpec())
    assert called["export"] is False


def test_compiler_export_solid_rejects_invalid_solid_before_any_bytes(monkeypatch):
    """The CadPlan compiler / assembly / frame-family pipeline shares a
    SEPARATE export path (export_solid) from templates; it must get the same
    pre-write validity gate."""
    from app.cad.plan import compiler as compiler_mod

    called = {"export": False}

    def _spy_export_bytes(*a, **k):
        called["export"] = True
        raise AssertionError("should never reach export")

    monkeypatch.setattr(compiler_mod, "_export_bytes", _spy_export_bytes)

    with pytest.raises(CadGenerationError):
        compiler_mod.export_solid(_InvalidSolid())
    assert called["export"] is False


def test_disconnected_solids_still_pass_brep_validity_but_are_caught_downstream(client, auth):
    """The universal isValid() gate must NOT regress the existing
    disconnected-body detection: two valid, non-touching boxes are each a
    valid BRep (isValid() is about self-intersection/corrupt faces, not
    connectivity), so they still reach the dimension-report's component-count
    check and get flagged critical_failure there, exactly as before."""
    from app.database import SessionLocal
    from app.cad.plan.planner import build_and_validate
    from app.cad.plan.schema import CadPlan
    from app.models import Design, Project
    from app.services import design_service

    plan_dict = {
        "object_type": "broken", "name": "two disconnected boxes",
        "features": [
            {"id": "a", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
             "at": [0, 0, 0]},
            {"id": "b", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
             "at": [100, 0, 0]},
        ],
        "expected": {"bbox_mm": {"x": 110, "y": 10, "z": 10}},
    }
    db = SessionLocal()
    try:
        proj = Project(name="test", user_id=auth["user"]["id"])
        db.add(proj)
        db.flush()
        design = Design(project_id=proj.id, prompt="x")
        db.add(design)
        db.flush()
        plan = CadPlan(**plan_dict)
        outcome = build_and_validate(plan)  # must not raise -- BRep is valid
        design_service._store_plan(db, design, plan, outcome, 0, None)
        db.commit()
        design_id = design.id
    finally:
        db.close()

    r = client.get(f"/api/designs/{design_id}", headers=auth["headers"])
    assert r.status_code == 200
    assert r.json()["validation_status"] == "critical_failure"


# --- upload parsing timeout ------------------------------------------------

def test_upload_parse_timeout_rejects_a_hanging_parse(monkeypatch):
    from app.services import upload_guard

    def _slow_inspect(data, filename, content_type):
        time.sleep(2.0)
        return "should never get here"

    monkeypatch.setattr(upload_guard, "inspect_upload", _slow_inspect)

    with pytest.raises(upload_guard.UploadTimeout):
        upload_guard.inspect_upload_with_timeout(b"x", "f.svg", "image/svg+xml", timeout=0.1)


def test_upload_parse_timeout_error_status_is_422():
    from app.services.upload_guard import UploadTimeout

    assert UploadTimeout.status_code == 422


def test_fast_upload_still_passes_through_the_timeout_wrapper():
    """No regression: a normal upload still returns the real result."""
    from app.services.upload_guard import inspect_upload_with_timeout

    # A truncated/garbage SVG is enough to exercise the wrapper end-to-end
    # without depending on a real image fixture.
    data = b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='1' height='1'/></svg>"
    result = inspect_upload_with_timeout(data, "drawing.svg", "image/svg+xml", timeout=10.0)
    assert result.file_type == "svg"


def test_endpoint_maps_upload_timeout_to_a_safe_422(client, auth, monkeypatch):
    import app.routers.drawings as drawings_mod

    def _timeout_everything(data, filename, content_type):
        from app.services.upload_guard import UploadTimeout
        raise UploadTimeout("This file took too long to process and was rejected.")

    monkeypatch.setattr(drawings_mod, "inspect_upload_with_timeout", _timeout_everything)

    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("drawing.png", b"\x89PNGnotreallyapng", "image/png")},
        headers=auth["headers"],
    )
    assert r.status_code == 422
    body = r.json()
    assert "too long" in body["detail"].lower()
    # Safe structured failure -- no traceback/path leak.
    assert "Traceback" not in r.text
    assert "/Users/" not in r.text and "/app/" not in r.text


# --- signed URL / artifact delivery hygiene --------------------------------

def test_default_signed_url_ttl_is_short_lived():
    import dataclasses

    from app.config import Settings

    default_ttl = next(
        f.default for f in dataclasses.fields(Settings) if f.name == "s3_signed_url_ttl"
    )
    assert default_ttl <= 1800  # 30 minutes ceiling for what we consider "short-lived"
    assert default_ttl == 900


def test_zip_archive_is_rejected_on_every_upload_endpoint(client, auth):
    """No upload endpoint supports archives; a ZIP (even one truthfully named
    and typed) must be rejected by the magic-byte check, never unzipped or
    otherwise treated as a container of further files."""
    zip_bytes = b"PK\x03\x04" + b"\x00" * 64

    r1 = client.post(
        "/api/drawings/interpret",
        files={"file": ("drawing.zip", zip_bytes, "application/zip")},
        headers=auth["headers"],
    )
    assert r1.status_code in (415, 422), r1.text

    r2 = client.post(
        "/api/drawings/interpret",
        files={"file": ("drawing.png", zip_bytes, "image/png")},
        headers=auth["headers"],
    )
    assert r2.status_code in (415, 422), r2.text


def test_exportfile_url_is_never_a_raw_signed_url(client, auth):
    r = client.post("/api/designs/create", json={"prompt": "a 30mm cube"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["exports"], "design should have produced exports"
    for export in d["exports"]:
        url = export["url"]
        assert url.startswith("http")
        assert "/api/designs/" in url and "/files/" in url
        # A raw S3 presigned URL carries a signature query string; the stored
        # URL must always be our own owner-checked route, never that.
        assert "X-Amz-Signature" not in url
        assert "amazonaws.com" not in url
