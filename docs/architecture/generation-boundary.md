# The Generation Boundary: How Model Output Becomes Geometry

**Purpose.** This document answers one question precisely: *what is the complete
path from an OpenAI response to a CadQuery solid, and is there any point on
that path where model-controlled or user-controlled text could be
interpreted as code?* It is written from direct inspection of the current
tree (branch `hardening/export-integrity`, HEAD `68f3eba`), not from prior
documentation. Where this session leaned on a prior audit
(`docs/production-readiness.md`, 2026-07-19 baseline `a07af49`), that is
called out explicitly as *previously verified, re-confirmed this session* or
*previously verified, not independently re-confirmed*.

---

## 1. The rule the codebase enforces

`backend/app/llm/base.py:1-19` states the contract in the module docstring:

> "A provider takes a natural-language prompt and returns a JSON-serializable
> dict describing the part. It must NOT return executable code — only data
> that we then validate against our strict schema."

`backend/app/llm/base.py:236-240` (comment, verified present in the current
`LLMProvider` ABC): there is **deliberately no `cad_program()` hook** on the
interface — a historical hook of that shape existed, executed the string it
returned, and was removed (see Section 5).

Every provider method that exists today returns one of exactly two shapes:
plain text (`str`) for clarification/explanation strings, or a `dict` that is
immediately validated by a specific Pydantic model. Nothing in the interface
can return a callable, a code object, or a string that any part of the
codebase subsequently `exec`s.

---

## 2. The three geometry-producing pipelines

LunaiCAD has three independent ways to turn a request into a CadQuery solid.
All three terminate in repo-authored Python functions that read only
*numbers* out of validated model objects — never strings that get
interpreted.

### 2a. Deterministic templates (no model involved)

`backend/app/cad/registry.py` -> `get_template(spec.object_type).build(spec)`,
called from `backend/app/export/exporter.py:120-128` (`build_solid`).
`spec.object_type` is `app.schemas.design_spec.ObjectType`, a **closed Python
`Enum`** (`design_spec.py:17-49`) — Pydantic rejects any value outside the
enum before `build_solid` is ever reached, so the template lookup can never
be redirected by an arbitrary string. Each template is a hand-written
CadQuery builder function living in `backend/app/cad/templates/*.py`.

### 2b. The CadPlan compiler (`app/cad/plan/`) — primary route since v0.4-GEN

Entry: `design_service._try_cad_plan` (`design_service.py:1525`) ->
`app.cad.plan.planner.plan_from_prompt(prompt, provider)`
(`planner.py:40-51`):

```python
def plan_from_prompt(prompt: str, provider) -> CadPlan | None:
    raw = None
    try:
        raw = provider.plan_cad(prompt)
    except NotImplementedError:
        raw = None
    plan = _coerce_plan(raw)               # CadPlan(**raw), Pydantic
    if plan is not None and (plan.is_buildable() or plan.clarification_required):
        return plan
    return None
```

`_coerce_plan` (`planner.py:31-37`) wraps construction in
`try/except pydantic.ValidationError: return None` — a provider response that
fails schema validation is discarded, never partially trusted.

Compile: `build_and_validate(plan)` (`planner.py:63-68`) ->
`compile_cad_plan(plan)` in `backend/app/cad/plan/compiler.py:513` onward.
The dispatcher is a closed `if f.kind == FeatureKind.box: ... elif f.kind ==
FeatureKind.plate: ...` chain (`compiler.py:533-601`) against `FeatureKind`,
a Python `Enum` (`schema.py:30-60`), ending `else: raise
CadGenerationError(f"unsupported feature kind '{f.kind}'")`. Every numeric
input flows through helpers (`_dia`, `_pos`, `_require`, `Feature.p()` in
`schema.py:129-137`) that coerce to `float` and clamp to a bound
(`_MAX_DIM = 5000.0`, `schema.py:27` and `compiler.py`) — a string payload in
a numeric field either fails `float()` (Pydantic validation error, request
never reaches the compiler) or is clamped, never interpreted.

Grep of `compiler.py` for `eval(`, `exec(`, `compile(`, `subprocess`,
`os.system`, `getattr(` on-a-model-string, `__import__`: **zero matches**
(verified this session).

### 2c. The allowlisted feature-graph interpreter (`app/cad/feature_graph.py`)

