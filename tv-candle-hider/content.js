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
    const rects = [...container.querySelectorAll('canvas')]
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
  // hidden candle can never leak into a line.
  const PALETTE = ['#f7a600', '#2962ff', '#e91e63', '#00bcd4', '#9c27b0', '#8bc34a'];
  const IND_NAMES = { sma: 'SMA', ema: 'EMA', bb: 'Bollinger', vwap: 'VWAP', rsi: 'RSI' };
  const IND_DEFAULT = { sma: 20, ema: 50, bb: 20, vwap: 0, rsi: 14 };
  const RSI_HEIGHT = 90;

  let bars = [];
  let barsKey = '';
  let barsStamp = 0;
  let fetchedFrom = 0;
  let fetchedTo = 0;
  let fetchedAt = 0;
  let barsBusy = false;
  let dataSource = '';
  let indCanvas = null;
  let rsiCanvas = null;
  let indError = '';
  let calcCache = { key: '', lines: [] };

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
      bars = got;
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

  function calcSma(a, p) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < a.length; i++) {
      sum += a[i];
      if (i >= p) sum -= a[i - p];
      out.push(i >= p - 1 ? sum / p : null);
    }
    return out;
  }

  function calcEma(a, p) {
    const k = 2 / (p + 1);
    const out = [];
    let prev = null;
    for (let i = 0; i < a.length; i++) {
      if (i < p - 1) out.push(null);
      else {
        if (i === p - 1) prev = a.slice(0, p).reduce((x, y) => x + y, 0) / p;
        else prev = a[i] * k + prev * (1 - k);
        out.push(prev);
      }
    }
    return out;
  }

  function calcBb(a, p, mult) {
    const mid = calcSma(a, p);
    const up = [];
    const lo = [];
    for (let i = 0; i < a.length; i++) {
      if (mid[i] == null) {
        up.push(null);
        lo.push(null);
        continue;
      }
      let v = 0;
      for (let j = i - p + 1; j <= i; j++) v += (a[j] - mid[i]) * (a[j] - mid[i]);
      const sd = Math.sqrt(v / p);
      up.push(mid[i] + mult * sd);
      lo.push(mid[i] - mult * sd);
    }
    return [mid, up, lo];
  }

  function calcRsi(a, p) {
    const out = [null];
    let gain = 0;
    let loss = 0;
    for (let i = 1; i < a.length; i++) {
      const d = a[i] - a[i - 1];
      const g = Math.max(d, 0);
      const l = Math.max(-d, 0);
      if (i <= p) {
        gain += g / p;
        loss += l / p;
      } else {
        gain = (gain * (p - 1) + g) / p;
        loss = (loss * (p - 1) + l) / p;
      }
      out.push(i < p ? null : loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
    }
    return out;
  }

  // Resets every UTC day, like the usual session VWAP.
  function calcVwap(list) {
    let day = null;
    let pv = 0;
    let vol = 0;
    return list.map((b) => {
      const d = Math.floor(b.t / 86400);
      if (d !== day) {
        day = d;
        pv = 0;
        vol = 0;
      }
      const w = b.v || 1;
      pv += ((b.h + b.l + b.c) / 3) * w;
      vol += w;
      return pv / vol;
    });
  }

  // Lines for every configured indicator, using only candles before the curtain.
  function computeLines(r) {
    const limit = state.active ? barStart(state.cutoff, r) : Infinity;
    const key = [barsStamp, limit, JSON.stringify(state.inds || [])].join('|');
    if (calcCache.key === key) return calcCache.lines;
    const use = bars.filter((b) => b.t < limit);
    const close = use.map((b) => b.c);
    const lines = [];
    for (const ind of state.inds || []) {
      const add = (vals, dashed) => lines.push({ ind, vals, dashed: !!dashed, pane: ind.type === 'rsi' ? 'rsi' : 'main' });
      if (ind.type === 'sma') add(calcSma(close, ind.p));
      else if (ind.type === 'ema') add(calcEma(close, ind.p));
      else if (ind.type === 'vwap') add(calcVwap(use));
      else if (ind.type === 'rsi') add(calcRsi(close, ind.p));
      else if (ind.type === 'bb') {
        const [mid, up, lo] = calcBb(close, ind.p, ind.m || 2);
        add(mid, true);
        add(up);
        add(lo);
      }
    }
    calcCache = { key, lines: lines.map((l) => ({ ...l, times: use.map((b) => b.t) })) };
    return calcCache.lines;
  }

  // Time -> x: sample coordinateToTime across the pane and take each bar's middle.
  function buildXMap(ts, width) {
    const m = new Map();
    for (let x = 0; x <= width; x += 2) {
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
    const rects = [...container.querySelectorAll('canvas')]
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

  function strokeLine(ctx, line, xmap, yOf) {
    ctx.strokeStyle = line.ind.color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash(line.dashed ? [5, 3] : []);
    ctx.beginPath();
    let pen = false;
    for (let i = 0; i < line.times.length; i++) {
      const e = xmap.get(line.times[i]);
      const v = line.vals[i];
      if (!e || v == null) {
        pen = false;
        continue;
      }
      const x = (e[0] + e[1]) / 2;
      const y = yOf(v);
      if (pen) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
      pen = true;
    }
    ctx.stroke();
  }

  function removeIndCanvases() {
    if (indCanvas) indCanvas.remove();
    if (rsiCanvas) rsiCanvas.remove();
    indCanvas = null;
    rsiCanvas = null;
  }

  async function indicatorFrame() {
    requestAnimationFrame(indicatorFrame);
    const inds = state.inds || [];
    if (!inds.length) {
      removeIndCanvases();
      setIndStatus('');
      return;
    }
    try {
      indError = '';
      const c = chart();
      const container = chartContainer();
      const rect = container && mainPaneRect(container);
      if (!c || !rect) throw new Error('نمودار پیدا نشد.');
      const r = currentResolution();
      refreshBars(c, r);
      if (!bars.length) throw new Error(indError || 'در حال گرفتن داده کندل‌ها…');
      const ts = typeof c.getTimeScale === 'function' ? c.getTimeScale() : null;
      if (!ts || typeof ts.coordinateToTime !== 'function') throw new Error('محور زمان در دسترس نیست.');

      const box = container.getBoundingClientRect();
      const left = rect.left - box.left;
      const top = rect.top - box.top;
      const xmap = buildXMap(ts, rect.width);
      const lines = computeLines(r);

      const yMain = makeYMap(c, rect.height);
      indCanvas = sizedCanvas(indCanvas, container, 40, left, top, rect.width, rect.height);
      const ctx = indCanvas.getContext('2d');
      ctx.save();
      for (const l of lines) if (l.pane === 'main') strokeLine(ctx, l, xmap, yMain);
      ctx.restore();

      const rsiLines = lines.filter((l) => l.pane === 'rsi');
      if (rsiLines.length) {
        rsiCanvas = sizedCanvas(rsiCanvas, container, 41, left, top + rect.height - RSI_HEIGHT, rect.width, RSI_HEIGHT);
        const rc = rsiCanvas.getContext('2d');
        rc.fillStyle = curtainColor();
        rc.globalAlpha = 0.9;
        rc.fillRect(0, 0, rect.width, RSI_HEIGHT);
        rc.globalAlpha = 1;
        const yRsi = (v) => 6 + ((100 - v) / 100) * (RSI_HEIGHT - 12);
        rc.strokeStyle = '#787b86';
        rc.lineWidth = 1;
        rc.setLineDash([2, 4]);
        for (const lv of [30, 70]) {
          rc.beginPath();
          rc.moveTo(0, yRsi(lv));
          rc.lineTo(rect.width, yRsi(lv));
          rc.stroke();
        }
        for (const l of rsiLines) strokeLine(rc, l, xmap, yRsi);
      } else if (rsiCanvas) {
        rsiCanvas.remove();
        rsiCanvas = null;
      }
      setIndStatus('داده: ' + dataSource);
    } catch (e) {
      if (indCanvas) indCanvas.getContext('2d').clearRect(0, 0, indCanvas.width, indCanvas.height);
      setIndStatus('', e.message || String(e));
    }
  }

  function setIndStatus(src, err) {
    if (ui.isrc) ui.isrc.textContent = src || '';
    if (ui.ierr) ui.ierr.textContent = err || '';
  }

  function addIndicator(type, period) {
    const list = (state.inds = state.inds || []);
    const p = Math.max(1, parseInt(period, 10) || IND_DEFAULT[type] || 14);
    list.push({ id: Date.now(), type, p, m: 2, color: PALETTE[list.length % PALETTE.length] });
    saveState();
    renderInds();
  }

  function renderInds() {
    if (!ui.ilist) return;
    ui.ilist.textContent = '';
    for (const ind of state.inds || []) {
      const row = document.createElement('div');
      row.className = 'row';
      const color = document.createElement('input');
      color.type = 'color';
      color.value = ind.color;
      color.oninput = () => {
        ind.color = color.value;
        saveState();
      };
      const name = document.createElement('span');
      name.style.flex = '1';
      name.textContent = IND_NAMES[ind.type] + (ind.type === 'vwap' ? '' : ' ' + ind.p);
      const del = document.createElement('button');
      del.className = 'sec';
      del.style.flex = '0 0 auto';
      del.textContent = '×';
      del.onclick = () => {
        state.inds = state.inds.filter((x) => x !== ind);
        saveState();
        renderInds();
      };
      row.append(color, name, del);
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
        #tvch-panel select { background: #131722; color: inherit; border: 1px solid #434651; border-radius: 4px; padding: 4px; flex: 1; }
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
          <select class="tvch-itype"><option value="sma">SMA</option><option value="ema">EMA</option><option value="bb">Bollinger</option><option value="vwap">VWAP</option><option value="rsi">RSI</option></select>
          <input type="number" class="tvch-iperiod" min="1" value="20" style="width:56px">
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
    const itype = q('.tvch-itype');
    const iperiod = q('.tvch-iperiod');
    itype.onchange = () => {
      iperiod.disabled = itype.value === 'vwap';
      iperiod.value = IND_DEFAULT[itype.value] || 14;
    };
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
