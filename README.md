# SourceCAD AI Part Studio

Turn plain-English prompts into **editable, manufacturable, exportable** CAD
parts — brackets, enclosures, mounts, adapters, spacers, clamps, knobs and drill
jigs. This is parametric mechanical CAD, not decorative text-to-3D meshes.

**v0.2** sharpens three hero templates (mounting bracket, electronics enclosure,
drill jig / adapter plate) into demo-ready parts — rounded corners, finished
edges, real screw bosses and lids, and clearance / counterbore / countersink
holes — plus plain-English edits ("make it wider", "move the holes farther
apart"), a generated explanation of every part, and richer manufacturability
checks.

**v0.3 (private beta)** adds email/password **auth** with strict per-user
isolation, a first-class **OpenAI provider** (Responses API + Structured
Outputs), an **S3-compatible storage** backend with owner-checked downloads, a
**feedback** system, a 200+ prompt **eval harness** (`scripts/run_eval.py`), and
structured **observability** logging.

**v0.3.5 (drawing intelligence)** adds a studio **view toolbar** + PNG capture,
**2D drawing views** (top/front/right/left/iso as PNG & SVG with template
dimensions, rendered from the real model), **Drawing-to-CAD Assist** (upload a 2D
drawing → OpenAI vision extracts a validated `DrawingInterpretationSpec` you
confirm before generation), **point-and-prompt localized editing** (select a
hole/edge/face and edit just that feature), a one-click **CAD Package** download
(STEP + STL + DesignSpec + manufacturing report + drawing views), import docs,
and an advanced **inline-4 crankshaft** template.

**v0.3.6 (accuracy + interaction)** fixes the studio **view toolbar** + PNG
capture, adds **circle-to-edit** (draw a circle over a hole/edge/flange/face and
edit just that feature, resolved via stable feature IDs), makes **Drawing-to-CAD**
refuse to map complex unknown drawings to brackets (0.75 confidence gate +
"correct interpretation" hint + template override), adds a trusted **feature-graph
interpreter** and **ComplexCADPlan** routing for long/complex prompts, and adds
`flanged_pipe_branch` + `simple_gear_or_pulley` templates.

**v0.4-GEN (general CAD generation engine)** routes each prompt to the best
strategy — precision template → flexible feature graph → general planner /
restricted SCAD DSL → clarification — so a much wider range of plain-English
prompts produces real CAD. It adds tolerant schema coercion (numeric strings,
countersink-angle repair), feature-graph v2 ops (tube/counterbore/slot/…), new
parts (flange plate, shaft collar, bearing housing, pipe elbow), a self-repair
loop (gear teeth / hex / bore), and a 150-prompt regression benchmark. The SCAD
fallback runs as a sandboxed, statically-linted DSL (no include/import/file/shell,
timeout, temp-dir) and is STL-only; STEP is offered only for precision-template
and feature-graph models (never faked).

**v0.3.8 (generate-first)** makes the app build a reasonable first draft whenever
the prompt has enough info — missing *non-critical* details use documented
template defaults (surfaced as assumptions) instead of over-clarifying. Fixes
crankshaft routing (advanced types were missing from the OpenAI schema/prompt),
the OpenAI drawing-image path (data-URL + `detail:high`, real error surfacing,
hint fallback), and replaces "Something went wrong" with real backend errors plus
a "Generate with defaults" path. See [docs/CHANGELOG.md](docs/CHANGELOG.md) and
[docs/QA_CHECKLIST.md](docs/QA_CHECKLIST.md).

## How it works (and why it's safe)

```
prompt ──▶ LLM ──▶ strict JSON spec ──▶ Pydantic validation ──▶ trusted
                    (data, never code)                          template generator (CadQuery)
                                                                      │
                              preview mesh + STL + STEP ◀────────────┘
                                                                      │
                                          manufacturability checks ◀──┘
```

The LLM **only** emits a JSON design spec. It never produces executable code.
The spec is validated against a strict Pydantic schema (units, object type,
dimensions, holes) before any geometry is built by audited local template
functions. Every export is verified to exist and be non-empty.

## Stack

| Layer        | Choice                                                    |
| ------------ | --------------------------------------------------------- |
| Frontend     | Next.js (App Router) + TypeScript (strict) + Tailwind     |
| 3D preview   | Three.js via React Three Fiber + drei                     |
| Backend      | Python FastAPI                                            |
| CAD kernel   | CadQuery (OpenCascade)                                    |
| Persistence  | SQLAlchemy — SQLite in dev, Postgres-ready via `DATABASE_URL` |
| Storage      | Local filesystem in dev, S3-swappable `Storage` interface |
| LLM          | Provider abstraction: `mock` (offline), `anthropic`, `openai` |
| Exports      | STL + STEP                                                |

By default `LLM_PROVIDER=mock` — a deterministic, offline prompt parser — so the
whole app runs and all tests pass **with no API keys and no cost**.

## Prerequisites

- Python 3.11 (CadQuery wheels). On macOS: `brew install python@3.11`.
- Node 18+ and npm.

## Setup & run

### 1. Backend

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt

# optional: seed some example parts
.venv/bin/python -m scripts.seed

# run the API (http://127.0.0.1:8000, docs at /docs) — from the repo root:
bash scripts/dev.sh backend
```

> **Why the script (and not a bare `uvicorn --reload`)?** It restricts the
> reloader to `backend/app` and excludes `.venv`, `__pycache__`, `storage_data`
> (generated STEP/STL), `cadmaker.db`, test/eval artifacts, etc. Watching the
> whole backend directory makes WatchFiles restart the server whenever `.venv`
> changes or generated files land — killing in-flight requests, which the
> browser shows as `TypeError: Failed to fetch`.
>
> Two uvicorn gotchas the script handles (don't hand-roll this): uvicorn always
> watches the *current working directory* tree even with `--reload-dir app`,
> and a directory `--reload-exclude` only works as an **absolute path to an
> existing directory** — relative names and `".venv/*"` globs silently fail to
> match deep changes. See `scripts/dev.sh` for the exact flags.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local      # points at http://localhost:8000
npm run dev                            # http://localhost:3000
```

(Or run both at once with `bash scripts/dev.sh` from the repo root.)

Once both are up, `backend/.venv/bin/python -m scripts.smoke_local_dev` checks
the whole browser path: health, auth, plain-English generation, STEP+STL
downloads.

Open http://localhost:3000, click **New design**, and try:

> Wall-mounted bracket for a 25 mm pipe with two M6 screw holes, 5 mm thick, 80 mm wide.

Edit the parameters in the sidebar to regenerate the model instantly, review the
manufacturability panel, and download STL/STEP.

### Using a real LLM (optional)

Set in `backend/.env` (never commit real keys):

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# or
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

## Plain-English → CAD feature-graph engine (primary)

`CAD_ENGINE=feature_graph` (the default) compiles prompts into a **CadPlan** — a
strict, parametric feature graph — instead of routing the whole prompt to a fixed
template. The pipeline:

```
prompt → CadPlan (strict JSON, never code) → deterministic CadQuery compile
       → STEP + STL → validate (bbox / hole count / through-holes cut / exports)
       → one LLM repair pass → clarify only if a critical dimension is missing
```

- **Schema** `app/cad/plan/schema.py` — ~24 primitive feature `kind`s (box, plate,
  `circular_flange`, pipe, `pipe_spool`, `rectangular_wall`, boss, rib, gusset,
  `hole`, `hole_pattern_rect/circle`, slot, `v_groove`, countersink, counterbore,
  fillet, chamfer, shell, mirror, union, subtract). Machine fields are short
  enums; human text lives in a separate `description` (so a long string can never
  overflow an enum-like field).
- **Compiler** `app/cad/plan/compiler.py` — trusted dispatch on `kind` (no eval,
  no LLM code executed); composes primitives, cuts holes as real subtractive ops.
- **Validate** `app/cad/plan/validate.py` — reuses the mesh genus
  (`analyze_stl`) to *geometrically* prove through-holes were actually cut.
- **Planner** — OpenAI Responses + Structured Outputs (`plan_cad`,
  `CAD_PLAN_SCHEMA`); an offline deterministic planner (`app/cad/plan/deterministic.py`)
  covers the common families so the engine + evals run with no API key.

Env: `CAD_ENGINE=feature_graph|legacy`, `CAD_LLM_PROVIDER` (defaults to
`LLM_PROVIDER`), `CAD_LLM_MODEL=gpt-5.5` (graceful `gpt-5.1` → `gpt-4.1`
fallback). Examples that now compose primitives rather than misclassifying:
a **blind flange** → a circular flange (not a rectangular adapter plate); a
**straight pipe spool** → pipe + two end flanges (not a tee); a **U-bracket** →
base + two side walls (not an enclosure); a **bearing block** builds (no schema
crash).

Run the 10-prompt eval:

```bash
cd backend && python -m scripts.run_cad_evals --provider mock      # offline
LLM_PROVIDER=openai CAD_LLM_MODEL=gpt-5.5 python -m scripts.run_cad_evals --provider openai
# or: cd frontend && npm run test:cad
```

## Part templates (legacy fallback)

When the feature-graph engine doesn't recognize a part, generation falls back to
the legacy template pipeline: `rectangular_bracket`, `l_bracket`, `enclosure`
(box + lid), `spacer`/standoff, `pipe_clamp`, `drill_jig`, `handle`/knob,
`adapter_plate`. These also back parameter-editing and drawing-to-CAD. Browse
them at `GET /api/templates`. (Kept as a safety net + UI examples, not as the
primary generation path.)

## API

| Method | Route                          | Purpose                                   |
| ------ | ------------------------------ | ----------------------------------------- |
| POST   | `/api/designs/create`          | prompt → spec → geometry (or clarification) |
| POST   | `/api/auth/signup` · `/login` · `GET /me` | email/password auth → JWT bearer token |
| POST   | `/api/designs/{id}/regenerate` | deterministic rebuild from edited params  |
| POST   | `/api/designs/{id}/modify`     | plain-English edit → DesignModification → rebuild |
| GET    | `/api/designs/{id}/files/{fmt}`| owner-checked STL/STEP download (local stream / S3 redirect) |
| POST   | `/api/designs/{id}/feedback`   | thumbs up/down + issue categories + comment |
| GET    | `/api/designs/{id}/views/{view}` | drawing view (top/front/right/left/iso) as `?fmt=png\|svg` |
| GET    | `/api/designs/{id}/package`    | full CAD package ZIP (STEP/STL/spec/report/drawings) |
| POST   | `/api/designs/{id}/localized-edit` | edit only a selected hole/edge/face/feature |
| POST   | `/api/designs/{id}/circle-edit` | edit a feature resolved from a circle/lasso selection |
| POST   | `/api/drawings/interpret` · `/confirm` | 2D drawing image (+hint) → interpretation → (confirmed) design |
| POST   | `/api/designs/{id}/export`     | ensure + return STL/STEP URLs             |
| POST   | `/api/designs/{id}/checks`     | re-run manufacturability checks           |
| GET    | `/api/designs/{id}`            | full design (spec, preview, exports, checks) |
| GET    | `/api/designs`                 | list design summaries                     |
| GET    | `/api/templates`               | template catalog + parameter ranges       |

## Manufacturability checks

Minimum wall/thickness (method-aware), hole diameter validity, hole-to-edge
distance, **hole-to-hole spacing**, **counterbore/countersink validity**,
fillet-radius resolvability, impossible/negative dimensions (rejected at
validation), and 3D-printing risks (sub-mm features, tall slender standoffs,
counterbore bridging, enclosure print orientation). A material/method assumption
is always surfaced so the user can correct it.

## Editing parts in plain English

After generating, type an edit like *"make it wider"*, *"move the holes farther
apart"*, *"make the wall thickness 4 mm"* or *"add rounded edges"*. The LLM
returns a strict **DesignModification** (data, never code); we apply it
deterministically and re-validate. Ambiguous edits ("make it fly") return a
clarification instead of guessing.

## Accounts, privacy & storage

- **Auth**: email/password → JWT bearer token (`localStorage` on the web). Every
  design route requires a token; a design that isn't yours returns **404** (no
  existence leak). Projects, designs, exports and feedback are all user-scoped.
