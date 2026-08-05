# Dependency scanning, secret scanning, and SBOM

## Dependency scanning (`pip-audit`, `npm audit`)

Runs in CI on every push (`.github/workflows/security.yml`) and should be run
locally before a dependency bump too:

```bash
# Backend
cd backend && .venv/bin/pip install pip-audit && .venv/bin/pip-audit -r requirements.txt

# Frontend
cd frontend && npm audit
```

### Baseline scan (run 2026-07-29, superseded 2026-07-31)

Historical only — see `docs/security/dependency-risk-assessment.md` for the
current, authoritative state. Summary of what changed in the 2026-07-31
remediation pass: `python-jose` bumped to 3.5.0 (closing the pyasn1 CVEs as
a side effect), `starlette` fixed via a coupled `fastapi` bump to 0.134.0,
`pytest` bumped to 9.0.3, and `vtk`/`ecdsa` remain accepted risks but now
with concrete import-tracing reachability evidence instead of just "tracked."

### CI gate

`.github/workflows/security.yml`'s `pip-audit`/`npm audit` steps are a
**hard gate as of 2026-07-31**: `scripts/check_pip_audit_allowlist.py` and
`scripts/check_npm_audit_allowlist.py` fail the build on any finding that
isn't already in the reviewed allowlist (`backend/.pip-audit-allowlist.json`,
`frontend/.npm-audit-allowlist.json` — see
`docs/security/dependency-risk-assessment.md`'s "Accepted-risk register" for
what's in each and why). A brand-new finding for an unreviewed package fails
CI immediately, the same way an unreviewed secret does below — either fix it
or add a reviewed allowlist entry with a written reachability justification,
owner, review date, and trigger condition.

## Updating dependencies

1. Change the pin in `backend/requirements.txt` (backend) or
   `frontend/package.json` (frontend).
2. Backend only: regenerate the hash-pinned lock file —
   `cd backend && .venv/bin/pip-compile --generate-hashes --output-file=requirements.lock.txt requirements.txt`
   — and commit it alongside the `requirements.txt` change. CI's
   `hash-locked-install` job (`.github/workflows/security.yml`) will fail if
   the lock file doesn't match what `requirements.txt` resolves to.
3. Run `pip-audit -r backend/requirements.txt` / `npm audit` (frontend) and
   confirm no new findings, or add a reviewed allowlist entry per the CI-gate
   section above.
