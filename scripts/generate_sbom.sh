#!/usr/bin/env bash
# Regenerate the CycloneDX SBOMs (docs/ops/security-scanning.md).
#
# Backend SBOM is built from the ACTUAL installed environment (transitive
# deps included, not just the direct pins in requirements.txt) via
# `cyclonedx-py environment`. Frontend uses npm's built-in `npm sbom`
# (CycloneDX support since npm 9.5).
#
# Run from the repo root:
#   ./scripts/generate_sbom.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/sbom"

echo "-> Backend SBOM (from $ROOT/backend/.venv)"
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
    echo "   backend/.venv not found -- create it and pip install -r requirements.txt first."
    exit 1
fi
"$ROOT/backend/.venv/bin/pip" show cyclonedx-bom > /dev/null 2>&1 || \
    "$ROOT/backend/.venv/bin/pip" install cyclonedx-bom > /dev/null
"$ROOT/backend/.venv/bin/cyclonedx-py" environment "$ROOT/backend/.venv/bin/python" \
    -o "$ROOT/sbom/backend-sbom.cdx.json" --output-format json

echo "-> Frontend SBOM (npm sbom --sbom-format cyclonedx)"
(cd "$ROOT/frontend" && npm sbom --sbom-format cyclonedx > "$ROOT/sbom/frontend-sbom.cdx.json")

echo "Done. sbom/backend-sbom.cdx.json and sbom/frontend-sbom.cdx.json updated."
