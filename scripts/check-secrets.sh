#!/usr/bin/env bash
# Pre-commit secrets guard for SourceCAD.
#
#   bash scripts/check-secrets.sh
#
# 1) Confirms the env files that hold real secrets are gitignored.
# 2) Scans every file that WOULD be committed (tracked + untracked-not-ignored)
#    for things that look like live credentials, and fails if any are found.
#
# Exit 0 = clean, 1 = a likely secret (or an un-ignored env file) was detected.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

fail=0

# --- 1) Secret-bearing files must be gitignored ----------------------------
for f in backend/.env frontend/.env.local frontend/.env.production.local; do
  if [ -e "$f" ] && ! git check-ignore -q "$f"; then
    red "NOT IGNORED: $f is present and would be committed. Add it to .gitignore."
    fail=1
  fi
done

# --- 2) Scan would-be-committed files for live-looking credentials ---------
# Patterns: OpenAI keys, Anthropic keys, AWS access key ids, generic
# 'private key' blocks. The placeholder examples (sk-..., sk-ant-...) are short
# and excluded by the length requirement in the patterns.
PATTERNS='(sk-ant-[A-Za-z0-9_-]{20,})|(sk-[A-Za-z0-9]{20,})|(AKIA[0-9A-Z]{16})|(-----BEGIN [A-Z ]*PRIVATE KEY-----)'

# Skip files where matches are expected/benign.
is_skipped() {
  case "$1" in
    *.example|*.md|*lock*|*/check-secrets.sh) return 0 ;;
    *) return 1 ;;
  esac
}

hits=0
while IFS= read -r -d '' file; do
  is_skipped "$file" && continue
  if LC_ALL=C grep -InE "$PATTERNS" "$file" >/dev/null 2>&1; then
    red "POSSIBLE SECRET in: $file"
    LC_ALL=C grep -InE "$PATTERNS" "$file" | sed 's/^/    /' | cut -c1-120
    hits=1
    fail=1
  fi
done < <(git ls-files -c -o --exclude-standard -z)

if [ "$hits" -eq 0 ]; then
  green "No live-looking credentials found in would-be-committed files."
fi

if [ "$fail" -ne 0 ]; then
  echo
  red "Secrets check FAILED. Remove/rotate the secret and keep it in an ignored .env."
  exit 1
fi

green "Secrets check passed."