Used when `object_type == "feature_graph"` (`exporter.py:112-118`) or via
`provider.plan_feature_graph()`. This is the most model-adjacent of the three
paths and the one most directly modeled on "the LLM emits an operation
list" — so it is the one worth quoting in full.

```python
# backend/app/cad/feature_graph.py:16-22
_ALLOWED = {
    "box", "cylinder", "tube", "hex_prism", "polygon_prism", "cone", "sphere",
    "extrude_profile", "revolve_profile", "cut_hole", "counterbore", "countersink",
    "slot", "stepped_slot", "rectangular_cutout",
    "circular_pattern", "linear_pattern", "boolean_union", "boolean_cut",
    "union", "subtract", "fillet", "chamfer", "translate", "rotate", "mirror",
}
```

```python
# feature_graph.py:33-37 — every numeric parameter passes through this
def _p(params: dict, key: str, default: float = 0.0) -> float:
    v = float(params.get(key, default))
    if not math.isfinite(v) or abs(v) > _MAX_DIM:
        raise CadGenerationError(f"parameter '{key}'={v} is out of range")
    return v
```

```python
# feature_graph.py:105-109 — the dispatch itself
op = _OP_ALIASES.get(raw.get("op"), raw.get("op"))
...
if op not in _ALLOWED:
    raise CadGenerationError(f"operation '{op}' is not allowed")
```

Dispatch after the allowlist check is a literal `if op == "box": ... elif op
== "cylinder": ...` chain (`feature_graph.py:114-219`) — never `getattr(cq,
op)` or any string-keyed lookup into a namespace. `CADFeatureGraph.operations`
is capped at `max_length=200` (`complex_cad.py:91`); pattern `count` is
bounded `1 <= count <= 200` (`feature_graph.py:195-197`); polygon `sides` is
bounded `3 <= sides <= 64` (`feature_graph.py:60-62`).

**This is also the mechanism CadQuery itself uses to write export files** —
`backend/app/export/exporter.py:77-99` (`_export_bytes`) calls
`cq.exporters.export(solid, str(tmp_path), **kwargs)` where `tmp_path` comes
from `tempfile.NamedTemporaryFile(suffix=suffix, delete=False)` with a
**hardcoded** `suffix` (`".stl"` or `".step"`, never derived from request
data) — there is no user-influenced filesystem path anywhere in the export
step (verified this session, full file content obtained via
`git show HEAD:backend/app/export/exporter.py` after the working-tree copy
was transiently inaccessible — see Section 7).

---

## 3. Where OpenAI output is parsed, validated, normalized, or compiled — full inventory

| # | Stage | File : line | What happens |
|---|---|---|---|
| 1 | Request construction | `backend/app/llm/openai_provider.py:94-104,148-164` | `_structured()` builds a Responses API call with `text.format = {"type": "json_schema", "schema": <hand-authored schema>, "strict": False}`. Schemas: `backend/app/llm/schemas.py` (`DESIGN_SPEC_SCHEMA`, `CAD_PLAN_SCHEMA`, `CAD_FEATURE_GRAPH_SCHEMA`, `DRAWING_INTERPRETATION_SCHEMA`, `DESIGN_MODIFICATION_SCHEMA`, `GENERAL_CAD_PLAN_SCHEMA`). `strict: False` is required because `dimensions`/`params` are open numeric maps; the schema comment (`schemas.py:6-7`) is explicit that Pydantic re-validation afterward is "the real safety boundary," not the JSON Schema. |
| 2 | Raw text extraction | `openai_provider.py:297-312` (`_output_text`) | Reads `resp.output_text` or walks `resp.output[].content[].text`. Raises `ValueError` if no text is found — never falls back to executing anything. |
| 3 | JSON parse | `openai_provider.py:164` (`parse=json.loads`) | Standard library `json.loads` — not `eval`, not `yaml.load`. A malformed JSON string raises `json.JSONDecodeError`, caught by the model-fallback loop in `_run()` (`openai_provider.py:106-146`), which tries the next model in the fallback chain, then raises `LLMUnavailableError` (a clean 503) if all models fail. |
| 4 | Structural/type validation | `app/schemas/design_spec.py` (`DesignSpec`, `DesignModification`, `Hole`), `app/schemas/drawing_spec.py` (`DrawingInterpretationSpec`), `app/schemas/complex_cad.py` (`CADFeatureGraph`), `app/cad/plan/schema.py` (`CadPlan`, `Feature`) | Pydantic `BaseModel`s with `Field(gt=, le=, max_length=)` bounds on every numeric/string field. Enums (`ObjectType`, `Units`, `ManufacturingMethod`, `HoleType`, `CADOpType`, `FeatureKind`) close the set of accepted string values. |
| 5 | Normalization / coercion | `app/schemas/coerce.py` (`to_float`, `clamp`, `coerce_float_map`), field validators e.g. `design_spec.py:171-179` (`_coerce_dimensions`), `drawing_spec.py:217-225` (`_coerce_overall`) | Converts strings like `"O12"` (diameter symbol) or `"approx 90mm"` to floats via regex, or drops the key — never evaluates the string as an expression. |
| 6 | Repair-on-failure | `openai_provider.py:185-194` (`repair`), `design_service.py:1576-1584` (CadPlan repair pass), `drawing/interpret.py:111-122` (`_sanitize_drawing_raw` -> truncate over-long strings, then re-validate) | Re-prompts the model with the Pydantic error text and re-validates the new response with the same schema — repair never lowers the validation bar. |
| 7 | Compile | Section 2 above (three pipelines) | Validated model object -> CadQuery geometry via fixed dispatch. |

