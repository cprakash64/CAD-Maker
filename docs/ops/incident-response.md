# Incident response

Each scenario: how you'll notice, what to check, what to do, how to confirm
it's resolved. Cross-references `docs/ops/observability.md` for the exact
metrics/log events named below.

## OpenAI outage / degraded

**Notice**: `llm_circuit_breaker_state == 1`, a burst of
`llm_circuit_breaker_open` / `openai_call_failed` log events, users reporting
generation failures for non-deterministic families.

**What's still working**: deterministic routes (templates, gears, precision
parts, most `CadPlan`-buildable families — see `docs/CAD_FAMILIES.md`) never
call the LLM at all and are unaffected. `GET /api/provider-status` reflects
degraded capability to the frontend. Reads (viewing/exporting existing
designs) are completely unaffected — nothing about this touches the DB or
storage.

**Check**:
1. `curl -H "Authorization: Bearer $OPS_API_TOKEN" https://.../metrics | grep llm_`
2. Is it OpenAI (check https://status.openai.com) or us (check `OPENAI_API_KEY`
   validity, `OPENAI_BASE_URL` if using a proxy, network egress from the VPS)?

**Do**:
- If it's a genuine OpenAI-side outage: nothing to fix — the circuit breaker
  is already doing its job (failing fast instead of piling up slow timeouts).
  It self-heals: `LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS` (default 60s) after
  the last failure, the next call is allowed through as a probe: success
  resets it, failure re-opens it.
- If it's a bad key/config: fix the env var, `sudo systemctl restart
  lunaicad-backend lunaicad-worker` (circuit breaker state is per-process, so
  a restart also clears it — fine, since the underlying cause is now fixed).
- Communicate: this is exactly the "graceful degraded mode" the product is
  designed for — deterministic families keep working, everything else
  returns a clear "temporarily degraded" error rather than hanging.

**Resolved when**: `llm_circuit_breaker_state` back to 0, `openai_call_completed`
events resuming normally.

## Database unreachable

**Notice**: `GET /ready` returns 503 with `checks.database.ok == false`;
`_database_error` handler firing (`database_error` log events);
`db_pool_connections{state="checked_out"}` pinned at the pool max.

**Do**:
1. Confirm from the DB host itself: is Postgres up? Disk full (a full disk
   is a very common cause and easy to miss)? `df -h` on the DB host.
2. If Postgres is fine but unreachable from the app host: network/firewall,
   or the DB is refusing new connections (`max_connections` exhausted from
   OTHER clients — check `SELECT count(*) FROM pg_stat_activity;`).
3. If Postgres itself is down and won't come back: this is a restore
   scenario — see `docs/ops/backup-and-restore.md`. Restore into a fresh
   instance from the most recent nightly backup, point `DATABASE_URL` at it,
   `alembic upgrade head` (no-ops if already current), restart both services.
4. `lunaicad-backend` (FastAPI) will return 503s for every DB-touching route
   while this is happening (via the `SQLAlchemyError` handler) — not crash,
   not hang. `lunaicad-worker` will fail to claim jobs and retry on its next
   poll tick; jobs already `queued` are NOT lost (they're rows, and the rows
   are what's unreachable, not deleted) — they'll be claimed once the DB is
   back.

**Resolved when**: `/ready` returns 200, `db_pool_connections` back to normal
levels.

## Worker crash loop

**Notice**: `worker_crashes_total` climbing steadily; repeated
`worker_job_crashed` events with the SAME `exitcode` across different jobs
(a specific `exitcode` maps to a specific signal: `-9` = SIGKILL/likely
OOM-killer, `-11` = SIGSEGV/OCCT crash, `-24` = SIGXCPU/our own
`RLIMIT_CPU`).

**Do**:
1. `exitcode=-9` repeatedly → the VPS is out of memory. Check
   `JOB_WORKER_CONCURRENCY × JOB_MEMORY_LIMIT_MB` against actual available
   RAM (`docs/deployment.md`'s Sizing section) — lower concurrency or the
   per-job memory limit, or add RAM.
2. `exitcode=-11` repeatedly on a SPECIFIC input shape → an OCCT/CadQuery
   segfault on a particular family/dimension combination. This is contained
   (the supervisor never crashes, only the one child) but worth a bug report
   — check `worker_job_crashed`'s `job_id` against `job_completed` for the
   input that triggered it (via the job's `payload_json`, DB-side).
3. `exitcode=-24` repeatedly → `JOB_CPU_TIME_LIMIT_SECONDS` is too tight for
   legitimate work (not a crash-loop bug, a config mismatch) — raise it, or
   investigate why generation is taking that much CPU time.
4. If the SUPERVISOR itself is crash-looping (not individual jobs — check
   `sudo systemctl status lunaicad-worker` for repeated restarts),
   `journalctl -u lunaicad-worker -n 200` for the actual Python traceback
   (this would be a bug in the supervisor loop itself, not a job).

**Resolved when**: `worker_crashes_total`'s rate returns to baseline (crashes
happen; a crash RATE matching historical baseline is normal, zero is not the
bar — see the alert threshold in `docs/ops/observability.md`).

## Cost spike

**Notice**: the cost-spike alert (`docs/ops/observability.md`), or
`llm_circuit_breaker_state == 1` with `reason: "daily model-spend cap
reached"`.

