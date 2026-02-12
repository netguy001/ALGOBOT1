# AlgoTerminal — Algorithmic Trading Demo for Indian Stock Market

> **Full-stack demo application** showcasing algorithmic trading system design for NSE/BSE stocks.
> Built with Python (Flask + SocketIO), TradingView charts, and a professional Binance-style dark UI.
> **This is a simulation/demo — not connected to real brokers or live market feeds.**

---

## Table of Contents

1. [What Is This?](#what-is-this)
2. [Architecture Overview](#architecture-overview)
3. [Complete Folder Structure](#complete-folder-structure)
4. [Trading Algorithms](#trading-algorithms)
5. [How Trades Work (Signal → Order Flow)](#how-trades-work-signal--order-flow)
6. [Data: Real-Time vs Simulated](#data-real-time-vs-simulated)
7. [ML Prediction Module](#ml-prediction-module)
8. [Backtesting Engine](#backtesting-engine)
9. [Risk Management](#risk-management)
10. [REST API Reference](#rest-api-reference)
11. [WebSocket Events](#websocket-events)
12. [Database Schema](#database-schema)
13. [Configuration Reference](#configuration-reference)
14. [How to Run](#how-to-run)
15. [Running Tests](#running-tests)
16. [Docker Deployment](#docker-deployment)
17. [Upgrading to Production-Ready Real Algo Trading](#upgrading-to-production-ready-real-algo-trading)
18. [Tech Stack](#tech-stack)

---

## What Is This?

AlgoTerminal is a **complete, interview-ready demo** of an algorithmic trading system for the Indian stock market. It demonstrates:

- **4 trading strategies** (SMA Crossover, RSI Mean Reversion, Donchian Breakout, Momentum)
- **Automatic trade execution** — strategies generate signals, orders are auto-placed with position sizing, SL, and TP
- **Manual order placement** — via REST API or the web UI order form
- **Simulated broker** — realistic latency (200–800 ms), partial fills, slippage, ~5 % rejection rate
- **ML prediction filter** — XGBoost model that gates low-confidence signals
- **Backtesting engine** — run any strategy on historical data with Sharpe, drawdown, and win-rate metrics
- **Real-time WebSocket dashboard** — TradingView candlestick charts, watchlist, order book, P&L tracking
- **SQLite persistence** — orders, trades, P&L history, strategy logs all persisted
- **Risk management** — position sizing, stop-loss, take-profit, drawdown tracking
- **21 unit tests** + GitHub Actions CI (lint + test)

### What It Is NOT

| | |
|-|-|
| ❌ | **Not connected to real brokers** — uses a simulated broker (but includes a Zerodha adapter template) |
| ❌ | **Not real-time market data** — replays historical / synthetic CSV data as ticks |
| ❌ | **Not production-grade** — no authentication, no HTTPS, no rate limiting, single-process |
| ❌ | **Not financial advice** — this is purely a technical demonstration |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      BROWSER (Frontend)                       │
│  TradingView Charts │ Watchlist │ Order Form │ P&L Panel      │
│                   Socket.IO + REST                            │
└──────────────┬──────────────────────┬────────────────────────┘
               │ WebSocket            │ HTTP
┌──────────────▼──────────────────────▼────────────────────────┐
│                     FLASK + SOCKETIO SERVER                    │
│                                                               │
│  ┌──────────┐   ┌───────────────┐   ┌────────────────────┐   │
│  │ REST API │   │ WebSocket Hub │   │  Tick Loop         │   │
│  │ /api/*   │   │ events: tick, │   │  (background       │   │
│  │          │   │ signal, pnl,  │   │   eventlet thread) │   │
│  │          │   │ order_update  │   │                    │   │
│  └────┬─────┘   └───────┬───────┘   └────────┬──────────┘   │
│       │                 │                     │               │
│  ┌────▼─────────────────▼─────────────────────▼───────────┐  │
│  │                STRATEGY ENGINE                          │  │
│  │  ┌─────────────┐ ┌────────────┐ ┌───────────────────┐  │  │
│  │  │SMA Crossover│ │RSI MeanRev │ │ Breakout / Moment │  │  │
│  │  └──────┬──────┘ └─────┬──────┘ └────────┬──────────┘  │  │
│  │         └───────────┬──┘                  │             │  │
│  │              Signal │ (optional ML gate)   │             │  │
│  └─────────────────────┼─────────────────────┘             │  │
│                        ▼                                      │
│  ┌────────────────────────────────────────────────────────┐   │
│  │               ORDER MANAGER                             │  │
│  │  Position sizing → SL/TP → Idempotency → Retry logic   │  │
│  └────────────────────────┬───────────────────────────────┘   │
│                           ▼                                    │
│  ┌────────────────────────────────────────────────────────┐   │
│  │            SIMULATED BROKER                             │   │
│  │  Latency (200–800ms) → Slippage → Partial fills        │   │
│  │  → Webhook callback → OrderManager updates status       │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           │                                    │
│                  ┌────────▼────────┐                          │
│                  │ SQLite Database  │                          │
│                  │ orders │ trades  │                          │
│                  │ pnl    │ logs    │                          │
│                  └─────────────────┘                          │
└───────────────────────────┬──────────────────────────────────┘
                            │
                 ┌──────────▼──────────┐
                 │   DATA LAYER         │
                 │  CSV (OHLCV replay)  │
                 │  yfinance download   │
                 │  Synthetic GBM       │
                 │  fallback            │
                 └──────────────────────┘
```

---

## Complete Folder Structure

```
algo_demo_india/
│
├── .env                          # Environment variables (port, symbols, strategy, risk, ML)
├── .env.example                  # Template with all available settings
├── requirements.txt              # Python dependencies (15 packages)
├── Dockerfile                    # Docker image (Python 3.11-slim)
├── docker-compose.yml            # Single-service compose with volume mounts
├── build_zip.sh                  # Package project as algo_demo_india.zip
├── run_demo.sh                   # One-command setup: venv → install → fetch data → run
├── postman_collection.json       # Postman collection for all REST endpoints
├── README.md                     # This file
│
├── app/                          # ═══════ BACKEND (Python) ═══════
│   ├── __init__.py
│   ├── config.py                 # Centralised config from .env (all tunable params)
│   ├── main.py                   # Flask entry point, component wiring, background tick loop
│   │
│   ├── strategy/                 # ─── Trading Strategies ───
│   │   ├── __init__.py
│   │   ├── engine.py             # Strategy manager: load/switch strategies, ML filter gate
│   │   └── strategies.py         # 4 strategy classes + STRATEGY_REGISTRY dict
│   │
│   ├── broker/                   # ─── Order Execution ───
│   │   ├── __init__.py
│   │   ├── order_manager.py      # Order lifecycle, position tracking, P&L, state machine
│   │   ├── simulated_broker.py   # Fake broker: latency, slippage, partial fills, webhooks
│   │   └── adapter_template.py   # Abstract BrokerAdapter ABC + Zerodha Kite skeleton
│   │
│   ├── utils/                    # ─── Utilities ───
│   │   ├── __init__.py
│   │   ├── data.py               # Yahoo Finance download, Synthetic GBM fallback, tick generator
│   │   ├── indicators.py         # SMA, EMA, RSI, BB, ATR, MACD, Momentum, Donchian
│   │   └── risk.py               # Position sizing, SL/TP calculation, DrawdownTracker
│   │
│   ├── routes/                   # ─── REST API ───
│   │   ├── __init__.py
│   │   ├── api.py                # /api/* endpoints (start, stop, orders, pnl, etc.)
│   │   └── webhook.py            # /webhook/order-update (broker callback receiver)
│   │
│   ├── ws/                       # ─── WebSocket ───
│   │   ├── __init__.py
│   │   └── socket_server.py      # Socket.IO event handlers (control, ping, request_state)
│   │
│   ├── ml/                       # ─── Machine Learning ───
│   │   ├── __init__.py
│   │   ├── trainer.py            # XGBoost trainer (15 features → binary classifier)
│   │   ├── predictor.py          # Load model & predict P(up-move) for signal filtering
│   │   └── models/               # Saved XGBoost model + feature metadata JSON
│   │
│   ├── backtest/                 # ─── Backtesting ───
│   │   ├── __init__.py
│   │   ├── backtester.py         # Run strategies on historical data, Sharpe/DD/WR metrics
│   │   └── reports/              # JSON reports + equity curve PNGs
│   │
│   └── db/                       # ─── Database ───
│       ├── __init__.py
│       └── storage.py            # SQLite DAL: 4 tables, WAL mode, thread-safe
│
├── frontend/                     # ═══════ FRONTEND ═══════
│   ├── index.html                # Single-page trading terminal UI (276 lines)
│   ├── assets/                   # Placeholder for images / icons
│   └── static/
│       ├── app.js                # Dashboard JS: chart, watchlist, WebSocket, order form
│       └── style.css             # Binance-style dark theme (1000+ lines CSS)
│
├── scripts/                      # ═══════ SCRIPTS ═══════
│   └── fetch_data.py             # Download OHLCV for all default symbols
│
├── tests/                        # ═══════ TESTS ═══════
│   ├── __init__.py
│   └── test_strategy.py          # 21 unit tests (indicators, risk, orders, strategies)
│
├── data/                         # ═══════ DATA (auto-generated) ═══════
│   ├── RELIANCE_NS_1d.csv        # 500 rows daily OHLCV
│   ├── TCS_NS_1d.csv
│   ├── INFY_NS_1d.csv
│   ├── HDFCBANK_NS_1d.csv
│   ├── SBIN_NS_1d.csv
│   └── algo_demo.db              # SQLite database
│
├── logs/                         # ═══════ LOGS ═══════
│   └── app.log                   # Rotating log file (5 MB × 3 backups)
│
└── .github/workflows/
    └── ci.yml                    # GitHub Actions: flake8 lint + pytest on every push
```

### What Each File Does

| File | Lines | Purpose |
|------|-------|---------|
| `app/config.py` | 81 | Loads ALL settings from `.env` with typed defaults. Single source of truth. |
| `app/main.py` | 257 | Creates Flask + SocketIO app, wires components, runs background tick loop that streams prices. |
| `app/strategy/engine.py` | 153 | Manages active strategy, feeds ticks, applies ML filter, forwards signals to OrderManager. |
| `app/strategy/strategies.py` | 236 | 4 strategy classes sharing `.on_tick()` interface + `STRATEGY_REGISTRY` dict. |
| `app/broker/order_manager.py` | 361 | Order lifecycle: signal → order (risk-sized), state machine, position & P&L tracking, retry logic. |
| `app/broker/simulated_broker.py` | 149 | Fake broker with async queue, latency/slippage/partial fills, fires webhook on state change. |
| `app/broker/adapter_template.py` | 103 | Abstract `BrokerAdapter` ABC + Zerodha Kite skeleton for real-broker migration. |
| `app/utils/data.py` | 261 | Yahoo Finance download, Geometric Brownian Motion fallback, tick generator (CSV row replay). |
| `app/utils/indicators.py` | 98 | Pure functions: SMA, EMA, RSI (Wilder), BB, ATR, MACD, Momentum, Donchian Channel. |
| `app/utils/risk.py` | 107 | Position sizing formula, SL/TP price calc, `DrawdownTracker` class. |
| `app/routes/api.py` | 156 | REST endpoints: start/stop engine, place/cancel orders, get positions/P&L/status/ML predict. |
| `app/routes/webhook.py` | 89 | Receives POST from simulated broker, updates order status, broadcasts via WebSocket. |
| `app/ws/socket_server.py` | 82 | Socket.IO handlers: `control`, `ping`, `request_state`. Client↔Server real-time comm. |
| `app/ml/trainer.py` | 158 | Trains XGBoost classifier (15 technical features → predict next-day direction). CLI tool. |
| `app/ml/predictor.py` | 113 | Loads trained model, computes P(up-move) for live signal filtering. |
| `app/backtest/backtester.py` | 269 | Iterates historical data through strategies, manages SL/TP exits, calculates Sharpe/DD/WR. CLI tool. |
| `app/db/storage.py` | 230 | SQLite DAL with 4 tables. WAL journal mode. Thread-safe via module-level lock. |
| `frontend/index.html` | 276 | 3-column layout: watchlist, TradingView chart + tabs, P&L/order form/strategy sidebar. |
| `frontend/static/app.js` | 798 | Tick→candle conversion, chart overlays (SMA/EMA/BB), WebSocket handling, order form, watchlist. |
| `frontend/static/style.css` | 1003 | Binance-inspired dark theme, CSS Grid layout, animations, responsive breakpoints. |
| `tests/test_strategy.py` | 259 | 21 tests: indicators, risk sizing, order state machine, strategy signals. |
| `scripts/fetch_data.py` | 23 | CLI: downloads OHLCV for all `DEFAULT_SYMBOLS`. |

---

## Trading Algorithms

### 1. SMA Crossover (`sma_crossover`)

| Aspect | Detail |
|--------|--------|
| **Logic** | **BUY** when SMA(20) crosses above SMA(50) — "Golden Cross". **SELL** when SMA(20) crosses below SMA(50) — "Death Cross". |
| **Type** | Trend-following |
| **Params** | `SMA_SHORT=20`, `SMA_LONG=50` (configurable in `.env`) |
| **Best for** | Trending markets with clear directional moves |

### 2. RSI Mean Reversion (`rsi_mean_reversion`)

| Aspect | Detail |
|--------|--------|
| **Logic** | **BUY** when RSI(14) < 30 (oversold). **SELL** when RSI(14) > 70 (overbought). |
| **Type** | Mean-reversion / oscillator |
| **Params** | `RSI_PERIOD=14`, `RSI_OVERSOLD=30`, `RSI_OVERBOUGHT=70` |
| **Best for** | Range-bound / sideways markets |

### 3. Donchian Breakout (`breakout`)

| Aspect | Detail |
|--------|--------|
| **Logic** | **BUY** when price > 20-period highest high (Donchian upper). **SELL** when price < 20-period lowest low. |
| **Type** | Breakout / channel |
| **Params** | Period = 20 |
| **Best for** | Volatile markets with strong breakouts |

### 4. Momentum (`momentum`)

| Aspect | Detail |
|--------|--------|
| **Logic** | **BUY** when momentum (price − price 10 bars ago) crosses from ≤ 0 to > 0. **SELL** on the reverse. |
| **Type** | Momentum / trend |
| **Params** | Lookback = 10 |
| **Best for** | Markets with acceleration / deceleration patterns |

### Indicator Library

All strategies use indicators from `app/utils/indicators.py`:

| Indicator | Function | Description |
|-----------|----------|-------------|
| SMA | `sma(series, period)` | Simple Moving Average |
| EMA | `ema(series, period)` | Exponential Moving Average |
| RSI | `rsi(series, period)` | Relative Strength Index (Wilder's smoothing) |
| Bollinger Bands | `bollinger_bands(series, period, std)` | Mean ± N standard deviations |
| ATR | `atr(high, low, close, period)` | Average True Range |
| MACD | `macd(series, fast, slow, signal)` | Moving Average Convergence Divergence |
| Momentum | `momentum(series, period)` | Price difference over N bars |
| Donchian | `donchian_channel(high, low, period)` | Highest high / lowest low over N bars |

---

## How Trades Work (Signal → Order Flow)

### Are Trades Automatic?

**Yes — fully automatic when the engine is running.** Users click **Start** and the system:

1. Streams ticks from CSV data
2. Feeds every tick to the active strategy
3. If a signal is generated → auto-creates an order with proper sizing, SL, TP
4. Submits to the simulated broker
5. Broker fills the order (with realistic latency and slippage)
6. All updates appear live in the dashboard

### Complete Flow

```
1. TICK LOOP (background thread, every ~0.5s per cycle)
   │  Reads next CSV row for each of 5 symbols
   │  Emits "tick" to all connected WebSocket clients
   ▼
2. STRATEGY ENGINE
   │  Feeds tick to active strategy (e.g., SMA Crossover)
   │  Strategy computes indicators on its rolling price buffer
   │  Returns Signal: {action: "BUY", symbol: "RELIANCE.NS", price: 2543.10}
   ▼
3. ML FILTER (optional — if ML_ENABLED=true)
   │  XGBoost predicts P(up-move)
   │  BUY blocked if P(up) < 0.65  |  SELL blocked if P(up) > 0.35
   ▼
4. ORDER MANAGER
   │  Deduplicates (no duplicate signal+symbol+price)
   │  Computes qty = floor(capital × 1% / (price × 2%))
   │  Creates order with auto SL & TP
   │  Persists to SQLite
   │  Submits to broker with retry (up to 3 attempts)
   ▼
5. SIMULATED BROKER (async background thread)
   │  Queues order → waits 200–400ms → ACK
   │  Optional partial fill (~30% chance for qty > 10)
   │  Waits 200–800ms → FILLED with slippage (~5% rejection)
   │  Fires webhook POST to /webhook/order-update
   ▼
6. WEBHOOK → ORDER MANAGER → WebSocket broadcast
      Order status updated in DB + memory
      Position updated, realised P&L calculated on close
      "order_update" + "position_update" emitted to clients
```

### Manual Orders

Users can also place orders manually:
- **Web UI:** Right sidebar → Place Order form (Symbol, Type, Qty, SL %, TP %)
- **REST API:** `POST /api/place-order` with `{symbol, side, qty, price}`
- **Cancel:** `POST /api/cancel-order` with `{order_id}`

---

## Data: Real-Time vs Simulated

### Current State: **SIMULATED / REPLAYED**

| Aspect | Detail |
|--------|--------|
| **Source** | Historical daily OHLCV CSV files (500 rows per symbol) |
| **Tick generation** | Each CSV row = 1 tick, replayed sequentially in an infinite loop |
| **Tick speed** | Configurable: `TICK_INTERVAL_SEC=0.5` (all 5 symbols tick per cycle) |
| **Yahoo Finance** | Attempted on first startup; if the API is unreachable, falls back to synthetic |
| **Synthetic fallback** | Geometric Brownian Motion: 12 % annual drift, 25 % vol, seeded per symbol |
| **Market indices** | NIFTY / SENSEX / BANKNIFTY shown in the UI are cosmetic random-walk values |

### Symbols Tracked

| Symbol | Yahoo Code | Synthetic Base Price |
|--------|-----------|---------------------|
| Reliance Industries | `RELIANCE.NS` | ₹2,540 |
| TCS | `TCS.NS` | ₹3,850 |
| Infosys | `INFY.NS` | ₹1,620 |
| HDFC Bank | `HDFCBANK.NS` | ₹1,720 |
| SBI | `SBIN.NS` | ₹620 |

### Frontend Candle Chart

- Each backend tick → 1 base candle (sequential timestamps, 60 s apart)
- Timeframe buttons aggregate: 1m = 1 tick/candle, 5m = 5 ticks/candle, 15m = 15, 1H = 60
- Client-side overlays: SMA(20, 50), EMA(12, 26), Bollinger Bands(20, 2), Volume histogram

---

## ML Prediction Module

### Model: XGBoost Binary Classifier

| Aspect | Detail |
|--------|--------|
| **Algorithm** | `XGBClassifier` (gradient-boosted trees) |
| **Target** | Binary: will tomorrow's close be higher? (1 = yes, 0 = no) |
| **Trees** | 200 estimators, max depth 5, learning rate 0.05 |
| **Loss** | Log-loss (binary cross-entropy) |
| **Split** | 80 / 20 train / test, no shuffle (time-series integrity) |

### 15 Features

| # | Feature | Description |
|---|---------|-------------|
| 1 | `sma_10` | 10-period SMA |
| 2 | `sma_20` | 20-period SMA |
| 3 | `sma_50` | 50-period SMA |
| 4 | `ema_12` | 12-period EMA |
| 5 | `ema_26` | 26-period EMA |
| 6 | `price_sma20_ratio` | Close / SMA(20) |
| 7 | `sma10_sma50_ratio` | SMA(10) / SMA(50) |
| 8 | `rsi_14` | RSI (14-period) |
| 9 | `momentum_10` | Price − Price(10 bars ago) |
| 10 | `atr_14` | Average True Range (14) |
| 11 | `daily_return` | % return from previous close |
| 12 | `return_std_10` | 10-day rolling std of returns |
| 13 | `volume_sma_10` | 10-period SMA of volume |
| 14 | `volume_ratio` | Volume / Volume SMA(10) |
| 15 | `close` | Raw close price |

### How ML Filtering Works

```
Strategy Signal
      │
      ▼
  ML enabled? ──No──▶ Pass through
      │
     Yes
      │
      ▼
  P(up) = XGBoost.predict_proba(features)
      │
      ├── BUY  signal + P(up) < 0.65  →  BLOCKED ✗
      ├── SELL signal + P(up) > 0.35  →  BLOCKED ✗
      └── Otherwise                   →  PASSED  ✓
```

### Train the Model

```bash
python -m app.ml.trainer --symbols RELIANCE.NS,TCS.NS,INFY.NS
# Saves: app/ml/models/xgb_model.json + xgb_model.meta.json
```

---

## Backtesting Engine

```bash
# Run a backtest
python -m app.backtest.backtester --symbol RELIANCE.NS --strategy sma_crossover

# Custom parameters
python -m app.backtest.backtester \
    --symbol TCS.NS \
    --strategy rsi_mean_reversion \
    --capital 500000 \
    --slippage 0.1
```

### Metrics Computed

| Metric | Description |
|--------|-------------|
| **Total Return** | (Final equity − Starting capital) / Starting capital |
| **Sharpe Ratio** | Annualised (√252 × mean / std of daily returns) |
| **Max Drawdown** | Largest peak-to-trough decline |
| **Win Rate** | % of profitable round-trip trades |
| **Trade Count** | Total completed trades |
| **Equity Curve** | Bar-by-bar portfolio value (PNG chart) |

### Output

- JSON report → `app/backtest/reports/<symbol>_<strategy>_<timestamp>.json`
- Equity curve → `app/backtest/reports/<symbol>_<strategy>_<timestamp>.png`

Supports configurable slippage (default 0.05 %), commission (default 0.03 %), and any registered strategy.

---

## Risk Management

| Feature | Implementation | Default |
|---------|---------------|---------|
| **Position sizing** | `qty = floor(capital × risk % / (price × SL %))` | 1 % of capital at risk |
| **Stop-loss** | Auto-attached. BUY: `entry × (1 − SL %)`, SELL: `entry × (1 + SL %)` | 2 % |
| **Take-profit** | Auto-attached. BUY: `entry × (1 + TP %)`, SELL: `entry × (1 − TP %)` | 4 % (2 : 1 R:R) |
| **ML gate** | Blocks low-probability signals | Threshold: 65 % |
| **Idempotency** | Rejects duplicate signal + symbol + price combos | Always on |
| **Retry logic** | Up to 3 broker submission attempts before REJECTED | Always on |
| **Drawdown tracker** | `DrawdownTracker` monitors peak-to-trough | Used in backtester |
| **Broker realism** | Latency 200–800 ms, 0.05 % slippage, ~30 % partial fills, ~5 % rejection | Configurable |

---

## REST API Reference

Base URL: `http://localhost:5005`

| Method | Endpoint | Body / Query | Response |
|--------|----------|-------------|----------|
| `GET` | `/` | — | Trading terminal HTML |
| `POST` | `/api/start` | `{"strategy": "sma_crossover"}` | `{"status": "started"}` |
| `POST` | `/api/stop` | — | `{"status": "stopped"}` |
| `GET` | `/api/status` | — | `{running, strategy, use_ml, ticks_processed}` |
| `GET` | `/api/positions` | — | `{positions: {...}}` with current prices |
| `GET` | `/api/pnl` | — | `{realised_pnl, unrealised_pnl, total_pnl, capital}` |
| `POST` | `/api/place-order` | `{symbol, side, qty, price, order_type?}` | `{order: {...}}` |
| `POST` | `/api/cancel-order` | `{order_id}` | `{order: {...}}` |
| `GET` | `/api/orders` | — | `{orders: [...]}` (limit 100) |
| `GET` | `/api/ml-predict` | `?symbol=RELIANCE.NS` | `{symbol, probability, direction}` |
| `POST` | `/webhook/order-update` | `{order_id, status, filled_qty, avg_price}` | `{ok: true}` |

A **Postman collection** is provided in `postman_collection.json`.

---

## WebSocket Events

### Server → Client

| Event | Payload | Frequency |
|-------|---------|-----------|
| `tick` | `{symbol, price, open, high, low, close, volume, timestamp}` | ~0.5 s per symbol |
| `signal` | `{action, symbol, price, reason, strategy, timestamp}` | On strategy signal |
| `order_update` | `{order_id, symbol, side, qty, status, filled_qty, avg_price, ...}` | On order state change |
| `position_update` | `{positions: {SYMBOL: {qty, avg_price, side}}}` | Every ~1 s |
| `pnl_update` | `{realised_pnl, unrealised_pnl, total_pnl, capital, trade_count}` | Every ~1 s |
| `status` | `{running, strategy, use_ml, ticks_processed}` | On state change |

### Client → Server

| Event | Payload | Description |
|-------|---------|-------------|
| `control` | `{action: "start" / "stop" / "set_strategy" / "toggle_ml"}` | Engine control |
| `ping` | — | Keep-alive |
| `request_state` | — | Full state snapshot on reconnect |

---

## Database Schema

SQLite at `data/algo_demo.db` — WAL journal mode, thread-safe.

```sql
-- Orders with full lifecycle tracking
CREATE TABLE orders (
    order_id    TEXT PRIMARY KEY,    -- UUID
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,       -- BUY / SELL
    qty         INTEGER NOT NULL,
    price       REAL,
    order_type  TEXT DEFAULT 'MARKET',
    status      TEXT DEFAULT 'NEW',  -- state machine below
    filled_qty  INTEGER DEFAULT 0,
    avg_price   REAL DEFAULT 0,
    strategy    TEXT,
    created_at  TEXT,
    updated_at  TEXT
);

-- Individual trade fills
CREATE TABLE trades (
    trade_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT REFERENCES orders(order_id),
    symbol      TEXT,  side TEXT,  qty INTEGER,  price REAL,
    timestamp   TEXT
);

-- P&L snapshots over time
CREATE TABLE pnl_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT,
    realised_pnl    REAL,
    unrealised_pnl  REAL,
    total_pnl       REAL,
    capital         REAL
);

-- Strategy decision audit log
CREATE TABLE strategy_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT,
    strategy    TEXT,  symbol TEXT,  signal TEXT,
    details     TEXT  -- JSON blob
);
```

### Order State Machine

```
NEW ──→ ACK ──→ PARTIAL ──→ FILLED
 │       │        │
 │       │        └──→ CANCELLED
 │       ├──→ FILLED
 │       ├──→ CANCELLED
 │       └──→ REJECTED
 ├──→ CANCELLED
 └──→ REJECTED
```

---

## Configuration Reference

All settings in `.env` (copy from `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_PORT` | `5005` | Server port |
| `DEFAULT_SYMBOLS` | `RELIANCE.NS,TCS.NS,INFY.NS,HDFCBANK.NS,SBIN.NS` | Symbols to stream |
| `TICK_INTERVAL_SEC` | `0.5` | Seconds between tick cycles |
| `DEFAULT_STRATEGY` | `sma_crossover` | Initial strategy |
| `SMA_SHORT` / `SMA_LONG` | `20` / `50` | SMA crossover periods |
| `RSI_PERIOD` | `14` | RSI period |
| `RSI_OVERSOLD` / `RSI_OVERBOUGHT` | `30` / `70` | RSI thresholds |
| `INITIAL_CAPITAL` | `1000000` | Starting capital (₹10 Lakhs) |
| `RISK_PER_TRADE_PCT` | `1.0` | % of capital risked per trade |
| `DEFAULT_STOP_LOSS_PCT` | `2.0` | Stop-loss distance % |
| `DEFAULT_TAKE_PROFIT_PCT` | `4.0` | Take-profit distance % (2:1 R:R) |
| `ML_ENABLED` | `false` | Enable ML prediction filter |
| `ML_PROBABILITY_THRESHOLD` | `0.65` | ML confidence cutoff |
| `BROKER_MIN_LATENCY_MS` | `200` | Min simulated broker latency |
| `BROKER_MAX_LATENCY_MS` | `800` | Max simulated broker latency |
| `SLIPPAGE_PCT` | `0.05` | Simulated slippage % |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## How to Run

### Prerequisites

- **Python 3.10+** (tested with 3.10 and 3.11)
- **pip**

### Quick Start (3 commands)

```bash
pip install -r requirements.txt
python scripts/fetch_data.py
python app/main.py
```

Open **http://localhost:5005** → Click **▶ Start**.

### Step-by-Step (Windows PowerShell)

```powershell
cd algo_demo_india

# Create virtual environment (recommended)
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Generate / download market data
python scripts/fetch_data.py

# (Optional) Train ML model
python -m app.ml.trainer --symbols RELIANCE.NS,TCS.NS,INFY.NS

# Start the server
python app/main.py
# With ML enabled:  python app/main.py --use-ml

# Open http://localhost:5005  →  click Start
```

### One-Command Start (Linux / Mac)

```bash
chmod +x run_demo.sh && ./run_demo.sh
# With ML: ./run_demo.sh --use-ml
```

---

## Running Tests

```bash
pytest tests/ -v
```

21 tests covering:
- Technical indicators (SMA, RSI, Momentum)
- Risk sizing and SL/TP calculations
- Order state-machine transitions
- Strategy signal generation
- OrderManager lifecycle

---

## Docker Deployment

```bash
docker-compose up --build
# Or:
docker build -t algo-terminal .
docker run -p 5005:5005 -v ./data:/app/data -v ./logs:/app/logs algo-terminal
```

---

## Upgrading to Production-Ready Real Algo Trading

This demo provides the **architectural foundation**. Here's the detailed roadmap:

### Phase 1 — Real Broker Integration

| Task | Details |
|------|---------|
| **Implement `BrokerAdapter`** | Fill in `adapter_template.py` — Zerodha Kite skeleton already provided |
| **Zerodha Kite Connect** | `pip install kiteconnect`. Implement `place_order()`, `cancel_order()`, `get_positions()`. API: ₹2,000/month |
| **Other brokers** | Angel One SmartAPI (free), Upstox, IIFL, 5Paisa — all have Python SDKs |
| **Auth flow** | Implement OAuth redirect → access token → secure storage. Tokens expire daily. |

```python
# Replace SimulatedBroker with real Zerodha:
from kiteconnect import KiteConnect

class ZerodhaAdapter(BrokerAdapter):
    def place_order(self, symbol, side, qty, price, order_type):
        return self.kite.place_order(
            tradingsymbol=symbol, exchange="NSE",
            transaction_type=side, quantity=qty,
            order_type=order_type, product="MIS",
        )
```

### Phase 2 — Real-Time Market Data

| Task | Details |
|------|---------|
| **WebSocket feeds** | Zerodha Kite Ticker, Angel SmartStream, or TrueData |
| **Replace `tick_generator`** | Same tick dict format — just swap the source |
| **Historical data** | Use broker APIs for minute / tick-level candles |

```python
from kiteconnect import KiteTicker

def on_ticks(ws, ticks):
    for t in ticks:
        tick = {"symbol": t["tradingsymbol"], "price": t["last_price"],
                "open": t["ohlc"]["open"], "high": t["ohlc"]["high"],
                "low": t["ohlc"]["low"], "close": t["ohlc"]["close"],
                "volume": t["volume_traded"]}
        socketio.emit("tick", tick)
        engine.on_tick(tick)
```

### Phase 3 — Infrastructure & Security

| Task | Details |
|------|---------|
| **Authentication** | JWT / session auth — never expose trading APIs without login |
| **HTTPS** | Nginx reverse proxy + Let's Encrypt |
| **Database** | SQLite → PostgreSQL for concurrent access |
| **Task queue** | Celery + Redis for order processing |
| **Rate limiting** | Flask-Limiter |
| **Monitoring** | Prometheus + Grafana |

### Phase 4 — Strategy Improvements

| Task | Details |
|------|---------|
| **Intraday candles** | 1-min / 5-min data instead of daily (re-tune parameters) |
| **Multi-timeframe** | Confirm on higher TF, enter on lower TF |
| **Advanced strategies** | VWAP, Supertrend, Heikin-Ashi, Options Greeks |
| **Walk-forward** | Rolling-window parameter optimisation |
| **Regime detection** | ML to classify trending / ranging / volatile → switch strategies |

### Phase 5 — Risk & Compliance

| Task | Details |
|------|---------|
| **Position limits** | Cap total exposure (e.g., 20 % of capital in one stock) |
| **Daily loss limit** | Auto-halt if daily loss > 3 % of capital |
| **Kill switch** | Emergency flatten-all + halt |
| **Audit trail** | Full decision logging (already partially implemented in `strategy_logs`) |
| **SEBI compliance** | Required for any production algo trading in India |

### Phase 6 — Production Architecture

```
Nginx (HTTPS) → Gunicorn (Flask) → Celery Workers → Redis
                       ↓                    ↓
                  PostgreSQL           Broker API
                (orders, trades)    (Zerodha / Angel)
```

### Estimated Monthly Cost

| Item | Cost |
|------|------|
| Zerodha Kite API | ₹2,000 |
| VPS (2 CPU, 4 GB RAM) | ₹1,000–2,000 |
| Managed PostgreSQL | ₹500–1,500 |
| Market data feed | ₹1,000–3,000 |
| **Total** | **₹4,500–8,500/month** |

---

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Backend | Python + Flask | 3.10+ / 3.0.3 |
| WebSocket | Flask-SocketIO + eventlet | 5.3.7 / 0.36.1 |
| Database | SQLite (WAL mode) | Built-in |
| ML | XGBoost + scikit-learn | 2.1.0 / 1.5.1 |
| Data | pandas + yfinance | 2.2.2 / 0.2.40 |
| Charts | TradingView Lightweight Charts | 4.1.3 |
| Frontend | Vanilla HTML / CSS / JS + Socket.IO | 4.7.5 |
| Fonts | Inter + JetBrains Mono | Google Fonts |
| Testing | pytest | 8.3.2 |
| CI/CD | GitHub Actions | flake8 + pytest |
| Container | Docker + docker-compose | Python 3.11-slim |

---

> **Disclaimer:** This project is a technical demonstration only. Algorithmic trading carries significant financial risk. Do not use for real trading without thorough testing, risk assessment, and SEBI regulatory compliance.