---

## 4. Drawing (vision) path — the same boundary, one more step

`backend/app/drawing/interpret.py:interpret_image` sends the uploaded image
as a base64 `data:` URL (`openai_provider.py:238-288`,
`interpret_drawing`) — never a path, never a fetched URL — so there is no
SSRF surface in how the image reaches OpenAI. The response goes through the
identical `json.loads` -> `DrawingInterpretationSpec(**raw)` -> (on
`ValidationError`) `_sanitize_drawing_raw` retry -> (on second failure)
`_partial_interpretation` degrade path (`interpret.py:105-160`), which keeps
only fields that pass `float()` coercion. A confirmed interpretation is
mapped to a `DesignSpec` by `drawing/interpret.py:to_design_spec` (data-only
dict construction) or to a `CadPlan`/feature-graph via
`app/services/drawing_to_spec.py` (not fully re-read this session — see
Section 7); either way it re-enters the same validated pipelines in Section 2.

---

## 5. Historical removal (context, not re-litigated)

`docs/production-readiness.md` documents that a `provider.cad_program()`
hook and an executor (`app/generation/code_sandbox.py`, `compiler.py`,
`cad_programs.py`, `scad_runner.py`) existed prior to baseline `a07af49` and
were removed in that baseline's Phase 1. **Re-verified this session**: all
four files are absent from the current tree
(`ls backend/app/generation/` does not list them); `grep -rn "cad_program"
backend/app/` returns zero matches; `LLMProvider`, `MockLLMProvider`, and
`OpenAIProvider` expose no `cad_program` attribute (confirmed by reading
`llm/base.py`, `llm/mock_provider.py`, `llm/openai_provider.py` directly).
`backend/tests/test_no_model_code_execution.py` (189 lines, full file read)
is a **repo-authored, currently-present regression test** that:

- Asserts `ModuleNotFoundError` on importing each of the four removed modules.
- Asserts none of the three provider classes expose `cad_program` (or
  `generate_code`/`generate_program`/`write_code`).
- Runs a **static AST scan** over every `.py` file under `app/` asserting no
  `ast.Call` node has `func.id in {"exec", "eval", "compile", "__import__"}`.
- Asserts no module contains the string `sys.executable` (no shelling out to
  a Python interpreter).
- Feeds 16 known bypass strings (`"__import__('os').system('id')"`,
  `"(1).__class__.__bases__[0].__subclasses__()"`, `"$(id)"`, etc.) as both
  operation names and parameter values into `build_feature_graph` and asserts
  every one is rejected.

This test's *existence and content* were verified by direct read this
session. Whether it currently **passes** could not be independently
re-confirmed by running pytest — see Section 7 (verification blocked; the
dev `.venv` was deleted mid-session per user direction to clear an unrelated
environment fault, and needs `pip install -r requirements.txt` before the
suite can run again).

---

## 6. Trust boundary summary

