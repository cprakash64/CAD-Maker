# Deployment

> **Production operations**: this document covers initial setup. For day-2
> operations (migrations, rollback, secret rotation, dependency updates,
> restore drills, incident playbooks, observability), see `docs/ops/`:
> - `docs/ops/deployment-runbook.md` — env validation, migrations, rollback, secrets, deps, worker restart
> - `docs/ops/observability.md` — telemetry schema, metrics catalog, redaction policy
> - `docs/ops/data-retention.md` — prompt/artifact retention policy
> - `docs/ops/backup-and-restore.md` — backup schedule + an EXECUTED restore drill with evidence
> - `docs/ops/incident-response.md` — OpenAI outage, DB down, worker crash loop, cost spike, queue saturation, auth abuse
> - `docs/ops/security-scanning.md` — dependency/secret scanning, SBOM

LunaiCAD's backend is **two independent OS processes** sharing one database:

1. **`lunaicad-backend`** — the FastAPI app (`uvicorn app.main:app`). Handles
   HTTP requests, auth, validation, and job **submission**. It never runs
   CAD geometry code itself once a route is wired to the queue (see
   `docs/adr/0001-job-queue-database-backed.md`).
2. **`lunaicad-worker`** — `python -m app.worker`. Claims queued jobs and
   runs each one (drawing parsing, CadPlan compilation, OpenCascade/CadQuery
   operations, meshing, validation, export) in its **own isolated OS
   subprocess**, so a hang or a kernel crash can never take the API down.

Both talk to the same database (the queue itself — no Redis) and the same
file storage. On a single Hostinger VPS this means: two systemd services
instead of one, nothing else new to install.

The frontend (Next.js) is a third, separate process, unchanged by this
document beyond the reverse-proxy config below.

## Local development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # dev defaults: SQLite, mock LLM provider
alembic upgrade head           # or rely on init_db()'s create_all in dev/test

# Terminal 1: the API
uvicorn app.main:app --reload --port 8000

# Terminal 2: the worker
python -m app.worker
```

Notes:

- **You do not strictly need Terminal 2 for interactive dev/manual testing**
  of most flows — with `TESTING=true` (or when the test harness sets it),
  `POST /api/designs/create` and the drawing endpoints run their job inline,
  in-process, via `app.worker.runner.run_inline` (see the ADR's "Test-mode
  inline execution" section) precisely because no separate worker is
  running. This is a `settings.testing`-gated path only — it refuses to run
  outside tests (`run_inline` raises if `not settings.testing`), so a real
  local dev session (`TESTING` unset) **does** need the worker process
  running, exactly like production.
- `pytest` never needs the worker process either, for the same reason.
- Watch `python -m app.worker`'s stdout for `job_claimed` / `job_succeeded` /
  `job_failed_terminal` / `worker_job_crashed` events (structured JSON via
  `app.observability.log_event`) while developing.

## Hostinger VPS deployment

### Prerequisites

- Python 3.11+, a Postgres instance (local or managed — `DATABASE_URL`
  points at it), Node.js for the frontend build, nginx.
- A dedicated, unprivileged system user (`lunaicad` below) that owns
  `/opt/lunaicad` and nothing else on the box.

### Layout

```text
/opt/lunaicad/
  backend/
    .venv/
    .env            # full backend env (see "Secret scoping" below)
    .env.worker      # MINIMAL worker env (see "Secret scoping" below)
    storage_data/    # STORAGE_BACKEND=local exports (or use S3 -- see .env.example)
    job_tmp/          # per-job scratch dirs; the worker owns this entirely
  frontend/
