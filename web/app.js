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
  ANNULAR_DEFAULTS, BLOOD_PRESETS, GEOMETRIES, HOUR, MM, esr, getGeometry, hasCore,
  innerRadius, katzIndex, maxSettlingRate, radius, sedimentMm, simulate,
  velocityField,
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
/* the returning plasma gets the one cool accent on the page */
const PLASMA_INK = '#2f7d9e';

/* One clock for everything: playing advances the settling, and the tracers
   drift at their true speed in that same accelerated time. */
const TIME_SCALE = 900;      // seconds of settling per second on screen
const TRACER_COUNT = 190;

const GEOMETRY_LABELS = {
  annular: 'Cone in cone',
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
  coneAngleDeg: ANNULAR_DEFAULTS.angleDeg,
  coneGapMm: ANNULAR_DEFAULTS.gapMm,
  showFlow: true,
  sample: 0,
  playing: false,
  result: null,
};

/** Tracer particles, advanced by the solved velocity field. */
let tracers = [];

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

/**
 * The tube, drawn in its own millimetre space and then rotated.
 *
 * Working in millimetres for both axes (with the bore widened by a fixed
 * factor so a 2.5 mm bore is visible beside a 200 mm column) means a tilt is a
 * true rotation rather than a shear -- which is the only way the Boycott effect
 * looks like what it is.
 */
function tubeFrame(width, height, result) {
  const geo = result.geometry;
  const lengthMm = geo.length / MM;
  const referenceMm = Math.max(REFERENCE_HEIGHT_MM, lengthMm);
  const tilt = (geo.tiltDeg * Math.PI) / 180;
  const cos = Math.cos(tilt);
  const sin = Math.sin(tilt);

  let widestMm = 0;
  for (let i = 0; i <= 60; i++) {
    widestMm = Math.max(widestMm, radius(geo, (geo.length * i) / 60) / MM);
  }
  // widen the bore against a common reference, so a capillary still reads narrow
  const exaggeration = (0.16 * referenceMm) / Math.max(REFERENCE_BORE_MM / 2, widestMm);
  const halfSpanMm = Math.max(widestMm * exaggeration, 4);

  const padTop = 12;
  const padBottom = 26;
  const rulerPx = 40;
  // the tube leans, so it needs room sideways as well as upright
  const spanX = 2 * halfSpanMm + Math.abs(sin) * lengthMm;
  const spanY = cos * lengthMm + 2 * halfSpanMm * Math.abs(sin);
  const pxPerMm = Math.min(
    (width - rulerPx - 78) / Math.max(spanX, 1),
    (height - padTop - padBottom) / Math.max(spanY, referenceMm * 0.55),
  );

  const footX = rulerPx + halfSpanMm * pxPerMm + 6;
  const footY = height - padBottom;

  return {
    geo, lengthMm, referenceMm, exaggeration, pxPerMm, footX, footY, halfSpanMm,
    tiltDeg: geo.tiltDeg,
    // x runs across the tube, y along its axis from the closed foot
    project(x, y) {
      return [footX + (x * cos + y * sin) * pxPerMm, footY - (y * cos - x * sin) * pxPerMm];
    },
    /* step sideways by a number of *pixels*, so ruler ticks keep their size
       whatever the tube's scale is */
    offsetPx(point, pixels) {
      return [point[0] + pixels * cos, point[1] + pixels * sin];
    },
    mmPerPixel: 1 / pxPerMm,
    halfAt(z) {
      return (radius(this.geo, z) / MM) * exaggeration;
    },
    innerAt(z) {
      const inner = (innerRadius(this.geo, z) / MM) * exaggeration;
      if (inner <= 0) return 0;
      // a real annular gap is a millimetre inside a vessel tens of mm across:
      // widen the band inward so the blood is visible, keeping the outer wall true
      const outer = (radius(this.geo, z) / MM) * exaggeration;
      const floor = 0.035 * this.referenceMm;
      return outer - inner < floor ? Math.max(outer - floor, 0) : inner;
    },
  };
}