- **Storage**: `STORAGE_BACKEND=local` (dev) or `s3` (prod, S3/MinIO/R2). CAD
  files are **never** served from a public path — downloads go through an
  owner-checked API route that streams from local disk or redirects to a
  short-lived S3 presigned URL.
- **LLM providers**: `LLM_PROVIDER=mock|anthropic|openai`. The OpenAI provider
  uses the **Responses API with Structured Outputs** (JSON schema) and supports
  `parse_prompt_to_design_spec`, `parse_modification`,
  `generate_clarification_question`, `generate_explanation`, and retry-once
  `repair`. `openai`/`boto3` are optional runtime deps (lazily imported); the
  app and the full test suite run on the offline `mock` provider with no keys.

## Evaluation harness

```bash
cd backend
.venv/bin/python -m scripts.run_eval --provider mock --limit 200
.venv/bin/python -m scripts.run_eval --provider openai --limit 50   # needs OPENAI_API_KEY
```

Runs 200+ prompts (`tests/data/eval_prompts.json`) through the production safety
pipeline and writes JSON + CSV reports to `backend/eval_reports/`, scoring
`valid_json`, `correct_template`, `model_or_clarification`, `export_success`,
`dangerous_prompt_blocked_or_clarified`, `latency_ms`, and `estimated_cost`.
Regenerate the dataset with `python -m scripts.build_eval_dataset`.

