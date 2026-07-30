# LunaiCAD product-quality evaluation harness

Answers one question: **"How often does LunaiCAD produce the correct usable
CAD result for realistic supported requests?"** — reported separately per
workflow and complexity level, never collapsed into one score.

This phase establishes the baseline. It does **not** tune prompts against
the evaluation set — see "Held-out policy" below.

## Relationship to existing eval scripts

Before building this, the repo already had several ad hoc eval assets:
`scripts/run_eval.py`, `scripts/run_cad_evals.py`,
`scripts/run_generation_regression.py`,
`scripts/run_semantic_generation_benchmark.py`, and
`tests/test_golden_benchmark.py` / `tests/test_beta_stress.py`, each with its
own JSON dataset under `tests/data/`. Those are **not removed** — they still
run standalone and some (`test_golden_benchmark.py`, `test_beta_stress.py`)
are part of the pytest suite. This harness is the single, schema-documented,
multi-suite successor the task asked for; several of its fixtures were
*derived from* those existing datasets and tests (see each case's `source`
field), not duplicated blind.

## Layout

```
eval/
  schema.py              EvalCase dataclass + validation (the record schema)
  bootstrap.py            env bootstrap -- MUST run before anything else (see below)
  context.py              EvalContext: what one case execution produces
  executors.py             per-workflow: drive the real HTTP API via TestClient
  assertions.py            reusable, composable assertion functions
  runner.py                orchestration: load -> execute -> score -> report
  report.py                JSON + Markdown report generation
  live_instrumentation.py OpenAI token/cost/retry capture for --live runs
  versioning.py            prompt-version fingerprint + git commit
  cli.py                   the one documented command (python -m eval.cli)
  fixtures/*.json          the 100 cases, one file per required suite
```

## The record schema

Every fixture file is a JSON list of objects. `eval/schema.py::EvalCase` is
the single source of truth; `load_cases()`/`load_all_cases()` validate every
entry against it at load time (unknown field, invalid enum value, missing
required field for a workflow, or a duplicate `case_id` — anywhere in the
whole `fixtures/` directory, not just within one file — all fail the run
loudly, never silently).

| Field | Meaning |
|---|---|
| `case_id` | Stable, globally-unique id |
| `suite` | One of the 8 required suites (see below) |
| `workflow` | `text_to_cad` \| `drawing_to_cad` \| `modification` \| `export_integrity` \| `security` |
| `complexity` | `simple` \| `moderate` \| `complex` |
| `source` | Provenance: which existing test/dataset this was derived from, or `"new"` / `"probed against create_design (mock), <date>"` |
| `data_split` | `development` \| `regression` \| `held_out` (see policy below) |
| `prompt` / `fixture` (+`fixture_media_type`) / `setup_prompt`+`edit_instruction`+`edit_kind`+`edit_selection` / `pytest_node_id` | The input, shaped per workflow |
| `requires_live` | Skipped offline; only runs with `--live` (e.g. raster drawings needing real vision) |
| `expected_capability_classification` | `generates` \| `clarification_required` \| `refusal` \| `needs_decomposition` \| `rejected_input` |
| `expected_object_type` | Any-of list of acceptable `object_type` values |
| `expected_capability_level` | One of `production_ready`/`validated_beta`/`experimental`/`unsupported`, or the string `"null"` for a design governed by the part_family honesty layer instead of the family registry (docs/product-contract.md) |
| `required_dimensions` | Expected bbox axes (mm) |
| `allowed_assumptions` | Substrings any stated assumption must match one of — an assumption outside this list is a silent-guess regression |
| `required_clarification_behavior` | `must_ask` \| `must_not_ask` \| `null` |
| `expected_semantic_features` | Feature `type` values expected in the design's `features` list |
| `geometric_assertions` | `units`, `bbox_mm`, `volume_range_mm3`, `holes` (`count`/`diameter_mm`), `feature_positions`, `symmetry_axis`, `wall_thickness_mm`, `allow_multibody` |
| `export_expectations` | `formats`, `require_reimport`, `no_partial_file` |
| `allowed_changed_paths` / `expected_spec_values` | Modification workflow: what's PERMITTED to change, and what it must become |
| `safety_classification` | `safe` \| `boundary` \| `adversarial` |
| `notes` | Free text — used extensively to record *why* a case asserts what it asserts, especially for documented known-current-failures |

