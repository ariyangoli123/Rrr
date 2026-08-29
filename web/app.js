/**
 * bloodsed -- interactive front end.
 *
 * The physics lives in sim.js; this file is the instrument around it: a tube
 * drawn on canvas with an etched millimetre scale, a chart-recorder trace of
 * the boundary, and controls that re-run the model on every change.
 *
 * A full run takes 100-300 ms, which is too slow to feel live while a finger is
 * on a slider, so interaction runs a coarse mesh and the full mesh follows once
 * the finger lifts. Both give the same reading -- the scheme is mesh
 * independent -- the coarse one just resolves the sediment less finely.
 */

import {
  BLOOD_PRESETS, GEOMETRIES, MM, esr, getGeometry, katzIndex, maxSettlingRate,
  radius, sedimentMm, simulate,
} from './sim.js';

const DURATION_H = 2;
/* Every tube is drawn against the same reference, so a capillary really does
   look narrower than a Westergren and a Wintrobe really is shorter. */
const REFERENCE_HEIGHT_MM = 200;
const REFERENCE_BORE_MM = 8;
const DRAFT = { nCells: 150, sampleIntervalS: 120 };
const FULL = { nCells: 400, sampleIntervalS: 30 };

/* Sequential ramp for the cell volume fraction: one hue, light to dark, the
   palest step being clear plasma. Fixed across themes -- see styles.css. */
const BLOOD_RAMP = ['#fdeceb', '#f9c9c5', '#f1a29c', '#e5726c',
                    '#d34b45', '#b3302c', '#8a1f1e', '#5c1213'];

const GEOMETRY_LABELS = {
  westergren: 'Westergren',
  wintrobe: 'Wintrobe',
  micro: 'Capillary',
  funnel: 'Funnel',
  'inverted-funnel': 'Inverted',
  hourglass: 'Hourglass',
  bulb: 'Bulb',
  stepped: 'Stepped',
};

const SAMPLE_LABELS = {
  normal: 'Normal',
  anemic: 'Anaemic',
  inflammation: 'Inflamed',
  'severe-inflammation': 'Severe',
  polycythemic: 'Polycythaemic',
  newborn: 'Newborn',
};

const state = {
  geometry: 'westergren',
  hematocrit: 0.45,
  aggregateUm: 60,
  tiltDeg: 0,
  sample: 0,
  playing: false,
  result: null,
};

const el = (id) => document.getElementById(id);
const tubeCanvas = el('tube-canvas');
const chartCanvas = el('chart-canvas');
const tubeCtx = tubeCanvas.getContext('2d');
const chartCtx = chartCanvas.getContext('2d');

// ---------------------------------------------------------------- colours
let ink = '#0e1518';
let ink2 = '#4a575b';
let muted = '#7f8c8e';
let line = '#d3dbd9';
let trace = '#1d5f8a';
let traceSoft = 'rgba(29,95,138,.14)';
let surface = '#f9fbfa';
let glass = '#faf7f3';
let glassRim = '#b6bcb8';

function readTheme() {
  const style = getComputedStyle(document.documentElement);
  const pick = (name, fallback) => (style.getPropertyValue(name).trim() || fallback);
  ink = pick('--ink', ink);
  ink2 = pick('--ink-2', ink2);
  muted = pick('--muted', muted);
  line = pick('--line', line);
  trace = pick('--trace', trace);
  traceSoft = pick('--trace-soft', traceSoft);
  surface = pick('--surface', surface);
  glass = pick('--glass', glass);
  glassRim = pick('--glass-rim', glassRim);
}

function rampColor(phi, maxPacking) {
  const t = Math.min(Math.max(phi / maxPacking, 0), 1) * (BLOOD_RAMP.length - 1);
  const i = Math.min(Math.floor(t), BLOOD_RAMP.length - 2);
  const f = t - i;
  const a = hexToRgb(BLOOD_RAMP[i]);
  const b = hexToRgb(BLOOD_RAMP[i + 1]);
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}

