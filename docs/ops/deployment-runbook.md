# Deployment runbook

Companion to `docs/deployment.md` (which covers the initial VPS setup,
systemd units, nginx, sizing). This covers the DAY-2 operations: validating a
config before it goes live, migrations, rollback, secret rotation,
dependency updates, and worker restarts.

## Production environment validation

**Automatic, at process boot**: `settings.validate_startup()`
(`app.config.Settings`) runs at import time in `app/main.py` — the FastAPI
app fails to construct at all if `APP_ENV` is `staging`/`production` (and
`TESTING` isn't set) and any of these are true:

- `LLM_PROVIDER=mock` (production requires `openai` + a real key)
- `JWT_SECRET` unset, still the dev default, or under 32 chars
- `DATABASE_URL` not explicitly set in the environment
- `STORAGE_BACKEND=s3` without `S3_BUCKET`, or an unknown backend value
- `CORS_ORIGINS` unset, containing `localhost`/`127.0.0.1`, or containing `*`
- `PUBLIC_BASE_URL` pointing at localhost
- `DEV_MODE=true`
- `OPS_API_TOKEN` unset or under 16 chars (gates `/metrics` and detailed `/ready`)
- `DB_POOL_SIZE < 1`, `DB_MAX_OVERFLOW < 0`, or `DB_POOL_TIMEOUT_SECONDS < 1`

This means "deploy with a bad config" fails LOUD and IMMEDIATE (the service
won't start, `systemctl status` shows the failure, the error message lists
EVERY problem found, not just the first) rather than booting into a silently
unsafe state. Verify this yourself before a real cutover:

```bash
# Dry-run the config check without actually starting the server:
cd /opt/lunaicad/backend
APP_ENV=production TESTING=false .venv/bin/python -c "
from app.config import settings
problems = settings.production_problems()
if problems:
    print('UNSAFE:'); [print(f'  - {p}') for p in problems]
    raise SystemExit(1)
print('Config OK for production.')
"
```

**Manual, before first launch (or after infra changes)**:

- [ ] `alembic upgrade head` runs clean against the target DB (see Migration
      procedure below).
- [ ] `curl -m 5 https://your-domain.example/ready` returns 200 with both
      checks `ok: true`.
- [ ] `curl -H "Authorization: Bearer $OPS_API_TOKEN" https://.../metrics`
      returns Prometheus text (confirms the token is actually set and
      matches).
- [ ] A restore drill has been run against a copy of the ACTUAL production
      database (not just the demo-sized drill in
      `docs/ops/backup-and-restore.md`) — restore time scales with data
      volume; know your real RTO before you need it under pressure.
- [ ] `deploy/smoke_test.py` (see "Deployment smoke tests" below) passes
      against the live deployment.

## Migration procedure

```bash
# 1. Always back up first (docs/ops/backup-and-restore.md) -- a migration
#    that goes wrong on production data is exactly what the backup is for.
/opt/lunaicad/scripts/backup.sh

# 2. Review what's about to run.
cd /opt/lunaicad/backend
.venv/bin/alembic history --indicate-current
.venv/bin/alembic upgrade head --sql > /tmp/migration_preview.sql   # dry-run: prints SQL, doesn't execute
less /tmp/migration_preview.sql

# 3. Apply, with the API stopped (avoids a request hitting a half-migrated
#    schema mid-transaction on a multi-statement migration).
sudo systemctl stop lunaicad-backend lunaicad-worker
.venv/bin/alembic upgrade head
sudo systemctl start lunaicad-backend lunaicad-worker
curl -m 5 https://your-domain.example/ready
```

`app.database.init_db()` is a deliberate no-op in production (see its
docstring) — the schema is ALWAYS Alembic's responsibility there, so a
missed migration fails loudly (missing-table errors surfaced through
`/ready`'s DB check and the `SQLAlchemyError` handler) rather than silently
drifting from migration history.

`tests/test_migrations.py::test_migration_matches_models_no_drift` runs in
CI on every push — it fails if a model change wasn't captured in a migration
(compares a fresh `alembic upgrade head` schema against `Base.metadata`
directly), so drift is caught before it ever reaches a deploy.

## Rollback procedure

Two independent things can need rolling back — code and schema — and they
don't always move together:

**Code rollback** (schema unaffected, or the new schema is backward
compatible with old code — the common case for additive migrations):

```bash
sudo systemctl stop lunaicad-backend lunaicad-worker
cd /opt/lunaicad/src && git checkout <previous-tag-or-sha>
cd backend && .venv/bin/pip install -r requirements.txt   # deps may have changed
sudo systemctl start lunaicad-backend lunaicad-worker
curl -m 5 https://your-domain.example/ready
```

**Schema rollback** (only when the new migration is genuinely incompatible
with old code — prefer forward-fixing over this when possible; a `downgrade`
that drops a column is itself a destructive, hard-to-reverse action):

```bash
sudo systemctl stop lunaicad-backend lunaicad-worker
cd /opt/lunaicad/backend
.venv/bin/alembic downgrade -1        # one revision back; every migration in
                                        # alembic/versions/ implements downgrade()
# then roll back code to match, as above
sudo systemctl start lunaicad-backend lunaicad-worker
```

If a migration has already run against data in a way `downgrade()` can't
cleanly reverse (e.g. a column was dropped and its data is gone), the real
rollback is **restore from the pre-migration backup**
(`docs/ops/backup-and-restore.md`) — this is exactly why step 1 of the
migration procedure is "always back up first."

## Secret rotation

| Secret | Rotation procedure | Blast radius if delayed |
|---|---|---|
| `JWT_SECRET` | Set the new value, restart `lunaicad-backend`. **Every existing session is invalidated immediately** (tokens are signed with the old secret) — users must log in again. Not zero-downtime; schedule it. | Old secret leaked → anyone can forge auth tokens. |
| `OPENAI_API_KEY` | Rotate in the OpenAI dashboard, update `.env` and `.env.worker` (both need it — see `docs/deployment.md`'s secret-scoping table), restart both services. Old key can be revoked immediately after (no session-continuity concern, unlike JWT). | Leaked key → attacker spends your OpenAI budget; the cost circuit breaker (`docs/ops/observability.md`) limits blast radius but doesn't eliminate it. |
| `OPS_API_TOKEN` | Set new value, restart. Update whatever's polling `/metrics` (monitoring config) in the same change — this one DOES break monitoring silently if you forget the second half. | Leaked token → cost/queue/business data readable externally. |
| `TELEMETRY_HASH_SECRET` | Set new value, restart. Pseudonymized user ids in NEW logs won't correlate with OLD logs anymore (expected — that's what rotation means for a pseudonymization secret). | Low urgency; low sensitivity if leaked (it protects log correlation, not access). |
| `S3_SECRET_ACCESS_KEY` | Rotate in the provider console, update both env files, restart both services. Old key can be revoked once confirmed working. | Leaked key → attacker can read/write/delete exported files and uploaded drawings. |
| DB password (`DATABASE_URL`) | Rotate in Postgres (`ALTER USER ... PASSWORD ...`), update `.env`/`.env.worker`, restart both services. Do this with the OLD password still valid until confirmed working, then revoke. | Leaked password → full DB read/write (the worst case — this is why it's the last one to revoke, not the first). |

General rule: **update the env file(s), restart the affected service(s),
verify (`/ready`, a manual login, a manual generation), THEN revoke the old
credential at the provider.** Revoking first and finding out the update
didn't take turns a rotation into an outage.

## Dependency updates

```bash
# Backend
cd backend
.venv/bin/pip list --outdated
.venv/bin/pip-audit -r requirements.txt   # see docs/ops/security-scanning.md
# bump versions in requirements.txt, then:
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q            # full suite must pass before deploying

# Frontend
cd frontend
npm outdated
npm audit
npm update <package>   # or edit package.json directly for a major bump
npm run typecheck && npm test && npm run build
```

Cadence: `pip-audit`/`npm audit` run in CI on every push
(`docs/ops/security-scanning.md`) — treat a NEW finding there as the trigger
to update, rather than waiting for a calendar cadence. For everything else
(non-security version bumps), monthly is a reasonable baseline; pin major
version bumps to a dedicated PR reviewed on its own (not bundled with
feature work), since a major bump is the one category of "dependency update"
genuinely likely to change behavior.

## Worker restart

```bash
# Graceful (lets in-flight jobs finish; claims no new ones once stopping):
sudo systemctl stop lunaicad-worker
sudo systemctl start lunaicad-worker

# Or just:
sudo systemctl restart lunaicad-worker    # systemd stop-then-start; same effect
```

A job that was mid-flight when the worker stops uncleanly (killed, VPS
reboot, OOM of the whole box) is recovered automatically — `reap_stale_jobs`
runs on the NEXT supervisor startup (and periodically while running),
finding any `running`/`validating`/`exporting` job whose heartbeat has gone
stale (`JOB_STALE_HEARTBEAT_SECONDS`, default 90s) and requeuing or failing
it per the normal retry policy. **No manual intervention needed for a worker
restart under normal circumstances** — this is precisely what the
stale-job-reaping design exists for (`docs/adr/0001-job-queue-database-backed.md`).

`Restart=always` in `lunaicad-worker.service` also means the supervisor's OWN
deliberate exit (after `JOB_WORKER_MAX_JOBS_BEFORE_RECYCLE` jobs, bounding
slow native-library memory growth) results in a fresh process automatically
— "restart" isn't only a manual/incident action, it's the steady-state
behavior.

## Deployment smoke tests

`deploy/smoke_test.py` (repo root — see the script itself for the full list)
exercises the deployed instance end to end: signup → login → create a design
→ poll to completion → download STL → check `/ready` and `/metrics`. Run
against a fresh deploy before considering it live:

```bash
python deploy/smoke_test.py --base-url https://your-domain.example
```

Exits non-zero with a clear message on the first failing step — safe to wire
into a post-deploy CI/CD gate later; today it's a manual post-deploy check.

**Note**: each run signs up a fresh throwaway account (random email) and
creates one real design. There's no self-service account deletion yet
(`docs/ops/data-retention.md`), so running this repeatedly against
production accumulates small test accounts/designs over time — harmless
(bounded, low-volume) but worth knowing. Prefer running it against a staging
environment for routine checks; reserve production runs for actual
post-deploy verification, and periodically clean up `smoketest-*@example.com`
accounts via a direct DB query if it matters for your account-count metrics.