### The 8 required suites

`known_families`, `compositional_cadplan`, `ambiguous_prompts`,
`unsupported_prompts`, `modifications`, `drawing_to_cad`, `export_integrity`,
`security_regressions` — one JSON file each under `eval/fixtures/`.

### Held-out policy

`data_split: held_out` cases (16 of the 100) are canaries against overfitting
a future prompt-tuning phase. **Do not read failures on held-out cases as
"needs a prompt fix" and tune against them directly** — they exist so a
future phase can check whether changes made to satisfy `development`/
`regression` cases *also* hold up on cases nobody was looking at. `regression`
cases (the majority) are the CI-gating stable baseline. `development` cases
(8) are known current gaps, deliberately not asserted as "must pass" material
for THIS phase — see e.g. `cc_bearing_generic_weak_001`, which is
**intentionally asserted to FAIL** against the geometrically-correct expected
value, documenting a real current defect rather than asserting against the
observed-but-wrong output.

## Reusable assertions (`eval/assertions.py`)

Every assertion function takes an `EvalContext` and returns
`list[AssertionResult]` (status `pass`/`fail`/`skip` — a skip is never
silently counted as a pass). None rely only on screenshots or face counts:

- `assert_capability_classification` / `assert_clarification_behavior` /
  `assert_refusal` — the generate/clarify/refuse/decompose/reject bucket
- `assert_allowed_assumptions` — no unlisted silent guess
- `assert_object_type` — family classification
- `assert_capability_level` — the DTO's `capability_level` against the family registry's maturity (docs/product-contract.md)
- `assert_units`
- `assert_bounding_dimensions` — against a **re-imported STEP solid**, not the DTO's self-reported bbox
- `assert_volume_range` — against the re-imported solid's kernel volume
- `assert_solid_validity` — re-imported STEP `isValid()` + STL watertight/manifold/single-body
- `assert_holes` — count + diameter, from the design's `selectable_holes` metadata, falling back (explicitly marked as a weaker signal) to STL mesh genus (topological through-hole count) when that metadata is empty
- `assert_feature_positions` / `assert_symmetry` — hole-center geometry, not visual inspection
- `assert_wall_thickness` — only when the design exposes it as an editable parameter; otherwise `skip`, never a fabricated pass
- `assert_semantic_features` — the design's own `features[].type` list
- `assert_export_and_reimport` — every requested format present, non-empty, and (for STEP/STL) actually re-openable/re-parseable, not just "a response existed"
- `assert_unchanged_except` / `assert_expected_spec_values` — the localized-edit invariant: everything not explicitly permitted to change must be byte-identical before/after, AND the edited field must have taken the correct new value (neither check alone proves a correct edit)

### Known assertion limitations (found while building this, not swept under the rug)

