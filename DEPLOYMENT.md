# Deployment — SourceCAD AI Part Studio

Production deployment guide for the FastAPI backend + Next.js frontend.

> **Scope:** this covers a single Linux VPS with a reverse proxy. Docker is
> **optional** (see the end); the primary path here is native systemd + nginx,
> which is the lightest way to run this app reliably.

---

## 0. Pre-flight: secrets safety

- `backend/.env` and `frontend/.env.local` are **gitignored** — never commit them.
- Run the secrets guard before every commit/deploy:
  ```bash
  bash scripts/check-secrets.sh
  ```
- **If any real API key or `JWT_SECRET` was ever committed, shared, screen-shared,
  or pasted into a chat — rotate it now.** Rotating OpenAI keys:
  <https://platform.openai.com/api-keys>. A leaked key is billable by anyone.

The app **fails fast at startup** in `staging`/`production` if the config is
unsafe (mock LLM, default/short `JWT_SECRET`, missing `DATABASE_URL`, localhost
`CORS_ORIGINS`/`PUBLIC_BASE_URL`, `DEV_MODE=true`, or `s3` without a bucket). The
error lists every problem at once.

---

## 1. Environment variables

### Backend (`backend/.env`) — required in production

| Variable | Required (prod) | Example | Notes |
| --- | --- | --- | --- |
| `APP_ENV` | yes | `production` | enables strict startup validation |
| `JWT_SECRET` | yes | `<openssl rand -hex 32>` | dev default + <32 chars are rejected |
| `DATABASE_URL` | yes | `postgresql+psycopg://u:p@host/cad` or `sqlite:////var/lib/cadmaker/cad.db` | must be set explicitly |
| `LLM_PROVIDER` | yes | `openai` | `mock` is rejected in prod |
| `OPENAI_API_KEY` | if openai | `sk-...` | keep only in `.env` / secret store |
| `OPENAI_MODEL` | recommended | `gpt-4.1` | invalid id is non-fatal (falls back) but adds latency |
| `CAD_LLM_MODEL` | optional | `gpt-4.1` | planner model; has its own fallback chain |
| `PUBLIC_BASE_URL` | yes | `https://api.yourdomain.com` | baked into download links; not localhost |
| `CORS_ORIGINS` | yes | `https://app.yourdomain.com` | comma-separated; not localhost |
| `DEV_MODE` | yes | `false` | hides provider status in prod |
| `STORAGE_BACKEND` | yes | `local` or `s3` | |
| `STORAGE_DIR` | if local | `/var/lib/cadmaker/storage` | persist on a durable volume |
| `S3_BUCKET` | if s3 | `sourcecad-exports` | required when `STORAGE_BACKEND=s3` |
| `S3_REGION` / `S3_ENDPOINT_URL` | if s3 | `us-east-1` / blank | endpoint for MinIO/R2 |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | s3 (unless IAM role) | — | omit to use an instance role |
| `OPENAI_TIMEOUT_SECONDS` | optional | `60` | per-request timeout |
| `OPENAI_MAX_RETRIES` | optional | `2` | SDK auto-retries 429/5xx |
| `RATE_LIMIT_ENABLED` | optional | `true` | on by default in prod; set `false` to disable (see §2c) |
| `RATE_LIMIT_AUTH` / `_CREATE` / `_REGENERATE` / `_MODIFY` / `_DRAWING` / `_PACKAGE` / `_DEFAULT` | optional | `30/60` | per-category `requests/window_s` (see §2c) |
| `LOG_LEVEL` | optional | `INFO` | |

Generate a JWT secret:
```bash
openssl rand -hex 32
```

> **Database driver:** `requirements.txt` bundles both SQLite (stdlib) and the
> Postgres driver (`psycopg[binary]`). Pick the backend with the URL scheme:
> ```bash
> # SQLite (single box):      DATABASE_URL=sqlite:////var/lib/cadmaker/cad.db
> # Postgres (recommended):   DATABASE_URL=postgresql+psycopg://user:pass@host:5432/cadmaker
> ```
> Postgres setup is covered in §2b. **Migrations:** the schema is owned by
> **Alembic** (see §2a). In
> staging/production the app does **not** auto-create tables — run
> `alembic upgrade head` before starting the server. (In dev/test it still
> auto-creates from the models for zero-setup convenience.)

### Frontend (`frontend/.env.local`) — build-time, PUBLIC

| Variable | Example | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE` | `https://api.yourdomain.com` | the only var the app reads; inlined at build → **rebuild after changing** |

