# CAD families, maturity & the capability registry

SourceCAD generates CAD from plain English across a growing set of **part /
assembly families**. Rather than hardcoding one example at a time, the backend
keeps a single, honest **family registry** that the classifier, the API, the
benchmark, and this document all read from. This is the scalability layer: adding
a new CAD type means registering a family, not rewriting the router.

- Registry: [`backend/app/cad/families.py`](../backend/app/cad/families.py)
- Classifier: [`backend/app/cad/classification.py`](../backend/app/cad/classification.py)
- Capability API: `GET /api/capabilities`
- Golden benchmark: [`backend/tests/data/golden_prompts.json`](../backend/tests/data/golden_prompts.json)
- Tests: [`backend/tests/test_family_registry.py`](../backend/tests/test_family_registry.py),
  [`backend/tests/test_capabilities_api.py`](../backend/tests/test_capabilities_api.py)

## Maturity levels (what the labels honestly mean)

| Maturity | Meaning |
| --- | --- |
| `production_ready` | Validated, dimension-checked, exportable as STEP + STL. Covered by golden generation tests. |
| `beta` | Generates real CAD, but with narrower coverage / fewer guarantees than production. |
| `concept` | Plausible **concept** geometry — *not* certified, FEA-analyzed, or standards-checked. |
| `unsupported` | Not generated as a single part; routed to **decomposition** guidance instead. |

We deliberately make **no fake accuracy or manufacturing claims**. A concept
tubular chassis is labelled concept and flagged "not FEA-certified"; an
approximate gear blank says its teeth are not a true involute profile.

## Supported families

| Family | Mode | Maturity | Notes / key limitation |
| --- | --- | --- | --- |
| `mounting_plate` | single part | production_ready | Flat plates/brackets with hole patterns. |
| `spacer` | single part | production_ready | Round/hex spacers, standoffs, bushings (no threads). |
| `l_bracket` | single part | production_ready | Two-flange right-angle bracket. |
| `flange` | single part | beta | Flange/adapter/transition plates; no ANSI/DIN standard tables. |
| `enclosure` | single part | beta | Single-cavity shelled box with bosses. |
| `pipe_fitting` | single part | beta | Spool/tee/clamp; geometry only, no pressure rating or NPT threads. |
| `drill_jig` | single part | beta | Guide-hole plate; plain bores (no hardened bushings). |
| `handle_knob` | single part | beta | Simple revolved/extruded grips. |
| `u_bracket` | single part | beta | True U channel (base + two side walls), not a flat plate. |
| `hinge_bracket` | single part | beta | Base + two ears + coaxial pin hole. |
| `clamp_block` | single part | beta | Split tube/pipe clamp with bore + tightening bolts. |
| `robotic_arm_base_bracket` | single part | beta | Circular/rectangular base + vertical tower + gussets (+ optional bearing pocket). |
| `screwdriver` | single part | concept | One fused hand tool along X: handle + coaxial shaft + tip. Phillips tip approximate; not manufacturing-certified. |
| `gear_blank` | single part | concept | **Approximate** teeth — not a true involute; use as a blank. |
| `crankshaft` | single part | beta | Inline-4 geometric model; not balance/stress validated. |
| `generic_feature_graph_part` | single part | beta | Anything composed from safe primitives (box/cylinder/tube/boss/rib/hole + booleans). No threads/splines/free-form. |
| `machine_frame` | assembly | concept | Welded square-tube frame: legs, top/bottom frames, braces, foot/motor plates, panel. |
| `engine_test_stand` | assembly | concept | Square-tube stand: engine plates, crossbar, radiator/fuel mounts, caster plates. |
| `drone_frame` | assembly | concept | Quadcopter X-frame: arms, motor hole patterns, central/battery plates, landing feet. |
| `motorcycle_subframe` | assembly | concept | Tapered tube rails, seat rails, shock/tail/side tabs, battery tray, bracing. |
| `skateboard_motor_mount` | single part | concept | Primary motor mount bracket of a larger deck assembly (decomposed). |
| `tube_chassis` | assembly | concept | Tubular space frame — concept CAD; tubes export as solid cylinders. |
| `reference_buggy_tubular_chassis` | assembly | concept | Hand-authored reference buggy/sports-car layout; concept only. |
| `generic_assembly_decomposition` | assembly | unsupported | Whole machines → decomposition plan, no geometry. |

### Structural-frame & concept-assembly generators

The frame families above are produced by reusable deterministic generators in
[`backend/app/cad/assembly/frames.py`](../backend/app/cad/assembly/frames.py)
(square/round tube frames built from beams, tubes and plates) and validated by
profile in [`backend/app/cad/assembly/frame_report.py`](../backend/app/cad/assembly/frame_report.py).
They are routed **before** the complexity gate and built with CadQuery and **no
LLM call**, so hard structural prompts that used to time out or decompose now
return a validated concept model fast. Square tubing is exported as solid beams
and round tubing as solid cylinders; real wall thickness is carried as cut-list
metadata. Validation profiles: `structural_frame_assembly`, `drone_frame`,
`motorcycle_subframe`, `motor_mount_component`.

The `skateboard_motor_mount` family honours an explicit "if too complex, build
the main bracket first" request: it generates the **primary motor mount bracket**
(a single fused part) and records a decomposition note for the rest of the deck
assembly, instead of returning generic decomposition.

