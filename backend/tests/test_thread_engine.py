"""Modeled-thread engine (app.cad.threads.metric) — unit + geometry tests."""
from __future__ import annotations

import math

import pytest


def test_coarse_pitch_table():
    from app.cad.threads.metric import metric_coarse_pitch

    assert metric_coarse_pitch(12.0) == 1.75
    assert metric_coarse_pitch(3.0) == 0.5
    assert metric_coarse_pitch(8.0) == 1.25
    assert metric_coarse_pitch(999.0) is None


def test_internal_minor_diameter():
    from app.cad.threads.metric import internal_minor_diameter

    # D1 = D - 1.0825*P ; M12x1.75 -> ~10.106
    assert internal_minor_diameter(12.0, 1.75) == pytest.approx(10.106, abs=0.01)


def test_modeled_thread_is_watertight_and_present():
    """A modeled M12 thread must be a watertight single solid with real helical
    geometry (varying bore wall)."""
    import cadquery as cq
    from cadquery import exporters

    from app.cad.plan.dimension_report import mesh_facts
    from app.cad.semantic_audits import measure_internal_thread
    from app.cad.threads import THREAD_MODELED, cut_internal_thread
    from app.cad.threads.metric import (
        THREAD_STL_ANGULAR_TOLERANCE,
        THREAD_STL_TOLERANCE,
    )

    body = cq.Workplane("XY").polygon(6, 18 / 0.8660254).extrude(10.8)
    solid, res = cut_internal_thread(
        body, major_diameter=12.0, pitch=1.75, length=10.8, detail=THREAD_MODELED)
    assert res.representation == THREAD_MODELED and res.modeled is True
    assert len(solid.solids().vals()) == 1

    exporters.export(solid, "/tmp/_engine_m12.stl",
                     tolerance=THREAD_STL_TOLERANCE,
                     angularTolerance=THREAD_STL_ANGULAR_TOLERANCE)
    stl = open("/tmp/_engine_m12.stl", "rb").read()
    mf = mesh_facts(stl)
    assert mf["watertight"] and mf["manifold"]
    span = measure_internal_thread(stl, 12.0)["bore_radial_span_mm"]
    assert span is not None and span > 0.5 * res.depth_mm


def test_cosmetic_detail_is_smooth_and_labeled():
    import cadquery as cq

    from app.cad.threads import THREAD_COSMETIC, cut_internal_thread

    body = cq.Workplane("XY").polygon(6, 18 / 0.8660254).extrude(10.8)
    solid, res = cut_internal_thread(
        body, major_diameter=12.0, pitch=1.75, length=10.8, detail=THREAD_COSMETIC)
    assert res.representation == THREAD_COSMETIC and res.modeled is False
    assert len(solid.solids().vals()) == 1


def test_representation_values_are_the_documented_set():
    from app.cad.threads import THREAD_COSMETIC, THREAD_FALLBACK, THREAD_MODELED

    assert THREAD_MODELED == "modeled"
    assert THREAD_COSMETIC == "cosmetic"
    assert THREAD_FALLBACK == "failed_to_model_fallback_cosmetic"


def _hexbody(af, h):
    import cadquery as cq

    e = af / 0.8660254
    ch = min((e - af) / 2 * 0.85, h * 0.15)
    return cq.Workplane("XY").polygon(6, e).extrude(h).edges(">Z or <Z").chamfer(ch)


def test_modeled_thread_has_clean_bearing_faces():
    """The modeled M12 thread must be bounded between the lead-in recesses and must
    NOT bleed onto the flat top/bottom bearing faces."""
    from cadquery import exporters

    from app.cad.semantic_audits import measure_thread_on_faces
    from app.cad.threads import THREAD_MODELED, cut_internal_thread
    from app.cad.threads.metric import (
        THREAD_STL_ANGULAR_TOLERANCE,
        THREAD_STL_TOLERANCE,
    )

    height = 10.8
    solid, res = cut_internal_thread(
        _hexbody(18.0, height), major_diameter=12.0, pitch=1.75,
        length=height, detail=THREAD_MODELED)
    assert res.representation == THREAD_MODELED and res.modeled is True
    # Thread bounded strictly inside the bore (after lead-in, before bottom lead-in).
    assert res.thread_z_start > 0.5
    assert res.thread_z_end < height - 0.5
    assert res.lead_in_mm > 0

    exporters.export(solid, "/tmp/_clean_faces_m12.stl",
                     tolerance=THREAD_STL_TOLERANCE,
                     angularTolerance=THREAD_STL_ANGULAR_TOLERANCE)
    stl = open("/tmp/_clean_faces_m12.stl", "rb").read()
    intr = measure_thread_on_faces(stl, 12.0, height)["face_intrusion_points"]
    assert intr == 0, f"thread bled onto a bearing face ({intr} points)"


def test_cross_section_debug_artifact():
    """The debug cross-section exposes the bore and is exportable (visual QA)."""
    import os

    from cadquery import exporters

    from app.cad.threads import cut_internal_thread
    from app.cad.threads.metric import cross_section_half

    solid, _res = cut_internal_thread(
        _hexbody(18.0, 10.8), major_diameter=12.0, pitch=1.75,
        length=10.8, detail="modeled")
    section = cross_section_half(solid)
    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports", "thread_debug")
    os.makedirs(out_dir, exist_ok=True)
    stl_path = os.path.join(out_dir, "m12_nut_section.stl")
    step_path = os.path.join(out_dir, "m12_nut_section.step")
    exporters.export(section, stl_path)
    exporters.export(section, step_path)
    assert os.path.getsize(stl_path) > 0
    assert os.path.getsize(step_path) > 0