4. Run the full backend suite and the frontend suite/typecheck/build.
5. If the package is one you don't directly control the version-compatibility
   ceiling of (e.g. Starlette via FastAPI's own pin), check whether the
   *parent* package needs bumping too before the target version is even
   installable — see `docs/security/dependency-risk-assessment.md`'s
   Starlette entry for a worked example (verifying compatible FastAPI/
   Starlette/Pydantic ranges via PyPI release metadata before committing to a
   version).
6. Dependabot (`.github/dependabot.yml`) opens weekly PRs for `pip`, `npm`,
   and `github-actions` bumps automatically — it does NOT regenerate
   `requirements.lock.txt` (pip-compile isn't Dependabot-aware), so step 2
   above must still be done by hand when merging a Dependabot PR that
   touches `backend/requirements.txt`.

## Secret scanning

Two complementary tools — deliberately kept both rather than replacing one
with the other:

| | `scripts/check-secrets.sh` (pre-existing) | `detect-secrets` + `scripts/check_secrets_baseline.py` (this phase) |
|---|---|---|
| Purpose | Fast, dependency-free pre-commit guard | Broad, plugin-based CI gate with an audited baseline |
| Coverage | A handful of hand-written regexes (OpenAI/Anthropic keys, AWS key ids, PEM private-key blocks) + confirms `.env`/`.env.local` are gitignored | ~25 detector plugins (JWTs, high-entropy strings, cloud-provider tokens, Basic Auth, etc.) — see `.secrets.baseline`'s `plugins_used` |
| When to run | Locally, before every commit (cheap, no install) | CI, every push/PR; locally before a dependency/config change likely to introduce false positives worth re-auditing |
| False positives | Rare (narrow patterns) | Expected and handled via the audited baseline (see below) |

```bash
# Fast local guard (run before every commit):
bash scripts/check-secrets.sh

# Broad CI gate:
pip install detect-secrets
python scripts/check_secrets_baseline.py
```

`.secrets.baseline` (repo root) is the audited baseline — every finding in it
has been reviewed and marked `is_secret: false` (or `true`, if a real one is
ever found and needs tracking through remediation). The CI check
(`scripts/check_secrets_baseline.py`) re-scans and fails ONLY on findings NOT
already in the baseline — existing (already-reviewed) entries never fail the
build, so the gate stays meaningful instead of permanently red.

### Baseline scan (run 2026-07-29, re-audited 2026-07-31)

Original: 15 findings across 9 files. Re-audited 2026-07-31 during the
dependency-remediation pass (`docs/security/dependency-risk-assessment.md`)
because line-number drift from intervening commits made several
already-reviewed findings look "new" to the line-keyed baseline; regenerated
via `detect-secrets scan ... --baseline .secrets.baseline` and manually
re-reviewed every new-looking entry against its actual source line (not
rubber-stamped) before marking `is_secret: false`. Now 33 findings across 18
files, all reviewed and confirmed non-secrets — the 18 net-new-looking ones
are the same categories as below (Alembic revision ids, canary/dummy test
credentials, doc examples of ephemeral drill-database passwords, and one
self-referential doc line describing this very table):

| File | Finding type | Why it's safe |
|---|---|---|
| `app/config.py` | Secret Keyword | `_DEFAULT_JWT_SECRET = "dev-insecure-secret-change-me"` — the documented, intentionally-insecure dev placeholder; production refuses to boot with it (`Settings.production_problems`). |
| `app/observability.py` | Basic Auth Credentials | A **regex literal** (`r"://[^/\s:@]+:[^/\s:@]+@"`) matching the shape of `user:pass@host` for redaction purposes — the pattern itself looks like a credential to the scanner, but it's code, not data. |
| `alembic/versions/...b1c4e7a92f38...py`, and 3 more (`7a0975b01cf8`, `3f2c9a7d1b44`, `f2a8b3a98437`) | Hex High Entropy String | Alembic revision ids — deterministic hash-like identifiers, not secrets. |
| `tests/test_phase2_authorization.py` | JSON Web Token | A deliberately-malformed `alg=none` JWT fixture, used to test that FORGED tokens are rejected. |
| `tests/test_phase2_upload_limits_and_data.py` | Secret Keyword / Base64 High Entropy String | Sentinel test values (`JWT-SENTINEL-...`, `sk-SENTINEL-key`) and a deliberately-wrong login password used in a rate-limit test. |
| `tests/test_auth.py`, `tests/test_observability.py`, `tests/test_rate_limit.py`, `tests/test_v037_production.py`, `tests/test_adversarial_injection_hardening.py`, `tests/test_privacy_controls.py`, `tests/test_production_startup_hardening.py` | Secret Keyword / Basic Auth Credentials | Test fixtures (`"password123"`, `"wrong-password"`, `"a" * 40` as a fake JWT secret, `sk-live-not-a-real-key-but-present`, `postgresql://user:pass@host/db`, etc.) — dummy/canary values, never real credentials. |
| `deploy/smoke_test.py` | Secret Keyword | `"smoke-test-password-123"` — a hardcoded password for the smoke-test's own throwaway test account, not a production credential. |
| `docs/ops/backup-and-restore.md`, `docs/release-readiness-report.md` | Basic Auth Credentials | Example `DATABASE_URL`/`CADMAKER_TEST_PG_URL` connection strings for ephemeral local Docker drill/test Postgres containers (`drillpass`, `testpass`) documented as reproducible commands, not live credentials. |
| `docs/ops/observability.md` | Basic Auth Credentials | Prose describing the shape of a connection-string secret to redact, not an actual one. |
| `docs/ops/security-scanning.md` (this file) | Secret Keyword / Hex High Entropy String | Self-referential — this table's own text quotes the `app/config.py` placeholder and an Alembic revision id as examples, which the scanner also flags where they're quoted here. |

**Known limitation**: `detect-secrets scan <directory>` only considers
git-tracked files (confirmed empirically — an untracked new file in a scanned
directory is silently skipped until `git add`ed). This is actually the right
behavior for the CI use case (a push/PR's changed files ARE tracked by the
time CI runs), but means running the check locally against a dirty working
tree with brand-new, not-yet-`git add`ed files won't catch secrets in THOSE
files until they're staged. `git add` before running the local check if
you've just created a file you want covered.

### Updating the baseline

```bash
detect-secrets scan backend/app backend/tests backend/alembic backend/deploy \
    backend/requirements.txt backend/alembic.ini backend/pytest.ini \
    frontend/src docs deploy .github \
    --baseline .secrets.baseline > /tmp/new_baseline.json
mv /tmp/new_baseline.json .secrets.baseline
detect-secrets audit .secrets.baseline   # interactively label any new findings
git diff .secrets.baseline               # review before committing
```

## SBOM (CycloneDX)

```bash
./scripts/generate_sbom.sh
```

Generates `sbom/backend-sbom.cdx.json` (from the actual installed
environment via `cyclonedx-py environment` — transitive dependencies
included, not just `requirements.txt`'s direct pins) and
`sbom/frontend-sbom.cdx.json` (via npm's built-in `npm sbom --sbom-format
cyclonedx`, npm 9.5+).

Current snapshot (generated 2026-07-29, committed under `sbom/`):

| | Components |
|---|---|
| Backend | 92 |
| Frontend | 299 |

Regenerated in CI on every push (`.github/workflows/security.yml`) and
uploaded as a build artifact; the committed copy under `sbom/` is a
point-in-time reference, not guaranteed to be perfectly current between
dependency bumps — regenerate before relying on it for an audit.
