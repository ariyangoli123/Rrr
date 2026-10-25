// TV Candle Hider
// The TradingView chart is drawn on a <canvas>, so candles are not HTML elements
// and cannot be removed by editing the DOM. Instead this script lays an opaque
// HTML curtain over the chart from the cutoff candle to the right edge, and uses
// the page's chart API (window.TradingViewApi) to find where that candle is
// drawn, so the curtain follows zoom/scroll and works on every timeframe.
(() => {
  'use strict';

  const STORE_KEY = 'tvch-state';
  const WEEK_OFFSET = 4 * 86400; // 1970-01-05 was a Monday; TradingView weeks start Monday

  // State: cutoff = first hidden moment (unix seconds, UTC).
  let state = loadState();

  function loadState() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
    } catch (e) {
      return {};
    }
  }

  function saveState() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (e) {}
  }

  function api() {
    return window.TradingViewApi;
  }

  function chart() {
    const a = api();
    return a && typeof a.activeChart === 'function' ? a.activeChart() : null;
  }

  // ----- timeframe math -----

  function parseResolution(res) {
    const m = /^(\d*)([SDWM]?)$/i.exec(String(res).trim());
    if (!m) return null;
    const n = parseInt(m[1] || '1', 10);
    const unit = m[2].toUpperCase();
    if (unit === 'S') return { kind: 'fixed', sec: n };
    if (unit === 'D') return { kind: 'fixed', sec: n * 86400 };
    if (unit === 'W') return { kind: 'week', sec: n * 604800 };
    if (unit === 'M') return { kind: 'month', months: n };
    return { kind: 'fixed', sec: n * 60 };
  }

  // Start of the bar that contains time t.
  function barStart(t, r) {
    if (r.kind === 'fixed') return Math.floor(t / r.sec) * r.sec;
    if (r.kind === 'week') return Math.floor((t - WEEK_OFFSET) / r.sec) * r.sec + WEEK_OFFSET;
    const d = new Date(t * 1000);
    const idx = d.getUTCFullYear() * 12 + d.getUTCMonth();
    const start = idx - (idx % r.months);
    return Date.UTC(Math.floor(start / 12), start % 12, 1) / 1000;
  }

  function addBars(t, r, count) {
    if (r.kind !== 'month') return t + r.sec * count;
    const d = new Date(t * 1000);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + r.months * count, 1) / 1000;
  }

  function currentResolution() {
    const c = chart();
    const r = c && parseResolution(c.resolution());
    if (!r) throw new Error('این تایم‌فریم پشتیبانی نمی‌شود (مثلاً تیک یا رنج).');
    return r;
  }

  // ----- colors -----

  function curtainColor() {
    return state.color || detectBackground();
  }

  function detectBackground() {
    const el = document.querySelector('.chart-container') || document.body;
    const bg = getComputedStyle(el).backgroundColor;
    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return rgbToHex(bg);
    return document.documentElement.classList.contains('theme-dark') ? '#131722' : '#ffffff';
  }

  function rgbToHex(rgb) {
    const m = rgb.match(/\d+/g);
    if (!m) return '#131722';
    return '#' + m.slice(0, 3).map((x) => (+x).toString(16).padStart(2, '0')).join('');
  }

  // ----- curtain overlay -----

  let overlay = null;
  let rafId = 0;
  let lastError = '';

  function chartContainer() {
    return document.querySelector('.chart-container.active') || document.querySelector('.chart-container');
  }

  // Bounding box of the chart panes (main pane plus indicator panes). They are the
  // widest canvases in the chart; the price axis is narrower and the time axis is short.
  function paneRect(container) {
    const rects = [...container.querySelectorAll('canvas:not([data-tvch])')]
      .map((c) => c.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0);
    if (!rects.length) return null;
    const maxW = Math.max(...rects.map((r) => r.width));
    const panes = rects.filter((r) => Math.abs(r.width - maxW) < 2 && r.height > 40);
    if (!panes.length) return null;
    return {
      left: panes[0].left,
      width: maxW,
      top: Math.min(...panes.map((r) => r.top)),
      bottom: Math.max(...panes.map((r) => r.bottom)),
    };
  }

  // X offset (from the pane's left edge) where the first hidden bar's slot begins.
  function edgeX(c, firstHidden, width, r) {
    let ts = null;
    try {
      ts = typeof c.getTimeScale === 'function' ? c.getTimeScale() : null;
    } catch (e) {}
    if (ts && typeof ts.coordinateToTime === 'function') {
      // coordinateToTime returns the bar under x, so the smallest x whose bar is
      // hidden is exactly the left edge of the first hidden candle's slot.
      const hidden = (x) => {
        const t = ts.coordinateToTime(x);
        return t == null ? x > width / 2 : t >= firstHidden;
      };
      if (hidden(0)) return 0;
      let lo = 0;
      let hi = width;
      while (hi - lo > 0.5) {
        const mid = (lo + hi) / 2;
        if (hidden(mid)) hi = mid;
        else lo = mid;
      }
      return lo;
    }
    // Fallback: linear map of the visible time range (accurate for 24/7 markets).
    const range = c.getVisibleRange();
    const span = range.to - range.from;
    if (!(span > 0)) return 0;
    const barW = (width * (addBars(firstHidden, r, 1) - firstHidden)) / span;
    const centerX = ((firstHidden - range.from) / span) * width;
    return centerX - barW / 2 - (state.safeEdge === false ? 0 : barW / 2);
  }

  function ensureOverlay(container) {
    if (overlay && overlay.parentNode === container) return overlay;
    if (overlay) overlay.remove();
    if (getComputedStyle(container).position === 'static') container.style.position = 'relative';
    overlay = document.createElement('div');
    overlay.id = 'tvch-curtain';
    // Opaque HTML on top of the canvas: TradingView cannot redraw or fade it, and it
    // swallows the mouse so hovering hidden bars does not show their OHLC values.
    overlay.style.cssText = 'position:absolute;z-index:50;pointer-events:auto;cursor:default;';
    container.appendChild(overlay);
    return overlay;
  }

  function reportError(msg) {
    if (msg !== lastError) setStatus(msg);
    lastError = msg;
  }

  // Runs every animation frame so the curtain follows scrolling, zooming and resizing.
  function updateOverlay() {
    rafId = 0;
    if (!state.active) return;
    try {
      const c = chart();
      const container = chartContainer();
      const pane = container && paneRect(container);
      if (!c || !pane) throw new Error('نمودار پیدا نشد.');
      const r = currentResolution();
      const x = Math.min(pane.width, Math.max(0, edgeX(c, barStart(state.cutoff, r), pane.width, r)));
      const box = container.getBoundingClientRect();
      const el = ensureOverlay(container);
      el.style.left = pane.left - box.left + x + 'px';
      el.style.top = pane.top - box.top + 'px';
      el.style.width = pane.width - x + 'px';
      el.style.height = pane.bottom - pane.top + 'px';
      el.style.background = curtainColor();
      el.style.display = '';
      reportError('');
    } catch (e) {
      if (overlay) overlay.style.display = 'none';
      reportError(e.message || String(e));
    }
    rafId = requestAnimationFrame(updateOverlay);
  }

  async function removeCurtain() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    if (overlay) overlay.remove();
    overlay = null;
  }

  async function drawCurtain() {
    if (!chart()) throw new Error('API تریدینگ‌ویو پیدا نشد. صفحه را رفرش کنید.');
    currentResolution();
    if (!rafId) rafId = requestAnimationFrame(updateOverlay);
  }

  // ----- actions -----

  async function run(fn) {
    try {
      await fn();
      setStatus('');
    } catch (e) {
      setStatus(e.message || String(e));
    }
    refreshInfo();
  }

  function hideFrom(dateValue) {
    return run(async () => {
      if (!dateValue) throw new Error('اول تاریخ را انتخاب کنید.');
      const t = Math.floor(new Date(dateValue).getTime() / 1000);
      if (!isFinite(t)) throw new Error('تاریخ نامعتبر است.');
      state.cutoff = barStart(t, currentResolution());
      state.active = true;
      saveState();
      await drawCurtain();
    });
  }

  function step(dir) {
    return run(async () => {
      if (!state.active) throw new Error('اول کندل‌ها را مخفی کنید.');
      const r = currentResolution();
      state.cutoff = addBars(barStart(state.cutoff, r), r, dir);
      saveState();
      await drawCurtain();
    });
  }

  function showAll() {
    return run(async () => {
      state.active = false;
      saveState();
      await removeCurtain();
    });
  }

  // ----- indicators -----
  // Computed by this extension from the candle data, so they are not TradingView
  // indicators. They only ever see candles older than the curtain edge, so a
  // hidden candle can never leak into a line. Colors, line styles, legends and
  // price-axis tags copy TradingView's defaults for the built-in indicators.
  const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';
  const BLUE = '#2962FF';
  const ORANGE = '#FF6D00';
  const PURPLE = '#7E57C2';
  const GREY = '#787B86';
  const SUB_MIN = 70;
  const SUB_MAX = 140;

  const line = (title, vals, color, extra) => ({ title, vals, color, style: 'line', width: 1, ...extra });
  const band = (v, dash) => ({ v, dash: dash || [4, 4], alpha: dash ? 0.5 : 1 });

  // type -> definition. calc(d, params) gets {open, high, low, close, volume, hl2, hlc3, time}.
  const IND = {
    sma: { name: 'MA', group: 'main', params: [9], calc: (d, [n]) => ({ plots: [line('MA', sma(d.close, n), BLUE)] }) },
    ema: { name: 'EMA', group: 'main', params: [9], calc: (d, [n]) => ({ plots: [line('EMA', ema(d.close, n), BLUE)] }) },
    wma: { name: 'WMA', group: 'main', params: [9], calc: (d, [n]) => ({ plots: [line('WMA', wma(d.close, n), BLUE)] }) },
    bb: {
      name: 'BB',
      group: 'main',
      params: [20, 2],
      calc: (d, [n, m]) => {
        const basis = sma(d.close, n);
        const sd = stdev(d.close, n);
        const up = basis.map((b, i) => (b == null ? null : b + m * sd[i]));
        const lo = basis.map((b, i) => (b == null ? null : b - m * sd[i]));
        return {
          plots: [line('Basis', basis, ORANGE), line('Upper', up, BLUE), line('Lower', lo, BLUE)],
          fills: [{ a: up, b: lo, color: 'rgba(33,150,243,0.05)' }],
        };
      },
    },
    dc: {
      name: 'DC',
      group: 'main',
      params: [20],
      calc: (d, [n]) => {
        const up = highest(d.high, n);
        const lo = lowest(d.low, n);
        const basis = up.map((u, i) => (u == null ? null : (u + lo[i]) / 2));
        return {
          plots: [line('Lower', lo, BLUE), line('Basis', basis, ORANGE), line('Upper', up, BLUE)],
          fills: [{ a: up, b: lo, color: 'rgba(33,150,243,0.05)' }],
        };
      },
    },
    vwap: { name: 'VWAP', group: 'main', params: [], calc: (d) => ({ plots: [line('VWAP', vwap(d), BLUE)] }) },
    ichimoku: {
      name: 'Ichimoku',
      group: 'main',
      params: [9, 26, 52, 26],
      calc: (d, [c, b, s, disp]) => {
        const mid = (n) => highest(d.high, n).map((h, i) => (h == null ? null : (h + lowest(d.low, n)[i]) / 2));
        const conv = mid(c);
        const base = mid(b);
        const leadA = conv.map((v, i) => (v == null || base[i] == null ? null : (v + base[i]) / 2));
        const leadB = mid(s);
        const off = disp - 1;
        return {
          plots: [
            line('Conversion Line', conv, BLUE),
            line('Base Line', base, '#B71C1C'),
            line('Lagging Span', d.close, '#43A047', { offset: -off }),
            line('Leading Span A', leadA, '#A5D6A7', { offset: off }),
            line('Leading Span B', leadB, '#EF9A9A', { offset: off }),
          ],
          fills: [
            {
              a: leadA,
              b: leadB,
              offset: off,
              colors: leadA.map((v, i) => (v != null && leadB[i] != null && v > leadB[i] ? 'rgba(67,160,71,0.1)' : 'rgba(244,67,54,0.1)')),
            },
          ],
        };
      },
    },
    supertrend: {
      name: 'Supertrend',
      group: 'main',
      params: [10, 3],
      calc: (d, [n, f]) => {
        const { st, dir } = supertrend(d, n, f);
        const bodyMid = d.open.map((o, i) => (o + d.close[i]) / 2);
        const up = st.map((v, i) => (dir[i] < 0 ? v : null));
        const dn = st.map((v, i) => (dir[i] > 0 ? v : null));
        return {
          plots: [line('Up Trend', up, '#4CAF50'), line('Down Trend', dn, '#FF5252')],
          fills: [
            { a: up, b: bodyMid, color: 'rgba(76,175,80,0.1)' },
            { a: dn, b: bodyMid, color: 'rgba(255,82,82,0.1)' },
          ],
        };
      },
    },
    sar: {
      name: 'SAR',
      group: 'main',
      params: [0.02, 0.02, 0.2],
      calc: (d, [s, inc, max]) => ({ plots: [line('ParabolicSAR', sar(d, s, inc, max), BLUE, { style: 'cross' })] }),
    },
    volume: {
      name: 'Vol',
      group: 'main',
      params: [],
      calc: (d) => ({
        volume: true,
        plots: [
          {
            title: 'Volume',
            vals: d.volume,
            style: 'columns',
            colors: d.close.map((c, i) => (c >= d.open[i] ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)')),
            compact: true,
          },
        ],
      }),
    },
    rsi: {
      name: 'RSI',
      group: 'sub',
      params: [14],
      calc: (d, [n]) => ({
        plots: [line('RSI', rsi(d.close, n), PURPLE)],
        bands: [band(70), band(50, [1, 3]), band(30)],
        bandFill: [70, 30, 'rgba(126,87,194,0.1)'],
      }),
    },
    macd: {
      name: 'MACD',
      group: 'sub',
      params: [12, 26, 9],
      calc: (d, [f, s, sig]) => {
        const fast = ema(d.close, f);
        const slow = ema(d.close, s);
        const m = fast.map((v, i) => (v == null || slow[i] == null ? null : v - slow[i]));
        const signal = ema(m, sig);
        const hist = m.map((v, i) => (v == null || signal[i] == null ? null : v - signal[i]));
        const colors = hist.map((h, i) => {
          const rising = i > 0 && hist[i - 1] != null && hist[i - 1] < h;
          return h >= 0 ? (rising ? '#26A69A' : '#B2DFDB') : rising ? '#FFCDD2' : '#FF5252';
        });
        return {
          plots: [
            { title: 'Histogram', vals: hist, style: 'hist', colors },
            line('MACD', m, BLUE),
            line('Signal', signal, ORANGE),
          ],
          bands: [{ v: 0, dash: [], alpha: 0.5 }],
        };
      },
    },
    stoch: {
      name: 'Stoch',
      group: 'sub',
      params: [14, 1, 3],
      calc: (d, [n, sk, sd]) => {
        const hh = highest(d.high, n);
        const ll = lowest(d.low, n);
        const raw = d.close.map((c, i) => (hh[i] == null ? null : hh[i] === ll[i] ? 0 : (100 * (c - ll[i])) / (hh[i] - ll[i])));
        const k = sma(raw, sk);
        return {
          plots: [line('%K', k, BLUE), line('%D', sma(k, sd), ORANGE)],
          bands: [band(80), band(50, [1, 3]), band(20)],
          bandFill: [80, 20, 'rgba(33,150,243,0.1)'],
        };
      },
    },
    cci: {
      name: 'CCI',
      group: 'sub',
      params: [20],
      calc: (d, [n]) => {
        const src = d.hlc3;
        const ma = sma(src, n);
        const out = src.map((v, i) => {
          if (ma[i] == null) return null;
          let dev = 0;
          for (let j = i - n + 1; j <= i; j++) dev += Math.abs(src[j] - ma[i]);
          dev /= n;
          return dev === 0 ? 0 : (v - ma[i]) / (0.015 * dev);
        });
        return {
          plots: [line('CCI', out, BLUE)],
          bands: [band(100), band(0, [1, 3]), band(-100)],
          bandFill: [100, -100, 'rgba(33,150,243,0.1)'],
        };
      },
    },
    willr: {
      name: '%R',
      group: 'sub',
      params: [14],
      calc: (d, [n]) => {
        const hh = highest(d.high, n);
        const ll = lowest(d.low, n);
        const out = d.close.map((c, i) => (hh[i] == null ? null : hh[i] === ll[i] ? 0 : (100 * (c - hh[i])) / (hh[i] - ll[i])));
        return {
          plots: [line('%R', out, PURPLE)],
          bands: [band(-20), band(-50, [1, 3]), band(-80)],
          bandFill: [-20, -80, 'rgba(126,87,194,0.1)'],
        };
      },
    },
    atr: { name: 'ATR', group: 'sub', params: [14], calc: (d, [n]) => ({ plots: [line('ATR', rma(trueRange(d), n), '#B71C1C')] }) },
    dmi: {
      name: 'DMI',
      group: 'sub',
      params: [14, 14],
      calc: (d, [n, sm]) => {
        const { plus, minus, adx } = dmi(d, n, sm);
        return { plots: [line('ADX', adx, '#F50057'), line('+DI', plus, BLUE), line('-DI', minus, ORANGE)] };
      },
    },
    obv: {
      name: 'OBV',
      group: 'sub',
      params: [],
      calc: (d) => {
        let acc = 0;
        const out = d.close.map((c, i) => {
          if (i > 0) acc += c > d.close[i - 1] ? d.volume[i] : c < d.close[i - 1] ? -d.volume[i] : 0;
          return acc;
        });
        return { plots: [line('OBV', out, BLUE, { compact: true })] };
      },
    },
  };

  const IND_TITLES = {
    sma: 'Moving Average',
    ema: 'Moving Average Exponential',
    wma: 'Moving Average Weighted',
    bb: 'Bollinger Bands',
    dc: 'Donchian Channels',
    vwap: 'VWAP',
    ichimoku: 'Ichimoku Cloud',
    supertrend: 'Supertrend',
    sar: 'Parabolic SAR',
    volume: 'Volume',
    rsi: 'Relative Strength Index',
    macd: 'MACD',
    stoch: 'Stochastic',
    cci: 'Commodity Channel Index',
    willr: 'Williams %R',
    atr: 'Average True Range',
    dmi: 'Directional Movement Index',
    obv: 'On Balance Volume',
  };

  let bars = [];
  let barsKey = '';
  let barsStamp = 0;
  let fetchedFrom = 0;
  let fetchedTo = 0;
  let fetchedAt = 0;
  let barsBusy = false;
  let dataSource = '';
  let indError = '';

  function barSeconds(r) {
    return r.kind === 'month' ? r.months * 2630000 : r.sec;
  }

  function barsFromExport(res) {
    const schema = res.schema || [];
    const find = (name) =>
      schema.findIndex((x) =>
        name === 'time'
          ? x.type === 'time'
          : String(x.plotTitle || x.title || x.name || '').toLowerCase() === name
      );
    const it = find('time');
    const ic = find('close');
    if (it < 0 || ic < 0) throw new Error('ساختار داده exportData ناشناخته است.');
    const io = find('open');
    const ih = find('high');
    const il = find('low');
    const iv = find('volume');
    return res.data
      .map((row) => ({
        t: row[it],
        o: row[io],
        h: row[ih],
        l: row[il],
        c: row[ic],
        v: iv >= 0 ? row[iv] || 0 : 0,
      }))
      .filter((b) => b.c != null);
  }

  // Fallback: read the main series straight from the chart widget internals.
  function barsFromInternals() {
    const a = api();
    let w = null;
    try {
      w = a._activeChartWidgetWV && a._activeChartWidgetWV.value();
    } catch (e) {}
    const cw = (w && w._chartWidget) || (chart() && chart()._chartWidget);
    const series = cw && cw.model().mainSeries();
    const b = series && series.bars();
    if (!b) throw new Error('داده کندل‌ها در دسترس نیست.');
    const out = [];
    for (let i = b.firstIndex(); i <= b.lastIndex(); i++) {
      const v = b.valueAt(i);
      const row = Array.isArray(v) ? v : v && v.value;
      if (row && row[4] != null) out.push({ t: row[0], o: row[1], h: row[2], l: row[3], c: row[4], v: row[5] || 0 });
    }
    return out;
  }

  async function refreshBars(c, r) {
    if (barsBusy) return;
    const key = c.symbol() + '|' + c.resolution();
    const range = c.getVisibleRange();
    const bs = barSeconds(r);
    const stale =
      key !== barsKey ||
      range.from < fetchedFrom + 200 * bs ||
      range.to > fetchedTo - 5 * bs ||
      Date.now() - fetchedAt > 5000;
    if (!stale) return;
    barsBusy = true;
    try {
      const from = Math.floor(range.from - 1500 * bs);
      const to = Math.ceil(range.to + 100 * bs);
      let got = null;
      try {
        if (typeof c.exportData !== 'function') throw new Error('no exportData');
        got = barsFromExport(await c.exportData({ from, to, includeTime: true, includeSeries: true, includedStudies: [] }));
        dataSource = 'exportData';
      } catch (e) {
        got = barsFromInternals();
        dataSource = 'internals';
      }
      bars = got.sort((x, y) => x.t - y.t);
      priceDecimals = decimalsOf(bars);
      barsKey = key;
      fetchedFrom = from;
      fetchedTo = to;
      fetchedAt = Date.now();
      barsStamp++;
    } catch (e) {
      indError = e.message || String(e);
    } finally {
      barsBusy = false;
    }
  }

  // Time -> x: sample coordinateToTime across the pane and take each bar's middle.
  function buildXMap(ts, width) {
    const m = new Map();
    for (let x = 0; x <= width; x++) {
      const t = ts.coordinateToTime(x);
      if (t == null) continue;
      const e = m.get(t);
      if (e) e[1] = x;
      else m.set(t, [x, x]);
    }
    return m;
  }

  function makeYMap(c, rectHeight) {
    const pane = c.getPanes()[0];
    const ps = pane.getMainSourcePriceScale ? pane.getMainSourcePriceScale() : null;
    if (!ps || typeof ps.getVisiblePriceRange !== 'function')
      throw new Error('محور قیمت در دسترس نیست؛ اندیکاتورها قابل رسم نیستند.');
    const rg = ps.getVisiblePriceRange();
    const lo = rg.from != null ? rg.from : rg.min;
    const hi = rg.to != null ? rg.to : rg.max;
    const mode = ps.getMode ? ps.getMode() : 0;
    const h = pane.getHeight ? pane.getHeight() : rectHeight;
    if (mode === 1) {
      const a = Math.log(hi);
      const b = Math.log(lo);
      return (v) => ((a - Math.log(v)) / (a - b)) * h;
    }
    if (mode !== 0) throw new Error('مقیاس محور قیمت باید Regular یا Log باشد.');
    return (v) => ((hi - v) / (hi - lo)) * h;
  }

  function mainPaneRect(container) {
    const paneBox = paneRect(container);
    if (!paneBox) return null;
    const rects = [...container.querySelectorAll('canvas:not([data-tvch])')]
      .map((el) => el.getBoundingClientRect())
      .filter((q) => Math.abs(q.width - paneBox.width) < 2 && q.height > 40)
      .sort((x, y) => x.top - y.top);
    return rects[0] || null;
  }

  function sizedCanvas(existing, container, z, left, top, w, h) {
    let cv = existing;
    if (!cv || cv.parentNode !== container) {
      if (cv) cv.remove();
      cv = document.createElement('canvas');
      cv.dataset.tvch = '1'; // excluded when locating TradingView's own canvases
      cv.style.cssText = `position:absolute;z-index:${z};pointer-events:none;`;
      container.appendChild(cv);
    }
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
    }
    Object.assign(cv.style, { left: left + 'px', top: top + 'px', width: w + 'px', height: h + 'px' });
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return cv;
  }

  // ----- indicator math (matches Pine's ta.* functions) -----

  // Run fn on the series after its leading nulls, then pad the nulls back.
  function afterLead(fn) {
    return (a, ...args) => {
      const f = a.findIndex((v) => v != null);
      if (f < 0) return a.map(() => null);
      return Array(f).fill(null).concat(fn(a.slice(f), ...args));
    };
  }

  const sma = afterLead((a, p) => {
    const out = [];
    let sum = 0;
    for (let i = 0; i < a.length; i++) {
      sum += a[i];
      if (i >= p) sum -= a[i - p];
      out.push(i >= p - 1 ? sum / p : null);
    }
    return out;
  });

  // Exponential average seeded with the SMA of the first p values, like ta.ema/ta.rma.
  function smoothed(alphaOf) {
    return afterLead((a, p) => {
      const k = alphaOf(p);
      const out = [];
      let prev = null;
      for (let i = 0; i < a.length; i++) {
        if (i < p - 1) out.push(null);
        else {
          prev = i === p - 1 ? a.slice(0, p).reduce((x, y) => x + y, 0) / p : a[i] * k + prev * (1 - k);
          out.push(prev);
        }
      }
      return out;
    });
  }

  const ema = smoothed((p) => 2 / (p + 1));
  const rma = smoothed((p) => 1 / p);

  const wma = afterLead((a, p) => {
    const den = (p * (p + 1)) / 2;
    return a.map((_, i) => {
      if (i < p - 1) return null;
      let s = 0;
      for (let j = 0; j < p; j++) s += a[i - j] * (p - j);
      return s / den;
    });
  });

  function stdev(a, p) {
    const mean = sma(a, p);
    return a.map((_, i) => {
      if (mean[i] == null) return null;
      let v = 0;
      for (let j = i - p + 1; j <= i; j++) v += (a[j] - mean[i]) ** 2;
      return Math.sqrt(v / p);
    });
  }

  function windowed(a, p, pick) {
    return a.map((_, i) => (i < p - 1 ? null : pick(a.slice(i - p + 1, i + 1))));
  }

  const highest = (a, p) => windowed(a, p, (w) => Math.max(...w));
  const lowest = (a, p) => windowed(a, p, (w) => Math.min(...w));

  function trueRange(d) {
    return d.high.map((h, i) =>
      i === 0 ? h - d.low[i] : Math.max(h - d.low[i], Math.abs(h - d.close[i - 1]), Math.abs(d.low[i] - d.close[i - 1]))
    );
  }

  function rsi(close, n) {
    const up = close.map((c, i) => (i === 0 ? null : Math.max(c - close[i - 1], 0)));
    const dn = close.map((c, i) => (i === 0 ? null : Math.max(close[i - 1] - c, 0)));
    const u = rma(up, n);
    const dd = rma(dn, n);
    return u.map((v, i) => (v == null ? null : dd[i] === 0 ? 100 : v === 0 ? 0 : 100 - 100 / (1 + v / dd[i])));
  }

  // Resets every UTC day, like the session VWAP on a 24/7 market.
  function vwap(d) {
    let day = null;
    let pv = 0;
    let vol = 0;
    return d.close.map((_, i) => {
      const dayNo = Math.floor(d.time[i] / 86400);
      if (dayNo !== day) {
        day = dayNo;
        pv = 0;
        vol = 0;
      }
      const w = d.volume[i] || 1;
      pv += d.hlc3[i] * w;
      vol += w;
      return pv / vol;
    });
  }

  // Port of ta.supertrend: dir < 0 is an up trend.
  function supertrend(d, n, f) {
    const atr = rma(trueRange(d), n);
    const st = [];
    const dir = [];
    let prevUp = null;
    let prevLo = null;
    let prevSt = null;
    for (let i = 0; i < d.close.length; i++) {
      if (atr[i] == null) {
        st.push(null);
        dir.push(0);
        continue;
      }
      let up = d.hl2[i] + f * atr[i];
      let lo = d.hl2[i] - f * atr[i];
      if (prevLo != null && !(lo > prevLo || d.close[i - 1] < prevLo)) lo = prevLo;
      if (prevUp != null && !(up < prevUp || d.close[i - 1] > prevUp)) up = prevUp;
      let dr;
      if (atr[i - 1] == null) dr = 1;
      else if (prevSt === prevUp) dr = d.close[i] > up ? -1 : 1;
      else dr = d.close[i] < lo ? 1 : -1;
      const v = dr < 0 ? lo : up;
      st.push(v);
      dir.push(dr);
      prevUp = up;
      prevLo = lo;
      prevSt = v;
    }
    return { st, dir };
  }

  // Port of the reference ta.sar implementation from the Pine manual.
  function sar(d, start, inc, max) {
    const out = [null];
    let result = null;
    let maxMin = null;
    let accel = null;
    let below = null;
    for (let i = 1; i < d.close.length; i++) {
      const h = d.high;
      const l = d.low;
      let first = false;
      if (i === 1) {
        if (d.close[1] > d.close[0]) {
          below = true;
          maxMin = h[1];
          result = l[0];
        } else {
          below = false;
          maxMin = l[1];
          result = h[0];
        }
        first = true;
        accel = start;
      }
      result += accel * (maxMin - result);
      if (below) {
        if (result > l[i]) {
          first = true;
          below = false;
          result = Math.max(h[i], maxMin);
          maxMin = l[i];
          accel = start;
        }
      } else if (result < h[i]) {
        first = true;
        below = true;
        result = Math.min(l[i], maxMin);
        maxMin = h[i];
        accel = start;
      }
      if (!first) {
        if (below && h[i] > maxMin) {
          maxMin = h[i];
          accel = Math.min(accel + inc, max);
        } else if (!below && l[i] < maxMin) {
          maxMin = l[i];
          accel = Math.min(accel + inc, max);
        }
      }
      if (below) {
        result = Math.min(result, l[i - 1]);
        if (i > 1) result = Math.min(result, l[i - 2]);
      } else {
        result = Math.max(result, h[i - 1]);
        if (i > 1) result = Math.max(result, h[i - 2]);
      }
      out.push(result);
    }
    return out;
  }

  function dmi(d, n, sm) {
    const plusDm = d.high.map((h, i) => {
      if (i === 0) return null;
      const up = h - d.high[i - 1];
      const down = d.low[i - 1] - d.low[i];
      return up > down && up > 0 ? up : 0;
    });
    const minusDm = d.low.map((l, i) => {
      if (i === 0) return null;
      const up = d.high[i] - d.high[i - 1];
      const down = d.low[i - 1] - l;
      return down > up && down > 0 ? down : 0;
    });
    const tr = rma(trueRange(d).map((v, i) => (i === 0 ? null : v)), n);
    const plus = rma(plusDm, n).map((v, i) => (v == null || !tr[i] ? null : (100 * v) / tr[i]));
    const minus = rma(minusDm, n).map((v, i) => (v == null || !tr[i] ? null : (100 * v) / tr[i]));
    const dx = plus.map((p, i) => (p == null || minus[i] == null ? null : Math.abs(p - minus[i]) / (p + minus[i] || 1)));
    const adx = rma(dx, sm).map((v) => (v == null ? null : 100 * v));
    return { plus, minus, adx };
  }

  // ----- indicator rendering -----

  let calcCache = { key: '', list: [], useLen: 0, timeIndex: new Map() };
  let priceDecimals = 2;
  let mainCanvas = null;
  let axisCanvas = null;
  let subCanvases = [];
  let legendEls = [];
  let mouseX = null;
  let trackedContainer = null;

  // Smallest number of decimals that represents every recent close.
  function decimalsOf(list) {
    const closes = list.slice(-100).map((b) => b.c);
    for (let dec = 0; dec <= 8; dec++) {
      const f = 10 ** dec;
      if (closes.every((c) => Math.abs(c * f - Math.round(c * f)) < 1e-4)) return dec;
    }
    return 8;
  }

  function seriesOf(use) {
    const d = { time: [], open: [], high: [], low: [], close: [], volume: [], hl2: [], hlc3: [] };
    for (const b of use) {
      const hi = b.h ?? b.c;
      const lo = b.l ?? b.c;
      d.time.push(b.t);
      d.open.push(b.o ?? b.c);
      d.high.push(hi);
      d.low.push(lo);
      d.close.push(b.c);
      d.volume.push(b.v || 0);
      d.hl2.push((hi + lo) / 2);
      d.hlc3.push((hi + lo + b.c) / 3);
    }
    return d;
  }

  // Older versions stored {p, m} and their own palette; move them to TradingView defaults.
  function normInds() {
    state.inds = (state.inds || []).filter((x) => IND[x.type]);
    for (const ind of state.inds) {
      if (!Array.isArray(ind.params)) {
        const def = IND[ind.type].params;
        ind.params = ind.type === 'bb' ? [ind.p || 20, ind.m || 2] : def.length ? [ind.p || def[0], ...def.slice(1)] : [];
        delete ind.color;
      }
      if (ind.visible == null) ind.visible = true;
    }
  }

  function defaultColor(type) {
    try {
      const two = seriesOf([
        { t: 0, o: 1, h: 2, l: 0.5, c: 1.5, v: 1 },
        { t: 60, o: 1.5, h: 2.5, l: 1, c: 2, v: 1 },
      ]);
      const p = IND[type].calc(two, IND[type].params).plots.find((x) => !x.colors);
      return p ? p.color : BLUE;
    } catch (e) {
      return BLUE;
    }
  }

  function computeAll(r) {
    const limit = state.active ? barStart(state.cutoff, r) : Infinity;
    const key = [barsStamp, limit, JSON.stringify(state.inds)].join('|');
    if (calcCache.key === key) return calcCache;
    const use = bars.filter((b) => b.t < limit);
    const d = seriesOf(use);
    const list = [];
    for (const ind of state.inds) {
      if (!ind.visible) continue;
      const def = IND[ind.type];
      const out = def.calc(d, ind.params);
      if (ind.color) {
        const p = out.plots.find((x) => !x.colors);
        if (p) p.color = ind.color;
      }
      list.push({ ind, def, out });
    }
    calcCache = { key, list, useLen: use.length, timeIndex: new Map(use.map((b, i) => [b.t, i])) };
    return calcCache;
  }

  function barSpacing(xmap) {
    const w = [...xmap.values()].map((e) => e[1] - e[0] + 1).sort((a, b) => a - b);
    return w.length ? w[Math.floor(w.length / 2)] : 6;
  }

  // Bar index -> x center. Bars cut by the pane edge are centered by the bar spacing,
  // and indexes past the last bar (Ichimoku's leading spans) are extrapolated.
  function makeXAt(xmap, spacing, width) {
    const center = (e) => (e[0] === 0 ? e[1] + 1 - spacing / 2 : e[1] >= width ? e[0] + spacing / 2 : (e[0] + e[1]) / 2);
    const n = bars.length;
    const lastE = n ? xmap.get(bars[n - 1].t) : null;
    const lastX = lastE ? center(lastE) : null;
    return (i) => {
      if (i < 0) return undefined;
      if (i < n) {
        const e = xmap.get(bars[i].t);
        return e ? center(e) : undefined;
      }
      return lastX == null ? undefined : lastX + (i - n + 1) * spacing;
    };
  }

  function axisRect(container, main) {
    let best = null;
    for (const el of container.querySelectorAll('canvas:not([data-tvch])')) {
      const r = el.getBoundingClientRect();
      if (Math.abs(r.left - main.right) < 3 && Math.abs(r.top - main.top) < 3 && r.width > 0 && r.width < 200) {
        if (!best || r.width > best.width) best = r;
      }
    }
    return best;
  }

  // Bottom of TradingView's own legend, so ours goes right below it.
  function tvLegendBottom(container, main) {
    let best = main.top + 6;
    for (const el of container.querySelectorAll('[data-name="legend"], [class*="legend"]')) {
      const r = el.getBoundingClientRect();
      if (!r.height || r.height > main.height * 0.5) continue;
      if (r.top < main.top - 2 || r.top > main.top + main.height * 0.4 || r.left > main.left + main.width / 2) continue;
      best = Math.max(best, r.bottom);
    }
    return best;
  }

  function hexToRgb(color) {
    const m = color.match(/^#([0-9a-f]{6})$/i);
    if (m) return [0, 2, 4].map((k) => parseInt(m[1].substr(k, 2), 16));
    const n = color.match(/\d+(\.\d+)?/g);
    return n ? n.slice(0, 3).map(Number) : [0, 0, 0];
  }

  function isLight(color) {
    const [r, g, b] = hexToRgb(color);
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
  }

  function themeColors() {
    const bg = curtainColor();
    return isLight(bg)
      ? { bg, text: '#131722', sep: '#E0E3EB', band: GREY }
      : { bg, text: '#B2B5BE', sep: '#2A2E39', band: GREY };
  }

  function solid(color) {
    return color.replace(/rgba\((\d+),\s*(\d+),\s*(\d+),\s*[\d.]+\)/, 'rgb($1,$2,$3)');
  }

  function compact(v) {
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(3) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(3) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(3) + 'K';
    return v.toFixed(2);
  }

  function fmtVal(v, plot, group) {
    if (v == null || !isFinite(v)) return '∅';
    if (plot.compact) return compact(v);
    return v.toFixed(group === 'main' ? priceDecimals : 2);
  }

  function lastIndex(vals, last) {
    for (let i = Math.min(last, vals.length - 1); i >= 0; i--) if (vals[i] != null) return i;
    return -1;
  }

  function drawFill(ctx, f, xAt, yOf, last) {
    const off = f.offset || 0;
    for (let i = 1; i <= last; i++) {
      const a0 = f.a[i - 1];
      const a1 = f.a[i];
      const b0 = f.b[i - 1];
      const b1 = f.b[i];
      if (a0 == null || a1 == null || b0 == null || b1 == null) continue;
      const x0 = xAt(i - 1 + off);
      const x1 = xAt(i + off);
      if (x0 === undefined || x1 === undefined) continue;
      ctx.fillStyle = f.colors ? f.colors[i] : f.color;
      ctx.beginPath();
      ctx.moveTo(x0, yOf(a0));
      ctx.lineTo(x1, yOf(a1));
      ctx.lineTo(x1, yOf(b1));
      ctx.lineTo(x0, yOf(b0));
      ctx.closePath();
      ctx.fill();
    }
  }

  function drawPlot(ctx, p, xAt, yOf, last, spacing, baseY) {
    const off = p.offset || 0;
    if (p.style === 'line') {
      ctx.strokeStyle = p.color;
      ctx.lineWidth = p.width || 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i <= last; i++) {
        const v = p.vals[i];
        const x = xAt(i + off);
        if (v == null || x === undefined) {
          pen = false;
          continue;
        }
        const y = yOf(v);
        if (pen) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
        pen = true;
      }
      ctx.stroke();
    } else if (p.style === 'cross') {
      const s = Math.max(2, Math.min(4, spacing * 0.35));
      ctx.strokeStyle = p.color;
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      for (let i = 0; i <= last; i++) {
        const v = p.vals[i];
        const x = xAt(i + off);
        if (v == null || x === undefined) continue;
        const y = yOf(v);
        ctx.moveTo(x - s, y);
        ctx.lineTo(x + s, y);
        ctx.moveTo(x, y - s);
        ctx.lineTo(x, y + s);
      }
      ctx.stroke();
    } else {
      // 'hist' grows from zero, 'columns' from the bottom of the area.
      const w = Math.max(1, Math.round(spacing * 0.6));
      for (let i = 0; i <= last; i++) {
        const v = p.vals[i];
        const x = xAt(i + off);
        if (v == null || x === undefined) continue;
        const y = yOf(v);
        ctx.fillStyle = p.colors ? p.colors[i] : p.color;
        ctx.fillRect(Math.round(x - w / 2), Math.min(y, baseY), w, Math.max(1, Math.abs(baseY - y)));
      }
    }
  }

  function drawTag(ctx, y, text, color, w) {
    const h = 18;
    ctx.fillStyle = color;
    ctx.fillRect(0, Math.round(y - h / 2), w, h);
    ctx.fillStyle = isLight(color) ? '#131722' : '#FFFFFF';
    ctx.font = `12px ${FONT}`;
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 6, y);
  }

  // Min/max of the plots over the bars currently on screen (never hidden ones).
  function visibleRange(item, xAt, last, width) {
    let lo = Infinity;
    let hi = -Infinity;
    for (const p of item.out.plots) {
      const off = p.offset || 0;
      for (let i = 0; i <= last; i++) {
        const v = p.vals[i];
        const x = xAt(i + off);
        if (v == null || x === undefined || x < 0 || x > width) continue;
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
      if (p.style === 'hist') {
        lo = Math.min(lo, 0);
        hi = Math.max(hi, 0);
      }
    }
    for (const b of item.out.bands || []) {
      lo = Math.min(lo, b.v);
      hi = Math.max(hi, b.v);
    }
    if (!isFinite(lo)) return [0, 1];
    if (hi === lo) return [lo - 1, hi + 1];
    return [lo, hi];
  }

  function legendEl(container, k) {
    let el = legendEls[k];
    if (!el || el.parentNode !== container) {
      if (el) el.remove();
      el = document.createElement('div');
      el.className = 'tvch-lg';
      el.style.cssText = `position:absolute;z-index:46;pointer-events:none;font:13px/20px ${FONT};white-space:nowrap;`;
      container.appendChild(el);
      legendEls[k] = el;
    }
    return el;
  }

  function legendLine(item, idx, theme) {
    const params = item.ind.params.length ? ' ' + item.ind.params.join(' ') : '';
    const vals = item.out.plots
      .map((p) => {
        const j = idx - (p.offset || 0);
        const col = solid(p.colors ? p.colors[j] || theme.text : p.color);
        return `<span style="color:${col}">${fmtVal(p.vals[j], p, item.def.group)}</span>`;
      })
      .join(' ');
    return `<div><span style="color:${theme.text}">${item.def.name}${params}</span>&nbsp; ${vals}</div>`;
  }

  function setLegend(el, left, top, html) {
    el.style.left = left + 'px';
    el.style.top = top + 'px';
    if (el._html !== html) {
      el.innerHTML = html;
      el._html = html;
    }
  }

  function clearIndicatorDom() {
    for (const el of [mainCanvas, axisCanvas, ...subCanvases, ...legendEls]) if (el) el.remove();
    mainCanvas = null;
    axisCanvas = null;
    subCanvases = [];
    legendEls = [];
  }

  function trackMouse(container) {
    if (trackedContainer === container) return;
    trackedContainer = container;
    container.addEventListener('mousemove', (e) => (mouseX = e.clientX), true);
    container.addEventListener('mouseleave', () => (mouseX = null), true);
  }

  // The legend shows the bar under the mouse, like TradingView, but never a hidden bar.
  function hoverIndex(ts, main, cache) {
    const last = cache.useLen - 1;
    if (mouseX == null) return last;
    const idx = cache.timeIndex.get(ts.coordinateToTime(mouseX - main.left));
    return idx == null ? last : Math.min(idx, last);
  }

  function indicatorFrame() {
    requestAnimationFrame(indicatorFrame);
    if (!(state.inds || []).some((x) => x.visible)) {
      clearIndicatorDom();
      setIndStatus('');
      return;
    }
    try {
      indError = '';
      const c = chart();
      const container = chartContainer();
      const main = container && mainPaneRect(container);
      if (!c || !main) throw new Error('نمودار پیدا نشد.');
      const r = currentResolution();
      refreshBars(c, r);
      if (!bars.length) throw new Error(indError || 'در حال گرفتن داده کندل‌ها…');
      const ts = typeof c.getTimeScale === 'function' ? c.getTimeScale() : null;
      if (!ts || typeof ts.coordinateToTime !== 'function') throw new Error('محور زمان در دسترس نیست.');
      trackMouse(container);

      const cache = computeAll(r);
      const last = cache.useLen - 1;
      const theme = themeColors();
      const box = container.getBoundingClientRect();
      const left = main.left - box.left;
      const top = main.top - box.top;
      const W = main.width;
      const axis = axisRect(container, main);
      const axisW = axis ? axis.width : 0;
      const xmap = buildXMap(ts, W);
      const spacing = barSpacing(xmap);
      const xAt = makeXAt(xmap, spacing, W);
      const hover = hoverIndex(ts, main, cache);

      const mains = cache.list.filter((x) => x.def.group === 'main');
      const subs = cache.list.filter((x) => x.def.group === 'sub');
      let subH = Math.min(SUB_MAX, Math.max(SUB_MIN, main.height * 0.2));
      if (subs.length * subH > main.height * 0.6) subH = (main.height * 0.6) / subs.length;
      const mainH = main.height - subs.length * subH;

      // Main pane: overlays on TradingView's own price scale.
      const yMain = makeYMap(c, main.height);
      mainCanvas = sizedCanvas(mainCanvas, container, 40, left, top, W, main.height);
      const ctx = mainCanvas.getContext('2d');
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, W, mainH);
      ctx.clip();
      for (const item of mains) {
        if (item.out.volume) {
          let vmax = 0;
          const vals = item.out.plots[0].vals;
          for (let i = 0; i <= last; i++) {
            const x = xAt(i);
            if (x !== undefined && x >= 0 && x <= W) vmax = Math.max(vmax, vals[i]);
          }
          const volH = mainH * 0.2;
          drawPlot(ctx, item.out.plots[0], xAt, (v) => mainH - (vmax ? (v / vmax) * volH : 0), last, spacing, mainH);
          continue;
        }
        for (const f of item.out.fills || []) drawFill(ctx, f, xAt, yMain, last);
        for (const p of item.out.plots) drawPlot(ctx, p, xAt, yMain, last, spacing, yMain(0));
      }
      ctx.restore();

      if (axis) {
        axisCanvas = sizedCanvas(axisCanvas, container, 42, axis.left - box.left, top, axisW, mainH);
        const actx = axisCanvas.getContext('2d');
        for (const item of mains) {
          if (item.out.volume) continue;
          for (const p of item.out.plots) {
            const i = lastIndex(p.vals, last);
            if (i >= 0) drawTag(actx, yMain(p.vals[i]), fmtVal(p.vals[i], p, 'main'), p.color, axisW);
          }
        }
      } else if (axisCanvas) {
        axisCanvas.remove();
        axisCanvas = null;
      }

      let k = 0;
      if (mains.length) {
        const html = mains.map((item) => legendLine(item, hover, theme)).join('');
        setLegend(legendEl(container, k++), left + 8, tvLegendBottom(container, main) - box.top + 2, html);
      }

      // Oscillators: one pane each, stacked at the bottom of the chart.
      subs.forEach((item, n) => {
        const paneTop = top + mainH + n * subH;
        const cv = (subCanvases[n] = sizedCanvas(subCanvases[n], container, 44, left, paneTop, W + axisW, subH));
        const sc = cv.getContext('2d');
        sc.fillStyle = theme.bg;
        sc.fillRect(0, 0, W + axisW, subH);
        sc.fillStyle = theme.sep;
        sc.fillRect(0, 0, W + axisW, 1);
        const [lo, hi] = visibleRange(item, xAt, last, W);
        const pad = (hi - lo) * 0.08;
        const yS = (v) => 4 + ((hi + pad - v) / (hi - lo + 2 * pad)) * (subH - 8);

        sc.save();
        sc.beginPath();
        sc.rect(0, 1, W, subH - 1);
        sc.clip();
        if (item.out.bandFill) {
          const [a, b, col] = item.out.bandFill;
          sc.fillStyle = col;
          sc.fillRect(0, yS(a), W, yS(b) - yS(a));
        }
        for (const b of item.out.bands || []) {
          sc.strokeStyle = theme.band;
          sc.globalAlpha = b.alpha;
          sc.setLineDash(b.dash);
          sc.lineWidth = 1;
          sc.beginPath();
          sc.moveTo(0, Math.round(yS(b.v)) + 0.5);
          sc.lineTo(W, Math.round(yS(b.v)) + 0.5);
          sc.stroke();
          sc.globalAlpha = 1;
        }
        for (const p of item.out.plots) drawPlot(sc, p, xAt, yS, last, spacing, yS(0));
        sc.restore();

        if (axisW) {
          sc.save();
          sc.translate(W, 0);
          sc.fillStyle = theme.text;
          sc.font = `12px ${FONT}`;
          sc.textBaseline = 'middle';
          for (const b of item.out.bands || []) if (b.alpha === 1) sc.fillText(String(b.v), 6, yS(b.v));
          for (const p of item.out.plots) {
            const i = lastIndex(p.vals, last);
            if (i < 0) continue;
            const col = p.colors ? solid(p.colors[i]) : p.color;
            drawTag(sc, Math.min(subH - 9, Math.max(9, yS(p.vals[i]))), fmtVal(p.vals[i], p, 'sub'), col, axisW);
          }
          sc.restore();
        }
        setLegend(legendEl(container, k++), left + 8, paneTop + 4, legendLine(item, hover, theme));
      });
      for (const extra of subCanvases.splice(subs.length)) if (extra) extra.remove();
      for (const extra of legendEls.splice(k)) if (extra) extra.remove();

      setIndStatus('داده: ' + dataSource);
    } catch (e) {
      clearIndicatorDom();
      setIndStatus('', e.message || String(e));
    }
  }

  function setIndStatus(src, err) {
    if (ui.isrc) ui.isrc.textContent = src || '';
    if (ui.ierr) ui.ierr.textContent = err || '';
  }

  function indOptions() {
    const opts = (g) =>
      Object.keys(IND)
        .filter((k) => IND[k].group === g)
        .map((k) => `<option value="${k}">${IND_TITLES[k]}</option>`)
        .join('');
    return `<optgroup label="روی چارت">${opts('main')}</optgroup><optgroup label="پنجره جدا">${opts('sub')}</optgroup>`;
  }

  function addIndicator(type, text) {
    const def = IND[type];
    const nums = String(text || '')
      .split(/[\s,،]+/)
      .filter(Boolean)
      .map(Number);
    const params = def.params.map((dv, i) => {
      const v = nums[i];
      if (!(v > 0)) return dv;
      return Number.isInteger(dv) ? Math.max(1, Math.round(v)) : v;
    });
    state.inds = state.inds || [];
    state.inds.push({ id: Date.now(), type, params, visible: true });
    saveState();
    renderInds();
  }

  function renderInds() {
    if (!ui.ilist) return;
    ui.ilist.textContent = '';
    for (const ind of state.inds || []) {
      const row = document.createElement('div');
      row.className = 'row';
      const eye = document.createElement('input');
      eye.type = 'checkbox';
      eye.checked = ind.visible;
      eye.title = 'نمایش/مخفی';
      eye.onchange = () => {
        ind.visible = eye.checked;
        saveState();
      };
      const color = document.createElement('input');
      color.type = 'color';
      color.value = ind.color || defaultColor(ind.type);
      color.oninput = () => {
        ind.color = color.value;
        saveState();
      };
      const name = document.createElement('span');
      name.style.flex = '1';
      name.title = IND_TITLES[ind.type];
      name.textContent = IND[ind.type].name + (ind.params.length ? ' ' + ind.params.join(' ') : '');
      const del = document.createElement('button');
      del.className = 'sec';
      del.style.flex = '0 0 auto';
      del.textContent = '×';
      del.onclick = () => {
        state.inds = state.inds.filter((x) => x !== ind);
        saveState();
        renderInds();
      };
      row.append(eye, color, name, del);
      ui.ilist.appendChild(row);
    }
  }

  // ----- UI -----

  let ui = {};

  function fmt(t) {
    const d = new Date(t * 1000);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function toLocalInput(t) {
    return fmt(t).replace(' ', 'T');
  }

  function setStatus(msg) {
    if (ui.status) ui.status.textContent = msg;
  }

  function refreshInfo() {
    if (!ui.info) return;
    ui.info.textContent = state.active
      ? `مخفی از: ${fmt(state.cutoff)} (به وقت سیستم شما)`
      : 'غیرفعال';
  }

  function buildPanel() {
    const panel = document.createElement('div');
    panel.id = 'tvch-panel';
    panel.dir = 'rtl';
    panel.innerHTML = `
      <style>
        #tvch-panel { position: fixed; top: 60px; left: 60px; z-index: 99999; width: 250px;
          background: #1e222d; color: #d1d4dc; border: 1px solid #434651; border-radius: 8px;
          font: 13px/1.5 Tahoma, sans-serif; box-shadow: 0 4px 16px rgba(0,0,0,.4); }
        #tvch-panel .hd { padding: 6px 10px; cursor: move; background: #2a2e39; border-radius: 8px 8px 0 0;
          display: flex; justify-content: space-between; align-items: center; user-select: none; }
        #tvch-panel .bd { padding: 10px; display: grid; gap: 8px; }
        #tvch-panel.min .bd { display: none; }
        #tvch-panel input[type=datetime-local] { width: 100%; box-sizing: border-box; background: #131722;
          color: inherit; border: 1px solid #434651; border-radius: 4px; padding: 4px; }
        #tvch-panel button { background: #2962ff; color: #fff; border: 0; border-radius: 4px;
          padding: 6px; cursor: pointer; font: inherit; }
        #tvch-panel button.sec { background: #363a45; }
        #tvch-panel input[type=text], #tvch-panel select { background: #131722; color: inherit; border: 1px solid #434651; border-radius: 4px; padding: 4px; flex: 1; }
        #tvch-panel .row { display: flex; gap: 6px; align-items: center; }
        #tvch-panel .row > button { flex: 1; }
        #tvch-panel .big { font-size: 15px; padding: 10px; }
        #tvch-panel .muted { color: #787b86; font-size: 12px; }
        #tvch-panel .err { color: #f23645; font-size: 12px; min-height: 0; }
      </style>
      <div class="hd"><b>مخفی‌کردن کندل‌ها</b><button class="sec tvch-min" title="کوچک کردن">–</button></div>
      <div class="bd">
        <label>از این تاریخ به بعد مخفی شود:</label>
        <input type="datetime-local" class="tvch-date">
        <div class="row">
          <button class="tvch-hide">مخفی کن</button>
          <button class="sec tvch-show">نمایش همه</button>
        </div>
        <button class="big tvch-next">کندل بعدی ◀</button>
        <button class="sec tvch-prev">▶ یک کندل عقب</button>
        <label class="row"><input type="checkbox" class="tvch-safe"> لبه امن (هیچ بخشی از کندل آینده دیده نشود)</label>
        <label class="row">رنگ پرده: <input type="color" class="tvch-color"></label>
        <hr style="width:100%;border:0;border-top:1px solid #434651;margin:2px 0">
        <b>اندیکاتورها</b>
        <div class="row">
          <select class="tvch-itype">${indOptions()}</select>
          <input type="text" class="tvch-iperiod" placeholder="پارامترها" style="width:70px">
          <button class="tvch-iadd" style="flex:0 0 auto">+</button>
        </div>
        <div class="tvch-ilist"></div>
        <div class="muted tvch-isrc"></div>
        <div class="err tvch-ierr"></div>
        <div class="muted tvch-info"></div>
        <div class="err tvch-status"></div>
        <div class="muted">میانبر: Shift+→ کندل بعدی، Shift+← عقب</div>
      </div>`;
    document.body.appendChild(panel);

    const q = (s) => panel.querySelector(s);
    ui = { panel, info: q('.tvch-info'), status: q('.tvch-status'), ilist: q('.tvch-ilist'), isrc: q('.tvch-isrc'), ierr: q('.tvch-ierr') };
    normInds();
    const itype = q('.tvch-itype');
    const iperiod = q('.tvch-iperiod');
    itype.onchange = () => {
      const params = IND[itype.value].params;
      iperiod.value = params.join(', ');
      iperiod.disabled = !params.length;
    };
    itype.onchange();
    q('.tvch-iadd').onclick = () => addIndicator(itype.value, iperiod.value);
    renderInds();

    const date = q('.tvch-date');
    if (state.cutoff) date.value = toLocalInput(state.cutoff);
    const safe = q('.tvch-safe');
    safe.checked = state.safeEdge !== false;
    const color = q('.tvch-color');
    color.value = curtainColor();

    q('.tvch-hide').onclick = () => hideFrom(date.value);
    q('.tvch-show').onclick = showAll;
    q('.tvch-next').onclick = () => step(1);
    q('.tvch-prev').onclick = () => step(-1);
    q('.tvch-min').onclick = () => panel.classList.toggle('min');
    safe.onchange = () => {
      state.safeEdge = safe.checked;
      saveState();
      if (state.active) run(drawCurtain);
    };
    color.oninput = () => {
      state.color = color.value;
      saveState();
      if (state.active) run(drawCurtain);
    };

    makeDraggable(panel, q('.hd'));
    refreshInfo();
  }

  function makeDraggable(panel, handle) {
    handle.addEventListener('mousedown', (e) => {
      if (e.target.tagName === 'BUTTON') return;
      const sx = e.clientX - panel.offsetLeft;
      const sy = e.clientY - panel.offsetTop;
      const move = (ev) => {
        panel.style.left = ev.clientX - sx + 'px';
        panel.style.top = ev.clientY - sy + 'px';
      };
      const up = () => {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }

  function bindKeys() {
    document.addEventListener(
      'keydown',
      (e) => {
        if (!e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;
        if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return;
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        e.preventDefault();
        e.stopPropagation();
        step(e.key === 'ArrowRight' ? 1 : -1);
      },
      true
    );
  }

  function init() {
    buildPanel();
    bindKeys();
    requestAnimationFrame(indicatorFrame);
    let tries = 0;
    const wait = setInterval(() => {
      const c = chart();
      if (c) {
        clearInterval(wait);
        if (state.active) run(drawCurtain);
      } else if (++tries > 120) {
        clearInterval(wait);
        setStatus('API تریدینگ‌ویو در دسترس نیست؛ صفحه را رفرش کنید.');
      }
    }, 500);
  }

  init();
})();