```

```bash
sudo useradd -r -m -d /opt/lunaicad -s /usr/sbin/nologin lunaicad
sudo -u lunaicad git clone <repo> /opt/lunaicad/src   # or rsync a release tarball
cd /opt/lunaicad/src/backend
sudo -u lunaicad python3 -m venv /opt/lunaicad/backend/.venv
sudo -u lunaicad /opt/lunaicad/backend/.venv/bin/pip install -r requirements.txt
```

### Migrations

Run once per deploy, BEFORE starting/restarting either service (both
processes assume the schema matches their code):

```bash
cd /opt/lunaicad/backend
sudo -u lunaicad .venv/bin/alembic upgrade head
```

`app.database.init_db()` is a no-op in `app_env=production` — the schema is
Alembic's responsibility there, deliberately (see its docstring), so a
missed migration fails loudly (missing-table errors) rather than silently
diverging from migration history.

### Cost control (docs/operations/cost-control-architecture.md)

`COST_*` env vars in `.env.example` set the beta-default quota policy (all
have safe built-in defaults — nothing needs to be set to boot). Per-account
overrides and the global emergency stop are DB state, managed via the
`/api/admin/cost/*` endpoints — there is no env var for "this one account's
limit."

There is no self-service path to `User.is_admin` (by design — see
`app.auth.deps.get_current_admin_user`'s docstring). Grant the first admin
directly:

```sql
UPDATE users SET is_admin = true WHERE email = 'you@example.com';
```

### Secret scoping ("no production secrets unless absolutely necessary")

Two env files, not one — the worker gets a **strict subset**:

| Variable | `backend/.env` (API) | `backend/.env.worker` (worker) |
|---|---|---|
| `DATABASE_URL` | yes | yes — needed to claim/update jobs |
| `STORAGE_*` (backend + creds) | yes | yes — needed to read uploads / write exports |
| `OPENAI_API_KEY` / `LLM_PROVIDER` / `CAD_LLM_*` | yes | yes — CAD generation needs it |
| `JWT_SECRET` | yes | **no** — the worker never authenticates anyone |
| `CORS_ORIGINS`, `PUBLIC_BASE_URL` | yes | **no** — HTTP-layer only |
| `RATE_LIMIT_*` | yes | **no** — enforced at the API layer only |
| `JOB_*` (queue tuning) | optional | yes — this is what it reads |

Keep `.env.worker` literally minimal — copy only the rows above, not the
whole `.env`. This is enforced by convention (the systemd unit points at a
separate file), not by code — review `.env.worker`'s contents at each deploy
if secrets are added to `.env` for unrelated reasons.

### systemd units

Install the unit files from `deploy/systemd/` — two long-running services plus
two nightly timer-triggered oneshots (backup, retention sweep; see
`docs/ops/backup-and-restore.md` / `docs/ops/data-retention.md`):

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lunaicad-backend lunaicad-worker
sudo systemctl enable --now lunaicad-backup.timer lunaicad-retention.timer
sudo systemctl status lunaicad-backend lunaicad-worker --no-pager
sudo systemctl list-timers lunaicad-backup.timer lunaicad-retention.timer
```

`lunaicad-backup.service`/`lunaicad-retention.service` are `Type=oneshot` —
they run to completion once per timer firing, not continuously; do NOT
`enable --now` the `.service` files directly (that would just run them once
immediately with no future schedule) — enable the `.timer` units instead, as
above. `scripts/backup.sh` (the actual backup logic) requires `BACKUP_REMOTE`
to be set in `backend/.env` for the dump to be copied off-box; without it the
service still runs (and says so loudly in its logs) but the backup only
exists on the same box as the database it protects.

`lunaicad-worker.service` (see the file for the full annotated version):
`Restart=always` so systemd brings up a fresh supervisor both after a crash
AND after the supervisor's own deliberate exit once it hits
`JOB_WORKER_MAX_JOBS_BEFORE_RECYCLE` — this is the "process replacement
after crashes or configured job count" requirement, implemented with a
standard, boring systemd primitive rather than hand-rolled self-restart
logic.

### nginx (reverse proxy)

```nginx
upstream lunaicad_backend { server 127.0.0.1:8010; }
upstream lunaicad_frontend { server 127.0.0.1:3010; }

server {
    listen 443 ssl;
    server_name your-domain.example;

    location /api/ {
        proxy_pass http://lunaicad_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;  # TRUST_PROXY_HEADERS=true
        proxy_read_timeout 60s;  # job submission responses are bounded by
                                 # JOB_SYNC_WAIT_SECONDS (default 25s) + margin
    }

    location / {
        proxy_pass http://lunaicad_frontend;
        proxy_set_header Host $host;
    }
}
```

The worker is **never** exposed through nginx — it has no HTTP server at
all, only a DB connection.

### Health checks

```bash
curl -m 5 http://127.0.0.1:8010/health   # liveness -- is the process up at all
curl -m 5 http://127.0.0.1:8010/ready    # readiness -- DB + storage actually reachable
sudo systemctl is-active lunaicad-worker
```

Point your load balancer / uptime monitor at `/ready`, not `/health` — see
`docs/ops/observability.md`'s "Health vs readiness" for why. Queue depth and
every other operational metric are on `/metrics` (Prometheus text format,
`docs/ops/observability.md`), gated by `OPS_API_TOKEN`.

A stuck/crash-looping worker is visible via
`sudo journalctl -u lunaicad-worker -f` (structured `log_event` JSON) and via
`worker_recycling` / `worker_supervisor_crashed` log lines.

### Sizing

`JOB_WORKER_CONCURRENCY` (default 2) should roughly match the VPS's CPU core
count minus headroom for the API process and Postgres — CAD compilation is
CPU-bound. `JOB_MEMORY_LIMIT_MB` (default 1536) × `JOB_WORKER_CONCURRENCY`
should stay comfortably under the VPS's total RAM (leave room for Postgres,
the API process, and the OS). On a small Hostinger VPS (e.g. 2 vCPU / 4GB),
`JOB_WORKER_CONCURRENCY=2` with the default memory limit is a reasonable
starting point; watch `worker_job_crashed` with `exitcode=-9` (OOM-killed)
in the logs and lower concurrency or raise the VPS's RAM if that recurs.

### Storage and scratch space

- `STORAGE_DIR` (local backend) and `JOB_TMP_ROOT` must be on the same
  filesystem the worker process can write to — both API and worker read the
  former; only the worker writes the latter, and always cleans it up (see
  the ADR and `app/worker/supervisor.py::_finalize`).
- `job_tmp/` should be EMPTY between jobs in steady state — if it
  accumulates orphaned per-job directories, that indicates either the
  worker is being SIGKILLed externally without going through
  `lunaicad-worker.service`'s normal stop path, or a bug; it's always safe
  to `rm -rf` its contents while the worker is stopped.

### Rolling out a change

```bash
sudo systemctl stop lunaicad-worker      # finishes in-flight jobs, claims no new ones
# deploy new code (git pull / rsync, pip install -r requirements.txt)
alembic upgrade head
sudo systemctl start lunaicad-worker
sudo systemctl restart lunaicad-backend  # brief request interruption; nginx retries are the client's concern
```

Stopping the worker first (rather than killing it) lets in-flight jobs
finish normally instead of being reaped as stale on the next startup — not
required for correctness (stale-job recovery handles an unclean stop fine),
but avoids unnecessarily failing jobs that were seconds from completing.