**Do**:
1. `curl .../metrics | grep llm_estimated_cost_usd_total` — which model, how
   fast is it climbing.
2. Runaway loop vs. legitimate traffic spike: check `design_requests_total`'s
   rate alongside the cost — if requests aren't correspondingly spiking, a
   SINGLE code path may be retrying/looping (check `openai_model_fallback`
   frequency — repeated fallback through the whole model chain multiplies
   cost per request).
3. If it's a genuine traffic spike (marketing, viral post, etc.): this is
   working as intended — the daily cap (`LLM_COST_DAILY_CAP_USD`) protects
   against a BILLING surprise, not against real usage; raise the cap if the
   revenue/usage justifies it, or let it degrade gracefully until the UTC-day
   rollover (documented user-facing behavior, not a bug).
4. If it's a bug (retry loop, a prompt template that ballooned in size,
   etc.): fix and deploy; the circuit breaker's cost trip already stopped
   the bleeding while you investigate — that's its job.

**Resolved when**: cost accrual rate back to baseline, breaker closed (or
intentionally left open until the next UTC day if the cap was hit
legitimately).

## Queue saturation

**Notice**: `JobQueueSaturated` errors (503s on generation endpoints),
`sum(job_active_status_count)` approaching `JOB_MAX_QUEUE_DEPTH`.

**Do**:
1. Legitimate load vs. stuck jobs: `job_active_status_count{status="running"}`
   high but not moving → jobs are stuck, not just numerous. Check
   `worker_job_duration_seconds` — are jobs actually finishing?
2. If workers are genuinely keeping up but volume is just high: this is the
   queue depth cap doing its job (protecting the DB/VPS from unbounded
   backlog) — raise `JOB_MAX_QUEUE_DEPTH` and/or `JOB_WORKER_CONCURRENCY`
   (mind the memory sizing math in `docs/deployment.md`) if the VPS has
   headroom, or add a second worker host (the job table IS the queue — a
   second `lunaicad-worker` process pointed at the same DB just works, no
   config beyond that).
3. If jobs ARE stuck: check for a stale supervisor (`reap_stale_jobs` should
   self-heal this within `JOB_STALE_HEARTBEAT_SECONDS`, default 90s — if
   it's been longer, the supervisor process itself may be wedged;
   `sudo systemctl restart lunaicad-worker`).

**Resolved when**: queue depth trending down, `JobQueueSaturated` errors
stop.

## Authentication abuse (credential stuffing / brute force)

**Notice**: `auth_failures_total{reason="invalid_credentials"}` spiking,
`rate_limited_total{category="auth"}` spiking (rate limiting is already
throttling it — this is a "notice," not necessarily an "act now").

**Do**:
1. Distinguish targeted (many attempts against ONE account) from broad
   (credential stuffing across MANY accounts) — the former may warrant
   notifying that specific user; the latter is generic internet noise
   `rate_limit`'s per-IP bucketing already absorbs most of.
2. If a specific IP/range is the source and rate limiting isn't sufficient
   (e.g. a large botnet, one attempt per IP), block at the nginx/firewall
   layer — this is outside the app's own rate limiter's design (per-process,
   not a WAF).
3. `RATE_LIMIT_AUTH` (default `10/60`) can be tightened temporarily if
   needed; remember to revert once the incident passes.

**Resolved when**: `auth_failures_total`'s rate back to baseline.
