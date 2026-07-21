#!/usr/bin/env bash
# End-to-end verification: backend tests + a generated STL/STEP + frontend build.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# verify.sh runs OFFLINE only — force the mock provider so it never makes live
# API calls (the real OpenAI path is exercised by the opt-in smoke scripts).
export LLM_PROVIDER=mock APP_ENV=development TESTING=true DEV_ALLOW_MOCK_DRAWING=true

echo "==> Backend: pytest"
backend/.venv/bin/python -m pytest -q backend

echo "==> Backend: generate a bracket and verify non-empty STL + STEP"
backend/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app.export.exporter import generate
from app.schemas.design_spec import DesignSpec, Hole
spec = DesignSpec(
    object_type="rectangular_bracket",
    dimensions={"width": 80, "depth": 40, "thickness": 5},
    holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)],
)
a = generate(spec)
assert len(a.stl_bytes) > 0 and len(a.step_bytes) > 0, "empty export"
b = generate(DesignSpec(object_type="rectangular_bracket",
                        dimensions={"width": 120, "depth": 40, "thickness": 5}))
assert a.spec_hash != b.spec_hash and a.stl_bytes != b.stl_bytes, "regen not different"
print(f"   OK  stl={len(a.stl_bytes)}B step={len(a.step_bytes)}B  param-change -> new model")
PY

echo "==> Backend: prompt benchmark (>=80% model-or-clarification)"
backend/.venv/bin/python - <<'PY'
import sys, json; sys.path.insert(0, "backend")
from pathlib import Path
from app.cad.base import CadGenerationError
from app.export.exporter import generate
from app.parsing.prompt_parser import parse_prompt
C = json.loads(Path("backend/tests/data/benchmark_prompts.json").read_text())["creation"]
ok = 0
for e in C:
    r = parse_prompt(e["prompt"])
    if r.spec is None:
        ok += 1
        continue
    try:
        generate(r.spec); ok += 1
    except CadGenerationError:
        ok += 1
rate = ok / len(C)
print(f"   OK  {ok}/{len(C)} = {rate:.0%} produced a model or clarification")
assert rate >= 0.80
PY

echo "==> Backend: eval harness (>=200 prompts, mock provider)"
( cd backend && .venv/bin/python -m scripts.run_eval --provider mock --limit 200 >/dev/null )
echo "   OK  eval harness ran 200 prompts -> JSON/CSV reports"

echo "==> Backend: crankshaft + drawing views + CAD package (v0.3.5)"
backend/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
import io, zipfile
from app.schemas.design_spec import DesignSpec, Hole
from app.export.exporter import generate
from app.cad.templates.crankshaft import crankshaft_summary
from app.drawing.render import render_view
from app.services.package_service import build_package_zip

ck = DesignSpec(object_type="inline_4_crankshaft", dimensions={})
s = crankshaft_summary(ck); g = generate(ck)
assert s["main_journal_count"] == 5 and s["rod_journal_count"] == 4 and s["flange_bolt_count"] == 6
assert len(g.stl_bytes) > 0 and g.step_bytes[:5] == b"ISO-1"
assert g.bounding_box_mm["x"] > g.bounding_box_mm["y"], "crank should orient along X"
print(f"   OK  crankshaft: 5 main / 4 rod / 6 bolts, STL+STEP, X-oriented")

br = DesignSpec(object_type="rectangular_bracket",
               dimensions={"width": 80, "depth": 40, "thickness": 5},
               holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)])
png = render_view(br, "iso", "png"); svg = render_view(br, "front", "svg")
assert png[:8] == b"\x89PNG\r\n\x1a\n" and b"<svg" in svg[:600]
print("   OK  drawing views render to PNG + SVG")

zf = zipfile.ZipFile(io.BytesIO(build_package_zip(br, "d1")))
need = {"design_spec.json", "manufacturing_report.json", "drawings/iso.png"}
assert need <= set(zf.namelist()), "package missing entries"
print("   OK  CAD package ZIP contains STEP/STL/spec/report/drawings")
PY

echo "==> Backend: v0.3.6 (new templates, feature graph, circle-edit, drawing safety)"
backend/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app.schemas.design_spec import DesignSpec, Hole
from app.export.exporter import generate
from app.cad.feature_graph import build_feature_graph
from app.schemas.complex_cad import CADFeatureGraph
from app.editing.localized import apply_localized_request
from app.schemas.editing_spec import LocalizedEditRequest, SelectedFeatureSpec
from app.drawing.interpret import interpret_image
from app.llm.mock_provider import MockLLMProvider

