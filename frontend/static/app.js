/**
 * AlgoTerminal v4 — Production-Ready Trading Dashboard
 * =====================================================
 * Key improvements over v3:
 *   - Historical candles fetched from /api/candles on page load
 *     so the chart is populated BEFORE the websocket connects.
 *   - Chart is created ONCE, never destroyed/recreated on reconnect.
 *   - Disconnect indicator banner with auto-retry.
 *   - Loading spinner while historical candles are fetching.
 *   - Paginated orders table for large order volumes.
 *   - Mode-aware: fake indices only shown in demo mode.
 */

// ── Config ──────────────────────────────────────────────────
const API = '';
const MAX_CANDLES = 500;
const MAX_ORDERS = 200;
const MAX_TRADES = 200;
const MAX_SIGNALS = 100;
const PNL_THROTTLE = 400;
const ORDERS_PAGE_SIZE = 50;   // rows per page for order table pagination

// ── Candle grouping: how many ticks per candle for each TF ──
const TF_MAP = { '1': 1, '5': 5, '15': 15, '60': 60, 'D': 1 };
let ticksPerCandle = 1;              // default: 1 tick = 1 candle

// ── State ───────────────────────────────────────────────────
let selectedSymbol = 'RELIANCE.NS';
let selectedSide = 'BUY';
let tickCount = 0;
let signalCount = 0;
let tradeList = [];
let orderList = [];
let lastPnlTs = 0;
let ordersPage = 0;                 // current page of order pagination
let chartReady = false;             // guard: prevent chart operations before init
let historicalLoaded = false;       // guard: have we loaded /api/candles yet?
let engineState = 'IDLE';           // IDLE | RUNNING | STOPPED | PAUSED

// Per-symbol raw tick buffers (for chart reconstruction on symbol switch)
const tickBuffers = {};          // { 'RELIANCE.NS': [{o,h,l,c,v,t}, ...], ... }
// Positions cache (for order form indication)
let positionsCache = {};         // { 'RELIANCE.NS': {side:'BUY',qty:10,...}, ... }
// Current candle accumulator for the selected symbol
let pendingTicks = [];
// Completed candle array for chart
let candleData = [];
let volumeData = [];
// IST offset in seconds (UTC+5:30 = 19800s)
// TradingView Lightweight Charts displays `time` as UTC.
// To show IST on the X-axis we add the IST offset to every epoch value.
const IST_OFFSET = 5.5 * 3600;   // 19800 seconds

// Watchlist
const watchData = {};
const SYMBOLS = ['RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS', 'SBIN.NS'];
const firstPrice = {};              // for change % calc

// ── DOM ─────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const connBadge = $('conn-badge'), engBadge = $('engine-badge');
const clock = $('clock');
const chSym = $('ch-sym'), chLtp = $('ch-ltp'), chChg = $('ch-chg');
const symSel = $('symbol-select'), stratSel = $('strategy-select');
const mlToggle = $('ml-toggle');
const btnStart = $('btn-start'), btnStop = $('btn-stop');
const orderBody = $('order-body'), posBody = $('pos-body');
const tradesBody = $('trades-body'), signalLog = $('signal-log');
const cntOrders = $('cnt-orders'), cntPos = $('cnt-pos');
const pnlTotal = $('pnl-total'), pnlReal = $('pnl-real');
const pnlUnreal = $('pnl-unreal'), pnlCap = $('pnl-cap'), pnlTrades = $('pnl-trades');
const stName = $('st-name'), stTicks = $('st-ticks'), stSignals = $('st-signals'), stMl = $('st-ml');

// ── Helpers ─────────────────────────────────────────────────
function formatINR(v) {
    const s = v < 0 ? '-' : '';
    return s + '₹' + Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pnlColor(v) { return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text)'; }
function shortTime(iso) {
    // Convert UTC ISO timestamp to IST and display.
    // Shows "HH:MM:SS am/pm" for today, or "DD/MM HH:MM" for older dates.
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const nowIST = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
    const dIST = new Date(d.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
    const sameDay = nowIST.getFullYear() === dIST.getFullYear()
        && nowIST.getMonth() === dIST.getMonth()
        && nowIST.getDate() === dIST.getDate();
    if (sameDay) {
        return d.toLocaleTimeString('en-IN', {
            hour: '2-digit', minute: '2-digit', second: '2-digit',
            timeZone: 'Asia/Kolkata'
        });
    }
    // Different day — show date + time (no seconds) so user knows it's old
    return d.toLocaleDateString('en-IN', {
        day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit',
        timeZone: 'Asia/Kolkata'
    });
}
function sideClass(s) { return s === 'BUY' ? 'side-buy' : 'side-sell'; }
function statusClass(s) { return 'st-' + (s || 'new').toLowerCase(); }
function n(v, d = 2) { return Number(v).toFixed(d); }

// Client-side clock fallback (server clock overrides via fetchServerClock)
setInterval(() => {
    // Only update if server hasn't set it recently — the server clock
    // update (every 5s) will overwrite this with IST time.
    if (!clock._serverUpdated) {
        const d = new Date();
        clock.textContent = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
    }
}, 1000);

// ═══════════════════════════════════════════════════════════
//  LOADING SPINNER  (shown while fetching historical candles)
// ═══════════════════════════════════════════════════════════
function showChartLoading(show) {
    const chartEl = document.getElementById('tv-chart');
    if (!chartEl) return;
    let spinner = chartEl.querySelector('.chart-spinner');
    if (show) {
        if (!spinner) {
            spinner = document.createElement('div');
            spinner.className = 'chart-spinner';
            spinner.innerHTML = '<div class="spinner-ring"></div><span>Loading chart data…</span>';
            chartEl.appendChild(spinner);
        }
        spinner.style.display = 'flex';
    } else if (spinner) {
        spinner.style.display = 'none';
    }
}

// ═══════════════════════════════════════════════════════════
//  CHART SETUP  (TradingView Lightweight Charts)
// ═══════════════════════════════════════════════════════════
const chartEl = document.getElementById('tv-chart');
const chart = LightweightCharts.createChart(chartEl, {
    layout: {
        background: { type: 'solid', color: '#0b0e11' },
        textColor: '#848e9c',
        fontFamily: "'Inter', sans-serif",
        fontSize: 11,
    },
    grid: {
        vertLines: { color: '#1a1f27' },
        horzLines: { color: '#1a1f27' },
    },
    crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: '#2a3240', width: 1, style: 2, labelBackgroundColor: '#1e80ff' },
        horzLine: { color: '#2a3240', width: 1, style: 2, labelBackgroundColor: '#1e80ff' },
    },
    rightPriceScale: {
        borderColor: '#1e2530',
        scaleMargins: { top: 0.05, bottom: 0.15 },
    },
    timeScale: {
        borderColor: '#1e2530',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
        barSpacing: 8,
        minBarSpacing: 4,
    },
    handleScroll: true,
    handleScale: true,
});

