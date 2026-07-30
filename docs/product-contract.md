# LunaiCAD product contract

This is the single canonical statement of what LunaiCAD is, what it promises,
and how it decides when to ask vs. when to assume. Every other document
(`docs/CAD_FAMILIES.md`, `README.md`), the capability registry
(`app/cad/families.py`), the API (`DesignDTO`), and the model instructions
(`app/llm/base.py` system prompts) must agree with this document. Where an
older document uses different vocabulary, this document is authoritative and
the older document should be read as describing the same underlying system.

## Product definition

> A prompt-first platform for generating editable, 3D-printable functional
> CAD from text or dimensioned drawings, using structured intent and trusted
> deterministic geometry generators.

Consequences of that sentence, stated explicitly so they can't drift:

- **Text-to-CAD is the primary workflow.** It gets the deepest family
  coverage, the tightest validation, and is the one benchmarked by the
  evaluation harness (`backend/eval/`) by default.
- **Drawing-to-CAD remains beta (`validated_beta`) until its own benchmark
  passes.** It is not promoted to `production_ready` by fiat — promotion is
  gated on the same eval-harness mechanism as everything else (see
  "Capability levels" below). As of the phase-4 baseline, the deterministic
  vector path (SVG/DXF) has passing coverage; the raster/vision path has not
  been benchmarked at all (`requires_live` cases, never executed against a
  real vision model in this phase) — so drawing-to-CAD as a whole stays
  `validated_beta`, not `production_ready`, until that changes.
- **Manufacturability checks are advisory, not engineering certification.**
  `ManufacturingCheck` / `dimension_report` / `print_readiness` tell you
  whether the geometry looks internally consistent and printable — they are
  not a substitute for an engineer signing off on a load-bearing or
  safety-critical part. This is why `known_limitations` on every
  `production_ready` family still says things like "not FEA-analyzed."
- **Safety-critical components are unsupported for production use.** Nothing
  in this system claims to produce a part safe to use in a load-bearing,
  pressure-retaining, or life-safety application without independent
  engineering review — regardless of maturity label. `production_ready`
  means "the geometry reliably matches the request," never "certified safe
  to build."

## Capability levels

Four standardized levels (`app.cad.families.Maturity`), used everywhere in
the system as the SAME four strings:

| Level | Meaning |
|---|---|
| `production_ready` | Validated, dimension-checked, and exportable as STEP + STL. Requires an eval-harness benchmark pass rate ≥ 0.9. |
| `validated_beta` | Generates real CAD with fewer guarantees / narrower coverage. Requires an eval-harness benchmark pass rate ≥ 0.7, where measured. |
| `experimental` | Plausible geometry, not certified or analysis-validated. No benchmark gate. |
| `unsupported` | Not generated as one part; routed to decomposition guidance, a clarification, or an honest decline. |

(This supersedes the earlier `beta`/`concept` vocabulary used before this
phase — those two terms now mean exactly `validated_beta` and `experimental`
respectively, renamed everywhere in code, tests, and docs.)

### Required per-family/workflow metadata

Every entry in the family registry (`app.cad.families.CADFamily`) declares:

| Field | Meaning |
|---|---|
| `required_dimensions` | Human-readable list of what must be known to build at all |
| `optional_dimensions` | What can be omitted (a default fills the gap) |
| `safe_defaults` | Numeric defaults actually used when an optional dimension is omitted — populated automatically from the real `app.cad.registry` template `DimensionSpec` defaults where one exists; empty (not fabricated) for CadPlan-only/concept families with no single fixed default |
| `default_assumptions` | The prose form of the same defaults, always populated |
| `known_limitations` | What this family deliberately does NOT do |
| `supported_editing_operations` | Subset of `LocalizedOperation` this family supports via circle-edit/localized-edit (declared baseline for mature single-part families; not yet independently verified per family) |
| `export_policy` / `supported_exports` | Export formats (both keys carry the same list; `export_policy` predates this phase, `supported_exports` is the name this contract asks for) |
| `physical_validation_status` | `none` \| `dimension_checked` \| `benchmarked` — see below |
| `minimum_benchmark_threshold` | The pass-rate this family's maturity claim requires (0.9 / 0.7 / `None`) |
| `benchmark_pass_rate` + `benchmark_source` | The actual measured pass rate and where it came from, or both `None` if never measured |

### What "benchmarked" means, concretely