```
UNTRUSTED
  - OpenAI response text          - Uploaded drawing bytes
  - User prompt / hint / notes    - Client-supplied form fields
        |
        |  json.loads (stdlib only)
        v
VALIDATION (Pydantic, closed schemas -- the real boundary)
  DesignSpec / DesignModification / CADFeatureGraph / CadPlan /
  DrawingInterpretationSpec -- enums close string sets, Field()
  bounds close numeric ranges, unknown ops rejected
        |
        |  only validated model objects cross
        v
TRUSTED (repo-authored Python only)
  - Template builders (app/cad/templates/*.py)
  - CadPlan compiler (app/cad/plan/compiler.py) -- enum dispatch
  - Feature-graph interpreter (app/cad/feature_graph.py) --
    allowlist dispatch, no dynamic lookup
  - cq.exporters.export() to a tempfile-generated path only
```

No arrow in this diagram crosses from UNTRUSTED directly into TRUSTED. Every
path observed this session goes through the VALIDATION layer first.

---

## 7. Known unknowns / not independently verified this session

- **`app/services/drawing_to_spec.py`** (vector/profile-to-CadPlan mapping
  for the DXF/SVG deterministic drawing path) was not read this session —
  its `analysis_from_vector`/`plan_from_analysis` functions are referenced
  from `routers/drawings.py` but their internals are unverified. Given the
  consistent pattern everywhere else (Pydantic model -> numeric dispatch),
  risk is assessed as low, but this is an inference, not a read.
