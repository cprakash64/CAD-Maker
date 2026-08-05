# Data Retention Policy (user-facing) — DRAFT

> **DRAFT — REQUIRES QUALIFIED LEGAL REVIEW. THIS IS NOT LEGAL ADVICE.**
> This is the user-facing counterpart to `docs/ops/data-retention.md`, which
> is the internal operations document (backup schedule, restore drill,
> operator procedures). This document describes the same mechanics from the
> user's point of view; keep the two in sync if either changes.

## What is kept, and for how long

| Data | Retention | Deletion mechanism |
|---|---|---|
| Account (email, password hash) | Until account deletion | Self-service: `DELETE /api/auth/me` (immediate, permanent, requires password confirmation) |
| Design records (prompt, spec, dimensions, validation results, safety classification) | Until the design or account is deleted | Self-service per-design deletion, or account deletion (cascades to all owned designs) |
| Exported files (STL/STEP/GLB) | `artifact_retention_days` (configurable; see `backend/.env.example`) from last use, then reclaimed automatically | Automatic background sweep (`backend/app/ops/retention_sweep.py`) — reclaims the FILE, never the design record itself. Re-exporting a still-existing design regenerates the file on demand. |
| Structured logs | Per the logging infrastructure's own rotation/retention (operational, not currently a fixed user-facing SLA) | `[UNRESOLVED — REQUIRES LEGAL INPUT: confirm whether a specific log-retention period must be committed to publicly, e.g. for a jurisdiction's data-minimization requirement.]` |

## Self-service deletion

Every account can, at any time, from **Settings → Privacy & data**:

1. See exactly what's stored for their account (project/design counts,
   export storage used) — `GET /api/auth/privacy-summary`.
2. Change their data-improvement opt-in choice.
3. Permanently delete their account and everything it owns.

Account deletion is immediate and does not go through a queued/delayed
process in the current implementation.
`[UNRESOLVED — REQUIRES LEGAL INPUT: some jurisdictions expect (or a
company's own policy may want) a grace/cooldown period before permanent
deletion, or a distinction between "deactivation" and "deletion" — the
current implementation has only immediate, permanent deletion. Decide
whether that needs to change before this is published as policy.]`

## What is NOT currently offered

- **Data export/portability**: there is no "download all my data" endpoint
  today. `[UNRESOLVED — REQUIRES LEGAL INPUT: flag to counsel if a
  portability right requires this to be built.]`
- **Partial/selective deletion of log data**: logs are pseudonymized
  (see `docs/ops/observability.md`) but not user-deletable individually.
