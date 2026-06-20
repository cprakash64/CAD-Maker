"""v0.5-GEN2 visual-semantic QA: verify ACTUAL geometry, not self-reported metadata.

These fail when "hole-cut" operations don't visibly affect the geometry.
"""
import pytest

from app.generation.cad_programs import generate_program
from app.generation.code_sandbox import run_program
from app.generation.compiler import compile_prompt
from app.generation.mesh_analysis import analyze_stl
from app.generation.semantic_verifier import verify
from app.llm.mock_provider import MockLLMProvider
from app.schemas.brief import BriefHole, CADDesignBrief, CADProgramSpec

PLAIN = "result = cq.Workplane('XY').circle(30).extrude(8)\nmeta = {}"
HOLED = (
    "part = cq.Workplane('XY').circle(30).extrude(8)\n"
    "for i in range(4):\n"
    "    a = math.radians(90*i)\n"
    "    part = part.cut(cq.Workplane('XY').circle(3).extrude(10)"
    ".translate((18*math.cos(a), 18*math.sin(a), -1)))\n"
    "result = part\nmeta = {}"
)


# --- genus = real through-hole count --------------------------------------
def test_genus_zero_for_plain_solid():
    stl, _, _ = run_program(PLAIN, trusted=True)
    assert analyze_stl(stl).through_holes == 0


def test_genus_counts_actual_holes():
    stl, _, _ = run_program(HOLED, trusted=True)
    assert analyze_stl(stl).through_holes == 4


# --- verifier rejects metadata that lies about holes ----------------------
def test_verifier_fails_when_holes_claimed_but_not_cut():
    stl, _, _ = run_program(PLAIN, trusted=True)  # a plain disk, zero holes
    stats = analyze_stl(stl)
    brief = CADDesignBrief(
        object_type="flange_plate", object_family="flange_plate", bores=[40],
        holes=[BriefHole(count=8, pattern="bolt_circle", bolt_circle_diameter_mm=100)],
        required_features=["bolt_circle", "center_bore"],
    )
    # Metadata CLAIMS 8 holes — but the geometry has none.
    meta = {"object_type": "flange_plate", "solid_count": 1, "holes": 8,
            "feature_counts": {"holes": 8}}
    report = verify(brief, meta, stats.bbox, mesh=stats)
    assert not report.passed
    assert any(c.name == "holes_cut_through_geometry" and not c.passed for c in report.checks)


def test_verifier_passes_when_holes_actually_cut():
    stl, _, _ = run_program(HOLED, trusted=True)
    stats = analyze_stl(stl)
    brief = CADDesignBrief(
        object_type="plate", object_family="plate", bores=[],
        holes=[BriefHole(count=4, pattern="bolt_circle", bolt_circle_diameter_mm=36)],
    )
    report = verify(brief, {"object_type": "plate", "solid_count": 1, "holes": 4}, stats.bbox, mesh=stats)
    assert any(c.name == "holes_cut_through_geometry" and c.passed for c in report.checks)


# --- the real flange plate / bearing / hex gear / block, geometrically -----
def test_flange_plate_8_holes_visible_in_geometry():
    out = compile_prompt("a flange plate with 8 holes on a 100mm bolt circle", MockLLMProvider())
    assert out.ok
    stats = analyze_stl(out.result.stl_bytes)
    assert stats.through_holes >= 9  # 8 bolt holes + center bore
    assert any(c.name == "holes_cut_through_geometry" and c.passed for c in out.report.checks)


def test_bearing_housing_connected_with_bore():
    out = compile_prompt("a simple bearing housing for a 20mm shaft", MockLLMProvider())
    stats = analyze_stl(out.result.stl_bytes)
    assert out.ok and stats.components == 1 and stats.through_holes >= 1


def test_counterbored_block_single_body_two_holes():
    out = compile_prompt("a rectangular block with a stepped slot and two counterbored holes",
                         MockLLMProvider())
    stats = analyze_stl(out.result.stl_bytes)
    assert out.ok and stats.components == 1 and stats.through_holes >= 2


def test_hex_gear_is_not_a_circular_disk():
    out = compile_prompt("a hexagonal gear with a 10mm shaft", MockLLMProvider())
    stats = analyze_stl(out.result.stl_bytes)
    assert out.ok and stats.outer_corner_count <= 8  # hexagon, not a smooth circle
    assert any(c.name == "not_plain_disk" and c.passed for c in out.report.checks)


# --- a faked plain cylinder is never accepted -----------------------------
class _FakeFlange(MockLLMProvider):
    name = "mock"

    def cad_program(self, prompt, feedback=None):
        brief = CADDesignBrief(
            object_type="flange_plate", object_family="flange_plate", bores=[40],
            holes=[BriefHole(count=8, pattern="bolt_circle", bolt_circle_diameter_mm=100, diameter_mm=11)],
            required_features=["bolt_circle", "center_bore"])
        code = ("od=145.0\nth=12.0\nresult=cq.Workplane('XY').circle(od/2).extrude(th)\n"
                "meta={'object_type':'flange_plate','solid_count':1,'holes':8,"
                "'feature_counts':{'holes':8}}\n")
        return brief, CADProgramSpec(generated_code=code)


def test_faked_plain_cylinder_is_rejected_after_repairs():
    out = compile_prompt("flange plate 8 holes 100mm bolt circle", _FakeFlange(), max_repairs=2)
    assert out is not None and not out.ok  # never passes — geometry has no holes
    assert out.repair_attempts == 2
