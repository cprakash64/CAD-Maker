"""v0.5-GEN2: CAD compiler — sandbox, semantic verifier, repair loop, integration."""
import pytest

from app.cad.base import CadGenerationError
from app.generation.cad_programs import generate_program
from app.generation.code_sandbox import lint_code, run_program
from app.generation.compiler import compile_prompt
from app.generation.semantic_verifier import verify
from app.llm.mock_provider import MockLLMProvider
from app.schemas.brief import BriefHole, CADDesignBrief, CADProgramSpec


# --- AST lint (safety) ----------------------------------------------------
@pytest.mark.parametrize("bad", [
    "import os", "import subprocess", "open('/etc/passwd')", "__import__('os')",
    "exec('x=1')", "eval('1')", "x = os.system('ls')", "y = (1).__class__.__bases__",
    "from sys import exit", "getattr(cq, 'x')",
])
def test_lint_rejects_unsafe(bad):
    with pytest.raises(CadGenerationError):
        lint_code(bad)


def test_lint_allows_safe_cadquery():
    lint_code("import math\nr = cq.Workplane('XY').box(1,1,1)\nresult = r\nmeta = {}")


# --- semantic verifier ----------------------------------------------------
def test_verifier_flags_wrong_hole_count():
    brief = CADDesignBrief(object_type="flange_plate", object_family="flange_plate",
                           holes=[BriefHole(count=8, pattern="bolt_circle", bolt_circle_diameter_mm=100)])
    meta = {"object_type": "flange_plate", "solid_count": 1, "holes": 3,
            "feature_counts": {"holes": 3}, "dimensions": {"x": 140, "y": 140, "z": 12}}
    report = verify(brief, meta, {"x": 140, "y": 140, "z": 12})
    assert not report.passed
    assert any(c.name == "hole_count" and not c.passed for c in report.checks)


def test_verifier_flags_disconnected_bodies():
    brief = CADDesignBrief(object_type="block", object_family="block")
    meta = {"object_type": "block", "solid_count": 3, "dimensions": {"x": 50, "y": 50, "z": 20}}
    report = verify(brief, meta, {"x": 50, "y": 50, "z": 20})
    assert not report.passed
    assert any(c.name == "single_connected_body" and not c.passed for c in report.checks)


# --- compiler: the success-criteria families ------------------------------
SUCCESS = [
    ("a simple bearing housing for a 20mm shaft", "bearing"),
    ("a rectangular block with a stepped slot and two counterbored holes", "block"),
    ("a hexagonal spacer with a 6mm through hole", "hex"),
    ("a shaft collar with an M6 clamp screw", "collar"),
    ("a flange plate with 8 holes on a 100mm bolt circle", "flange"),
    ("a pulley with a 10mm shaft hole and 60mm outer diameter", "pulley"),
    ("a hexagonal gear with a 10mm shaft", "hex"),
    ("a 90 degree pipe elbow with circular flanges", "elbow"),
    ("a small vise jaw with two mounting holes and a V groove", "vise"),
    ("a motor mounting plate for a NEMA 17 stepper", "motor"),
    ("spur gear with 32 teeth and 8mm bore", "gear"),
]


@pytest.mark.parametrize("prompt,family_token", SUCCESS)
def test_compiler_generates_and_semantically_passes(prompt, family_token):
    out = compile_prompt(prompt, MockLLMProvider())
    assert out is not None and out.ok, f"{prompt!r} did not compile"
    assert out.report.passed, out.report.summary()
    assert len(out.result.stl_bytes) > 0 and out.result.step_bytes[:5] == b"ISO-1"
    assert family_token in out.brief.object_family
    assert out.result.preview.triangle_count > 50  # real geometry


def test_flange_plate_has_8_holes_and_pcd():
    out = compile_prompt("a flange plate with 8 holes on a 100mm bolt circle", MockLLMProvider())
    assert out.ok
    assert any(c.name == "bolt_circle_pattern" and c.passed for c in out.report.checks)


def test_hex_gear_is_hex_not_disk():
    out = compile_prompt("a hexagonal gear with a 10mm shaft", MockLLMProvider())
    assert out.ok and out.brief.object_family == "hexagonal_gear"


def test_bearing_housing_is_single_connected_body():
    out = compile_prompt("a simple bearing housing for a 20mm shaft", MockLLMProvider())
    assert out.ok
    assert any(c.name == "single_connected_body" and c.passed for c in out.report.checks)


# --- repair loop ----------------------------------------------------------
class _RepairProvider(MockLLMProvider):
    """First program drops the bolt holes; the second (after feedback) fixes it."""
    name = "mock"  # trusted in-process for speed

    def cad_program(self, prompt, feedback=None):
        brief = CADDesignBrief(object_type="flange_plate", object_family="flange_plate",
                               holes=[BriefHole(count=8, pattern="bolt_circle",
                                                bolt_circle_diameter_mm=100, diameter_mm=11)],
                               required_features=["bolt_circle"])
        if feedback is None:
            code = ("od=145.0\nth=12.0\n"
                    "result = cq.Workplane('XY').circle(od/2).extrude(th)\n"
                    "meta = {'object_type':'flange_plate','solid_count':1,'holes':0,"
                    "'feature_counts':{'holes':0}}\n")
        else:
            code = ("n=8\npcd=100.0\nbd=11.0\nod=145.0\nth=12.0\n"
                    "part = cq.Workplane('XY').circle(od/2).extrude(th)\n"
                    "tools = cq.Workplane('XY')\n"
                    "for i in range(n):\n"
                    "    a = math.radians(360.0*i/n)\n"
                    "    tools = tools.union(cq.Workplane('XY').circle(bd/2).extrude(th+2)"
                    ".translate((pcd/2*math.cos(a), pcd/2*math.sin(a), -1)))\n"
                    "result = part.cut(tools)\n"
                    "meta = {'object_type':'flange_plate','solid_count':1,'holes':8,"
                    "'feature_counts':{'holes':8}}\n")
        return brief, CADProgramSpec(generated_code=code)


def test_repair_loop_fixes_missing_holes():
    out = compile_prompt("flange plate 8 holes 100mm bolt circle", _RepairProvider())
    assert out.ok, out.report.summary() if out.report else "no report"
    assert out.repair_attempts == 1  # fixed on the first repair


# --- API integration ------------------------------------------------------
def test_create_design_uses_compiler_and_stores_program(client, auth, legacy_engine):
    r = client.post("/api/designs/create",
                    json={"prompt": "a flange plate with 8 holes on a 100mm bolt circle"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is False
    assert d["route"] == "cadquery_program"
    assert d["semantic_passed"] is True
    assert d["has_program"] is True
    assert len(d["exports"]) == 2  # STL + STEP
    assert any(c["name"] == "bolt_circle_pattern" and c["passed"] for c in d["semantic_checks"])


def test_create_design_bearing_housing_via_compiler(client, auth, legacy_engine):
    r = client.post("/api/designs/create",
                    json={"prompt": "a simple bearing housing for a 20mm shaft"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["route"] == "cadquery_program" and d["semantic_passed"] is True
    assert d["preview"]["triangle_count"] > 50


# --- subprocess sandbox (real, opt-in marker via small program) -----------
def test_subprocess_sandbox_runs_real_program():
    brief, prog = generate_program("a hexagonal spacer with a 6mm through hole")
    stl, step, meta = run_program(prog.generated_code, trusted=False, timeout=60)
    assert len(stl) > 0 and step[:5] == b"ISO-1" and meta["solid_count"] == 1
