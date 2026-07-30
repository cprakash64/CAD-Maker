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

### Baseline scan (run 2026-07-29)

`pip-audit -r requirements.txt` found **25 known vulnerabilities across 6
packages**. Triaged here, not silently ignored:

| Package | Fixed in | Action taken |
|---|---|---|
| `python-multipart` 0.0.20 | 0.0.22–0.0.31 (6 CVEs) | **Fixed** — bumped to 0.0.32 in this change. This library parses untrusted file-upload bodies directly (`app.routers.drawings`), so these CVEs sit on the actual attack surface; verified via the full test suite (including the hostile-filename fuzz tests in `tests/test_phase2_upload_limits_and_data.py`) that the bump doesn't change behavior in a way the app doesn't already handle safely. |
| `python-jose` 3.3.0 | 3.4.0 (algorithm-confusion CVEs) | **Tracked, not yet bumped.** This is the JWT library (`app.auth.security`) — a live auth-behavior change deserves its own reviewed change with dedicated regression testing beyond this phase's scope, not a drive-by bump. Tracked as a follow-up; see `docs/ops/deployment-runbook.md`'s dependency-update cadence. |
| `starlette` 0.41.3 (via `fastapi`) | several point releases | **Tracked.** Transitive via the pinned FastAPI version; bumping requires bumping FastAPI together and re-running the full suite. Follow-up. |
| `ecdsa` 0.19.2 (via `python-jose`) | none (upstream) | **Accepted, documented.** The maintainer's stance is that the underlying timing-side-channel class of issue is inherent to pure-Python ECDSA and won't be patched; this is a known, widely-accepted risk for `python-jose` users. Resolves once `python-jose` is replaced or ships a fix upstream. |
| `vtk` 9.3.1 (via `cadquery`) | 9.5.1 | **Tracked.** Transitive via `cadquery`'s own pin; not independently controllable without pinning around `cadquery`'s dependency resolution, which risks breaking CAD generation. Revisit when `cadquery` bumps its own `vtk` pin. |
| `pytest` 8.3.4 | 9.0.3 | **Tracked, low priority.** Dev/test-only dependency, never shipped to production; not on the runtime attack surface. |

`npm audit` (frontend): run as part of the same CI job; see the workflow run
for the current-at-any-time result (frontend deps churn faster than this
document would stay accurate).

### CI gate

`.github/workflows/security.yml`'s `pip-audit`/`npm audit` steps are
**report-only today** (they don't fail the build) — given the triage above
shows several genuinely un-fixable-right-now transitive findings, a hard
fail-on-any-finding gate would be permanently red and trained to be ignored,
which defeats the purpose. The gate that DOES fail the build is
`check_secrets_baseline.py` (below) — a new potential secret has no
legitimate reason to be "tracked as a known issue," unlike a transitive CVE
with no available fix.

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

### Baseline scan (run 2026-07-29)

15 findings across 9 files, all reviewed and confirmed non-secrets:

| File | Finding type | Why it's safe |
|---|---|---|
| `app/config.py` | Secret Keyword | `_DEFAULT_JWT_SECRET = "dev-insecure-secret-change-me"` — the documented, intentionally-insecure dev placeholder; production refuses to boot with it (`Settings.production_problems`). |
| `app/observability.py` | Basic Auth Credentials | A **regex literal** (`r"://[^/\s:@]+:[^/\s:@]+@"`) matching the shape of `user:pass@host` for redaction purposes — the pattern itself looks like a credential to the scanner, but it's code, not data. |
| `alembic/versions/...b1c4e7a92f38...py` | Hex High Entropy String | An Alembic revision id (`"b1c4e7a92f38"`) — a deterministic hash-like identifier, not a secret. |
| `tests/test_phase2_authorization.py` | JSON Web Token | A deliberately-malformed `alg=none` JWT fixture, used to test that FORGED tokens are rejected. |
| `tests/test_phase2_upload_limits_and_data.py` | Secret Keyword / Base64 High Entropy String | Sentinel test values (`JWT-SENTINEL-...`, `sk-SENTINEL-key`) and a deliberately-wrong login password used in a rate-limit test. |
| `tests/test_auth.py`, `tests/test_observability.py`, `tests/test_rate_limit.py`, `tests/test_v037_production.py` | Secret Keyword | Test fixtures (`"password123"`, `"a" * 40` as a fake JWT secret, etc.) — dummy values, never real credentials. |

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
