"""Physical calibration system tests (docs/calibration.md).

Covers: the versioned profile/measurement schema, the pure resolver (generic
estimate fallback + real-profile override), the DB-aware service layer
(CRUD, CSV import, review/activation gating, comparison), all ten coupon
generators through the real HTTP API + validated export path, consumption
(bearing/phone-holder provenance, the mechanism-coupon's real DB-backed
override), and the acceptance criteria stated directly.
"""
from __future__ import annotations

import io

import pytest

from app.schemas.calibration import (
    CalibrationMeasurement,
    CalibrationMeasurementType,
    CalibrationProfile,
    CalibrationProfileStatus,
    CalibrationSourceType,
    FitClass,
)

COUPON_TYPES = [
    "calibration_master_coupon", "calibration_vertical_hole_gauge",
    "calibration_horizontal_hole_plate", "calibration_fit_ladder",
    "calibration_wall_pin_gap_coupon", "calibration_overhang_bridge_tower",
    "calibration_fastener_plate", "calibration_snap_fit_kit",
    "calibration_mechanism_coupon", "calibration_text_plate",
]


# --------------------------------------------------------------------- schema

def test_measurement_statistics_are_server_derived_from_raw_samples():
    m = CalibrationMeasurement(
        measurement_type="radial_clearance", feature="hole_pin_clearance",
        nominal_mm=8.0, raw_samples_mm=[0.10, 0.12, 0.11, 0.09, 0.13])
    m.recompute_statistics()
    assert m.sample_count == 5
    assert m.median_mm == pytest.approx(0.11)
    assert m.range_mm == pytest.approx(0.04)
    assert m.stddev_mm > 0
    assert 0 < m.confidence <= 0.95


def test_measurement_type_specific_requirements_enforced():
    with pytest.raises(Exception):
        CalibrationMeasurement(measurement_type="dimensional_correction", feature="x")
    with pytest.raises(Exception):
        CalibrationMeasurement(measurement_type="functional_fit_recommendation", feature="x")
    # OK: dimensional_correction with a note explaining no single nominal applies.
    CalibrationMeasurement(measurement_type="dimensional_correction", feature="x",
                           notes="blanket correction, no single nominal")


def test_generic_estimate_profile_never_eligible_for_validation():
    p = CalibrationProfile(
        id="p1", label="x", source_type="generic_estimate",
        printer="a", nozzle_mm=0.4, material_type="PLA",
        layer_height_mm=0.2, line_width_mm=0.45, reviewed=True,
        measurements=[CalibrationMeasurement(
            measurement_type="radial_clearance", feature="x",
            raw_samples_mm=[0.1, 0.1, 0.1])])
    errors = p.eligible_for_validation()
    assert any("generic_estimate" in e for e in errors)


def test_incomplete_profile_lists_every_missing_requirement():
    p = CalibrationProfile(id="p1", label="incomplete", source_type="user_calibrated")
    errors = p.eligible_for_validation()
    assert any("printer" in e for e in errors)
    assert any("no measurements" in e for e in errors)
    assert any("reviewed" in e for e in errors)
    assert p.eligible_for_activation()  # can't activate either


def test_insufficient_samples_blocks_validation():
    m = CalibrationMeasurement(measurement_type="radial_clearance", feature="x",
                               raw_samples_mm=[0.1, 0.11])
    m.recompute_statistics()
    p = CalibrationProfile(
        id="p1", label="x", source_type="user_calibrated", reviewed=True,
        printer="a", nozzle_mm=0.4, material_type="PLA",
        layer_height_mm=0.2, line_width_mm=0.45, measurements=[m])
    errors = p.eligible_for_validation()
    assert any("at least 3" in e for e in errors)


def test_complete_reviewed_profile_is_eligible():
    m = CalibrationMeasurement(measurement_type="radial_clearance", feature="x",
                               raw_samples_mm=[0.1, 0.11, 0.12])
    m.recompute_statistics()
    p = CalibrationProfile(
        id="p1", label="x", source_type="user_calibrated", reviewed=True,
        printer="a", nozzle_mm=0.4, material_type="PLA",
        layer_height_mm=0.2, line_width_mm=0.45, measurements=[m])
    assert p.eligible_for_validation() == []


