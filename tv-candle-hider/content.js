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
        <div class="muted tvch-info"></div>
        <div class="err tvch-status"></div>
        <div class="muted">میانبر: Shift+→ کندل بعدی، Shift+← عقب</div>
      </div>`;
    document.body.appendChild(panel);

    const q = (s) => panel.querySelector(s);
    ui = { panel, info: q('.tvch-info'), status: q('.tvch-status') };

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
    let tries = 0;
    const wait = setInterval(() => {
      const c = chart();
      if (c && typeof c.createMultipointShape === 'function') {
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
