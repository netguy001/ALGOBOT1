# AlgoTerminal — Algorithmic Trading Demo for Indian Stock Market

> **Full-stack production-ready demo application** showcasing algorithmic trading system design for NSE/BSE stocks.
> Built with Python (Flask + SocketIO), TradingView Lightweight Charts, and a professional Binance-style dark UI.
> **This is a simulation/demo — not connected to real brokers or live market feeds.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-3.0.3-green.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Table of Contents

1. [What Is This?](#what-is-this)
2. [Architecture Overview](#architecture-overview)
3. [Complete Folder Structure](#complete-folder-structure)
4. [Core Components Deep Dive](#core-components-deep-dive)
5. [Trading Algorithms](#trading-algorithms)
6. [How Trades Work (Signal → Order Flow)](#how-trades-work-signal--order-flow)
7. [Data: Real-Time vs Simulated](#data-real-time-vs-simulated)
8. [ML Prediction Module](#ml-prediction-module)
9. [Backtesting Engine](#backtesting-engine)
10. [Risk Management](#risk-management)
11. [Capital Management System](#capital-management-system)
12. [Order Validation Layer](#order-validation-layer)
13. [Authentication System](#authentication-system)
14. [REST API Reference](#rest-api-reference)
15. [WebSocket Events](#websocket-events)
16. [Database Schema](#database-schema)
17. [Configuration Reference](#configuration-reference)
18. [How to Run](#how-to-run)
19. [Running Tests](#running-tests)
20. [Docker Deployment](#docker-deployment)
21. [Upgrading to Production-Ready Real Algo Trading](#upgrading-to-production-ready-real-algo-trading)
22. [Tech Stack](#tech-stack)
23. [Glossary](#glossary)

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
| ❌ | **Not fully production-grade** — basic authentication only, no HTTPS, single-process |
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
| `app/config.py` | 134 | Loads ALL settings from `.env` with typed defaults. Single source of truth for config. |
| `app/main.py` | 492 | Flask entry point, component wiring, background tick loop, WebSocket integration. |
| `app/engine_controller.py` | 174 | **Single source of truth** for engine state (IDLE/RUNNING/STOPPED/PAUSED). |
| `app/strategy/engine.py` | 251 | Manages active strategy, feeds ticks, applies ML filter, safety guards. |
| `app/strategy/strategies.py` | 353 | 4 strategy classes with trend filter, edge-trigger signals, `STRATEGY_REGISTRY`. |
| `app/broker/order_manager.py` | 533 | Order lifecycle, state machine (NEW→ACK→FILLED), retry logic, SL/TP management. |
| `app/broker/capital_manager.py` | 418 | **DB-backed** capital/position tracking, margin calculation, daily loss limits. |
| `app/broker/order_validator.py` | 192 | 10 pre-trade validation guards: kill switch, cooldowns, exposure caps. |
| `app/broker/trade_ledger.py` | ~100 | Independent PnL verification from trades table. |
| `app/broker/simulated_broker.py` | 183 | Fake broker with latency queue, slippage, partial fills, rejection simulation. |
| `app/broker/adapter_template.py` | 103 | Abstract `BrokerAdapter` ABC + Zerodha Kite skeleton for real-broker migration. |
| `app/data_feed/base.py` | ~50 | Abstract `DataFeed` interface: `connect()`, `subscribe()`, `on_tick()`. |
| `app/data_feed/demo_feed.py` | ~120 | CSV replay implementation — each row → one tick, loops infinitely. |
| `app/data_feed/provider.py` | ~30 | Factory function to create appropriate feed based on `MODE`. |
| `app/utils/data.py` | 299 | Yahoo Finance download, GBM synthetic fallback, tick generator (CSV replay). |
| `app/utils/indicators.py` | 98 | Pure functions: SMA, EMA, RSI (Wilder), BB, ATR, MACD, Momentum, Donchian. |
| `app/utils/risk.py` | 202 | Position sizing with 7 safety guards, SL/TP calculation, `DrawdownTracker`. |
| `app/utils/clock.py` | ~80 | IST-aware clock, NSE market hours detection. |
| `app/routes/api.py` | 396 | REST endpoints: engine control, orders, positions, PnL, candles, ML predict. |
| `app/routes/webhook.py` | 89 | Receives POST from broker, updates order status, broadcasts via WebSocket. |
| `app/routes/auth.py` | 162 | User registration, login, logout, session management. |
| `app/ws/socket_server.py` | 137 | Socket.IO handlers: `control`, `ping`, `request_state`, tick history on connect. |
| `app/ml/trainer.py` | 158 | Trains XGBoost classifier (15 technical features → predict next-day direction). |
| `app/ml/predictor.py` | 113 | Loads trained model, computes P(up-move) for live signal filtering. |
| `app/backtest/backtester.py` | 326 | Run strategies on historical data, SL/TP exits, Sharpe/DD/WR metrics. |
| `app/db/storage.py` | 735 | SQLite DAL: accounts, positions, orders, trades, candles. WAL mode, thread-safe. |
| `frontend/index.html` | 344 | 3-column layout: watchlist, TradingView chart + tabs, sidebar with auth modal. |
| `frontend/static/app.js` | 1312 | Chart management, WebSocket handling, order form, paginated tables, overlays. |
| `frontend/static/style.css` | 1003 | Binance-inspired dark theme, CSS Grid layout, animations, responsive design. |
| `tests/test_strategy.py` | 375 | 21+ unit tests: indicators, risk sizing, capital manager, strategies. |
| `scripts/fetch_data.py` | 23 | CLI: downloads OHLCV for all `DEFAULT_SYMBOLS`. |

---

## Core Components Deep Dive

### Engine Controller (`app/engine_controller.py`)

The **EngineController** is the **single source of truth** for the trading engine state. Every component that performs trading-related work checks `controller.is_running` before proceeding.

| State | Description |
|-------|-------------|
| `IDLE` | System booted, no strategy active |
| `RUNNING` | Strategy executing, orders flowing |
| `STOPPED` | User pressed stop; market data streams but trading is frozen |
| `PAUSED` | Soft pause; can resume without full restart |

```python
# All trading components guard with this pattern:
if not controller.is_running:
    return  # Skip processing when not RUNNING
```

**Key Features:**
- Thread-safe state transitions with locking
- Persists state to DB (survives server restart)
- Auto-resume capability on server recovery
- Stop event for background thread coordination

### Capital Manager (`app/broker/capital_manager.py`)

DB-backed capital and position management — the **single source of truth** for financial state.

| Property | Description |
|----------|-------------|
| `available_capital` | Free cash for new trades |
| `used_margin` | Capital locked in open positions |
| `realised_pnl` | Closed trade profits/losses |
| `unrealised_pnl` | Mark-to-market on open positions |

**Hard Caps Enforced:**
- `MAX_POSITION_SIZE_PER_TRADE` — per-trade notional limit
- `MAX_OPEN_POSITIONS` — concurrent open symbols cap (default: 10)
- `MAX_TOTAL_EXPOSURE_PERCENT` — portfolio-level exposure (default: 80%)
- `MAX_QTY_PER_ORDER` — absolute share count sanity limit

**Capital Flow:**
```
Opening Fill:   available_capital -= qty × fill_price
Position Close: available_capital += margin + realised_pnl
```

### Order Validator (`app/broker/order_validator.py`)

Pre-trade validation layer with 10 defence-in-depth safety guards:

| Guard | Description |
|-------|-------------|
| Kill Switch | Hard stop, no exceptions |
| Daily Loss Halt | Automatic after threshold breach |
| Duplicate Signal | Idempotency check (same symbol+action+price) |
| Tick Cooldown | Minimum ticks between signals per symbol |
| Time Cooldown | Minimum wall-clock seconds between signals |
| Position Direction | Blocks doubling-down in same direction |
| Max Open Positions | Rejects if position count at limit |
| Available Capital | Ensures funds before creating order |
| Exposure Cap | Total portfolio exposure limit |
| Quantity Sanity | Rejects zero-quantity trades |

### Data Feed Abstraction (`app/data_feed/`)

| Component | Purpose |
|-----------|---------|
| `base.py` | Abstract `DataFeed` interface — `connect()`, `subscribe()`, `on_tick()` |
| `demo_feed.py` | CSV replay implementation for demo mode |
| `provider.py` | Factory function to create appropriate feed based on `MODE` |

**DemoDataFeed Workflow:**
1. On `connect()` — loads CSV data for all symbols
2. Creates tick generators that replay OHLCV rows
3. Each CSV row → one tick event
4. Loops infinitely for continuous demo

### Trade Ledger (`app/broker/trade_ledger.py`)

Computes PnL exclusively from the `trades` table — independent verification layer.

```python
# Verify CapitalManager vs TradeLedger (should match!)
verification = ledger.verify_against_capital_manager(capital_mgr)
# {"match": True, "discrepancy": 0.0}
```

### Engine Clock (`app/utils/clock.py`)

IST (India Standard Time) aware clock with NSE market hours awareness:

- `is_market_open()` — checks if within NSE trading hours (9:15 AM - 3:30 PM IST)
- Supports different modes: `demo` (always open), `paper`, `live`
- Auto-converts UTC timestamps to IST for display

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

### Position Sizing Safety Guards

The risk module (`app/utils/risk.py`) applies multiple safety guards in sequence:

1. **Minimum Stop Distance** — Rejects if `SL% < MIN_STOP_DISTANCE_PCT` (prevents qty explosion)
2. **SL Floor** — Enforces `MIN_STOP_LOSS_PCT` to prevent near-zero SL
3. **Notional Cap** — `MAX_POSITION_SIZE_PCT_OF_CAPITAL` (10%) limits single position size
4. **Per-Trade Cap** — `MAX_POSITION_SIZE_PER_TRADE` (500 shares)
5. **Per-Order Cap** — `MAX_QTY_PER_ORDER` (10,000 shares)
6. **Absolute Max** — `ABSOLUTE_MAX_QTY` hard ceiling
7. **Capital Check** — `qty × price` must not exceed available capital

---

## Capital Management System

The **CapitalManager** (`app/broker/capital_manager.py`) provides DB-backed, thread-safe capital tracking.

### Account State (Persisted to SQLite)

| Field | Type | Description |
|-------|------|-------------|
| `initial_capital` | float | Starting capital (₹10 Lakhs default) |
| `available_capital` | float | Free cash for new trades |
| `realised_pnl` | float | Cumulative closed-trade P&L |
| `used_margin` | float | Capital locked in open positions |
| `daily_loss_halted` | bool | Auto-halt flag after loss threshold |

### Position Tracking

Each position tracks:
```python
{
    "symbol": "RELIANCE.NS",
    "qty": 50,
    "avg_price": 2543.10,
    "side": "BUY"  # or "SELL" for short
}
```

### Capital Flow Operations

| Operation | Effect |
|-----------|--------|
| `reserve_capital(qty, price)` | `available_capital -= qty × price` |
| `release_capital(qty, price)` | `available_capital += qty × price` |
| `record_pnl(amount)` | `realised_pnl += amount; available_capital += amount` |

### Daily Loss Limit

When `realised_pnl` drops below `-DAILY_LOSS_LIMIT` (default: ₹50,000):
1. Sets `daily_loss_halted = True`
2. Persists halt flag to DB (survives restart)
3. All new orders are rejected until manual reset

---

## Order Validation Layer

The **OrderValidator** (`app/broker/order_validator.py`) implements 10 safety checks before any order is created.

### Validation Sequence

```
Signal arrives → OrderValidator.validate_signal()
                         │
                         ├── 1. Kill switch active? → REJECT
                         ├── 2. Daily loss halted? → REJECT
                         ├── 3. Daily loss limit breached? → REJECT + HALT
                         ├── 4. Duplicate signal (idempotency)? → REJECT
                         ├── 5. Tick cooldown not met? → REJECT
                         ├── 6. Time cooldown not met? → REJECT
                         ├── 7. Already have position in same direction? → REJECT
                         ├── 8. Max open positions reached? → REJECT
                         ├── 9. Insufficient capital? → REJECT
                         ├── 10. Exposure cap exceeded? → REJECT
                         │
                         └── All passed → APPROVED → Order created
```

### Cooldown System

- **Tick Cooldown**: `STRATEGY_COOLDOWN_CANDLES` (5 ticks) between signals for same symbol
- **Time Cooldown**: `SIGNAL_COOLDOWN_SEC` (30 seconds) wall-clock time between signals
- Prevents overtrading even when ticks arrive rapidly

---

## Authentication System

The auth module (`app/routes/auth.py`) provides basic multi-user support with session management.

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/register` | Create new user + account |
| `POST` | `/auth/login` | Log in, set Flask session |
| `POST` | `/auth/logout` | Clear session |
| `GET` | `/auth/me` | Get current user info |

### User Flow

1. **Register**: Creates user row + dedicated account with `INITIAL_CAPITAL`
2. **Login**: Validates password hash, sets session cookie
3. **Session**: Flask session tracks `user_id` for request authentication
4. **Logout**: Clears session, invalidates access

### Password Security

- Uses `werkzeug.security.generate_password_hash()` (PBKDF2-SHA256)
- Minimum 4 character password requirement
- Username: 2-50 characters, case-insensitive

### Guest Mode

Users can skip authentication ("Continue as Guest") for demo purposes without creating an account.

---

## REST API Reference

Base URL: `http://localhost:5005`

### Engine Control

| Method | Endpoint | Body | Response |
|--------|----------|------|----------|
| `POST` | `/api/start` | `{"strategy": "sma_crossover"}` | `{"status": "started", "strategy": "..."}` |
| `POST` | `/api/stop` | — | `{"status": "stopped", "state": "STOPPED"}` |
| `GET` | `/api/status` | — | `{running, strategy, use_ml, ticks_processed, mode, market_open}` |
| `GET` | `/api/clock` | — | `{ist_time, market_open, mode, ...}` |

### Trading

| Method | Endpoint | Body / Query | Response |
|--------|----------|-------------|----------|
| `POST` | `/api/place-order` | `{symbol, side, qty, price, sl_pct?, tp_pct?}` | `{order: {...}}` or `{error: "..."}` |
| `POST` | `/api/cancel-order` | `{order_id}` | `{order: {...}}` |
| `GET` | `/api/orders` | `?limit=100` | `{orders: [...]}` |
| `GET` | `/api/positions` | — | `{positions: [...]}` with current prices |
| `GET` | `/api/pnl` | — | `{realised_pnl, unrealised_pnl, total_pnl, capital}` |
| `GET` | `/api/ledger` | — | Trade-ledger computed PnL (verification) |

### Data

| Method | Endpoint | Query | Response |
|--------|----------|-------|----------|
| `GET` | `/api/candles` | `symbol=RELIANCE.NS&timeframe=1m&limit=500` | `{candles: [...], count: N}` |
| `GET` | `/api/ml-predict` | `symbol=RELIANCE.NS` | `{symbol, probability, direction}` |

### Authentication

| Method | Endpoint | Body | Response |
|--------|----------|------|----------|
| `POST` | `/auth/register` | `{username, password}` | `{user_id, username}` |
| `POST` | `/auth/login` | `{username, password}` | `{user_id, username}` |
| `POST` | `/auth/logout` | — | `{ok: true}` |
| `GET` | `/auth/me` | — | `{user_id, username}` or `{error}` |

### Webhook

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| `POST` | `/webhook/order-update` | `{order_id, status, filled_qty, avg_price}` | Broker callback for order state changes |

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
-- Account capital tracking (DB-backed, survives restart)
CREATE TABLE accounts (
    account_id        TEXT PRIMARY KEY DEFAULT 'default',
    initial_capital   REAL NOT NULL,
    available_capital REAL NOT NULL,
    realised_pnl      REAL NOT NULL DEFAULT 0,
    daily_loss_halted INTEGER DEFAULT 0,
    engine_state      TEXT DEFAULT 'IDLE',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Position book (per-account, per-symbol)
CREATE TABLE positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT NOT NULL DEFAULT 'default',
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL DEFAULT 'FLAT',
    qty         INTEGER NOT NULL DEFAULT 0,
    avg_price   REAL NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    UNIQUE(account_id, symbol)
);

-- Orders with full lifecycle tracking
CREATE TABLE orders (
    order_id    TEXT PRIMARY KEY,    -- UUID
    account_id  TEXT DEFAULT 'default',
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
    account_id  TEXT DEFAULT 'default',
    symbol      TEXT,  side TEXT,  qty INTEGER,  price REAL,
    pnl         REAL DEFAULT 0,
    timestamp   TEXT
);

-- P&L snapshots over time
CREATE TABLE pnl_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT DEFAULT 'default',
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

-- Historical candles (persisted chart data)
CREATE TABLE candles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT NOT NULL,
    timeframe  TEXT NOT NULL DEFAULT '1m',
    timestamp  INTEGER NOT NULL,  -- Unix epoch
    open       REAL NOT NULL,
    high       REAL NOT NULL,
    low        REAL NOT NULL,
    close      REAL NOT NULL,
    volume     REAL NOT NULL DEFAULT 0,
    UNIQUE(symbol, timeframe, timestamp)
);

-- Users table for multi-user support
CREATE TABLE users (
    user_id    TEXT PRIMARY KEY,
    username   TEXT UNIQUE NOT NULL,
    password   TEXT NOT NULL,  -- PBKDF2-SHA256 hash
    created_at TEXT NOT NULL
);

-- Indexes for performance
CREATE INDEX idx_candles_sym_tf ON candles(symbol, timeframe, timestamp DESC);
CREATE INDEX idx_orders_account ON orders(account_id, created_at DESC);
CREATE INDEX idx_trades_account ON trades(account_id, timestamp DESC);
CREATE INDEX idx_positions_account ON positions(account_id);
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

## Glossary

| Term | Definition |
|------|------------|
| **ATR** | Average True Range — volatility indicator measuring price movement range |
| **Backtesting** | Running a strategy on historical data to evaluate performance |
| **Bollinger Bands** | Price channels at ±N standard deviations from a moving average |
| **Donchian Channel** | Highest high and lowest low over N periods |
| **Drawdown** | Peak-to-trough decline in portfolio value |
| **EMA** | Exponential Moving Average — weighted average giving more weight to recent prices |
| **GBM** | Geometric Brownian Motion — mathematical model for random price movements |
| **Golden Cross** | When short-term SMA crosses above long-term SMA (bullish signal) |
| **Death Cross** | When short-term SMA crosses below long-term SMA (bearish signal) |
| **Idempotency** | Ensuring duplicate signals don't create duplicate orders |
| **IST** | Indian Standard Time (UTC+5:30) |
| **Kill Switch** | Emergency stop that immediately halts all trading |
| **MACD** | Moving Average Convergence Divergence — trend momentum indicator |
| **Mark-to-Market (MTM)** | Valuing open positions at current market prices |
| **Mean Reversion** | Strategy based on prices returning to historical average |
| **Momentum** | Rate of price change over a period |
| **NSE** | National Stock Exchange of India |
| **OHLCV** | Open, High, Low, Close, Volume — standard price bar data |
| **Partial Fill** | When only part of an order is executed |
| **Position Sizing** | Calculating how many shares to buy based on risk parameters |
| **Realised P&L** | Profit/loss from closed positions |
| **RSI** | Relative Strength Index — momentum oscillator (0-100 scale) |
| **R:R** | Risk-to-Reward ratio (e.g., 2:1 means TP is 2× the SL distance) |
| **Sharpe Ratio** | Risk-adjusted return metric: (return - risk-free) / volatility |
| **Slippage** | Difference between expected and actual execution price |
| **SMA** | Simple Moving Average — arithmetic mean of prices over N periods |
| **Stop-Loss (SL)** | Order to exit a position when price moves against you |
| **Take-Profit (TP)** | Order to exit a position when price reaches a profit target |
| **Tick** | A single price update or the minimum price movement |
| **Trend Filter** | Confirming trade direction aligns with the broader trend |
| **Unrealised P&L** | Paper profit/loss on open positions |
| **WAL** | Write-Ahead Logging — SQLite journaling mode for better concurrency |
| **WebSocket** | Persistent bidirectional connection for real-time data |
| **XGBoost** | Extreme Gradient Boosting — ML algorithm for classification/regression |

---

> **Disclaimer:** This project is a technical demonstration only. Algorithmic trading carries significant financial risk. Do not use for real trading without thorough testing, risk assessment, and SEBI regulatory compliance.