const candleSeries = chart.addCandlestickSeries({
    upColor: '#0ecb81',
    downColor: '#f6465d',
    borderUpColor: '#0ecb81',
    borderDownColor: '#f6465d',
    wickUpColor: '#0ecb8199',
    wickDownColor: '#f6465d99',
});

const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
});
chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

// Overlay line series (lazy-created)
let sma20 = null, sma50 = null;
let ema12 = null, ema26 = null;
let bbUp = null, bbLo = null;

let overlays = { sma: true, ema: false, bb: false, vol: true };

function getOrCreateSma() {
    if (!sma20) {
        sma20 = chart.addLineSeries({ color: '#f0b90b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'SMA20' });
        sma50 = chart.addLineSeries({ color: '#1e80ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'SMA50' });
    }
    return [sma20, sma50];
}
function getOrCreateEma() {
    if (!ema12) {
        ema12 = chart.addLineSeries({ color: '#e040fb', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'EMA12' });
        ema26 = chart.addLineSeries({ color: '#ff6d00', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'EMA26' });
    }
    return [ema12, ema26];
}
function getOrCreateBB() {
    if (!bbUp) {
        bbUp = chart.addLineSeries({ color: '#00bcd4', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, title: 'BB+' });
        bbLo = chart.addLineSeries({ color: '#00bcd4', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, title: 'BB-' });
    }
    return [bbUp, bbLo];
}

// ── Indicator calculations ──────────────────────────────────
function calcSMA(arr, period) {
    const res = [];
    for (let i = 0; i < arr.length; i++) {
        if (i < period - 1) continue;
        let sum = 0;
        for (let j = i - period + 1; j <= i; j++) sum += arr[j].close;
        res.push({ time: arr[i].time, value: sum / period });
    }
    return res;
}
function calcEMA(arr, period) {
    const res = []; const k = 2 / (period + 1); let ema = null;
    for (let i = 0; i < arr.length; i++) {
        if (ema === null) {
            if (i < period - 1) continue;
            let sum = 0; for (let j = i - period + 1; j <= i; j++) sum += arr[j].close;
            ema = sum / period;
        } else {
            ema = arr[i].close * k + ema * (1 - k);
        }
        res.push({ time: arr[i].time, value: ema });
    }
    return res;
}
function calcBB(arr, period, mult) {
    const up = [], lo = [];
    for (let i = 0; i < arr.length; i++) {
        if (i < period - 1) continue;
        let sum = 0, sum2 = 0;
        for (let j = i - period + 1; j <= i; j++) { sum += arr[j].close; sum2 += arr[j].close ** 2; }
        const mean = sum / period, std = Math.sqrt(Math.max(0, sum2 / period - mean ** 2));
        up.push({ time: arr[i].time, value: mean + mult * std });
        lo.push({ time: arr[i].time, value: mean - mult * std });
    }
    return { up, lo };
}

function refreshOverlays() {
    if (candleData.length < 5) return;
    // SMA
    const [s20, s50] = getOrCreateSma();
    if (overlays.sma) { s20.setData(calcSMA(candleData, 20)); s50.setData(calcSMA(candleData, 50)); }
    else { s20.setData([]); s50.setData([]); }
    // EMA
    const [e12, e26] = getOrCreateEma();
    if (overlays.ema) { e12.setData(calcEMA(candleData, 12)); e26.setData(calcEMA(candleData, 26)); }
    else { e12.setData([]); e26.setData([]); }
    // BB
    const [bu, bl] = getOrCreateBB();
    if (overlays.bb) { const bb = calcBB(candleData, 20, 2); bu.setData(bb.up); bl.setData(bb.lo); }
    else { bu.setData([]); bl.setData([]); }
}

// ── Resize ──────────────────────────────────────────────────
function resizeChart() {
    const r = chartEl.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) chart.resize(r.width, r.height);
}
window.addEventListener('resize', resizeChart);
new ResizeObserver(resizeChart).observe(chartEl);
setTimeout(resizeChart, 50);
setTimeout(resizeChart, 300);

// ═══════════════════════════════════════════════════════════
//  TICK → CANDLE CONVERSION
// ═══════════════════════════════════════════════════════════
// Each backend tick is a daily OHLCV bar replayed from CSV.
// We treat each tick as "1 base unit". Timeframe buttons
// control how many ticks are grouped into one chart candle.
//
// Each candle uses the real UTC timestamp from the backend,
// shifted to IST for display on the chart X-axis.

// Convert an ISO-8601 UTC string to chart-time (epoch seconds shifted to IST)
function isoToChartTime(iso) {
    if (!iso) return Math.floor(Date.now() / 1000) + IST_OFFSET;
    const ms = (typeof iso === 'number') ? iso * 1000 : Date.parse(iso);
    if (isNaN(ms)) return Math.floor(Date.now() / 1000) + IST_OFFSET;
    return Math.floor(ms / 1000) + IST_OFFSET;
}

function resetChartData() {
    candleData = [];
    volumeData = [];
    pendingTicks = [];
    candleSeries.setData([]);
    volumeSeries.setData([]);
    getOrCreateSma(); sma20.setData([]); sma50.setData([]);
    if (ema12) { ema12.setData([]); ema26.setData([]); }
    if (bbUp) { bbUp.setData([]); bbLo.setData([]); }
}

function processTick(tick) {
    if (tick.symbol !== selectedSymbol) return;

    // Store raw tick
    if (!tickBuffers[tick.symbol]) tickBuffers[tick.symbol] = [];
    tickBuffers[tick.symbol].push(tick);
    if (tickBuffers[tick.symbol].length > MAX_CANDLES * 60) tickBuffers[tick.symbol].shift();

    // Add to pending
    pendingTicks.push(tick);

    // When we have enough ticks to form a candle
    if (pendingTicks.length >= ticksPerCandle) {
        flushCandle();
    }
}

function flushCandle() {
    if (pendingTicks.length === 0) return;

    // Use the LAST tick's real timestamp for this candle
    const last = pendingTicks[pendingTicks.length - 1];
    const chartTime = isoToChartTime(last.timestamp);

    // Aggregate pending ticks into one OHLCV candle
    const first = pendingTicks[0];
    let o = first.open || first.price;
    let h = first.high || first.price;
    let l = first.low || first.price;
    let c = first.close || first.price;
    let v = first.volume || 0;

    for (let i = 1; i < pendingTicks.length; i++) {
        const t = pendingTicks[i];
        const tH = t.high || t.price;
        const tL = t.low || t.price;
        const tC = t.close || t.price;
        h = Math.max(h, tH);
        l = Math.min(l, tL);
        c = tC;
        v += (t.volume || 0);
    }

    // Ensure time is strictly increasing (TradingView requirement)
    let t = chartTime;
    if (candleData.length > 0) {
        const prevTime = candleData[candleData.length - 1].time;
        if (t <= prevTime) t = prevTime + 1;
    }

    const candle = { time: t, open: o, high: h, low: l, close: c };
    const vol = { time: t, value: v, color: c >= o ? '#0ecb8130' : '#f6465d30' };

    pendingTicks = [];

    // Push to history
    candleData.push(candle);
    volumeData.push(vol);
    if (candleData.length > MAX_CANDLES) {
        candleData.shift();
        volumeData.shift();
    }

    // Update chart
    candleSeries.update(candle);
    if (overlays.vol) volumeSeries.update(vol);

    // Refresh overlays every 5 candles
    if (candleData.length % 5 === 0) refreshOverlays();
}

// ═══════════════════════════════════════════════════════════
//  WATCHLIST
// ═══════════════════════════════════════════════════════════
function buildWatchlist() {
    const wl = $('watchlist');
    if (!wl) return;
    wl.innerHTML = '';
    SYMBOLS.forEach(sym => {
        const short = sym.replace('.NS', '');
        watchData[sym] = { price: 0, prevPrice: 0 };
        const row = document.createElement('div');
        row.className = 'wl-row' + (sym === selectedSymbol ? ' active' : '');
        row.dataset.sym = sym;
        row.innerHTML = `
            <span class="wl-sym">${short}</span>
            <div class="wl-right">
                <span class="wl-price" id="wl-p-${short}">—</span>
                <span class="wl-chg" id="wl-c-${short}"></span>
            </div>`;
        row.addEventListener('click', () => selectSymbol(sym));
        wl.appendChild(row);
    });
}

function updateWatchPrice(sym, price) {
    const short = sym.replace('.NS', '');
    if (!firstPrice[sym]) firstPrice[sym] = price;
    const base = firstPrice[sym];
    const chg = price - base;
    const pct = base ? (chg / base * 100) : 0;
    watchData[sym] = { price, prevPrice: watchData[sym]?.price || price };

    const pEl = document.getElementById('wl-p-' + short);
    const cEl = document.getElementById('wl-c-' + short);
    if (pEl) {
        pEl.textContent = n(price);
        pEl.style.color = chg >= 0 ? 'var(--green)' : 'var(--red)';
    }
    if (cEl) {
        cEl.textContent = (chg >= 0 ? '+' : '') + n(chg) + ' (' + n(pct, 1) + '%)';
        cEl.style.color = chg >= 0 ? 'var(--green)' : 'var(--red)';
    }
}

function selectSymbol(sym) {
    if (sym === selectedSymbol && candleData.length > 0) return; // already loaded
    selectedSymbol = sym;
    symSel.value = sym;
    chSym.textContent = sym;
    const ofSym = $('of-sym');
    if (ofSym) ofSym.value = sym;
    updateOrderBtn();

    // Reset chart
    resetChartData();

    // Watchlist active highlight
    document.querySelectorAll('.wl-row').forEach(r => {
        r.classList.toggle('active', r.dataset.sym === sym);
    });

    // ── FETCH historical candles for this symbol from DB ──
    showChartLoading(true);
    loadHistoricalCandles(sym).then(() => {
        showChartLoading(false);
        // If we already had in-memory ticks that are newer, replay them
        const buf = tickBuffers[sym] || [];
        if (buf.length > candleData.length) {
            resetChartData();
            const start = Math.max(0, buf.length - MAX_CANDLES * ticksPerCandle);
            for (let i = start; i < buf.length; i++) {
                pendingTicks.push(buf[i]);
                if (pendingTicks.length >= ticksPerCandle) flushCandle();
            }
            refreshOverlays();
        }
        chart.timeScale().fitContent();
    }).catch(() => { showChartLoading(false); });

    // Update LTP display from in-memory buffer
    const buf = tickBuffers[sym] || [];
    if (buf.length > 0) {
        const last = buf[buf.length - 1];
        chLtp.textContent = n(last.price);
    }
}

// ═══════════════════════════════════════════════════════════
//  TABS
// ═══════════════════════════════════════════════════════════
document.querySelectorAll('.tab-bar .tab').forEach(tab => {
    tab.addEventListener('click', () => {
        const parent = tab.closest('.bottom-pane');
        parent.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        parent.querySelectorAll('.tab-body').forEach(b => b.classList.remove('active'));
        tab.classList.add('active');
        parent.querySelector('#tab-' + tab.dataset.tab).classList.add('active');
    });
});

// ═══════════════════════════════════════════════════════════
//  TIMEFRAME BUTTONS
// ═══════════════════════════════════════════════════════════
document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        ticksPerCandle = TF_MAP[btn.dataset.tf] || 1;

        // Rebuild chart from tick buffer with new aggregation
        resetChartData();
        const buf = tickBuffers[selectedSymbol] || [];
        const start = Math.max(0, buf.length - MAX_CANDLES * ticksPerCandle);
        for (let i = start; i < buf.length; i++) {
            pendingTicks.push(buf[i]);
            if (pendingTicks.length >= ticksPerCandle) flushCandle();
        }
        refreshOverlays();
        chart.timeScale().fitContent();
    });
});

