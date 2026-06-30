"""Standard Part Resolver — hex nut trust (BLACK-BOX through the create_design
path the browser uses), including MODELED internal threads.

Release blockers this guards:
  1. "Make a M12 hexagonal nut" must generate a model, never the generic
     "missing dimensions" clarification.
  2. M12 must resolve to M12 × 1.75 — never fall back to M3 × 0.5.
  3. A standard fastener defaults to a REAL modeled internal thread (helical
     geometry present in STL + STEP), reported honestly as such.
  4. A smooth bore must never be reported as a modeled thread (anti-fake).
"""
from __future__ import annotations

import math
import struct

import pytest

from app.cad.semantic_audits import measure_hex_sides, measure_internal_thread


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _download(client, auth, design_id: str, fmt: str) -> bytes:
    r = client.get(f"/api/designs/{design_id}/files/{fmt}", headers=auth["headers"])
    assert r.status_code == 200, f"{fmt} download failed: {r.status_code} {r.text[:200]}"
    return r.content


HEX_NUT_PROMPTS = [
    "Make a M12 hexagonal nut",
    "Make an M12 hex nut",
    "Make a DIN 934 M12 nut",
    "M12 hex nut",
    "ISO 4032 M12 hex nut",
    "Make a M12 nut",
]


# === A. PROMPT PARSING ======================================================
def test_m12_resolves_to_m12_never_m3():
    """The headline parsing bug: M12 must resolve to M12 × 1.75, never M3 × 0.5."""
    from app.cad.standard_parts.resolver import resolve_standard_part

    for p in HEX_NUT_PROMPTS:
        r = resolve_standard_part(p)
        assert r is not None, f"{p!r} should resolve"
        assert r.thread == "M12", f"{p!r} resolved to {r.thread}, expected M12"
        assert r.pitch_mm == 1.75, f"{p!r} pitch {r.pitch_mm}, expected 1.75"
        assert r.params.across_flats_mm == 18.0
        # The classic wrong answer must never appear.
        assert r.thread != "M3" and r.pitch_mm != 0.5


def test_m3_resolves_to_m3():
    from app.cad.standard_parts.resolver import resolve_standard_part

    r = resolve_standard_part("Make a M3 hexagonal nut")
    assert r.thread == "M3" and r.pitch_mm == 0.5
    assert r.params.across_flats_mm == 5.5


def test_make_a_m12_nut_does_not_resolve_to_m3():
    from app.cad.standard_parts.resolver import resolve_standard_part

    assert resolve_standard_part("Make a M12 nut").thread == "M12"
    assert resolve_standard_part("DIN 934 M12 nut").thread == "M12"


def test_resolver_rejects_non_standard():
    from app.cad.standard_parts.resolver import resolve_standard_part

    assert resolve_standard_part("a round spacer 10mm OD") is None
    assert resolve_standard_part("an M6 wing nut") is None
    assert resolve_standard_part("a hex standoff 8mm across flats") is None


def test_resolver_explicit_fine_pitch():
    from app.cad.standard_parts.resolver import resolve_standard_part

    fine = resolve_standard_part("M12x1.25 hex nut")
    assert fine.thread == "M12" and fine.pitch_mm == 1.25


# === core regression: a standard part NEVER clarifies =======================
@pytest.mark.parametrize("prompt", HEX_NUT_PROMPTS)
def test_hex_nut_prompt_generates_not_clarification(client, auth, prompt):
    d = _create(client, auth, prompt)
    assert d["generation_outcome"] == "generated_single_part", (
        f"{prompt!r} must generate a model, not {d['generation_outcome']}")
    assert d["needs_clarification"] is False
    assert d["clarification_question"] is None
    assert d["object_type"] == "hex_nut"
    assert d["route"].startswith("standard_part")
    assert d["validation_status"] in ("pass", "warning")
    assert d["validation_status"] != "critical_failure"
    assert d["download_blocked_reason"] is None


