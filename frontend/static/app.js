/**
 * AlgoTerminal v3 — Professional Trading Dashboard
 * ==================================================
 * - TradingView Lightweight Charts with proper candlesticks
 * - Each backend tick = 1 base candle (sequential timestamps)
 * - Timeframe buttons aggregate N ticks into 1 candle
 * - SMA / EMA / BB overlays computed from candle history
 * - Optimised: chart updates batched, PnL throttled
 */

// ── Config ──────────────────────────────────────────────────
const API = '';
const MAX_CANDLES = 300;
const MAX_ORDERS = 150;
const MAX_TRADES = 200;
const MAX_SIGNALS = 100;
const PNL_THROTTLE = 400;

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

// Per-symbol raw tick buffers (for chart reconstruction on symbol switch)
const tickBuffers = {};          // { 'RELIANCE.NS': [{o,h,l,c,v,t}, ...], ... }
// Current candle accumulator for the selected symbol
let pendingTicks = [];
// Completed candle array for chart
let candleData = [];
let volumeData = [];
// Sequential time counter (seconds, increments per candle)
let nextCandleTime = Math.floor(Date.now() / 1000) - MAX_CANDLES * 60;
const CANDLE_DT = 60;            // spacing between candles in seconds

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
    if (!iso) return '';
    return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function sideClass(s) { return s === 'BUY' ? 'side-buy' : 'side-sell'; }
function statusClass(s) { return 'st-' + (s || 'new').toLowerCase(); }
function n(v, d = 2) { return Number(v).toFixed(d); }

// Clock
setInterval(() => {
    const d = new Date();
    clock.textContent = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}, 1000);

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
// Time is assigned sequentially (not from wall-clock) so
// candles spread out properly on the chart.

function resetChartData() {
    candleData = [];
    volumeData = [];
    pendingTicks = [];
    nextCandleTime = Math.floor(Date.now() / 1000) - MAX_CANDLES * CANDLE_DT;
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

    const candle = { time: nextCandleTime, open: o, high: h, low: l, close: c };
    const vol = { time: nextCandleTime, value: v, color: c >= o ? '#0ecb8130' : '#f6465d30' };

    nextCandleTime += CANDLE_DT;
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
    selectedSymbol = sym;
    symSel.value = sym;
    chSym.textContent = sym;
    const ofSym = $('of-sym');
    if (ofSym) ofSym.value = sym;
    updateOrderBtn();

    // Reset chart and rebuild from buffer
    resetChartData();

    // Replay buffered ticks for this symbol
    const buf = tickBuffers[sym] || [];
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

    // Watchlist active highlight
    document.querySelectorAll('.wl-row').forEach(r => {
        r.classList.toggle('active', r.dataset.sym === sym);
    });
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
//  WEBSOCKET
// ═══════════════════════════════════════════════════════════
let socket;

function connectSocket() {
    socket = io({ transports: ['websocket', 'polling'], reconnectionDelay: 1000 });

    socket.on('connect', () => {
        connBadge.className = 'badge badge-on';
        connBadge.innerHTML = '<span class="dot"></span>Connected';
        socket.emit('request_state');
    });

    socket.on('disconnect', () => {
        connBadge.className = 'badge badge-off';
        connBadge.innerHTML = '<span class="dot"></span>Offline';
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

    // ── Order Update ──
    socket.on('order_update', order => {
        upsertOrder(order);
        if (order.status === 'FILLED' || order.status === 'PARTIAL') addTrade(order);
    });

    // ── Position ──
    socket.on('position_update', data => renderPositions(data.positions));

    // ── PnL (throttled) ──
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
        if (st.running) {
            engBadge.className = 'badge badge-run'; engBadge.textContent = 'Running';
        } else {
            engBadge.className = 'badge badge-idle'; engBadge.textContent = 'Idle';
        }
        if (st.strategy) stName.textContent = st.strategy;
        stMl.textContent = st.use_ml ? 'ON' : 'OFF';
        stMl.style.color = st.use_ml ? 'var(--green)' : 'var(--text2)';
    });
}

// ═══════════════════════════════════════════════════════════
//  ORDERS TABLE
// ═══════════════════════════════════════════════════════════
function upsertOrder(order) {
    const idx = orderList.findIndex(o => o.order_id === order.order_id);
    if (idx >= 0) orderList[idx] = order;
    else { orderList.unshift(order); if (orderList.length > MAX_ORDERS) orderList.pop(); }
    renderOrders();
}

function renderOrders() {
    if (!orderList.length) {
        orderBody.innerHTML = '<tr><td colspan="10" class="empty">No orders yet</td></tr>';
        cntOrders.textContent = '0'; return;
    }
    cntOrders.textContent = orderList.length;
    let h = '';
    for (const o of orderList) {
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
}

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
    try {
        await fetch(API + '/api/place-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: sym, side: cs, qty, price: 0 }),
        });
    } catch (e) { console.error('Close fail', e); }
};

// ═══════════════════════════════════════════════════════════
//  TRADE LOG
// ═══════════════════════════════════════════════════════════
function addTrade(order) {
    if (tradeList.find(t => t.order_id === order.order_id && t.status === order.status)) return;
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
        h += `<tr>
            <td>${shortTime(t.updated_at)}</td>
            <td>${t.symbol}</td>
            <td class="${sideClass(t.side)}">${t.side}</td>
            <td>${t.filled_qty || t.qty}</td>
            <td>${n(t.avg_price || t.price || 0)}</td>
            <td>${t.strategy || 'manual'}</td>
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
    fetch(API + '/api/start', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy: stratSel.value }),
    });
});

btnStop.addEventListener('click', () => {
    socket.emit('control', { action: 'stop' });
    fetch(API + '/api/stop', { method: 'POST' });
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
    const sym = ofSym ? ofSym.value.replace('.NS', '') : '';
    btn.textContent = selectedSide + ' ' + sym;
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
//  LOAD EXISTING DATA + FAKE INDICES
// ═══════════════════════════════════════════════════════════
async function loadExisting() {
    try {
        const res = await fetch(API + '/api/orders');
        const d = await res.json();
        if (d.orders) d.orders.forEach(o => upsertOrder(o));
    } catch (e) { }
}

function fakeIndices() {
    const base = { nifty: 25800, sensex: 83800, banknifty: 60700 };
    function tick() {
        for (const [name, b] of Object.entries(base)) {
            const v = b + (Math.random() - 0.48) * 80;
            base[name] = v;
            const el = $('idx-' + name);
            if (el) {
                el.textContent = Math.round(v).toLocaleString('en-IN');
                el.style.color = v >= 25800 && name === 'nifty' || v >= 83800 && name === 'sensex' || v >= 60700 && name === 'banknifty' ? 'var(--green)' : 'var(--red)';
            }
        }
    }
    tick();
    setInterval(tick, 2500);
}

// ═══════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════
buildWatchlist();
connectSocket();
loadExisting();
fakeIndices();
updateOrderBtn();