The live, machine-readable version (with required/optional dimensions, default
assumptions, example prompts and per-family limitations) is always available at
`GET /api/capabilities`.

## What "validated" means

For a generated single part, validation (the `production_ready`/`beta`
guarantee) asserts:

- a **STEP** and an **STL** file were exported and are non-empty;
- the model is **not empty** (positive volume);
- the measured **bounding box** is within tolerance of the requested envelope;
- expected **hole counts** match the plan metadata;
- compile errors are surfaced clearly (never silently swallowed).

See [`backend/tests/test_golden_benchmark.py`](../backend/tests/test_golden_benchmark.py)
for the dimensional safety net that enforces this on hand-authored parts.

## What "concept assembly" means

A concept assembly (e.g. a tubular chassis) produces a **previewable, exportable**
multi-body model that is *representative*, not engineering-validated:

- tubes are exported as **solid cylinders**; wall thickness is carried as
  cut-list metadata, not modelled as a hollow section;
- node/joint positions are **idealized** — no weld prep, load-driven gusseting,
  or triangulation optimization;
- it is **not** FEA-analyzed, homologated, or certified for structural use.

These assemblies are validated with the *assembly profile* (multi-body allowed)
and always labelled concept in the UI and API.

## Why some prompts ask for decomposition or clarification

The classifier runs **before** any expensive generation (pure string analysis,
no LLM/CadQuery):

- **Decomposition** — a prompt describing a whole machine or many subsystems
  (a complete car, an airframe, a drone) is far beyond single-part generation.
  Attempting it synchronously would burn minutes and produce nothing usable, so
  the system returns a decomposition plan: build one component at a time.
- **Clarification** — a prompt that is decorative/organic or too vague to map to
  safe mechanical geometry asks for a clearer description rather than guessing.

`needs_decomposition` is now used only when the prompt is an **unsupported**
family, has too many unrelated systems, is missing essential dimensions with no
feasible fallback component, or otherwise has no buildable deterministic family.
Supported hard prompts (machine/equipment frames, engine test stands, drone
frames, motorcycle subframes, e-skateboard mounts, and the medium parts above)
route to a deterministic generator instead.

## Single-part fuse safeguard

A single-part model must be **one connected solid**. The CadPlan compiler
([`backend/app/cad/plan/compiler.py`](../backend/app/cad/plan/compiler.py)) has a
bounded safeguard: if a build ends up as several near-collinear sub-bodies with
small gaps (handle + shaft + tip, pin + head, shaft + collar), it bridges them so
the part fuses, and records an "auto-fused …" assumption. Clearly-separate bodies
(large gaps) are **not** bridged — they still fail single-body validation and
their export stays blocked. Deterministic families (e.g. the screwdriver) are
built already-fused, so the safeguard is only a backstop. Common everyday objects
without a deterministic family (hammer, wrench, …) are routed to clarification
instead of free-form generation that tends to produce disconnected geometry.

## Timeout robustness

Hard structural prompts that match a supported deterministic family are
intercepted and built **before** any LLM call, so they cannot end with
"Generation took too long and was stopped". If an LLM-backed path does exhaust
its time budget, the request still surfaces a clean 503 rather than a hang, and
deterministic families remain available offline. The hard-prompt regression set
lives in [`backend/tests/data/hard_prompts.json`](../backend/tests/data/hard_prompts.json)
(see [`test_hard_prompts.py`](../backend/tests/test_hard_prompts.py)).

Every design now carries a structured `classification` block
(`family_id`, `confidence`, `design_mode`, `complexity`,
`generation_strategy`, `can_generate_now`, `required_missing_inputs`,
`visible_assumptions`, `limitations`) in `semantic_json` and in the design DTO.

## Generation strategies

| Strategy | When |
| --- | --- |
| `deterministic_template` | A strong parametric template matches the prompt. |
| `cadplan` | Built from the CadPlan feature graph (safe primitives + booleans). |
| `assembly_generator` | A supported concept-assembly builder (chassis). |
| `needs_clarification` | Missing critical info, or decorative/ambiguous. |
| `needs_decomposition` | Large multi-part assembly — split into single parts. |
| `unsupported` | Cannot be mapped to safe geometry. |

> Note: the family is the *kind of part*; the strategy is *how it is built this
> time*. A flange may be built via a template or, when the prompt needs a more
> flexible shape, via the feature graph (`cadplan`). Both are honest.

## How to add a new family

1. **Register it** in `backend/app/cad/families.py` — add one `CADFamily(...)`
   entry with its `family_id`, `display_name`, `design_mode`, honest `maturity`,
   `keywords`, generator `object_types`, required/optional dimensions, default
   assumptions, `generation_strategy`, `validation_profile`, `export_policy`,
   `known_limitations`, and `example_prompts`.
2. **Wire the generator** if it's new — a reusable parametric template
   (`backend/app/cad/templates/`) registered in
   [`backend/app/cad/registry.py`](../backend/app/cad/registry.py), or feature-graph
   support. Per project rules, generators are deterministic and never execute
   LLM-authored code.
3. **Add golden prompts** in `backend/tests/data/golden_prompts.json` (every
   `production_ready` family must have at least one — enforced by a test).
4. **Run the tests** — `test_family_registry.py` checks registry integrity,
   classifier routing, and benchmark coverage; add an end-to-end
   generation/export test for production-ready families.
5. **It auto-appears** in `GET /api/capabilities` and this catalog stays the
   single source of truth.