// ═══════════════════════════════════════════════════════════
//  OVERLAY BUTTONS
// ═══════════════════════════════════════════════════════════
document.querySelectorAll('.ov-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        btn.classList.toggle('active');
        overlays[btn.dataset.ov] = btn.classList.contains('active');

        // Volume visibility
        if (btn.dataset.ov === 'vol') {
            if (overlays.vol) volumeSeries.setData(volumeData);
            else volumeSeries.setData([]);
        }

        refreshOverlays();
    });
});

// ═══════════════════════════════════════════════════════════
//  HISTORICAL CANDLE FETCH  (from /api/candles — persistent DB)
// ═══════════════════════════════════════════════════════════
// Called ONCE on page load, BEFORE websocket connects.
// This ensures the chart shows historical data even after server restart.

async function loadHistoricalCandles(symbol) {
    showChartLoading(true);
    try {
        const res = await fetch(`${API}/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=tick&limit=500`);
        const data = await res.json();
        if (data.candles && data.candles.length > 0) {
            // Convert DB candles into tick-buffer format
            // c.timestamp is epoch seconds (UTC) from the backend
            const ticks = data.candles.map(c => ({
                symbol: c.symbol,
                price: c.close,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
                volume: c.volume || 0,
                timestamp: new Date(c.timestamp * 1000).toISOString(),
            }));

            // Load into tick buffer (don't overwrite if websocket already has more)
            if (!tickBuffers[symbol] || tickBuffers[symbol].length < ticks.length) {
                tickBuffers[symbol] = ticks;
            }

            // Build chart from these candles
            resetChartData();
            const buf = tickBuffers[symbol] || [];
            const start = Math.max(0, buf.length - MAX_CANDLES * ticksPerCandle);
            for (let i = start; i < buf.length; i++) {
                pendingTicks.push(buf[i]);
                if (pendingTicks.length >= ticksPerCandle) flushCandle();
            }
            refreshOverlays();
            chart.timeScale().fitContent();

            // Update LTP display
            if (buf.length > 0) {
                const last = buf[buf.length - 1];
                chLtp.textContent = n(last.price);
            }

            historicalLoaded = true;
            console.log('[candles] Loaded', data.candles.length, 'historical candles for', symbol);
        }
    } catch (e) {
        console.warn('[candles] Failed to load historical candles:', e);
    } finally {
        showChartLoading(false);
    }
}

