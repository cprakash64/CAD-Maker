#!/usr/bin/env bash
# SourceCAD local dev servers.
#
#   scripts/dev.sh backend    # FastAPI on http://127.0.0.1:8000 (reload-safe)
#   scripts/dev.sh frontend   # Next.js on http://localhost:3000
#   scripts/dev.sh            # both (backend in the background, frontend in front)
#
# The backend watches ONLY backend/app for reloads. Watching the whole backend
# directory caused a reload loop: every generated STEP/STL written to
# backend/storage_data, every SQLite commit to cadmaker.db, and any change in
# .venv/site-packages made WatchFiles restart the server MID-REQUEST, which the
# browser surfaced as "TypeError: Failed to fetch".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${SOURCECAD_HOST:-127.0.0.1}"
PORT="${SOURCECAD_PORT:-8000}"

backend() {
  cd "$ROOT/backend"
  # NB on uvicorn reload semantics (uvicorn/supervisors/watchfilesreload.py):
  #  * uvicorn ALWAYS watches the CWD tree — a --reload-dir inside the CWD is
  #    dropped, so the whole backend/ dir is watched no matter what.
  #  * a directory exclude only works as an ABSOLUTE EXISTING path: uvicorn
  #    checks `exclude_dir in event_path.parents` against absolute event paths
  #    (relative names and ".venv/*" globs never match a deep change), and an
  #    absolute path that does NOT exist crashes its pattern glob.
  local args=(
    --host "$HOST" --port "$PORT"
    --reload
    --reload-dir app
    --reload-exclude '__pycache__'
    --reload-exclude '*.db'
    --reload-exclude '*.stl'
    --reload-exclude '*.step'
    --reload-exclude '*.log'
  )
  local d
  for d in .venv .pytest_cache node_modules storage_data eval_reports reports tmp tests; do
    [ -d "$ROOT/backend/$d" ] && args+=(--reload-exclude "$ROOT/backend/$d")
  done
  exec .venv/bin/python -m uvicorn app.main:app "${args[@]}"
}

frontend() {
  cd "$ROOT/frontend"
  npm run dev
}

case "${1:-both}" in
  backend)  backend ;;
  frontend) frontend ;;
  both)
    ( cd "$ROOT" && "$0" backend ) &
    BACK_PID=$!
    trap 'kill "$BACK_PID" 2>/dev/null || true' EXIT INT TERM
    frontend
    ;;
  *)
    echo "usage: scripts/dev.sh [backend|frontend|both]" >&2
    exit 1
    ;;
esac