function hexToRgb(hex) {
  const value = parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

// ---------------------------------------------------------------- canvas
function fitCanvas(canvas, ctx) {
  const ratio = Math.min(window.devicePixelRatio || 1, 2.5);
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 1);
  const height = Math.max(rect.height, 1);
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

/** The tube, its etched millimetre scale, and the boundary being read. */
function drawTube() {
  const { width, height } = fitCanvas(tubeCanvas, tubeCtx);
  const ctx = tubeCtx;
  ctx.clearRect(0, 0, width, height);
  const result = state.result;
  if (!result) return;

  const geo = result.geometry;
  const lengthMm = geo.length / MM;
  const padTop = 14;
  const padBottom = 20;
  const scaleWidth = 42;
  const usable = height - padTop - padBottom;
  const referenceMm = Math.max(REFERENCE_HEIGHT_MM, lengthMm);
  const yOf = (mm) => padTop + usable * (1 - mm / referenceMm);

  const columnHalf = Math.min((width - scaleWidth) * 0.3, 58);
  // sit the column just clear of the scale, leaving the right for annotation
  const cx = Math.min(scaleWidth + columnHalf + 16, scaleWidth + (width - scaleWidth) / 2);
  const halfOf = (z) =>
    Math.max((radius(geo, z) / (0.5 * REFERENCE_BORE_MM * MM)) * columnHalf, 3.5);

  // etched scale
  ctx.font = '10px "IBM Plex Mono", ui-monospace, monospace';
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'right';
  const majorStep = 50;
  for (let mm = 0; mm <= lengthMm + 0.001; mm += majorStep / 5) {
    const major = Math.abs(mm % majorStep) < 1e-6;
    const y = yOf(mm);
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(scaleWidth - (major ? 12 : 6), y);
    ctx.lineTo(scaleWidth - 2, y);
    ctx.stroke();
    if (major) {
      ctx.fillStyle = muted;
      ctx.fillText(String(Math.round(mm)), scaleWidth - 15, y);
    }
  }

  // glass interior
  const profile = [];
  const steps = 220;
  for (let i = 0; i <= steps; i++) {
    const z = (geo.length * i) / steps;
    profile.push({ z, mm: z / MM, half: halfOf(z) });
  }
  ctx.beginPath();
  profile.forEach((p, i) => (i ? ctx.lineTo(cx - p.half, yOf(p.mm)) : ctx.moveTo(cx - p.half, yOf(p.mm))));
  for (let i = profile.length - 1; i >= 0; i--) ctx.lineTo(cx + profile[i].half, yOf(profile[i].mm));
  ctx.closePath();
  ctx.fillStyle = glass;
  ctx.fill();
  ctx.save();
  ctx.clip();

  // the suspension itself, cell by cell
  const phi = result.phi[state.sample];
  const zFaces = result.zFaces;
  const maxPacking = result.blood.maxPacking;
  for (let i = 0; i < phi.length; i++) {
    const yTop = yOf(zFaces[i + 1] / MM);
    const yBottom = yOf(zFaces[i] / MM);
    const half = Math.max(halfOf(zFaces[i]), halfOf(zFaces[i + 1])) + 1;
    ctx.fillStyle = rampColor(phi[i], maxPacking);
    ctx.fillRect(cx - half, yTop - 0.5, half * 2, yBottom - yTop + 1);
  }
  // a soft highlight so the column reads as glass
  const sheenHalf = halfOf(0) > 0 ? columnHalf : columnHalf;
  const sheen = ctx.createLinearGradient(cx - sheenHalf, 0, cx + sheenHalf, 0);
  sheen.addColorStop(0, 'rgba(255,255,255,0.30)');
  sheen.addColorStop(0.28, 'rgba(255,255,255,0.06)');
  sheen.addColorStop(0.75, 'rgba(0,0,0,0.05)');
  sheen.addColorStop(1, 'rgba(0,0,0,0.12)');
  ctx.fillStyle = sheen;
  ctx.fillRect(cx - sheenHalf, 0, sheenHalf * 2, height);
  ctx.restore();

  // rim
  ctx.strokeStyle = glassRim;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  profile.forEach((p, i) => (i ? ctx.lineTo(cx - p.half, yOf(p.mm)) : ctx.moveTo(cx - p.half, yOf(p.mm))));
  ctx.stroke();
  ctx.beginPath();
  profile.forEach((p, i) => (i ? ctx.lineTo(cx + p.half, yOf(p.mm)) : ctx.moveTo(cx + p.half, yOf(p.mm))));
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cx - profile[0].half, yOf(0));
  ctx.lineTo(cx + profile[0].half, yOf(0));
  ctx.stroke();

  // the reading: where the plasma boundary stands now
  const boundaryMm = result.interface[state.sample] / MM;
  const yBoundary = yOf(boundaryMm);
  const boundaryHalf = halfOf(result.interface[state.sample]) + 6;
  ctx.strokeStyle = ink;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(cx - boundaryHalf, yBoundary);
  ctx.lineTo(cx + boundaryHalf, yBoundary);
  ctx.stroke();

  ctx.textAlign = 'left';
  ctx.font = '600 11px "Barlow Semi Condensed", Arial, sans-serif';
  ctx.fillStyle = ink;
  const fall = result.fallMm[state.sample];
  const labelY = Math.min(Math.max(yBoundary - 8, padTop + 6), height - padBottom - 4);
  ctx.fillText(`${fall.toFixed(1)} mm fallen`, cx + boundaryHalf + 5, labelY);

  ctx.textAlign = 'center';
  ctx.fillStyle = muted;
  ctx.font = '10px "IBM Plex Sans", system-ui, sans-serif';
  ctx.fillText('height in mm · width exaggerated', cx, height - 5);
}

