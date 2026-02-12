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
MIN_STOP_LOSS_PCT: float = float(os.getenv("MIN_STOP_LOSS_PCT", "0.5"))
MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "10"))
MAX_POSITION_SIZE_PER_TRADE: int = int(os.getenv("MAX_POSITION_SIZE_PER_TRADE", "500"))
MAX_QTY_PER_ORDER: int = int(os.getenv("MAX_QTY_PER_ORDER", "10000"))
MAX_TOTAL_EXPOSURE_PERCENT: float = float(
    os.getenv("MAX_TOTAL_EXPOSURE_PERCENT", "80.0")
)
DAILY_LOSS_LIMIT: float = float(os.getenv("DAILY_LOSS_LIMIT", "50000"))
SIGNAL_COOLDOWN_TICKS: int = int(os.getenv("SIGNAL_COOLDOWN_TICKS", "100"))
STRATEGY_COOLDOWN_CANDLES: int = int(os.getenv("STRATEGY_COOLDOWN_CANDLES", "5"))
ORDER_TIMEOUT_SEC: int = int(os.getenv("ORDER_TIMEOUT_SEC", "60"))

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

# ---------------------------------------------------------------------------
# Trading mode  ("demo" | "paper" | "live")
# ---------------------------------------------------------------------------
# demo  — synthetic data generator + random indices allowed
# paper — real market data feeds, simulated execution (no real money)
# live  — real data + real broker execution (DANGER)
MODE: str = os.getenv("MODE", "demo")

# ---------------------------------------------------------------------------
# Engine safety guards
# ---------------------------------------------------------------------------
# Kill-switch: if True the engine will refuse to start
KILL_SWITCH: bool = os.getenv("KILL_SWITCH", "false").lower() == "true"
# Maximum % of capital lost in a single day before engine auto-stops
MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))

# ---------------------------------------------------------------------------
# Signal cooldown (time-based, complements tick-based cooldown)
# ---------------------------------------------------------------------------
SIGNAL_COOLDOWN_SEC: float = float(os.getenv("SIGNAL_COOLDOWN_SEC", "30.0"))

# ---------------------------------------------------------------------------
# Risk: position-size explosion guards
# ---------------------------------------------------------------------------
MIN_STOP_DISTANCE_PCT: float = float(os.getenv("MIN_STOP_DISTANCE_PCT", "0.5"))
MAX_POSITION_SIZE_PCT_OF_CAPITAL: float = float(
    os.getenv("MAX_POSITION_SIZE_PCT_OF_CAPITAL", "10.0")
)
ABSOLUTE_MAX_QTY: int = int(os.getenv("ABSOLUTE_MAX_QTY", "5000"))
