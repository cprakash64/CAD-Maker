# ADR 0001: A database-backed job queue, not Redis/Celery/RQ

- Status: accepted
- Date: 2026-07-28

## Context

CAD generation (drawing parsing, CadPlan compilation, OpenCascade/CadQuery
operations, mesh generation, validation, export) was running **inline in the
FastAPI request process**:

- `POST /api/designs/create` and every other design-mutating route called
  `design_service.*` directly from a synchronous route handler — the full
  CAD compute occupied a Starlette thread-pool thread for the entire request.
- `POST /api/drawings/to-cad` / `/generate` already returned 202 + a job id,
  but the "worker" was a bare `threading.Thread` inside the SAME OS process
  (`app/services/drawing_jobs.py`), with job state in a plain in-memory
  dict. A watchdog only *marked* a stuck job failed for the poller — the
  underlying thread kept running.

Neither survives a pathological input. A thread cannot be forcibly killed in
Python, and — critically — a segfault in OpenCascade's C++ core (a real,
observed failure mode, not a hypothetical one) takes down the **entire
process**, including every other in-flight request. There is no
`resource.setrlimit`, `signal.alarm`, cgroup, or container-level CPU/memory
limit anywhere in the codebase (confirmed by direct audit of the repository
before this change) — a wall-clock timeout existed for drawings only, and it
was cosmetic (see `drawing_jobs.py`'s own docstring: the watchdog fails the
job for the *poller*, the thread itself keeps running to completion).

Deployment reality (also confirmed by direct repository audit): **there is
no Dockerfile, docker-compose file, systemd unit, or nginx config anywhere
in this repo.** `README.md` describes a VPS/systemd topology in prose, but
nothing backs it. The target is a single Hostinger VPS — not a Kubernetes
cluster, not a managed queue service, not a multi-node fleet. `psycopg` (a
Postgres driver) is already a dependency for production; SQLite is the dev/
test default. `redis`, `celery`, and `rq` are **not** dependencies today.

## Decision

**The queue is the existing SQL database** (Postgres in production, SQLite
in dev/test) — a new `jobs` table (`app/models.py::Job`), written and read
exclusively through `app/services/job_service.py`. A separate OS process
(`python -m app.worker`, `app/worker/supervisor.py`) polls it, claims work
with a portable atomic conditional `UPDATE ... WHERE status='queued'`, and
runs each job in its own **`multiprocessing` "spawn" child process** —
genuine OS-level isolation, so a hang or an OCCT segfault can only ever take
down that one child, never the supervisor and never (since they were never
in the same process to begin with) the FastAPI app.

No Redis. No Celery. No RQ. No new network service to run, secure, monitor,
or keep alive on a resource-constrained VPS.

## Alternatives considered

**Redis + Celery/RQ.** The "default" choice for a Python job queue, and
genuinely a better fit *if* this were a multi-node deployment, needed very
high job throughput, or needed features a SQL table doesn't give you for
free (fine-grained priority queues, distributed rate limiting, a mature
scheduler UI). None of that is true here:

- One VPS. `job_worker_concurrency` (default 2) is sized for a VPS's core
  count, not a fleet.
- Expected volume is low — a personal/small-scale CAD generation service,
  not a high-throughput SaaS. `job_max_queue_depth` (default 50) is
  generous headroom, not a real ceiling being approached.
- Redis would be a **new service to deploy, secure, and keep alive**
  on the same VPS — another `systemctl status`, another thing that can run
  out of memory or fall over, another attack surface, another credential to
  rotate. "Do not introduce infrastructure complexity without evidence" —
  there is no evidence of a throughput or feature need Redis would satisfy
  that the database doesn't already satisfy for free.
- Postgres is *already* the production dependency. Reusing it means zero new
  moving parts, one set of backups/monitoring/credentials, and one fewer
  thing that can be down while the API is up (or vice versa).