// Also load historical for all symbols on startup (for watchlist)
async function loadAllHistoricalCandles() {
    // Load selected symbol first (for chart), then others in background
    await loadHistoricalCandles(selectedSymbol);
    for (const sym of SYMBOLS) {
        if (sym === selectedSymbol) continue;
        // Fire and forget — just populates tick buffers for watchlist
        fetch(`${API}/api/candles?symbol=${encodeURIComponent(sym)}&timeframe=tick&limit=10`)
            .then(r => r.json())
            .then(data => {
                if (data.candles && data.candles.length > 0) {
                    const last = data.candles[data.candles.length - 1];
                    updateWatchPrice(sym, last.close);
                }
            })
            .catch(() => { });
    }
}

// ═══════════════════════════════════════════════════════════
//  WEBSOCKET  (connects AFTER historical candles are loaded)
// ═══════════════════════════════════════════════════════════
let socket;
let reconnectCount = 0;

function connectSocket() {
    // Guard: only create ONE socket connection
    if (socket && socket.connected) {
        console.log('[ws] Already connected, skipping duplicate connectSocket()');
        return;
    }
    if (socket) {
        // Socket exists but disconnected — reconnect it instead of creating new
        socket.connect();
        return;
    }

    socket = io({
        transports: ['websocket', 'polling'],
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
        reconnectionAttempts: Infinity,
        forceNew: false,       // reuse existing connection
    });

    socket.on('connect', () => {
        reconnectCount++;
        connBadge.className = 'badge badge-on';
        connBadge.innerHTML = '<span class="dot"></span>Connected';
        const banner = document.getElementById('disconnect-banner');
        if (banner) banner.style.display = 'none';

        // Request state from server — but do NOT reset the chart.
        // The chart was already populated from /api/candles.
        // We only want position/pnl/status updates here.
        socket.emit('request_state');
    });

    socket.on('disconnect', () => {
        connBadge.className = 'badge badge-off';
        connBadge.innerHTML = '<span class="dot"></span>Offline';
        const banner = document.getElementById('disconnect-banner');
        if (banner) banner.style.display = 'flex';
        // NOTE: We do NOT reset the chart here. The existing candle data
        // stays visible so the user doesn't lose context during a blip.
    });

    // ── Tick History (sent on connect / reconnect) ──
    // Only used to SUPPLEMENT the chart — never replaces data loaded
    // from /api/candles. This handles new ticks that arrived since
    // the page loaded.
    socket.on('tick_history', history => {
        for (const [sym, ticks] of Object.entries(history)) {
            if (!ticks || !ticks.length) continue;
            // Merge: only replace if server has MORE ticks than we have
            if (!tickBuffers[sym] || ticks.length > tickBuffers[sym].length) {
                tickBuffers[sym] = ticks;
            }
            if (!firstPrice[sym] && ticks.length > 0) {
                firstPrice[sym] = ticks[0].price;
            }
            const last = ticks[ticks.length - 1];
            updateWatchPrice(sym, last.price);
        }

        // Only rebuild chart if we haven't loaded historical yet,
        // or if the server has significantly more data
        const buf = tickBuffers[selectedSymbol] || [];
        if (buf.length > candleData.length + 5) {
            resetChartData();
            const start = Math.max(0, buf.length - MAX_CANDLES * ticksPerCandle);
            for (let i = start; i < buf.length; i++) {
                pendingTicks.push(buf[i]);
                if (pendingTicks.length >= ticksPerCandle) flushCandle();
            }
            refreshOverlays();
            chart.timeScale().fitContent();
        }

        if (buf.length > 0) {
            const last = buf[buf.length - 1];
            chLtp.textContent = n(last.price);
            tickCount = buf.length;
            stTicks.textContent = tickCount;
        }
        console.log('[tick_history] Merged', Object.keys(history).length, 'symbols');
    });

    // ── Tick ──
    socket.on('tick', tick => {
        tickCount++;

        // Watchlist update for all symbols
        updateWatchPrice(tick.symbol, tick.price);

        // Chart: only process for selected symbol
        processTick(tick);

        // Header LTP
        if (tick.symbol === selectedSymbol) {
            chLtp.textContent = n(tick.price);
            // Change from first candle open
            if (candleData.length > 0) {
                const firstOpen = candleData[0].open;
                const chg = tick.price - firstOpen;
                const pct = firstOpen ? (chg / firstOpen * 100) : 0;
                chChg.textContent = (chg >= 0 ? '+' : '') + n(chg) + ' (' + n(pct, 2) + '%)';
                chChg.style.color = chg >= 0 ? 'var(--green)' : 'var(--red)';
            }
            stTicks.textContent = tickCount;
        }
    });

    // ── Order Update (single order — real-time from broker callback) ──
    socket.on('order_update', order => {
        console.log('[ws] order_update:', order.order_id?.substring(0, 8), order.status);
        upsertOrder(order);
        if (order.status === 'FILLED' || order.status === 'PARTIAL') addTrade(order);
    });

    // ── Orders Snapshot (full list — from request_state on connect/refresh) ──
    socket.on('orders_snapshot', data => {
        if (data && data.orders) {
            data.orders.forEach(o => {
                upsertOrder(o, true);   // bulk — skip per-order render
                // Populate trade log with filled orders from snapshot
                if (o.status === 'FILLED' || o.status === 'PARTIAL') addTrade(o);
            });
            sortOrderList();
            renderOrders();
        }
    });

    // ── Position ──
    socket.on('position_update', data => renderPositions(data.positions));

    // ── PnL (throttled) ──
    // NOTE: Do NOT block based on engineState here.
    // The backend already stops periodic PnL broadcasts when engine is stopped.
    // The only PnL we receive when stopped is from request_state (initial load),
    // which must always be accepted to show correct values after page refresh.
    socket.on('pnl_update', pnl => {
        const now = Date.now();
        if (now - lastPnlTs < PNL_THROTTLE) return;
        lastPnlTs = now;
        const tot = pnl.total_pnl || 0;
        pnlTotal.textContent = formatINR(tot);
        pnlTotal.style.color = pnlColor(tot);
        pnlReal.textContent = formatINR(pnl.realised_pnl || 0);
        pnlReal.style.color = pnlColor(pnl.realised_pnl);
        pnlUnreal.textContent = formatINR(pnl.unrealised_pnl || 0);
        pnlUnreal.style.color = pnlColor(pnl.unrealised_pnl);
        pnlCap.textContent = formatINR(pnl.capital || 0);
        pnlTrades.textContent = pnl.trade_count || orderList.filter(o => o.status === 'FILLED').length;
    });

    // ── Signal ──
    socket.on('signal', sig => {
        signalCount++;
        stSignals.textContent = signalCount;
        addSignalRow(sig);

        // Also put a marker on the chart
        if (sig.symbol === selectedSymbol && candleData.length > 0) {
            const last = candleData[candleData.length - 1];
            const isBuy = sig.side === 'BUY' || sig.action === 'BUY';
            candleSeries.setMarkers([...(candleSeries.markers ? candleSeries.markers() : []), {
                time: last.time,
                position: isBuy ? 'belowBar' : 'aboveBar',
                color: isBuy ? '#0ecb81' : '#f6465d',
                shape: isBuy ? 'arrowUp' : 'arrowDown',
                text: (sig.side || sig.action || '') + ' ' + n(sig.price || 0, 0),
            }]);
        }
    });

    // ── Status ──
    socket.on('status', st => {
        engineState = st.state || (st.running ? 'RUNNING' : 'IDLE');

        if (engineState === 'RUNNING') {
            engBadge.className = 'badge badge-run'; engBadge.textContent = 'Running';
        } else if (engineState === 'STOPPED') {
            engBadge.className = 'badge badge-off'; engBadge.textContent = 'Stopped';
        } else if (engineState === 'PAUSED') {
            engBadge.className = 'badge badge-idle'; engBadge.textContent = 'Paused';
        } else {
            engBadge.className = 'badge badge-idle'; engBadge.textContent = 'Idle';
        }
        if (st.strategy) stName.textContent = st.strategy;
        stMl.textContent = st.use_ml ? 'ON' : 'OFF';
        stMl.style.color = st.use_ml ? 'var(--green)' : 'var(--text2)';
    });
}