`physical_validation_status="benchmarked"` means at least one case in
`backend/eval/fixtures/*.json` resolves (via `family_for_object_type`) to
this family, and `benchmark_pass_rate` is the fraction of those cases that
passed in the most recent baseline run
(`backend/eval_reports/baseline_phase4/`, 2026-07-27). Families without eval
coverage are `dimension_checked` (their own generation-time dimension report
still runs, just not tracked as a suite) or `none` (concept assemblies,
explicitly not claiming any dimensional guarantee). No family's
`benchmark_pass_rate` is invented — it is either a real fraction from a real
report, or `None`.

**Verified invariant** (`tests/test_product_contract.py`): no family's
`benchmark_pass_rate` is below its own `minimum_benchmark_threshold`. If a
future benchmark run finds a `production_ready` family regressed below 0.9,
that test fails — the family's maturity label must be downgraded before its
benchmark score is allowed to slip further out of sync with its claim.

### Two honesty layers (a deliberate architectural split, not a gap)

Standard/catalog fastener parts (hex nut, square nut, bolt, threaded rod,
shaft coupler, GT2 pulley) and the tire/rim/wheel-assembly family are **not**
in `app.cad.families` — they predate it and are governed instead by
`app.cad.part_family`'s `generation_honesty_status` contract
(`exact`/`partial`/`substituted`/`unsupported`), exposed as
`DesignDTO.part_family_contract`. `DesignDTO.capability_level` is `None` for
these designs by construction — check `part_family_contract` instead. This
is documented here explicitly so it reads as an intentional split, not an
oversight discovered later.

## Clarification policy (ask vs. default)

The rule, stated exactly once, applied everywhere on the primary (CadPlan)
text-to-CAD route via `app.cad.plan.policy.decide_clarification()`:

**Always ask** when missing/ambiguous information falls in one of these
categories (`app.cad.plan.clarification_categories.ASK_REQUIRED_CATEGORIES`):

- `topology` — basic shape/structure is unclear
- `overall_size` — primary scale/envelope is unclear
- `fit` — a mating/clearance fit is unspecified
- `mating_geometry` — how this part connects to another is unclear
- `fastener_standard` — which screw/bolt/thread standard is intended
- `bearing_shaft_interface` — a bearing or shaft seat/fit is unspecified
- `assembly_relationship` — how this part relates to other parts/assembly
- `safety_or_load` — a load-bearing or safety-relevant assumption

**Never ask; use a visible default** for
(`SAFE_DEFAULT_CATEGORIES`):

- `cosmetic_fillet`
- `minor_chamfer`
- `noncritical_radius`
- `profile_derived_wall_thickness`
- `preview_only_cosmetic`

**How this is enforced deterministically, not by LLM judgment:** the planner
(LLM or the offline deterministic planner) may tag a `CadPlan` with
`ambiguity_flags` — which category (if any) it's unsure about. It does
**not** decide whether that means asking. `decide_clarification()` does,
as a pure function of the category tags:

- Any `ASK_REQUIRED_CATEGORIES` tag present → **fatal** (ask), even if the
  plan is otherwise fully buildable with real features. This overrides
  `clarification_required=False` from an LLM that under-asked.
- Only `SAFE_DEFAULT_CATEGORIES` tags (or none) present → never fatal on
  their account; an LLM that over-asked (`clarification_required=True`) with
  only a cosmetic concern is downgraded to a warning and the part still
  generates, with the default surfaced in `assumptions`.
- No features and no ask-required tag → falls back to the pre-existing
  buildability/mechanical-recognizability check (unchanged from before this
  phase).

Because the decision is a pure function of `(features present?,
ambiguity_flags, clarification_required)`, **the same category of ambiguity
is always handled the same way** — this is the property
`tests/test_product_contract.py::test_same_request_never_flips_ask_default`
checks directly, by calling `decide_clarification` twice on an
identically-constructed plan and asserting an identical verdict.

Every default that IS used must appear in `DesignDTO.assumptions` as a
structured, human-readable string — never applied silently. This was already
true for the pre-existing default-fill paths (`_vague_clarification`,
`_everyday_object_clarification`, template `DimensionSpec` defaults); the
new `ambiguity_flags`-tagged cosmetic path follows the same rule (see
`app/cad/plan/deterministic.py::_tag_ambiguity`).

### Known scope boundary

`decide_clarification()` governs the **primary CadPlan/text-to-CAD route**.
Several other deterministic gates in this codebase make conceptually
equivalent ask-vs-default decisions with their own vocabulary and
thresholds — the vague-category regex gate (`_vague_clarification`), the
everyday-object gate (`_everyday_object_clarification`), the drawing
confidence thresholds (`DrawingInterpretationSpec.overall_confidence` vs.
`CONFIDENCE_THRESHOLD`/`GENERATE_WITH_ASSUMPTIONS_CONFIDENCE`), and the
object-intelligence provenance ceiling (`status_ceiling`). Fully unifying all
of these under one shared category vocabulary is a larger follow-up than
this phase's scope; each is independently deterministic today (none delegate
the ask/default decision to raw LLM judgment), so the specific defect this
phase closes — an LLM's own boolean silently deciding ask-vs-default
inconsistently on the primary route — is closed. This is recorded as a
known boundary, not hidden.

