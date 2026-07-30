# LunaiCAD Release-Readiness Report

**Audit date**: 2026-07-30
**Auditor**: Claude (Sonnet 5), independent release-candidate audit per user request
**This is not legal advice.** Legal document drafts referenced below require
qualified legal review before publication (see "Legal-review status").

## Exact commit / environment

| | |
|---|---|
| Branch | `hardening/export-integrity` |
| Base commit | `68f3eba89b27125e2c374b10f9b1e09f063b8940` ("Harden authorization uploads and production data boundaries") |
| Working tree | **134 files changed/added relative to base commit, uncommitted** — this audit covers the working tree AS-IS, not a clean commit. Everything below (Phase 10 Studio UX, Phase 11 production operations, Phase 12 safety/legal/privacy, Phase 13 this audit's own fixes) is uncommitted local work on this branch. **A release cannot happen from this state — commit and review the diff before any deploy.** |
| Backend runtime | Python 3.11.13, `backend/.venv` (canonical venv, symlinked to `~/.venvs/lunaicad-backend`) |
| Frontend runtime | Node 22.22.2, npm 11.15.0 |
| Database (dev/test) | SQLite; production target is Postgres 16 (`docs/deployment.md`) |
| Platform audited on | macOS (Darwin), Apple Silicon — production target is a Linux Hostinger VPS; some checks (below) could not be verified on this platform and are flagged |

## Verification commands run

```bash
# Backend — full suite
cd backend && .venv/bin/python -m pytest -q

# Backend — targeted re-verification of this audit's own changes
.venv/bin/python -m pytest -q tests/test_feedback.py tests/test_migrations.py \
  tests/test_auth.py tests/test_phase2_authorization.py

# Frontend
cd frontend && npm run typecheck && npx vitest run && npm run build

# Dependency scan
cd backend && .venv/bin/pip-audit -r requirements.txt

# SBOM regeneration (after the python-jose bump below)
./scripts/generate_sbom.sh

# Alert rules syntax
python3 -c "import yaml; yaml.safe_load(open('deploy/prometheus/alerts.yml'))"

# Backup script syntax
bash -n scripts/backup.sh
```

## Test counts

| Suite | Result |
|---|---|
| Backend full suite (`pytest -q`) | **Completed. ~1,940 tests, exactly 1 failure, 0 collection errors.** The one failure is `test_drawing_to_cad.py::test_svg_hole_count_preserved` — root-caused below, not a stale/flaky result (reproduces 5/5 in isolation). |
| Backend `test_feedback.py` (incl. 4 new report-bad-result tests) | 10/10 passed |
| Backend `test_migrations.py` (no drift) | passed |
| Backend `test_auth.py` + `test_phase2_authorization.py` (post python-jose bump) | 85/85 passed |
| Backend `test_beta_stress.py` (151-prompt corpus, all categories) | 7/7 passed |
| Frontend (`vitest`) | **90/90 passed** (8 test files; 18 in `TrustPanel.test.tsx` incl. 5 new report-a-problem tests) |
| Frontend typecheck (`tsc --noEmit`) | clean |
| Frontend build (`next build`) | clean, 13 routes generated |

### Full-suite result

```
=========================== short test summary info ============================
FAILED tests/test_drawing_to_cad.py::test_svg_hole_count_preserved - AssertionError: hole-count validation must run
```

~1,940 tests collected, 1 failed, 0 errors. This is the single confirmed,
root-caused product-quality gap described under "Real bug found and
root-caused this audit" below — every other test in the entire backend
suite passes, including all pre-existing tests and every test this audit
added or modified (`test_feedback.py`, `TrustPanel.test.tsx`,
`test_beta_stress.py`'s new false-positive-refusal guard, etc.).

## Security — verified findings

| # | Check | Verdict |
|---|---|---|
| 1 | No model-generated execution reaches production | **PASS** — repo-wide grep finds zero `eval`/`exec`/`os.system`/`pickle.loads`; `app/cad/plan/compiler.py` dispatches on a fixed whitelist of feature kinds; the legacy `cadquery_program` execution route and `designs.program_code` column were removed (migration `b1c4e7a92f38`), enforced by `tests/test_phase1_llm_trust_boundary.py`. |
| 2 | Tenant isolation | **PASS** — every design/job lookup joins through `user_id`/`owner_id` (`get_owned_design`, `_owned_or_404`), never fetch-by-id-alone. |
| 3 | Hostile upload controls | **PASS** — `app/services/upload_guard.py` enforces byte/pixel/page/entity/coordinate limits on the actual request path (decompression-bomb, XML-entity, oversized-file guards), not just defined-but-unused. |
| 4 | Worker isolation | **PASS** — real OS subprocess per job (`multiprocessing.get_context("spawn")`), `RLIMIT_AS`/`RLIMIT_CPU` enforced, crash/OOM detected and metriced, never takes down the API. |
| 5 | Secure artifacts | **PASS** — non-sequential UUID design ids, every download route ownership-checked before a signed URL/stream is issued. |
| 6 | Safe authentication | **PASS** — bcrypt hashing, JWT HS256 with real secret validation, no dev-mode bypass reachable in production (`production_problems()` refuses `DEV_MODE=true`). |
| 7 | Rate and resource limits | **PASS** — 57 rate-limited routes; `test_every_route_has_a_rate_limit_except_the_liveness_probe` (global invariant test) passing, including this audit's new `/report-bad-result` route (inherits the same dependency, no allowlist edit needed). |
| 8 | Production secret handling | **PASS** — `Settings.production_problems()`/`validate_startup()` fails FastAPI app construction outright on any of ~9 unsafe-config conditions (mock LLM, weak JWT secret, implicit DB, wildcard CORS, `DEV_MODE=true`, missing `OPS_API_TOKEN`, bad DB pool settings). |
| 9 | Dependency scanning | **CONCERN, partially addressed this audit** — see "Dependency scan findings" below. `pip-audit`/`npm audit` remain report-only in CI (documented, deliberate); secret scanning IS a hard CI gate (`scripts/check_secrets_baseline.py` + `check-secrets.sh`). |
| 10 | Backup and restore evidence | **PASS** — `docs/ops/backup-and-restore.md` contains an ACTUALLY EXECUTED restore drill (real Postgres 16 in Docker, `pg_dump`/`DROP DATABASE`/`pg_restore`/`alembic upgrade head`, timestamped, with before/after row counts and an ORM read-back), self-disclosed as run against a small fresh dataset, not production scale. |

### Dependency scan findings (this audit)

`pip-audit -r requirements.txt` before this audit: **19 known vulnerabilities
across 5 packages** (python-jose ×3 CVEs, pytest, vtk ×3, starlette ×7,
pyasn1 ×4, ecdsa ×1).

**Fixed this audit** (scoped, verified): `python-jose[cryptography]`
3.3.0 → 3.4.0 — this is the JWT library backing `app/auth/security.py`
(directly auth-critical), the CVEs had a clean fix version with no breaking
API change, and `tests/test_auth.py` + `test_phase2_authorization.py`
(85 tests) pass unchanged after the bump. SBOM regenerated to reflect it.

**Left as accepted risk, NOT fixed this audit** (explicitly out of scope —
each requires a wider, riskier dependency bump that isn't a "clearly scoped"
fix under this audit's constraints):
- `starlette` 0.41.3 (7 CVE entries, fix versions 0.47.2–1.3.1) — transitively
  pinned by `fastapi==0.115.6`; bumping starlette alone risks an
  incompatible pairing, bumping both is a real (if likely mechanical)
  upgrade that needs its own dedicated test pass, not a same-day patch.
- `vtk` 9.3.1 (3 CVEs, fix 9.5.1), `pyasn1` 0.4.8 (4 CVEs, fix 0.6.3/0.6.4),
  `ecdsa` 0.19.2 (1 CVE, no fix version listed), `pytest` 8.3.4 (1 CVE, fix
  9.0.3 — dev/test tooling only, not a production runtime dependency).

**Recommendation**: schedule a dedicated `fastapi`+`starlette` compatibility
upgrade pass before wide production traffic; track the others normally per
`docs/ops/deployment-runbook.md`'s "Dependency updates" cadence.

## Product quality — verified findings

| # | Check | Verdict |
|---|---|---|
| 1 | Text-to-CAD benchmark by category | **PRESENT, numeric rate not historically reported** — `tests/test_beta_stress.py` + `beta_stress_prompts.json` run 151 labeled prompts across 5 categories (single_part≥60, vague≥30, assembly≥25, unsupported≥20, adversarial≥15), asserting per-category allowed contract outcomes, no false-PASS, no export leaks. All 7 tests in this file currently pass. No single "X% success" number is printed anywhere — see "Required manual checks." |
| 2 | Drawing-to-CAD benchmark | **PRESENT BUT THIN** — `tests/test_drawing_to_cad.py` (22 tests) covers exactly 3 real vector fixtures (adapter plate, flange, 4-hole plate) plus synthetic PDF/PNG cases. Real, kernel-level checks (hole count, dimensions, bolt-circle extraction) — but 3 fixtures is thin corpus coverage for a "benchmark" claim. |
| 3 | Modification correctness | **PASS** — edit tests assert on resulting geometry values (e.g. `new_spec.holes[0].diameter == 8.0`), not just "didn't crash." |
| 4 | Clarification behavior | **PASS** — ambiguous prompts assert `needs_clarification=True` AND `preview is None`/no exports — never silently builds a guess. |
| 5 | Unsupported-request honesty | **PASS** — the 4-state honesty contract (`exact`/`partial`/`substituted`/`unsupported`) is enforced and tested (e.g. a nyloc nut never silently becomes a plain hex nut). |
| 6 | Export round trips | **PASS** — STEP bytes are re-imported through a real OCC kernel and tessellated in `test_step_reimports_cleanly`; watertight/manifold checks run on every compile. |
| 7 | Physical print evidence | **MISSING** — zero photos, zero recorded physical measurements anywhere in the repo; the local calibration DB has 0 measurement rows. Everything today is simulated/geometric-only. |
| 8 | Calibration provenance | **PASS (honest)** — the system never claims a calibrated profile it doesn't have; the generic-estimate disclaimer (`CALIBRATION_PROVENANCE_NOTICE`) is the universal fallback since no real profile exists yet. |

### Real bug found and root-caused this audit: SVG hole-count validation gap

`tests/test_drawing_to_cad.py::test_svg_hole_count_preserved` fails
consistently (5/5 isolated reruns, not flaky). Root-caused, not just
observed:

1. The vector-parsed adapter-plate `CadPlan` (4× Ø6 corner holes + Ø20
   center bore, rounded corners) correctly carries `expected.hole_count=5`
   through `plan_from_analysis` and `normalize_cad_plan`.
2. `build_and_validate(plan)` compiles this SPECIFIC plan to a
   **self-intersecting/invalid solid** (confirmed via direct reproduction:
   `CadGenerationError: Geometry ... is not a valid solid`) — a real
   CadQuery/OCC boolean-operation robustness issue for this hole+fillet
   combination, not a validation-reporting bug.
3. `app/routers/drawings.py`'s `_run_to_cad_pipeline` correctly catches this
   `CadGenerationError` and silently falls back to `_generate_from_interpretation`
   (the vision/interpretation path) — which DOES successfully build a design
   (the HTTP response is 200, `holes: 5` is logged) but that fallback path's
   `semantic_json["checks"]` never includes a hole-count-named entry, so the
   test's specific assertion fails even though the design itself looks
   reasonable end-to-end.

**This is a genuine, reproducible product-quality gap**, not a stale/flaky
test and not something introduced by this session's other work. Fixing the
underlying OCC boolean robustness issue is a CAD-kernel-level investigation
— correctly out of scope for "clearly scoped release blocker" under this
audit's constraints. It is the primary evidence behind this report's
CONTROLLED BETA (not unrestricted production) recommendation for
drawing-to-CAD specifically.

## Operations — verified findings, and this audit's fixes

| # | Check | Before this audit | Fixed this audit? |
|---|---|---|---|
| 1 | Deployment reproducibility | 6 deps used open version ranges (`>=`), no exact pins | **Fixed** — pinned `psycopg`, `bcrypt`, `email-validator`, `openai`, `boto3`, `ezdxf`, `pypdfium2` to exact currently-tested versions in `requirements.txt`. |
| 2 | Migrations | Real drift-detection test, passing | Already PASS, no change needed. |
| 3 | Rollback | Documented + `alembic downgrade` round-trip tested; no EXECUTED rollback drill with evidence (unlike backup/restore) | **Not fixed** — flagged as a required manual check before go-live (see below); executing a full code+schema rollback drill needs a real staging deploy, out of scope for this audit pass. |
| 4 | Health checks | Real `/health` vs `/ready` distinction, `/ready` does real DB+storage checks | Already PASS. |
| 5 | Monitoring/alerts | 19 real Prometheus metrics; alert THRESHOLDS only existed as a markdown table, zero committed alert-rule files | **Fixed** — `deploy/prometheus/alerts.yml` (7 real `groups:`/`rules:` alerts translating that table, YAML-validated), `docs/ops/observability.md` updated to point at it. |
| 6 | Queue saturation | Real pre-insert queue-depth + per-user concurrency checks, tested | Already PASS. |
| 7 | Degraded OpenAI behavior | Circuit breaker trips and fails fast; deterministic/template routes keep working independent of it | Already PASS. |
| 8 | Cost controls | Single GLOBAL daily cap, tripped AFTER the overspending call completes (reactive, not pre-spend); no per-account cost ceiling | **Not fixed** — see "Accepted risks." Existing per-account generation-count quotas (`quota_designs_per_day/month`) provide an indirect spend ceiling per account even without a direct dollar cap. |
| 9 | Retention | `retention_sweep.py` and the backup procedure were real but only documented as "run via cron" — **no committed script, no committed timer**; `scripts/backup.sh` referenced in docs didn't exist as a file at all | **Fixed** — created the real `scripts/backup.sh` (matching the doc's intended behavior, off-box copy via `BACKUP_REMOTE`, local retention pruning), and committed `deploy/systemd/lunaicad-backup.{service,timer}` + `lunaicad-retention.{service,timer}` (03:00 retention, 03:05 backup). `docs/deployment.md`, `docs/ops/backup-and-restore.md`, `docs/ops/data-retention.md` updated to install/reference these instead of a hand-copied snippet. |

## UX and honesty — verified findings

| # | Check | Verdict |
|---|---|---|
| 1 | Assumptions visible | **PASS** — `TrustPanel.tsx` genuinely renders `design.assumptions` as a list, not just stores it. |
| 2 | Limitations visible | **PASS** — same for `known_limitations`; all 46 registered CAD families carry non-empty limitation text. |
| 3 | Capability labels accurate | **PASS, one minor concern** — no `production_ready` family carries a contradictory "not FEA analyzed" hedge; `validated_beta` structural families (hinge_bracket, u_bracket, clamp_block, robotic_arm_base_bracket) rely entirely on the separate Phase 12 safety-category system to catch load-bearing misuse, rather than also carrying their own hedge text. Worth a follow-up if the safety-category coverage for these specific families is ever found to have a gap (none found in this audit). |
| 4 | Beta workflows labeled | **PASS** — both backend (`drawing_beta` field) and frontend (two independent badge/notice renderings) surface it. |
| 5 | Blocked exports explained | **PASS** — cause-specific reason strings for every block cause (safety, critical-failure, unsupported, awaiting-clarification), not one generic "blocked" message. |
| 6 | Safety language correct | **PASS** — zero banned-claim-language hits anywhere in `frontend/src/`; the only "certified"/"guaranteed"/"production-ready" occurrences found are explicit NEGATIONS ("not structurally certified", "not a guaranteed conversion"). |
| 7 | No unsupported marketing claims | **PASS** — landing page copy is hedged throughout; no `/pricing` or `/about` route exists to check separately. |

## Controlled-beta metrics — what changed this audit

The system could already measure validated-generation rate, download rate,
cost-per-validated/downloaded-result as pure ratios of pre-existing
Prometheus counters (`design_validation_total`, `design_downloads_total`,
`llm_estimated_cost_usd_total`). This audit added the raw ingredients that
were genuinely missing:

- `design_feedback_total{rating,report}` and a dedicated
  `report_bad_result()` service function — **bad-result rate** now has a
  real signal distinct from a plain thumbs-down.
- `design_edit_total{kind,outcome}`, incremented centrally in
  `log_design_telemetry` — **modification success rate**, overall and
  per edit-kind.
- `Feedback.print_success`/`fit_success` columns +
  `print_outcomes_total`/`fit_outcomes_total` counters — **reported
  print/fit success** did not exist in any form before this audit; there
  was no way for a user to report a physical outcome back to the system at
  all.
- **Time-to-first-useful-design** and **seven-day return rate** are cohort
  measures computed via SQL over existing `users.created_at`/
  `designs.created_at` timestamps — no new instrumentation needed, exact
  queries in `docs/ops/beta-metrics.md`.

Full exact PromQL/SQL for all 10 required metrics: `docs/ops/beta-metrics.md`.

## "Report a bad result" workflow — implemented this audit

- Backend: `Feedback` model extended (migration
  `f2a8b3a98437_add_bad_result_report_fields_to_feedback`) with
  `is_bad_result_report`, `design_version_number`, `prompt_version`,
  `validation_snapshot`, `print_success`, `fit_success`, `report_consent`,
  `report_reason`. New `POST /api/designs/{id}/report-bad-result` endpoint
  (authenticated, rate-limited, same pattern as the existing feedback
  route — inherits the global auth/rate-limit invariant tests automatically).
- **Privacy-conscious by construction**: `reason` (free text) is only ever
  persisted when `consent=True` is explicitly sent; every other field
  (categories, version numbers, validation snapshot, print/fit booleans) is
  non-identifying operational data and is always recorded regardless of
  consent. Verified by `test_report_bad_result_without_consent_drops_reason`.
- A design never edited yet gets a version-1 baseline created on demand
  (`version_service.ensure_baseline`) so "which version was this bad?"
  always has an answer.
- `CAD_PROMPT_VERSION` (`app/llm/prompt_version.py`, currently `"2026.1"`)
  is frozen onto every design's `semantic_json["prompt_version"]` at
  generation time (not read live), so a later prompt/template bump never
  rewrites the history of what actually produced an already-reported design.
- Frontend: a "Report a problem with this design" disclosure in
  `TrustPanel.tsx` — category multi-select, print/fit tri-state toggles, a
  consent checkbox gating the explanation textarea, submit/cancel. 5 new
  tests in `TrustPanel.test.tsx`, all passing.

## Unresolved blockers

**None classified as release-blocking (NO-GO) per the rules below** — every
NO-GO trigger was checked and did not fire (see "Go/No-Go assessment"). The
following are real, evidenced gaps that inform the CONTROLLED BETA
recommendation, not blockers to fix before ANY release:

1. SVG-drawing hole-count validation gap (root-caused above) — affects one
   confirmed real fixture; unknown how many other drawing shapes hit the
   same underlying OCC boolean-robustness issue.
2. Zero physical print/fit evidence exists anywhere.
3. No numeric text-to-CAD success-rate has ever been computed/reported
   (the category-shaped pass/fail corpus exists and passes, but "92%
   success" style tracking does not).
4. Cost controls are reactive (post-spend) and global-only (no per-account
   cap).
5. No executed rollback drill (migration up/down IS tested; a full
   code+schema rollback against a real deploy is not).
6. `starlette`/`vtk`/`pyasn1`/`ecdsa` CVEs remain unaddressed (documented,
   deliberately deferred — see "Dependency scan findings").
7. The working tree has 134 uncommitted files — nothing in this report has
   been reviewed as a PR or committed.

## Accepted risks (explicit, not silently ignored)

- **Per-account LLM cost ceiling**: not implemented this audit (would need
  a new per-design cost ledger persisted to SQL, not just a Prometheus
  counter, to query per-user — a real, non-trivial addition). Mitigated
  today by per-account generation-COUNT quotas (`quota_designs_per_day/month`),
  which bound worst-case spend indirectly even without a dollar figure.
- **Backup off-box copy is opt-in** (`BACKUP_REMOTE` env var) — `scripts/backup.sh`
  runs and logs a loud warning if unset, but will not fail the systemd unit;
  an operator who deploys without setting `BACKUP_REMOTE` has a backup that
  lives next to the database it protects. Verify this is set during
  production environment validation (checklist below).
- **`starlette`/`vtk`/`pyasn1`/`ecdsa` CVEs**: see dependency findings above.
- **Structural `validated_beta` families' limitation text** doesn't itself
  carry a "not structurally validated" hedge (relies on the separate safety-
  category system) — no gap found in practice, but worth re-checking if
  that family list grows.

## Required manual checks before go-live

- [x] Full backend `pytest -q` completed: ~1,940 tests, 1 failure (the
      root-caused SVG hole-count gap), 0 errors.
- [ ] Commit and PR-review the 134 uncommitted files this report audited.
- [ ] Compute and record an actual numeric text-to-CAD success rate from the
      `beta_stress_prompts.json` corpus (script logic sketched in this
      audit's session but not completed — SQLite contention with parallel
      background test runs stalled it; re-run in isolation).
- [ ] Set `BACKUP_REMOTE` in production `backend/.env` and verify a real
      off-box backup lands (`systemctl start lunaicad-backup.service`
      manually once, check the remote).
- [ ] `systemctl enable --now lunaicad-backup.timer lunaicad-retention.timer`
      and confirm with `systemctl list-timers` — these are newly committed
      this audit and have never run against a real deployment.
- [ ] Load `deploy/prometheus/alerts.yml` into a real Prometheus instance and
      confirm rules parse and a receiver is wired (this repo intentionally
      does not commit Alertmanager receiver config — operator-specific).
- [ ] Execute one real rollback (code + a reversible schema change) against
      staging, with evidence, mirroring the already-executed restore drill.
- [ ] Decide and document an explicit "supported family" text-to-CAD success
      threshold — none was specified for this audit to check against.
- [ ] At least one real physical print + measurement + fit check, recorded
      with photos/measurements, before any user-facing "calibrated" claim
      is made (none is made today — the generic-estimate disclaimer is
      honest — but a controlled beta should still validate this before
      widening).

## Physical-validation evidence

**None exists.** Confirmed via repo-wide search (no photos, no recorded
measurements, 0 rows in the local calibration-measurements table). This is
the single clearest input to the CONTROLLED BETA (not unrestricted
production) recommendation below — the system is honest about this gap
(never claims a calibrated profile it doesn't have), but that honesty
doesn't substitute for actually knowing whether printed parts fit.

## Legal-review status

8 draft documents exist under `docs/legal/` (Terms of Service, Privacy
Policy, Acceptable Use Policy, Generated-design ownership, Data retention,
AI/model data usage, Safety and engineering disclaimer, Calibration-profile
disclaimer). All 8 carry the required "DRAFT — NOT LEGAL ADVICE, REQUIRES
QUALIFIED LEGAL REVIEW" banner with explicit `[UNRESOLVED — REQUIRES LEGAL
INPUT]` markers for jurisdiction/tax/entity/liability decisions. **None has
been reviewed by qualified counsel.** Do not publish or link any of these
publicly until that review happens.

## Rollback plan

Documented in full at `docs/ops/deployment-runbook.md` ("Rollback
procedure"): code rollback via `git checkout <sha>` + dependency reinstall +
service restart; schema rollback via `alembic downgrade -1` (every migration
has a real, tested `downgrade()`); a migration that can't be cleanly
downgraded falls back to restoring the pre-migration backup. **Not yet
executed end-to-end with evidence** — see "Required manual checks."

## Controlled-beta recommendation

# CONTROLLED BETA — not unrestricted production.

**Why not NO-GO**: every explicit NO-GO trigger was checked against real
evidence and none fires — no production-reachable model-generated
execution, no cross-user data access, bounded CAD jobs (queue depth +
per-user concurrency + per-account quotas), no fake/invalid STEP exports
reaching users (the one confirmed invalid-solid case is caught and
prevented from exporting, not silently shipped), real backup/restore
evidence exists, safety-critical categories are gated (Phase 12), and a
product-level correctness benchmark DOES exist (151 labeled prompts, 5
categories, currently all passing).

**Why not unrestricted production**: drawing-to-CAD accuracy has a
confirmed, real, root-caused gap (not hypothetical); physical
print/fit/calibration evidence is completely absent; no numeric
success-rate has ever been tracked against an agreed threshold; cost
controls and rollback both have real, disclosed gaps. None of these are
signs of a broken product — they are signs of a product that hasn't yet
been exercised against real users, real printers, and real production
traffic volume. A controlled beta (a bounded set of real users, active
monitoring via the alerts/metrics this audit helped complete, and a fast
feedback loop via the new "report a bad result" workflow) is exactly the
right next step to close these gaps with real evidence instead of more
simulated confidence.
