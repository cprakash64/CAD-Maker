# Drawing → CAD (beta)

Drawing → CAD is a **beta workflow** (`validated_beta`/`experimental` per
`docs/product-contract.md`'s capability levels) — not `production_ready` and
never described as precise unless the specific result is backed by benchmark
evidence (see "Ground truth" below). Every drawing-built design carries this
signal explicitly: `DesignDTO.drawing_beta` is always `true`, and
`DesignDTO.capability_level` is capped at `validated_beta` (or `experimental`
when review is required) regardless of how mature the underlying part family
is on the text-to-CAD path — the extra uncertainty lives in the drawing
interpretation, not the family (`app.services.design_service
.product_contract_fields`).

## Required flow

Upload → secure preprocessing → vector-first extraction → raster/vision
fallback (only when necessary) → structured drawing interpretation →
interpretation review → unresolved-dimension gate → trusted geometry
compilation → dimensional/semantic verification → export.

| Stage | Implementation |
|---|---|
| Upload | `POST /api/drawings/to-cad` (`app/routers/drawings.py`) |
| Secure preprocessing | `app.services.upload_guard` (type/size/timeout-bounded inspection); never trusts client-declared content type |
| Vector-first extraction | DXF/SVG parsed deterministically and exactly — no vision call at all (`app.services.drawing_ingest`, `drawing_to_spec.analysis_from_vector`) |
| Raster/vision fallback | Only for PNG/JPG/PDF, and only after deterministic CV pre-classification; time-boxed, with a deterministic best-effort fallback (`app.services.drawing_best_effort`) if the vision provider times out or errs |
| Structured interpretation | `DrawingInterpretationSpec` (`app/schemas/drawing_spec.py`) — the strict, `extra="forbid"` contract every path populates |
| Interpretation review | Mandatory frontend review card (`frontend/src/app/drawing/page.tsx`) whenever `drawing_review_required` is true — the design is never auto-opened in the studio unread |
| Unresolved-dimension gate | `UnresolvedDimension` / `critical_unresolved_dimensions()` / `blocks_final_export()` block the manufacturable export via the existing critical-failure mechanism (`is_critical_failure` / `_block_export_if_critical`) |
| Trusted geometry compilation | Deterministic family builders first (route-locked for pipe/flange/branch — never the LLM); feature-graph/CadPlan compiler otherwise |
| Verification | Feature audit, dimension report, `drawing_fidelity` reconciliation (`_fidelity_report` / `_attach_fidelity`) |
| Export | STEP/STL, gated by `download_blocked_reason` when a critical dimension was never resolved |

## Interpretation contract

Every interpretation returns (`DrawingInterpretationSpec` /
`DrawingToCADAnalysis`, surfaced on the design DTO):

- Detected units (`drawing_units_confidence`) and detected views (`views`,
  each with a `DrawingViewType`)
- Overall dimensions and per-feature dimensions (`DrawingDimensionSpec`,
  with `value`, `tolerance`, `confidence`)
- Hole and thread callouts (`DrawingHoleCalloutSpec`: `diameter`, `callout`,
  `pattern`, and now `through` / `blind_depth` for through/blind status)
- Tolerances when detected (`DrawingDimensionSpec.tolerance`)
- Confidence per extracted item (`*_confidence` fields at both the
  item and interpretation level) plus one top-level `confidence` on the
  design DTO
- Unresolved or conflicting dimensions (`unresolved_dimensions`:
  `UnresolvedDimension{field, category, reason, detail, candidates,
  critical}`) and `missing_critical_dimensions`
- Assumptions that would be required to proceed (`assumptions`,
  `required_assumptions_preview`)

**Important missing depth, thickness, bore type, view relationship, or
feature placement is never silently inferred.** These five categories
(`CRITICAL_UNRESOLVED_CATEGORIES` in `app/schemas/drawing_spec.py`) are
detected via `infer_critical_categories()` (deliberate word-boundary
matching — the same discipline the "bearing"/"ring" substring bug taught
this codebase to apply) and, when present, set `blocks_final_export() ==
True`. The design still builds and stays fully inspectable; only the
downloadable STEP/STL is withheld until the dimension is resolved (an
explicit `thickness_mm` override, a clearer re-upload, or a note).

### The pipe/flange/branch exception (documented, not a loophole)

`flanged_pipe_branch` and `pipe_tee` (`PIPE_BRANCH_DETERMINISTIC_FAMILIES`,
`app/schemas/drawing_spec.py`) are built only by a route-locked, deterministic
builder that **always** estimates wall/PCD/flange-thickness/branch-length
from drawing proportions — a disclosed, family-level characteristic capped at
`review`, never `failed` (`app/routers/drawings.py`, the "PART G" builder
comment). This is deliberately narrower than the broader
`PIPE_FLANGE_FAMILIES` set: a plain vector `flange`/`blind_flange`/
`pipe_spool`/`pipe_elbow`/`pipe_fitting` built from clean SVG/DXF has real
parsed geometry, so a genuinely missing depth on those families is still a
critical unresolved dimension like any other part.

## Supported beta envelope

**Supported** (has real coverage and at least one passing benchmark case):

- Clean, dimensioned single-part drawings
- Simple orthographic views (front/top/side)
- Vector DXF/SVG profiles — parsed exactly, no vision required
- Limited raster (PNG/JPG/PDF) drawings with legible, readable dimensions

**Unsupported / experimental** (not benchmarked; treat results as a
starting point, never as a verified part):

- Complex, multi-part assemblies
- GD&T-heavy drawings (datums, feature control frames)
- Poor-quality scans (low contrast, skew, noise beyond the deterministic CV
  pre-classifier's tolerance)
- Drawings with missing critical dimensions — the system now *tells you*
  instead of guessing (this document's core change), but a drawing that
  never states depth/thickness/bore type still cannot produce a trustworthy
  final export
- Complex section/detail views
- Ambiguous hidden geometry (ex: an unclear blind vs. through bore with no
  section view to disambiguate)

## Ground truth (benchmark)

`backend/eval/fixtures/drawing_to_cad.json`, run via `backend/eval/cli.py run
--suite drawing_to_cad`. Each case measures a subset of:

- unit accuracy (`assert_units`)
- dimension extraction / final solid dimensions (`assert_bounding_dimensions`,
  `required_dimensions`, `geometric_assertions`)
- view association (`expected_semantic_features`, view-derived family
  detection asserted indirectly via `assert_object_type`)
- feature reconstruction (`assert_holes`, `assert_feature_positions`,
  `assert_symmetry`)
- unresolved-dimension detection (`assert_unresolved_dimensions` against
  `expected_critical_unresolved` — new in this phase)
- export integrity (`assert_export_and_reimport`: STEP re-import validity,
  STL mesh readability, no zero-byte partial exports)

Paired cases prove both sides of the gate on the same fixture: e.g.
`dwg_svg_adapter_plate_001` (a `thickness_mm` override resolves the missing
depth → clean `generates` + full export) and
`dwg_svg_adapter_plate_unresolved_depth_001` (as-drawn, no override → the
drawing genuinely never shows a depth → `refusal` classification, i.e. no
silent export, `expected_critical_unresolved: ["depth"]`). Same pairing for
`dwg_svg_flange_001` / `dwg_svg_flange_unresolved_depth_001`.

Raster/vision cases are marked `requires_live` and skipped in the offline
mock-provider baseline (mock cannot read pixels); they run only with
`--live` against a real vision model, and until they do, the raster/vision
path stays unbenchmarked — which is exactly why drawing-to-CAD as a whole
stays capped below `production_ready` (`docs/product-contract.md`).

## UI

- A visible **Beta** badge on `/drawing`, always shown (drawing-to-CAD is
  beta as a whole, not per-result).
- A **mandatory interpretation review** card whenever
  `DesignDTO.drawing_review_required` is true (fidelity isn't a clean `ok`,
  or a critical dimension was never resolved) — the generated design is
  never auto-opened in the 3D studio unread. It shows fidelity status +
  confidence, the specific unresolved critical categories, the export-block
  reason if any, and every assumption made, with an explicit "I've reviewed
  it" action to proceed.
- Results are never labeled "precise" or "production-ready" in this UI;
  copy explicitly frames beta results as a starting point.

## Acceptance criteria → where each is enforced

- **Important unresolved dimensions block automatic final generation** —
  `UnresolvedDimension.critical` + `blocks_final_export()` →
  `is_critical_failure` → `_block_export_if_critical` (409 on direct
  download) and `DesignDTO.download_blocked_reason` /
  `drawing_review_required`. Tests:
  `tests/test_drawing_to_cad.py::test_svg_adapter_plate_generates_validated_model`,
  `test_svg_flange_builds_circular_part_with_bolt_circle`,
  `test_drawing_built_design_is_capped_beta_and_flags_review`.
- **Supported cases pass their declared benchmark** — offline
  `drawing_to_cad` suite, 100% pass rate on all non-`requires_live` cases
  (`backend/eval/fixtures/drawing_to_cad.json`).
- **Unsupported drawings are rejected or downgraded honestly** — invalid/
  empty/unreadable input → 4xx (`test_invalid_file_type_rejected_with_
  useful_error`, `test_unreadable_svg_is_422`); low-confidence or
  provider-error reads → `drawing_fidelity_status: "review"`/`"failed"`,
  never a silent `"ok"` (`_fidelity_report`'s "verdict only ever gets
  stricter" merge invariant in `_attach_fidelity`).
- **No silent geometry invention occurs for critical dimensions** — the
  resolution path requires an explicit, user-supplied override
  (`thickness_mm`) or a clearer re-upload; there is no code path that
  defaults a critical dimension and reports `"ok"`/unblocked in the same
  response.
