# CAD_Maker / SourceCAD — Project Status Report

_Audit date: 2026-06-19. Read-only audit; no source files were modified._

> Product/marketing name in code & docs is **SourceCAD AI Part Studio**. The repo
> folder is `CAD_Maker`. They are the same product.

---

## 1. Product overview

**What it does.** SourceCAD turns plain-English prompts into *editable,
manufacturable, exportable* parametric mechanical CAD — brackets, plates,
enclosures, flanges, pipes/spools, mounts, adapters, spacers, clamps, jigs,
gears/pulleys, even an inline-4 crankshaft. It is explicitly **not** decorative
text-to-3D mesh generation; it produces real B-rep geometry exported as **STEP**
and **STL**.

**Core safety contract (enforced, see `CLAUDE.md`).** The LLM only ever emits
**structured JSON** (a `CadPlan` feature graph or a `DesignSpec`), never
executable code. JSON is re-validated by Pydantic, then a **trusted, deterministic
CadQuery compiler** builds geometry. Exports are verified to exist and be
non-empty; through-holes are proved cut via mesh-genus analysis.

**Main user flows.**
1. **Sign up / sign in** (email + password → JWT).
2. **New design**: type a prompt → backend plans → 3D preview + manufacturability
   checks + assumptions + STL/STEP downloads, or a clarification question.
3. **Studio** (`/studio/[id]`): edit parameters (sidebar, deterministic rebuild),
   plain-English edits ("make it wider"), circle/point-and-prompt localized edits,
   2D drawing views (top/front/right/left/iso as PNG/SVG), CAD-package ZIP
   download, thumbs up/down feedback.
4. **Drawing→CAD** (`/drawing`): upload a 2D drawing image; OpenAI vision extracts
   a validated interpretation; one-shot generate (assumption-first) or confirm.
5. **Dashboard**: list of the user's designs.

**Architecture.** Monorepo with two apps:
- `frontend/` — Next.js 14 (App Router) + TypeScript (strict) + Tailwind; 3D via
  React-Three-Fiber / drei / three. Talks to the backend over REST with a JWT
  bearer token stored in `localStorage`.
- `backend/` — Python 3.11 FastAPI; CadQuery 2.7 (OpenCascade) CAD kernel;
  SQLAlchemy ORM; layered safety pipeline.

**Frameworks.** Frontend: **Next.js 14.2.18 App Router**. Backend: **FastAPI
0.115** on **uvicorn**.

**Database & auth.** SQLAlchemy 2.0; **SQLite** in dev (`backend/cadmaker.db`),
**Postgres-ready** via `DATABASE_URL`. Tables auto-created at startup via
`Base.metadata.create_all` — **no migration tool (no Alembic)**. Auth is
email/password → **JWT** (`python-jose` HS256, `bcrypt` hashing). Auth is **fully
wired**: every `/api/designs/*` and `/api/drawings/*` route requires
`get_current_user`; non-owned designs return **404** (no existence leak).

**External services / APIs.**
- **OpenAI** (Responses API + Structured Outputs; also vision for Drawing→CAD).
- **Anthropic** (text-only provider, optional).
- **Mock** provider (offline, deterministic) — default; runs whole app + tests
  with no keys.
- **S3-compatible storage** (AWS S3 / MinIO / R2) via boto3, optional (`s3` backend).

---

## 2. What has been achieved so far

This is a **mature, working prototype** — far past scaffolding. All backend tests
pass and the frontend typechecks cleanly.

**Working features**
- Plain-English → CAD via a **feature-graph (`CadPlan`) compiler** (primary engine)
  with validation, one LLM repair pass, and a feature-level audit.
- Legacy template pipeline + a sandboxed CadQuery-program path as fallbacks.
- Parametric editing (sidebar), plain-English modify, localized/circle edits.
- 2D drawing views (PNG + SVG) rendered from the real model.
- Drawing→CAD (image → interpretation → geometry), assumption-first, with a hard
  accuracy gate that refuses to ship a wrong model.
- Manufacturability checks (wall/thickness, hole sizing, spacing, edge distance,
  counterbore/countersink, print-risk).
- STL + STEP export; one-click **CAD package ZIP** (STEP/STL/spec/report/drawings).
- Email/password auth with strict per-user isolation; feedback (thumbs + categories).
- Observability: structured JSON events + `X-Response-Time-ms` header; secrets scrubbed.
- Eval/benchmark harness (200+ prompts, semantic + regression benchmarks, CSV/JSON reports).

**Completed pages/routes** — see §4.

**Backend endpoints** — see §5.