# ------------------------------------------------------------------- resolver

def test_resolver_falls_back_to_generic_estimate_labeled_as_such():
    from app.cad.calibration.resolver import resolve_measurement

    r = resolve_measurement(
        [], measurement_type=CalibrationMeasurementType.diametral_clearance,
        feature="bearing_seat", fit_class=FitClass.press)
    assert r.value_mm == pytest.approx(-0.02)
    assert r.is_estimate is True
    assert r.provenance["source_type"] == "generic_estimate"


def test_resolver_prefers_a_matching_validated_profile_over_the_estimate():
    from app.cad.calibration.resolver import resolve_measurement

    m = CalibrationMeasurement(measurement_type="diametral_clearance", feature="bearing_seat",
                               fit_class="press", raw_samples_mm=[-0.03, -0.025, -0.028, -0.031])
    m.recompute_statistics()
    profile = CalibrationProfile(
        id="p1", label="My Ender3 PLA", source_type="user_calibrated",
        status="validated", active=True, printer="Ender3", material_type="PLA",
        nozzle_mm=0.4, layer_height_mm=0.2, line_width_mm=0.45, reviewed=True,
        measurements=[m])
    r = resolve_measurement(
        [profile], measurement_type=CalibrationMeasurementType.diametral_clearance,
        feature="bearing_seat", fit_class=FitClass.press,
        printer="Ender3", material_type="PLA", nozzle_mm=0.4)
    assert r.is_estimate is False
    assert r.value_mm == pytest.approx(-0.029)
    assert r.provenance["profile_id"] == "p1"


def test_resolver_ignores_a_profile_that_does_not_match_the_context():
    from app.cad.calibration.resolver import resolve_measurement

    m = CalibrationMeasurement(measurement_type="diametral_clearance", feature="bearing_seat",
                               fit_class="press", raw_samples_mm=[-0.03, -0.025, -0.028])
    m.recompute_statistics()
    profile = CalibrationProfile(
        id="p1", label="x", source_type="user_calibrated", status="validated", active=True,
        printer="Ender3", material_type="PLA", nozzle_mm=0.4, layer_height_mm=0.2,
        line_width_mm=0.45, reviewed=True, measurements=[m])
    r = resolve_measurement(
        [profile], measurement_type=CalibrationMeasurementType.diametral_clearance,
        feature="bearing_seat", fit_class=FitClass.press,
        printer="Prusa MK3S", material_type="ABS")
    assert r.is_estimate is True  # falls back; the profile doesn't apply here


def test_resolver_draft_profile_never_wins_over_estimate():
    from app.cad.calibration.resolver import resolve_measurement

    m = CalibrationMeasurement(measurement_type="diametral_clearance", feature="bearing_seat",
                               fit_class="press", raw_samples_mm=[-0.03, -0.025, -0.028])
    m.recompute_statistics()
    draft = CalibrationProfile(
        id="p1", label="unreviewed", source_type="user_calibrated", status="draft",
        active=True, printer="Ender3", material_type="PLA", measurements=[m])
    r = resolve_measurement(
        [draft], measurement_type=CalibrationMeasurementType.diametral_clearance,
        feature="bearing_seat", fit_class=FitClass.press, printer="Ender3", material_type="PLA")
    assert r.is_estimate is True


# ---------------------------------------------------------------- consolidation

def test_no_more_duplicate_clearance_tables():
    """The three independent M-size clearance tables found during the audit
    (standards/defaults.py, plan/defaults.py, hex_standoff.py) must now agree
    exactly -- plan/defaults.py and hex_standoff.py delegate to the one
    canonical standards table instead of repeating the numbers."""
    from app.cad.hex_standoff import clearance_hole as hex_clearance_hole
    from app.cad.plan.defaults import CLEARANCE_HOLES_MM
    from app.cad.standards.defaults import METRIC_CLEARANCE_HOLES

    for label, tiers in METRIC_CLEARANCE_HOLES.items():
        assert CLEARANCE_HOLES_MM[label] == tiers["normal"]
    assert hex_clearance_hole("M4", "normal") == METRIC_CLEARANCE_HOLES["M4"]["normal"]