## API contract

Every `POST /api/designs/create` (and subsequent edit/regenerate) response
exposes, at the top level of `DesignDTO`, additively (no existing field
renamed or removed):

| Field | Existing since | Meaning |
|---|---|---|
| `object_type`, `title` | — | Interpreted intent (structured) |
| `interpreted_intent` | this phase | Interpreted intent (one human-readable label) |
| `spec.units` / `normalized_units` | — / this phase | Normalized units (always `"mm"`) |
| `capability_level` | this phase | One of the four standardized levels, or `null` for a part governed by the part-family honesty layer instead |
| `assumptions`, `default_assumptions` | — | Every default actually used, in prose |
| `clarification_question`, `clarification_questions`, `missing_required` | — | Open questions (per-field detail) |
| `unanswered_questions` | this phase | The same open questions, deduplicated, one place |
| `confidence` | this phase | Best available top-level confidence signal, `null` if none was computed |
| `known_limitations` (via family registry) / `limitations` | — / this phase | What this design's family deliberately doesn't do |
| `validation_status`, `dimension_report`, `print_readiness` | — | Validation state |
| `download_blocked_reason`, `export_eligibility` | — / this phase | Export eligibility (detail / single top-level verdict) |
| `safety` | safety-policy phase | Sticky high-consequence-category classification (`app.safety`); `null` when none ever detected. `export_eligibility` already reflects any safety block — this field adds the categories/policy/acknowledgment detail behind that verdict. |
| `generation_outcome` | — | The universal six-state contract terminal outcome (`app.cad.contract`) |

Backward compatibility: every field in this list that predates this phase is
unchanged in name, type, or meaning. The seven new fields are all optional
with safe defaults, so an older frontend build ignoring them continues to
work unmodified; a newer frontend can read them without waiting for every
other field to be migrated.

## Tests

`backend/tests/test_product_contract.py` proves:

1. Topology-changing ambiguity (`ambiguity_flags=["topology"]` via a
   no-features clarification plan) asks a question.
2. Low-impact/cosmetic ambiguity (`ambiguity_flags=["cosmetic_fillet"]`) on
   an otherwise-buildable plan generates silently with a visible assumption
   — never asks.
3. Fit ambiguity (`ambiguity_flags=["fit"]`) asks a question **even when the
   plan has real, buildable features** — the specific override this phase
   adds, verified end-to-end through `POST /api/designs/create` with the
   deterministic offline planner, not just at the unit level.
4. An unsupported part (nyloc nut) is refused honestly:
   `generation_honesty_status="unsupported"`, `capability_level is None`,
   `object_type != "hex_nut"` (never silently substituted).
5. Every family's advertised `maturity` is consistent with its own
   `benchmark_pass_rate`/`minimum_benchmark_threshold` (no family claims a
   level its measured benchmark doesn't support).
6. Assumptions remain editable: a design generated with a visible default
   can still have that same parameter changed via `/regenerate` with no
   loss of the assumption's traceability.
7. Determinism: `decide_clarification` returns byte-identical verdicts for
   two structurally-identical plans (same features-present, same
   `ambiguity_flags`, same `clarification_required`) — the same request
   never sometimes asks and sometimes silently assumes.

## Change log

- **This phase**: renamed `beta`/`concept` → `validated_beta`/`experimental`
  (code, tests, docs); added `safe_defaults` /
  `supported_editing_operations` / `physical_validation_status` /
  `minimum_benchmark_threshold` / `benchmark_pass_rate` /
  `benchmark_source` to the family registry, cross-referenced against the
  phase-4 eval baseline; closed five family-registry object_type gaps
  (`mounting_plate`, `motor_mount`, `blind_flange`, `pipe_spool`/`pipe_tee`,
  `electronics_enclosure`/`rpi4_enclosure`/`rpi5_enclosure`/
  `board_enclosure`, `vise_jaw`/`bearing_block`/`generic_fitted_box`) that
  previously resolved to no family at all; added `ambiguity_flags` to
  `CadPlan` and the deterministic category policy in
  `app.cad.plan.clarification_categories`; extended `decide_clarification()`
  to treat an ask-required category as fatal even for an otherwise-buildable
  plan; added the seven additive `DesignDTO` product-contract fields.