/** The settling curve, drawn like a chart recorder trace. */
function drawChart() {
  const { width, height } = fitCanvas(chartCanvas, chartCtx);
  const ctx = chartCtx;
  ctx.clearRect(0, 0, width, height);
  const result = state.result;
  if (!result) return;

  const padLeft = 34;
  const padRight = 10;
  const padTop = 10;
  const padBottom = 24;
  const totalMin = (result.times[result.times.length - 1]) / 60;
  let maxFall = 0;
  for (const value of result.fallMm) maxFall = Math.max(maxFall, value);
  maxFall = Math.max(maxFall, 1);
  const niceMax = niceCeiling(maxFall);

  const xOf = (min) => padLeft + ((width - padLeft - padRight) * min) / totalMin;
  const yOf = (mm) => padTop + ((height - padTop - padBottom) * mm) / niceMax;

  // grid and axes
  ctx.font = '10px "IBM Plex Mono", ui-monospace, monospace';
  ctx.strokeStyle = line;
  ctx.lineWidth = 1;
  ctx.fillStyle = muted;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  const gridStep = niceCeiling(niceMax / 4);
  for (let mm = 0; mm <= niceMax + 1e-9; mm += gridStep) {
    const y = yOf(mm);
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(width - padRight, y);
    ctx.stroke();
    ctx.fillText(String(Math.round(mm)), padLeft - 6, y);
  }

  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (const hours of [1, 2]) {
    const minutes = hours * 60;
    if (minutes > totalMin + 1e-9) continue;
    const x = xOf(minutes);
    ctx.strokeStyle = line;
    ctx.beginPath();
    ctx.moveTo(x, padTop);
    ctx.lineTo(x, height - padBottom);
    ctx.stroke();
    ctx.fillStyle = muted;
    ctx.fillText(`${hours} h`, x, height - padBottom + 5);
  }
  ctx.fillStyle = muted;
  ctx.fillText('0', padLeft, height - padBottom + 5);

  // trace with an area fill
  ctx.beginPath();
  ctx.moveTo(xOf(0), yOf(0));
  for (let i = 0; i < result.times.length; i++) ctx.lineTo(xOf(result.times[i] / 60), yOf(result.fallMm[i]));
  ctx.lineTo(xOf(result.times[result.times.length - 1] / 60), yOf(0));
  ctx.closePath();
  ctx.fillStyle = traceSoft;
  ctx.fill();

  ctx.beginPath();
  for (let i = 0; i < result.times.length; i++) {
    const x = xOf(result.times[i] / 60);
    const y = yOf(result.fallMm[i]);
    if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  }
  ctx.strokeStyle = trace;
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // where the tube is being read right now
  const x = xOf(result.times[state.sample] / 60);
  const y = yOf(result.fallMm[state.sample]);
  ctx.strokeStyle = ink;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, padTop);
  ctx.lineTo(x, height - padBottom);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(x, y, 4.5, 0, Math.PI * 2);
  ctx.fillStyle = trace;
  ctx.fill();
  ctx.strokeStyle = surface;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.save();
  ctx.translate(11, height / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = muted;
  ctx.font = '10px "IBM Plex Sans", system-ui, sans-serif';
  ctx.fillText('fall (mm)', 0, 0);
  ctx.restore();
}