# ------------------------------------------------------------------- service

@pytest.fixture
def db_session():
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_user(db):
    import uuid

    from app.models import User

    u = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
    db.add(u)
    db.commit()
    return u


def test_service_review_refuses_incomplete_profile(db_session):
    from app.services import calibration_service as svc

    u = _make_user(db_session)
    p = svc.create_profile(db_session, user_id=u.id, source_type="user_calibrated",
                           fields={"label": "x"})
    with pytest.raises(svc.CalibrationError, match="not eligible"):
        svc.review_and_validate(db_session, p.id, reviewer_user_id=u.id)


def test_service_validated_profile_is_immutable(db_session):
    from app.services import calibration_service as svc

    u = _make_user(db_session)
    p = svc.create_profile(db_session, user_id=u.id, source_type="user_calibrated", fields={
        "label": "x", "printer": "Ender3", "nozzle_mm": 0.4, "material_type": "PLA",
        "layer_height_mm": 0.2, "line_width_mm": 0.45})
    svc.add_measurement(db_session, p.id, measurement_type="radial_clearance", feature="x",
                        fit_class=None, nominal_mm=None, raw_samples_mm=[0.1, 0.11, 0.12])
    validated = svc.review_and_validate(db_session, p.id, reviewer_user_id=u.id)
    assert validated.status == "validated"
    with pytest.raises(svc.CalibrationError, match="immutable"):
        svc.update_profile(db_session, p.id, {"label": "renamed"})
    with pytest.raises(svc.CalibrationError, match="fork it first"):
        svc.add_measurement(db_session, p.id, measurement_type="radial_clearance",
                            feature="y", fit_class=None, nominal_mm=None,
                            raw_samples_mm=[1, 1, 1])


def test_service_activation_requires_validated_status(db_session):
    from app.services import calibration_service as svc

    u = _make_user(db_session)
    p = svc.create_profile(db_session, user_id=u.id, source_type="user_calibrated",
                           fields={"label": "x"})
    with pytest.raises(svc.CalibrationError, match="cannot be activated"):
        svc.activate_profile(db_session, p.id)


def test_service_csv_import_is_all_or_nothing(db_session):
    from app.services import calibration_service as svc

    u = _make_user(db_session)
    p = svc.create_profile(db_session, user_id=u.id, source_type="user_calibrated",
                           fields={"label": "x"})
    bad_csv = ("measurement_type,feature,samples\n"
              "radial_clearance,good,0.1;0.11;0.12\n"
              "radial_clearance,bad,notanumber\n")
    with pytest.raises(svc.CalibrationError):
        svc.import_measurements_csv(db_session, p.id, bad_csv)
    fresh = svc.get_profile(db_session, p.id)
    assert fresh.measurements == []  # the good row was NOT partially applied


def test_service_csv_import_happy_path(db_session):
    from app.services import calibration_service as svc

    u = _make_user(db_session)
    p = svc.create_profile(db_session, user_id=u.id, source_type="user_calibrated",
                           fields={"label": "x"})
    csv_text = ("measurement_type,feature,fit_class,nominal_mm,samples,notes\n"
               "diametral_clearance,bearing_seat,press,,-0.03;-0.028;-0.031;-0.029,caliper\n")
    rows = svc.import_measurements_csv(db_session, p.id, csv_text)
    assert len(rows) == 1
    assert rows[0].sample_count == 4
    assert rows[0].median_mm == pytest.approx(-0.0295)


def test_service_comparison_aligns_measurements_across_profiles(db_session):
    from app.services import calibration_service as svc

    u = _make_user(db_session)
    p1 = svc.create_profile(db_session, user_id=u.id, source_type="user_calibrated",
                            fields={"label": "A"})
    p2 = svc.create_profile(db_session, user_id=u.id, source_type="user_calibrated",
                            fields={"label": "B"})
    svc.add_measurement(db_session, p1.id, measurement_type="radial_clearance", feature="x",
                        fit_class=None, nominal_mm=None, raw_samples_mm=[0.1, 0.11, 0.12])
    svc.add_measurement(db_session, p2.id, measurement_type="radial_clearance", feature="x",
                        fit_class=None, nominal_mm=None, raw_samples_mm=[0.2, 0.21, 0.22])
    cmp = svc.compare_profiles(db_session, [p1.id, p2.id])
    assert len(cmp["measurements"]) == 1
    row = cmp["measurements"][0]
    assert row["by_profile"][p1.id]["median_mm"] == pytest.approx(0.11)
    assert row["by_profile"][p2.id]["median_mm"] == pytest.approx(0.21)


