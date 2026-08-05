# Observability: telemetry, metrics, and the redaction contract

This is the source of truth for what LunaiCAD logs, what it exposes as
metrics, and — the part that matters most — what it deliberately **never**
logs. If you're adding a new `log_event(...)` call site, read the "What must
never appear in a log" section first.

## Structured logs

Every event is one JSON line (`app.observability.log_event`), written to
stdout (captured by systemd/journald in production — see
`docs/deployment.md`). There is no separate log-shipping story yet; `journalctl
-u lunaicad-backend -o cat` / `-u lunaicad-worker -o cat` gives you the raw
JSON stream today. A future step (out of scope here) would ship these to a
log aggregator (e.g. Loki, CloudWatch Logs) — the JSON-per-line format is
already shaped for that.

### Automatic fields

- **`request_id`** — assigned per HTTP request by `app.main`'s
  `_request_context` middleware (honors an inbound `X-Request-Id` from the
  reverse proxy, so a trace started upstream stays one id end to end), stored
  in a `contextvar`, and merged into every `log_event(...)` call automatically
  for the lifetime of that request. Also returned as a response header. A job
  submitted from within a request carries the SAME request id into its own
  logs (submission happens synchronously inside the request), but note the
  worker process that later CLAIMS and RUNS the job is a separate process
  with its own context — its logs correlate by `job_id`, not `request_id`.

### The canonical generation-workflow event

`job_completed` (`app.services.job_service._log_job_completed`) fires exactly
once per job, from whichever of `mark_succeeded` / `mark_terminal_failure`
the job actually reaches (never on a requeue) — this is the "one event per
generation attempt" record the product-metrics side of the system is built
around:

```json
{
  "event": "job_completed",
  "job_id": "...", "job_type": "design_create", "workflow": "design_create",
  "final_outcome": "succeeded",
  "error_category": null,
  "retries": 0, "attempt": 1,
  "queue_wait_ms": 12.4, "run_ms": 3210.5,
  "request_id": "..."
}
```

Fields not on this event but needed by the full "structured, redacted
telemetry" list are on the events it's paired with (same `request_id`,
correlatable):

| Field | Event | Notes |
|---|---|---|
| `design_id` | `geometry_generated`, `design_created`, `design_edited` | |
| `user_id` (pseudonymous) | `design_created` etc. | see "Privacy-safe identity" below |
| `prompt_hash` (never raw prompt) | `design_created` etc. | see "What must never appear" |
| `model`, `reasoning_effort` | `openai_call_completed` | |
| `capability_level` | `design_created` etc. | production_ready / validated_beta / experimental / unsupported |
| `input_tokens`, `output_tokens` | `openai_call_completed` | |
| `estimated_cost_usd` | `openai_call_completed` | see `app.llm.pricing` |
| `compile_ms`, `validate_ms`, `export_ms` | `geometry_generated` | see `app.export.exporter.generate` / `design_service._regenerate_geometry` |
| worker memory/process outcome | `worker_job_crashed` (`exitcode`), `worker_job_finished` | negative exitcode = killed by signal (SIGSEGV/OOM/RLIMIT_CPU) |
| `error_category` | `job_completed`, `job_failed_terminal` | see `job_service.classify_error` |

### Privacy-safe identity (`app.observability.pseudonymize`)

