# Algo Demo India — Algorithmic Trading Demo for the Indian Stock Market

> A complete, professional demo algorithmic trading web application for NSE/BSE,  
> using Yahoo Finance data, rule-based + ML strategies, simulated broker, and a  
> clean real-time dashboard.

---

## Research & References

Before building this system the following sources were reviewed. Each link is
annotated with a short summary of what it contributes.

| # | Source | Notes |
|---|--------|-------|
| 1 | [yfinance PyPI & docs](https://pypi.org/project/yfinance/) | Official Python wrapper for Yahoo Finance. Provides `download()` and `Ticker` APIs for OHLCV data. Rate-limited to ~2 000 requests/hour; 1-minute intraday data available for the last 7 days only; daily data available for decades. |
| 2 | [Yahoo Finance — NSE ticker format](https://finance.yahoo.com/quote/RELIANCE.NS/) | NSE-listed stocks use the `.NS` suffix (e.g. `RELIANCE.NS`, `TCS.NS`). BSE-listed stocks use `.BO`. Some tickers have special names — always verify against Yahoo's search. |
| 3 | [Zerodha Kite Connect API docs](https://kite.trade/docs/connect/v3/) | Reference architecture for Indian broker API design — order lifecycle (OPEN → COMPLETE / CANCELLED / REJECTED), webhook/postback structure, and instrument token format. Used as a template for our simulated broker. |
| 4 | [scikit-learn & XGBoost best practices](https://xgboost.readthedocs.io/en/stable/) | XGBoost documentation for gradient-boosted classifiers. We use `XGBClassifier` with `use_label_encoder=False` and `eval_metric='logloss'`. Feature engineering follows standard TA indicators. |
| 5 | [Investopedia — Algorithmic Trading Strategies](https://www.investopedia.com/articles/active-trading/101014/basics-algorithmic-trading-concepts-and-examples.asp) | Overview of SMA crossover, mean-reversion, and momentum strategies. Used to validate strategy parameter defaults (SMA 20/50, RSI 30/70). |
| 6 | [QuantConnect — Backtesting Best Practices](https://www.quantconnect.com/docs/v2/writing-algorithms) | Guidance on backtesting methodology: avoiding look-ahead bias, proper slippage modelling (we use 0.05 % default), commission handling, and equity-curve analysis. |
| 7 | [NSE India — Market Timings & Lot Sizes](https://www.nseindia.com/market-data/live-equity-market) | NSE cash-market hours 09:15–15:30 IST, pre-open 09:00–09:08. Useful for validating tick-simulation timing assumptions. |

### Key assumptions

| Topic | Assumption |
|-------|------------|
| **Ticker format** | NSE tickers append `.NS` (e.g. `RELIANCE.NS`). BSE tickers append `.BO`. This demo defaults to `.NS`. |
| **Data granularity** | yfinance provides free daily OHLCV going back 20+ years. Intraday (1 min) is limited to the last 7 days. We use daily data for backtesting/ML and replay it as simulated ticks. |
| **Simulated latency** | Broker fill latency is randomised between 200–800 ms. Slippage is modelled at 0.05 % of price. |
| **Risk defaults** | 1 % of capital risked per trade; default stop-loss 2 %, take-profit 4 %. All configurable. |
| **ML toggle** | The system runs fully without ML. Pass `--use-ml` or toggle in the UI to activate the XGBoost prediction filter. |

---

## Quick Start

### Prerequisites

- Python 3.10+ (tested on 3.11)
- pip
- (Optional) Docker & docker-compose

### 1. Clone / unzip

```bash
unzip algo_demo_india.zip
cd algo_demo_india
```

### 2. Create virtualenv & install

```bash
python -m venv venv
# Linux / macOS
source venv/bin/activate
# Windows
venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Fetch sample data

```bash
python scripts/fetch_data.py
```

This downloads ~2 years of daily OHLCV for RELIANCE.NS, TCS.NS, INFY.NS into
`data/`.

### 4. (Optional) Train ML model

```bash
python -m app.ml.trainer
```

Produces `app/ml/models/xgb_model.json`.

### 5. Run the application

```bash
python app/main.py
```

Open **http://localhost:5000** in your browser.

### 6. Run with ML enabled

```bash
python app/main.py --use-ml
```

### 7. Run backtests

```bash
python -m app.backtest.backtester
```

Reports saved to `app/backtest/reports/`.

### 8. Run tests

```bash
pytest tests/ -v
```

---

## Docker (optional)

```bash
docker-compose up --build
```

App available at **http://localhost:5000**.

---

## REST API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/start` | Start the strategy engine |
| POST | `/api/stop` | Stop the strategy engine |
| GET | `/api/status` | Get engine status |
| GET | `/api/positions` | Current open positions |
| GET | `/api/pnl` | Realised + unrealised PnL |
| POST | `/api/place-order` | Place a manual order |
| POST | `/api/cancel-order` | Cancel an open order |
| GET | `/api/ml-predict` | Get ML prediction for a symbol |

## WebSocket Events

| Event | Direction | Payload |
|-------|-----------|---------|
| `tick` | server → client | `{symbol, price, timestamp, volume}` |
| `order_update` | server → client | `{order_id, status, filled_qty, ...}` |
| `position_update` | server → client | `{symbol, qty, entry, pnl, ...}` |
| `control` | client → server | `{action: "start"/"stop", strategy, symbol}` |

---

## Toggling ML vs Rule-Based

- **UI**: Flip the "ML Enabled" toggle on the dashboard.
- **CLI**: `python app/main.py --use-ml`
- **Config**: Set `ML_ENABLED=true` in `.env`.

When ML is enabled, strategy signals are filtered: only signals where the
XGBoost model outputs P(up) > 0.65 are forwarded to the order manager.

---

## Backtest Reports

After running the backtester, check `app/backtest/reports/` for:

- `equity_curve.png` — cumulative returns plot
- `report.json` — Sharpe, win rate, max drawdown, trade count

---

## Logging

All logs go to `logs/app.log` (rotated at 5 MB, 3 backups) and to the console.
Critical events (order fills, errors) are also persisted in the SQLite database.

---

## 3-Minute Interview Demo Script

Use this script to walk through the demo in an interview setting.

### Minute 1 — Architecture overview (talk while app loads)

> "This is a full-stack algorithmic trading demo for the Indian stock market.
> The backend is Python/Flask with Flask-SocketIO for real-time streaming.
> I use Yahoo Finance for market data and replay historical ticks to simulate
> a live feed. The system has a modular architecture: strategy engine, risk
> engine, simulated broker with webhook callbacks, and an optional ML layer."

*Open the browser dashboard at localhost:5000.*

### Minute 2 — Live trading demo

> "Let me start the SMA crossover strategy on RELIANCE.NS."

1. Select **RELIANCE.NS** from the symbol dropdown.
2. Select **SMA Crossover** from the strategy dropdown.
3. Click **Start**.
4. Point to the live chart updating in real time.
5. Point to the order log as trades appear.
6. Show PnL updating live.

> "Each order goes through a realistic lifecycle — NEW, ACK, PARTIAL, FILLED —
> with simulated broker latency. Risk management sizes each position to 1% of
> capital with automatic stop-loss and take-profit."

### Minute 3 — ML & Backtest

> "I can also toggle on the ML module."

7. Flip the **ML Enabled** switch.

> "Now the XGBoost model filters signals — only trades where the model predicts
> >65% probability of an up-move are executed."

8. Show a prediction appearing in the log.

> "For validation, I built a backtester that computes Sharpe ratio, win rate,
> and max drawdown over historical data."

9. Open the backtest report (pre-generated) and show the equity curve.

> "The entire project is well-tested, dockerised, and ready for production
> extension — for example, swapping the simulated broker for a real Zerodha
> adapter."

---

*Built with ❤ for the Indian markets.*