---

## 2. Backend setup (Python 3.11)

```bash
cd /opt/cadmaker/backend
python3.11 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt   # includes the Postgres driver

cp .env.example .env        # then edit .env with real production values
```

### 2a. Database migrations (Alembic)

The schema is managed by Alembic (config in `backend/alembic.ini`, migrations in
`backend/alembic/versions/`). Alembic reads `DATABASE_URL` from the app settings,
so it always targets the same database the app uses. **Run all commands from
`backend/`** with the venv active.

```bash
cd /opt/cadmaker/backend

# Apply all migrations (run this on every deploy, BEFORE starting the server):
.venv/bin/python -m alembic upgrade head

# Inspect state:
.venv/bin/python -m alembic current        # current DB revision
.venv/bin/python -m alembic history         # migration history
.venv/bin/python -m alembic check           # fail if models drift from migrations

# Roll back the most recent migration (if a deploy goes wrong):
.venv/bin/python -m alembic downgrade -1

# Create a new migration after changing app/models.py (review the file before commit!):
.venv/bin/python -m alembic revision --autogenerate -m "describe the change"
```

> **Existing database (IMPORTANT — do not lose data):** if the database was
> created by the old `create_all` path and already has the tables, do **not** run
> `upgrade` (it would try to re-create existing tables). Instead, mark it as
> already at the baseline once:
> ```bash
> .venv/bin/python -m alembic stamp head
> ```
> After stamping, future `alembic upgrade head` runs apply only new migrations.

> **Postgres:** the driver is already installed (`psycopg[binary]`). Just set
> `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/cadmaker`; the migration
> command is identical (see §2b). Migrations use batch mode so future `ALTER`s
> work on SQLite too; this is transparent on Postgres.

### 2b. Postgres setup (recommended for production)

`psycopg[binary]` ships in `requirements.txt`. Use the SQLAlchemy URL scheme
`postgresql+psycopg://` (psycopg 3). The connection pool uses `pool_pre_ping`, so
dropped/idle connections are recycled automatically.

**Local Postgres (dev/staging on one box):**
```bash
# macOS (Homebrew):
brew install postgresql@16 && brew services start postgresql@16
createdb cadmaker
# Debian/Ubuntu:
sudo apt-get install -y postgresql
sudo -u postgres createuser --pwprompt cadmaker
sudo -u postgres createdb -O cadmaker cadmaker

# Point the app at it, then migrate:
export DATABASE_URL="postgresql+psycopg://cadmaker:PASSWORD@127.0.0.1:5432/cadmaker"
.venv/bin/python -m alembic upgrade head
```

**Production Postgres (managed service or dedicated server):**
```sql
-- one-time, as a superuser:
CREATE ROLE cadmaker LOGIN PASSWORD 'strong-password';
CREATE DATABASE cadmaker OWNER cadmaker;
```
```bash
# In backend/.env:
DATABASE_URL=postgresql+psycopg://cadmaker:strong-password@db-host:5432/cadmaker
# (URL-encode special characters in the password, e.g. @ -> %40.)
# For TLS to a managed DB, append ?sslmode=require

# Apply migrations before starting the server (systemd ExecStartPre does this):
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current   # -> shows the head revision
```

> Verified: `alembic upgrade head` / `check` / `downgrade base` and a full ORM +
> JSON-column round-trip all pass on Postgres 16. Run the opt-in migration test
> against your own instance:
> ```bash
> CADMAKER_TEST_PG_URL="postgresql+psycopg://USER@127.0.0.1:5432/cadmaker_test" \
>   .venv/bin/python -m pytest tests/test_migrations.py -k postgres
> ```

### 2c. Rate limiting

Abuse-prone routes are rate limited per **authenticated user** (or per **client
IP** when anonymous). It is **off in dev/test** and **on by default in
staging/production**; `RATE_LIMIT_ENABLED=true|false` forces it either way.

Limits are `requests/window_seconds` and tunable per category via env:

| Env var | Default | Protects |
| --- | --- | --- |
| `RATE_LIMIT_AUTH` | `10/60` | `POST /api/auth/login`, `/signup` (per IP) |
| `RATE_LIMIT_CREATE` | `30/60` | `POST /api/designs/create`, `/generate-with-defaults` |
| `RATE_LIMIT_REGENERATE` | `60/60` | `POST /api/designs/{id}/regenerate` |
| `RATE_LIMIT_MODIFY` | `30/60` | `/modify`, `/localized-edit`, `/circle-edit` |
| `RATE_LIMIT_DRAWING` | `12/60` | `POST /api/drawings/interpret`, `/generate`, `/confirm` |
| `RATE_LIMIT_PACKAGE` | `60/60` | `/export`, `/package`, `/views/{view}` |
| `RATE_LIMIT_DEFAULT` | `120/60` | fallback |

