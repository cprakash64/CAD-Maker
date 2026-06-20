# Changelog

## v0.7.4-ACCURATE — drawing-mode hard acceptance + crankshaft topology gate

- **Drawing mode never ships a wrong model.** The required-feature audit is now
  a hard gate for drawings: the planner's model (already repaired once on a
  failed audit) must pass, otherwise the design is REBUILT from a deterministic
  fallback; if that also fails, the request returns 422 "Could not generate
  accurate CAD" with the missing-feature diagnostics and the design row is
  deleted — a 6/10 audit is no longer "Generated with warnings".
- **Structured fallback builder** `app/drawing/fallback.py`:
  `DrawingPipeBranchSpec` is built from the SCALED drawing data (never a lossy
  text prompt) and compiled with the drawing's anatomy — vertical main run on
  Z with hollow bore, perpendicular branch on X with its own bore, top flange
  at +Z, bottom at −Z, branch at +X, a repeated bolt pattern on every flange;
  ids main_pipe/branch_pipe/top_flange/bottom_flange/branch_flange. Other
  detected types (spool, blind flange, brackets, bearing block, enclosure)
  fall back to the offline deterministic planner.
- **Orientation audit:** when the drawing shows a vertical main run, a
  horizontal generic tee FAILS the `main_pipe` audit item ("main run built
  horizontal — drawing shows a vertical run").
- **Crankshaft topology gate** `app/cad/topology.py` + `exporter.generate`:
  precision-template output must be a valid BRep (`Shape.isValid()`), ONE
  fused component (mesh union-find — unfused journals/webs = floating solids),
  and non-degenerate; otherwise generation raises with diagnostics ("marked
  unsupported instead of exporting broken CAD"). Current template passes
  (valid, 1 component).
- `design_service.rebuild_design_from_plan` rebuilds an existing design row
  from a deterministic CadPlan (audit re-run, repair count bumped,
  auto_repaired visible).
- Tests `tests/test_drawing_accuracy.py` (10): structured spec/plan anatomy,
  audit failures (missing bottom flange / main bore / 12-or-24-total holes /
  horizontal tee), wrong-LLM-plan rescue through the real API, no-fallback
  422, crankshaft topology, floating-solids detection.

## v0.7.3-AUTODRAW — one-shot drawing→CAD + drawing-scale inference

- **One-shot flow:** new `POST /api/drawings/generate` interprets the uploaded
  image and immediately generates when a mechanical object is recognized with
  usable confidence — no second "confirm" click. Returns
  `{generated, interpretation, design}`. The Drawing page's primary button is
  now "Generate CAD from drawing" (navigates straight to the studio);
  "Interpret only" remains for review. Non-mechanical/unreadable images return
  `generated:false` with the interpretation explaining why.
- **Drawing-scale inference** (`app/drawing/scale.py`): one consistent
  drawing→mm factor for ALL dimensions + hole callouts. Unclear units with an
  implausibly small envelope (< 30mm) are treated as cm (×10) — so 14.8 / Ø12 /
  "12xØ1" become 148mm / Ø120 / 12×Ø10, never twelve 1mm holes. Explicit inches
  convert ×25.4. Explicit mm is obeyed but warns when the result is physically
  tiny. A still-tiny hole callout on a real-size part is rescaled with an
  assumption. All inferences are visible assumptions/warnings on the design.
- **Flanged tee/branch accuracy:** `_plan_pipe_tee` now honors flange OD, main
  length/height, wall + flange thickness, and N×Ø bolt callouts ("12x 10mm
  holes per flange"), with proportional fallbacks instead of fixed defaults;
  ids `main_pipe`, `branch_pipe`, `top_flange`, `bottom_flange`,
  `branch_flange`; object_type `flanged_pipe_branch` when the prompt says
  "pipe branch". Pipe families ALWAYS build via the feature graph from
  drawings (template path skipped).
- **Position-aware audit:** flanged-tee requirements now carry stable ids
  (`main_pipe`, `branch_pipe`, `main_bore`, `branch_bore`, `{top,bottom,
  branch}_flange`, `{top,bottom,branch}_bolt_pattern`) and verify per-flange
  bolt counts (36 total for a 12xØ callout), flange OD against the drawing,
  and hollow pipes — a 12-hole-total model, a missing branch flange, or a
  generic default-size tee FAILS even though STEP/STL export fine.
- **Full-geometry prompt synthesis:** `_drawing_to_prompt` no longer collapses
  a drawing to "pipe branch, 12x1mm holes". For flanged branches/tees it emits
  the complete anatomy — vertical main run pipe with a central bore,
  perpendicular side branch pipe with its own bore, three circular flanges
  (top, bottom, branch) each carrying a repeated bolt-hole circle — plus every
  scaled dimension, the per-flange callout ("12x 10mm bolt holes per flange"),
  and a drawing-scale conversion note. The hole-callout rescue also fires when
  the envelope is UNKNOWN (callout read but no overall dims), so Ø1 never
  survives into the prompt. Round-trip tested: the deterministic planner
  rebuilds 3 flanges × 12 × Ø10 on a Ø120 flange from the prompt alone.
- Vision system prompt updated: report numbers as written (backend rescales),
  always extract N×Ø callouts as per-flange bolt circles, read wall/flange
  thickness from section views.
- `ApiError` now carries the endpoint; the Drawing page shows
  `message [status · METHOD /path]` on failures.
- Tests `tests/test_drawing_auto_generate.py` (9).

## v0.7.2-DRAW — assumption-first drawing-to-CAD

- **Root cause fixed:** `DrawingInterpretationSpec.is_actionable()` required
  zero clarification questions, zero missing critical dimensions, AND
  confidence ≥ 0.75 — so a correctly detected `flanged_pipe_branch` at 0.78
  with a missing PCD dead-ended at "Needs clarification — can't generate
  safely yet".
- New gate `generatable_with_assumptions()` (mechanical type recognized +
  confidence ≥ 0.45, `GENERATE_WITH_ASSUMPTIONS_CONFIDENCE`): open questions
  become assumptions + non-blocking warnings. Strict `is_actionable()` is
  unchanged (no-caveat path). Both serialize to the UI as `actionable` /
  `generate_with_assumptions_available`.
- `/api/drawings/confirm` now gates on the assumption-first check; a
  partially-specified template type falls through to the feature-graph engine
  instead of 422. The design carries the drawing's assumptions, a units
  assumption when the drawing didn't mark units, each open question as a
  warning ("drawing_clarification"), and route reason
  "Drawing → feature-graph CAD".
- `_drawing_to_prompt` writes dimensions value-first ("90mm main pipe outer
  diameter") so the deterministic planner actually parses them; the pipe-tee
  family's main pipe id is now `main_pipe`; the feature audit for flanged
  tees/branches requires 3 `flange_body` + a `bolt_circle`.
- Drawing page: "Generate CAD with assumptions" button whenever the detected
  object is mechanical at ≥ 0.45 confidence; the questions panel explains the
  defaults instead of blocking. Non-mechanical/low-confidence images still
  refuse (422 / disabled button).
- Tests `tests/test_drawing_assumption_first.py`: the exact field case
  (flanged_pipe_branch, 0.78, missing PCD/units) is generatable; confirm
  produces STEP+STL with `main_pipe`/`branch_pipe`/3 flanges + passing audit;
  warnings + assumptions surface; non-mechanical and 0.1-confidence still 422.

## v0.7.1-DEV — reload-safe dev server (kills the "Failed to fetch" loop)

- **Root cause:** `uvicorn --reload` was effectively watching the whole
  `backend/` tree (uvicorn always watches the CWD, even with `--reload-dir
  app`), so changes in `.venv/site-packages` restarted the server — killing
  in-flight browser requests, surfaced as `TypeError: Failed to fetch`.
- New `scripts/dev.sh` (`backend` | `frontend` | `both`): runs uvicorn with
  `--reload-dir app` plus directory excludes for `.venv`, `.pytest_cache`,
  `node_modules`, `storage_data` (generated STEP/STL), `eval_reports`,
  `reports`, `tmp`, `tests`, and glob excludes for `__pycache__`/`*.db`/
  `*.stl`/`*.step`/`*.log`. Verified empirically: touching
  `.venv/site-packages/*.py`, `storage_data/*.stl`, `cadmaker.db`, or
  `tests/*.py` triggers **zero** reloads; touching `app/*.py` reloads once;
  a real generation writing exports + SQLite triggers zero reloads.
- Uvicorn gotchas codified in the script (and README): a directory
  `--reload-exclude` only works as an **absolute path to an existing
  directory** — relative names and `".venv/*"` globs silently fail; absolute
  paths to non-existent dirs crash uvicorn's pattern glob.
- CORS dev defaults now include `http://localhost:3000/3001` and
  `http://127.0.0.1:3000/3001` out of the box (plus the dev-mode
  any-localhost-port regex from v0.7).
- New `backend/scripts/smoke_local_dev.py`: proves the browser path —
  `/health` 200, `/api/auth/me` 401-then-200, plain-English bearing-block
  generation via `POST /api/designs/create`, and non-empty STEP + STL
  downloads. Boots its own server (mock provider, temp DB) when none is
  running; targets `SOURCECAD_API` otherwise.
- README run instructions updated to use `scripts/dev.sh`.

## v0.7-AUDIT — feature-level semantic audit + clamp block + fetch diagnostics

### Feature-level audit (semantic accuracy, not just exportability)
- New `app/cad/plan/audit.py`: derives REQUIRED/FORBIDDEN canonical features
  from the *prompt* (independent of the plan) and checks them against the
  compiled feature graph + actually-cut holes. Stable feature ids: `base_plate`,
  `clamp_body`, `tube_bore`, `clamp_gap`, `tightening_bolt_holes`,
  `mounting_holes`, `bearing_boss`, `shaft_bore`, `hinge_ears`, `pin_hole`,
  `flange_body`, `bolt_circle`, `center_bore`, `pipe_body`, `branch_pipe`,
  `side_walls`, `sensor_hole`, `enclosure_body`, `v_groove`.
- Forbidden-feature checks: "no center bore" blind flange must have NO bore;
  a "straight" spool must have NO branch (a tee fails the audit). Wrong primary
  geometry now fails tests even when STEP/STL export fine.
- A failed audit triggers one LLM repair pass; the model still ships (warnings
  never block downloads), with failures surfaced in the UI feature-audit panel.
- Persisted in `semantic_json["feature_audit"]`; DTO `feature_audit` +
  `feature_audit_passed`; new collapsible "Feature audit" panel in the studio.

### Tube clamp block family
- New deterministic family `_plan_clamp_block`: flat mounting base, clamp body,
  horizontal Ø-tube bore, vertical split/clamp gap from the bore through the
  top, two tightening bolt holes crossing the gap, 4 base mounting holes — a
  mechanically meaningful clamp, never "a block on a plate".

### Canonical feature ids + hinge fix
- All deterministic planners emit stable canonical ids (was `base`/`earL`/`m0`).
- Hinge bracket ears now stand ON TOP of the base (total height = base + ears,
  e.g. 6+30=36mm) with the coaxial pin hole through both ears.
- `_count` no longer grabs a number from a distant clause ("90 degree V groove
  and 2 M6 mounting holes" yields 2, not 90).

### "TypeError: Failed to fetch" fixed
- `frontend/src/lib/api.ts` wraps network-level fetch failures into `ApiError`
  with the API base, method + endpoint, and a how-to-fix hint (backend not
  running / wrong `NEXT_PUBLIC_API_BASE`).
- Backend CORS now also accepts `localhost`/`127.0.0.1` on ANY port in
  development (`allow_origin_regex`), so a Next dev server on :3001 or a
  127.0.0.1 origin no longer produces an opaque browser fetch failure.

### Tests
- `tests/test_benchmark_feature_audit.py`: the 7 benchmark prompts (clamp block,
  bearing block, hinge bracket, blind flange, pipe spool, U bracket, sensor
  enclosure) + 4 regression families (NEMA plate, L bracket, vise jaw, adapter
  plate) run through the real API and must pass feature-level checks, dimension
  preservation, STEP+STL download, and listed assumptions; plus negative tests
  proving wrong primary geometry FAILS the audit even though it exports.

## v0.5-GEN2.1 — visual-semantic verification + New Design routing

### New Design 404
- Canonical route `/designs/new`; `/new-design` redirects to it; `/new` kept as a
  working alias. All render the shared `components/NewDesign.tsx` (component
  preserved, not removed). Navbar + dashboard link to the canonical route.
  `scripts/check-routes.mjs` + `npm run check:routes` prove the routes exist.

### Geometric (visual-semantic) verification
- The verifier no longer trusts self-reported metadata. `app/generation/
  mesh_analysis.py` welds the exported STL and derives **ground truth**:
  connected components, **genus via Euler characteristic V−E+F=2−2g** (a single
  solid's genus == its through-hole count), and an outer-profile corner count.
- `semantic_verifier.verify(..., mesh=stats)` now requires holes/bores to be
  **visibly cut** (`holes_cut_through_geometry`: genus ≥ expected, slit-adjusted),
  a single connected body (`geometric_connected_body`), and a non-circular profile
  for hex/gear (`not_plain_disk`: hex ≤8 corners / gear radial variation). A plain
  cylinder that *claims* 8 holes now fails and triggers auto-repair.
- Screenshot/thumbnail regression: `python -m scripts.render_thumbnails` writes
  shaded PNG thumbnails + `index.json` (through-holes, components, corners) for the
  10 manual prompts.
- Tests: `tests/test_v05b_geometric_verifier.py` (genus counts real holes; verifier
  rejects metadata that lies; flange shows 8 holes + bore; hex gear ≠ disk; faked
  plain cylinder rejected after repairs). verify.sh asserts flange genus≥9,
  hex≠circle, and faked-holes rejection.

## v0.5-GEN2 — general CAD compiler

Generate *semantically correct* CAD from plain English, not just any geometry.
Templates remain a mode, but broad mechanical prompts are generated as restricted,
sandbox-run CadQuery programs and validated by a semantic verifier before being
accepted — with an auto-repair loop.

### Pipeline
prompt → **CADDesignBrief** → **CADProgramSpec** (restricted code, data-only) →
**sandboxed execution** → **semantic verifier** → **repair (≤2)** → model.

### Sandbox (safety)
- `app/generation/code_sandbox.py`: AST lint (no imports except cadquery/math;
  no open/exec/eval/compile/__import__/getattr; no os/sys/subprocess/socket/…; no
  dunders; bounded loops). UNTRUSTED (LLM) code always runs in a **subprocess**
  (temp dir, timeout, captured stderr, output confined to temp dir, export size
  cap) emitting `model.stl` / `model.step` / `metadata.json`. Trusted deterministic
  generators may use a linted in-process exec (minimal builtins) for CI speed; LLM
  code never runs in-process.

### Semantic verifier
- `app/generation/semantic_verifier.py` checks the brief vs the model: object
  family, hole/bolt-circle counts, bore presence/size, **single connected body**
  (real solid count), required features, non-degenerate dims, "not a plain disk".
  Files existing is never sufficient.

### Compiler + families
- `app/generation/compiler.py` orchestrates brief→program→sandbox→verify→repair
  and returns a `GenerationResult` (preview parsed from the sandbox STL).
- `app/generation/cad_programs.py` authors correct parametric CadQuery for:
  bearing housing, counterbored+slotted block, hex spacer, shaft collar, flange
  plate (N holes on a PCD), pulley, hex gear, spur gear, 90° flanged pipe elbow,
  vise jaw, NEMA-17 motor plate. Crankshaft stays a precision template.

### Integration
- `create_design` routes compiler families through the compiler and stores the
  program design (auditable `program_code`, `semantic_json`, route, repair count,
  STL/STEP). DTO + studio show route, assumptions, **semantic check results**,
  auto-repair attempts, and STEP/STL availability. No generic "Something went wrong".

### Benchmark
- `tests/data/semantic_generation_benchmark.json` (**200 prompts**) with
  expected family/features/forbidden-modes/hole-count/bore/route. `scripts.
  run_semantic_generation_benchmark --provider {mock|openai}` (live opt-in).

### Testing
- `tests/test_v05_cad_compiler.py` (lint, verifier, 11 families, repair loop,
  subprocess sandbox, API integration) + `tests/test_semantic_benchmark.py`.

## v0.4-GEN — general CAD generation engine

Generate useful CAD from a much wider range of prompts via a routed pipeline,
while keeping the safety/validation architecture (LLMs emit only validated JSON;
geometry only via trusted templates, the trusted feature-graph interpreter, or a
sandboxed restricted SCAD DSL; no executed LLM code).

### GenerationRouter
- `app/generation/router.py` → `GenerationRoute` (precision_template |
  feature_graph | scad_generator | clarification) with confidence/reason/
  target_template/assumptions. Drives `plan_prompt`. Route is persisted and shown
  in the UI.

### Schema robustness (the manual blockers)
- `Hole.countersink_angle` now repairs `None`/`"Ø90"`/out-of-range → clamped 90
  (no longer a fatal drill-jig error); hole numeric fields coerce strings.
- New `app/schemas/coerce.py`; `DesignSpec.dimensions`, drawing
  `overall_dimensions`, and `DrawingDimensionSpec.value` coerce numeric strings
  ("14.8", "Ø12", "approx 90mm") and drop unparseable keys instead of rejecting
  the whole spec/interpretation (fixes `overall_vertical_height`).

### Feature graph v2
- Added ops: `tube`, `counterbore`, `countersink`, `slot`, `stepped_slot`,
  `union`/`subtract` aliases (still whitelist-only, range-checked, no eval).
- New builders: `flange_plate` (bolt circle), `shaft_collar` (clamp screw + slit),
  plus existing bearing housing / hex spacer / pipe elbow / stepped-slot block.

### Self-check / render-and-repair
- `app/generation/self_check.py` repairs obvious prompt↔spec mismatches once:
  gear with no teeth → 24 teeth; "hexagonal gear" → hex profile (not a disk);
  missing shaft bore → added; "N holes" with none placed → added. `auto_repaired`
  flag shown in the UI.

### Restricted SCAD generator (general planner)
- `GeneralCADPlan` schema; the LLM emits a **plan**, never code. The plan compiles
  to a trusted feature graph (CadQuery → STL **and** STEP). `app/generation/
  scad_runner.py` provides the restricted SCAD DSL: static lint (rejects
  include/use/import/surface/file/shell), sandboxed subprocess, timeout, temp-dir
  only — used when OpenSCAD is installed and a shape needs it (STL only; STEP is
  never faked). General mechanical prompts (cube/ring/block-with-hole) now build.

### Gear/pulley
- gear→teeth, pulley→groove, hexagonal→hex profile, shaft→bore; defaults 60 OD /
  12 thick / 10 bore / 24 teeth / 0.5mm fillet; assumptions explained.

### Regression benchmark
- `tests/data/generation_regression_prompts.json` now **150 prompts** with
  expected_route/expected_template/should_generate/must_have(_features)/
  must_not_have(_features)/assumptions_expected/export_expectation.
  `scripts.run_generation_regression --provider {mock|openai}` (live opt-in).

### Frontend
- Route badge (Precision template / Flexible CAD graph / SCAD generator),
  "Auto-repaired generation" badge, and an STL-only note when STEP is unavailable.
  Backend error detail already replaces "Something went wrong".

### Testing
- `tests/test_v04_general_generation.py` + the 150-prompt regression in pytest &
  `verify.sh`.

## v0.3.9 — generation engine reliability sprint

Generate usable CAD for a much wider range of prompts, not just built-in
examples. Safety architecture unchanged (LLMs emit only validated JSON; geometry
only via trusted templates or the trusted feature-graph interpreter).

### Schema over-strictness fixed
- Bumped short descriptive limits (drawing dimension `label` 64→256, section/
  view descriptions →1024, assumptions/questions →1024, rationale →2000, etc.) so
  a long label no longer invalidates the whole interpretation.
- `material` is now normalized to a short keyword (steel/aluminum/PLA/…) via a
  validator instead of being rejected; long descriptive/style text goes to the
  new `visual_notes` field (never affects geometry).
- Drawing interpretation gains a **repair pass**: over-long strings are sanitized
  and re-validated; if it still fails, a **partial** interpretation is preserved
  (type/dims/confidence) with a targeted clarification — never silent unknown/0%.

### Gear/pulley rework
- `simple_gear_or_pulley` now builds a hexagonal outer profile, a spur gear with
  teeth, OR a grooved pulley — plus center bore, optional keyway/hub. Defaults:
  60mm OD, 12mm thick, 10mm bore, 24 teeth when a gear is requested. A *gear*
  prompt never falls back to a plain pulley; "hexagonal gear" → hex profile.

### Safe feature-graph fallback (flexible CAD)
- Interpreter extended with `hex_prism`, `polygon_prism`, `rectangular_cutout`,
  `translate`, `rotate`, `mirror` (whitelist-only, range-checked, no eval).
- New `feature_graph` design type builds end-to-end (STL/STEP, preview, checks,
  features) from a validated `CADFeatureGraph`. Deterministic builders for
  bearing housing, hexagonal spacer/standoff, 90° flanged pipe elbow, and
  stepped-slot block; the OpenAI provider can emit equivalent graphs via
  structured output. Unified `plan_prompt` routing: template → feature-graph →
  clarification (decorative/unbuildable prompts still clarify).

### Long-prompt robustness
- The full inline-four crankshaft prompt routes to `inline_4_crankshaft` and
  generates; long material/style text no longer blocks it (→ visual_notes).

### Regression benchmark
- `tests/data/generation_regression_prompts.json` (**103 prompts**: examples,
  manual failures, gear/pulley, pipe/flange, machinist parts, vague-but-buildable,
  unsupported) with expected route/template/features/should_generate.
- `python -m scripts.run_generation_regression --provider {mock|openai} --limit N`
  (live OpenAI opt-in). Non-live regression runs in `verify.sh` and pytest.

### Frontend
- "Generated by flexible CAD graph" badge listing the operations; assumptions and
  generate-with-defaults surfaced; provider/validation errors shown (no generic
  "Something went wrong").

### Testing
- `tests/test_v039_reliability.py` + `tests/test_generation_regression.py`.

## v0.3.8 — generate-first reliability fix

Generate CAD whenever the prompt has enough to build a reasonable model; clarify
only when generation is unsafe, impossible, or truly ambiguous. Safety
architecture unchanged.

### Generate-first prompt-to-CAD
- New Missing-Information Policy (`app/parsing/policy.py`): only `object_type`,
  impossible dimensions, or unsupported parts are critical. `parse_prompt` is now
  generate-first — non-critical missing info (hole count/start/spacing, lip,
  fillet/chamfer, wall thickness, counterbore, bolt PCD, material, …) uses
  template defaults and is surfaced as assumptions instead of blocking.
- The drill-jig example (and bracket / enclosure / pipe-clamp examples) now
  generate; the mock also parses `120 by 80`, `spaced 25mm`, `6mm guide holes`,
  and a registration lip.
- `DesignDTO` gains `default_assumptions`, `can_generate_with_defaults`,
  `missing_required`; new `POST /api/designs/{id}/generate-with-defaults`.

### Crankshaft routing fixed
- Root cause: the advanced templates were missing from the OpenAI structured-
  output enum and the system prompt, so the model literally couldn't pick
  `inline_4_crankshaft` ("outside supported types"). Added all three advanced
  types to the enum + prompt. Added crankshaft *indicators* (main/rod journals,
  throw radius, counterweights, flywheel flange, 4-cyl inline) to
  `detect_advanced_template`; `create_design` routes advanced-indicator or long
  prompts through ComplexCADPlan. Missing bolt PCD/keyway use defaults.

### OpenAI Drawing-to-CAD image path
- `input_image` sent as a base64 data URL with `detail: high`; debug logging
  (model, mime, byte size, raw detected type) — never the API key.
- `interpret_image` surfaces real provider/parse errors (`provider_error`)
  instead of silently collapsing to "unknown / 0%", and falls back to a shared
  deterministic hint classifier so a good correction hint always generates.
- `/api/provider-status` gains `text_generation_available`,
  `image_understanding_available`, `structured_outputs_available`, `model`,
  `provider_error`.

### Frontend error visibility
- Real backend error detail replaces "Something went wrong"; clarification shows
  what's missing + "Generate with defaults" (when safe) / "Refine prompt";
  generated-with-defaults banner; drawing provider-error surfaced.

### Smoke scripts (opt-in)
- `scripts.smoke_openai_prompt "…"`, `scripts.smoke_complex_prompt_openai file`,
  `scripts.smoke_drawing_image_openai img --hint "…"` — print provider/model,
  template, confidence, assumptions, missing info, and generated file paths.

### Testing
- `tests/test_v038_generate_first.py` (policy, examples build, crankshaft routing,
  image error visibility, hint generation). **Backend: 304 tests pass.**

## v0.3.7 — production interaction & real AI fixes

Make user-facing beta flows real (no mock in production), fix the main viewport
controls, make circle-to-edit actually select features, and route Drawing-to-CAD
and long prompts through the real OpenAI provider. Safety architecture unchanged.

### Disable mock in beta/production
- `APP_ENV` (development|staging|production), `TESTING`, `DEV_ALLOW_MOCK_DRAWING`.
- `validate_startup()` aborts boot if `APP_ENV` is staging/production with
  `LLM_PROVIDER=mock` (or openai without a key); factory refuses mock when not
  allowed. Drawing-to-CAD endpoint returns 409 unless a vision provider is
  configured (or dev opt-in). New `/api/provider-status` capability endpoint.

### Real OpenAI path
- OpenAI provider (Responses API + Structured Outputs + vision) used for
  Drawing-to-CAD and long prompts when `LLM_PROVIDER=openai`; lazy import.
- Opt-in smoke scripts (NOT in verify): `scripts/smoke_openai.py`,
  `scripts/smoke_drawing_image_openai.py`, `scripts/smoke_complex_prompt_openai.py`.

### View toolbar (root cause fixed)
- `next/dynamic` dropped the ref to `Viewer3D`, so the toolbar and circle-edit
  never reached the viewer. Now `Studio3D` is the dynamic (ssr:false) boundary and
  imports `Viewer3D` directly, so refs forward. Top/Front/Right/Left/Iso/Fit move
  the main camera (explicit position/target/up + bbox fit); active state; PNG of
  the live viewport.

### Circle-to-edit (works)
- Ref fix unblocks `projectPoints`; added nearest-feature fallback, an explicit
  "No editable feature found" message, and a dev anchor-debug overlay. Removed the
  right-side **Drawing Views** panel (standard views live only in the top toolbar;
  drawing views remain in the CAD Package).

### Long / complex prompts
- Prompts > 1500 chars route through `ComplexCADPlan` in `create_design`
  (advanced template / feature-graph / unsupported→clarification), separating
  material/visual notes from engineering.

### Trust UX
- Provider-status banner + blocking state for image understanding; corrected
  wording ("extracts geometry and dimensions when possible; confirm before
  generation"); clearer errors (key missing / unavailable in mock / low
  confidence / unsupported-but-detected).

### Testing
- `tests/test_v037_production.py` (config gating, factory refusal, provider
  status, blocked mock drawing, long-prompt routing). verify.sh extended with a
  gating block. **Backend: 259 tests pass.** Frontend builds (9 routes).

## v0.3.6 — accuracy + interaction fix sprint

Pre-beta fixes for issues found in manual testing. Safety architecture intact
(LLMs emit only validated JSON; geometry from trusted templates or the trusted
feature-graph interpreter; Pydantic validation mandatory; low confidence →
clarification, never a guess).

### P0-1 View toolbar (fixed)
- Rewrote `Viewer3D` with explicit camera control (no Bounds/OrbitControls
  fighting): Top/Front/Right/Left/Iso visibly move the camera; Fit frames the
  model bounding sphere; PNG capture via `preserveDrawingBuffer`.

### P0-2 Circle-to-edit
- Stable **feature metadata** (`app/cad/features.py`): deterministic ids +
  anchors per template (holes/faces/edges/flanges/bosses/vents/webs/journals/
  bolt patterns), carried in `GenerationResult` → `DesignDTO.features`.
- Schemas: `SelectedRegionSpec`, `SelectedFeatureSpec`, `CircleSelectionSpec`,
  `LocalizedEditRequest`, `LocalizedEditResult`.
- `apply_localized_request` validates `selected_entity_id` against the model's
  features, infers the operation, edits only that feature; endpoint
  `POST /api/designs/{id}/circle-edit`. Frontend `Studio3D` overlay: draw a
  circle → project feature anchors to screen → select nearest → edit.

### P0-3 Drawing-to-CAD correctness
- Mock interpreter **never** maps an unknown complex drawing to a bracket; it
  can't read images, so it returns low confidence + clarification unless given a
  "correct interpretation" hint, which it classifies strictly (flange/pipe →
  `flanged_pipe_branch`, else `unsupported_complex_pipe_assembly`).
- Confidence threshold **0.75** gates `is_actionable`; new fields
  `detected_object_type`, `template_candidate`, `missing_critical_dimensions`,
  `drawing_units_confidence`, `view_detection_confidence`,
  `dimension_extraction_confidence`, `interpretation_rationale`.

### P0-4 Complex CAD + long prompts
- Trusted **feature-graph interpreter** (`app/cad/feature_graph.py`): builds from
  a whitelisted op list (box/cylinder/cone/sphere/extrude/revolve/cut_hole/
  patterns/booleans/fillet/chamfer) by dispatch only — no eval, range-checked.
- `ComplexCADPlan` + `CADIntentClassification` + `classify_intent`/
  `build_complex_plan`: routes simple/advanced/feature_graph/unsupported,
  separates material/visual notes from engineering. Request prompt limit raised
  to 20k chars; full >2000-word crankshaft prompt routes + exports.

### New templates
- `flanged_pipe_branch` (main pipe + side branch + bolted flanges) and
  `simple_gear_or_pulley` (spur gear or grooved pulley). Registry now 11.

### P0-5 Trust UX
- Mock-mode banners (New / Studio / Drawing pages); confidence-gated "Confirm"
  → "Needs clarification" when low; "Why this interpretation?" rationale;
  "Correct interpretation" box; template-override selector.

### Testing
- +~45 tests (templates+feature-graph, features+circle-edit, drawing safety,
  complex plan). verify.sh extended; `docs/QA_CHECKLIST.md` added. Live OpenAI
  vision excluded from verify (`scripts/smoke_drawing_image_openai.py`).

## v0.3.5 — drawing intelligence, view control, localized editing

Pre-beta feature round. Safety architecture preserved: LLMs emit only validated
JSON (DesignSpec / DesignModification / DrawingInterpretationSpec /
LocalizedModificationSpec); geometry is built only by trusted local templates.

### View system (WS1) + 2D drawing views (WS2)
- Studio **view toolbar**: top / front / right / left / iso / fit, plus capture
  the current 3D view as PNG (Three.js `preserveDrawingBuffer`).
- **Drawing views** rendered from the real tessellated model with matplotlib
  (Agg/SVG, painter's z-sort): `app/drawing/render.py` + `dimensions.py`. Overall
  dimensions from the true bounding box; template callouts (hole ø/spacing, wall,
  journals) from the spec. Endpoint `GET /api/designs/{id}/views/{view}?fmt=png|svg`.
  No new deps — matplotlib/Pillow already present.

### Drawing-to-CAD Assist (WS3)
- Schemas: `DrawingInterpretationSpec`, `DrawingViewSpec`, `DrawingDimensionSpec`,
  `DrawingHoleCalloutSpec`, `DrawingSectionSpec`, `DrawingAssumption`,
  `DrawingClarificationQuestion`.
- OpenAI vision via the Responses API image input + JSON-schema structured output
  (`interpret_drawing`); deterministic mock for offline tests. Endpoints
  `POST /api/drawings/interpret` (image upload) and `/confirm` (user-confirmed →
  generate). Surfaces assumptions/clarifications; unsupported drawings get an
  `unsupported_reason` — never a silent guess. Live smoke:
  `python -m scripts.smoke_drawing_image_openai img.png` (excluded from verify).

### Localized editing (WS4)
- `LocalizedModificationSpec` (entity type/id, constrained operation, NL
  instruction, validated params) → `app/editing/localized.py` translates to
  trusted DesignSpec edits. Supports hole diameter/type/counterbore/countersink/
  move, edge fillet/chamfer, wall thickening, enclosure vents, plate cutouts,
  gusset. Unsupported selections return an explanation. Endpoint
  `POST /api/designs/{id}/localized-edit`. Viewer face-pick selection in the UI.

### CAD package (WS5)
- `GET /api/designs/{id}/package` → ZIP: STEP, STL, design_spec.json,
  manufacturing_report.json+txt, drawings/*.png+svg, README. Import docs page
  (Fusion 360 / AutoCAD / FreeCAD + known limitations).

### Advanced template (WS6)
- `inline_4_crankshaft`: 5 main journals, 4 throw-offset rod journals (phasing
  0/180/180/0), 8 counterweighted webs, keyed front snout, rear flywheel flange
  with 6 bolts + pilot; oriented along X; STL+STEP. Prompt-routed via the mock.

### Testing
- +28 tests (crankshaft, drawing views/interpret, localized edit, CAD package);
  **209 backend tests pass**. verify.sh extended with crankshaft/drawing/package
  checks. Frontend builds clean (10 routes).

## v0.3 — private beta readiness

Make the app usable by 20–50 private beta users without developer help, without
breaking the safety architecture (LLM emits only validated JSON; geometry is
built by trusted local templates).

### Auth & isolation
- Email/password signup/login → JWT bearer tokens (`app/auth/`, `routers/auth.py`).
- Every design route requires auth and is scoped to the user's projects;
  non-owned ids return 404. Projects, designs, exports and feedback are
  user-scoped. Added auth + cross-user isolation tests.

### OpenAI provider
- Rewritten for the **Responses API + Structured Outputs** (`app/llm/schemas.py`):
  `parse_prompt_to_design_spec`, `parse_modification`,
  `generate_clarification_question`, `generate_explanation`, retry-once `repair`,
  optional `OPENAI_REASONING_EFFORT`. Injectable client; schema tests use a fake
  Responses client (no network). Provider-conformance test across mock/anthropic/openai.

### Storage
- `Storage` interface gains `read`/`exists`/`delete`/`signed_url`; added
  `S3Storage` (boto3, presigned URLs) selected by `STORAGE_BACKEND`. Downloads go
  through an owner-checked route (`/api/designs/{id}/files/{fmt}`) — local stream
  or S3 redirect. Path-traversal-safe. Storage tests cover local + S3 (fake client).

### Feedback
- `Feedback` model + routes: thumbs up/down, issue categories (wrong template,
  wrong dimensions, bad geometry, export failed, confusing explanation, missing
  feature, other), free-text comment, linked to user + design (spec hash snapshot).

### Eval harness & observability
- `tests/data/eval_prompts.json` (200+ prompts) via `scripts/build_eval_dataset.py`;
  `scripts/run_eval.py --provider --limit` scores valid_json / correct_template /
  model_or_clarification / export_success / dangerous_blocked / latency_ms /
  estimated_cost → JSON + CSV.
- `app/observability.py`: structured JSON event logging with secret scrubbing;
  request-timing middleware; generation latency + provider recorded on each design.

### Other
- Replaced `pydantic-settings` with a tiny env loader in `config.py` (lighter,
  faster startup, fewer deps).
- Frontend: auth context + signin/signup/signout, protected dashboard/studio,
  table dashboard (template/export/created/edited), feedback widget, hole-table
  editor, dev-only provider badge, 6 onboarding examples.

## v0.2 — useful, demo-ready parts

Focus: turn the three hero templates from "basic shapes" into useful mechanical
CAD, and make the app safe to put in front of real SourceCAD users.

### Hero templates (geometry)
- **Mounting bracket**: rounded plan-view corners, finished (filleted/chamfered)
  top edge, optional strengthening **gusset rib**, and clearance / counterbore /
  countersink holes.
- **Electronics enclosure**: rounded outer corners, four internal **screw bosses
  with pilot holes**, and a matching **countersunk lid** — a real screw-together
  assembly exported as both pieces.
- **Drill jig / adapter plate**: drill jig gains a **registration lip** and
  **chamfered guide-hole lead-ins**; adapter plate gains rounded corners, a
  **chamfered center bore**, and a corner bolt pattern clear of the bore.

### Schema & generation
- `Hole.hole_type` (`simple` / `counterbore` / `countersink`) with feature
  dimensions and validation; type is inferred from supplied dims for back-compat.
- `DesignSpec.chamfer_size` alongside `fillet_radius` (mutually exclusive).
- `DesignModification` + `apply_modification` for deterministic edit prompts.

### LLM rules
- Validation failure now **retries once** with the errors fed back to the
  provider (`repair` hook); still-invalid → useful clarification.
- New **modification** parsing ("make it wider", "move the holes farther apart",
  "make the wall thickness 4 mm", "add rounded edges").
- Contradictory-but-valid specs (e.g. bore > plate) return a clarification
  instead of a 500.

### Checks, explanation, UI
- Added hole-to-hole spacing, counterbore/countersink validity, sub-mm feature,
  counterbore-bridging, and an always-on material/method assumption.
- Server-side **plain-English explanation** of every generated part.
- Studio UI: explanation panel, edit-with-a-prompt box, assumptions list, hole
  summary, export buttons.

### Testing
- `tests/data/benchmark_prompts.json`: 60 categorized prompts.
- New suites: benchmark (≥80% model-or-clarification; routing), v0.2 features
  (hole types, modifications, new checks, retry, explanation), STEP re-import
  validity. Backend: **140 tests**, all passing; benchmark success **100%**.

## v0.1 — MVP vertical slice
- 8 templates, prompt → spec → geometry → STL/STEP + preview, editable
  parameters, basic checks, Next.js studio, deterministic regeneration.
