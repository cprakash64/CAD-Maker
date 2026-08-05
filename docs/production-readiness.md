# LunaiCAD Production Readiness — Phase 0 Audit

**Status:** Phase 0 (evidence-based audit) — in progress, no code changed yet.
**Date:** 2026-07-19
**Baseline commit:** `a07af49` (branch `main`, clean working tree at audit start)

Every claim below is anchored to a file and line verified by reading the
repository. Items I have **not** yet verified are listed explicitly in
[Not yet audited](#not-yet-audited) rather than assumed.

---

## 1. Verified architecture

The prompt's description of the architecture is broadly accurate. Confirmed:

| Capability | Verified location |
|---|---|
| FastAPI app, CORS, request logging, `/health` | `backend/app/main.py:30,52,78` |
| Routers: auth, designs, drawings, templates, capabilities | `backend/app/main.py:44-49` |
| `/api/drawing-to-cad` compatibility alias | `backend/app/main.py:47` (`drawings.alias_router`) |
| JWT auth + dependency | `backend/app/auth/deps.py`, `backend/app/auth/security.py` |
| Postgres/Alembic | `backend/alembic/`, `psycopg[binary]` in `requirements.txt` |
| Rate limiting | `backend/app/rate_limit.py`, applied via `rate_limit("drawing")` |
| Storage abstraction | `backend/app/storage/storage.py` |
| CadQuery 2.7.0 geometry | `backend/requirements.txt` |
| Deterministic family templates (28 families) | `backend/app/cad/templates/*.py` |
| Part Family Contract | `backend/app/cad/part_family.py`, `backend/app/cad/contract.py` |
| Face selection / localized editing | `backend/app/editing/face_edit.py`, `localized.py` |
| Next.js 14 App Router frontend | `frontend/src/app/` |
| Vitest + tsc typecheck configured | `frontend/package.json` scripts |

**The codebase is materially more mature than the task brief assumes.** Several
things the brief asks to "add" already exist and work. Notable examples:

- **OpenAI calls are already centralized** in `backend/app/llm/openai_provider.py`
  behind a provider interface (`backend/app/llm/base.py`) with a factory
  (`backend/app/llm/factory.py`).
- **The current OpenAI SDK Responses API is already in use** —
  `self._client.responses.create(**kwargs)` at `openai_provider.py:128`.
- **Timeouts and bounded retries already exist** — `openai_provider.py:73-80`
  sets `timeout=settings.openai_timeout_seconds` and
  `max_retries=settings.openai_max_retries` on the client, plus a per-call
  remaining-budget cap at `openai_provider.py:127`.
- **Export gating on critical validation failures already exists** —
  `_block_export_if_critical()` at `backend/app/routers/designs.py:205`, with the
  dev override explicitly restricted to `DEV_MODE`.

Work in later phases should therefore **extend** these, not replace them.

---

## 2. Findings

### F-1 — Latent LLM-code-execution path (CRITICAL, architectural)

**Gap.** The repository contains a full facility for executing LLM-authored
Python/CadQuery source:

- `backend/app/generation/code_sandbox.py` — `run_program(code, trusted=...)`;
  the module docstring states *"UNTRUSTED code (from an LLM) always runs in a
  subprocess"* (`code_sandbox.py:7-9`).
- `backend/app/generation/compiler.py:45,52` — `compile_prompt()` calls
  `provider.cad_program(prompt, ...)` and passes the returned
  `program.generated_code` straight into `run_program()`, with
  `trusted = (provider.name == "mock")` (`compiler.py:38`).
- Reachable from the live request path:
  `design_service.py:2895` → `_try_compiler()` → `compile_prompt()`.

**Actual current exposure — important nuance.** This path is **not live today.**
`cad_program()` is implemented *only* by the mock provider
(`backend/app/llm/mock_provider.py:378`). The base class returns `None`
(`backend/app/llm/base.py:236-241`), and `OpenAIProvider` does **not** override
it (verified: `grep "cad_program"` matches only `base.py`, `mock_provider.py`,
and `compiler.py`). With `LLM_PROVIDER=openai`, `compile_prompt()` therefore
returns `None` at `compiler.py:46` and the request falls back to the
deterministic template/feature-graph path. The Python that *does* execute comes
from `backend/app/generation/cad_programs.py`, which is repo-authored constant
code, not model output.

So this is a **latent** violation, not an active one. It matters because the
architecture actively invites the violation: any future provider that implements
`cad_program()` silently turns on arbitrary-model-code execution with no further
review.

**Why the existing lint is not a sufficient boundary.** `lint_code()`
(`code_sandbox.py:44`) is an AST **denylist**. It blocks a fixed set of names
(`_FORBIDDEN_NAMES`), attribute roots (`_FORBIDDEN_ATTR_ROOTS`) and dunder
access. Denylists of this shape are routinely bypassable — e.g. the root name
`cq` is not forbidden, so attribute traversal into a CadQuery submodule that
itself imports `os` (`cq.<...>.os.system`) is not caught by the root-name check,
and the subprocess harness (`_HARNESS`, `code_sandbox.py:131`) runs the body with
**full builtins**, not the restricted `_SAFE_BUILTINS` used in-process. The
subprocess also has no memory cap and inherits `PATH`.

**Risk:** Critical (latent). **Planned change:** remove the untrusted branch
entirely — delete `provider.cad_program` from the provider interface, drop the
`trusted=False` code path, and restrict `run_program` to repo-authored constants
(or replace it with direct function calls). Per the brief's non-negotiable rule,
geometry must come only from reviewed deterministic builders or an allowlisted
feature-graph interpreter.
**Verification:** a test asserting no provider exposes `cad_program`, plus a test
that `compile_prompt` cannot execute provider-supplied source.

> **Decision required before I act** — removing this touches
> `tests/test_v05_cad_compiler.py`, `tests/test_v05b_geometric_verifier.py` and
> the mock provider path. See [Open questions](#open-questions).

### F-2 — Anthropic provider contradicts the stated provider constraint (MEDIUM)

`backend/app/llm/anthropic_provider.py` exists, `anthropic==0.42.0` is pinned in
`backend/requirements.txt`, and `factory.py:17-20` routes `LLM_PROVIDER=anthropic`
to it. `config.py:285` validates `anthropic_api_key`.

This **pre-dates** this task; the brief says not to *add* Anthropic, and also says
to follow repository evidence and document conflicts. Documented here rather than
unilaterally removed — deleting a working provider is out of scope for a
production-hardening pass and is a product decision.
**Risk:** Medium (dependency surface + contradicts stated direction).
**Planned change:** none without direction. See [Open questions](#open-questions).

### F-3 — 53 duplicate `" 2"` files committed to git (MEDIUM)

`git ls-files | grep " 2\."` returns **53 tracked files** — macOS Finder
duplication artifacts, including **19 app modules** (e.g.
`backend/app/services/design_service 2.py`, `backend/app/cad/feature_graph 2.py`,
`backend/app/drawing/sketch_to_cad 2.py`) and ~13 test modules.

Verified: **no module imports any `" 2"` file** (grep for `design_service 2`,
`feature_graph 2`, `sketch_to_cad 2`, `drawing_jobs 2` returns no importers), so
the app modules are dead code. The duplicated **test** files, however, *are*
collected by pytest (`testpaths = tests`) because their basenames differ from the
originals — so CI runs stale forks of the suite and roughly doubles runtime.

**Risk:** Medium — stale divergent logic, doubled CI time, reviewer confusion.
**Planned change:** delete all 53. This is destructive, so it needs sign-off.
**Verification:** full suite green after removal.

### F-4 — Test suite is impractically slow on this machine (BLOCKER for gates)

`pytest --collect-only` did not finish within 10 minutes. Diagnosed rather than
guessed:

- `sample <pid>` shows **100% of samples blocked in `read()`** in
  `libsystem_kernel.dylib`, under `builtin_exec` → `PyEval_EvalCode` (import time).
- `lsof` on the blocked process shows it reading `matplotlib/.../_trifinder.pyc`
  — i.e. genuinely progressing through imports, not deadlocked.
- The venv is **932 MB**; the loaded-image list includes OCCT, **VTK 9.3**,
  matplotlib and ezdxf — hundreds of large dylibs under `~/Documents`, where
  macOS code-signing/XProtect validation serializes `dlopen`.
- Isolated, warm: `import OCP` 3.9 s, `import cadquery` 29.0 s.

This is primarily a **local environment** constraint, not a repo defect — but it
means the acceptance gate *"all pre-existing tests still pass"* cannot currently
be demonstrated here in reasonable wall-clock time. I will not report test
results I have not actually observed.

**Update — collection completed, exit 0.** `--collect-only` eventually finished
(pytest buffers collection output to the end, which is why the file stayed empty
throughout). Results:

- **1445 tests across 100 files, with zero collection/import errors.** The suite
  is structurally healthy; the cost is import/startup, not broken modules.
- Of those, **151 tests (10.5%) come from the 13 duplicated `" 2.py"` test
  files** listed in F-3.

This **corrects an earlier estimate of mine**: removing the duplicates cuts
~10% of the suite, *not* "roughly half". Wall-clock collection alone took on the
order of 20 minutes on this machine.

**Measured subset (actually run, exit 0):**

```
LLM_PROVIDER=mock APP_ENV=development TESTING=true \
  .venv/bin/python -m pytest tests/test_auth.py tests/test_part_family_contract.py -q
17 passed — 38.1 s wall (~13 s fixed startup/import; slowest call 4.58 s)
```

Both gate-relevant files pass on the current baseline. Extrapolating is
unreliable (per-test cost ranges from milliseconds to ~5 s for geometry tests),
but with ~20 min of import cost plus 1445 tests the full suite is plausibly
**45–90 minutes** on this machine. That range is an estimate, **not a measured
result** — the full suite has not yet been run end to end.

**Planned change:** if CI is similarly affected, propose fast/slow test markers.

### F-5 — Single Alembic revision (LOW, informational)

`backend/alembic/versions/` contains exactly one migration
(`2026_06_19_1603-7fbf0c6446aa_initial_schema.py`). Any schema work in later
phases must be additive and forward-safe from this baseline.

---

## 3. Positive findings (no action needed)

- **Ownership is enforced, not decorative.** `_owned_or_404()`
  (`routers/designs.py:197-202`) checks `design_service.user_owns_design(...)`
  and 404s (not 403 — no existence oracle). Every design route reviewed takes
  `Depends(get_current_user)`.
- **Drawing jobs are owner-scoped.** `job_status()` (`routers/drawings.py:105`)
  passes `user.id` into `drawing_jobs.get_job(job_id, user.id)` and 404s
  otherwise — no IDOR on job polling.
- **Test isolation is correct.** `tests/conftest.py` redirects `DATABASE_URL` and
  `STORAGE_DIR` to a temp dir *before* app settings are read, and gates the mock
  provider behind explicit `TESTING`/`APP_ENV` flags.
- **Mock provider is environment-gated.** `factory.py:10-14` refuses the offline
  mock unless `settings.mock_allowed`.

---

## 4. Not yet audited

Called out so nothing here is mistaken for a clean bill of health:

- Drawing upload pipeline: MIME sniffing, byte/pixel/page limits, SVG
  sanitization, DXF complexity limits, SSRF on document references.
- How drawing image bytes are passed to OpenAI (data URL vs. path vs. public URL).
- Prompt-injection handling for text extracted from drawings.
- Storage: signed-URL expiry, key generation, bucket privacy.
- CSRF/cookie posture; JWT `iss`/`aud`/`alg` validation specifics.
- Security headers (CSP, HSTS, X-Content-Type-Options, Referrer-Policy).
- Frontend typecheck/build/test status (not yet run).
- The seven named regression cases in the brief (drill jig, crankshaft, flanged
  pipe branch, drawing-to-CAD "unknown/0%", error-state distinctness, simple
  drawings, hole circularity) — **not yet reproduced or confirmed**.
- Whether durable job state / worker infrastructure exists beyond
  `services/drawing_jobs.py` (in-process `DrawingJob` objects observed at
  `routers/drawings.py:216`, which suggests state is lost on restart — the 404
  message at `drawings.py:115` explicitly says "or the server restarted").

---

## Open questions

These change architecture or are destructive, so I am not proceeding on them
unilaterally:

1. **F-1 removal scope** — remove the `cad_program`/untrusted-sandbox facility
   outright (my recommendation), or keep `run_program` for repo-authored constant
   programs and only delete the provider-authored entry point?
2. **F-2 Anthropic provider** — leave as-is (documented conflict), or remove the
   provider + dependency?
3. **F-3 duplicate files** — confirm deletion of all 53 tracked `" 2"` files.

---

---

# Phase 1 — Implementation (decisions F-1, F-2, F-3 approved)

Status: **implemented**. Scope was limited to security, repository cleanup,
test-collection performance and confirmed regressions. Billing, Redis, print
profiles, calibration values and frontend redesign were explicitly out of scope
and were not touched.

## F-1 — Model-authored code execution removed

**What was removed.** The provider→source→execute pipeline is gone entirely:

| File | Action |
|---|---|
| `backend/app/generation/code_sandbox.py` | deleted (the `exec`/subprocess executor) |
| `backend/app/generation/compiler.py` | deleted (called `provider.cad_program()`) |
| `backend/app/generation/cad_programs.py` | deleted (authored program source) |
| `backend/app/generation/scad_runner.py` | deleted (OpenSCAD subprocess; already dead) |
| `backend/app/llm/base.py` | `cad_program()` hook removed from the interface |
| `backend/app/llm/mock_provider.py` | `cad_program()` implementation removed |
| `backend/app/services/design_service.py` | `_try_compiler()` + `_store_program()` (81 lines) and the call site removed |

**Were the repo-authored programs used in production? No.** Verified before
removal: `compile_prompt()` obtained source via `provider.cad_program()`, which
only `MockLLMProvider` implemented. `LLMProvider.cad_program()` returned `None`
and `OpenAIProvider` never overrode it — confirmed by direct execution against
the pre-change tree:

```
OpenAIProvider has cad_program attr: True   (inherited from base)
cad_program() returns: None
=> compile_prompt() returned None before any execution
```

So with `LLM_PROVIDER=openai` the route was already inert and every request fell
through to the deterministic template / feature-graph pipeline. No trusted
program registry was therefore needed: there was no production behaviour to
preserve. `run_program(source: str)` is gone rather than replaced.

**What geometry generation now uses.** Only reviewed deterministic builders
(`app/cad/templates/*`, `app/cad/plan/deterministic.py`) and the allowlisted
feature-graph interpreter (`app/cad/feature_graph.py`), whose `_ALLOWED` set
rejects unknown ops and whose params are numerically coerced by `_p()`.

**Kept deliberately.** `semantic_verifier.py` and `stl_preview.py` — pure
analysis, no execution (`mesh_analysis` depends on the latter).

**Obsolete AST denylist.** Removed with `code_sandbox.py`. It existed solely to
police model-authored code and was not a sound boundary anyway: it was a
denylist, `cq` was not a forbidden attribute root, and the subprocess harness ran
the body with full builtins rather than the restricted in-process set.

**Residual.** The `designs.program_code` column is retained (nullable, no longer
written) so the change needs no destructive migration; `has_program` is now
always false for new designs. Historic rows keep their values.

## F-2 — Anthropic runtime removed

Verified first that nothing required it: no migration, no production import path
beyond the provider registry.

- Deleted `backend/app/llm/anthropic_provider.py`.
- `factory.py`: `LLM_PROVIDER=anthropic` branch removed.
- `config.py`: `anthropic_api_key` / `anthropic_model` settings removed;
  validation now states OpenAI is the only supported production provider and
  rejects any other value.
- `main.py`: Anthropic branch removed from the capability/health payload.
- `requirements.txt`: `anthropic==0.42.0` removed.
- `observability.py`: `anthropic_api_key` dropped from the redaction list.
- `.env.example`, `README.md`, `scripts/run_generation_regression.py` updated.
- `tests/test_providers.py`: conformance test replaced with tests asserting the
  module is gone and the factory rejects unknown providers.

The generic provider abstraction (`LLMProvider`, factory, mock) is retained.
`docs/CHANGELOG.md` keeps its historical mention as a record.

## F-3 — Duplicate `" 2"` files removed

All 53 tracked duplicates were compared before deletion, not blind-deleted:

- **51 were byte-identical** to their canonical counterpart (SHA-256 match).
- **2 differed** and were reviewed line by line:
  - `app/cad/feature_graph 2.py` — an older snapshot. Canonical is a strict
    superset (adds `tube`, `hex_prism`, `polygon_prism`, op aliases,
    counterbore/countersink/slot). Automated check: string literals and function
    definitions present in the duplicate but absent from canonical = **none**.
  - `app/services/design_service 2.py` — 3062 lines vs canonical 3193.
    Duplicate-only lines: **0** (a strict subset).
- **No unique change was lost.** Nothing referenced any duplicate (`git grep`
  across `.py`/`.json`/`.sh` returned no importers, fixtures or config refs).

## Test collection performance

| | Before | After |
|---|---|---|
| Collection wall-clock | ~20 min (never completed in a 10-min window) | **20.1 s** |
| Tests collected | 1445 | 1319 |

**Honest attribution.** Deduplication removed 151 tests (10.5%) and cannot
explain a 60× speedup. The dominant factor was that the original run was a
**cold** first load: `sample` showed 100% of samples blocked in `read()` during
module import, and the venv is 932 MB of OCCT/VTK/matplotlib dylibs under
`~/Documents`, where macOS code-signing validation serialises `dlopen`. Warm,
the same imports cost ~33 s (`OCP` 3.9 s, `cadquery` 29.0 s). The problem is
**environment-specific, not code-specific** — no source change would have fixed
it, and no heavy imports were moved. Deduplication is a real but secondary win.

No test was disabled, skipped or mocked away to achieve this.

## Regression reproduction

Reproduced against the current tree **before** any behaviour change. New file:
`backend/tests/test_named_regressions.py`.

| # | Case | Previous result | Fix | New result |
|---|---|---|---|---|
| 1 | Drill jig 120×80×6, 6 mm holes @25 mm generates on defaults | **Already passed** (no clarification) — but see 7 | none needed | pass (locked in) |
| 1b | …bounding box honours 120×80×6 | pass | none | pass |
| 2 | Inline-4 crankshaft routes to `inline_4_crankshaft` | **Already passed** | none | pass (locked in) |
| 3 | …does not silently become a generic shaft | **Already passed** | none | pass (locked in) |
| 4 | Flanged-pipe-branch drawing + hint routes to `flanged_pipe_branch` | **not reproduced** — needs drawing fixtures | deferred | not covered |
| 5 | Drawing-to-CAD returns unknown/0% on internal image failure | **partly confirmed** via unsupported MIME (below) | see 6 | pass for the MIME path |
| 6 | Unsupported MIME is a distinct outcome | **FAILED** — `.exe` + `application/x-msdownload` returned **HTTP 200** with `detected: unknown, confidence 0.2` | content-sniff via existing `detect_file_type()` on `/interpret` and `/generate`; unsupported → **415** | pass |
| 7 | Circular holes: count, diameter, spacing, export validity | **FAILED** — drill jig produced a blank plate: `selectable_holes: []`, `triangle_count: 12` | added `_plan_drill_jig` family | pass |

### Case 6 detail (security)

`/api/drawings/to-cad` already content-sniffed via `detect_file_type()`, but
`/interpret` and `/generate` did not — they used the client's `Content-Type`
verbatim (`media_type = file.content_type or "image/png"`). Arbitrary binary was
accepted and degraded into an "unknown / 0.2 confidence" interpretation instead
of an explicit rejection. Fixed by applying the repository's existing sniffing
helper to both endpoints and deriving the media type from the file's own bytes
(`_MEDIA_TYPE_BY_FILE_TYPE`), so a client header can never mislabel content
downstream.

**Residual weakness (pre-existing, now shared by all three endpoints):**
`detect_file_type()` is *magic-bytes-first*, but when no signature matches it
still falls back to the filename extension and then to `Content-Type` tokens
(`drawing_ingest.py:115-125`). So arbitrary bytes named `drawing.png` are still
accepted as PNG and handed to the image path. Raising the rejection to
"signature must match, extension is never sufficient" is a deliberate follow-up
rather than a silent change here, because `/to-cad` has always relied on this
fallback and tightening it could reject drawings that currently work. Tracked in
the next-phase list.

### Case 7 detail (scope-limited fix)

Root cause: the prompt matched the **last** dispatch rule in
`app/cad/plan/deterministic.py` (`"plate" in t`) → `_plan_plate`, which supports
only up to four *corner* holes plus a centre bore and has no notion of a hole
grid. There was no `drill_jig` predicate at all, so the defining feature of the
part was silently dropped.

Added `_plan_drill_jig()`, dispatched ahead of the generic plate rule, which
parses hole diameter and spacing and emits a proper grid.

**Important scoping caveat:** `deterministic.plan()` is the **mock/offline**
planner. Its own docstring states the limitation is intentional ("the OpenAI
planner handles arbitrary parts via the LLM"). This fix therefore makes
offline/dev mode faithful and gives real hole-regression coverage; it does
**not** prove the production LLM planner emits the same grid. Confirming that
requires a live OpenAI eval, which is out of scope for this run (no live calls
in the default suite).

### A second, unfixed drill-jig defect (documented, not addressed)

An alternate phrasing takes a different route and is still wrong:

```
"a drill jig 120mm x 80mm x 6mm with 6mm guide holes on 25mm spacing"
  → route=precision_template, object_type=drill_jig
  → bounding_box_mm z = 14.0   (requested 6.0)
  → selectable_holes = 0       (despite 15,720 triangles, so holes exist)
```

Two distinct problems: the `drill_jig` **template** ignores the requested
thickness, and hole metadata extraction does not populate `selectable_holes` for
it (which would break the frontend hole table and parametric editing). Unlike
case 7 this sits in a template used on the production path, so it is the highest
-value follow-up. Not fixed here because it is behaviour change beyond the
approved scope of this run.

## New tests added

| File | Purpose |
|---|---|
| `tests/test_no_model_code_execution.py` | 43 tests: removed modules unimportable; no provider exposes `cad_program`; **AST scan proving no `exec`/`eval`/`compile`/`__import__` anywhere in `app/`**; no `sys.executable` subprocess; 16 bypass strings rejected as op names and as param values; hostile-provider end-to-end |
| `tests/test_named_regressions.py` | the table above, plus cross-user 404s for designs, exports and drawing-job polling, and a path-traversal filename check |
| `tests/test_semantic_verifier_and_families.py` | replaces the compiler tests: verifier coverage kept; 9 ex-compiler family prompts asserted to still generate; 2 known gaps as strict `xfail` |
| `tests/test_geometric_verifier.py` | genus/hole analysis and "metadata that lies" coverage preserved, with geometry now built directly via CadQuery instead of executed source strings |

## Test results (actually observed)

Backend — full suite, after all changes:

```
LLM_PROVIDER=mock APP_ENV=development TESTING=true .venv/bin/python -m pytest -q
exit 0 — 1327 passed, 2 xfailed, 2 skipped, 0 failed, 0 errors
```

(The 2 xfails are the documented shaft-collar / flange-plate coverage gaps.
`pytest.ini` already sets `-q`, so passing `-q` again suppresses the summary
line; counts are from the progress output: 1331 outcomes = 1319 collected + 12
new regression tests.)

Frontend:

```
npx tsc --noEmit      → exit 0 (5.5 s)
npm test  (vitest)    → exit 0 — 3 files, 51 passed
npm run build         → exit 0 — 11 routes emitted, no errors
```

The production build emits all 11 routes (10 static, `/studio/[id]` dynamic),
87.3 kB shared first-load JS. No frontend source file was modified in this run;
`has_program` remains in the API contract (now always false), so the existing
`types.ts` contract is unchanged and the change is backward-compatible.

Note on timing: an earlier `tsc` run timed out at 10 minutes purely because the
backend suite was running concurrently; on an idle machine it takes 5.5 s. Any
wall-clock number in this document should be read with that contention caveat.

## Security verification

Verified in this run (test-backed unless noted):

| Check | Result | Evidence |
|---|---|---|
| Cross-user design read → 404 | pass | `test_named_regressions.py` |
| Cross-user export download (STL + STEP) → 404 | pass | `test_named_regressions.py` |
| Cross-user drawing-job polling → 404 | pass | `test_named_regressions.py`; `drawing_jobs.get_job(job_id, user.id)` |
| Arbitrary provider text cannot execute | pass | `test_no_model_code_execution.py` (43 tests) |
| No `exec`/`eval`/`compile`/`__import__` in `app/` | pass | AST scan over every module |
| No Python interpreter subprocess | pass | `sys.executable` scan |
| Arbitrary source strings cannot reach CAD runtime | pass | no `run_program(source)` boundary exists |
| Unsupported upload content cannot reach geometry parsers | **fixed** | `/interpret` + `/generate` now 415 |
| Uploaded filename cannot cause path traversal | pass | server-generated keys; `../../../../etc/passwd` returns <500 and is not echoed |
| Critical validation blocks export | pass (pre-existing) | `_block_export_if_critical`, `designs.py:205` |
| DEV_MODE cannot be on in production | pass (pre-existing) | `config.py:325` — startup raises `RuntimeError` |
| Export override requires DEV_MODE | pass (pre-existing) | `designs.py:214` |
| `OPENAI_API_KEY` never reaches the client bundle | pass | no reference in `frontend/src/`; only `NEXT_PUBLIC_API_BASE` is public |

Not attempted this run: SVG sanitisation depth, PDF page limits, DXF complexity
limits, SSRF on document references, CSRF posture, JWT `iss`/`aud`/`alg`
specifics, security headers (CSP/HSTS). These remain open from the Phase 0
"Not yet audited" list.

## Remaining risks

1. **Two ex-compiler prompts no longer produce geometry in mock mode** —
   "a shaft collar with an M6 clamp screw" (`unsupported`) and "a flange plate
   with 8 holes on a 100mm bolt circle" (clarification). Production was already
   on this path, so this is not a production regression, but it is a genuine
   deterministic-coverage gap. Tracked as strict `xfail` so CI flags them when
   family defaults land.
2. **The `drill_jig` template thickness/`selectable_holes` defect above.**
3. **Case 4 (flanged-pipe-branch drawing + hint) was never reproduced** — it
   needs drawing fixtures and provider mocking. No claim is made about it.
4. **Live OpenAI behaviour is unverified.** Every result here is from the
   deterministic mock provider. Cases 1–3 and 7 pass offline; production
   equivalence is unproven.
5. The full suite result is recorded below only if it actually completed.

## Deferred (print-first roadmap)

Per the approved direction — LunaiCAD is print-first, future default fabrication
intent `fdm_print`, general CAD secondary, initial calibration PLA @ 0.4 mm
nozzle. None of it implemented in this run. When it is: the profile schema may
carry a 0.5 mm nozzle field, but **no 0.5 mm value may be invented or
extrapolated from the 0.4 mm profile**, and generic profiles must never be
labelled tested/verified/calibrated.

## Acceptance criteria for this run

| Criterion | Status |
|---|---|
| `cad_program` gone from runtime interfaces and compiler flow | **pass** |
| No provider can return executable Python | **pass** (43 guard tests incl. AST scan) |
| Anthropic runtime support and dependency gone | **pass** |
| All reviewed duplicate `" 2"` files removed | **pass** (53/53) |
| No unique changes lost | **pass** (51 identical; 2 strict subsets, verified) |
| Targeted security/compiler/provider tests pass | **pass** |
| Named regressions reproduced, then fixed or confirmed passing | **partial** — cases 1,2,3,6,7 done; **case 4 never reproduced**; case 5 only via the MIME path |
| Test collection completes, or blocker documented with evidence | **pass** (20.1 s; cold-load cause evidenced) |
| No working deterministic family / drawing / validation / export behaviour intentionally removed | **pass, with one caveat** — 2 prompts that produced geometry *only under the mock compiler* now clarify instead; production was already on that path |
| No unrelated frontend redesign or product expansion | **pass** (no frontend source changed) |

Deliberately **not** claimed: that LunaiCAD is production-ready. This run closed
one critical latent security hole, removed a provider, cleaned the repo and fixed
two confirmed defects. The Phase 0 "Not yet audited" list is still largely open.

## Recommended next phase

1. Fix the `drill_jig` template thickness + `selectable_holes` extraction (real
   production path, highest value).
2. Reproduce case 4 with proper drawing fixtures.
3. Close the shaft-collar / flange-plate deterministic gaps (promotes 2 xfails).
4. Distinguish the remaining drawing failure modes (encoding failure, refusal,
   timeout, schema failure, genuine low confidence) as separate internal and
   user-visible states — only the unsupported-MIME path is distinct today.
5. Tighten `detect_file_type()` so a signature match is required and the
   filename extension is never sufficient on its own (see the residual weakness
   noted under Case 6).
5. Then Phase 2 generation policy (`generate_with_assumptions` vs
   `clarification_required`) as originally scoped.

## Changelog

- 2026-07-19 — Phase 0 audit opened. No application code modified.
- 2026-07-19 — F-1/F-2/F-3 implemented; unsupported-MIME rejection and the
  deterministic drill-jig family added; obsolete compiler tests replaced.