When exceeded the API returns **HTTP 429** with a `Retry-After` header and a
clear JSON `detail`. Clients should honor `Retry-After` and back off.

> **Multi-worker / multi-host caveat:** counters are **in-memory per process**.
> With `--workers N` (or multiple hosts) the effective limit is roughly `N×`
> the configured value, because each worker counts independently. For a strict
> global limit, run **one worker** for the API, or back the limiter with Redis
> later (the `rate_limit(category)` interface is unchanged). Ensure nginx sets
> `X-Forwarded-For` (the sample config does) so IP-based limits see the real
> client IP, not the proxy.

**Production run command** (no `--reload`; multiple workers; bind localhost so
only the reverse proxy is public):
```bash
.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --workers 3 --proxy-headers
```
Pick workers ≈ CPU cores. CadQuery geometry is CPU-bound and synchronous, so
each worker handles one generation at a time. (For more concurrency use
`gunicorn -k uvicorn.workers.UvicornWorker app.main:app -w N`.)

> CadQuery's wheel bundles OpenCascade — no system CAD libraries needed. The
> OpenSCAD fallback (rarely used) needs the `openscad` binary; install it only
> if you exercise that path: `apt-get install -y openscad`.

### systemd unit — `/etc/systemd/system/cadmaker-api.service`
```ini
[Unit]
Description=SourceCAD API (FastAPI/uvicorn)
After=network.target

[Service]
User=cadmaker
WorkingDirectory=/opt/cadmaker/backend
EnvironmentFile=/opt/cadmaker/backend/.env
# Apply DB migrations before the server starts (no-op if already at head).
ExecStartPre=/opt/cadmaker/backend/.venv/bin/python -m alembic upgrade head
ExecStart=/opt/cadmaker/backend/.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --workers 3 --proxy-headers
Restart=always
RestartSec=3
# Persist generated files + sqlite if used:
StateDirectory=cadmaker

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cadmaker-api
```

---

## 3. Frontend setup (Node 18+)

```bash
cd /opt/cadmaker/frontend
npm ci
cp .env.local.example .env.local     # set NEXT_PUBLIC_API_BASE to your API URL
npm run build                         # production build
npm run start                         # serves on http://127.0.0.1:3000
```
`npm run build` runs `next build`; this repo also has `npm run check:routes`
(asserts the New-Design routes exist) and `npm run typecheck`.

### systemd unit — `/etc/systemd/system/cadmaker-web.service`
```ini
[Unit]
Description=SourceCAD Web (Next.js)
After=network.target

[Service]
User=cadmaker
WorkingDirectory=/opt/cadmaker/frontend
Environment=NODE_ENV=production
Environment=PORT=3000
ExecStart=/usr/bin/npm run start
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

---

## 4. Reverse proxy (nginx) + TLS

Frontend on `app.yourdomain.com`, backend on `api.yourdomain.com`.

`/etc/nginx/sites-available/cadmaker`:
```nginx
# Frontend
server {
  server_name app.yourdomain.com;
  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}

# Backend API
server {
  server_name api.yourdomain.com;
  client_max_body_size 16m;          # drawing uploads (backend caps at 12 MB)
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;         # CAD generation can take seconds
  }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/cadmaker /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d app.yourdomain.com -d api.yourdomain.com   # TLS
```
Then set backend `CORS_ORIGINS=https://app.yourdomain.com`,
`PUBLIC_BASE_URL=https://api.yourdomain.com`, and frontend
`NEXT_PUBLIC_API_BASE=https://api.yourdomain.com` (rebuild the frontend).

> Single-domain alternative: serve the frontend at `/` and proxy only `/api` +
> `/health` to the backend. If you do this, the download URLs in `PUBLIC_BASE_URL`
> must still resolve to the backend.

---

## 5. Health checks

| Check | Command | Expect |
| --- | --- | --- |
| API liveness | `curl -s https://api.yourdomain.com/health` | `{"status":"ok"}` (no provider info in prod) |
| Provider status | `curl -s https://api.yourdomain.com/api/provider-status` | `provider`, `model`, `model_verified:false` |
| Frontend | `curl -sI https://app.yourdomain.com` | `200 OK` |
| Auth smoke | `curl -s -XPOST https://api.yourdomain.com/api/auth/signup -H 'content-type: application/json' -d '{"email":"a@b.com","password":"password123"}'` | `201` + token |