function niceCeiling(value) {
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(value, 1e-6)));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * magnitude) return step * magnitude;
  }
  return 10 * magnitude;
}

// ------------------------------------------------------------- running
function run(quality) {
  const geometry = getGeometry(state.geometry, state.tiltDeg);
  state.result = simulate(
    geometry,
    { hematocrit: state.hematocrit, aggregateUm: state.aggregateUm },
    { durationH: DURATION_H, ...quality },
  );
  state.sample = Math.min(state.sample, state.result.times.length - 1);
  render();
}

function render() {
  const result = state.result;
  if (!result) return;
  const reading = esr(result, 1);
  el('reading-value').innerHTML = `${reading.toFixed(1)}<small>mm</small>`;
  el('esr-2h').innerHTML = `${esr(result, 2).toFixed(1)}<small> mm</small>`;
  el('peak-rate').innerHTML = `${maxSettlingRate(result).toFixed(1)}<small> mm/h</small>`;
  el('sediment').innerHTML = `${sedimentMm(result).toFixed(1)}<small> mm</small>`;
  el('katz').innerHTML = `${katzIndex(result).toFixed(1)}<small> mm</small>`;
  el('tube-volume').innerHTML =
    `${(result.geometry.length / MM).toFixed(0)}<small> mm tall</small>`;
  const minutes = result.times[state.sample] / 60;
  el('clock-time').textContent = `${minutes.toFixed(0)} min`;
  el('scrub').max = String(result.times.length - 1);
  el('scrub').value = String(state.sample);
  drawTube();
  drawChart();
}

let pendingQuality = null;
let frameHandle = 0;

/** Coalesce slider input into one run per animation frame. */
function schedule(quality) {
  pendingQuality = quality;
  if (frameHandle) return;
  frameHandle = requestAnimationFrame(() => {
    frameHandle = 0;
    const wanted = pendingQuality;
    pendingQuality = null;
    run(wanted);
  });
}

let settleTimer = 0;
function scheduleRefine() {
  clearTimeout(settleTimer);
  settleTimer = setTimeout(() => run(FULL), 220);
}

// ------------------------------------------------------------ controls
function tubeSilhouette(key) {
  const geo = GEOMETRIES[key]();
  const points = 26;
  let maxRadius = 0;
  const radii = [];
  for (let i = 0; i <= points; i++) {
    const r = radius(geo, (geo.length * i) / points);
    radii.push(r);
    maxRadius = Math.max(maxRadius, r);
  }
  const left = [];
  const right = [];
  for (let i = 0; i <= points; i++) {
    const y = 44 - (44 * i) / points;
    const half = (radii[i] / maxRadius) * 9;
    left.push(`${(11 - half).toFixed(2)},${y.toFixed(2)}`);
    right.unshift(`${(11 + half).toFixed(2)},${y.toFixed(2)}`);
  }
  return `<svg viewBox="0 0 22 44" aria-hidden="true"><path d="M${left.join('L')}L${right.join('L')}Z"/></svg>`;
}