**Keep the in-memory thread approach, just add a watchdog that actually
kills the thread.** Rejected outright: Python cannot forcibly terminate a
thread. There is no safe way to reclaim a thread stuck in a C extension
(OCCT) without terminating the whole process — which defeats the purpose.
Real isolation requires a real OS process boundary.

**A file-based queue (drop job JSON files in a directory, watch it).**
Rejected: reinvents transactional claim semantics (two workers must never
grab the same file) that a SQL row's atomic `UPDATE` already gives for
free, with weaker durability/consistency guarantees and no query capability
(list-my-jobs, queue-depth checks) without extra bookkeeping.

## Consequences

**Portability constraint accepted deliberately:** claiming a job cannot use
`SELECT ... FOR UPDATE SKIP LOCKED` (Postgres-only; SQLite has no such
clause), because dev/test run SQLite and production runs Postgres, and the
claim logic must be identical in both. Instead, `claim_next_job` reads a
small batch of candidate queued-job ids, then claims each with
`UPDATE jobs SET status='running', ... WHERE id=:id AND status='queued'` —
at most one caller's `UPDATE` can match a given row on either backend
(single-row atomicity is a base guarantee of both), so a race between two
workers resolves to exactly one winner with zero extra locking primitives.
This is measurably slower under very high contention than
`FOR UPDATE SKIP LOCKED` would be — an accepted tradeoff at this scale (a
handful of workers, not hundreds).

**No cross-node fan-out today.** A `Job` row's `worker_id` is
`hostname:pid`; nothing currently coordinates MULTIPLE Hostinger VPS
instances against the same database. If LunaiCAD ever needs horizontal
worker scale-out, the SAME `jobs` table and `claim_next_job` already work
correctly across multiple worker processes/hosts (that's exactly what the
atomic-claim design is for) — no schema change needed, only "run
`python -m app.worker` on a second box pointed at the same `DATABASE_URL`."
This decision is revisited if/when that need materializes with evidence
(sustained queue depth, not a guess).

**Test-mode inline execution.** There is no separate worker process running
under the pytest harness. `app.worker.runner.run_inline` runs a job's
handler synchronously, in-process, with explicitly **no** resource limits
applied (`resource.setrlimit` is irrevocable for the calling process's
remaining lifetime — it must never be applied to the test runner or a dev
API process). This is a deliberate, narrow, `settings.testing`-gated escape
hatch, not a production code path — the real isolation guarantees are
verified against genuine subprocesses in `tests/test_job_queue.py`, not
through this shortcut.

**Known frontend gap (accepted for this phase).** `POST /api/designs/create`
now returns a 202 `{job_id, status, poll}` for a job that doesn't finish
within `job_sync_wait_seconds` (default 45s) — the SAME contract shape the
drawing endpoints already used before this phase, and which the frontend
already knows how to poll (`frontend/src/lib/drawingJob.ts`). The design-
create call site (`frontend/src/lib/api.ts::createDesign`) has **not** been
updated to poll on a 202 in this phase; `job_sync_wait_seconds` is set high
enough that ordinary generations essentially never cross it, so the common
case is unaffected, but a genuinely slow generation degrades to a response
shape today's frontend can't parse rather than the clean polling flow the
API now supports. Generalizing `drawingJob.ts` into a job-type-agnostic
poller and wiring it into the create-design page is the natural, scoped
follow-up — deliberately not done here to keep this phase's blast radius to
the backend/infrastructure change it was scoped as.

## Related

- `app/models.py::Job`, `app/services/job_service.py` — the queue and its
  safe state machine (queued/running/validating/exporting/succeeded/failed/
  timed_out/cancelled).
- `app/worker/` — the supervisor, the subprocess entry point (resource
  limits, per-job temp dir), and job-type handlers.
- `docs/deployment.md` — local dev and Hostinger VPS deployment, including
  the systemd unit that runs the worker as its own process.
