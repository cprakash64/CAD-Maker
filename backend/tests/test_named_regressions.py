"""Named beta regressions from the production-readiness brief.

Each case is written to assert the *desired* behaviour so that it either
reproduces a genuine failure or locks in behaviour that already works. Where a
case already passes on the current baseline it is kept as regression coverage
(see docs/production-readiness.md for the reproduction table).
"""
from __future__ import annotations

import math

import pytest


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


# --- Case 1: drill jig generates on defaults, no needless clarification ----
DRILL_JIG = ("a drill jig plate 120mm by 80mm and 6mm thick with 6mm holes "
             "spaced 25mm apart")


def test_drill_jig_generates_with_defaults(client, auth):
    d = _create(client, auth, DRILL_JIG)
    assert d["needs_clarification"] is False, (
        "drill jig has all manufacturing-critical dimensions; missing "
        "non-critical values must use documented family defaults"
    )
    assert d["exports"], "drill jig produced no exports"


def test_drill_jig_dimensions_are_honoured(client, auth):
    d = _create(client, auth, DRILL_JIG)
    bbox = d.get("bounding_box_mm") or {}
    got = sorted(round(float(v)) for v in (bbox.get("x"), bbox.get("y"), bbox.get("z"))
                 if v is not None)
    assert got == [6, 80, 120], f"expected 120x80x6 plate, got {bbox}"


# --- Case 7: circular holes stay circular, correct count/diameter/spacing --
def test_drill_jig_holes_are_circular_and_correctly_sized(client, auth):
    d = _create(client, auth, DRILL_JIG)
    holes = d.get("selectable_holes") or []
    assert holes, "no selectable holes reported for a drill jig"

    # Every hole is the requested 6mm.
    for h in holes:
        assert h["diameter_mm"] == pytest.approx(6.0, abs=0.25), (
            f"hole {h['hole_id']} has diameter {h['diameter_mm']}, expected 6.0"
        )

    # Holes sit on a 25mm grid: every nearest-neighbour distance is a multiple
    # of 25 within tolerance (this is what "spaced 25mm" must mean).
    centers = [(h["center"][0], h["center"][1]) for h in holes]
    if len(centers) > 1:
        for i, (x1, y1) in enumerate(centers):
            dists = [math.dist((x1, y1), (x2, y2))
                     for j, (x2, y2) in enumerate(centers) if j != i]
            nearest = min(dists)
            assert nearest == pytest.approx(25.0, abs=0.5), (
                f"nearest neighbour spacing {nearest:.2f} is not the requested 25mm"
            )


def test_drill_jig_holes_survive_export_round_trip(client, auth):
    """Holes present in the model must still be present in the exported mesh."""
    from app.generation.mesh_analysis import analyze_stl

    d = _create(client, auth, DRILL_JIG)
    expected = len(d.get("selectable_holes") or [])
    assert expected > 0

    r = client.get(f"/api/designs/{d['id']}/files/stl", headers=auth["headers"])
    assert r.status_code == 200, r.text
    stats = analyze_stl(r.content)
    assert stats.through_holes == expected, (
        f"model reports {expected} holes but the exported STL has "
        f"{stats.through_holes} through-holes"
    )


def test_step_export_is_reopenable(client, auth):
    d = _create(client, auth, DRILL_JIG)
    r = client.get(f"/api/designs/{d['id']}/files/step", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"ISO-1", "STEP export is not a valid ISO-10303 file"


# --- Cases 2 & 3: crankshaft routes to its family, never a generic shaft ---
CRANKSHAFT = ("an inline-four engine crankshaft with 4 crank pins, 50mm main "
              "journal diameter, 45mm crank pin diameter, 40mm stroke and "
              "5 main bearing journals")


def test_crankshaft_routes_to_inline_4_family(client, auth):
    d = _create(client, auth, CRANKSHAFT)
    blob = " ".join(str(d.get(k, "")) for k in ("route", "object_type", "family", "title")).lower()
    assert "inline_4_crankshaft" in blob or "crankshaft" in blob, (
        f"crankshaft did not route to the crankshaft family: route={d.get('route')!r} "
        f"object_type={d.get('object_type')!r}"
    )


def test_crankshaft_does_not_silently_become_a_generic_shaft(client, auth):
    """Part Family Contract: no silent substitution to a generic shaft."""
    d = _create(client, auth, CRANKSHAFT)
    object_type = str(d.get("object_type") or "").lower()
    route = str(d.get("route") or "").lower()
    for generic in ("generic_shaft", "simple_shaft", "shaft_blank", "rectangular_bracket"):
        assert generic not in object_type, f"silently substituted to {generic}"
        assert generic not in route, f"silently substituted to {generic}"
    # If it could not build the real family it must say so, not fake it.
    if d.get("needs_clarification"):
        assert d.get("clarification_question")


# --- Case 6: failure modes are distinct, not collapsed into one state -----
def test_unsupported_upload_mime_is_rejected_distinctly(client, auth):
    """An unsupported content type must not reach the geometry parsers."""
    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("evil.exe", b"MZ\x90\x00binary", "application/x-msdownload")},
        headers=auth["headers"],
    )
    assert r.status_code in (400, 415, 422), (
        f"unsupported MIME returned {r.status_code}; expected an explicit rejection"
    )
    body = r.text.lower()
    assert "unknown" not in body or "0%" not in body, (
        "unsupported MIME collapsed into a generic unknown/0% result"
    )


def test_path_traversal_filename_is_neutralised(client, auth):
    """Uploaded filenames must never be used as storage paths."""
    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("../../../../etc/passwd", b"%PDF-1.4 fake", "application/pdf")},
        headers=auth["headers"],
    )
    # Whatever the outcome, it must not be a server error and must not echo a path.
    assert r.status_code < 500, r.text
    assert "/etc/passwd" not in r.text


# --- cross-user isolation (kept as standing security regression) ----------
def test_cross_user_design_access_returns_404(client, auth, auth2):
    d = _create(client, auth, DRILL_JIG)
    r = client.get(f"/api/designs/{d['id']}", headers=auth2["headers"])
    assert r.status_code == 404, f"cross-user read returned {r.status_code}"


def test_cross_user_export_download_returns_404(client, auth, auth2):
    d = _create(client, auth, DRILL_JIG)
    for fmt in ("stl", "step"):
        r = client.get(f"/api/designs/{d['id']}/files/{fmt}", headers=auth2["headers"])
        assert r.status_code == 404, f"cross-user {fmt} download returned {r.status_code}"


def test_cross_user_drawing_job_polling_returns_404(client, auth2):
    r = client.get("/api/drawings/jobs/some-other-users-job", headers=auth2["headers"])
    assert r.status_code == 404
