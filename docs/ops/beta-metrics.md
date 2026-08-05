# Controlled-beta quality metrics

The exact query for each metric required for a controlled beta
(`docs/release-readiness-report.md`). Most are ratios of counters already in
`app/metrics.py` (`/metrics`, Prometheus text, `OPS_API_TOKEN`-gated — see
`docs/ops/observability.md`); two (time-to-first-useful-design, seven-day
return rate) are per-user cohort measures better expressed as SQL against the
`designs`/`users` tables directly, since they're not well shaped as a live
Prometheus ratio. Run the SQL queries read-only against a replica or with
`db.execute(text(...))` in a one-off script — never against the primary in a
way that competes with request traffic.

## 1. Validated generation rate

Share of generation attempts that reach a `pass` (or `warning`) validation
status, not `critical_failure`.

```promql
sum(design_validation_total{status=~"pass|warning"}) / sum(design_validation_total)
```

## 2. Download rate

Share of created designs that get at least one file downloaded. `design_downloads_total`
counts download EVENTS, not distinct designs, so for a precise per-design
rate use the SQL form; the PromQL form is a good live proxy.

```promql
sum(design_downloads_total) / sum(design_requests_total)
```

```sql
-- Precise: distinct designs with >=1 export download, via the audit trail
-- that log_design_telemetry's "design_exported" event writes to structured
-- logs (not a DB table) -- if a table-level source is needed, downloads are
-- not currently persisted as rows (only as a Counter + a log event). Until/
-- unless a DownloadEvent table exists, treat the PromQL ratio above as
-- authoritative and cross-check against log volume for `design_exported`.
```

## 3. Bad-result rate

Share of feedback submissions that are a "report a bad result" (not just a
thumbs-down), relative to designs created.

```promql
sum(design_feedback_total{report="true"}) / sum(design_requests_total)
```

```sql
-- Per-category breakdown (which failure modes dominate):
SELECT jsonb_array_elements_text(categories) AS category, count(*)
FROM feedback WHERE is_bad_result_report = true
GROUP BY 1 ORDER BY 2 DESC;
-- (SQLite: categories is stored as a JSON array via the SAJSON type; use
-- json_each(categories) instead of jsonb_array_elements_text.)
```

## 4. Modification success

Share of edit attempts (`regenerate`, `modify_prompt`, `face_edit_plan`,
`localized_edit`, ...) that land on `pass`.

```promql
sum(design_edit_total{outcome="pass"}) / sum(design_edit_total)
```

Per edit-kind breakdown (which edit type is least reliable):

```promql
sum by (kind) (design_edit_total{outcome="pass"}) / sum by (kind) (design_edit_total)
```

## 5. Time to first useful design

Per user, wall-clock time from account creation to their first design that
reached a validated (`pass`/`warning`) generated outcome. A cohort measure,
not a live counter — compute from timestamps already stored on `users`/`designs`.

```sql
WITH first_success AS (
  SELECT p.user_id, min(d.created_at) AS first_success_at
  FROM designs d
  JOIN projects p ON p.id = d.project_id
  WHERE d.route NOT IN ('needs_decomposition', 'needs_clarification')
    -- validation_status lives in semantic_json, not a column; this filters
    -- to designs that reached SOME generated outcome. Cross-check against
    -- design_validation_total{status=~"pass|warning"} in Prometheus for the
    -- live version of "generated and validated".
  GROUP BY p.user_id
)
SELECT
  avg(EXTRACT(EPOCH FROM (fs.first_success_at - u.created_at))) AS avg_seconds,
  percentile_cont(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (fs.first_success_at - u.created_at))
  ) AS median_seconds
FROM first_success fs JOIN users u ON u.id = fs.user_id;
```

## 6. Reported print success

Share of print outcomes users actually reported (via "Report a problem" →
print toggle, or a thumbs-up with a print report) that were successful. Only
counts designs where a user chose to report — never assume unreported means
success or failure.

```promql
sum(print_outcomes_total{outcome="success"}) / sum(print_outcomes_total)
```

```sql
-- Coverage: what fraction of downloaded designs ever get a print report
-- (tells you how much to trust the rate above -- low coverage = noisy):
SELECT
  (SELECT count(*) FROM feedback WHERE print_success IS NOT NULL) AS reported,
  (SELECT sum(value) FROM prometheus_scrape WHERE metric = 'design_downloads_total') AS downloaded;
```

## 7. Reported fit success

Same shape as print success.

```promql
sum(fit_outcomes_total{outcome="success"}) / sum(fit_outcomes_total)
```

## 8. Seven-day return rate

Share of users whose SECOND design (any design, not necessarily successful)
was created within 7 days of their first. Purely derived from `designs.created_at`
grouped by user — no separate "last active" field needed.

```sql
WITH ordered AS (
  SELECT p.user_id, d.created_at,
         row_number() OVER (PARTITION BY p.user_id ORDER BY d.created_at) AS rn
  FROM designs d JOIN projects p ON p.id = d.project_id
),
first_two AS (
  SELECT user_id,
         max(CASE WHEN rn = 1 THEN created_at END) AS first_at,
         max(CASE WHEN rn = 2 THEN created_at END) AS second_at
  FROM ordered WHERE rn <= 2
  GROUP BY user_id
)
SELECT
  count(*) FILTER (WHERE second_at IS NOT NULL
                    AND second_at <= first_at + INTERVAL '7 days') * 1.0
  / count(*) AS seven_day_return_rate
FROM first_two;
```

## 9. Cost per validated result

```promql
sum(llm_estimated_cost_usd_total) / sum(design_validation_total{status=~"pass|warning"})
```

## 10. Cost per downloaded result

```promql
sum(llm_estimated_cost_usd_total) / sum(design_downloads_total)
```

Caveat shared by 9 and 10: `llm_estimated_cost_usd_total` only accrues cost
for designs that actually called the LLM provider (`app/llm/pricing.py`) —
deterministic/template-routed designs (the majority of supported families,
per `docs/ops/beta-metrics.md`'s companion benchmark doc) cost ~$0 in this
sense, so these ratios understate true infra cost per result if you need a
fully-loaded number; they correctly isolate the LLM-specific cost driver
that's actually variable and controllable via the circuit breaker.

## Where each metric's raw ingredient comes from

| Metric | New instrumentation added | Why not new before |
|---|---|---|
| 1, 2, 9, 10 | None — pure ratios of existing counters | Already had the raw counters from the production-operations phase |
| 3 (bad-result rate) | `design_feedback_total{rating,report}`, `report_bad_result()` | No "this specific result was bad, here's why" capture existed distinct from a plain thumbs-down |
| 4 (modification success) | `design_edit_total{kind,outcome}`, incremented centrally in `log_design_telemetry` | Edit outcomes were logged as structured events but never aggregated as a queryable ratio |
| 5, 8 | None (SQL over existing timestamps) | Cohort measures, not counters — computed on demand, not worth a live gauge for a controlled beta's scale |
| 6, 7 (print/fit success) | `Feedback.print_success`/`fit_success` columns, `print_outcomes_total`/`fit_outcomes_total` | No physical-world outcome capture existed at all before the "report a result" workflow |