function buildChips() {
  const tubes = el('geometry-chips');
  tubes.innerHTML = Object.keys(GEOMETRIES).map((key) => `
    <button class="chip" type="button" data-geometry="${key}"
            aria-pressed="${key === state.geometry}">
      ${tubeSilhouette(key)}<span>${GEOMETRY_LABELS[key] ?? key}</span>
    </button>`).join('');
  tubes.addEventListener('click', (event) => {
    const button = event.target.closest('[data-geometry]');
    if (!button) return;
    state.geometry = button.dataset.geometry;
    for (const chip of tubes.querySelectorAll('.chip')) {
      chip.setAttribute('aria-pressed', String(chip.dataset.geometry === state.geometry));
    }
    run(FULL);
  });

  const samples = el('sample-chips');
  const keys = ['normal', 'anemic', 'inflammation', 'severe-inflammation'];
  samples.innerHTML = keys.map((key) => `
    <button class="chip" type="button" data-sample="${key}" aria-pressed="false">
      <span>${SAMPLE_LABELS[key]}</span>
    </button>`).join('');
  samples.addEventListener('click', (event) => {
    const button = event.target.closest('[data-sample]');
    if (!button) return;
    const preset = BLOOD_PRESETS[button.dataset.sample];
    state.hematocrit = preset.hematocrit;
    state.aggregateUm = preset.aggregateUm;
    el('hematocrit').value = String(Math.round(preset.hematocrit * 100));
    el('aggregate').value = String(preset.aggregateUm);
    syncSliderLabels();
    markSample();
    run(FULL);
  });
}

function markSample() {
  for (const chip of el('sample-chips').querySelectorAll('.chip')) {
    const preset = BLOOD_PRESETS[chip.dataset.sample];
    const matches = Math.abs(preset.hematocrit - state.hematocrit) < 1e-9 &&
                    Math.abs(preset.aggregateUm - state.aggregateUm) < 1e-9;
    chip.setAttribute('aria-pressed', String(matches));
  }
}

function syncSliderLabels() {
  el('hematocrit-out').textContent = `${Math.round(state.hematocrit * 100)} %`;
  el('aggregate-out').textContent = `${state.aggregateUm} µm`;
  el('tilt-out').textContent = `${state.tiltDeg}°`;
}

function bindSliders() {
  const bind = (id, apply) => {
    const input = el(id);
    input.addEventListener('input', () => {
      apply(Number(input.value));
      syncSliderLabels();
      markSample();
      schedule(DRAFT);
      scheduleRefine();
    });
    input.addEventListener('change', () => {
      apply(Number(input.value));
      syncSliderLabels();
      markSample();
      run(FULL);
    });
  };
  bind('hematocrit', (v) => { state.hematocrit = v / 100; });
  bind('aggregate', (v) => { state.aggregateUm = v; });
  bind('tilt', (v) => { state.tiltDeg = v; });

  el('scrub').addEventListener('input', (event) => {
    stopPlaying();
    state.sample = Number(event.target.value);
    render();
  });
}

// ---------------------------------------------------------------- clock
let playHandle = 0;
let lastFrame = 0;

function stopPlaying() {
  state.playing = false;
  if (playHandle) cancelAnimationFrame(playHandle);
  playHandle = 0;
  el('play').setAttribute('aria-label', 'Play the settling');
  el('play-icon').setAttribute('d', 'M3 1.5 L13 8 L3 14.5 Z');
}

function startPlaying() {
  if (!state.result) return;
  state.playing = true;
  el('play').setAttribute('aria-label', 'Pause');
  el('play-icon').setAttribute('d', 'M3 2 H6 V14 H3 Z M10 2 H13 V14 H10 Z');
  lastFrame = performance.now();
  const tick = (now) => {
    if (!state.playing) return;
    const elapsed = now - lastFrame;
    if (elapsed > 40) {
      lastFrame = now;
      const total = state.result.times.length;
      state.sample = state.sample >= total - 1 ? 0 : state.sample + 1;
      render();
    }
    playHandle = requestAnimationFrame(tick);
  };
  playHandle = requestAnimationFrame(tick);
}

// ----------------------------------------------------------------- boot
function boot() {
  readTheme();
  buildChips();
  bindSliders();
  syncSliderLabels();
  markSample();

  el('play').addEventListener('click', () => (state.playing ? stopPlaying() : startPlaying()));

  const media = window.matchMedia('(prefers-color-scheme: dark)');
  media.addEventListener?.('change', () => { readTheme(); render(); });
  new MutationObserver(() => { readTheme(); render(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  let resizeHandle = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeHandle);
    resizeHandle = setTimeout(render, 120);
  });

  if (document.fonts?.ready) document.fonts.ready.then(render);

  run(FULL);
  state.sample = state.result.times.length - 1;
  render();
}

boot();
