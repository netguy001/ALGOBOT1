"""
app/ml/predictor.py
===================
Load a trained XGBoost model and expose a prediction function.

The primary entry point is ``predict_proba(symbol, price)`` which returns
the probability of an up-move (0.0–1.0).
"""

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app.config import ML_MODEL_PATH
from app.utils.data import load_cached_ohlcv, resolve_symbol
from app.utils.indicators import sma, rsi, ema, atr, momentum

logger = logging.getLogger(__name__)

_model = None
_feature_cols: list[str] = []


def _load_model():
    """Lazy-load XGBoost model and feature metadata."""
    global _model, _feature_cols

    if _model is not None:
        return

    if not ML_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model file not found at {ML_MODEL_PATH}. "
            "Run 'python -m app.ml.trainer' first."
        )

    try:
        import xgboost as xgb
    except ImportError:
        raise ImportError("xgboost is required for ML predictions")

    _model = xgb.XGBClassifier()
    _model.load_model(str(ML_MODEL_PATH))

    meta_path = ML_MODEL_PATH.with_suffix(".meta.json")
    if meta_path.exists():
        with open(meta_path) as f:
            _feature_cols = json.load(f).get("feature_cols", [])

    logger.info("XGBoost model loaded (%d features)", len(_feature_cols))


def _build_latest_features(symbol: str) -> Optional[np.ndarray]:
    """
    Build the feature vector for the latest data point of ``symbol``.
    Returns 1-D numpy array or None if data is insufficient.
    """
    yf_sym = resolve_symbol(symbol)
    df = load_cached_ohlcv(yf_sym)
    if df.empty or len(df) < 60:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    feat = {}
    feat["sma_10"] = sma(close, 10).iloc[-1]
    feat["sma_20"] = sma(close, 20).iloc[-1]
    feat["sma_50"] = sma(close, 50).iloc[-1]
    feat["ema_12"] = ema(close, 12).iloc[-1]
    feat["ema_26"] = ema(close, 26).iloc[-1]
    feat["price_sma20_ratio"] = close.iloc[-1] / feat["sma_20"] if feat["sma_20"] else 1
    feat["sma10_sma50_ratio"] = feat["sma_10"] / feat["sma_50"] if feat["sma_50"] else 1
    feat["rsi_14"] = rsi(close, 14).iloc[-1]
    feat["momentum_10"] = momentum(close, 10).iloc[-1]
    feat["atr_14"] = atr(high, low, close, 14).iloc[-1]
    feat["daily_return"] = close.pct_change().iloc[-1]
    feat["return_std_10"] = close.pct_change().rolling(10).std().iloc[-1]
    feat["volume_sma_10"] = sma(volume.astype(float), 10).iloc[-1]
    feat["volume_ratio"] = (
        float(volume.iloc[-1]) / feat["volume_sma_10"] if feat["volume_sma_10"] else 1
    )

    if _feature_cols:
        row = [feat.get(c, 0) for c in _feature_cols]
    else:
        row = list(feat.values())

    return np.array(row, dtype=float).reshape(1, -1)


def predict_proba(symbol: str, price: float = 0.0) -> Optional[float]:
    """
    Return probability of an up-move for ``symbol``.

    Parameters
    ----------
    symbol : str  Yahoo-format or plain India ticker
    price  : float  current price (unused in feature set but kept for API compat)

    Returns
    -------
    float  probability in [0, 1] or None if prediction is unavailable
    """
    _load_model()

    features = _build_latest_features(symbol)
    if features is None:
        logger.warning("Insufficient data for prediction on %s", symbol)
        return None

    try:
        proba = _model.predict_proba(features)[0]
        # proba is [P(down), P(up)]
        return float(proba[1])
    except Exception as exc:
        logger.error("Prediction failed: %s", exc)
        return None