function quad(ctx, frame, x0, x1, y0, y1) {
  const a = frame.project(x0, y0);
  const b = frame.project(x1, y0);
  const c = frame.project(x1, y1);
  const d = frame.project(x0, y1);
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.lineTo(c[0], c[1]);
  ctx.lineTo(d[0], d[1]);
  ctx.closePath();
}

function wallPath(ctx, frame, side, steps = 200) {
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const z = (frame.geo.length * i) / steps;
    const half = side < 0 ? -frame.halfAt(z) : frame.halfAt(z);
    const p = frame.project(half, z / MM);
    if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]);
  }
  ctx.stroke();
}

function corePath(ctx, frame, side, steps = 200) {
  ctx.beginPath();
  let started = false;
  for (let i = 0; i <= steps; i++) {
    const z = (frame.geo.length * i) / steps;
    const inner = frame.innerAt(z);
    if (inner <= 0) { started = false; continue; }
    const p = frame.project(side < 0 ? -inner : inner, z / MM);
    if (started) ctx.lineTo(p[0], p[1]); else { ctx.moveTo(p[0], p[1]); started = true; }
  }
  ctx.stroke();
}

function drawTube() {
  const { width, height } = fitCanvas(tubeCanvas, tubeCtx);
  const ctx = tubeCtx;
  ctx.clearRect(0, 0, width, height);
  const result = state.result;
  if (!result) return;

  const frame = tubeFrame(width, height, result);
  const { geo } = frame;
  const phi = result.phi[state.sample];
  const zFaces = result.zFaces;
  const maxPacking = result.blood.maxPacking;
  const annular = hasCore(geo);

  // --- etched scale, attached to the tube so it leans with it ----------
  ctx.font = '10px "IBM Plex Mono", ui-monospace, monospace';
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'right';
  const majorStep = frame.lengthMm > 150 ? 50 : frame.lengthMm > 60 ? 25 : 10;
  for (let mm = 0; mm <= frame.lengthMm + 1e-6; mm += majorStep / 5) {
    const major = Math.abs(mm % majorStep) < 1e-6;
    const wall = frame.project(-frame.halfAt(mm * MM), mm);
    const from = frame.offsetPx(wall, -(major ? 14 : 8));
    const to = frame.offsetPx(wall, -3);
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(from[0], from[1]);
    ctx.lineTo(to[0], to[1]);
    ctx.stroke();
    if (major) {
      const at = frame.offsetPx(wall, -17);
      ctx.fillStyle = muted;
      ctx.fillText(String(Math.round(mm)), Math.max(at[0], 22), at[1]);
    }
  }

  // --- glass, then the suspension cell by cell -------------------------
  ctx.save();
  ctx.beginPath();
  for (let i = 0; i <= 200; i++) {
    const z = (geo.length * i) / 200;
    const p = frame.project(-frame.halfAt(z), z / MM);
    if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]);
  }
  for (let i = 200; i >= 0; i--) {
    const z = (geo.length * i) / 200;
    const p = frame.project(frame.halfAt(z), z / MM);
    ctx.lineTo(p[0], p[1]);
  }
  ctx.closePath();
  ctx.fillStyle = glass;
  ctx.fill();
  ctx.clip();

  const seam = 0.6 * frame.mmPerPixel;   // hide the hairline between bands
  for (let i = 0; i < phi.length; i++) {
    const y0 = zFaces[i] / MM - seam;
    const y1 = zFaces[i + 1] / MM + seam;
    const outer = Math.max(frame.halfAt(zFaces[i]), frame.halfAt(zFaces[i + 1]));
    const inner = annular
      ? Math.min(frame.innerAt(zFaces[i]), frame.innerAt(zFaces[i + 1]))
      : 0;
    ctx.fillStyle = rampColor(phi[i], maxPacking);
    if (inner > 0) {
      quad(ctx, frame, inner, outer, y0, y1);
      ctx.fill();
      quad(ctx, frame, -outer, -inner, y0, y1);
      ctx.fill();
    } else {
      quad(ctx, frame, -outer, outer, y0, y1);
      ctx.fill();
    }
  }

  if (state.showFlow) drawBoycottLayers(ctx, frame, result);
  if (state.showFlow) drawTracers(ctx, frame, result);
  ctx.restore();

  // --- walls ----------------------------------------------------------
  ctx.strokeStyle = glassRim;
  ctx.lineWidth = 1.3;
  wallPath(ctx, frame, -1);
  wallPath(ctx, frame, 1);
  if (annular) {
    ctx.lineWidth = 1.1;
    corePath(ctx, frame, -1);
    corePath(ctx, frame, 1);
  }
  const bottomLeft = frame.project(-frame.halfAt(0), 0);
  const bottomRight = frame.project(frame.halfAt(0), 0);
  ctx.lineWidth = 1.3;
  ctx.beginPath();
  ctx.moveTo(bottomLeft[0], bottomLeft[1]);
  ctx.lineTo(bottomRight[0], bottomRight[1]);
  ctx.stroke();

  // --- the reading ----------------------------------------------------
  const boundaryMm = result.interface[state.sample] / MM;
  const half = frame.halfAt(result.interface[state.sample]);
  const left = frame.project(-half - 5, boundaryMm);
  const right = frame.project(half + 5, boundaryMm);
  ctx.strokeStyle = ink;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(left[0], left[1]);
  ctx.lineTo(right[0], right[1]);
  ctx.stroke();

  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
  ctx.font = '600 11px "Barlow Semi Condensed", Arial, sans-serif';
  ctx.fillStyle = ink;
  const labelX = Math.min(Math.max(right[0], right[0]), width - 74);
  ctx.fillText(`${result.fallMm[state.sample].toFixed(1)} mm fallen`, labelX + 5, right[1] - 3);

  // --- gravity, so the lean is unambiguous ----------------------------
  if (geo.tiltDeg !== 0) {
    const gx = width - 20;
    const gy = 20;
    ctx.strokeStyle = muted;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(gx, gy);
    ctx.lineTo(gx, gy + 22);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(gx - 3.5, gy + 16);
    ctx.lineTo(gx, gy + 23);
    ctx.lineTo(gx + 3.5, gy + 16);
    ctx.fillStyle = muted;
    ctx.fill();
    ctx.textAlign = 'right';
    ctx.font = 'italic 10px "IBM Plex Sans", system-ui, sans-serif';
    ctx.fillText('g', gx - 5, gy + 16);
  }

  ctx.textAlign = 'center';
  ctx.fillStyle = muted;
  ctx.font = '10px "IBM Plex Sans", system-ui, sans-serif';
  const note = annular ? 'height in mm · bore and gap exaggerated'
                       : 'height in mm · bore exaggerated';
  ctx.fillText(note, width / 2, height - 6);
}