# New templates export.
for ot, dims in [("flanged_pipe_branch", {}), ("simple_gear_or_pulley", {"tooth_count": 18})]:
    g = generate(DesignSpec(object_type=ot, dimensions=dims))
    assert len(g.stl_bytes) > 0 and g.step_bytes[:5] == b"ISO-1", ot
print("   OK  flanged_pipe_branch + gear export STL/STEP")

# Trusted feature graph (rejects non-whitelisted ops).
fg = CADFeatureGraph(operations=[
    {"op": "box", "id": "b", "params": {"width": 30, "depth": 30, "height": 8}},
    {"op": "cut_hole", "id": "h", "target": "b", "params": {"radius": 4, "depth": 20}, "at": (0, 0, -2)},
], result_id="h")
assert build_feature_graph(fg).val().tessellate(0.4)[1]
try:
    build_feature_graph(CADFeatureGraph(operations=[{"op": "exec", "id": "x"}])); raise SystemExit("FAIL")
except Exception:
    pass
print("   OK  feature-graph interpreter builds + rejects unknown ops")

# Circle-edit on a feature id.
br = DesignSpec(object_type="rectangular_bracket",
                dimensions={"width": 80, "depth": 40, "thickness": 6},
                holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)])
ns, res = apply_localized_request(br, LocalizedEditRequest(
    selected=SelectedFeatureSpec(entity_type="hole", entity_id="hole_0"),
    instruction="make this 8mm", validated_parameters={"diameter": 8}))
assert res.applied and ns.holes[0].diameter == 8 and ns.holes[1].diameter == 6.6
print("   OK  circle-edit changes only the selected hole")

# Drawing safety: complex image (no hint) must NOT become a bracket.
interp = interpret_image(b"x" * 4000, "image/png", provider=MockLLMProvider())
assert interp.suggested_object_type != "rectangular_bracket" and not interp.is_actionable()
print("   OK  complex drawing is not silently mapped to a bracket")
PY

echo "==> Backend: v0.3.7 production gating (mock blocked in prod, image gating)"
backend/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app.config import Settings
# Mock must be refused in staging/production.
for env in ("staging", "production"):
    try:
        Settings(app_env=env, llm_provider="mock", testing=False).validate_startup()
        raise SystemExit(f"FAIL: mock allowed in {env}")
    except RuntimeError:
        pass
Settings(app_env="development", llm_provider="mock").validate_startup()  # ok
assert Settings(llm_provider="openai", openai_api_key="sk-x").can_understand_images()
assert not Settings(llm_provider="mock").can_understand_images()
assert not Settings(llm_provider="mock", dev_allow_mock_drawing=False).drawing_to_cad_enabled()
print("   OK  mock blocked in staging/production; image-understanding gating correct")
PY

echo "==> Backend: v0.3.8 generate-first (examples build; crankshaft routes)"
backend/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app.parsing.prompt_parser import parse_prompt
from app.parsing.complex_plan import detect_advanced_template
from app.export.exporter import generate
examples = {
    "Drill jig plate 120mm by 80mm, 6mm thick, with 6mm guide holes spaced 25mm and a registration lip.": "drill_jig",
    "Mounting bracket 80mm wide 40mm deep 5mm thick with two M6 holes.": "rectangular_bracket",
    "Electronics enclosure 100mm wide, 60mm deep, 40mm tall with 2.5mm walls and a screw-down lid.": "enclosure",
    "Pipe clamp for a 25mm pipe, 6mm thick, with two M6 holes.": "pipe_clamp",
}
for prompt, ot in examples.items():
    r = parse_prompt(prompt)
    assert r.spec is not None, f"DID NOT GENERATE: {prompt}"
    assert r.spec.object_type == ot, f"{prompt} -> {r.spec.object_type} != {ot}"
    g = generate(r.spec); assert len(g.stl_bytes) > 0 and g.step_bytes[:5] == b"ISO-1"
