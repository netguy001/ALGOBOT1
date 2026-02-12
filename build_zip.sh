#!/usr/bin/env bash
# =============================================================
# build_zip.sh — Package the project into algo_demo_india.zip
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

ZIP_NAME="algo_demo_india.zip"

# Remove old zip if present
[ -f "$ZIP_NAME" ] && rm "$ZIP_NAME"

echo "Building $ZIP_NAME …"

zip -r "$ZIP_NAME" . \
    -x ".git/*" \
    -x "venv/*" \
    -x "__pycache__/*" \
    -x "*/__pycache__/*" \
    -x "*.pyc" \
    -x ".env" \
    -x "data/*.csv" \
    -x "data/*.db" \
    -x "logs/*" \
    -x "app/ml/models/*.json" \
    -x "app/backtest/reports/*" \
    -x "$ZIP_NAME"

echo "Done → $ZIP_NAME ($(du -h "$ZIP_NAME" | cut -f1))"