# === title / badge / metadata / measurements all agree on M12 ===============
def test_m12_metadata_consistent_everywhere(client, auth):
    d = _create(client, auth, "Make a M12 hexagonal nut")
    sp = d["standard_part"]
    assert sp["standard_part"] is True
    assert sp["family"] == "hex_nut"
    assert sp["thread"] == "M12"
    assert sp["pitch_mm"] == 1.75
    assert sp["standard"] == "ISO 4032"
    assert sp["across_flats_mm"] == pytest.approx(18.0, abs=0.01)
    assert d["title"] == "Hex nut"
    # Badge agrees on M12 × 1.75 and never shows M3.
    assert "M12 × 1.75" in sp["badge"]
    assert "M3" not in sp["badge"]
    # Measured geometry agrees on the M12 across-flats (~18mm), not M3 (~5.5mm).
    bb = d["bounding_box_mm"]
    assert min(bb["x"], bb["y"]) == pytest.approx(18.0, abs=0.3)


# === B. GEOMETRY (modeled thread) ===========================================
def test_m12_is_modeled_thread(client, auth):
    d = _create(client, auth, "Make a M12 hexagonal nut")
    sp = d["standard_part"]
    assert sp["thread_representation"] == "modeled"
    assert sp["internal_thread_modeled"] is True
    assert "Modeled thread" in sp["badge"]
    assert d["validation_status"] in ("pass", "warning")


def test_m12_geometry_and_exports(client, auth):
    d = _create(client, auth, "Make a M12 hexagonal nut")
    fmts = {e["fmt"] for e in d["exports"]}
    assert {"stl", "step"} <= fmts

    stl = _download(client, auth, d["id"], "stl")
    step = _download(client, auth, d["id"], "step")
    assert len(stl) > 0 and len(step) > 0
    # six-sided outer body
    assert measure_hex_sides(stl)["corner_count"] == 6
    # standard dims: ~18 across flats, ISO height ~10.8, ~20.78 across corners
    bb = d["bounding_box_mm"]
    assert min(bb["x"], bb["y"]) == pytest.approx(18.0, abs=0.3)
    assert max(bb["x"], bb["y"]) == pytest.approx(18.0 / 0.8660254, abs=0.3)
    assert bb["z"] == pytest.approx(10.8, abs=0.3)
    # the EXPORTED STL must physically contain the helical thread (varying bore).
    span = measure_internal_thread(stl, major_diameter_mm=12.0)["bore_radial_span_mm"]
    assert span is not None and span > 0.4 * (12.0 - 12.0 + 1.0825 * 1.75) / 2.0


def test_m12_clean_faces_and_hole_counts(client, auth):
    """The threaded M12 nut must have clean bearing faces and report 1 hole /
    1 through hole / 1 threaded hole."""
    d = _create(client, auth, "Make a M12 hexagonal nut")
    measured = (d["dimension_report"] or {}).get("measured") or {}
    assert measured.get("hole_count") == 1
    assert measured.get("through_hole_count") == 1
    assert measured.get("threaded_hole_count") == 1

    sp = d["standard_part"]
    assert sp["faces_clean"] is True
    assert sp.get("threaded_hole_count") == 1
    assert d["validation_status"] in ("pass", "warning")
    assert d["validation_status"] != "critical_failure"

    # The exported STL must have NO thread geometry on the flat bearing faces.
    stl = _download(client, auth, d["id"], "stl")
    from app.cad.semantic_audits import measure_thread_on_faces
    height = d["bounding_box_mm"]["z"]
    intr = measure_thread_on_faces(stl, 12.0, height)["face_intrusion_points"]
    assert intr == 0, f"thread bled onto a bearing face ({intr} points)"

    thread_audit = ((d["dimension_report"] or {}).get("semantic_audit") or {}).get("thread") or {}
    # thread present in the bore, bounded inside (faces_clean from geometry)
    assert thread_audit.get("internal_thread_modeled") is True
    assert thread_audit.get("faces_clean") is True


