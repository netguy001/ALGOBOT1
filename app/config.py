"""
app/config.py
=============
Centralised configuration loaded from environment variables (.env file) with
sensible defaults.  Every tunable parameter lives here — no magic numbers
elsewhere in the codebase.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
FLASK_HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5005"))
FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"
SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key")

# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
DEFAULT_SYMBOLS: list[str] = os.getenv(
    "DEFAULT_SYMBOLS", "RELIANCE.NS,TCS.NS,INFY.NS"
).split(",")
TICK_INTERVAL_SEC: float = float(os.getenv("TICK_INTERVAL_SEC", "0.5"))
DATA_DIR: Path = _PROJECT_ROOT / os.getenv("DATA_DIR", "data")

# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
DEFAULT_STRATEGY: str = os.getenv("DEFAULT_STRATEGY", "sma_crossover")
SMA_SHORT: int = int(os.getenv("SMA_SHORT", "20"))
SMA_LONG: int = int(os.getenv("SMA_LONG", "50"))
RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD: int = int(os.getenv("RSI_OVERSOLD", "30"))
RSI_OVERBOUGHT: int = int(os.getenv("RSI_OVERBOUGHT", "70"))

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "1000000"))
RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
DEFAULT_STOP_LOSS_PCT: float = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "2.0"))
DEFAULT_TAKE_PROFIT_PCT: float = float(os.getenv("DEFAULT_TAKE_PROFIT_PCT", "4.0"))

# ---------------------------------------------------------------------------
# ML
# ---------------------------------------------------------------------------
ML_ENABLED: bool = os.getenv("ML_ENABLED", "false").lower() == "true"
ML_PROBABILITY_THRESHOLD: float = float(os.getenv("ML_PROBABILITY_THRESHOLD", "0.65"))
ML_MODEL_PATH: Path = _PROJECT_ROOT / "app" / "ml" / "models" / "xgb_model.json"

# ---------------------------------------------------------------------------
# Simulated broker
# ---------------------------------------------------------------------------
BROKER_MIN_LATENCY_MS: int = int(os.getenv("BROKER_MIN_LATENCY_MS", "200"))
BROKER_MAX_LATENCY_MS: int = int(os.getenv("BROKER_MAX_LATENCY_MS", "800"))
SLIPPAGE_PCT: float = float(os.getenv("SLIPPAGE_PCT", "0.05"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: str = os.getenv("LOG_FILE", "logs/app.log")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH: Path = _PROJECT_ROOT / "data" / "algo_demo.db"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = _PROJECT_ROOT
