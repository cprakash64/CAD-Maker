# Release Change Inventory — Prompt 14 (Freeze, Preserve, Inventory)

**Purpose**: a source-backed inventory of every uncommitted change in the
working tree, produced BEFORE any commit is made, so the ~134-file (actual:
154 raw `git status` entries / 209 individual files once untracked
directories are expanded — see "Correction to the 134 figure" in the
verification section) uncommitted diff can be reviewed and split into
reviewable commits instead of landing as one undifferentiated blob.

**This phase changed no application code, no tests, no migrations.** Every
finding below is read-only investigation. The two things this phase *did*
write are this document and the external backup (see the accompanying
final report).

- Branch: `hardening/export-integrity`
- HEAD: `68f3eba89b27125e2c374b10f9b1e09f063b8940`
- Merge-base with `main`: `a07af49308ced6974846c0596d4c59ce312dea0f` (this
  branch is 3 commits ahead of `main`: `d297424`, `f346bde`, `68f3eba`)
- Captured: 2026-07-30T19:20Z

---

## Category legend

| # | Category |
|---|---|
| 1 | Security boundary |
| 2 | Tenant isolation |
| 3 | Upload or artifact hardening |
| 4 | Evaluation harness |
| 5 | Product contract or capability policy |
| 6 | Text-to-CAD reliability |
| 7 | Drawing-to-CAD |
| 8 | Calibration or print profiles |
| 9 | Worker isolation or queueing |
| 10 | Studio UX or editing |
| 11 | Observability or cost control |
| 12 | Safety, privacy, or legal surfaces |
| 13 | Backup, deployment, or operations |
| 14 | Tests or fixtures |
| 15 | Database migration |
| 16 | Documentation |
| 17 | Generated build output |
| 18 | Local development state |
| 19 | Suspicious / requires manual review |
| 20 | Unclear ownership |

Many backend files legitimately serve TWO categories (e.g. a calibration
resolver is both "Calibration" and "Text-to-CAD reliability"); the table
lists the PRIMARY category per the task's instruction, with a secondary
noted in the notes column where genuinely dual-purpose.

---

## Step 3 — Full file classification

### Legend for columns
- **State**: `M` modified tracked / `??` untracked (new) / `SYMLINK` special case
- **Size**: approximate lines changed (modified) or lines total (new file)
- **Runtime?**: does this change production runtime behavior
- **API?**: does this change a request/response contract
- **Migration?**: involves a DB schema migration
- **Safe?**: safe to commit as source (see Step 4 for exclusions)

#### Category 1 — Security boundary

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/cad/plan/schema.py` | M | +26 | 11 (hardening) | Yes — rejects unexpected extra fields in LLM/plan-produced CadPlan JSON (`model_config={"extra":"forbid"}`) | Internal schema only, no wire contract change | No | `test_migrations.py` indirectly (model shape), broad CadPlan test coverage | No | Yes |
| `.github/workflows/security.yml` | ?? | 87 | 11 | CI-only, no app runtime | No | No | N/A (CI config) | No | Yes |
| `.secrets.baseline` | ?? | 265 | 11 | No | No | No | `scripts/check_secrets_baseline.py` | Yes — `detect-secrets` output, but intentionally committed/audited | Yes (already re-scanned clean this session) |
| `scripts/check-secrets.sh` | ?? | — (existing tool, re-verified working) | 11 | CI/dev tooling only | No | No | Self-verifying (exit code) | No | Yes |
| `scripts/check_secrets_baseline.py` | ?? | 78 | 11 | CI/dev tooling only | No | No | Self-verifying | No | Yes |

#### Category 2 — Tenant isolation

No files in this diff are PRIMARILY about tenant isolation — the existing
`get_owned_design`/`_owned_or_404` pattern (verified in the prior audit) was
not modified this session; new endpoints (report-bad-result, calibration,
jobs, ops) all *reuse* that existing pattern rather than changing it. See
`backend/app/routers/designs.py`, `backend/app/routers/calibration.py`,
`backend/app/routers/jobs.py` under their primary categories below — each
inherits tenant isolation from the shared helper, verified in Step 7.

#### Category 3 — Upload or artifact hardening

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/services/upload_guard.py` | M | +38 | pre-session (11 or earlier) | Yes | No (internal validation) | No | `test_phase2_upload_limits_and_data.py` | No | Yes |

#### Category 4 — Evaluation harness

