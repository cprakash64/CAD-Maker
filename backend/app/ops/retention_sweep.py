"""Artifact retention sweep (docs/ops/backup-and-retention.md).

Reclaims storage for STL/STEP export files on designs that haven't been
touched in `settings.artifact_retention_days` days. The Design row itself
(prompt, spec, preview mesh) is NEVER deleted by this sweep — only the
heavier, regenerable manufacturable export files. A swept design can still be
viewed and re-exported (regenerating STL/STEP on demand) at any time; this
only reclaims disk/object storage for artifacts nobody has touched in a long
time, it never destroys product data.

Run via cron on the VPS (see docs/ops/deployment-runbook.md):
    0 3 * * *  cd /opt/lunaicad/backend && .venv/bin/python -m app.ops.retention_sweep

Dry-run first to see what WOULD be reclaimed:
    python -m app.ops.retention_sweep --dry-run
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Design, ExportFile
from app.observability import log_event


@dataclass
class SweepResult:
    dry_run: bool
    designs_swept: int = 0
    files_reclaimed: int = 0
    bytes_reclaimed: int = 0
    errors: list[str] = field(default_factory=list)


def sweep(*, retention_days: int | None = None, dry_run: bool = False) -> SweepResult:
    days = settings.artifact_retention_days if retention_days is None else retention_days
    result = SweepResult(dry_run=dry_run)
    if days <= 0:
        log_event("retention_sweep_disabled")
        return result

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        # Only designs with at least one export left to reclaim — an
        # already-swept design is naturally skipped on the next run.
        design_ids = db.scalars(
            select(Design.id)
            .join(ExportFile, ExportFile.design_id == Design.id)
            .where(Design.updated_at < cutoff)
            .distinct()
        ).all()

        for design_id in design_ids:
            exports = list(db.scalars(
                select(ExportFile).where(ExportFile.design_id == design_id)))
            if not exports:
                continue
            design_bytes = sum(e.size_bytes or 0 for e in exports)
            if dry_run:
                result.designs_swept += 1
                result.files_reclaimed += len(exports)
                result.bytes_reclaimed += design_bytes
                continue

            from app.storage.storage import StorageError, get_storage

            storage = get_storage()
            for export in exports:
                try:
                    storage.delete(export.storage_key)
                except StorageError as exc:
                    result.errors.append(f"{export.storage_key}: {exc}")
                    continue
                db.delete(export)
            db.commit()
            result.designs_swept += 1
            result.files_reclaimed += len(exports)
            result.bytes_reclaimed += design_bytes

        from app.metrics import storage_cleanup_bytes_reclaimed_total

        if not dry_run and result.bytes_reclaimed:
            storage_cleanup_bytes_reclaimed_total.inc(result.bytes_reclaimed)
        log_event(
            "retention_sweep_completed",
            dry_run=dry_run, retention_days=days,
            designs_swept=result.designs_swept,
            files_reclaimed=result.files_reclaimed,
            bytes_reclaimed=result.bytes_reclaimed,
            errors=len(result.errors),
        )
        return result
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be reclaimed without deleting anything.")
    parser.add_argument("--retention-days", type=int, default=None,
                        help="Override settings.artifact_retention_days for this run.")
    args = parser.parse_args()

    result = sweep(retention_days=args.retention_days, dry_run=args.dry_run)
    label = "Would reclaim" if args.dry_run else "Reclaimed"
    print(
        f"{label}: {result.files_reclaimed} files "
        f"({result.bytes_reclaimed / 1024 / 1024:.2f} MB) across "
        f"{result.designs_swept} designs."
    )
    if result.errors:
        print(f"{len(result.errors)} storage errors (see logs).")


if __name__ == "__main__":
    main()
