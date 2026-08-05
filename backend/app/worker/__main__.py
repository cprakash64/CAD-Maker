"""Worker process entry point: ``python -m app.worker``.

A completely separate OS process from the FastAPI app (docs/deployment.md
documents the systemd unit that runs this). SIGTERM (systemd stop / restart)
triggers a graceful drain: no NEW jobs are claimed, but in-flight ones are
allowed to finish (or hit their own hard timeout) before this process exits.
"""
from __future__ import annotations

import signal
import sys

from app.observability import log_event
from app.worker.supervisor import Supervisor


def main() -> int:
    supervisor = Supervisor()

    def _handle_signal(signum, _frame) -> None:
        log_event("worker_signal_received", signum=signum)
        supervisor.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        supervisor.run_forever()
    except Exception:  # noqa: BLE001 - a crashed supervisor must still exit
        # cleanly so systemd's Restart=always brings up a fresh one; never a
        # silent hang.
        log_event("worker_supervisor_crashed")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
