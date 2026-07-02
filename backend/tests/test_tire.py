"""Tire / rim / wheel-assembly families — finished geometry, never a car assembly."""
from __future__ import annotations

import pytest


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _detail(d):
    return d.get("part_family_detail") or {}


def _fc(d):
    return d.get("feature_contract") or {}


# === 1: rubber-only tire, "threads" typo -> Treaded tire, hollow, REVIEW =====
def test_tire_rubber_only_threads_typo(client, auth):
    d = _create(
        client, auth,
        "Create a tire of 100 mm diameter with the threads only. Don't include rim or "
        "other details. I only want the rubber part of the tire designed.")
    assert d["object_type"] == "tire"
    assert d["route"] == "part_family_tire"
    assert d["title"] == "Treaded tire"
    assert not d.get("needs_clarification") and not d.get("needs_decomposition")
    bb = d["bounding_box_mm"]
    assert max(bb["x"], bb["y"]) == pytest.approx(100.0, abs=1.5)
    det = _detail(d)
    assert det["rim_included"] is False and det["hollow"] is True
    assert det["tread_generated"] is True
    assert det["id_source"] == "assumed" and det["width_source"] == "assumed"
    assert d["validation_status"] == "warning"        # assumed ID/width -> REVIEW
    fc = _fc(d)
    assert "center_opening" in fc["generated_features"]
    assert "tread_pattern" in fc["generated_features"]


# === 2: off-road tread, no rim, full dims -> PASS ===========================
def test_offroad_tire_full_dims(client, auth):
    d = _create(client, auth,
                "Make a 100 mm OD rubber tyre, 60 mm ID, 30 mm wide, with off-road tread "
                "blocks and no rim")
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["tread_style"] == "off_road"
    assert det["rim_included"] is False and det["hollow"] is True
    assert d["validation_status"] == "pass"


# === 3: smooth racing slick, no block tread -> PASS ========================
def test_smooth_slick_tire(client, auth):
    d = _create(client, auth,
                "Make a smooth racing slick tire 100 mm OD, 60 mm ID, 30 mm wide, no rim")
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["tread_generated"] is False and det["tread_style"] == "slick"
    assert d["title"] == "Slick tire"
    assert d["validation_status"] == "pass"
    assert "tread_pattern" not in _fc(d)["requested_features"]


# === 4: city street, circumferential grooves ===============================
def test_city_street_tire(client, auth):
    d = _create(client, auth,
                "Make a city street tire 120 mm OD, 70 mm ID, 35 mm wide with "
                "circumferential grooves and no rim")
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["tread_style"] == "street"
    assert det["outer_diameter_mm"] == 120 and det["rim_included"] is False


# === 5: wheel rim only ======================================================
def test_wheel_rim_only(client, auth):
    d = _create(client, auth, "Make a 100 mm wheel rim with 5 spokes and 20 mm center bore")
    assert d["object_type"] == "rim"
    det = _detail(d)
    assert det["tire_included"] is False
    assert det["spoke_style"] == "5-spoke"
    assert det["center_bore_mm"] == 20
    fc = _fc(d)
    assert "no_tire" in fc["generated_features"] and "spokes" in fc["generated_features"]


# === 6: off-road tire with a rim -> wheel assembly ==========================
def test_offroad_tire_with_rim_assembly(client, auth):
    d = _create(client, auth,
                "Make a 100 mm OD off-road tire with a 5 spoke rim, 60 mm tire ID, 30 mm wide")
    assert d["object_type"] == "wheel_assembly"
    det = _detail(d)
    assert det["tire_included"] is True and det["rim_included"] is True
    assert det["tread_style"] == "off_road"
    fc = _fc(d)
    assert "rim_barrel" in fc["generated_features"] and "tread_pattern" in fc["generated_features"]


# === 7: car wheel assembly -> wheel_assembly, not a full-vehicle assembly ====
def test_car_wheel_assembly_not_vehicle(client, auth):
    d = _create(client, auth, "Make a car wheel assembly with tire and rim")
    assert d["object_type"] == "wheel_assembly"
    assert not d.get("needs_decomposition")            # never a whole-vehicle decompose
    assert d["validation_status"] != "pass"            # missing dims -> REVIEW


# === 8: tire tread ring -> tire, not a screw thread =========================
def test_tire_tread_ring(client, auth):
    d = _create(client, auth, "Make a tire tread ring 100 mm diameter")
    assert d["object_type"] == "tire"
    assert d["object_type"] not in ("bolt", "threaded_rod")
    assert _detail(d)["tread_generated"] is True