## Observability

Structured JSON events (`app/observability.py`) for prompt parsing, geometry
generation latency, provider used, and validation / CAD / export failures, plus
an `X-Response-Time-ms` header. Secrets (API keys, passwords, tokens) are scrubbed
from every log payload and never sent to the frontend.

## Prompt benchmark

`backend/tests/data/benchmark_prompts.json` holds 60 categorized prompts (clear,
vague, ambiguous, invalid, manufacturing, modification, and per-template). The
suite asserts every prompt yields a valid model **or** a useful clarification
(never a crash) — currently **100%**, against an 80% target — plus correct
template routing and that exported STEP files re-import cleanly through
OpenCascade (FreeCAD's kernel).

## Tests & verification

```bash
# backend unit + integration tests
cd backend && .venv/bin/python -m pytest

# one-shot: backend tests + STL/STEP generation + frontend build
bash scripts/verify.sh
```

The suite covers prompt parsing, spec validation, every CAD template (each must
export non-empty STL **and** STEP), deterministic regeneration (same spec → same
file; changed params → different valid file), manufacturability warnings, and
the full HTTP flow.

## Project layout

```
backend/
  app/
    schemas/design_spec.py   # strict LLM↔CAD contract (Pydantic)
    llm/                     # provider abstraction: mock | anthropic | openai
    parsing/                 # prompt → validated ParseResult
    cad/                     # base + registry + templates/ (trusted generators)
    manufacturability/       # checks
    export/                  # build geometry, STL/STEP bytes, preview mesh
    storage/                 # local FS now, S3-swappable
    services/                # orchestration (prompt→spec→geometry→persist)
    routers/                 # FastAPI routes
  tests/                     # pytest suite
  scripts/seed.py            # example designs
frontend/
  src/app/                   # landing, dashboard, new, studio/[id]
  src/components/            # Viewer3D, ParameterSidebar, ChecksPanel
  src/lib/                   # api client + types
scripts/verify.sh           # end-to-end verification
```

## Production notes / remaining risks

- **Auth** is modeled (`User`) but not wired into routes yet — designs are
  currently unauthenticated. Add login + per-user scoping before deploying.
- **Storage**: implement an `S3Storage(Storage)` and return signed URLs; the
  interface is already in place.
- **Jobs**: generation is synchronous (fast for these templates). Move to a task
  queue only if heavier parts/assemblies are added.
- CadQuery requires Python 3.11; 3.13 wheels may lag.
```