`/health` is a good load-balancer probe. Every response carries an
`X-Response-Time-ms` header.

---

## 6. Operations — restart / logs / common failures

```bash
# Restart
sudo systemctl restart cadmaker-api
sudo systemctl restart cadmaker-web

# Logs (structured JSON events from the API)
journalctl -u cadmaker-api -f
journalctl -u cadmaker-web -f
```

**Common failure cases**

| Symptom | Cause | Fix |
| --- | --- | --- |
| API exits immediately with "Refusing to start: unsafe production configuration" | a required prod var is missing/unsafe | read the listed problems; fix `.env`; restart |
| `503 The AI service is temporarily unavailable` | OpenAI down, bad key, or invalid `OPENAI_MODEL` after the fallback chain is exhausted | check key/quota; set a valid `OPENAI_MODEL`; inspect `openai_call_failed` log events |
| Browser: `Failed to fetch` / CORS error | `CORS_ORIGINS` doesn't match the frontend origin, or `NEXT_PUBLIC_API_BASE` is wrong | align both; rebuild frontend after changing it |
| Downloads 404 / wrong host | `PUBLIC_BASE_URL` wrong, or `storage_data` not persisted | set `PUBLIC_BASE_URL`; mount a durable volume / use S3 |
| Postgres: `ModuleNotFoundError: psycopg` | venv predates the driver | `pip install -r requirements.txt` (psycopg is bundled) |
| Postgres: auth/connection refused | wrong `DATABASE_URL`, role/db missing, or unencoded password char | verify role+db exist (§2b); URL-encode special chars (e.g. `@`→`%40`); add `?sslmode=require` for managed DBs |
| New code expects a DB column that doesn't exist | a migration wasn't applied | `alembic upgrade head` (see §2a); generate one with `alembic revision --autogenerate` after model changes |
| `alembic upgrade` fails: "table already exists" | DB predates Alembic (made by old `create_all`) | one-time `alembic stamp head`, then upgrade (see §2a) |
| Drawing upload rejected | image > 12 MB or proxy body limit | raise `client_max_body_size`; the backend hard cap is 12 MB |
| 401 on every design route | missing/expired bearer token | the frontend stores the JWT in localStorage; re-login |
| `429 Rate limit exceeded` (legit traffic) | limits too low, or `N` workers each counting separately | raise the relevant `RATE_LIMIT_*` (§2c); for a strict global cap run 1 worker or add Redis |
| IP-based limit blocks all users together | nginx not forwarding the real client IP | ensure `proxy_set_header X-Forwarded-For ...` (sample config has it) |

---

## 7. Remaining blockers before public launch

These are **not** solved by this deployment guide and should be addressed:

1. **Rotate the OpenAI key** currently in a local `backend/.env` and make the
   first git commit (the repo has no history yet).
2. **Backups** for the database and `storage_data`/S3.
3. **Confirm a valid `OPENAI_MODEL`** for your account (the historical default
   `gpt-5.5` is not a valid id; the fallback chain masks this but adds latency).

> ✅ **Database migrations (Alembic) are in place** — initial baseline +
> upgrade/downgrade/check verified on clean SQLite **and Postgres 16**.
> `create_all` is no longer the production strategy (dev/test-only).
> ✅ **Postgres is supported and tested** — `psycopg[binary]` bundled, ORM +
> JSON-column round-trip verified, `pool_pre_ping` enabled (§2b).
> ✅ **Rate limiting is in place** — per-user/per-IP limits on auth, create,
> regenerate, modify, drawing and export routes; 429 + `Retry-After`; on by
> default in production (§2c). For a strict global cap across workers, add Redis.

---

## 8. (Optional) Docker

Containers are a reasonable alternative to systemd. There are **no Dockerfiles in
the repo yet**; if you want them, a minimal split is:
- **backend**: `python:3.11-slim`, `pip install -r requirements.txt`, run the
  uvicorn command from §2.
- **frontend**: `node:20-slim`, `npm ci && npm run build`, run `npm run start`.
- a reverse proxy container (nginx/Caddy) or Compose `ports` for TLS.

Recommendation: for a single VPS, **native systemd + nginx (this guide) is
simpler and uses less memory** than Docker. Reach for containers when you need
horizontal scaling or reproducible multi-service orchestration.