**CAD pipeline status.** Production-quality and the centerpiece. Primary route is
the `CadPlan` feature graph; deterministic offline planner covers common families
so it works with no API key. STEP only offered for real B-rep paths (never faked).

**File/export.** STL + STEP via owner-checked download route (local stream or S3
presigned redirect); package ZIP; drawing views.

**Auth/session.** JWT bearer in `localStorage`; `useRequireAuth` redirects to
`/signin`; one-week token expiry.

**Deployment/proxy.** **None present.** No Dockerfile, docker-compose, nginx/Caddy
config, systemd unit, Procfile, or any cloud config. Only local dev scripts exist
(`scripts/dev.sh`, `scripts/verify.sh`). See §7.

**Scripts/checks/tests.**
- `scripts/dev.sh` (reload-safe dev servers), `scripts/verify.sh` (end-to-end).
- 36 pytest files (~425 tests, all passing).
- `frontend/scripts/check-routes.mjs` (post-build route assertion).
- `backend/scripts/`: eval runners, benchmark builders, OpenAI smoke tests, seed,
  thumbnail render.

---

## 3. Current folder structure

```
CAD_Maker/
├─ CLAUDE.md               # CAD generation rules (the safety contract)
├─ README.md               # detailed product/dev docs (somewhat stale — see §8)
├─ docs/CHANGELOG.md       # very detailed; latest = v0.7.4-ACCURATE
├─ docs/QA_CHECKLIST.md
├─ scripts/dev.sh          # local dev (backend reload-safe / frontend / both)
├─ scripts/verify.sh       # backend tests + STL/STEP gen + frontend build
├─ backend/
│  ├─ app/
│  │  ├─ main.py           # FastAPI app, CORS, startup, /health, /api/provider-status
│  │  ├─ config.py         # env-driven Settings (tiny .env loader, no pydantic-settings)
│  │  ├─ database.py       # SQLAlchemy engine/session, init_db (create_all)
│  │  ├─ models.py         # User, Project, Design, ExportFile, ManufacturingCheck, Feedback
│  │  ├─ routers/          # auth, designs, drawings, templates
│  │  ├─ auth/             # security (JWT/bcrypt) + deps (get_current_user)
│  │  ├─ schemas/          # Pydantic: design_spec, drawing_spec, api, coerce, complex_cad…
│  │  ├─ llm/              # provider abstraction: mock | anthropic | openai + JSON schemas
│  │  ├─ parsing/          # prompt/modification parsers, complex_plan, policy
│  │  ├─ cad/
│  │  │  ├─ plan/          # PRIMARY engine: schema, planner, compiler, validate, audit,
│  │  │  │                 #   normalize, deterministic, policy, defaults
│  │  │  ├─ templates/     # legacy template builders (bracket, enclosure, crankshaft…)
│  │  │  ├─ registry.py, features.py, feature_graph.py, fallback_graphs.py, topology.py
│  │  ├─ generation/       # SECOND engine: router, scad_generate, scad_runner,
│  │  │                    #   code_sandbox, cad_programs, semantic_verifier, mesh_analysis…
│  │  ├─ drawing/          # interpret, render (views), scale, fallback, hint_classifier
│  │  ├─ editing/          # localized edits
│  │  ├─ export/exporter.py# build geometry, STL/STEP bytes, preview mesh
│  │  ├─ storage/storage.py# LocalStorage + S3Storage (boto3)
│  │  ├─ services/         # design_service (orchestration), package_service
│  │  ├─ manufacturability/checks.py
│  │  └─ observability.py, explain.py
│  ├─ tests/               # 36 test files (+ data/ fixtures)
│  ├─ scripts/             # eval/benchmark/smoke/seed
│  ├─ requirements.txt, pytest.ini, .env (HAS A REAL KEY — see §8), .env.example
│  ├─ cadmaker.db          # dev SQLite (gitignored, on disk)
│  ├─ storage_data/        # generated STEP/STL (gitignored, on disk; ~many dirs)
│  └─ eval_reports/, reports/  # eval output artifacts
└─ frontend/
   ├─ src/app/             # App Router pages (see §4)
   ├─ src/components/      # Viewer3D, Studio3D, ParameterSidebar, ChecksPanel, …
   ├─ src/lib/             # api.ts (client), auth.tsx, types.ts, examples.ts
   ├─ scripts/check-routes.mjs
   ├─ package.json, next.config.mjs, tailwind.config.ts, tsconfig.json
   └─ .env.local (NEXT_PUBLIC_API_BASE), .env.local.example
```