# === Routing priority regressions (the exact prompts the user reported) =======
# A "wheel assembly ... with a matching rim" prompt has no literal word "tire", yet
# must build the FULL wheel — it must never collapse to rim-only.
def test_offroad_wheel_assembly_matching_rim_routes_to_assembly(client, auth):
    d = _create(client, auth,
                "Create an off-road wheel assembly with aggressive tread and a matching rim.")
    assert d["object_type"] == "wheel_assembly"
    assert d["route"] == "part_family_wheel_assembly"
    det = _detail(d)
    assert det["tire_included"] is True and det["rim_included"] is True
    assert det["tread_style"] == "off_road"          # aggressive tread family
    assert d["validation_status"] != "pass"              # dims assumed -> REVIEW


def test_wheel_with_tire_and_rim_routes_to_assembly(client, auth):
    d = _create(client, auth, "Make a wheel with a tire and rim, outer diameter 120 mm.")
    assert d["object_type"] == "wheel_assembly"
    assert _detail(d)["tire_included"] is True and _detail(d)["rim_included"] is True


def test_offroad_rim_only_routes_to_rim(client, auth):
    d = _create(client, auth, "Create an off-road rim only.")
    assert d["object_type"] == "rim"
    assert _detail(d)["tire_included"] is False


def test_just_the_rim_routes_to_rim(client, auth):
    d = _create(client, auth, "Make just the rim.")
    assert d["object_type"] == "rim"
    assert _detail(d)["tire_included"] is False


def test_offroad_tire_no_rim_routes_to_tire(client, auth):
    d = _create(client, auth,
                "Make a 100 mm off-road tire, 60 mm inner diameter, 30 mm wide, no rim.")
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["rim_included"] is False and det["hollow"] is True
    assert det["tread_style"] == "off_road"
    assert d["validation_status"] == "pass"              # OD/ID/width all given


def test_threads_typo_routes_to_treaded_tire(client, auth):
    d = _create(client, auth, "Make a tire with threads only.")
    assert d["object_type"] == "tire"                    # not a threaded rod / bolt
    assert _detail(d)["tread_generated"] is True         # "threads" read as tread


# === The exact acceptance-criteria prompts (routing + intent) ================
def test_acceptance_offroad_wheel_assembly_matching_rim(client, auth):
    d = _create(client, auth,
                "Create an off-road wheel assembly with aggressive tread and a matching rim.")
    assert d["object_type"] == "wheel_assembly"          # never rim-only, never tire-only
    det = _detail(d)
    assert det["tire_included"] is True and det["rim_included"] is True
    assert det["tread_style"] == "off_road"


def test_acceptance_tire_threads_only_no_rim(client, auth):
    d = _create(client, auth, "Make a tire with threads only, no rim.")
    assert d["object_type"] == "tire"                    # tire only, no rim/assembly
    det = _detail(d)
    assert det["rim_included"] is False and det["tread_generated"] is True


def test_acceptance_aggressive_tire_full_dims(client, auth):
    d = _create(client, auth,
                "Create a 100 mm diameter rubber tire, 60 mm inner diameter, 30 mm "
                "wide, aggressive tread.")
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["outer_diameter_mm"] == 100 and det["inner_diameter_mm"] == 60
    assert det["width_mm"] == 30 and det["tread_style"] == "off_road"  # aggressive->off-road
    assert d["validation_status"] == "pass"              # all dims given, real geometry


def test_acceptance_rc_car_tire_with_matching_rim(client, auth):
    d = _create(client, auth,
                "Create an RC car tire with chunky off-road tread and matching rim.")
    assert d["object_type"] == "wheel_assembly"          # "matching rim" -> full wheel
    det = _detail(d)
    assert det["tire_included"] is True and det["rim_included"] is True
    assert det["tread_style"] == "off_road"


# === Tread-style DEFAULTS: a generic tyre is a normal STREET tyre, never off-road ==
@pytest.mark.parametrize("prompt", [
    "Make a tire",
    "Create a 100 mm tire",
    "Create a rubber tire, 100 mm outer diameter, 60 mm inner diameter, 30 mm wide",
    "make a 100 mm tire",
])
def test_generic_tire_defaults_to_street(client, auth, prompt):
    d = _create(client, auth, prompt)
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["tread_style"] == "street"                 # NOT off-road / aggressive
    assert det["tread_style_source"] == "assumed"         # surfaced as an assumption
    assert det["tread_style_label"] == "Street"


