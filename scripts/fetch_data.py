"""
scripts/fetch_data.py
=====================
Download historical OHLCV for default Indian tickers and store as CSVs.

Usage::

    python scripts/fetch_data.py
"""

import sys
from pathlib import Path

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.utils.data import fetch_default_symbols

if __name__ == "__main__":
    print("Downloading historical data for default Indian tickers…")
    fetch_default_symbols()
    print("Done.  CSVs saved under data/")
