"""Prometheus metrics for production operations (docs/ops/observability.md).

Counters/histograms are incremented at the same call sites that already emit
structured `log_event`s — metrics and logs stay in sync by construction rather
than one being derived from the other after the fact. DB-derived gauges
(queue depth, storage bytes) are computed live at scrape time via a custom
Collector, so they're never stale between scrapes.

Cost-per-request / cost-per-validated-result / cost-per-downloaded-result are
NOT precomputed here — they're ratios of two counters
(`llm_estimated_cost_usd_total` / `design_requests_total` etc.), which
Prometheus/Grafana compute directly via PromQL. See docs/ops/observability.md
for example queries and alert thresholds.

## Multiprocess deployments (`--workers N`)

The systemd unit (`deploy/systemd/lunaicad-backend.service`) runs uvicorn
with multiple worker PROCESSES. Plain in-memory Counters/Histograms/Gauges
are per-process — without special handling, a `/metrics` scrape would only
see whichever one worker happened to answer that request, silently
undercounting (and a Gauge could appear to jump around as different workers
answer consecutive scrapes). `prometheus_client` solves this with
"multiprocess mode": set `PROMETHEUS_MULTIPROC_DIR` to a writable, EMPTY-ON-
STARTUP directory before the app imports this module, and every Counter/
Histogram/Gauge below automatically switches to writing its value into an
mmap'd file in that directory instead of pure Python memory (this switch is
internal to prometheus_client, keyed off the env var — no code change needed
per-metric). `render_latest()` below then reads those files back and merges
across all worker PIDs via `multiprocess.MultiProcessCollector`, so ONE
scrape sees the true sum/max across every worker. See
`docs/ops/observability.md`'s "Multiprocess metrics" section.
"""
from __future__ import annotations

import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

REGISTRY = CollectorRegistry(auto_describe=True)

# --- HTTP / API -----------------------------------------------------------
http_requests_total = Counter(
    "http_requests_total", "API requests by route template and status class",
    ["method", "route", "status_class"], registry=REGISTRY,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "API request latency",
    ["route"], registry=REGISTRY,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)

# --- Auth / abuse -----------------------------------------------------------
auth_failures_total = Counter(
    "auth_failures_total", "Failed authentication attempts", ["reason"], registry=REGISTRY,
)
rate_limited_total = Counter(
    "rate_limited_total", "Requests rejected by rate limiting", ["category"], registry=REGISTRY,
)
quota_exceeded_total = Counter(
    "quota_exceeded_total", "Requests rejected by a per-account quota", ["quota"], registry=REGISTRY,
)

