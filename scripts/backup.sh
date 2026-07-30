#!/usr/bin/env bash
# Nightly full Postgres backup -- see docs/ops/backup-and-restore.md.
#
# Invoked by deploy/systemd/lunaicad-backup.timer (preferred) or a cron line
# (docs/ops/backup-and-restore.md's "Cron" section) -- same script either
# way. Requires DATABASE_URL in the environment (systemd unit sources it from
# backend/.env; for cron, source it explicitly before calling this script).
#
# Off-box copy: set BACKUP_REMOTE to an rclone remote (e.g.
# "remote:lunaicad-backups/") to additionally copy the dump off-box. A backup
# that lives next to the thing it's protecting isn't a backup -- if
# BACKUP_REMOTE is unset, this script still runs (local retention only) but
# logs a loud warning, since that's a real gap worth noticing in the logs.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"
BACKUP_DIR="${BACKUP_DIR:-/opt/lunaicad/backups}"
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/lunaicad_${STAMP}.dump"

pg_dump "$DATABASE_URL" -F c -f "$OUT"
echo "backup_written path=${OUT} bytes=$(stat -c%s "$OUT" 2>/dev/null || stat -f%z "$OUT")"

if [ -n "${BACKUP_REMOTE:-}" ]; then
  rclone copy "$OUT" "$BACKUP_REMOTE"
  echo "backup_copied_offbox remote=${BACKUP_REMOTE}"
else
  echo "WARNING: BACKUP_REMOTE not set -- this backup only exists on the" \
       "same box as the database it protects. Set BACKUP_REMOTE before relying" \
       "on this for disaster recovery (see docs/ops/backup-and-restore.md)." >&2
fi

find "$BACKUP_DIR" -name '*.dump' -mtime "+${RETAIN_DAYS}" -delete
echo "backup_retention_applied retain_days=${RETAIN_DAYS}"