User/tenant ids in the canonical telemetry stream (`log_design_telemetry` and
friends) are HMAC-SHA256'd with `TELEMETRY_HASH_SECRET` (falls back to
`JWT_SECRET` if unset — set a **dedicated** value in production so rotating
one doesn't invalidate the other's correlation history). This is
deterministic (same user → same hash every time), so events/metrics stay
correlatable by account without a log reader being able to recover the raw
id. Lower-level debug events elsewhere in the codebase (e.g.
`job_service.submit_job`'s `job_submitted`) still log the raw `user_id` —
that id is itself an opaque random UUID, never an email, so this is a
judgment call, not an oversight: the CANONICAL product-telemetry stream
(what analytics/cost dashboards are built on) is pseudonymized; incidental
debug breadcrumbs are not yet retrofitted. If you're adding a new telemetry
consumer that leaves this process, pseudonymize the id at that boundary.

### Content fingerprints (`app.observability.content_fingerprint`)

A SHA-256 (truncated to 16 hex chars) of a piece of content — used for
`prompt_hash` and `email_hash`. Lets you answer "has this exact prompt been
submitted before" or "how many failed logins hit this email" from logs
without the logs ever containing the prompt or the email.

## What must never appear in a log (and how it's enforced)

Two layers, deliberately redundant:

1. **Callers only pass safe fields.** `log_design_telemetry` passes
   `prompt_hash`, never `prompt`. `openai_provider.py` logs
   `image_b64_len=len(image_b64)`, never the image data. This is the primary
   defense — get the call site right and there's nothing to redact.
2. **`app.observability._scrub` is the backstop**, applied to every
   `log_event(...)` call automatically:
   - **By key name**: `api_key`, `password`, `token`, `secret`,
     `authorization`, `cookie`, `signed_url`, etc. (see `_SECRET_KEYS`) are
     replaced with `"***"` outright, whatever the value.
   - **By value pattern**: even under an innocuous key name, a string value
     is scanned for `Bearer <token>`, a raw JWT (`eyJ...`), a
     `scheme://user:pass@host` connection string, or a signed-URL query
     string (`?...Signature=...` / `X-Amz-Signature=...`) — the matched span
     is redacted, not the whole value, so the rest of the string (e.g. a
     path) stays useful.
   - **Length-capped**: any string over 2000 chars is truncated with a
     `"...<N more chars truncated>"` marker — a backstop against ever
     accidentally dumping a whole file/body into a log line.
3. **The last-resort exception handlers** (`app.main`'s `_database_error` /
   `_unhandled_error`) log the FULL traceback server-side (never to the
   client — see their docstrings) via `log_exception_redacted`, which applies
   the SAME value-pattern scrub to the formatted traceback text before it
   hits the log — specifically so a DB driver error that echoes back a
   connection string (a real SQLAlchemy failure mode) can't leak credentials
   even through this path.

**Complete uploaded drawings**: never logged. `app.routers.drawings` logs
`image_hash` (a content hash) and `image_b64_len`, never the bytes. The
raster/vector content itself lives only in job-scoped temp storage, deleted
by `app.worker.supervisor._cleanup_job_storage` once the job reaches a
terminal state (see `docs/ops/data-retention.md`).

**Proprietary prompts**: never logged as raw text anywhere in the codebase —
see `docs/ops/data-retention.md` for where the prompt DOES live (the
access-controlled `designs.prompt` column) and its retention policy.

## Metrics (`/metrics`, Prometheus text format)

`app.metrics` (backed by `prometheus_client`). Counters/histograms are
incremented at the SAME call sites that emit the structured logs above —
metrics and logs are two views of the same instrumentation, not derived from
each other. Gated by `OPS_API_TOKEN` (`Authorization: Bearer <token>`) —
required to be set in production (`Settings.production_problems`) because
this endpoint reveals cost/queue/business data. Open in dev/test
(`OPS_API_TOKEN` unset).

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `http_requests_total` | counter | `method, route, status_class` | **API error rate**: `sum(rate(http_requests_total{status_class=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` |
| `http_request_duration_seconds` | histogram | `route` | latency percentiles |
| `worker_jobs_total` | counter | `job_type, outcome` | **job timeout rate**: `rate(worker_jobs_total{outcome="timed_out"}[1h]) / rate(worker_jobs_total[1h])` |
| `worker_job_duration_seconds` | histogram | `job_type` | run time, claimed→finished |
| `worker_job_queue_wait_seconds` | histogram | `job_type` | time spent queued before claim |
| `worker_crashes_total` | counter | `signal` | **worker crash rate**: `rate(worker_crashes_total[1h])` |
| `job_active_status_count` | gauge (live, DB-computed) | `status` | **queue depth**: `sum(job_active_status_count)` |
| `design_requests_total` | counter | `outcome` | **generation success**: `design_requests_total{outcome="success"} / (success+failed)` |
| `design_validation_total` | counter | `status` | **semantic-validation success**: share with `status="pass"` |
| `design_export_total` | counter | `fmt, outcome` | **export success** |
| `design_downloads_total` | counter | `fmt` | downloads by format (stl/step/glb/package) |
| `llm_calls_total` | counter | `model, outcome` | includes `outcome="blocked"` while the circuit breaker is open |
| `llm_tokens_total` | counter | `model, kind` (input/output) | |
| `llm_estimated_cost_usd_total` | counter | `model` | **cost per request** = `llm_estimated_cost_usd_total / design_requests_total`; **cost per validated result** = `.../design_validation_total{status="pass"}`; **cost per downloaded result** = `.../sum(design_downloads_total)` |
| `llm_circuit_breaker_state` | gauge | | 1 = open (degraded mode) |
| `storage_bytes_used` | gauge (live, DB-computed) | | **storage growth**: track this gauge's slope over time (e.g. `deriv(storage_bytes_used[1d])`) |
| `storage_cleanup_bytes_reclaimed_total` | counter | | bytes reclaimed by the retention sweep |
| `auth_failures_total` | counter | `reason` (invalid_credentials / invalid_token / signup_conflict) | **authentication abuse** |
| `rate_limited_total` | counter | `category` | **rate-limit events** |
| `quota_exceeded_total` | counter | `quota` (daily/monthly/storage) | quota-exhaustion events |
| `db_pool_connections` | gauge (live) | `state` (checked_out/checked_in) | connection-pool pressure |
| `budget_reservations_total` | counter | `operation_type, outcome` (reserved/committed/released) | **refund rate** = `released / reserved`; sudden reservation-rate spike (see alerts.yml) |
| `cost_control_rejections_total` | counter | `reason, scope` (account/global) | **per-account/global quota rejections**; `reason` is the CostControlError code |
| `cost_reconciliation_delta_cents` | histogram | `operation_type` | actual − estimated cost at commit; a widening distribution means the pre-flight estimate (`app.cost_control.estimator`) needs recalibrating |
| `estimated_vs_actual_cost_cents_ratio` | histogram | `operation_type` | actual/estimated ratio; 1.0 = exact |
| `stale_reservations_reaped_total` | counter | | reservations released by TTL (crashed/restarted process before commit/release) |
| `emergency_stop_active` | gauge | | 1 = global cost-control kill switch is active |
| `storage_bytes_used_top_accounts` | gauge (live, DB-computed, bounded to top 10) | `rank` | **abnormal per-account storage usage** — see `docs/operations/cost-control-architecture.md` |
| worker seconds per successful result | *(derived)* | | `sum(rate(worker_job_duration_seconds_sum[1h])) / sum(rate(worker_jobs_total{outcome="succeeded"}[1h]))` |

### Alert thresholds (adjust to your traffic)

Committed as real, loadable Prometheus alert rules at
`deploy/prometheus/alerts.yml` (load via `rule_files:` in `prometheus.yml`) —
this table is the rationale for each rule, that file is what actually fires.
The defaults below match `app.config.Settings`' defaults
(`JOB_MAX_QUEUE_DEPTH=50`, `LLM_COST_DAILY_CAP_USD=25.0`, `DB_POOL_SIZE=10`,
`DB_MAX_OVERFLOW=10`); if a deployment overrides these env vars, update both
this table and `alerts.yml` to match.

| Alert | Expression | Why |
|---|---|---|
| API error rate high | `sum(rate(http_requests_total{status_class="5xx"}[5m])) / sum(rate(http_requests_total[5m])) > 0.05` for 5m | 5xx above 5% sustained |
| Worker crash loop | `rate(worker_crashes_total[15m]) > 0` for 15m | any sustained crash rate is worth paging |
| Queue saturating | `sum(job_active_status_count) > 0.8 * <JOB_MAX_QUEUE_DEPTH>` | approaching `JobQueueSaturated` |
| Job timeout rate high | `rate(worker_jobs_total{outcome="timed_out"}[1h]) / rate(worker_jobs_total[1h]) > 0.1` | >10% of jobs timing out |
| Cost spike | `increase(llm_estimated_cost_usd_total[1h]) > 0.5 * <LLM_COST_DAILY_CAP_USD>` | half the daily cap burned in an hour |
| Circuit breaker open | `llm_circuit_breaker_state == 1` for 2m | degraded mode is active — see `docs/ops/incident-response.md` |
| DB pool exhausted | `db_pool_connections{state="checked_out"} >= <DB_POOL_SIZE> + <DB_MAX_OVERFLOW>` | requests about to start queuing on the pool |
| Cost-control sudden spike | reservation rate > 3x the same window an hour ago | see `docs/operations/cost-control-architecture.md` |
| Reservation reconciliation errors | p95 `cost_reconciliation_delta_cents` > 20¢ for 15m | pre-flight estimates drifting from real usage |
| High retry rate | job failure rate > 20% for 15m | possible retry storm |
| One account disproportionate usage | an account trips its own limits >50x/hour | see the admin `/api/admin/cost/accounts/{id}/abnormal-usage` endpoint |
| Global emergency budget activation | `emergency_stop_active == 1` | all generation is currently paused |
| Global budget rejections | `cost_control_rejections_total{scope="global"}` increasing | the WHOLE SYSTEM is out of daily/hourly budget, not just one account |
| Storage growth anomaly | `deriv(storage_bytes_used[1h]) > 500MB/hour` for 30m | see `docs/operations/cost-control-architecture.md` |

Full rule definitions: `deploy/prometheus/alerts.yml`'s `lunaicad-cost-control`
group.

## Multiprocess metrics (`--workers N`)

`deploy/systemd/lunaicad-backend.service` runs uvicorn with `--workers 2` —
two independent OS processes behind the same socket. Plain
Counters/Histograms/Gauges are per-process in-memory by default; without
handling this, a `/metrics` scrape would only reflect whichever ONE process
answered it, silently undercounting (and a Gauge would appear to flip
between values as different scrapes hit different workers).

Fix: set `PROMETHEUS_MULTIPROC_DIR` (see `.env.example`) to a writable
directory. `prometheus_client` then automatically backs every metric with an
mmap'd file per process instead of pure memory (no code change needed per
metric — this is keyed off the env var being present when each metric object
is created, i.e. at import time). `app.metrics.render_latest()` detects the
same env var and, when set, reads ALL workers' files back via
`multiprocess.MultiProcessCollector` and merges them — so one scrape sees
the true aggregate. `llm_circuit_breaker_state` uses `multiprocess_mode="max"`
(degraded if ANY worker's breaker is open, not an average). The DB-derived
gauges (`job_active_status_count`, `storage_bytes_used`, `db_pool_connections`
— `app.metrics._DbGaugesCollector`) are unaffected either way: they run a
fresh DB query on every scrape regardless of which worker answers, so there's
no per-process state to merge in the first place.

The systemd unit's `ExecStartPre` wipes the multiproc directory before each
start — without this, a stale file from a PID that no longer exists (a
previous run) would inflate merged counts forever. Running with
`--workers 1` (or plain `uvicorn` locally in dev) needs none of this;
`PROMETHEUS_MULTIPROC_DIR` unset is the normal, correct configuration there.

## Health vs readiness

- **`GET /health`** (liveness) — trivial, always `{"status": "ok"}` if the
  process can respond at all. No dependency checks. Use for "should this
  process be restarted" (systemd/process-manager level).
- **`GET /ready`** (readiness) — actually probes the DB (`SELECT 1`) and
  storage (writability for local, config presence for S3). Returns 503 with
  a per-check breakdown if anything's down. Use for "should traffic be routed
  here" (load balancer / reverse proxy level). Public — no secrets in the
  body, only booleans, so it's safe to poll unauthenticated.