- **Whether `test_no_model_code_execution.py` currently passes** — the file
  was read and its assertions are sound, but pytest could not be run to
  completion this session (see the audit doc's Verification section). This
  is a *content-verified, execution-unverified* claim.
- **A hypothetical future provider** — `llm/factory.py` only builds
  `MockLLMProvider` or `OpenAIProvider`. If a third provider is ever added,
  the AST-scan test and the "no `cad_program`" parametrized test would need
  to keep including it (`test_no_model_code_execution.py:38-43` hardcodes
  the provider class list) — a provider added without updating that list
  would silently escape this specific guard, though it would still be caught
  by the AST scan if it tried to add an executor to `app/`.
- **`app/cad/plan/deterministic.py`** (the offline/mock CadPlan planner) was
  read only via a background research agent, not by the primary session
  directly — its findings are incorporated but not independently
  re-confirmed by a second read.

---

## 8. Remediation implemented (this phase)

Following a remediation task scoped to "all production-reachable
model-execution and injection risks identified in the audit," the audit's
own completion gate found **no such path existed** (Section 5 documents the
prior, already-landed removal). The following defense-in-depth hardening was
implemented instead, closing the gaps the audit did flag as open or
unverified:

- **`extra="forbid"` added to every model that validates raw provider
  output**: `DesignSpec`, `Hole`, `DesignModification`
  (`app/schemas/design_spec.py`); `CADIntentClassification`, `CADPrimitive`,
  `CADBooleanOperation`, `CADPatternOperation`, `CADFilletChamferOperation`,
  `CADFeatureGraph`, `ComplexCADPlan` (`app/schemas/complex_cad.py`);
  `Feature`, `Operation`, `Expected`, `CadPlan` (`app/cad/plan/schema.py`);
  every `DrawingInterpretationSpec` sub-model — `DrawingDimensionSpec`,
  `DrawingHoleCalloutSpec`, `DrawingSectionSpec`, `DrawingViewSpec`,
  `DrawingAssumption`, `DrawingClarificationQuestion`
  (`app/schemas/drawing_spec.py`). Previously, an unrecognized field in
  provider output was silently dropped (Pydantic's default `extra="ignore"`);
  it is now rejected outright, matching the task's "forbidden unknown fields"
  requirement.
  - **One deliberate, documented exception**: `DrawingInterpretationSpec`
    itself keeps `extra="forbid"` but adds a `model_validator(mode="before")`
    (`_strip_computed_fields`) that first drops exactly two known keys —
    `actionable` and `generate_with_assumptions_available` — before
    validation. Both are `@computed_field` properties serialized into every
    response the frontend receives, and `POST /api/drawings/confirm`
    legitimately re-validates that exact round-tripped JSON as its request
    body. Discovered by the full test suite (`test_drawing.py::test_drawing_interpret_and_confirm_endpoints`,
    `test_drawing_v036.py::test_interpret_with_hint_and_confirm` failed
    with `422 extra_forbidden` on first application of blanket `extra="forbid"`)
    — fixed surgically rather than reverting the hardening entirely.
- **`CADFeatureGraph.operations` gap closed**: this field was (and remains)
  typed `list[dict]` rather than a discriminated union of the four typed
  operation models already declared in the same file (`CADPrimitive` et al.
  were dead validation code — never referenced as the field's type). Added
  `_validate_operations` (a `field_validator`) that rejects any operation
  dict with a key outside `_OPERATION_ALLOWED_KEYS` (the exact set
  `build_feature_graph` ever reads: `op, id, params, at, target, tool,
  source, count, axis, plane`), enforces `id` is a non-empty string ≤40
  chars, and caps `params` at 32 entries — closing the one place a
  provider-controlled dict reached the interpreter with no schema-level
  shape check at all (numeric bounds on individual param *values* were
  already enforced later, at build time, by `feature_graph.py`'s `_p()`).
- **`app/database.py` raw-SQL question resolved**: read in full this phase
  (72 lines). The only `.execute()` calls are three hardcoded PRAGMA string
  literals (`journal_mode`, `synchronous`, `busy_timeout`) with no
  interpolation of any kind — confirmed **no** raw SQL construction from any
  variable anywhere in the data-access layer.
- **`CAD_ENGINE=legacy` resolved**: `backend/tests/conftest.py`'s
  `legacy_engine` fixture confirms this is a deliberately-maintained,
  still-tested alternate deterministic pipeline (the pre-CadPlan
  template-first route), not dead or forgotten code. It goes through the
  identical `DesignSpec` Pydantic-validation boundary as the primary route —
  same trust model, no separate review needed.
- **Adversarial test coverage added** — `backend/tests/test_adversarial_injection_hardening.py`
  (54 tests): a `HostileProvider` that returns attacker payloads (`'; DROP
  TABLE...'`, `__import__('os').system('id')`, `open('/etc/passwd').read()`,
  path traversal, template-expression shapes, control bytes, etc.) in
  *every* field of *every* provider method (`parse_prompt`, `plan_cad`,
  `plan_feature_graph`, `interpret_drawing`) and asserts each is either
  rejected by validation or absorbed as inert text; a real network-call
  canary (`socket.socket.connect` tripwire) and subprocess canary
  (`subprocess.Popen`/`os.system` tripwires) around a battery of
  adversarial HTTP requests; a secret-leak battery against the actual
  configured `DATABASE_URL`/`OPENAI_API_KEY` values; a STEP-metadata test
  proving an attacker string in `notes`/`visual_notes` never reaches the
  exported STEP/STL bytes; and capability-bypass checks (dev-only routes
  closed when `dev_mode=False`, no request field can select the LLM
  provider). All 54 pass.
- **Production-startup rejection given comprehensive coverage** —
  `backend/tests/test_production_startup_hardening.py` (17 tests): every
  individual unsafe condition `Settings.production_problems()` claims to
  reject (mock provider, missing API key, default/short/missing JWT secret,
  missing `DATABASE_URL`, bad S3/storage config, wildcard/localhost CORS,
  localhost `PUBLIC_BASE_URL`, `dev_mode=true`) is now asserted individually,
  plus that a fully-valid production config raises nothing and that
  `TESTING=true`/`APP_ENV=development` correctly bypass every check. Prior
  coverage exercised exactly one case (wildcard CORS). All 17 pass.

See `docs/production-readiness-audit.md` for the full before/after findings
table and exact test-run evidence.

## 9. Tenant isolation, upload, and export-artifact hardening (this phase)

Scope: every route touching designs, versions/modifications, face edits,
drawing jobs, uploaded source files, previews/thumbnails, generated
STL/STEP/GLB files, packages, job status, and validation reports — plus
centralized upload validation and artifact/export storage security. The
codebase already had extensive, previously-verified controls here (a
334-line dedicated authorization test suite, a single shared upload gate,
private-by-default storage with owner-checked routes never a raw bucket
URL — see §6 and the audit's Q2/Q3 completion-gate answers). This phase
closed the specific gaps that were still open or unverified:

- **Ownership moved to the database query boundary.** `_owned_or_404`
  (`app/routers/designs.py`) and the two `drawings.py` debug routes
  (`sketch_debug`, `sketch_debug_overlay` — which had drifted into their own
  duplicated fetch-then-check instead of reusing the shared helper) now all
  call a single new `design_service.get_owned_design(db, design_id,
  user_id)`, which enforces ownership with a JOIN-filtered query
  (`select(Design).join(Project).where(Design.id == id, Project.user_id ==
  user_id)`) rather than fetching the row by id and checking it afterwards
  in application code. Externally-visible behavior (404, indistinguishable
  from not-found) is unchanged and remains covered by the existing
  authorization suite; a new regression test also proves a design owned by
  an orphaned project (`Project.user_id` is nullable) can never be adopted
  or read by any authenticated user, since the JOIN can never match
  `user_id == None`.
- **Universal solid-validity gate — the concrete "no fake/partial STEP"
  fix.** Added `_assert_valid_solid()` (`app/export/exporter.py`), an OCCT
  BRep `isValid()` check. It previously ran, gated to exactly one object
  type (`inline_4_crankshaft`, via `TOPOLOGY_GATED_TYPES`), inside only one
  of the codebase's *two independent* STEP/STL-writing entry points. Every
  other object type, and the entire second entry point
  (`app/cad/plan/compiler.py::export_solid`, used by the CadPlan compiler
  *and* the assembly/frame-family builders) had **no BRep-validity check at
  all** — a self-intersecting/corrupt solid could be exported as long as
  the exporter call itself didn't throw and the file wasn't literally
  empty. `_assert_valid_solid()` now runs unconditionally, immediately after
  the solid is built and before any STL/STEP bytes are computed, in *both*
  entry points, raising the same well-tested `CadGenerationError` used
  throughout the pipeline's repair/fallback/`_store_failed_safe` handling.
  Confirmed (by test) not to regress the existing disconnected-body
  detection: `isValid()` checks self-intersection/corrupt faces, not
  connectivity, so two valid, non-touching boxes still pass this gate and
  are still caught downstream by the dimension-report's component-count
  check, exactly as before.
- **Upload parsing wall-clock timeout.** All three upload endpoints
  (`/api/drawings/interpret`, `/generate`, `/to-cad`) are `async def`
  handlers that called the synchronous, CPU-bound `inspect_upload()` gate
  directly in the coroutine — every byte/pixel/entity/page limit bounds the
  *input*, but nothing bounded how long a pathological-but-within-limits
  file (deeply nested SVG groups under the node cap, a slow-to-open PDF)
  could take to parse, and a slow parse would stall the asyncio event loop
  for the entire worker process, not just the one request. Added
  `upload_guard.inspect_upload_with_timeout()`, which runs the existing gate
  in a one-worker `ThreadPoolExecutor` under a 10s budget
  (`UPLOAD_PARSE_TIMEOUT_SECONDS`), raising a new `UploadTimeout` (422) on
  expiry; all three endpoints now call this wrapper instead of the bare
  function.
- **Signed URL default shortened + persisted-URL hygiene confirmed.**
  `S3_SIGNED_URL_TTL` default reduced from 3600s to 900s (still fully
  configurable) — a presigned URL carries no access check of its own, so it
  is only as private as its lifetime. Added a regression test for the
  invariant that already held: `ExportFile.url` (the DB-persisted value) is
  always the app's own owner-checked route
  (`/api/designs/{id}/files/{fmt}`), never a raw signed URL — the presigned
  URL itself is minted fresh, in memory, on every authorized download
  request and never cached or persisted.
- **Archive rejection confirmed with test evidence, no code change
  needed.** A ZIP (or any other unrecognized container), even truthfully
  named/typed, was already rejected by the existing magic-byte type
  detection — added `test_zip_archive_is_rejected_on_every_upload_endpoint`
  to close this out with evidence instead of inference.
- **No OCR dependency exists anywhere in the codebase** (confirmed by a full
  search for `pytesseract`/`tesseract`/`ocr`) — the "don't add OCR" rule is
  trivially satisfied; the existing references to "OCR" in
  `app/drawing/normalize.py` are about compensating for decimal-point loss
  in the *vision model's own* text extraction, not a separate OCR engine.
- **New test file** — `backend/tests/test_ownership_export_hardening.py`
  (16 tests, all passing): query-boundary ownership (unit + HTTP),
  orphaned-project unreachability, the shared debug-route ownership helper,
  the universal solid-validity gate (unit-level for both entry points, plus
  an end-to-end check that disconnected-but-valid geometry still reaches
  `critical_failure`), the upload timeout wrapper (unit + endpoint-level
  422 mapping with no traceback/path leak), the ZIP-rejection regression,
  the signed-URL TTL default, and the persisted-URL hygiene invariant.

One pre-existing, unrelated test failure was found (not introduced by this
phase) and is documented rather than silently left out of the count — see
`docs/production-readiness-audit.md` §14 for the diff-based evidence that
it predates this phase's changes.