Entire `backend/eval/` package (harness, fixtures, runner, CLI, versioning)
and its CI wiring predate this visible session (eval_reports are dated
2026-07-27, before Phases 10-14). Classified here as a cohesive unit.

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/eval/__init__.py`, `assertions.py`, `bootstrap.py`, `cli.py`, `context.py`, `executors.py`, `live_instrumentation.py`, `report.py`, `runner.py`, `schema.py`, `versioning.py`, `README.md` | ?? | ~1,900 total | pre-visible-session | No (offline eval tool, not imported by the app) | No | No | `test_eval_harness_regressions.py` | No | Yes |
| `backend/eval/fixtures/*.json` (7 files) | ?? | ~1,450 total | pre-visible-session | No | No | No | Consumed by the eval runner | No (hand-authored fixtures) | Yes |
| `.github/workflows/eval.yml` | ?? | 57 | pre-visible-session | CI-only | No | No | N/A | No | Yes |
| `backend/eval_reports/baseline_phase4/`, `baseline_phase5/`, `baseline_phase6_run1/` (2 files each) | ?? | ~14,000 total (mostly JSON data) | pre-visible-session | No | No | No | These ARE the historical benchmark evidence | **Yes, machine-generated eval OUTPUT** | Judgment call — see note |
| `backend/eval_reports/generation_regression_mock_*.json` (2), `semantic_benchmark_mock_*.json` (2) | ?? | ~4,600 total | pre-visible-session | No | No | No | Ad-hoc, not organized into a "baseline_" folder | Yes, generated scratch output | **No — recommend excluding, see category 18** |
| `backend/tests/data/eval_broken.svg` | ?? | 0 lines (deliberately malformed fixture) | pre-visible-session | No | No | No | `test_eval_harness_regressions.py` (adversarial malformed-input fixture) | No (hand-authored-empty) | Yes |

**Note on `eval_reports/baseline_phase*`**: these are curated, named milestone
snapshots (the release report cites them as evidence a benchmark exists) —
recommend keeping as committed historical evidence. The four loose
`*_mock_2026*.json` files at the top level of `eval_reports/` look like
incidental one-off local runs with no curation; recommend treating those as
category 18 (local development state) and NOT committing them, or moving
them under `.gitignore`'d `eval_reports/drawing_debug/`-style exclusion if
they have ongoing value. This is a judgment call for the branch owner, not
a functional risk either way.

#### Category 5 — Product contract or capability policy

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/cad/families.py` | M | +400/-38 | pre-visible-session (Maturity relabel) + 12 (safety metadata) | Yes — capability-level labels renamed (`beta`→`validated_beta`, `concept`→`experimental`) | Yes — `capability_level` wire values change | No | `test_capabilities_api.py`, `test_product_contract.py` | No | Yes |
| `backend/app/cad/registry.py` | M | +22 | 8 (calibration coupons) | Yes — registers 10 new calibration-coupon templates | Yes — new `object_type`s become generatable | No | Covered by `test_calibration.py` + template registry tests | No | Yes |
| `backend/app/cad/plan/policy.py` | M | +28/-… | 6/7 | Yes — clarification decision logic | Indirect (changes `needs_clarification` outcomes) | No | `test_ambiguous_prompts.py` (pre-existing, not in this diff but exercises this file) | No | Yes |
| `backend/app/cad/plan/clarification_categories.py` | ?? | 70 | 6/7 | Yes | Indirect | No | Exercised via `policy.py`'s consumers | No | Yes |
| `README.md` | M | +2/-1 | 5 (docs sync) | No | No | No | N/A | No | Yes |
| `docs/product-contract.md` | ?? | 251 | 5 | No (doc) | Documents the contract | No | N/A | No | Yes |
| `docs/CAD_FAMILIES.md` | M | +88 | 5/8 | No (doc) | Documents family/maturity changes | No | N/A | No | Yes |

#### Category 6 — Text-to-CAD reliability

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/cad/standards/defaults.py` | M | +4 | pre-visible-session | Yes — more metric clearance sizes | No | No | `test_templates.py` | No | Yes |
| `backend/app/cad/hex_standoff.py` | M | +2/-7 | pre-visible-session | Yes — dedupes clearance table, delegates to `standards/defaults.py` | No | No | Existing hex-standoff tests | No | Yes |
| `backend/app/cad/plan/defaults.py` | M | +15 | pre-visible-session | Yes | No | No | Template tests | No | Yes |
| `backend/app/cad/plan/deterministic.py` | M | +42 | pre-visible-session | Yes | No | No | `test_hard_prompts.py`-class coverage | No | Yes |
| `backend/app/cad/plan/dimension_report.py` | M | +14 | pre-visible-session/12 (timing) | Yes | Adds fields to dimension report | No | Covered indirectly | No | Yes |
| `backend/app/cad/plan/compiler.py` | M | +3/-… | pre-visible-session | Yes | No | No | Broad CadPlan test coverage | No | Yes |
| `backend/app/cad/selectable_faces.py` | M | +50 | pre-visible-session | Yes | Adds selectable-feature metadata | No | `test_selectable_faces.py`, `test_selection_phase6.py` | No | Yes |
| `backend/app/cad/understanding.py` | M | +2/-1 | pre-visible-session | Yes | Minor | No | Covered indirectly | No | Yes |
| `backend/app/manufacturability/checks.py` | M | +55 | pre-visible-session | Yes | No | No | Manufacturability test coverage | No | Yes |
| `backend/app/llm/mock_provider.py` | M | +10/-… | pre-visible-session | Test/dev-only provider | No | No | `test_mock_provider_word_boundaries.py` | No | Yes |
| `backend/tests/test_mock_provider_word_boundaries.py` | ?? | 84 | pre-visible-session | Test only | — | — | Self | No | Yes |

#### Category 7 — Drawing-to-CAD

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/services/drawing_to_spec.py` | M | +37 | pre-visible-session | Yes | No | No | `test_drawing_to_cad.py` | No | Yes |
| `backend/app/schemas/drawing_spec.py` | M | +176 | pre-visible-session | Yes | Yes — schema fields | No | `test_drawing_to_cad.py` | No | Yes |
| `backend/app/schemas/drawing_analysis.py` | M | +17 | pre-visible-session | Yes | Yes | No | `test_drawing_to_cad.py` | No | Yes |
| `backend/app/services/drawing_jobs.py` | M | +151/-… (net refactor) | 9 (job-queue migration) | Yes — reduced to a thin sync-path shim now that `job_service.py` owns real job state | Internal | No | `test_job_queue.py`, `test_drawing_beta_acceptance.py` | No | Yes |
| `backend/app/routers/drawings.py` | M | +217 | 7/9/12 (safety gate wiring) | Yes | Yes | No | `test_drawing_to_cad.py`, `test_drawing_beta_acceptance.py`, `test_safety_policy.py` | No | Yes |
| `backend/tests/test_drawing_to_cad.py` | M | +72 | 7 | Test only | — | — | Self | No | Yes — **contains the 1 known-failing test, do not silently "fix" by deleting/weakening the assertion** |
| `backend/tests/test_drawing_beta_acceptance.py` | ?? | 145 | 7 | Test only | — | — | Self | No | Yes |
| `docs/drawing-to-cad-beta.md` | ?? | 170 | 7 | No (doc) | — | — | — | No | Yes |

#### Category 8 — Calibration or print profiles

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/cad/calibration/__init__.py` | ?? | 0 | 8 | — | — | — | — | No | Yes |
| `backend/app/cad/calibration/resolver.py` | ?? | 181 | 8 | Yes — resolves generic-estimate vs validated-profile measurements | Indirect | No | `test_calibration.py` | No | Yes |
| `backend/app/cad/templates/calibration_coupons.py` | ?? | 555 | 8 | Yes — 10 new coupon part templates | Yes — new object types | No | `test_calibration.py` | No | Yes |
| `backend/app/schemas/calibration.py` | ?? | 301 | 8 | Yes | Yes — new API schemas | No | `test_calibration.py` | No | Yes |
| `backend/app/services/calibration_service.py` | ?? | 471 | 8 | Yes | Backs calibration endpoints | No | `test_calibration.py` | No | Yes |
| `backend/app/routers/calibration.py` | ?? | 400 | 8 | Yes | Yes — new `/api/calibration/*` routes | No | `test_calibration.py` | No | Yes |
| `backend/alembic/versions/2026_07_27_1400-...add_calibration_profiles.py` | ?? | 115 | 8 | Yes | — | **Yes** | `test_migrations.py` | No | Yes (reviewed Step 5, safe — new tables only) |
| `backend/app/cad/object_intelligence/resolver.py` | M | +31 | 8 | Yes — bearing/phone-holder resolvers now source clearances from the calibration resolver instead of hardcoded literals | No | No | Object-intelligence test coverage | No | Yes |
| `docs/calibration.md` | ?? | 275 | 8 | No (doc) | — | — | — | No | Yes |
| `docs/legal/calibration-profile-disclaimer.md` | ?? | 54 | 12 | No (doc, legal draft) | — | — | — | No | Yes |
| `backend/tests/test_calibration.py` | ?? | 458 | 8 | Test only | — | — | Self | No | Yes |

#### Category 9 — Worker isolation or queueing

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/worker/__init__.py`, `__main__.py`, `handlers.py`, `runner.py`, `supervisor.py` | ?? | ~645 total | 9 | Yes — the entire DB-backed job worker process | N/A (separate OS process) | No | `test_job_queue.py` | No | Yes |
| `backend/app/services/job_service.py` | ?? | 536 | 9/11 (quotas) | Yes — sole writer of `Job` rows, queue depth/concurrency/quota checks | Indirect (job status API) | No | `test_job_queue.py`, `test_quotas.py` | No | Yes |
| `backend/app/routers/jobs.py` | ?? | 51 | 9 | Yes | Yes — job status polling route | No | `test_job_queue.py` | No | Yes |
| `backend/alembic/versions/2026_07_28_1000-...add_jobs.py` | ?? | 60 | 9 | Yes | — | **Yes** | `test_migrations.py` | No | Yes (reviewed Step 5, safe — new table) |
| `docs/adr/0001-job-queue-database-backed.md` | ?? | 147 | 9 | No (doc) | — | — | — | No | Yes |
| `backend/tests/test_job_queue.py` | ?? | 359 | 9 | Test only | — | — | Self | No | Yes |
| `backend/tests/test_quotas.py` | ?? | 86 | 11 | Test only | — | — | Self | No | Yes |

#### Category 10 — Studio UX or editing

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/editing/face_edit.py` | M | +36 | 10 | Yes | No | No | `test_face_edit.py` | No | Yes |
| `backend/app/editing/localized.py` | M | +112 | 10 | Yes | No | No | `test_localized_edit.py` | No | Yes |
| `backend/app/services/version_service.py` | ?? | 221 | 10 | Yes — version snapshot/restore/diff | Indirect | No | `test_design_versions.py` | No | Yes |
| `backend/alembic/versions/2026_07_28_1100-...add_design_versions.py` | ?? | 50 | 10 | Yes | — | **Yes** | `test_migrations.py` | No | Yes (reviewed Step 5, safe — new table) |
| `backend/app/export/glb.py` | ?? | 102 | 10 | Yes — GLB export for the 3D viewer | Yes — new export format | No | `test_glb_export.py` | No | Yes |
| `frontend/src/app/studio/[id]/page.tsx` | M | +151 | 10/12 | Yes | — | — | `TrustPanel.test.tsx` etc. exercise the components it renders | No | Yes |
| `frontend/src/app/drawing/page.tsx` | M | +123 | 7/10 | Yes | — | — | Manual/E2E | No | Yes |
| `frontend/src/components/TrustPanel.tsx` + `.test.tsx` | ?? | 408+187 | 10/12/14 | Yes | — | — | Self (18 tests) | No | Yes |
| `frontend/src/components/VersionHistory.tsx` + `.test.tsx` | ?? | 202+139 | 10 | Yes | — | — | Self (6 tests) | No | Yes |
| `frontend/src/components/EditDiff.tsx` + `.test.tsx` | ?? | 52+51 | 10 | Yes | — | — | Self (6 tests) | No | Yes |
| `frontend/src/components/ClarificationCard.tsx` + `.test.tsx` | ?? | 68+69 | 10 | Yes | — | — | Self (5 tests) | No | Yes |
| `frontend/src/components/ExportMenu.tsx` + `.test.tsx` | M / ?? | 50 / 73 | 10 | Yes | — | — | Self (4 tests) | No | Yes |
| `frontend/src/components/Header.tsx` | M | +4 | 10 | Yes (minor) | — | — | — | No | Yes |
| `frontend/src/lib/testFixtures.ts` | ?? | 55 | 10/14 | Test infra only | — | — | Used by all component tests | No | Yes |
| `frontend/vitest.setup.ts`, `vitest.config.ts` (M) | ?? / M | 8 / +16 | 10 | Test infra only | — | — | — | No | Yes |
| `backend/tests/test_face_edit.py`, `test_localized_edit.py`, `test_selectable_faces.py`, `test_selection_phase6.py`, `test_design_versions.py` | M / ?? | various | 10 | Test only | — | — | Self | No | Yes |

#### Category 11 — Observability or cost control

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/metrics.py` | ?? | 239 | 11/13 | Yes — the whole Prometheus metric catalog | Yes — `/metrics` output | No | `test_ops_endpoints.py` and indirectly everywhere metrics are incremented | No | Yes |
| `backend/app/observability.py` | M | +111 | 11 | Yes — redaction, pseudonymization, request-id, prompt fingerprinting | No | No | `test_auth_abuse_metrics.py` and general log-shape assertions | No | Yes |
| `backend/app/llm/circuit_breaker.py` | ?? | 115 | 11 | Yes | No | No | `test_llm_circuit_breaker.py` | No | Yes |
| `backend/app/llm/pricing.py` | ?? | 44 | 11 | Yes | No | No | `test_llm_pricing.py` | No | Yes |
| `backend/app/llm/prompt_version.py` | ?? | 12 | 13 | Yes — frozen prompt-version constant | No | No | Exercised via `test_feedback.py`'s report-bad-result tests | No | Yes |
| `backend/app/llm/openai_provider.py` | M | +51 | 11 | Yes — circuit breaker + cost/token metrics wired into the real call path | No | No | Existing OpenAI provider tests | No | Yes |
| `backend/app/ops/__init__.py`, `retention_sweep.py` | ?? | 125 | 11 | Yes | — | No | `test_retention_sweep.py` | No | Yes |
| `backend/app/routers/ops.py` | ?? | 102 | 11 | Yes | Yes — `/health`, `/ready`, `/metrics` | No | `test_ops_endpoints.py` | No | Yes |
| `backend/app/database.py` | M | +13 | 11 | Yes — pool sizing | No | No | `test_production_startup_hardening.py` | No | Yes |
| `backend/app/config.py` | M | +134 | 11/12 | Yes — ~15 new settings, `production_problems()` extended | No (env-driven) | No | `test_production_startup_hardening.py`, `test_v037_production.py` | No | Yes |
| `backend/app/main.py` | M | +51 | 11 | Yes — request-id middleware, timing/metrics middleware, ops router registration | No | No | Broad HTTP test coverage | No | Yes |
| `backend/.env.example` | M | +47/-… | 11/12 | Docs/template only (no secrets — verified) | — | — | — | No | Yes |
| `backend/requirements.txt` | M | +20/-… | 11/13 | Yes — dependency pins, `python-jose` CVE fix, `prometheus_client` add | No | No | Full suite (implicit) | No | Yes |
| `deploy/prometheus/alerts.yml` | ?? | 91 | 13 | Ops config, not app runtime | — | — | YAML-validated this session | No | Yes |
| `deploy/systemd/lunaicad-backup.{service,timer}`, `lunaicad-retention.{service,timer}` | ?? | 19+15+11+12 | 13 | Ops config | — | — | Syntax-reviewed; NOT executed against a real systemd host | No | Yes |
| `deploy/systemd/lunaicad-backend.service`, `lunaicad-worker.service` | ?? | 30+48 | 11 | Ops config (untracked — see "unclear ownership" note below) | — | — | — | No | Yes |
| `deploy/smoke_test.py` | ?? | 172 | 11 | Ops tooling | — | — | Manually verified against a live local instance (per prior phase) | No | Yes |
| `scripts/backup.sh` | ?? | 37 | 13 | Ops tooling | — | — | `bash -n` syntax-checked this session | No | Yes |
| `scripts/generate_sbom.sh` | ?? | 28 | 11 | Ops tooling | — | — | Run successfully this session | No | Yes |
| `sbom/backend-sbom.cdx.json`, `sbom/frontend-sbom.cdx.json` | ?? | 5,492 + 13,686 | 11/13 | No (data artifact) | — | — | — | **Yes — fully machine-generated by `cyclonedx-py`/`npm sbom`** | Yes — intentionally committed as a security artifact, not excluded despite being generated |
| `docs/ops/*.md` (8 files: beta-metrics, backup-and-restore, data-retention, deployment-runbook, incident-response, observability, security-scanning) | ?? | ~1,300 total | 11/13 | No (doc) | — | — | — | No | Yes |
| `docs/deployment.md` | ?? | 240 | 11/13 | No (doc) | — | — | — | No | Yes |
| `backend/tests/test_llm_circuit_breaker.py`, `test_llm_pricing.py`, `test_generation_timing.py`, `test_quotas.py`, `test_retention_sweep.py`, `test_ops_endpoints.py`, `test_auth_abuse_metrics.py`, `test_production_startup_hardening.py` | ?? | various | 11 | Test only | — | — | Self | No | Yes |

**Note on `deploy/systemd/lunaicad-backend.service` and `lunaicad-worker.service`**:
these show as untracked (`??`) even though they were described in the prior
session's summary as already existing/committed. Re-verify at commit time —
possible that `deploy/` itself was never committed as a directory despite
individual files being edited in a prior (now-lost) commit, or these are
genuinely new. Flagged for the committer to double check rather than assumed.

#### Category 12 — Safety, privacy, or legal surfaces

| Path | State | Size | Phase | Runtime? | API? | Migration? | Tests | Generated? | Safe? |
|---|---|---|---|---|---|---|---|---|---|
| `backend/app/safety/__init__.py`, `categories.py`, `classifier.py`, `language.py`, `policy.py` | ?? | ~640 total | 12 | Yes — the entire safety-policy layer | Yes — refusal responses, `safety` DTO field | No | `test_safety_policy.py` | No | Yes |
| `backend/app/services/account_service.py` | ?? | 111 | 12 | Yes — account deletion, privacy summary | Yes — new endpoints | No | `test_privacy_controls.py` | No | Yes |
| `backend/app/routers/auth.py` | M | +86 | 12 | Yes — abuse metrics, privacy endpoints | Yes | No | `test_auth_abuse_metrics.py`, `test_privacy_controls.py` | No | Yes |
| `backend/app/auth/deps.py` | M | +5 | 12 | Yes (minor) | No | No | Existing auth tests | No | Yes |
| `backend/app/services/design_service.py` | M | +468/-… | 10/12/13 (largest single diff in the tree) | Yes — safety gate wiring, sticky classification, report-bad-result, prompt-version stamping | Yes — several DTO fields | No | `test_safety_policy.py`, `test_feedback.py`, broad design-service coverage | No | Yes — **but this file's diff is large and multi-purpose; see commit-split note in Step 6** |
| `backend/app/routers/designs.py` | M | +302 | 10/12/13 | Yes | Yes — several new/changed routes and response fields | No | Broad `tests/test_api.py` + feature-specific tests | No | Yes — same multi-purpose caveat as `design_service.py` |
| `backend/app/models.py` | M | +250 | 8/9/10/12/13 (every phase touched this) | Yes | Indirect | **Yes (backs 5 migrations)** | `test_migrations.py` (drift check) | No | Yes |
| `backend/app/schemas/api.py` | M | +136 | 10/12/13 | Yes | Yes — many new request/response models | No | Exercised by every router test | No | Yes |
| `backend/app/schemas/complex_cad.py`, `design_spec.py`, `editing_spec.py` | M | 66/18/16 | pre-visible/10 | Yes | Yes | No | Broad coverage | No | Yes |
| `backend/app/llm/schemas.py` | M | +13 | 12 | Yes | Indirect | No | Broad coverage | No | Yes |
| `frontend/src/lib/api.ts` | M | +56/-… | 10/12/13 | Yes | Yes — new client calls | No | `TrustPanel.test.tsx`, `VersionHistory.test.tsx` mock this module | No | Yes |
| `frontend/src/lib/types.ts` | M | +113 | 10/12/13 | Yes | Yes — new DTO shapes | No | Same | No | Yes |
| `frontend/src/app/settings/page.tsx` | ?? | 209 | 12 | Yes — privacy controls UI | — | — | Manual/E2E (no dedicated test file found) | No | Yes — **flag: no automated test coverage found for this page, recommend adding before/soon after commit** |
| `docs/legal/*.md` (8 files + README) | ?? | ~560 total | 12 | No (legal drafts) | — | — | — | No | Yes — **all carry required DRAFT/NOT-LEGAL-ADVICE banners, verified this session; none reviewed by counsel** |
| `backend/tests/test_safety_policy.py`, `test_privacy_controls.py`, `test_product_contract.py`, `test_adversarial_injection_hardening.py`, `test_ownership_export_hardening.py` | ?? | ~1,700 total | 12 | Test only | — | — | Self | No | Yes |
| `backend/tests/test_feedback.py` | M | +75 | 13 | Test only | — | — | Self (10/10 passing, incl. 4 new report-bad-result tests) | No | Yes |
| `backend/alembic/versions/2026_07_29...safety_acknowledgments...py` | ?? | 64 | 12 | Yes | — | **Yes** | `test_migrations.py` | No | Yes (reviewed Step 5, safe — `server_default` used correctly) |
| `backend/alembic/versions/2026_07_30...bad_result_report_fields...py` | ?? | 50 | 13 | Yes | — | **Yes** | `test_migrations.py` | No | **Yes to commit, but see Step 5 — has a real production-data-safety bug, do not deploy as-is** |

#### Category 13 — Backup, deployment, or operations

Covered above under category 11 (`deploy/`, `scripts/backup.sh`, `docs/ops/*`)
— operations and observability changes were made together this session and
are hard to meaningfully split by category; see category 11's rows.

#### Category 14 — Tests or fixtures

Every dedicated test file is listed inline under the category of the feature
it tests (this matches how they should be committed — WITH their feature,
per Step 6's "keep implementation and its tests together" rule) rather than
duplicated in a separate table here. Purely infra-only test files:

| Path | State | Size | Notes |
|---|---|---|---|
| `backend/tests/conftest.py` | M | +15 | Shared fixtures (incl. `_reset_llm_circuit_breaker` autouse fixture from Phase 11) |
| `backend/tests/test_api.py`, `test_beta_readiness.py`, `test_capabilities_api.py`, `test_complexity_and_reliability.py`, `test_phase2_authorization.py`, `test_phase2_upload_limits_and_data.py`, `test_phase7_hardening.py`, `test_v037_production.py`, `test_beta_stress.py`, `test_templates.py` | M | small diffs each | Pre-existing test files updated in place for new fields/routes/invariants across phases |

#### Category 15 — Database migration

All 5 migrations are cross-referenced under the category of the feature they
back (calibration, jobs, design_versions, safety/privacy, report-bad-result)
per the instruction to inspect the diff rather than classify by filename
alone — a migration's PRIMARY category is what it enables, not "migration"
generically. Full standalone review in Step 5 below.

#### Category 16 — Documentation

| Path | State | Size | Safe? |
|---|---|---|---|
| `docs/architecture/generation-boundary.md`, `docs/architecture/request-data-flow.md` | ?? | 440+210 | Yes — pre-visible-session architecture docs |
| `docs/production-readiness-audit.md` | ?? | 806 | Yes — an EARLIER, independent audit phase (dated same HEAD, references a still-earlier `docs/production-readiness.md` which is already tracked/unchanged) |
| `docs/release-readiness-report.md` | ?? | 359 | Yes — this session's Phase 13 output |

#### Category 17 — Generated build output

None found tracked as committable. `frontend/.next/`, `node_modules/`,
`__pycache__/`, `.pytest_cache/` are all correctly gitignored and do not
appear anywhere in `git status`. See Step 4 for the one real exception
(`backend/.venv`, a symlink, category 18 not 17).

#### Category 18 — Local development state

| Path | State | Concern | Recommendation |
|---|---|---|---|
| `backend/.venv` | ?? | A **symlink** to `/Users/cprakash/.venvs/lunaicad-backend` (machine-specific absolute path). `.gitignore` has `backend/.venv/` (trailing slash) which does NOT match a symlink in git's pattern matching — a real gitignore gap, not a false positive. | **Exclude from staging.** Recommend also fixing `.gitignore` to `backend/.venv` (no trailing slash) or `backend/.venv*` in a follow-up so this stops recurring — not done in this phase per the "do not modify" instruction, but flagged here since it will keep showing up as untracked on every future `git status` otherwise. |
| `backend/eval_reports/generation_regression_mock_*.json` (2), `semantic_benchmark_mock_*.json` (2) | ?? | Ad-hoc one-off local eval runs, not curated "baseline" snapshots | Recommend excluding from the commit (or moving to a gitignored scratch location) — not a functional risk either way, purely a repo-hygiene judgment call |

#### Category 19 — Suspicious / requires manual review

**None found.** No secrets, no credentials, no unexplained binary files, no
files with content inconsistent with their name/location. See Step 4 for
the full secret-scan methodology and results (clean).

#### Category 20 — Unclear ownership

| Path | Concern |
|---|---|
| `deploy/systemd/lunaicad-backend.service`, `lunaicad-worker.service` | Untracked despite being referenced as pre-existing in prior session summaries — see note under category 11. Low risk (content is almost certainly correct, per this session's own reference reads of these exact files), but worth the committer double-checking there isn't a divergent already-committed version being silently shadowed. |

---

## Step 4 — Files that must not be committed

**Methodology**: ran `bash scripts/check-secrets.sh` (pattern-based
live-credential scan over every would-be-committed file) and
`python3 scripts/check_secrets_baseline.py` (detect-secrets diff against the
audited `.secrets.baseline`) against the current tree. Both passed clean.
Additionally manually checked for: real `.env` files, SQLite DB files,
generated CAD artifacts, images/PDFs, hardcoded personal absolute paths,
`.next`/`node_modules`/`__pycache__`/coverage/log/IDE/OS-metadata leakage.

**Result: zero secrets, zero credentials found.** `backend/.env` and
`frontend/.env.local` (both contain real local config) are NOT part of this
diff — correctly excluded by `.gitignore`. `backend/cadmaker.db` (local
SQLite dev DB, 491 designs / 0 feedback rows) is also correctly gitignored
and does not appear in `git status`.

**The one file that must be excluded at staging time**: `backend/.venv`
(symlink, see category 18 above) — not a secret, but a machine-specific
local-environment artifact that slipped past `.gitignore`'s trailing-slash
pattern because it's a symlink, not a real directory.

**Recommended additional exclusion (hygiene, not a hard blocker)**: the 4
loose `eval_reports/*_mock_2026*.json` scratch files (category 18).

No actual secret was found in tracked content — the "mark the phase blocked"
condition does not apply.

---

## Step 5 — Migration review

All 5 migrations chain linearly to a single head (`f2a8b3a98437`), verified
via `alembic heads` (one head) and `alembic history` (unbroken chain from
`<base>` → `7fbf0c6446aa` → ... → `f2a8b3a98437`). `test_migrations.py`'s
`test_migration_matches_models_no_drift` passes (fresh `alembic upgrade
head` schema matches `Base.metadata` exactly) and its upgrade/downgrade
round-trip test passes.

| Revision | Parent | Creates/Alters | Downgrade present? | Safe for populated data? |
|---|---|---|---|---|
| `557ca72e7e96` add_calibration_profiles | `b1c4e7a92f38` | 2 new tables (`calibration_profiles`, `calibration_measurements`) | Yes (`drop_table` ×2) | **Yes** — new tables only, no existing-row risk |
| `7a0975b01cf8` add_jobs | `557ca72e7e96` | 1 new table (`jobs`) | Yes | **Yes** — new table |
| `3f2c9a7d1b44` add_design_versions | `7a0975b01cf8` | 1 new table (`design_versions`) | Yes | **Yes** — new table |
| `0db7d6dd7f9b` add_safety_acknowledgments_and_user_opt_in | `3f2c9a7d1b44` | 1 new table (`safety_acknowledgments`) + `users.data_improvement_opt_in` (NOT NULL boolean) | Yes | **Yes** — the new NOT NULL column on the EXISTING `users` table correctly uses `server_default=sa.false()`, backfills, then drops the default (a textbook-safe pattern, explicitly commented as such in the migration) |
| `f2a8b3a98437` add_bad_result_report_fields_to_feedback | `0db7d6dd7f9b` | `feedback` table gets 8 new columns, 2 of them (`is_bad_result_report`, `report_consent`) **NOT NULL with no `server_default`** | Yes | **NO — real bug.** On Postgres, `ALTER TABLE feedback ADD COLUMN is_bad_result_report BOOLEAN NOT NULL` (no default) fails outright if the `feedback` table has ANY existing rows. Locally this went unnoticed because (a) SQLite's batch-alter-table rebuild path tolerates it more permissively than Postgres, and (b) the local dev DB currently has 0 feedback rows, so the drift/round-trip tests never exercise a populated table. **This has not been executed against Postgres or a populated table in this or any prior session.** |

### Required follow-up for `f2a8b3a98437` (NOT fixed in this phase — migrations are off-limits per this phase's scope)

Recommend, in the stabilization phase: add `server_default=sa.false()` to
both `is_bad_result_report` and `report_consent` columns, then
`batch_op.alter_column(..., server_default=None)` after backfill — the exact
pattern already used correctly in `0db7d6dd7f9b` for
`data_improvement_opt_in`. This needs a NEW migration revision (never edit
an already-applied migration in place) if this migration has already run
anywhere; since it is still uncommitted and has never run against a real
deployment, editing this exact file in place before it's ever applied
anywhere is also a defensible option for whoever does the fix — that
decision belongs to stabilization, not this preservation phase.

**Index/constraint naming**: all 5 migrations consistently use
`batch_op.f(...)` for index names (the SQLAlchemy-recommended pattern for
safe naming under batch mode) — no naming-convention issues found.

**Multiple heads**: none — confirmed single head via `alembic heads`.

---

## Step 6 — Proposed commit sequence

10 logical commits, ordered by dependency. Migrations are kept WITH the
feature they back (not split into a separate "migrations" commit) since
each migration and its model/service code are one atomic unit of work that
would leave the tree in a broken state if split — this matches the task's
instruction not to split tightly-coupled code merely to reduce diff size.

| # | Title | Files | Prerequisites | Verify with | Risk | Rollback |
|---|---|---|---|---|---|---|
| 1 | **Evaluation harness + pre-existing text-to-CAD reliability tuning** | `backend/eval/**`, `.github/workflows/eval.yml`, `backend/eval_reports/baseline_phase4/5/6*` (curated only — exclude the 4 loose scratch files), `backend/tests/data/eval_broken.svg`, `backend/app/cad/standards/defaults.py`, `hex_standoff.py`, `plan/defaults.py`, `plan/deterministic.py`, `plan/dimension_report.py`, `plan/compiler.py`, `selectable_faces.py`, `understanding.py`, `manufacturability/checks.py`, `llm/mock_provider.py`, `test_mock_provider_word_boundaries.py`, `test_eval_harness_regressions.py` | None | `pytest tests/test_eval_harness_regressions.py tests/test_mock_provider_word_boundaries.py tests/test_templates.py` | Low — additive, pre-dates other work | Revert cleanly, no dependents |
| 2 | **Product-contract capability relabeling** | `backend/app/cad/families.py` (maturity rename), `README.md`, `docs/CAD_FAMILIES.md`, `docs/product-contract.md`, `backend/tests/test_capabilities_api.py`, `test_product_contract.py` | None | `pytest tests/test_capabilities_api.py tests/test_product_contract.py` | **Medium — wire-format change** (`capability_level` values rename); any external consumer of the old `beta`/`concept` values breaks | Coordinate with frontend consumers before merge; frontend already expects new values (see commit 8) |
| 3 | **Calibration system** | `backend/app/cad/calibration/**`, `backend/app/cad/templates/calibration_coupons.py`, `backend/app/cad/registry.py`, `backend/app/schemas/calibration.py`, `backend/app/services/calibration_service.py`, `backend/app/routers/calibration.py`, `backend/app/cad/object_intelligence/resolver.py`, migration `557ca72e7e96`, `docs/calibration.md`, `test_calibration.py` | 2 (uses updated family registry) | `alembic upgrade head` (from a clean test DB) + `pytest tests/test_calibration.py tests/test_migrations.py` | Low — additive, new tables only | Clean `alembic downgrade` |
| 4 | **Database-backed job queue + worker isolation** | `backend/app/worker/**`, `backend/app/services/job_service.py`, `backend/app/routers/jobs.py`, `backend/app/services/drawing_jobs.py` (refactored to shim), migration `7a0975b01cf8`, `docs/adr/0001-job-queue-database-backed.md`, `test_job_queue.py` | None (independent subsystem) | `pytest tests/test_job_queue.py` | **Medium — changes how `drawing_jobs.py` behaves**; verify sync-path (`sync=true`) callers still work | Clean `alembic downgrade`; code revert restores old in-process job registry |
| 5 | **Drawing-to-CAD routing changes** | `backend/app/services/drawing_to_spec.py`, `backend/app/schemas/drawing_spec.py`, `drawing_analysis.py`, `backend/app/routers/drawings.py` (drawing-routing portions only — safety-gate portions belong in commit 7), `test_drawing_to_cad.py`, `test_drawing_beta_acceptance.py`, `docs/drawing-to-cad-beta.md` | 4 (drawings.py now dispatches through the job queue) | `pytest tests/test_drawing_to_cad.py tests/test_drawing_beta_acceptance.py` | **Medium — contains the 1 known-failing test.** Commit it AS failing with a clear message referencing this inventory, do not silently skip/xfail it without discussion. | Revert restores prior drawing routing |
| 6 | **Studio UX: versions, GLB export, structured edits** | `backend/app/editing/**`, `backend/app/services/version_service.py`, `backend/app/export/glb.py`, migration `3f2c9a7d1b44`, `frontend/src/components/{TrustPanel,VersionHistory,EditDiff,ClarificationCard,ExportMenu}.{tsx,test.tsx}`, `frontend/src/app/studio/[id]/page.tsx`, `frontend/src/app/drawing/page.tsx`, `frontend/src/components/Header.tsx`, `frontend/src/lib/testFixtures.ts`, `frontend/vitest.setup.ts`, `vitest.config.ts`, `test_face_edit.py`, `test_localized_edit.py`, `test_selectable_faces.py`, `test_selection_phase6.py`, `test_design_versions.py`, `test_glb_export.py` | 4 (uses job queue for async ops) | `pytest tests/test_face_edit.py tests/test_localized_edit.py tests/test_design_versions.py tests/test_glb_export.py` + `cd frontend && npx vitest run` | Medium — large surface, but each piece independently tested | Clean `alembic downgrade`; frontend revert is independent of backend |
| 7 | **Production operations: observability, cost control, secrets/SBOM scanning** | `backend/app/metrics.py`, `observability.py`, `llm/circuit_breaker.py`, `pricing.py`, `openai_provider.py`, `ops/**`, `routers/ops.py`, `database.py`, `config.py`, `main.py`, `.env.example`, `requirements.txt` (the Phase-11 portion — prometheus_client, python-multipart bump), `.github/workflows/security.yml`, `.secrets.baseline`, `scripts/check-secrets.sh`, `scripts/check_secrets_baseline.py`, `scripts/generate_sbom.sh`, `sbom/**`, `docs/deployment.md`, `docs/ops/{observability,data-retention,deployment-runbook,backup-and-restore,incident-response,security-scanning}.md`, all Phase-11 test files (`test_llm_circuit_breaker.py`, `test_llm_pricing.py`, `test_generation_timing.py`, `test_quotas.py`, `test_retention_sweep.py`, `test_ops_endpoints.py`, `test_auth_abuse_metrics.py`, `test_production_startup_hardening.py`, `test_v037_production.py`, `test_phase2_*.py` updates) | None (foundational, independent of app features) | `pytest tests/test_ops_endpoints.py tests/test_llm_circuit_breaker.py tests/test_production_startup_hardening.py tests/test_phase2_authorization.py` + `./scripts/generate_sbom.sh` + `bash scripts/check-secrets.sh` | **High blast radius if wrong** (touches `main.py`, `config.py` startup validation) but thoroughly tested; verify `production_problems()` still fails loud on bad config | Revert restores pre-ops-hardening startup behavior |
| 8 | **Safety policy layer + capability-label frontend wiring** | `backend/app/safety/**`, `backend/app/schemas/api.py`, `llm/schemas.py`, `schemas/{complex_cad,design_spec,editing_spec}.py`, the safety-gate portions of `services/design_service.py` and `routers/designs.py` and `routers/drawings.py`, `frontend/src/lib/{api.ts,types.ts}` (safety + capability-label portions), `test_safety_policy.py`, `test_adversarial_injection_hardening.py` | 2, 4, 5 (needs updated capability labels + job queue + drawing routing already in place to gate all entry points) | `pytest tests/test_safety_policy.py tests/test_adversarial_injection_hardening.py` + `cd frontend && npx vitest run` | **High** — this is the security-relevant refusal layer; the false-positive regression already found and fixed this session (vehicle/aerospace decomposition prompts) should be called out explicitly in the commit message as a documented, tested edge case | Revert removes the refusal gate entirely — do not revert partially |
| 9 | **Privacy controls + legal document drafts** | `backend/app/services/account_service.py`, `routers/auth.py` (privacy portions), `auth/deps.py`, migration `0db7d6dd7f9b`, `frontend/src/app/settings/page.tsx`, `docs/legal/**`, `test_privacy_controls.py` | 8 (references safety/legal framing) | `pytest tests/test_privacy_controls.py` + `alembic upgrade head` | Medium — new user-facing account-deletion path, verify cascade-delete behavior against a real (non-empty) test dataset before shipping | Clean `alembic downgrade` |
| 10 | **Controlled-beta metrics + report-bad-result workflow + this audit's own ops fixes** | `backend/app/models.py` (Feedback extension portion), migration `f2a8b3a98437` (**flag the production-data-safety bug in the PR description**), `backend/app/services/design_service.py` (report_bad_result portion), `routers/designs.py` (report-bad-result route), `schemas/api.py` (ReportBadResult* portion), `llm/prompt_version.py`, `metrics.py` (feedback/edit/print/fit counters — additive to commit 7's file), `frontend/src/components/TrustPanel.{tsx,test.tsx}` (report-a-problem portion), `frontend/src/lib/{api.ts,types.ts}` (ReportBadResult portion), `test_feedback.py`, `deploy/prometheus/alerts.yml`, `deploy/systemd/lunaicad-{backup,retention}.{service,timer}`, `scripts/backup.sh`, `docs/ops/beta-metrics.md`, updates to `docs/deployment.md`/`backup-and-restore.md`/`data-retention.md`/`observability.md`, `requirements.txt` (python-jose bump + version pins — the Phase-13 portion), `sbom/**` (regenerated), `docs/release-readiness-report.md`, `docs/ownership_export_hardening` tests if applicable | 7, 9 | `pytest tests/test_feedback.py tests/test_auth.py tests/test_phase2_authorization.py` + `bash -n scripts/backup.sh` + YAML-validate `alerts.yml` | Medium — the migration bug (see Step 5) means this commit should NOT be deployed to a database with real feedback rows until that's fixed | **Do not deploy `f2a8b3a98437` against a populated `feedback` table until the server_default fix lands** |

**Note on splitting `design_service.py`, `routers/designs.py`,
`schemas/api.py`, `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`,
and `metrics.py`**: these 6 files accumulated changes from 4-5 different
phases each (they are genuine "hub" files every feature touches). Git
cannot cleanly split a single file's diff across commits without
`git add -p` (interactive hunk staging). This is flagged explicitly rather
than silently — whoever executes this commit plan should use `git add -p`
on these specific files, staging only the hunks relevant to each commit's
theme, verified by re-running that commit's test list before moving to the
next.

**Commits intentionally kept small and independent**: 1, 2, 3, 4 have no
inter-dependencies on 6-10 and could be committed (and even reviewed) in
parallel by different reviewers if useful.

**Fewer than 12**: 10 commits, as required.