/**
 * The two wall layers a tilted tube develops: clear plasma running up the
 * raised side, sediment sliding down the lowered one. The 1-D model resolves
 * only the axis, so these are drawn schematically -- their thickness follows
 * how much of the enhancement comes from the tilt.
 */
function drawBoycottLayers(ctx, frame, result) {
  const tilt = frame.geo.tiltDeg;
  if (!tilt) return;
  const lam = result.enhancement[state.sample];
  const share = Math.min(Math.max((lam - 1) / Math.max(lam, 1e-9), 0), 0.5);
  if (share < 0.02) return;

  const top = result.interface[state.sample];
  const bottom = result.sediment[state.sample];
  if (top <= bottom) return;

  const steps = 60;
  for (const [side, color, alpha] of [[-1, BLOOD_RAMP[0], 0.92], [1, BLOOD_RAMP[7], 0.5]]) {
    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const z = bottom + ((top - bottom) * i) / steps;
      const half = frame.halfAt(z);
      const p = frame.project(side * half, z / MM);
      if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]);
    }
    for (let i = steps; i >= 0; i--) {
      const z = bottom + ((top - bottom) * i) / steps;
      const half = frame.halfAt(z);
      const p = frame.project(side * half * (1 - share), z / MM);
      ctx.lineTo(p[0], p[1]);
    }
    ctx.closePath();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = color;
    ctx.fill();
    ctx.globalAlpha = 1;
  }
}

