"""Derive stable feature metadata (ids + anchors) from a validated DesignSpec.

Deterministic: the same spec always yields the same ids, so a circle/lasso
selection of e.g. ``hole_2`` remains valid after parameter edits. Anchors are in
CadQuery model space (mm, Z-up).
"""
from __future__ import annotations

import math

from app.schemas.design_spec import DesignSpec
from app.schemas.feature_spec import FeatureSpec

_PLATE_TYPES = {"rectangular_bracket", "adapter_plate", "drill_jig"}


def _mm(spec: DesignSpec, key: str, default: float = 0.0) -> float:
    return spec.to_mm(spec.dimensions[key]) if key in spec.dimensions else default


def extract_features(spec: DesignSpec, bbox: dict | None = None) -> list[FeatureSpec]:
    feats: list[FeatureSpec] = []
    ot = spec.object_type

    # Feature-graph designs have no template features; expose just the body.
    if ot == "feature_graph":
        return [FeatureSpec(id="body", type="body", label="Whole body", anchor=(0, 0, 0))]

    # Holes are common to many templates.
    top_z = _plate_top_z(spec)
    for i, h in enumerate(spec.holes):
        feats.append(
            FeatureSpec(
                id=f"hole_{i}",
                type="hole",
                label=f"Hole {i + 1} (ø{spec.to_mm(h.diameter):.1f})",
                anchor=(spec.to_mm(h.x), spec.to_mm(h.y), top_z),
                meta={"diameter_mm": spec.to_mm(h.diameter), "hole_type": h.hole_type},
            )
        )

    # Named faces from the bounding box (when available).
    if bbox:
        hx, hy, hz = bbox["x"] / 2, bbox["y"] / 2, bbox["z"] / 2
        for fid, label, anchor in [
            ("face_top", "Top face", (0, 0, hz)),
            ("face_bottom", "Bottom face", (0, 0, -hz)),
            ("face_+X", "+X face", (hx, 0, 0)),
            ("face_-X", "-X face", (-hx, 0, 0)),
            ("face_+Y", "+Y face", (0, hy, 0)),
            ("face_-Y", "-Y face", (0, -hy, 0)),
        ]:
            feats.append(FeatureSpec(id=fid, type="face", label=label, anchor=anchor))
        feats.append(
            FeatureSpec(id="edge_top", type="edge", label="Top perimeter edge",
                        anchor=(hx, hy, hz))
        )

    # Template-specific features.
    if ot == "enclosure":
        iw, idp = _mm(spec, "width", 80), _mm(spec, "depth", 60)
        boss = _mm(spec, "boss_diameter", 7)
        if boss > 0:
            inset = boss / 2 + 0.5
            px, py = iw / 2 - inset, idp / 2 - inset
            for j, (sx, sy) in enumerate([(px, py), (-px, py), (px, -py), (-px, -py)]):
                feats.append(FeatureSpec(id=f"boss_{j}", type="boss",
                                         label=f"Screw boss {j + 1}", anchor=(sx, sy, 0)))
        vents = int(_mm(spec, "vent_count", 0))
        for k in range(vents):
            feats.append(FeatureSpec(id=f"vent_{k}", type="vent", label=f"Vent {k + 1}",
                                     anchor=(0, idp / 2, 0)))
        feats.append(FeatureSpec(id="wall_+Y", type="face", label="Front wall (+Y)",
                                 anchor=(0, idp / 2, 0)))

    elif ot == "flanged_pipe_branch":
        ml = _mm(spec, "main_pipe_length_mm", 200)
        fod = _mm(spec, "flange_outer_diameter_mm", 120)
        feats.append(FeatureSpec(id="flange_main_front", type="flange",
                                 label="Main flange (front)", anchor=(-ml / 2, 0, 0)))
        feats.append(FeatureSpec(id="flange_main_rear", type="flange",
                                 label="Main flange (rear)", anchor=(ml / 2, 0, 0)))
        feats.append(FeatureSpec(id="flange_branch", type="flange",
                                 label="Branch flange", anchor=(0, 0, _mm(spec, "branch_pipe_length_mm", 90))))
        feats.append(FeatureSpec(id="bolt_pattern_main", type="bolt_pattern",
                                 label="Main flange bolt circle",
                                 anchor=(-ml / 2, 0, fod / 3),
                                 meta={"bolt_count": int(_mm(spec, "bolt_count", 8))}))
        feats.append(FeatureSpec(id="pipe_main", type="body", label="Main pipe body",
                                 anchor=(0, 0, 0)))
        feats.append(FeatureSpec(id="pipe_branch", type="body", label="Branch pipe",
                                 anchor=(0, 0, 40)))

    elif ot == "inline_4_crankshaft":
        # Approximate journal centers along X (post-rotation orientation).
        for i in range(5):
            feats.append(FeatureSpec(id=f"journal_main_{i}", type="journal",
                                     label=f"Main journal {i + 1}", anchor=(0, 0, 0)))
        for i in range(4):
            feats.append(FeatureSpec(id=f"journal_rod_{i}", type="journal",
                                     label=f"Rod journal {i + 1}", anchor=(0, 0, 0)))
        for i in range(8):
            feats.append(FeatureSpec(id=f"web_{i}", type="web", label=f"Web {i + 1}",
                                     anchor=(0, 0, 0)))
        feats.append(FeatureSpec(id="snout", type="body", label="Front keyed snout",
                                 anchor=(0, 0, 0)))
        feats.append(FeatureSpec(id="flange_flywheel", type="flange",
                                 label="Flywheel flange", anchor=(0, 0, 0),
                                 meta={"bolt_count": int(_mm(spec, "flywheel_bolt_count", 6))}))
        feats.append(FeatureSpec(id="bolt_pattern_flywheel", type="bolt_pattern",
                                 label="Flywheel bolt circle", anchor=(0, 0, 0)))

    if ot in _PLATE_TYPES and "center_bore" in spec.dimensions:
        feats.append(FeatureSpec(id="center_bore", type="hole", label="Center bore",
                                 anchor=(0, 0, top_z)))

    feats.append(FeatureSpec(id="body", type="body", label="Whole body", anchor=(0, 0, 0)))
    return feats


def _plate_top_z(spec: DesignSpec) -> float:
    for key in ("thickness", "wall_thickness"):
        if key in spec.dimensions:
            return spec.to_mm(spec.dimensions[key]) / 2.0
    return 0.0


def feature_ids(spec: DesignSpec, bbox: dict | None = None) -> set[str]:
    return {f.id for f in extract_features(spec, bbox)}