**Duplicate / cruft / confusing items (concrete):**
- `backend/app/cad/feature_graph 2.py` — stray "copy 2" duplicate of `feature_graph.py`. **Delete.**
- `frontend/src/app/drawing/page 2.tsx` — stray "copy 2" of `drawing/page.tsx`. **Delete** (harmless but confusing; the space means it isn't a route).
- **Two parallel generation subsystems** coexist: `app/cad/plan/*` (the primary
  `CadPlan` engine) and `app/generation/*` (SCAD DSL sandbox + sandboxed CadQuery
  programs). Both are reachable from `design_service.create_design`. This is the
  single most confusing part of the codebase for a newcomer.
- `backend/eval_reports/` and `backend/reports/` hold many generated artifacts
  checked out on disk; `storage_data/` has dozens of generated STEP/STL dirs.
  All gitignored but clutter the working tree.
- `.DS_Store` files scattered (gitignored).
- Three "New Design" routes exist by design (`/designs/new` canonical, `/new`
  alias, `/new-design` redirect) — intentional, not a bug, but worth knowing.

---

## 4. Frontend status

**App Router pages (`frontend/src/app/`):**

| Route | File | Role |
| --- | --- | --- |
| `/` | `page.tsx` | Landing (marketing + examples) |
| `/signin` | `signin/page.tsx` | Renders `<AuthForm mode="signin">` |
| `/signup` | `signup/page.tsx` | Renders `<AuthForm mode="signup">` |
| `/dashboard` | `dashboard/page.tsx` | Auth-gated list of user's designs |
| `/designs/new` | `designs/new/page.tsx` | **Canonical** New Design (re-exports `components/NewDesign`) |
| `/new` | `new/page.tsx` | **Alias** — re-exports same `NewDesign` component (keeps `/new?prompt=` deep links) |
| `/new-design` | `new-design/page.tsx` | **Redirect** → `/designs/new` |
| `/studio/[id]` | `studio/[id]/page.tsx` | Main editor: 3D viewer, params, edits, views, downloads, feedback (466 lines) |
| `/drawing` | `drawing/page.tsx` | Drawing→CAD upload/interpret/generate |
| `/docs/import` | `docs/import/page.tsx` | Static import/compatibility docs |

- **Canonical vs alias:** `/designs/new` is canonical; `/new` is a component alias;
  `/new-design` is a server redirect. `check-routes.mjs` asserts all three exist
  post-build.

**Major components (`src/components/`):** `Viewer3D` / `Studio3D` (three.js
viewer + view toolbar + circle projection), `ParameterSidebar`, `ChecksPanel`,
`HoleTable`, `ModifyBox` (plain-English edits), `CircleEditPanel`,
`FeedbackWidget`, `MockModeBanner`, `AuthForm`, `Header`, `NewDesign`.

**API client:** `src/lib/api.ts` — typed wrapper, JWT injection, friendly
network-error messages, `ApiError`. `src/lib/auth.tsx` — `AuthProvider`,
`useAuth`, `useRequireAuth`.

**UI bugs / inconsistencies found:**
- Header nav links (`/dashboard`, `/drawing`, `/docs/import`, `/designs/new`) all
  resolve — no broken links found.
- `frontend/.env.local` defines both `NEXT_PUBLIC_API_BASE` and
  `NEXT_PUBLIC_API_BASE_URL`, but only `NEXT_PUBLIC_API_BASE` is read by code.
  The `_URL` variant is dead/confusing.
- `api.regenerate(...)` only forwards `dimensions` + `holes`; the backend
  `RegenerateRequest` also accepts `fillet_radius`, `manufacturing_method`,
  `material`, which the client never sends (works, but a partial surface).
- `page 2.tsx` (with the space) under `drawing/` is not a route but looks like one.
- TypeScript strict build passes (`tsc --noEmit` clean).

---

## 5. Backend status

**Routers / endpoints**

Auth (`routers/auth.py`, prefix `/api/auth`):
- `POST /signup`, `POST /login` → `{access_token, user}`; `GET /me`.

Designs (`routers/designs.py`, prefix `/api/designs`, all owner-scoped):
- `POST /create`, `POST /{id}/regenerate`, `POST /{id}/modify`,
  `POST /{id}/export`, `POST /{id}/checks`, `POST /{id}/feedback`,
  `GET /{id}/feedback`, `GET /{id}`, `GET ""` (list),
  `GET /{id}/files/{fmt}` (owner-checked download),
  `GET /{id}/views/{view}` (?fmt=png|svg), `GET /{id}/package` (ZIP),
  `POST /{id}/localized-edit`, `POST /{id}/circle-edit`,
  `POST /{id}/generate-with-defaults`.

Drawings (`routers/drawings.py`, prefix `/api/drawings`):
- `POST /interpret`, `POST /confirm`, `POST /generate` (one-shot).

Templates (`routers/templates.py`): `GET /api/templates`.

App-level (`main.py`): `GET /health` (dev-only provider/storage status),
`GET /api/provider-status` (frontend capability gating; **never returns secrets**).

**App startup flow (`main.py`).**
1. `settings.validate_startup()` — fails fast if `APP_ENV` is staging/production
   while `LLM_PROVIDER=mock`, or openai without `OPENAI_API_KEY`.
2. Build `FastAPI`, add CORS (explicit origins + a dev regex allowing any
   localhost/127.0.0.1 port).
3. Include the four routers.
4. `@app.on_event("startup")` → `init_db()` (`create_all`) + ensure local storage dir.
5. Timing middleware logs `/api/*` calls and adds `X-Response-Time-ms`.

> Note: uses the **deprecated** `@app.on_event("startup")` (lifespan handlers are
> the modern replacement) — works on FastAPI 0.115 but emits a deprecation warning.

**Database models & migrations.** Models: `User`, `Project`, `Design`,
`ExportFile`, `ManufacturingCheck`, `Feedback` (`models.py`). SQLite tables
confirmed present in `cadmaker.db`. **No Alembic / migrations** — schema is created
by `create_all`, which **adds new tables but never alters existing ones**. The
`Design` table is wide (it has accreted many columns across versions:
`program_code`, `semantic_json`, `repair_attempts`, `route`, `route_reason`,
`clarified_spec_candidate`, …). On Postgres or any pre-existing SQLite DB, new
columns added later will **not** appear without a manual migration — a real risk
(see §8).

**Startup errors / env assumptions.** None at import time; mock provider needs no
keys. In staging/production, startup intentionally raises without a real provider
key. `PUBLIC_BASE_URL` is baked into stored download URLs at generation time, so
changing it later doesn't rewrite old rows.

**Known DB/Postgres issues.** (1) No migrations — see above. (2) JSON columns use
SQLAlchemy generic `JSON`; on Postgres they become `json`/`jsonb`-ish but there's
no indexing/strategy. (3) `Project.user_id` is nullable (legacy), but creation
always sets it. No connection-pool tuning for Postgres.

---

## 6. CAD generation status

**How plain English becomes CAD (primary path).** `design_service.create_design`:
1. **`_try_cad_plan`** (when `CAD_ENGINE=feature_graph`, the default) —
   `plan_from_prompt` asks the provider for a strict `CadPlan` JSON (or uses the
   offline deterministic planner). Policy decides if a *fatal* clarification is
   needed. `normalize_cad_plan` fills secondary dims. `build_and_validate`
   compiles deterministically (`compiler.compile_cad_plan`), exports STL+STEP,
   and `validate()` checks bbox/hole-count/through-holes (mesh genus). One repair
   pass on fatal failure; a feature-level **audit** (`audit_plan`) can trigger one
   more repair. Persisted via `_store_plan`.
2. If the feature graph can't build it and the prompt isn't "complex":
   **`_try_compiler`** → `generation/cad_programs.py` + `generation/compiler.py`
   (a **sandboxed CadQuery program** path, semantically verified).
3. Else **`_plan_long_prompt`** (ComplexCADPlan) or **`plan_prompt`** (legacy
   template-first) → `DesignSpec` → `_regenerate_geometry` (template builders).
4. If nothing builds → a clarification question (no geometry) or a
   "generate with defaults" candidate.

**Key files/classes/functions.**
- Schema: `cad/plan/schema.py` (`CadPlan`, `FeatureKind` ~24 primitives).
- Planner/orchestration: `cad/plan/planner.py` (`plan_from_prompt`,
  `build_and_validate`, `repair_plan`).
- Compiler (trusted dispatch on `kind`): `cad/plan/compiler.py`.
- Validation: `cad/plan/validate.py` (+ `generation/mesh_analysis.py` genus check).
- Audit: `cad/plan/audit.py`. Normalize/defaults/policy/deterministic alongside.
- Legacy templates: `cad/templates/*` + `cad/registry.py`.
- Sandbox program engine: `generation/{router,cad_programs,compiler,code_sandbox,
  scad_generate,scad_runner,semantic_verifier}.py`.
- Geometry/export: `export/exporter.py`. Drawing views: `drawing/render.py`.

**Mechanisms used.**
- **CadQuery (OpenCascade)** is the real kernel for the primary + template paths
  (B-rep, STEP + STL).
- **Restricted OpenSCAD DSL** exists as a sandboxed STL-only fallback
  (`generation/scad_generate.py` / `scad_runner.py`) — statically linted, no
  include/import/file/shell, timeout, temp-dir, **STL only** (never fakes STEP).
- **AI-generated code** is allowed *only* through the sandboxed CadQuery-program
  path with static linting + semantic verification; it is **not** the primary
  path and is heavily guarded. The dominant path is **JSON feature graph → trusted
  compiler** (no code execution), consistent with `CLAUDE.md`.

**Working vs stubbed.**
- ✅ Feature-graph engine, compiler, validation, audit, repair — working, tested.
- ✅ Template builders, drawing views, package ZIP, manufacturability checks — working.
- ✅ Offline deterministic planner (mock) — working (enables no-key operation/tests).
- ⚠️ Two engines overlap (`cad/plan` vs `generation/*`) — both real, but the
  division of responsibility is not documented and is a maintenance risk.
- ⚠️ OpenSCAD fallback requires the `openscad` binary at runtime for STL render
  (not in `requirements.txt`; not validated to be installed) — see §8.

---

## 7. Deployment / VPS status

**There is no deployment configuration in the repo.** No Dockerfile,
docker-compose, nginx/Caddy config, systemd unit, Procfile, fly.toml, vercel.json,
or render.yaml were found. README §"Production notes" lists production as future
work. So a VPS deploy must be assembled from scratch.

**Local dev commands (the only documented run path).**
- Backend: `bash scripts/dev.sh backend` → uvicorn on `127.0.0.1:8000` (reload
  restricted to `backend/app`; many excludes to avoid reload loops — see the long
  comment in `scripts/dev.sh`). Docs at `/docs`.
- Frontend: `cd frontend && npm run dev` → `http://localhost:3000`.
- Both: `bash scripts/dev.sh`.
- Verify: `bash scripts/verify.sh` (backend tests + STL/STEP gen + frontend build).

**Likely production commands (to be created).**
- Backend: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (behind gunicorn or
  multiple uvicorn workers), `APP_ENV=production`, real `LLM_PROVIDER`/key,
  `JWT_SECRET`, `DATABASE_URL` (Postgres), `STORAGE_BACKEND=s3` (+ S3 creds),
  `PUBLIC_BASE_URL=https://api.yourdomain`, `CORS_ORIGINS=https://yourdomain`,
  `DEV_MODE=false`.
- Frontend: `npm run build && npm run start` (port 3000) with
  `NEXT_PUBLIC_API_BASE=https://api.yourdomain`.

**Domains / ports / proxy assumptions.** None encoded. Dev assumes backend `:8000`,
frontend `:3000/3001`. A reverse proxy (nginx/Caddy) terminating TLS and routing
`/api`→backend, `/`→frontend would need to be written. CORS currently allows any
localhost port in dev; in prod it relies on `CORS_ORIGINS`.

**What's needed to run on a VPS.** Python 3.11 (CadQuery wheels), Node 18+,
OpenCascade comes with the CadQuery wheel (no system dep), optionally an
`openscad` binary if the SCAD fallback is exercised, Postgres, an S3 bucket (or
keep local storage on a persistent volume), a process manager + reverse proxy.

**Local↔prod mismatches.** SQLite→Postgres (no migrations), local FS→S3,
`PUBLIC_BASE_URL`/`CORS_ORIGINS` hardcoded to localhost in `.env`, `DEV_MODE=true`,
`JWT_SECRET=dev-insecure-secret-change-me`, `OPENAI_MODEL=gpt-5.5` (see §8).

---

## 8. Known problems and risks

**Critical / security**
- 🔴 **A real-looking `OPENAI_API_KEY` is present in `backend/.env`** on disk.
  It is gitignored (so not in git), **but the repo has no commits at all** — if
  this directory is ever zipped/shared/committed wholesale, the key leaks.
  **Rotate the key and treat it as exposed.** Never commit `.env`.
- 🔴 **No git history** — `git log` shows zero commits; everything is untracked.
  There is no version control safety net. First action should be an initial commit
  (after confirming `.gitignore` covers `.env`, `cadmaker.db`, `storage_data/`).
- 🔴 **`JWT_SECRET` defaults to `dev-insecure-secret-change-me`** and `.env`
  doesn't override it. Any deploy without setting it allows token forgery.
- 🟠 **`OPENAI_MODEL=gpt-5.5`** in `.env`. The CAD planner has a fallback chain
  (`gpt-5.5 → gpt-5.1 → gpt-4.1 → default`) so planning survives, but
  `parse_prompt_to_design_spec`, `interpret_drawing`, modify, clarify, and explain
  call `self._model` (`gpt-5.5`) **directly with no fallback** — against the real
  OpenAI API these likely error if `gpt-5.5` isn't a valid model id. Set a valid
  model (e.g. `gpt-4o` / `gpt-4.1`) or extend the fallback to non-planner calls.

**Database**
- 🟠 **No migrations (no Alembic).** `create_all` adds tables but never alters
  them. Moving to Postgres, or reusing an older `cadmaker.db`, will be missing
  columns the wide `Design` model expects. Adopt Alembic before prod.

**Deployment / config**
- 🟠 No Docker/nginx/systemd/compose anywhere — production is greenfield.
- 🟠 `.env` hardcodes localhost `PUBLIC_BASE_URL`/`CORS_ORIGINS` and `DEV_MODE=true`.
- 🟡 `requirements.txt` has no `psycopg`/`asyncpg` driver, so `DATABASE_URL=postgresql+psycopg://…`
  will fail until a driver is added.
- 🟡 SCAD fallback needs an `openscad` binary not listed as a dependency.

**Code health / correctness**
- 🟡 Stray duplicate files: `backend/app/cad/feature_graph 2.py`,
  `frontend/src/app/drawing/page 2.tsx`.
- 🟡 **README is stale**: "Production notes" claims *auth is not wired into routes*
  and *S3 is not implemented* — both are now done (`S3Storage` exists, all routes
  require auth). README version banner stops at v0.3.8/v0.4 while CHANGELOG is at
  **v0.7.4-ACCURATE**. `main.py` FastAPI `version="0.3.7"` is also stale.
- 🟡 Deprecated `@app.on_event("startup")` (use lifespan).
- 🟡 Dead env var `NEXT_PUBLIC_API_BASE_URL` in `frontend/.env.local`.
- 🟡 Two overlapping generation engines (`cad/plan` vs `generation/*`) with no
  documented boundary.

**CORS** — fine in dev (explicit origins + dev regex). In prod it depends entirely
on `CORS_ORIGINS`; `allow_credentials=True` with a wildcard would be invalid, so
ensure `CORS_ORIGINS` is set to exact origins (the code already splits on commas).

**Build / tests** — ✅ backend pytest: all ~425 tests pass (mock provider).
✅ frontend `tsc --noEmit`: clean. No failing tests observed.

**TODO/FIXME** — no literal TODO/FIXME markers in source; `NotImplementedError`s
are intentional abstract-interface stubs (base `Storage`, base `LLMProvider`),
not incomplete features.

---

## 9. What is still left to do

**Critical blockers (before any deploy)**
- Rotate the OpenAI key; ensure `.env` is never committed; make the initial git commit.
- Set a strong `JWT_SECRET` via env in every non-dev environment.
- Fix `OPENAI_MODEL` to a valid id (and/or extend the model fallback to all OpenAI calls).
- Introduce DB migrations (Alembic) before switching off the throwaway SQLite file.

**High-priority product work**
- Update README + `main.py` version string to match reality (auth wired, S3 done, v0.7.x).
- Document (and ideally consolidate) the two generation engines.
- Decide Postgres vs SQLite for prod and wire the driver + migrations.

**Backend work**
- Add `psycopg[binary]` (or asyncpg) to requirements; test Postgres path.
- Replace `on_event` with lifespan handlers.
- Add a `/api` health/readiness endpoint suitable for a load balancer.
- Confirm/guard the OpenSCAD-binary dependency (feature-detect + clear error).

**Frontend work**
- Remove `page 2.tsx`; remove dead `NEXT_PUBLIC_API_BASE_URL`.
- Surface `fillet_radius`/`material`/`manufacturing_method` in `api.regenerate`.
- Add empty/error states polish on dashboard/studio (mostly present already).

**CAD generation work**
- Document which prompts route to which engine; add routing observability to the UI.
- Expand regression coverage for the CHANGELOG's named regression prompts in `CLAUDE.md`.

**Deployment work**
- Write Dockerfiles (backend Py3.11 + CadQuery; frontend Node), docker-compose,
  nginx/Caddy reverse proxy + TLS, systemd or container orchestration, and a
  `.env.production` template. Persist `storage_data` (or move to S3).

**Testing / QA**
- Add a Postgres CI run; add a frontend test/e2e (Playwright) — none exist today.
- Run `bash scripts/verify.sh` in CI; wire `check-routes.mjs` into the build.

**Nice-to-have**
- Async job queue if heavier assemblies are added (currently synchronous).
- Rate limiting / abuse protection on auth + generation endpoints.
- Thumbnails pipeline (`scripts/render_thumbnails.py`) wired into the dashboard.

---

## 10. Recommended next steps (top 10, in order)

1. **Rotate the OpenAI API key and lock down secrets.** It sits in
   `backend/.env`. Treat as compromised. Verify: `grep -r OPENAI_API_KEY backend/.env`
   shows the key, then confirm `.gitignore` lists `backend/.env` (it does).
   *Why:* leaked keys are the highest-severity, lowest-effort risk.

2. **Make the first git commit** with a verified `.gitignore`.
   Files: `.gitignore`, whole tree. Verify:
   `git status --porcelain | grep -E '\.env$|cadmaker\.db|storage_data'` returns
   nothing before `git add -A`. *Why:* no history exists today; nothing is recoverable.

3. **Fix `OPENAI_MODEL` / model fallback.** Set `OPENAI_MODEL=gpt-4.1` (or a valid
   id) in `.env`, or extend the fallback chain in
   `backend/app/llm/openai_provider.py` (currently only `plan_cad` falls back).
   Verify: `cd backend && LLM_PROVIDER=openai .venv/bin/python -m scripts.smoke_openai_prompt`.
   *Why:* real-provider text/drawing calls likely error on `gpt-5.5`.

4. **Set a real `JWT_SECRET` per environment** (`backend/app/config.py:84`,
   read from env). Verify: a token signed with the old default no longer validates.
   *Why:* default secret = forgeable auth.

5. **Add Alembic migrations** and a baseline matching `backend/app/models.py`.
   Files: new `backend/alembic/`, `requirements.txt`. Verify:
   `alembic upgrade head` on a fresh Postgres yields all 6 tables + every `Design`
   column. *Why:* `create_all` can't evolve schemas; Postgres move will break otherwise.

6. **Add the Postgres driver and test the prod DB path.** Add `psycopg[binary]`
   to `requirements.txt`; run with `DATABASE_URL=postgresql+psycopg://…`. Verify:
   `cd backend && DATABASE_URL=… .venv/bin/python -c "from app.database import init_db; init_db()"`.
   *Why:* current deps can't talk to Postgres at all.

7. **Write deployment config** (Dockerfile x2, docker-compose, nginx/Caddy + TLS,
   `.env.production` template). Verify: `docker compose up` serves frontend and
   `/api/*` through the proxy; `curl https://host/health` → `{"status":"ok"}`.
   *Why:* there is currently no path to production.

8. **Reconcile docs with reality.** Update `README.md` "Production notes" (auth is
   wired; `S3Storage` exists) and the `main.py` `version=` string; point to
   `docs/CHANGELOG.md` (v0.7.4). *Why:* the README actively misleads a new dev.

9. **Delete cruft.** Remove `backend/app/cad/feature_graph 2.py` and
   `frontend/src/app/drawing/page 2.tsx`; drop `NEXT_PUBLIC_API_BASE_URL`.
   Verify: `cd backend && .venv/bin/python -m pytest -q` and
   `cd frontend && npx tsc --noEmit` still clean. *Why:* removes ambiguity for the next AI/dev.

10. **Set up CI** running `bash scripts/verify.sh` (backend tests + STL/STEP +
    frontend build) plus `frontend/scripts/check-routes.mjs`, and add a first
    Playwright smoke (signup → new design → preview → download). *Why:* locks in
    the currently-green state and protects the two-engine pipeline from regressions.

---

## 11. Questions for the project owner

**Assumptions I made**
- "CAD_Maker" and "SourceCAD AI Part Studio" are the same product (folder vs brand name).
- The OpenAI key in `.env` is a live key (I did not test it, to avoid spend/exposure).
- The primary engine is the `CadPlan` feature graph (per `CLAUDE.md` + `CAD_ENGINE`
  default), with `generation/*` as a secondary/fallback engine.
- Target deployment is a single VPS (you referenced "VPS"); not serverless.

**Decisions needing clarification**
1. **Production DB:** stay on SQLite (single box) or move to Postgres? This drives
   the migrations + driver work.
2. **Storage in prod:** S3/R2/MinIO, or local disk on a persistent volume?
3. **LLM in prod:** OpenAI (which exact model?), Anthropic, or both? `gpt-5.5` is
   not a known valid id — what should it be?
4. **The two generation engines** — keep both, or consolidate on `cad/plan`?
   (Affects how much `generation/*` to maintain/test.)
5. **OpenSCAD fallback** — is it required in prod (needs the `openscad` binary) or
   can it be disabled?
6. **Domain(s) + TLS + reverse proxy** preference (nginx vs Caddy) and whether
   frontend and backend share a domain (`/api` path) or use subdomains.
7. **Is there any out-of-repo deployment** already (server, DNS, CI) I should align
   with, or is this fully greenfield?

---

## Handoff summary (paste into ChatGPT)

```
PROJECT: SourceCAD AI Part Studio (repo folder "CAD_Maker"). Plain-English →
parametric mechanical CAD (brackets, plates, flanges, pipes, enclosures, gears,
crankshaft). Exports STEP + STL. NOT decorative text-to-3D.

SAFETY MODEL (enforced, see CLAUDE.md): the LLM emits only structured JSON
(a CadPlan feature graph or DesignSpec), never code. JSON is Pydantic-validated,
then a trusted deterministic CadQuery (OpenCascade) compiler builds geometry.
Exports verified non-empty; through-holes proven via mesh-genus. AI-generated
code only runs through a heavily sandboxed, lint+verify CadQuery-program fallback.

STACK: Frontend = Next.js 14 App Router + TypeScript(strict) + Tailwind + R3F/
three. Backend = Python 3.11 FastAPI + CadQuery 2.7 + SQLAlchemy 2. DB = SQLite
dev (cadmaker.db), Postgres-ready via DATABASE_URL but NO migrations (Alembic
absent; create_all only). Auth = email/password → JWT (python-jose HS256, bcrypt),
FULLY wired; non-owned designs return 404. Storage = LocalStorage or S3Storage
(boto3). LLM providers = mock(offline default) | openai(Responses API+Structured
Outputs+vision) | anthropic(text).

FRONTEND ROUTES: / (landing), /signin, /signup, /dashboard, /designs/new
(canonical), /new (alias→same component), /new-design (redirect), /studio/[id]
(main editor), /drawing (Drawing→CAD), /docs/import.

BACKEND ENDPOINTS: /api/auth/{signup,login,me}; /api/designs/{create, list, {id},
{id}/regenerate, modify, export, checks, feedback, files/{fmt}, views/{view},
package, localized-edit, circle-edit, generate-with-defaults}; /api/drawings/
{interpret, confirm, generate}; /api/templates; /health; /api/provider-status.

CAD PIPELINE (design_service.create_design): 1) PRIMARY cad/plan feature-graph
(planner→compiler→validate→audit→1 repair); 2) sandboxed CadQuery-program
(generation/*); 3) legacy templates / complex_plan; 4) clarification. Offline
deterministic planner lets it run with no API key. NOTE: two generation engines
(app/cad/plan vs app/generation) coexist with no documented boundary.

STATE: Mature working prototype. Backend ~425 pytest tests ALL PASS (mock);
frontend tsc --noEmit clean. Rich feature set: param edits, plain-English edits,
circle/localized edits, 2D drawing views (PNG/SVG), CAD package ZIP, drawing→CAD,
manufacturability checks, feedback, observability, eval/benchmark harness.
Latest version per docs/CHANGELOG.md = v0.7.4-ACCURATE.

TOP RISKS: (1) real OPENAI_API_KEY sits in backend/.env (gitignored) AND repo has
ZERO git commits — rotate key + make first commit. (2) JWT_SECRET defaults to
"dev-insecure-secret-change-me". (3) OPENAI_MODEL=gpt-5.5 (not a valid id);
only the CAD planner has a model fallback chain, other OpenAI calls (parse/
drawing/modify/explain) use it directly and will likely error on the real API.
(4) NO migrations → Postgres move breaks (wide Design table). (5) NO deployment
config at all (no Docker/nginx/compose/systemd). (6) README stale (claims auth
unwired + S3 missing — both actually done; version banner behind). (7) cruft:
"feature_graph 2.py", "drawing/page 2.tsx", dead NEXT_PUBLIC_API_BASE_URL.
(8) requirements.txt has no Postgres driver; SCAD fallback needs openscad binary.

NEXT STEPS (ordered): rotate key+lock secrets → first git commit → fix OPENAI_MODEL/
fallback → set real JWT_SECRET → add Alembic → add psycopg+test Postgres → write
Docker/nginx deploy → fix README/version → delete cruft → set up CI (verify.sh +
check-routes + Playwright smoke).

RUN LOCALLY: backend `bash scripts/dev.sh backend` (uvicorn :8000, docs /docs);
frontend `cd frontend && npm run dev` (:3000); both `bash scripts/dev.sh`;
verify `bash scripts/verify.sh`. Tests: `cd backend && LLM_PROVIDER=mock
APP_ENV=development TESTING=true .venv/bin/python -m pytest -q`.

OPEN QUESTIONS FOR OWNER: prod DB (SQLite vs Postgres)? prod storage (S3 vs disk)?
prod LLM + exact model (gpt-5.5 invalid)? keep both CAD engines or consolidate?
is OpenSCAD fallback needed in prod? domain/TLS/reverse-proxy choice? greenfield
deploy or align with existing infra?
```