/** Tracers carried by the velocity field the model actually solved. */
function drawTracers(ctx, frame, result) {
  for (const tracer of tracers) {
    const half = frame.halfAt(tracer.z);
    const inner = frame.innerAt(tracer.z);
    const across = inner > 0
      ? tracer.side * (inner + (half - inner) * tracer.lateral)
      : tracer.lateral * half * tracer.side;
    const [px, py] = frame.project(across, tracer.z / MM);
    ctx.beginPath();
    ctx.arc(px, py, tracer.phase === 'cells' ? 1.9 : 1.5, 0, Math.PI * 2);
    ctx.fillStyle = tracer.phase === 'cells' ? BLOOD_RAMP[7] : PLASMA_INK;
    ctx.globalAlpha = tracer.phase === 'cells' ? 0.75 : 0.85;
    ctx.fill();
    ctx.globalAlpha = 1;
  }
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

// -------------------------------------------------------------- tracers
function seedTracers(result) {
  const geo = result.geometry;
  tracers = [];
  for (let i = 0; i < TRACER_COUNT; i++) {
    tracers.push({
      phase: i % 3 === 0 ? 'plasma' : 'cells',
      z: Math.random() * result.fillHeight,
      lateral: 0.15 + Math.random() * 0.8,
      side: Math.random() < 0.5 ? -1 : 1,
    });
  }
  void geo;
}

/**
 * Move every tracer by the local phase velocity over ``dtSim`` seconds of
 * settling. Cells fall, plasma rises, both at the speed the solver produced --
 * nothing here is invented, only carried.
 */
function advanceTracers(dtSim) {
  const result = state.result;
  if (!result || !tracers.length) return;
  const { cells, plasma } = velocityField(result, state.sample);
  const z = result.zCenters;
  const dz = z.length > 1 ? z[1] - z[0] : 1;
  const top = result.interface[state.sample];
  const floor = result.sediment[state.sample];

  for (const tracer of tracers) {
    const i = Math.min(Math.max(Math.round((tracer.z - z[0]) / dz), 0), z.length - 1);
    const speed = tracer.phase === 'cells' ? cells[i] : plasma[i];
    tracer.z += (tracer.phase === 'cells' ? -1 : 1) * speed * dtSim;

    // recycle: cells re-enter under the boundary, plasma re-enters above the bed
    if (tracer.phase === 'cells' && tracer.z <= floor + 1e-6) {
      tracer.z = top - Math.random() * Math.max(top - floor, 1e-6) * 0.25;
      tracer.lateral = 0.15 + Math.random() * 0.8;
      tracer.side = Math.random() < 0.5 ? -1 : 1;
    } else if (tracer.phase === 'plasma' && tracer.z >= top - 1e-6) {
      tracer.z = floor + Math.random() * Math.max(top - floor, 1e-6) * 0.25;
      tracer.lateral = 0.15 + Math.random() * 0.8;
      tracer.side = Math.random() < 0.5 ? -1 : 1;
    }
    tracer.z = Math.min(Math.max(tracer.z, 0), result.fillHeight);
  }
}

// ------------------------------------------------------------- running
function currentGeometry() {
  return getGeometry(state.geometry, state.tiltDeg, {
    angleDeg: state.coneAngleDeg,
    gapMm: state.coneGapMm,
  });
}

function run(quality) {
  let geometry;
  try {
    geometry = currentGeometry();
  } catch (error) {
    el('geometry-error').textContent = error.message;
    return;
  }
  el('geometry-error').textContent = '';
  state.result = simulate(
    geometry,
    { hematocrit: state.hematocrit, aggregateUm: state.aggregateUm },
    { durationH: DURATION_H, ...quality },
  );
  state.sample = Math.min(state.sample, state.result.times.length - 1);
  seedTracers(state.result);
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

  const { cells, plasma } = velocityField(result, state.sample);
  const perHour = HOUR / MM;
  let fastestCells = 0;
  let fastestPlasma = 0;
  for (let i = 0; i < cells.length; i++) {
    if (result.phi[state.sample][i] > 0.01) fastestCells = Math.max(fastestCells, cells[i]);
    fastestPlasma = Math.max(fastestPlasma, plasma[i]);
  }
  el('flow-cells').innerHTML = `${(fastestCells * perHour).toFixed(1)}<small> mm/h</small>`;
  el('flow-plasma').innerHTML = `${(fastestPlasma * perHour).toFixed(1)}<small> mm/h</small>`;
  el('flow-boost').innerHTML =
    `${result.enhancement[state.sample].toFixed(2)}<small>×</small>`;

  const annular = state.geometry === 'annular';
  el('cone-controls').hidden = !annular;
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
  const radii = [];
  const cores = [];
  let maxRadius = 0;
  for (let i = 0; i <= points; i++) {
    const z = (geo.length * i) / points;
    const r = radius(geo, z);
    radii.push(r);
    cores.push(innerRadius(geo, z));
    maxRadius = Math.max(maxRadius, r);
  }
  const band = (outerScale, innerScale) => {
    const left = [];
    const right = [];
    for (let i = 0; i <= points; i++) {
      const y = 44 - (44 * i) / points;
      left.push(`${(11 - outerScale(i)).toFixed(2)},${y.toFixed(2)}`);
      right.unshift(`${(11 + outerScale(i)).toFixed(2)},${y.toFixed(2)}`);
    }
    void innerScale;
    return `M${left.join('L')}L${right.join('L')}Z`;
  };
  const outer = (i) => (radii[i] / maxRadius) * 9;
  let path = band(outer);
  if (hasCore(geo)) {
    // punch the core out, so a cone-in-cone reads as a gap rather than a funnel
    const inner = [];
    const innerRight = [];
    for (let i = 0; i <= points; i++) {
      const y = 44 - (44 * i) / points;
      const half = Math.max((cores[i] / maxRadius) * 9, 0);
      inner.push(`${(11 - half).toFixed(2)},${y.toFixed(2)}`);
      innerRight.unshift(`${(11 + half).toFixed(2)},${y.toFixed(2)}`);
    }
    path += `M${inner.join('L')}L${innerRight.join('L')}Z`;
  }
  return `<svg viewBox="0 0 22 44" aria-hidden="true"><path fill-rule="evenodd" d="${path}"/></svg>`;
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
  el('cone-angle-out').textContent = `${state.coneAngleDeg}°`;
  el('cone-gap-out').textContent = `${state.coneGapMm.toFixed(1)} mm`;
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
  bind('cone-angle', (v) => { state.coneAngleDeg = v; });
  bind('cone-gap', (v) => { state.coneGapMm = v / 10; });

  const flow = el('flow-toggle');
  flow.addEventListener('click', () => {
    state.showFlow = !state.showFlow;
    flow.setAttribute('aria-pressed', String(state.showFlow));
    flow.querySelector('span').textContent = state.showFlow ? 'Flow on' : 'Flow off';
    render();
  });

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
    const dtReal = Math.min((now - lastFrame) / 1000, 0.1);
    lastFrame = now;
    const times = state.result.times;
    const dtSim = dtReal * TIME_SCALE;
    let clock = times[state.sample] + dtSim;
    if (clock > times[times.length - 1]) clock = 0;
    // nearest stored profile, then carry the tracers over the same interval
    let index = Math.round(clock / (times[1] - times[0]));
    index = Math.min(Math.max(index, 0), times.length - 1);
    state.sample = index;
    advanceTracers(dtSim);
    render();
    playHandle = requestAnimationFrame(tick);
  };
  playHandle = requestAnimationFrame(tick);
}

/** Keep the tracers drifting even while paused, so the flow stays readable. */
function idleDrift() {
  let last = performance.now();
  const tick = (now) => {
    const dtReal = Math.min((now - last) / 1000, 0.1);
    last = now;
    if (!state.playing && state.showFlow && state.result && !document.hidden) {
      advanceTracers(dtReal * TIME_SCALE);
      drawTube();
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
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

  idleDrift();
  run(FULL);
  state.sample = state.result.times.length - 1;
  render();
}

boot();