# --- Job queue / worker -----------------------------------------------------
worker_jobs_total = Counter(
    "worker_jobs_total", "Jobs reaching a terminal outcome",
    ["job_type", "outcome"], registry=REGISTRY,
)
worker_job_duration_seconds = Histogram(
    "worker_job_duration_seconds", "Job run time (claimed to finished)",
    ["job_type"], registry=REGISTRY,
    buckets=(1, 5, 15, 30, 60, 120, 180, 300, 600),
)
worker_job_queue_wait_seconds = Histogram(
    "worker_job_queue_wait_seconds", "Time a job spent queued before being claimed",
    ["job_type"], registry=REGISTRY,
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
worker_crashes_total = Counter(
    "worker_crashes_total", "Worker child processes that exited abnormally (signal)",
    ["signal"], registry=REGISTRY,
)

# --- Generation pipeline -----------------------------------------------------
design_requests_total = Counter(
    "design_requests_total", "Design generation attempts", ["outcome"], registry=REGISTRY,
)
design_validation_total = Counter(
    "design_validation_total", "Generated designs by validation status",
    ["status"], registry=REGISTRY,
)
design_export_total = Counter(
    "design_export_total", "Export file materialization attempts",
    ["fmt", "outcome"], registry=REGISTRY,
)
design_downloads_total = Counter(
    "design_downloads_total", "Manufacturable/preview file downloads",
    ["fmt"], registry=REGISTRY,
)
design_stage_duration_seconds = Histogram(
    "design_stage_duration_seconds", "Time spent per generation stage",
    ["stage"], registry=REGISTRY,
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
design_edit_total = Counter(
    "design_edit_total", "Edit/modification attempts by kind and resulting "
    "validation status -- modification success rate = pass / sum(all)",
    ["kind", "outcome"], registry=REGISTRY,
)

# --- Controlled-beta quality metrics ------------------------------------------
# See docs/ops/beta-metrics.md for the exact PromQL for each required
# controlled-beta measurement (validated/download/bad-result rate, cost per
# validated/downloaded result, etc); most are ratios of these counters and the
# existing design_requests_total/design_validation_total/design_downloads_total
# above, so no new instrumentation was needed for those.
design_feedback_total = Counter(
    "design_feedback_total", "Feedback submissions -- bad-result rate = "
    "report=\"true\" count / design_requests_total",
    ["rating", "report"], registry=REGISTRY,
)
print_outcomes_total = Counter(
    "print_outcomes_total", "User-reported physical print outcomes "
    "(from the 'report a result' workflow; only incremented when reported)",
    ["outcome"], registry=REGISTRY,
)
fit_outcomes_total = Counter(
    "fit_outcomes_total", "User-reported physical fit outcomes "
    "(from the 'report a result' workflow; only incremented when reported)",
    ["outcome"], registry=REGISTRY,
)

# --- LLM / cost --------------------------------------------------------------
llm_calls_total = Counter(
    "llm_calls_total", "LLM provider calls", ["model", "outcome"], registry=REGISTRY,
)
llm_tokens_total = Counter(
    "llm_tokens_total", "LLM tokens consumed", ["model", "kind"], registry=REGISTRY,
)
llm_estimated_cost_usd_total = Counter(
    "llm_estimated_cost_usd_total", "Estimated LLM spend (see app.llm.pricing)",
    ["model"], registry=REGISTRY,
)
llm_circuit_breaker_state = Gauge(
    "llm_circuit_breaker_state", "1 if the LLM circuit breaker is open (degraded mode), else 0",
    registry=REGISTRY,
    # The breaker is per-process state (see app.llm.circuit_breaker); "max"
    # means the merged multiprocess value is 1 (degraded) if ANY worker's
    # breaker is open, not an average/sum across workers.
    multiprocess_mode="max",
)

# --- Storage -----------------------------------------------------------------
storage_cleanup_bytes_reclaimed_total = Counter(
    "storage_cleanup_bytes_reclaimed_total", "Bytes reclaimed by the artifact-retention sweep",
    registry=REGISTRY,
)


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


class _DbGaugesCollector(Collector):
    """Live DB-derived gauges, computed fresh on every /metrics scrape (never
    stale, never held in a background thread). Best-effort: a DB hiccup during
    a scrape must never turn into a 500 on the metrics endpoint itself."""

    def collect(self):
        job_status = GaugeMetricFamily(
            "job_active_status_count", "Jobs currently in each active status "
            "(sum across all statuses == current queue depth)", labels=["status"])
        storage_bytes = GaugeMetricFamily(
            "storage_bytes_used", "Sum of ExportFile.size_bytes (logical storage usage)")
        db_pool = GaugeMetricFamily(
            "db_pool_connections", "SQLAlchemy connection pool state", labels=["state"])

        try:
            from sqlalchemy import func, select

            from app.database import SessionLocal, engine
            from app.models import ExportFile, Job
            from app.services.job_service import ACTIVE_STATUSES

            db = SessionLocal()
            try:
                counts = {s: 0 for s in ACTIVE_STATUSES}
                for status_, n in db.execute(
                    select(Job.status, func.count()).where(Job.status.in_(ACTIVE_STATUSES))
                    .group_by(Job.status)
                ).all():
                    counts[status_] = n
                for status_, n in counts.items():
                    job_status.add_metric([status_], n)

                total_bytes = db.scalar(select(func.coalesce(func.sum(ExportFile.size_bytes), 0)))
                storage_bytes.add_metric([], float(total_bytes or 0))
            finally:
                db.close()

            pool = engine.pool
            for state, value in (
                ("checked_out", pool.checkedout()),
                ("checked_in", pool.checkedin()),
            ):
                db_pool.add_metric([state], float(value))
        except Exception:  # noqa: BLE001 - a scrape must never 500
            pass

        yield job_status
        yield storage_bytes
        yield db_pool


REGISTRY.register(_DbGaugesCollector())


def render_latest() -> bytes:
    """The full /metrics body -- transparently merges per-process Counter/
    Histogram/Gauge values across every worker when PROMETHEUS_MULTIPROC_DIR
    is set (see this module's docstring), and always includes the DB-derived
    gauges (which have no per-process state to merge in the first place)."""
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return generate_latest(REGISTRY)

    from prometheus_client import multiprocess

    merged = CollectorRegistry()
    multiprocess.MultiProcessCollector(merged, path=multiproc_dir)
    merged.register(_DbGaugesCollector())
    return generate_latest(merged)