@pytest.mark.parametrize("prompt", [
    "Create a wheel with tire and rim",
    "Create a complete wheel assembly with a matching rim",
    "Make a car wheel assembly with tire and rim",
])
def test_generic_wheel_assembly_defaults_to_street(client, auth, prompt):
    d = _create(client, auth, prompt)
    assert d["object_type"] == "wheel_assembly"
    det = _detail(d)
    assert det["tire_included"] is True and det["rim_included"] is True
    assert det["tread_style"] == "street"


# === Explicit tread-style selection (the 4 canonical styles) ================
@pytest.mark.parametrize("prompt,style", [
    ("Create a slick racing tire, 100 mm outer diameter, 60 mm inner diameter, 30 mm wide", "slick"),
    ("Create a smooth tire with no tread, 100 mm outer diameter", "slick"),
    ("Create a street tire, 100 mm outer diameter, 60 mm inner diameter, 28 mm wide", "street"),
    ("Create an all-terrain tire, 100 mm outer diameter, 60 mm inner diameter, 32 mm wide", "all_terrain"),
    ("Create an off-road tire with aggressive chunky tread, 100 mm OD, 60 mm ID, 30 mm wide", "off_road"),
    ("Create an RC crawler tire with deep lugs", "off_road"),
    ("Create a mud-terrain tire, 100 mm OD, 60 mm ID, 30 mm wide", "off_road"),
    ("Create a trail tire, 100 mm OD, 60 mm ID, 30 mm wide", "all_terrain"),
])
def test_explicit_tread_style_selection(client, auth, prompt, style):
    d = _create(client, auth, prompt)
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["tread_style"] == style
    assert det["tread_style_source"] == "explicit"


def test_threads_only_full_dims_defaults_to_street_tire(client, auth):
    """A typo'd 'threads' (no aggressive words) is read as tread, but must default to
    STREET — not off-road — and stay a rim-less tyre."""
    d = _create(client, auth,
                "Create a tire with threads only, no rim, 100 mm outer diameter, "
                "60 mm inner diameter, 30 mm wide")
    assert d["object_type"] == "tire"
    det = _detail(d)
    assert det["rim_included"] is False
    assert det["tread_style"] == "street" and det["tread_generated"] is True


def test_off_road_wheel_assembly_full_spec(client, auth):
    d = _create(client, auth,
                "Create a complete off-road wheel assembly with a 100 mm tire, 60 mm "
                "rim seat, 30 mm width, chunky staggered tread, and a matching 5-spoke rim.")
    assert d["object_type"] == "wheel_assembly"
    det = _detail(d)
    assert det["tire_included"] is True and det["rim_included"] is True
    assert det["tread_style"] == "off_road"


def test_street_wheel_assembly_full_spec(client, auth):
    d = _create(client, auth,
                "Create a street wheel assembly with a 110 mm tire, 65 mm rim diameter, "
                "30 mm width, shallow circumferential grooves, and a sport 6-spoke rim.")
    assert d["object_type"] == "wheel_assembly"
    det = _detail(d)
    assert det["tire_included"] is True and det["rim_included"] is True
    assert det["tread_style"] == "street"


def test_rim_only_full_spec_stays_rim(client, auth):
    d = _create(client, auth, "Create a 60 mm wheel rim only with 5 spokes and a center bore.")
    assert d["object_type"] == "rim"
    assert _detail(d)["tire_included"] is False


# === Direct geometry regression guards (build the template, inspect the solid) ==
# These lock in the redesigned deep-cavity C-section so a future edit can't silently
# regress the tire back to a solid/blocky/closed-bore ring.
import math

import pytest
from cadquery import Vector

from app.cad.templates.rim import RimTemplate
from app.cad.templates.tire import (
    TREAD_BLOCK,
    TREAD_OFFROAD,
    TREAD_SMOOTH,
    TireTemplate,
)
from app.cad.templates.tire import TREAD_RACING, TREAD_STREET
from app.cad.templates.wheel_assembly import WheelAssemblyTemplate
from app.schemas.design_spec import DesignSpec


def _spec(od=100.0, idia=60.0, width=30.0, style=TREAD_BLOCK):
    return DesignSpec(
        object_type="tire", material="rubber", manufacturing_method="cnc_milling",
        dimensions={"outer_diameter": od, "inner_diameter": idia, "width": width,
                    "tread_style_code": float(style)})