- `assert_symmetry` treats all holes' coordinates on one axis uniformly; it
  is not face/axis-aware, so it is not applied to parts with holes through
  multiple differently-oriented faces (see `cc_l_bracket_001`'s notes).
- `selectable_holes` metadata (the same data the face-edit UI depends on) is
  **not populated for several CadPlan-compositional families** (pipe spool,
  pipe tee, blind flange, flanged pipe branch, generic fitted box, the SVG
  flange drawing path) even though the holes are real and correctly cut.
  This is a genuine product-introspection gap discovered while building this
  harness — it likely also means face-edit/hole-resize does not work for
  these families today. The STL mesh-genus fallback is not a reliable
  substitute (confirmed mismatches on pipe_spool, pipe_tee, and a blind-hole
  case) since genus counts topological through-holes, not "count of named
  holes," and blind holes contribute 0 regardless of how many exist.
- Modifier-only features (e.g. a fillet) are not enumerated in `features[]`
  at all for at least one compositional family (`mounting_plate`) —
  `expected_semantic_features` is only asserted where directly confirmed.

## Running it

```
# Deterministic, offline, mock provider — the default. This is the CI subset.
python -m eval.cli run

# One suite only
python -m eval.cli run --suite known_families

# Skip held-out cases (e.g. for a quick local check)
python -m eval.cli run --split development --split regression

# Validate fixtures against the schema without running anything
python -m eval.cli validate

# Live, OpenAI-backed (never automatic; requires OPENAI_API_KEY)
python -m eval.cli run --live --repeats 3
```

`run` always writes both a JSON and a Markdown report under `--out` (default
`settings.eval_report_dir`) and prints the Markdown summary to stdout. Exit
code is non-zero if any case has status `fail` (not merely `no_signal`), so
`python -m eval.cli run` doubles as a CI gate over the deterministic subset.

Every report records: timestamp, `live` flag, provider, model (live only),
a **prompt-version fingerprint** (a sha256 of the actual system-prompt
constants the running process will use — changes exactly when a prompt
changes, no manual bumping required), git commit (best-effort, 3s timeout —
this environment has a known git/iCloud interaction that can hang `git`, so
this is never allowed to block a run), repeats, and (for `--live`) latency,
retries, and token/cost estimates captured by wrapping the OpenAI SDK client
after construction (`live_instrumentation.py`) — nothing in `app/llm/`
tracked usage before this; mock/offline runs are unaffected and unchanged.

### Critical ordering constraint

`eval/bootstrap.py` imports nothing from `app.*` or the rest of `eval.*` and
must be the only thing imported before `bootstrap_environment()` runs. This
is not a style preference: `app.config.Settings.load()` reads a `.env` file
via `os.environ.setdefault` exactly once at import time, and this repo's
`backend/.env` sets `LLM_PROVIDER=openai` with a real API key. Importing
`eval.runner` (or anything that imports `eval.assertions` →
`app.cad.tolerance` → `app.config`) before the offline default is forced into
`os.environ` lets the `.env` file's value silently win — turning a default
"offline" run into an unintended, costly live OpenAI call. **This exact bug
was hit once while building this harness** (a smoke-test run made one real
live call before the fix) and `eval/cli.py`'s import order is the fix;
`eval/runner.py` carries a docstring warning against reintroducing it.

## Regression detection

`tests/test_eval_harness_regressions.py` (pytest, part of the normal suite)
proves the harness actually catches injected regressions rather than
passing regardless of the code:

- a dimension regression (patching the bracket template default width)
- a feature/hole-count regression (dropping a hole from a spec before assertion)
- an authorization regression (monkeypatching `get_owned_design` to ignore ownership) — run through the `security` workflow against a scoped-down copy of the phase-3 ownership test

Each test asserts the harness reports the injected case as `fail` with the
correct assertion name in the failure detail — not just "something failed."

## CI

`.github/workflows/eval.yml` runs `python -m eval.cli run` (offline,
deterministic, no secrets required) on every push/PR, after the backend
pytest suite. It is a separate job from pytest so a fixture/harness issue is
never confused with an application regression in CI output.

## What this baseline does NOT cover yet

- Raster/vision drawing cases (`requires_live: true`, 4 of the 100 cases) —
  the offline mock provider cannot read pixels; these need `--live` against
  real OpenAI vision, which was not run for this baseline (no API budget
  allocated for this phase; see the final report for the exact offline
  numbers this phase established).
- `face_edit` (a fourth edit API alongside `modify`/`circle_edit`/
  `localized_edit`) has no cases yet — natural next addition to the
  `modifications` suite.
- DXF thickness-override and units-override query-parameter variants
  (`tests/test_drawing_to_cad.py::test_dxf_thickness_override_beats_annotation`,
  `test_units_inch_scales_vector_geometry`) aren't covered — the harness's
  drawing executor doesn't yet pass extra multipart form fields beyond
  `sync=true`.