// ═══════════════════════════════════════════════════════════
//  ORDERS TABLE  (with pagination)
// ═══════════════════════════════════════════════════════════
function upsertOrder(order, skipRender) {
    const idx = orderList.findIndex(o => o.order_id === order.order_id);
    if (idx >= 0) orderList[idx] = order;
    else { orderList.unshift(order); if (orderList.length > MAX_ORDERS) orderList.pop(); }
    if (!skipRender) renderOrders();
}

// Sort order list so newest (by updated_at/created_at) appear first
function sortOrderList() {
    orderList.sort((a, b) => {
        const ta = new Date(a.updated_at || a.created_at || 0).getTime();
        const tb = new Date(b.updated_at || b.created_at || 0).getTime();
        return tb - ta;  // descending — newest first
    });
}

function renderOrders() {
    if (!orderList.length) {
        orderBody.innerHTML = '<tr><td colspan="10" class="empty">No orders yet</td></tr>';
        cntOrders.textContent = '0';
        renderOrderPagination();
        return;
    }
    cntOrders.textContent = orderList.length;

    // Pagination: show only ORDERS_PAGE_SIZE rows at a time
    const start = ordersPage * ORDERS_PAGE_SIZE;
    const end = Math.min(start + ORDERS_PAGE_SIZE, orderList.length);
    const pageOrders = orderList.slice(start, end);

    let h = '';
    for (const o of pageOrders) {
        const canC = o.status === 'NEW' || o.status === 'ACK' || o.status === 'PARTIAL';
        h += `<tr>
            <td>${shortTime(o.updated_at || o.created_at)}</td>
            <td>${o.symbol}</td>
            <td class="${sideClass(o.side)}">${o.side}</td>
            <td>${o.order_type || 'MKT'}</td>
            <td>${o.qty}</td>
            <td>${n(o.avg_price || o.price || 0)}</td>
            <td>${o.filled_qty || 0}/${o.qty}</td>
            <td class="${statusClass(o.status)}">${o.status}</td>
            <td>${o.strategy || 'manual'}</td>
            <td>${canC ? '<button class="cancel-btn" onclick="cancelOrder(\'' + o.order_id + '\')">Cancel</button>' : ''}</td>
        </tr>`;
    }
    orderBody.innerHTML = h;
    renderOrderPagination();
}

