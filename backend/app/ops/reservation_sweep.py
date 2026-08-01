"""Stale budget-reservation sweep (docs/operations/cost-control-architecture.md).

Releases (fully refunds) any BudgetReservation still 'reserved' past its
COST_RESERVATION_TTL_SECONDS -- the restart-safety net for a process that
died between reserve and commit/release, same role as
app.services.job_service.reap_stale_jobs plays for orphaned Job rows. The
admin API (POST /api/admin/cost/reservations/release-stale) can also trigger
this on demand; this script is for the unattended, scheduled path.

Run via systemd timer on the VPS (deploy/systemd/lunaicad-reservation-sweep.timer):
    cd /opt/lunaicad/backend && .venv/bin/python -m app.ops.reservation_sweep
"""
from __future__ import annotations

from app.cost_control.service import reap_stale_reservations
from app.database import SessionLocal
from app.observability import log_event


def main() -> None:
    db = SessionLocal()
    try:
        count = reap_stale_reservations(db)
        log_event("reservation_sweep_completed", released=count)
        print(f"Released {count} stale reservation(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