def _tire(od=100.0, idia=60.0, width=30.0, style=TREAD_BLOCK):
    return TireTemplate().build(_spec(od, idia, width, style)).val()


def _mesh_pts(solid):
    """(N,3) array of the tessellated surface vertices — for profile/tread probes."""
    import numpy as np
    verts, _ = solid.tessellate(0.15, 0.25)
    return np.array([[v.x, v.y, v.z] for v in verts])


def _outer_radius(pts, z0, z1, r_floor):
    """Max outer-surface radius in the axial band [z0, z1] (the ``r_floor`` filter
    drops the inner cavity liner so only the outer wall is measured)."""
    import numpy as np
    r = np.hypot(pts[:, 0], pts[:, 1])
    m = (pts[:, 2] >= z0) & (pts[:, 2] <= z1) & (r > r_floor)
    return float(r[m].max()) if m.any() else 0.0


def _center_tread_depth(pts, ro, width):
    """Deepest groove/lug recess at the crown centre: ro - min outer-crown radius in a
    small central axial window (``r`` filtered above the cavity ceiling)."""
    import numpy as np
    r = np.hypot(pts[:, 0], pts[:, 1])
    lo, hi = width * 0.5 - 4.0, width * 0.5 + 4.0
    m = (pts[:, 2] > lo) & (pts[:, 2] < hi) & (r > ro - 4.5) & (r <= ro + 0.3)
    return ro - float(r[m].min()) if m.any() else -1.0


@pytest.mark.parametrize("style", [TREAD_SMOOTH, TREAD_OFFROAD, TREAD_BLOCK])
def test_tire_is_single_valid_solid(style):
    s = _tire(style=style)
    assert s.isValid()


def test_tire_dimensions_preserved_and_hollow():
    """OD/width are held exactly, and the body is genuinely hollow — well under a
    solid disc of the same envelope (the old shape came out near-solid)."""
    s = _tire(od=100.0, idia=60.0, width=30.0, style=TREAD_BLOCK)
    bb = s.BoundingBox()
    assert bb.xlen == pytest.approx(100.0, abs=1.0)
    assert bb.zlen == pytest.approx(30.0, abs=0.6)
    solid_disc = math.pi * 50.0 ** 2 * 30.0
    assert s.Volume() < solid_disc * 0.55        # deep cavity, not a near-solid ring


def test_tire_centre_bore_stays_open():
    """A tire-only body must be see-through at the centre and hollow inside — the
    axis and the interior cavity contain no material."""
    s = _tire(style=TREAD_BLOCK)
    assert not s.isInside(Vector(0, 0, 15), 0.05)      # centre bore open
    assert not s.isInside(Vector(38, 0, 15), 0.05)     # interior cavity is air
    # Crown rubber is present — checked on the slick tire so a lateral tread groove
    # at the sampled angle can't create a false negative.
    assert _tire(style=TREAD_SMOOTH).isInside(Vector(49, 0, 15), 0.05)


def test_tire_is_one_connected_watertight_solid():
    """The finished tire must export as ONE watertight, single-component solid — no
    enclosed void and no floating tread lug (both showed up as '2 separate bodies'
    in the disconnected-geometry validator). Guards the open C-section + lug union."""
    from app.export.exporter import generate
    from app.generation.mesh_analysis import analyze_stl
    for style in (TREAD_SMOOTH, TREAD_STREET, TREAD_BLOCK, TREAD_OFFROAD):
        s = _tire(style=style)
        assert len(s.Solids()) == 1                     # one BRep solid
        ms = analyze_stl(generate(_spec(style=style)).stl_bytes)
        assert ms.components == 1                        # one connected mesh, no floaters
        assert ms.watertight                             # printable / manifold


def test_tire_cross_section_is_not_a_flat_cylinder():
    """The outer wall must be a curved tire profile, not a washer/flat cylinder: the
    crown (axial centre) reaches the OD, the axial edges taper well inside it, and the
    crown stays broad (still ~OD a good way out from centre). The old torus/washer
    would either be a single-point peak or a constant-radius cylinder."""
    pts = _mesh_pts(_tire(style=TREAD_SMOOTH))       # slick: pure carcass, no grooves
    ro = 50.0
    crown = _outer_radius(pts, 14, 16, ro - 9)       # axial centre
    broad = _outer_radius(pts, 6, 9, ro - 9)         # ~half-way out along the tread
    edge = _outer_radius(pts, 0, 2, ro - 9)          # axial edge (sidewall)
    assert crown == pytest.approx(ro, abs=1.0)       # crown reaches the OD
    assert broad > ro - 2.0                           # crown is a BROAD band, not a spike
    assert edge < crown - 2.5                         # sidewall curves in (not vertical)
    assert edge > ro - 12.0                           # ...but still bulges out (convex)