def test_sequential_nut_sizes_are_distinct(client, auth):
    """M3, M6, M12 generated in one session must each have their OWN standard
    dimensions, badge, and exports — guards the 'same-size nut' stale-state bug."""
    expected = {"M3": 5.5, "M6": 10.0, "M12": 18.0}
    designs = {}
    for size, af in expected.items():
        d = _create(client, auth, f"Make a {size} hex nut")
        designs[size] = d
        sp = d["standard_part"]
        # badge, metadata, and measured geometry all agree on THIS size.
        assert sp["thread"] == size, f"{size}: badge thread {sp['thread']}"
        assert f"{size} ×" in sp["badge"]
        assert sp["across_flats_mm"] == pytest.approx(af, abs=0.01)
        bb = d["bounding_box_mm"]
        assert min(bb["x"], bb["y"]) == pytest.approx(af, abs=0.3), f"{size}: bbox {bb}"
    # Distinct designs, distinct export URLs (no stale reuse across sizes).
    ids = {s: d["id"] for s, d in designs.items()}
    assert len(set(ids.values())) == 3
    urls = {s: sorted(e["url"] for e in d["exports"]) for s, d in designs.items()}
    assert urls["M3"] != urls["M6"] != urls["M12"] != urls["M3"]
    # Sanity: the three across-flats are genuinely different.
    afs = {s: designs[s]["standard_part"]["across_flats_mm"] for s in expected}
    assert afs["M3"] < afs["M6"] < afs["M12"]


def test_din934_standard_is_respected(client, auth):
    sp = _create(client, auth, "Make a DIN 934 M12 nut")["standard_part"]
    assert sp["standard"] == "DIN 934"
    assert sp["standard_assumed"] is False


def test_assumed_standard_message(client, auth):
    d = _create(client, auth, "Make a M12 hex nut")
    assert d["standard_part"]["standard_assumed"] is True
    assert "change standard" in d["standard_part"]["assumed_message"].lower()


# === C. ANTI-FAKE ===========================================================
def test_smooth_bore_is_not_a_modeled_thread():
    """A smooth cylindrical bore must NOT pass the internal-thread audit."""
    import cadquery as cq
    from cadquery import exporters

    from app.cad.semantic_audits import audit_internal_thread, measure_internal_thread

    nut = (cq.Workplane("XY").polygon(6, 18 / 0.8660254).extrude(10.8)
           .faces(">Z").workplane().hole(10.1))
    exporters.export(nut, "/tmp/_smooth_nut.stl")
    stl = open("/tmp/_smooth_nut.stl", "rb").read()
    span = measure_internal_thread(stl, major_diameter_mm=12.0)["bore_radial_span_mm"]
    assert span is None or span < 0.2, "a smooth bore must read ~0 radial variation"
    # Claiming modeled over a smooth bore -> a warning (review), never silent pass.
    issues = audit_internal_thread(
        claimed_modeled=True, thread_pitch_mm=1.75,
        bore_radial_span_mm=span, thread_depth_mm=0.947)
    assert any(i.check == "internal_thread_not_modeled" for i in issues)
    assert all(i.severity == "warning" for i in issues)


def test_modeled_claim_with_real_thread_passes_audit():
    from app.cad.semantic_audits import audit_internal_thread

    # A bore that varies by ~the thread depth is accepted as modeled.
    assert audit_internal_thread(
        claimed_modeled=True, thread_pitch_mm=1.75,
        bore_radial_span_mm=0.9, thread_depth_mm=0.947) == []


def test_engine_reports_fallback_when_not_modeled():
    """The thread engine must never label a smooth bore as modeled — if modeling
    fails it returns a fallback representation."""
    from app.cad.threads import THREAD_COSMETIC, THREAD_MODELED, cut_internal_thread
    import cadquery as cq

    body = cq.Workplane("XY").polygon(6, 18 / 0.8660254).extrude(10.8)
    # Cosmetic detail must produce a smooth bore reported as cosmetic.
    _solid, res = cut_internal_thread(
        body, major_diameter=12.0, pitch=1.75, length=10.8, detail="cosmetic")
    assert res.representation == THREAD_COSMETIC
    assert res.modeled is False

    # Modeled detail on M12 succeeds (helical geometry).
    _solid2, res2 = cut_internal_thread(
        body, major_diameter=12.0, pitch=1.75, length=10.8, detail="modeled")
    assert res2.representation == THREAD_MODELED
    assert res2.modeled is True
