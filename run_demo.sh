#!/usr/bin/env bash
# =============================================================
# run_demo.sh — One-command setup and launch for Algo Demo India
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "===== Algo Demo India — Setup & Launch ====="

# 1. Create virtual environment
if [ ! -d "venv" ]; then
    echo "[1/5] Creating virtual environment…"
    python3 -m venv venv
else
    echo "[1/5] Virtual environment already exists."
fi

# 2. Activate
echo "[2/5] Activating venv…"
source venv/bin/activate

# 3. Install dependencies
echo "[3/5] Installing dependencies…"
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4. Fetch sample data
echo "[4/5] Fetching sample data (RELIANCE.NS, TCS.NS, INFY.NS)…"
python scripts/fetch_data.py

# 5. Optional: train ML model
if [ "${1:-}" = "--use-ml" ]; then
    echo "[4b] Training ML model…"
    python -m app.ml.trainer
    ML_FLAG="--use-ml"
else
    ML_FLAG=""
fi

# 6. Start the application
echo "[5/5] Starting Flask app on http://localhost:5000 …"
python app/main.py $ML_FLAG