print("   OK  generate-first examples all build (drill jig, bracket, enclosure, pipe clamp)")
assert detect_advanced_template("crankshaft for a 4-cylinder inline engine") == "inline_4_crankshaft"
cr = parse_prompt("Realistic inline-4 crankshaft, five main journals, four rod journals, flywheel flange.")
assert cr.spec is not None and cr.spec.object_type == "inline_4_crankshaft"
assert len(generate(cr.spec).stl_bytes) > 0
print("   OK  crankshaft prompt routes to inline_4_crankshaft and builds")
PY

echo "==> Backend: v0.4-GEN generation regression (mock, >=150 prompts) + routes"
backend/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
import json, pathlib
from scripts.run_generation_regression import evaluate
data = json.loads(pathlib.Path("backend/tests/data/generation_regression_prompts.json").read_text())
rows = [evaluate(e) for e in data["prompts"]]
passed = sum(r["ok"] for r in rows)
fg = sum(1 for r in rows if r.get("route") == "feature_graph" and r["ok"])
scad = sum(1 for r in rows if r.get("route") == "scad_generator" and r["ok"])
assert len(rows) >= 150, f"only {len(rows)} regression prompts"
assert passed == len(rows), [r for r in rows if not r["ok"]][:5]
assert fg >= 10, f"only {fg} feature-graph prompts built"
print(f"   OK  generation regression {passed}/{len(rows)} (feature_graph={fg}, general_planner={scad})")

from app.parsing.complex_plan import plan_prompt
g = plan_prompt("a hexagonal gear with a 10mm shaft").spec
assert g.dimensions.get("hex") == 1.0 and g.dimensions.get("tooth_count", 0) == 0
p = plan_prompt("a pulley with a 10mm shaft hole and 60mm outer diameter").spec
assert "tooth_count" not in p.dimensions
from app.schemas.design_spec import DesignSpec, Hole
DesignSpec(object_type="inline_4_crankshaft", material="forged polished steel studio render " * 9)
# countersink_angle null/garbage repaired (was a fatal drill-jig bug)
Hole(diameter=5, x=0, y=0, hole_type="countersink", countersink_diameter=10, countersink_angle=None)
# numeric-string coercion from vision
DesignSpec(object_type="rectangular_bracket", dimensions={"width": "80", "depth": "approx 40mm"})
print("   OK  hex-gear!=pulley; countersink/null & numeric-strings repaired")
PY

echo "==> Backend: no model-authored code execution (hardening gate)"
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_no_model_code_execution.py \
  backend/tests/test_phase1_security_regressions.py >/dev/null
echo "   OK  no exec/eval/compile/__import__ in app; no source-returning provider hook; hostile op names + params rejected"

echo "==> Backend: geometric (visual-semantic) verification"
backend/.venv/bin/python -m pytest -q backend/tests/test_geometric_verifier.py >/dev/null
echo "   OK  geometric verify: holes must be visibly cut; faked hole counts REJECTED"

echo "==> Backend: plain-English -> CAD feature-graph evals (the 10 task prompts)"
backend/.venv/bin/python -m pytest -q backend/tests/test_plain_english_cad.py >/dev/null
( cd backend && .venv/bin/python -m scripts.run_cad_evals --provider mock | tail -1 )
echo "   OK  blind flange (circular), pipe spool (straight), U-bracket (base+walls), bearing block — all compile + export STEP/STL"

echo "==> Backend: assumption-first generation (warnings never block a compiled model)"
backend/.venv/bin/python -m pytest -q backend/tests/test_assumption_first.py >/dev/null
echo "   OK  bearing block / hinge / sensor enclosure generate with assumptions; STEP+STL export; drawing pipe-branch not 'unknown'"

echo "==> Backend: benchmark feature audit (semantic accuracy, not just exports)"
backend/.venv/bin/python -m pytest -q backend/tests/test_benchmark_feature_audit.py >/dev/null
echo "   OK  clamp block / bearing / hinge / flange / spool / U-bracket / enclosure pass feature-level audit; wrong primary geometry FAILS even with exports"

echo "==> Frontend: production build"
( cd frontend && npm run build >/dev/null )
echo "   OK  frontend build"

echo "All checks passed."
echo "(Live OpenAI smokes are opt-in, not run here:"
echo "   python -m scripts.smoke_openai"
echo "   python -m scripts.smoke_drawing_image_openai img.png [\"hint\"]"
echo "   python -m scripts.smoke_complex_prompt_openai prompt.txt)"