function renderOrderPagination() {
    // Create or update pagination controls below the orders table
    let pagEl = document.getElementById('orders-pagination');
    const totalPages = Math.max(1, Math.ceil(orderList.length / ORDERS_PAGE_SIZE));

    if (!pagEl) {
        const tabOrders = document.getElementById('tab-orders');
        if (!tabOrders) return;
        pagEl = document.createElement('div');
        pagEl.id = 'orders-pagination';
        pagEl.className = 'pagination-bar';
        tabOrders.appendChild(pagEl);
    }

    if (totalPages <= 1) {
        pagEl.innerHTML = '';
        return;
    }

    let h = '<button class="pg-btn" onclick="changeOrdersPage(-1)"' + (ordersPage === 0 ? ' disabled' : '') + '>&laquo; Prev</button>';
    h += `<span class="pg-info">Page ${ordersPage + 1} of ${totalPages}</span>`;
    h += '<button class="pg-btn" onclick="changeOrdersPage(1)"' + (ordersPage >= totalPages - 1 ? ' disabled' : '') + '>Next &raquo;</button>';
    pagEl.innerHTML = h;
}

window.changeOrdersPage = function (delta) {
    const totalPages = Math.ceil(orderList.length / ORDERS_PAGE_SIZE);
    ordersPage = Math.max(0, Math.min(ordersPage + delta, totalPages - 1));
    renderOrders();
};

window.cancelOrder = async function (id) {
    try {
        const res = await fetch(API + '/api/cancel-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order_id: id }),
        });
        const d = await res.json();
        if (res.ok && d.order) upsertOrder(d.order);
    } catch (e) { console.error('Cancel fail', e); }
};

// ═══════════════════════════════════════════════════════════
//  POSITIONS TABLE
// ═══════════════════════════════════════════════════════════
function renderPositions(positions) {
    // Store in cache for order form indication
    positionsCache = positions || {};
    updateOrderBtn();  // refresh button label with open/close indication

    if (!positions || !Object.keys(positions).length) {
        posBody.innerHTML = '<tr><td colspan="8" class="empty">No open positions</td></tr>';
        cntPos.textContent = '0'; return;
    }
    let cnt = 0, h = '';
    for (const [sym, pos] of Object.entries(positions)) {
        if (pos.qty === 0) continue;
        cnt++;
        const ltp = pos.current_price || 0;
        const diff = pos.side === 'BUY' ? ltp - pos.avg_price : pos.avg_price - ltp;
        const pnl = diff * pos.qty;
        const pct = pos.avg_price ? (diff / pos.avg_price * 100) : 0;
        h += `<tr>
            <td>${sym}</td>
            <td class="${sideClass(pos.side)}">${pos.side}</td>
            <td>${pos.qty}</td>
            <td>${n(pos.avg_price)}</td>
            <td>${n(ltp)}</td>
            <td style="color:${pnlColor(pnl)}">${formatINR(pnl)}</td>
            <td style="color:${pnlColor(pnl)}">${(pct >= 0 ? '+' : '') + n(pct)}%</td>
            <td><button class="close-btn" onclick="closePos('${sym}','${pos.side}',${pos.qty})">Close</button></td>
        </tr>`;
    }
    cntPos.textContent = cnt;
    posBody.innerHTML = h || '<tr><td colspan="8" class="empty">No open positions</td></tr>';
}

window.closePos = async function (sym, side, qty) {
    const cs = side === 'BUY' ? 'SELL' : 'BUY';
    // Use current market price (from watchlist) instead of 0
    const ltp = (watchData[sym] && watchData[sym].price) || 0;
    try {
        await fetch(API + '/api/place-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: sym, side: cs, qty, price: ltp }),
        });
    } catch (e) { console.error('Close fail', e); }
};

// ═══════════════════════════════════════════════════════════
//  TRADE LOG
// ═══════════════════════════════════════════════════════════
function addTrade(order) {
    // Dedup by order_id (trades from both /api/trades and order_update events)
    const id = order.order_id || order.trade_id || '';
    if (id && tradeList.find(t => (t.order_id || t.trade_id) === id)) return;
    tradeList.unshift(order);
    if (tradeList.length > MAX_TRADES) tradeList.pop();
    renderTrades();
}
function renderTrades() {
    if (!tradeList.length) {
        tradesBody.innerHTML = '<tr><td colspan="6" class="empty">No trades yet</td></tr>'; return;
    }
    let h = '';
    for (const t of tradeList) {
        const time = t.updated_at || t.timestamp || '';
        const lastCol = t.strategy ? t.strategy : (t.pnl !== undefined ? formatINR(t.pnl || 0) : 'manual');
        h += `<tr>
            <td>${shortTime(time)}</td>
            <td>${t.symbol}</td>
            <td class="${sideClass(t.side)}">${t.side}</td>
            <td>${t.filled_qty || t.qty}</td>
            <td>${n(t.avg_price || t.price || 0)}</td>
            <td>${lastCol}</td>
        </tr>`;
    }
    tradesBody.innerHTML = h;
}

// ═══════════════════════════════════════════════════════════
//  SIGNAL LOG
// ═══════════════════════════════════════════════════════════
function addSignalRow(sig) {
    const empty = signalLog.querySelector('.empty');
    if (empty) empty.remove();
    const row = document.createElement('div');
    row.className = 'sig-row';
    const dir = sig.side || sig.action || '—';
    row.innerHTML = `
        <span class="sig-time">${shortTime(sig.timestamp || new Date().toISOString())}</span>
        <span class="sig-sym">${sig.symbol || '—'}</span>
        <span class="sig-dir ${dir === 'BUY' ? 'side-buy' : 'side-sell'}">${dir}</span>
        <span class="sig-price">${n(sig.price || 0)}</span>
        <span class="sig-strat">${sig.strategy || '—'}</span>`;
    signalLog.prepend(row);
    while (signalLog.children.length > MAX_SIGNALS) signalLog.lastChild.remove();
}