# --------------------------------------------------------------------- coupons

@pytest.mark.parametrize("coupon_type", COUPON_TYPES)
def test_every_coupon_generates_a_single_valid_solid_and_exports(client, auth, coupon_type):
    """Acceptance criterion: coupon exports pass geometry and export checks."""
    r = client.post(f"/api/calibration/coupons/{coupon_type}/generate",
                    json={"dimensions": {}}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["object_type"] == coupon_type
    fmts = {e["fmt"] for e in d["exports"]}
    assert fmts >= {"stl", "step"}
    assert all(e["size_bytes"] > 0 for e in d["exports"])
    assert d["download_blocked_reason"] is None
    assert d["capability_level"] == "experimental"
    for fmt in ("stl", "step"):
        dl = client.get(f"/api/designs/{d['id']}/files/{fmt}", headers=auth["headers"])
        assert dl.status_code == 200
        assert len(dl.content) > 0


def test_coupon_list_endpoint_lists_all_ten(client, auth):
    r = client.get("/api/calibration/coupons", headers=auth["headers"])
    assert r.status_code == 200
    listed = {c["object_type"] for c in r.json()}
    assert listed == set(COUPON_TYPES)


def test_unknown_coupon_type_is_404(client, auth):
    r = client.post("/api/calibration/coupons/not_a_real_coupon/generate",
                    json={"dimensions": {}}, headers=auth["headers"])
    assert r.status_code == 404


# ------------------------------------------------------------------- API/workflow

def test_full_profile_lifecycle_via_api(client, auth):
    h = auth["headers"]
    r = client.post("/api/calibration/profiles",
                    json={"source_type": "user_calibrated", "label": "Ender3 PLA"}, headers=h)
    assert r.status_code == 200
    pid = r.json()["id"]
    assert r.json()["status"] == "draft"

    # incomplete -> review refused
    r = client.post(f"/api/calibration/profiles/{pid}/review", headers=h)
    assert r.status_code == 422

    r = client.post(f"/api/calibration/profiles/{pid}/measurements", json={
        "measurement_type": "diametral_clearance", "feature": "bearing_seat",
        "fit_class": "press", "raw_samples_mm": [-0.03, -0.028, -0.031, -0.029]}, headers=h)
    assert r.status_code == 200
    assert r.json()["sample_count"] == 4

    r = client.patch(f"/api/calibration/profiles/{pid}", json={
        "printer": "Ender3", "nozzle_mm": 0.4, "material_type": "PLA",
        "layer_height_mm": 0.2, "line_width_mm": 0.45}, headers=h)
    assert r.status_code == 200

    r = client.post(f"/api/calibration/profiles/{pid}/review", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "validated"

    r = client.post(f"/api/calibration/profiles/{pid}/activate", headers=h)
    assert r.status_code == 200
    assert r.json()["active"] is True


def test_csv_file_import_endpoint(client, auth):
    h = auth["headers"]
    pid = client.post("/api/calibration/profiles",
                      json={"source_type": "user_calibrated", "label": "x"}, headers=h).json()["id"]
    csv_bytes = (b"measurement_type,feature,fit_class,nominal_mm,samples\n"
                b"radial_clearance,hole_pin_clearance,,8,0.1;0.11;0.12\n")
    r = client.post(f"/api/calibration/profiles/{pid}/measurements/import-csv-file",
                    files={"file": ("measurements.csv", io.BytesIO(csv_bytes), "text/csv")},
                    headers=h)
    assert r.status_code == 200
    assert r.json()["imported"] == 1


def test_cross_user_profile_access_is_404(client, auth, auth2):
    pid = client.post("/api/calibration/profiles",
                      json={"source_type": "user_calibrated", "label": "private"},
                      headers=auth["headers"]).json()["id"]
    r = client.get(f"/api/calibration/profiles/{pid}", headers=auth2["headers"])
    assert r.status_code == 404


def test_invalid_incomplete_profile_cannot_be_activated_via_api(client, auth):
    """Acceptance criterion: invalid or incomplete profiles cannot be
    activated as tested profiles."""
    h = auth["headers"]
    pid = client.post("/api/calibration/profiles",
                      json={"source_type": "user_calibrated", "label": "incomplete"},
                      headers=h).json()["id"]
    r = client.post(f"/api/calibration/profiles/{pid}/activate", headers=h)
    assert r.status_code == 422

    # A generic_estimate profile, even if "complete", can never validate/activate.
    pid2 = client.post("/api/calibration/profiles", json={
        "source_type": "generic_estimate", "label": "builtin", "printer": "x",
        "nozzle_mm": 0.4, "material_type": "PLA", "layer_height_mm": 0.2,
        "line_width_mm": 0.45}, headers=h).json()["id"]
    client.post(f"/api/calibration/profiles/{pid2}/measurements", json={
        "measurement_type": "radial_clearance", "feature": "x",
        "raw_samples_mm": [0.1, 0.11, 0.12]}, headers=h)
    r = client.post(f"/api/calibration/profiles/{pid2}/review", headers=h)
    assert r.status_code == 422
    r = client.post(f"/api/calibration/profiles/{pid2}/activate", headers=h)
    assert r.status_code == 422


# --------------------------------------------------------------------- consumption

def test_bearing_holder_design_shows_calibration_provenance(client, auth):
    """Acceptance criterion: profile provenance appears in user-facing
    validation results."""
    r = client.post("/api/designs/create",
                    json={"prompt": "A bearing holder for a 608 bearing, slip fit"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    calib_checks = [c for c in d["checks"] if c["check"] == "calibration_profile_provenance"]
    assert len(calib_checks) == 1
    assert "generic engineering estimate" in calib_checks[0]["message"]
    assert calib_checks[0]["severity"] == "warning"


def test_mechanism_coupon_uses_active_validated_profile_over_estimate(client, auth):
    """Acceptance criterion: no critical fit value is silently hardcoded
    where a profile lookup is required -- a real, DB-backed override."""
    h = auth["headers"]
    r = client.post("/api/calibration/coupons/calibration_mechanism_coupon/generate",
                    json={"dimensions": {"shaft_diameter": 6.0}}, headers=h)
    baseline_bbox = r.json()["bounding_box_mm"]
    assert any("generic estimate" in a for a in r.json()["assumptions"])

    pid = client.post("/api/calibration/profiles", json={
        "source_type": "user_calibrated", "label": "Ender3 PLA", "printer": "Ender3",
        "nozzle_mm": 0.4, "material_type": "PLA", "layer_height_mm": 0.2,
        "line_width_mm": 0.45}, headers=h).json()["id"]
    client.post(f"/api/calibration/profiles/{pid}/measurements", json={
        "measurement_type": "diametral_clearance", "feature": "holder_case_clearance",
        "fit_class": "normal", "raw_samples_mm": [0.35, 0.33, 0.36, 0.34]}, headers=h)
    client.post(f"/api/calibration/profiles/{pid}/review", headers=h)
    client.post(f"/api/calibration/profiles/{pid}/activate", headers=h)

    r = client.post("/api/calibration/coupons/calibration_mechanism_coupon/generate",
                    json={"dimensions": {"shaft_diameter": 6.0}, "printer": "Ender3",
                         "material_type": "PLA", "nozzle_mm": 0.4}, headers=h)
    d = r.json()
    assert any("Ender3 PLA" in a for a in d["assumptions"])
    assert d["bounding_box_mm"]["x"] != baseline_bbox["x"]


def test_explicit_bore_clearance_override_is_never_replaced_by_a_profile(client, auth):
    h = auth["headers"]
    r = client.post("/api/calibration/coupons/calibration_mechanism_coupon/generate",
                    json={"dimensions": {"shaft_diameter": 6.0, "bore_clearance_override": 0.2}},
                    headers=h)
    assert r.status_code == 200
    d = r.json()
    assert not any("generic estimate" in a or "validated calibration profile" in a
                  for a in d["assumptions"])