def test_tread_depth_increases_with_aggressiveness():
    """Canonical styles ranked by tread depth on the crown: off_road (TREAD_OFFROAD) >
    all_terrain (TREAD_BLOCK) > street (TREAD_STREET) > slick (TREAD_SMOOTH ≈ 0).
    Off-road lugs must be genuinely deep (≥3 mm on a 100 mm tyre). Shape-independent."""
    ro, w = 50.0, 30.0
    depth = {st: _center_tread_depth(_mesh_pts(_tire(style=st)), ro, w)
             for st in (TREAD_SMOOTH, TREAD_STREET, TREAD_BLOCK, TREAD_OFFROAD)}
    assert depth[TREAD_SMOOTH] < 0.6                  # slick: no meaningful tread
    assert depth[TREAD_OFFROAD] >= 3.0               # aggressive lugs are deep
    # slick < street < all_terrain <= off_road
    assert depth[TREAD_SMOOTH] < depth[TREAD_STREET] < depth[TREAD_BLOCK]
    assert depth[TREAD_BLOCK] <= depth[TREAD_OFFROAD]


def test_slick_has_no_chunky_blocks_street_has_mild_tread():
    """Slick: bald rolling surface (no lugs, no grooves). Street: shallow
    CIRCUMFERENTIAL grooves — the crown between grooves stays a CONTINUOUS ring (not a
    field of alternating blocks like off-road). Probed at a between-groove axial band."""
    ro, z = 50.0, 12.5                       # z between street's circumferential grooves
    slick = _tire(style=TREAD_SMOOTH)
    street = _tire(style=TREAD_STREET)
    offroad = _tire(style=TREAD_OFFROAD)
    # slick: continuous bald crown, no tread depth
    assert _crown_coverage(slick, z, ro, probe=ro - 0.5) > 0.95
    assert _center_tread_depth(_mesh_pts(slick), ro, 30.0) < 0.6
    # street: shallow, and between grooves the crown is a solid continuous ring
    assert _center_tread_depth(_mesh_pts(street), ro, 30.0) < 3.0
    assert _crown_coverage(street, z, ro, probe=ro - 0.5) > 0.9
    # off-road at the same band is a broken-up lug field (not a continuous ring)
    assert _crown_coverage(offroad, z, ro, probe=ro - 0.5) < 0.85


def test_default_template_build_is_street_not_aggressive():
    """A tyre built with NO explicit tread code must default to street geometry —
    shallow tread, not deep off-road lugs (guards the code-3 default)."""
    spec = DesignSpec(object_type="tire", material="rubber",
                      manufacturing_method="cnc_milling",
                      dimensions={"outer_diameter": 100.0, "inner_diameter": 60.0,
                                  "width": 30.0})   # no tread_style_code
    s = TireTemplate().build(spec).val()
    assert _center_tread_depth(_mesh_pts(s), 50.0, 30.0) < 3.0    # not off-road-deep


def test_tread_sits_on_the_crown_not_the_sidewall():
    """Tread must live on the outer rolling surface: lugs reach the OD on the crown,
    while the deep axial sidewall / bulge face (past the shoulder wrap) carries NO lug
    at the OD — the reported bug pasted rectangular blocks around the flat side.
    (Probed with ``isInside``: flat lug tops don't reliably yield mesh vertices.)"""
    s = _tire(style=TREAD_BLOCK)
    ro = 50.0
    assert _crown_coverage(s, 15.0, ro) > 0.4            # lugs on the crown at the OD
    assert _crown_coverage(s, 0.5, ro) < 0.15            # nothing at the OD on the side


def _crown_coverage(solid, zc, ro, probe=None, n=48):
    """Fraction of the circumference (at axial position ``zc``) whose outer surface is
    a raised lug — probed just above the recessed groove floor. ~0 means a bald ring,
    a healthy tread band is well above 0 all the way across the width."""
    import math
    from cadquery import Vector
    r = ro - 2.0 if probe is None else probe
    return sum(solid.isInside(Vector(r * math.cos(2 * math.pi * k / n),
                                     r * math.sin(2 * math.pi * k / n), zc), 0.05)
               for k in range(n)) / n