// ═══════════════════════════════════════════════════════════
//  CONTROLS
// ═══════════════════════════════════════════════════════════
symSel.addEventListener('change', () => selectSymbol(symSel.value));

btnStart.addEventListener('click', () => {
    socket.emit('control', { action: 'start', strategy: stratSel.value });
});

btnStop.addEventListener('click', () => {
    socket.emit('control', { action: 'stop' });
});

stratSel.addEventListener('change', () => {
    socket.emit('control', { action: 'set_strategy', strategy: stratSel.value });
    stName.textContent = stratSel.options[stratSel.selectedIndex].text;
});

mlToggle.addEventListener('change', () => {
    socket.emit('control', { action: 'toggle_ml', use_ml: mlToggle.checked });
    stMl.textContent = mlToggle.checked ? 'ON' : 'OFF';
    stMl.style.color = mlToggle.checked ? 'var(--green)' : 'var(--text2)';
});

// ═══════════════════════════════════════════════════════════
//  ORDER FORM
// ═══════════════════════════════════════════════════════════
document.querySelectorAll('.os-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.os-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        selectedSide = tab.dataset.side;
        updateOrderBtn();
    });
});

$('of-type').addEventListener('change', function () {
    const row = $('of-limit-row');
    if (this.value === 'LIMIT') row.classList.add('show');
    else row.classList.remove('show');
});

function updateOrderBtn() {
    const btn = $('btn-order');
    if (!btn) return;
    const ofSym = $('of-sym');
    const sym = ofSym ? ofSym.value : '';
    const shortSym = sym.replace('.NS', '');

    // Determine if this order would OPEN, CLOSE, or ADD to position
    let action = 'OPEN';
    const pos = positionsCache[sym];
    if (pos && pos.qty > 0) {
        if (pos.side === selectedSide) {
            action = 'ADD';   // same side = adding to position
        } else {
            action = 'CLOSE'; // opposite side = closing position
        }
    }

    btn.textContent = `${selectedSide} ${shortSym} (${action})`;
    btn.className = 'submit-btn ' + (selectedSide === 'BUY' ? 'buy-bg' : 'sell-bg');
}

const ofSymEl = $('of-sym');
if (ofSymEl) ofSymEl.addEventListener('input', updateOrderBtn);

$('btn-order').addEventListener('click', async () => {
    const sym = $('of-sym').value;
    const type = $('of-type').value;
    const qty = parseInt($('of-qty').value, 10);
    const price = parseFloat($('of-price').value) || 0;
    const msg = $('ord-msg');

    if (!sym || !qty || qty < 1) { msg.textContent = 'Fill all fields'; msg.style.color = 'var(--red)'; return; }

    try {
        const res = await fetch(API + '/api/place-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: sym, side: selectedSide, qty, price, order_type: type }),
        });
        const d = await res.json();
        if (res.ok) {
            msg.textContent = 'Placed: ' + (d.order?.order_id || '').substring(0, 8);
            msg.style.color = 'var(--green)';
            if (d.order) upsertOrder(d.order);
        } else {
            msg.textContent = d.error || 'Failed'; msg.style.color = 'var(--red)';
        }
    } catch (e) {
        msg.textContent = 'Network error'; msg.style.color = 'var(--red)';
    }
    setTimeout(() => { msg.textContent = ''; }, 4000);
});

// ═══════════════════════════════════════════════════════════
//  AUTH  (login / register / guest)
// ═══════════════════════════════════════════════════════════
let currentUser = null;   // { username, account_id } or null

function showAuth() {
    const ov = document.getElementById('auth-overlay');
    if (ov) ov.style.display = 'flex';
}
function hideAuth() {
    const ov = document.getElementById('auth-overlay');
    if (ov) ov.style.display = 'none';
}
function showUserInfo(username) {
    const el = document.getElementById('user-info');
    const nm = document.getElementById('user-name');
    if (el) el.style.display = 'inline-flex';
    if (nm) nm.textContent = username || 'Guest';
}

// Auth tab switching
document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const isLogin = tab.dataset.auth === 'login';
        document.getElementById('login-form').style.display = isLogin ? 'flex' : 'none';
        document.getElementById('register-form').style.display = isLogin ? 'none' : 'flex';
        document.getElementById('auth-msg').textContent = '';
    });
});

// Login form
document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = document.getElementById('auth-msg');
    const username = document.getElementById('login-user').value.trim();
    const password = document.getElementById('login-pass').value;
    if (!username || !password) { msg.textContent = 'Fill all fields'; msg.style.color = 'var(--red)'; return; }
    try {
        const res = await fetch(API + '/auth/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const d = await res.json();
        if (res.ok) {
            currentUser = { username: d.username, account_id: d.account_id };
            hideAuth();
            showUserInfo(d.username);
            startApp();
        } else {
            msg.textContent = d.error || 'Login failed'; msg.style.color = 'var(--red)';
        }
    } catch (err) {
        msg.textContent = 'Network error'; msg.style.color = 'var(--red)';
    }
});

// Register form
document.getElementById('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = document.getElementById('auth-msg');
    const username = document.getElementById('reg-user').value.trim();
    const password = document.getElementById('reg-pass').value;
    if (!username || !password) { msg.textContent = 'Fill all fields'; msg.style.color = 'var(--red)'; return; }
    if (password.length < 4) { msg.textContent = 'Password min 4 chars'; msg.style.color = 'var(--red)'; return; }
    try {
        const res = await fetch(API + '/auth/register', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const d = await res.json();
        if (res.ok) {
            // Show success and switch to Login tab
            msg.textContent = 'Account created! Please log in.';
            msg.style.color = 'var(--green)';
            // Auto-switch to login tab after brief delay
            setTimeout(() => {
                document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
                const loginTab = document.querySelector('.auth-tab[data-auth="login"]');
                if (loginTab) loginTab.classList.add('active');
                document.getElementById('login-form').style.display = 'flex';
                document.getElementById('register-form').style.display = 'none';
                // Pre-fill username
                document.getElementById('login-user').value = username;
                document.getElementById('login-pass').value = '';
                document.getElementById('login-pass').focus();
                msg.textContent = '';
            }, 1200);
        } else {
            msg.textContent = d.error || 'Registration failed'; msg.style.color = 'var(--red)';
        }
    } catch (err) {
        msg.textContent = 'Network error'; msg.style.color = 'var(--red)';
    }
});

