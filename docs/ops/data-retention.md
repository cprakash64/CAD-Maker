# Data retention policy

## Prompts (the "proprietary prompts" requirement)

**Ordinary telemetry never carries the raw prompt** — see
`docs/ops/observability.md`'s redaction section. `log_design_telemetry`
(`app.services.design_service`) logs `prompt_hash` (a non-reversible
fingerprint), never `prompt`.

The full prompt text is retained in exactly one place: the `designs.prompt`
column. This IS the "access-controlled product storage under an explicit
retention policy" the product needs:

- **Access control**: every read goes through
  `design_service.get_owned_design` / the `_owned_or_404` pattern used
  throughout `app.routers.designs` — a JOIN filter on `Project.user_id`
  enforced by the query itself, not a post-hoc check. A design (and its
  prompt) is only ever readable by its owner. There is no admin/support
  back-door route that reads prompts today; support debugging a specific
  user's issue needs that user's own session or a DB-level query run by an
  operator with production DB access (itself gated by the secret-rotation /
  access controls in `docs/ops/deployment-runbook.md`).
- **Retention window**: `ARTIFACT_RETENTION_DAYS` (default 180) governs how
  long a design's generated EXPORT FILES are kept before the retention sweep
  reclaims them (see below) — but note this does **not** delete the prompt
  or the design row itself. There is currently no automatic deletion of the
  prompt/design row by age; it persists until the account is deleted.
- **Account deletion**: deleting a `User` cascades to `Project` →
  `Design` → (`ExportFile`, `ManufacturingCheck`, `Feedback`) via the ORM
  relationship `cascade="all, delete-orphan"` chain in `app.models` — a
  deleted account's prompts are fully removed, not soft-deleted. (There is
  no self-service account-deletion endpoint yet; this is the data-layer
  guarantee for when one is built, and for manual deletion requests handled
  by an operator today.)

## Uploaded drawings

Deleted as soon as the job that consumed them reaches a terminal state
(`app.worker.supervisor._cleanup_job_storage`, keyed off
`payload_json["upload_storage_key"]`). A drawing is never retained past the
job it was uploaded for — there's no "drawing history" feature, so there's
nothing to retain it for. If a job needs a retry after that point (e.g. an
operator manually re-queues something), the source file must be re-uploaded.

## Generated artifacts (STL/STEP/preview)

Governed by `ARTIFACT_RETENTION_DAYS` (default 180, 0 disables) and
`app.ops.retention_sweep`:

- A design whose `updated_at` is older than the retention window has its
  `ExportFile` rows and their backing storage objects deleted.
- The `Design` row itself (prompt, `spec_json`, preview mesh) is **never**
  touched by the sweep — only the regenerable manufacturable files. Opening
  the design again (or calling `POST /api/designs/{id}/export`) rebuilds
  them from `spec_json` on demand, deterministically (`app.export.exporter`
  — "same spec in → same geometry out, no LLM").
- Run nightly via the committed systemd timer
  (`deploy/systemd/lunaicad-retention.service` + `.timer`, installed per
  `docs/deployment.md`'s systemd section) at 03:00 server time — before the
  nightly DB backup's 03:05 slot (`docs/ops/backup-and-restore.md`), so the
  backup reflects post-sweep state. Verify scheduling with
  `systemctl list-timers lunaicad-retention.timer`. Cron fallback if a box
  can't run systemd timers:
  ```cron
  0 3 * * *  cd /opt/lunaicad/backend && .venv/bin/python -m app.ops.retention_sweep >> /var/log/lunaicad-retention.log 2>&1
  ```
- Dry-run first when changing the retention window:
  `python -m app.ops.retention_sweep --dry-run --retention-days 90`

## Storage quota interaction

`STORAGE_QUOTA_MB_PER_USER` (default 4096 MB, 0 disables) is checked at
generation time (`job_service._check_quotas`), summing `ExportFile.size_bytes`
across a user's designs. The retention sweep is the mechanism that actually
frees quota headroom for an account that's hit its cap without deleting
anything itself — it's the "delete some exports" the quota's error message
points a user toward, except it happens automatically on a schedule rather
than requiring the user to manually delete designs.

## Telemetry log retention

Structured logs (`journalctl`) are subject to the OS/journald's own rotation
policy (`journalctl --vacuum-time=` / `--vacuum-size=` — not application
code). Recommended: 30 days on-box, matching most incident-investigation
windows; longer-term log retention is a log-aggregator concern (see
`docs/ops/observability.md`'s note on log shipping), out of scope for this
phase.
