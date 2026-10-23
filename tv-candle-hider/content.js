// TV Candle Hider
// The TradingView chart is drawn on a <canvas>, so candles are not HTML elements
// and cannot be removed by editing the DOM. Instead this script uses the page's
// own chart API (window.TradingViewApi) to draw a locked, opaque rectangle that
// starts at the cutoff time and extends to the right. Because the rectangle is
// anchored to time, it follows zoom/scroll and works on every timeframe.
(() => {
  'use strict';

  const STORE_KEY = 'tvch-state';
  const FAR_HIGH = 1e12;
  const FAR_LOW = 1e-12;
  const WEEK_OFFSET = 4 * 86400; // 1970-01-05 was a Monday; TradingView weeks start Monday

  // State: cutoff = first hidden moment (unix seconds, UTC).
  let state = loadState();
  let shapeId = null;
  let shapeChart = null;

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

  // ----- curtain shape -----

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

  async function removeCurtain() {
    if (shapeId != null && shapeChart) {
      try {
        shapeChart.removeEntity(shapeId);
      } catch (e) {}
    }
    shapeId = null;
    shapeChart = null;
  }

  // Draw (or redraw) the curtain for the stored cutoff on the active chart.
  async function drawCurtain() {
    const c = chart();
    if (!c) throw new Error('API تریدینگ‌ویو پیدا نشد. صفحه را رفرش کنید.');
    const r = currentResolution();
    const firstHidden = barStart(state.cutoff, r);
    // Drawing anchors snap to bar centers. In safe mode the curtain starts at the
    // last visible bar so no part of a future candle leaks (half of the last
    // visible candle is covered instead).
    const left = state.safeEdge === false ? firstHidden : addBars(firstHidden, r, -1);
    const points = [
      { time: left, price: FAR_HIGH },
      { time: addBars(left, r, 1), price: FAR_LOW },
    ];
    const color = curtainColor();

    if (shapeId != null && shapeChart === c) {
      try {
        const shape = c.getShapeById(shapeId);
        shape.setPoints(points);
        shape.setProperties({ backgroundColor: color, color: color });
        return;
      } catch (e) {
        // Shape is gone (symbol/layout changed); fall through and recreate it.
      }
    }

    await removeCurtain();
    shapeId = await c.createMultipointShape(points, {
      shape: 'rectangle',
      lock: true,
      disableSelection: true,
      disableSave: true,
      disableUndo: true,
      showInObjectsTree: false,
      zOrder: 'top',
      overrides: {
        backgroundColor: color,
        color: color,
        fillBackground: true,
        transparency: 0,
        linewidth: 1,
        extendRight: true,
        extendLeft: false,
      },
    });
    shapeChart = c;
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

  // Redraw after timeframe or symbol changes so the curtain snaps to the new bars.
  function subscribeChartEvents() {
    const c = chart();
    const redraw = () => setTimeout(() => state.active && run(drawCurtain), 300);
    try {
      c.onIntervalChanged().subscribe(null, redraw);
    } catch (e) {}
    try {
      c.onSymbolChanged().subscribe(null, () => {
        shapeId = null; // drawings belong to the old symbol
        redraw();
      });
    } catch (e) {}
  }

  function init() {
    buildPanel();
    bindKeys();
    let tries = 0;
    const wait = setInterval(() => {
      const c = chart();
      if (c && typeof c.createMultipointShape === 'function') {
        clearInterval(wait);
        subscribeChartEvents();
        if (state.active) run(drawCurtain);
      } else if (++tries > 120) {
        clearInterval(wait);
        setStatus('API تریدینگ‌ویو در دسترس نیست؛ صفحه را رفرش کنید.');
      }
    }, 500);
  }

  init();
})();