// Skip (guest)
document.getElementById('auth-skip-btn').addEventListener('click', () => {
    currentUser = { username: 'Guest', account_id: 'default' };
    hideAuth();
    showUserInfo('Guest');
    startApp();
});

// Logout
document.getElementById('btn-logout').addEventListener('click', async () => {
    try { await fetch(API + '/auth/logout', { method: 'POST' }); } catch (e) { }
    currentUser = null;
    document.getElementById('user-info').style.display = 'none';
    // Disconnect socket and reset app state so re-login is clean
    if (socket) { socket.disconnect(); socket = null; }
    appStarted = false;
    orderList = []; tradeList = []; signalCount = 0; tickCount = 0;
    showAuth();
});

// Check if already logged in (session cookie)
async function checkAuth() {
    try {
        const res = await fetch(API + '/auth/me');
        if (res.ok) {
            const d = await res.json();
            if (d.logged_in) {
                currentUser = { username: d.username, account_id: d.account_id };
                hideAuth();
                showUserInfo(d.username);
                return true;
            }
        }
    } catch (e) { }
    return false;
}

// ═══════════════════════════════════════════════════════════
//  SERVER CLOCK  (IST time from /api/clock)
// ═══════════════════════════════════════════════════════════
async function fetchServerClock() {
    try {
        const res = await fetch(API + '/api/clock');
        if (res.ok) {
            const d = await res.json();
            if (d.ist_time) {
                clock.textContent = d.ist_time;
                clock._serverUpdated = true;
                // Reset flag after 6s so client fallback resumes if server stops responding
                clearTimeout(clock._resetTimer);
                clock._resetTimer = setTimeout(() => { clock._serverUpdated = false; }, 6000);
            }
        }
    } catch (e) { clock._serverUpdated = false; }
}
// Update IST clock from server every 5s (client clock as fallback)
setInterval(fetchServerClock, 5000);

// ═══════════════════════════════════════════════════════════
//  LOAD EXISTING DATA + FAKE INDICES  (mode-aware)
// ═══════════════════════════════════════════════════════════
async function loadExisting() {
    try {
        const res = await fetch(API + '/api/orders?limit=50&offset=0');
        const d = await res.json();
        if (d.orders) {
            d.orders.forEach(o => upsertOrder(o, true));  // bulk load — skip per-order render
            sortOrderList();   // ensure newest orders on page 1
            renderOrders();
            // Also populate trade log with filled orders
            d.orders.filter(o => o.status === 'FILLED' || o.status === 'PARTIAL')
                .forEach(o => addTrade(o));
        }
    } catch (e) { }
    // Also fetch trades from dedicated endpoint
    try {
        const res = await fetch(API + '/api/trades?limit=100');
        const d = await res.json();
        if (d.trades && d.trades.length) {
            d.trades.forEach(t => addTrade(t));
        }
    } catch (e) { }
}

// ── Data Source Debug Panel ──
async function loadDataSourceInfo() {
    try {
        const res = await fetch(API + '/api/datasource');
        if (!res.ok) return;
        const d = await res.json();
        const body = document.getElementById('datasource-body');
        const modeEl = document.getElementById('ds-mode');
        if (!body) return;

        if (modeEl) modeEl.textContent = 'Mode: ' + (d.mode || '?').toUpperCase();

        const syms = d.symbols || {};
        if (!Object.keys(syms).length) {
            body.innerHTML = '<tr><td colspan="5" class="empty">No symbols configured</td></tr>';
            return;
        }

        let h = '';
        for (const [sym, info] of Object.entries(syms)) {
            const srcClass = (info.source || '').includes('Yahoo') ? 'side-buy' : 'side-sell';
            h += `<tr>
                <td>${sym}</td>
                <td class="${srcClass}">${info.source || '?'}</td>
                <td>${info.rows || 0}</td>
                <td style="font-size:.72rem">${info.date_range || '—'}</td>
                <td>${info.csv_exists ? '✓' : '✗'}</td>
            </tr>`;
        }
        body.innerHTML = h;
    } catch (e) { console.debug('datasource load error', e); }
}

// Fake market indices — ONLY shown in demo mode.
// In paper/live mode these should come from real data feeds.
function fakeIndices() {
    const base = { nifty: 25800, sensex: 83800, banknifty: 60700 };
    const origin = { nifty: 25800, sensex: 83800, banknifty: 60700 };
    function tick() {
        for (const [name, b] of Object.entries(base)) {
            const v = b + (Math.random() - 0.48) * 80;
            base[name] = v;
            const el = $('idx-' + name);
            if (el) {
                el.textContent = Math.round(v).toLocaleString('en-IN');
                el.style.color = v >= origin[name] ? 'var(--green)' : 'var(--red)';
            }
        }
    }
    tick();
    setInterval(tick, 2500);
}

// Detect mode from status endpoint
async function detectMode() {
    try {
        const res = await fetch(API + '/api/status');
        const st = await res.json();
        return st.mode || 'demo';
    } catch (e) {
        return 'demo';
    }
}

// ═══════════════════════════════════════════════════════════
//  INIT  —  Auth check → Historical candles → WebSocket
// ═══════════════════════════════════════════════════════════
let appStarted = false;

async function startApp() {
    if (appStarted) return;
    appStarted = true;

    buildWatchlist();
    updateOrderBtn();

    // 1. Show loading state and fetch persistent candles from DB
    showChartLoading(true);
    await loadAllHistoricalCandles();

    // 2. Now connect websocket — new ticks will APPEND to the chart
    connectSocket();

    // 3. Load existing orders
    loadExisting();

    // 4. Data source debug info
    loadDataSourceInfo();

    // 5. Fake indices only in demo mode (strict mode compliance)
    const mode = await detectMode();
    if (mode === 'demo') {
        fakeIndices();
    } else {
        // In non-demo modes, hide the SIM tags or show real data
        document.querySelectorAll('.sim-tag').forEach(el => el.style.display = 'none');
    }

    // 5. Fetch server IST clock immediately
    fetchServerClock();
}

async function init() {
    // Check if already logged in (session cookie persists across refresh)
    const loggedIn = await checkAuth();
    if (loggedIn) {
        startApp();
    } else {
        // Show login overlay
        showAuth();
    }
}

init();