def test_offroad_tread_is_distributed_across_the_crown_width():
    """The whole point of this fix: lugs must cross the contact patch, not cluster on
    the shoulders. Sample lug coverage at the CENTRE of the crown and at several
    positions across the width — the centre must be well-treaded (the old tire was
    BALD at z=centre, ≈0.0) and coverage must be reasonably EVEN across the width, not
    only near the sidewall edges."""
    s = _tire(style=TREAD_OFFROAD, width=30.0)
    ro = 50.0
    zs = [6.0, 9.0, 12.0, 15.0, 18.0, 21.0, 24.0]        # across the 30 mm width
    cov = {z: _crown_coverage(s, z, ro) for z in zs}
    centre = cov[15.0]
    interior = [cov[z] for z in (9.0, 12.0, 15.0, 18.0, 21.0)]
    assert centre > 0.4                                  # contact patch is treaded (not bald)
    assert min(interior) > 0.35                          # every interior band is treaded
    # coverage is EVEN, not shoulder-dominated: the centre is not a fraction of the edge
    assert centre >= 0.6 * max(cov.values())


def test_block_and_offroad_cross_center_all_terrain_finer():
    """All-terrain (block) uses smaller, more-numerous staggered lugs than off-road,
    and both must actually cover the crown centre (coverage there ≫ 0)."""
    ro = 50.0
    assert _crown_coverage(_tire(style=TREAD_OFFROAD), 15.0, ro) > 0.4
    assert _crown_coverage(_tire(style=TREAD_BLOCK), 15.0, ro) > 0.4


def test_rim_builds_and_bore_is_open():
    spec = DesignSpec(
        object_type="rim", material="aluminum", manufacturing_method="cnc_milling",
        dimensions={"rim_diameter": 60.0, "width": 25.0, "center_bore": 20.0,
                    "spoke_count": 5.0})
    s = RimTemplate().build(spec).val()
    assert s.isValid()
    assert not s.isInside(Vector(0, 0, 12.5), 0.05)    # centre bore open


def test_wheel_assembly_fuses_tire_and_rim():
    spec = DesignSpec(
        object_type="wheel_assembly", material="aluminum",
        manufacturing_method="cnc_milling",
        dimensions={"outer_diameter": 120.0, "inner_diameter": 72.0, "width": 35.0,
                    "rim_diameter": 72.0, "center_bore": 24.0, "spoke_count": 5.0,
                    "tread_style_code": float(TREAD_OFFROAD)})
    s = WheelAssemblyTemplate().build(spec).val()
    assert s.isValid()
    bb = s.BoundingBox()
    assert bb.xlen == pytest.approx(120.0, abs=1.5)    # tire OD dominates the envelope
    assert not s.isInside(Vector(0, 0, 17.5), 0.05)    # hub centre bore still open
    # The rim must be VISIBLE inside the tire (its barrel ring seats in the bore) yet
    # must NOT fill the tire's hollow cavity, and the tire crown is still solid rubber.
    assert s.isInside(Vector(35, 0, 17.5), 0.05)       # rim barrel present at the seat
    assert not s.isInside(Vector(45, 0, 17.5), 0.05)   # air gap — rim doesn't fill bore
    assert s.isInside(Vector(55, 0, 17.5), 0.05)       # tire crown rubber above the rim


def test_tire_mesh_is_smooth_not_low_poly():
    """The tire must tessellate to a SMOOTH round body, not a ~10-facet polygon.
    A low-poly revolve (the reported bug) yields only a few hundred triangles; a
    smooth 100mm tire yields thousands. Guards the export/preview tolerances."""
    from app.export.exporter import generate

    spec = DesignSpec(
        object_type="tire", material="rubber", manufacturing_method="cnc_milling",
        dimensions={"outer_diameter": 100.0, "inner_diameter": 60.0, "width": 30.0,
                    "tread_style_code": float(TREAD_SMOOTH)})
    res = generate(spec)
    # A 10-facet polygon tire is ~a few hundred triangles; a smooth one is thousands.
    assert res.preview.triangle_count > 4000
    # Round: the crown should present many distinct circumferential positions.
    xs = {round(res.preview.positions[i], 1) for i in range(0, len(res.preview.positions), 3)}
    assert len(xs) > 60                                # not a coarse polygon silhouette
