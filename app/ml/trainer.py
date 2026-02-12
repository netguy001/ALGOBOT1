"""
app/ml/trainer.py
=================
Train an XGBoost classifier to predict whether the next-day close will be
higher (1) or lower (0) than today's close, using technical features
derived from Yahoo Finance OHLCV data.

Usage::

    python -m app.ml.trainer                 # train on RELIANCE.NS (default)
    python -m app.ml.trainer --symbol TCS.NS
    python -m app.ml.trainer --symbols RELIANCE.NS,TCS.NS,INFY.NS

The trained model is saved to ``app/ml/models/xgb_model.json``.
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import ML_MODEL_PATH, DEFAULT_SYMBOLS
from app.utils.data import download_ohlcv, load_cached_ohlcv, resolve_symbol
from app.utils.indicators import sma, rsi, ema, atr, momentum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create technical features from OHLCV DataFrame.

    Returns a DataFrame with feature columns + ``target`` column.
    """
    feat = pd.DataFrame(index=df.index)
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # Moving averages
    feat["sma_10"] = sma(close, 10)
    feat["sma_20"] = sma(close, 20)
    feat["sma_50"] = sma(close, 50)
    feat["ema_12"] = ema(close, 12)
    feat["ema_26"] = ema(close, 26)

    # Ratios
    feat["price_sma20_ratio"] = close / feat["sma_20"]
    feat["sma10_sma50_ratio"] = feat["sma_10"] / feat["sma_50"]

    # Momentum
    feat["rsi_14"] = rsi(close, 14)
    feat["momentum_10"] = momentum(close, 10)
    feat["atr_14"] = atr(high, low, close, 14)

    # Volatility
    feat["daily_return"] = close.pct_change()
    feat["return_std_10"] = feat["daily_return"].rolling(10).std()

    # Volume features
    feat["volume_sma_10"] = sma(volume.astype(float), 10)
    feat["volume_ratio"] = volume.astype(float) / feat["volume_sma_10"]

    # Target: 1 if tomorrow's close > today's close, else 0
    feat["target"] = (close.shift(-1) > close).astype(int)

    return feat.dropna()


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_model(symbols: list[str] | None = None) -> None:
    """Download data, engineer features, train XGBoost, and save model."""
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("xgboost not installed — run: pip install xgboost")
        return

    symbols = symbols or DEFAULT_SYMBOLS
    all_features = []

    for sym in symbols:
        yf_sym = resolve_symbol(sym)
        df = load_cached_ohlcv(yf_sym)
        if df.empty:
            df = download_ohlcv(yf_sym, period="2y")
        if df.empty:
            logger.warning("Skipping %s — no data", yf_sym)
            continue
        feat = _build_features(df)
        feat["symbol"] = yf_sym
        all_features.append(feat)

    if not all_features:
        logger.error("No data available for training")
        return

    combined = pd.concat(all_features, ignore_index=True)
    feature_cols = [c for c in combined.columns if c not in ("target", "symbol")]
    X = combined[feature_cols].values
    y = combined["target"].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False  # time-series: no shuffling
    )

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        eval_metric="logloss",
        use_label_encoder=False,
        random_state=42,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    # Evaluate
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    logger.info("Model accuracy on test set: %.2f%%", acc * 100)
    print(classification_report(y_test, y_pred, target_names=["DOWN", "UP"]))

    # Save
    ML_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(ML_MODEL_PATH))
    logger.info("Model saved to %s", ML_MODEL_PATH)

    # Also save feature column names for the predictor
    import json

    meta_path = ML_MODEL_PATH.with_suffix(".meta.json")
    with open(meta_path, "w") as f:
        json.dump({"feature_cols": feature_cols}, f)
    logger.info("Feature metadata saved to %s", meta_path)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Train XGBoost prediction model")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated Yahoo symbols",
    )
    args = parser.parse_args()
    train_model(args.symbols.split(","))
