/**
 * bloodsed -- sedimentation solver, browser/Node port.
 *
 * A line-for-line port of the Python package's physics: Kynch's batch
 * sedimentation conservation law on a tube of varying cross-section, solved
 * with finite volumes and the exact Godunov flux in supply/demand form.
 * `web/test_sim.mjs` checks it against reference values produced by the Python
 * model, so the two stay in step.
 *
 * Runs in a few milliseconds for a phone-sized mesh, which is why the app can
 * recompute on every slider move instead of showing a progress bar.
 */

export const MM = 1e-3;
export const HOUR = 3600;
export const GRAVITY = 9.80665;

// ---------------------------------------------------------------- blood
export const BLOOD_DEFAULTS = {
  hematocrit: 0.45,
  plasmaDensity: 1025.0,
  rbcDensity: 1093.0,
  plasmaViscosity: 1.6e-3,
  aggregateUm: 60.0,
  shapeFactor: 0.6,
  maxPacking: 0.9,
  exponent: 4.65,
  aggregationTimeS: 300.0,
};

export const BLOOD_PRESETS = {
  normal: { hematocrit: 0.45, aggregateUm: 60, aggregationTimeS: 300 },
  anemic: { hematocrit: 0.28, aggregateUm: 70, aggregationTimeS: 300 },
  polycythemic: { hematocrit: 0.62, aggregateUm: 55, aggregationTimeS: 300 },
  inflammation: { hematocrit: 0.4, aggregateUm: 110, aggregationTimeS: 240 },
  'severe-inflammation': { hematocrit: 0.35, aggregateUm: 160, aggregationTimeS: 180 },
  newborn: { hematocrit: 0.55, aggregateUm: 35, aggregationTimeS: 420 },
};

export function makeBlood(overrides = {}) {
  const blood = { ...BLOOD_DEFAULTS, ...overrides };
  if (!(blood.hematocrit >= 0 && blood.hematocrit < blood.maxPacking)) {
    throw new Error('hematocrit must be below the packing limit');
  }
  if (blood.aggregateUm <= 0 || blood.plasmaViscosity <= 0) {
    throw new Error('aggregate size and viscosity must be positive');
  }
  if (blood.rbcDensity <= blood.plasmaDensity) {
    throw new Error('red cells must be denser than plasma to settle');
  }
  return blood;
}

/** Terminal velocity of one isolated aggregate in still plasma [m/s]. */
export function stokesVelocity(blood) {
  const d = blood.aggregateUm * 1e-6;
  return (
    (blood.shapeFactor * (blood.rbcDensity - blood.plasmaDensity) * GRAVITY * d * d) /
    (18 * blood.plasmaViscosity)
  );
}

/** Fraction of terminal velocity reached at time t -- the rouleaux lag. */
export function aggregationFactor(blood, t) {
  if (!(blood.aggregationTimeS > 0)) return 1;
  return 1 - Math.exp(-t / blood.aggregationTimeS);
}

// ------------------------------------------------------------- geometry
const CUM_POINTS = 20001;

function makeGeometry({ name, lengthMm, radiusAt, innerRadiusAt = null,
                       breakpoints = [], tiltDeg = 0, kind }) {
  return {
    name, kind, length: lengthMm * MM, tiltDeg, radiusAt,
    innerRadiusAt, breakpoints,
    _cum: null, _wall: null, _hyd: null,
  };
}

export function cylinder(lengthMm, diameterMm, opts = {}) {
  const r = 0.5 * diameterMm * MM;
  return makeGeometry({
    kind: 'cylinder',
    name: opts.name ?? `straight ${diameterMm} mm`,
    lengthMm,
    tiltDeg: opts.tiltDeg ?? 0,
    radiusAt: () => r,
  });
}

export function taper(lengthMm, bottomMm, topMm, opts = {}) {
  const r0 = 0.5 * bottomMm * MM;
  const r1 = 0.5 * topMm * MM;
  const length = lengthMm * MM;
  return makeGeometry({
    kind: 'taper',
    name: opts.name ?? `taper ${bottomMm}-${topMm} mm`,
    lengthMm,
    tiltDeg: opts.tiltDeg ?? 0,
    radiusAt: (z) => r0 + (r1 - r0) * (z / length),
  });
}

export function hourglass(lengthMm, endMm, throatMm, at = 0.5, opts = {}) {
  const length = lengthMm * MM;
  const rEnd = 0.5 * endMm * MM;
  const rThroat = 0.5 * throatMm * MM;
  const zt = at * length;
  return makeGeometry({
    kind: 'hourglass',
    name: opts.name ?? `hourglass ${endMm}/${throatMm} mm`,
    lengthMm,
    tiltDeg: opts.tiltDeg ?? 0,
    breakpoints: [zt],
    radiusAt: (z) =>
      z <= zt
        ? rEnd + (rThroat - rEnd) * (z / zt)
        : rThroat + (rEnd - rThroat) * ((z - zt) / (length - zt)),
  });
}

export function bulb(lengthMm, diameterMm, bulgeMm, pos = 0.5, width = 0.12, opts = {}) {
  const length = lengthMm * MM;
  const r = 0.5 * diameterMm * MM;
  const rb = 0.5 * bulgeMm * MM;
  const zc = pos * length;
  const w = width * length;
  return makeGeometry({
    kind: 'bulb',
    name: opts.name ?? `bulb ${diameterMm}/${bulgeMm} mm`,
    lengthMm,
    tiltDeg: opts.tiltDeg ?? 0,
    radiusAt: (z) => r + (rb - r) * Math.exp(-0.5 * ((z - zc) / w) ** 2),
  });
}

/**
 * A cone standing inside another cone, with the blood filling the gap: a
 * lamella settler. Every wall is inclined, so cells fall only the width of the
 * gap before landing on the outer cone, and clear plasma is released under the
 * inner one -- the Boycott effect without tilting anything.
 */
export function annularCone(lengthMm, bottomDiameterMm, angleDeg, gapMm, opts = {}) {
  const length = lengthMm * MM;
  const r0 = 0.5 * bottomDiameterMm * MM;
  const slope = Math.tan((angleDeg * Math.PI) / 180);
  const innerSlope = Math.tan((((opts.innerAngleDeg ?? angleDeg)) * Math.PI) / 180);
  const radialGap = (gapMm * MM) / Math.cos((angleDeg * Math.PI) / 180);
  const geo = makeGeometry({
    kind: 'annular',
    name: opts.name ?? `annular ${angleDeg}° / ${gapMm} mm`,
    lengthMm,
    tiltDeg: opts.tiltDeg ?? 0,
    radiusAt: (z) => r0 + slope * z,
    innerRadiusAt: (z) => Math.max(r0 - radialGap + innerSlope * z, 0),
  });
  geo.angleDeg = angleDeg;
  geo.gap = gapMm * MM;
  for (let i = 0; i <= 256; i++) {
    const z = (length * i) / 256;
    if (radius(geo, z) - innerRadius(geo, z) <= 1e-6) {
      throw new Error('the two cones meet: widen the tube, narrow the gap, or ease the inner angle');
    }
  }
  return geo;
}

export function stepped(segments, opts = {}) {
  const edges = [0];
  const radii = [];
  for (const [segLenMm, segDiaMm] of segments) {
    edges.push(edges[edges.length - 1] + segLenMm * MM);
    radii.push(0.5 * segDiaMm * MM);
  }
  const total = edges[edges.length - 1];
  return makeGeometry({
    kind: 'stepped',
    name: opts.name ?? 'stepped bore',
    lengthMm: total / MM,
    tiltDeg: opts.tiltDeg ?? 0,
    breakpoints: edges.slice(1, -1),
    radiusAt: (z) => {
      for (let i = radii.length - 1; i >= 0; i--) if (z >= edges[i]) return radii[i];
      return radii[0];
    },
  });
}

export function radius(geo, z) {
  return geo.radiusAt(Math.min(Math.max(z, 0), geo.length));
}

/** Radius of the core the blood flows around; 0 when the tube has none. */
export function innerRadius(geo, z) {
  if (!geo.innerRadiusAt) return 0;
  const clamped = Math.min(Math.max(z, 0), geo.length);
  return Math.min(Math.max(geo.innerRadiusAt(clamped), 0), geo.radiusAt(clamped));
}

export const hasCore = (geo) => Boolean(geo.innerRadiusAt);

/** Flow area -- the annulus between the walls when there is a core. */
export function area(geo, z) {
  const outer = radius(geo, z);
  const inner = innerRadius(geo, z);
  return Math.PI * (outer * outer - inner * inner);
}

export function diameter(geo, z) {
  return 2 * radius(geo, z);
}

/**
 * 4A/P -- the width that matters to a settling cell: the bore of a plain tube,
 * twice the gap of an annulus.
 */
export function hydraulicDiameter(geo, z) {
  const outer = radius(geo, z);
  const inner = innerRadius(geo, z);
  const perimeter = 2 * Math.PI * (outer + inner);
  return perimeter > 0 ? (4 * area(geo, z)) / perimeter : 2 * outer;
}

function integrationGrid(geo, intervals) {
  const grid = new Float64Array(intervals + 1);
  for (let i = 0; i <= intervals; i++) grid[i] = (geo.length * i) / intervals;
  if (!geo.breakpoints.length) return grid;
  // bracket each discontinuity so a trapezoid never straddles it
  const eps = 1e-9 * Math.max(geo.length, 1e-9);
  const extra = [];
  for (const b of geo.breakpoints) {
    if (b > 0 && b < geo.length) extra.push(b - eps, b + eps);
  }
  const merged = Array.from(grid).concat(extra).sort((a, b) => a - b);
  return Float64Array.from(merged);
}

/** Cached table of (height, volume below that height). */
export function cumulative(geo) {
  if (geo._cum) return geo._cum;
  const grid = integrationGrid(geo, CUM_POINTS - 1);
  const cum = new Float64Array(grid.length);
  let running = 0;
  let aPrev = area(geo, grid[0]);
  for (let i = 1; i < grid.length; i++) {
    const aHere = area(geo, grid[i]);
    running += (grid[i] - grid[i - 1]) * 0.5 * (aPrev + aHere);
    cum[i] = running;
    aPrev = aHere;
  }
  geo._cum = { grid, cum };
  return geo._cum;
}

/**
 * Cached table of the walls' horizontal projection below each height.
 *
 * A wall that is not vertical is a settling surface: cells land on its
 * upward-facing side, clear plasma is released under its downward-facing side.
 * For an axisymmetric wall the horizontal projection between two heights is
 * exactly the change in the circle it encloses, so the total is the total
 * variation of pi r^2 -- no angles to estimate.
 */
function wallTable(geo) {
  if (geo._wall) return geo._wall;
  const grid = integrationGrid(geo, 4096);
  const cum = new Float64Array(grid.length);
  let running = 0;
  let prevOuter = Math.PI * radius(geo, grid[0]) ** 2;
  let prevInner = Math.PI * innerRadius(geo, grid[0]) ** 2;
  for (let i = 1; i < grid.length; i++) {
    const outer = Math.PI * radius(geo, grid[i]) ** 2;
    const inner = Math.PI * innerRadius(geo, grid[i]) ** 2;
    running += Math.abs(outer - prevOuter) + Math.abs(inner - prevInner);
    cum[i] = running;
    prevOuter = outer;
    prevInner = inner;
  }
  geo._wall = { grid, cum };
  return geo._wall;
}

export function wallProjection(geo, zLo, zHi) {
  if (zHi <= zLo) return 0;
  const { grid, cum } = wallTable(geo);
  const lo = interpolate(Math.min(Math.max(zLo, 0), geo.length), grid, cum);
  const hi = interpolate(Math.min(Math.max(zHi, 0), geo.length), grid, cum);
  return Math.max(hi - lo, 0);
}

function hydraulicTable(geo) {
  if (geo._hyd) return geo._hyd;
  const grid = integrationGrid(geo, 4096);
  const cum = new Float64Array(grid.length);
  let running = 0;
  let prev = hydraulicDiameter(geo, grid[0]);
  for (let i = 1; i < grid.length; i++) {
    const here = hydraulicDiameter(geo, grid[i]);
    running += (grid[i] - grid[i - 1]) * 0.5 * (prev + here);
    cum[i] = running;
    prev = here;
  }
  geo._hyd = { grid, cum };
  return geo._hyd;
}

export function tubeVolume(geo) {
  const { cum } = cumulative(geo);
  return cum[cum.length - 1];
}

/** Linear interpolation on a sorted table. */
export function interpolate(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  const last = xs.length - 1;
  if (x >= xs[last]) return ys[last];
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid;
    else hi = mid;
  }
  const span = xs[hi] - xs[lo];
  if (span <= 0) return ys[lo];
  return ys[lo] + ((x - xs[lo]) / span) * (ys[hi] - ys[lo]);
}

/** Volume of each cell delimited by zFaces -- sums to the tube volume exactly. */
export function cellVolumes(geo, zFaces) {
  const { grid, cum } = cumulative(geo);
  const n = zFaces.length - 1;
  const volumes = new Float64Array(n);
  let previous = interpolate(zFaces[0], grid, cum);
  for (let i = 0; i < n; i++) {
    const next = interpolate(zFaces[i + 1], grid, cum);
    volumes[i] = next - previous;
    previous = next;
  }
  return volumes;
}

/** Length-averaged hydraulic diameter -- the gap the Boycott model works with. */
export function meanDiameter(geo, zLo, zHi) {
  if (zHi <= zLo) return hydraulicDiameter(geo, zLo);
  const { grid, cum } = hydraulicTable(geo);
  const lo = interpolate(Math.min(Math.max(zLo, 0), geo.length), grid, cum);
  const hi = interpolate(Math.min(Math.max(zHi, 0), geo.length), grid, cum);
  return (hi - lo) / (zHi - zLo);
}

// ----------------------------------------------------------------- flux
export function makeFluxLaw(name, exponent, maxPacking) {
  let shape;
  let phiStar;
  if (name === 'hindered-packing') {
    shape = (p) => {
      const x = Math.min(Math.max(p / maxPacking, 0), 1);
      return Math.max(p, 0) * Math.pow(1 - x, exponent);
    };
    phiStar = maxPacking / (exponent + 1);
  } else if (name === 'richardson-zaki') {
    shape = (p) => {
      if (p >= maxPacking) return 0;
      const x = Math.min(Math.max(p, 0), 1);
      return x * Math.pow(1 - x, exponent);
    };
    phiStar = Math.min(1 / (exponent + 1), maxPacking * (1 - 1e-9));
  } else if (name === 'free') {
    shape = (p) => (p >= maxPacking ? 0 : Math.max(p, 0));
    phiStar = maxPacking * (1 - 1e-9);
  } else {
    throw new Error(`unknown flux law ${name}`);
  }
  const psiMax = shape(phiStar);
  return {
    name,
    shape,
    phiStar,
    psiMax,
    maxPacking,
    demand: (p) => (p <= phiStar ? shape(p) : psiMax),
    supply: (p) => (p <= phiStar ? psiMax : shape(p)),
  };
}

/** Faxen wall retardation, using the local tube diameter. */
export function wallFactor(particleDiameter, tubeDiameter, floor = 0.05) {
  const lam = Math.min(Math.max(particleDiameter / Math.max(tubeDiameter, 1e-12), 0), 0.8);
  const k = 1 - 2.104 * lam + 2.089 * lam ** 3 - 0.948 * lam ** 5;
  return Math.min(Math.max(k, floor), 1);
}

/**
 * Boycott enhancement: the horizontal projection of every settling surface,
 * divided by the cross-section the boundary is moving through.
 *
 * Two surfaces contribute and they simply add: the side of a tilted tube
 * (4 L sin(theta) / pi d), and any wall that is not vertical, which is what
 * makes a cone settle faster than a cylinder standing perfectly upright.
 * `efficiency` is calibrated so a 3 degree tilt inflates a Westergren reading
 * by about 30 %, as the clinical rule of thumb has it.
 */
export function boycottFactor(tiltDeg, suspensionLength, gap, options = {}) {
  const {
    model = 'pnk', efficiency = 0.08, maxEnhancement = 10,
    walls = true, wallProjection: projectedWall = 0, area: areaRef = 0,
  } = options;
  const theta = (tiltDeg * Math.PI) / 180;
  const cos = Math.max(Math.cos(theta), 0);
  if (model === 'none') return cos;

  let projected = 0;
  if (gap > 0 && suspensionLength > 0) {
    projected += (4 * suspensionLength * Math.abs(Math.sin(theta))) / (Math.PI * gap);
  }
  if (walls && projectedWall > 0 && areaRef > 0) projected += projectedWall / areaRef;
  if (projected <= 0) return cos;
  return Math.min(cos + efficiency * projected, maxEnhancement);
}

// --------------------------------------------------------------- solver
export const CONFIG_DEFAULTS = {
  durationH: 2.0,
  nCells: 400,
  sampleIntervalS: 60,
  cfl: 0.4,
  fillFraction: 1.0,
  fluxLaw: 'hindered-packing',
  wallCorrection: true,
  aggregationLag: true,
  interfaceFraction: 0.5,
  sedimentFraction: 0.5,
  boycott: { model: 'pnk', efficiency: 0.08, maxEnhancement: 10, walls: true },
};

/**
 * Height of the plasma/cell boundary, resolved below one cell: the height a
 * sharp boundary would sit at to carry the same cell volume as the profile,
 * anchored below the front so a concentrated column lower down cannot bias it.
 */
function interfaceHeight(zFaces, cumVolume, volumes, phi, threshold, ceiling) {
  const n = phi.length;
  let k = -1;
  for (let i = n - 1; i >= 0; i--) {
    if (phi[i] >= threshold) { k = i; break; }
  }
  if (k < 0) return 0;

  const lo = Math.max(k - 200, 0);
  let anchor = lo;
  for (let j = k; j > lo; j--) {
    // walk down through the front until the profile stops climbing
    if (Math.max(phi[j - 1], 1e-30) / Math.max(phi[j], 1e-30) <= 1.02) { anchor = j; break; }
  }

  const window = [];
  for (let i = Math.max(anchor - 2, 0); i <= anchor; i++) window.push(phi[i]);
  window.sort((a, b) => a - b);
  const reference = window[(window.length - 1) >> 1];
  if (reference <= 1e-12) return Math.min(zFaces[k + 1], ceiling);

  let cellsAbove = 0;
  for (let i = anchor; i < n; i++) cellsAbove += volumes[i] * phi[i];
  const target = cumVolume[anchor] + cellsAbove / reference;
  return Math.min(interpolate(target, cumVolume, zFaces), ceiling);
}

function crossingHeight(zCenters, phi, threshold, ceiling) {
  const n = phi.length;
  let k = -1;
  for (let i = n - 1; i >= 0; i--) {
    if (phi[i] >= threshold) { k = i; break; }
  }
  if (k < 0) return 0;
  if (k >= n - 1) return Math.min(zCenters[k], ceiling);
  const loValue = phi[k];
  const hiValue = phi[k + 1];
  if (loValue <= hiValue) return Math.min(zCenters[k], ceiling);
  const frac = (loValue - threshold) / (loValue - hiValue);
  return Math.min(zCenters[k] + frac * (zCenters[k + 1] - zCenters[k]), ceiling);
}

export function simulate(geometry, bloodInput = {}, configInput = {}) {
  const started = (typeof performance !== 'undefined' ? performance : Date).now();
  const blood = makeBlood(bloodInput);
  const config = {
    ...CONFIG_DEFAULTS,
    ...configInput,
    boycott: { ...CONFIG_DEFAULTS.boycott, ...(configInput.boycott ?? {}) },
  };

  const n = config.nCells;
  const zFaces = new Float64Array(n + 1);
  for (let i = 0; i <= n; i++) zFaces[i] = (geometry.length * i) / n;
  const zCenters = new Float64Array(n);
  for (let i = 0; i < n; i++) zCenters[i] = 0.5 * (zFaces[i] + zFaces[i + 1]);

  const areas = new Float64Array(n + 1);
  for (let i = 0; i <= n; i++) areas[i] = area(geometry, zFaces[i]);
  const volumes = cellVolumes(geometry, zFaces);

  const law = makeFluxLaw(config.fluxLaw, blood.exponent, blood.maxPacking);
  const u0 = stokesVelocity(blood);

  // fill level snapped to a cell face, so the first reading is exactly zero
  let fillTarget = config.fillHeightMm != null
    ? config.fillHeightMm * MM
    : config.fillFraction * geometry.length;
  fillTarget = Math.min(Math.max(fillTarget, zFaces[1]), geometry.length);
  let fillIndex = 1;
  let best = Infinity;
  for (let i = 1; i <= n; i++) {
    const d = Math.abs(zFaces[i] - fillTarget);
    if (d < best) { best = d; fillIndex = i; }
  }
  const fillHeight = zFaces[fillIndex];

  const phi = new Float64Array(n);
  phi.fill(blood.hematocrit, 0, fillIndex);
  let mass0 = 0;
  for (let i = 0; i < n; i++) mass0 += volumes[i] * phi[i];

  const uFace = new Float64Array(n + 1);
  for (let i = 0; i <= n; i++) {
    uFace[i] = config.wallCorrection
      ? u0 * wallFactor(blood.aggregateUm * 1e-6, hydraulicDiameter(geometry, zFaces[i]))
      : u0;
  }

  // CFL geometric factor: dt = cfl * gMin / (u0 * ramp * lambda)
  let gMin = Infinity;
  for (let i = 0; i < n; i++) {
    const q = Math.max(areas[i] * (uFace[i] / u0), areas[i + 1] * (uFace[i + 1] / u0));
    gMin = Math.min(gMin, volumes[i] / Math.max(q, 1e-300));
  }

  const cumVolume = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) cumVolume[i + 1] = cumVolume[i] + volumes[i];

  const durationS = config.durationH * HOUR;
  const sampleTimes = [];
  for (let t = 0; t <= durationS + 1e-9; t += config.sampleIntervalS) sampleTimes.push(t);
  if (sampleTimes[sampleTimes.length - 1] < durationS - 1e-9) sampleTimes.push(durationS);
  const nSamples = sampleTimes.length;

  const phiHistory = [];
  const interfaceH = new Float64Array(nSamples);
  const sedimentH = new Float64Array(nSamples);
  const enhancement = new Float64Array(nSamples).fill(1);
  const aggregation = new Float64Array(nSamples).fill(1);

  const ifaceThreshold = config.interfaceFraction * blood.hematocrit;
  const sedThreshold =
    blood.hematocrit + config.sedimentFraction * (blood.maxPacking - blood.hematocrit);

  const readInterface = () =>
    interfaceHeight(zFaces, cumVolume, volumes, phi, ifaceThreshold, fillHeight);

  const record = (k, lam, agg) => {
    phiHistory.push(Float64Array.from(phi));
    interfaceH[k] = readInterface();
    sedimentH[k] = crossingHeight(zCenters, phi, sedThreshold, fillHeight);
    enhancement[k] = lam;
    aggregation[k] = agg;
  };
  record(0, 1, config.aggregationLag ? aggregationFactor(blood, 0) : 1);

  // a straight upright tube has a constant enhancement; a cone does not,
  // because its inclined walls enter the column as the boundary descends
  const hasWalls = config.boycott.walls !== false
    && wallProjection(geometry, 0, geometry.length) > 0;
  const fixedLambda =
    config.boycott.model !== 'pnk' || (geometry.tiltDeg === 0 && !hasWalls)
      ? boycottFactor(geometry.tiltDeg, 0, 1, config.boycott)
      : null;

  const flux = new Float64Array(n + 1);
  let t = 0;
  let steps = 0;
  let nextSample = 1;

  while (nextSample < nSamples) {
    const target = sampleTimes[nextSample];

    let lam = fixedLambda;
    if (lam === null) {
      const top = readInterface();
      const bottom = crossingHeight(zCenters, phi, sedThreshold, fillHeight);
      const gap = meanDiameter(geometry, bottom, Math.max(top, bottom + 1e-6));
      lam = boycottFactor(geometry.tiltDeg, Math.max(top - bottom, 0), gap, {
        ...config.boycott,
        wallProjection: wallProjection(geometry, bottom, top),
        area: area(geometry, top),
      });
    }

    let dt = Math.min(config.sampleIntervalS, target - t);
    const aggEnd = config.aggregationLag ? aggregationFactor(blood, t + dt) : 1;
    dt = Math.min(dt, (config.cfl * gMin) / (u0 * lam * Math.max(aggEnd, 1e-9)));
    if (!(dt > 0)) break;
    const agg = config.aggregationLag ? aggregationFactor(blood, t + 0.5 * dt) : 1;
    const scale = lam * agg;

    for (let i = 1; i < n; i++) {
      const above = phi[i];
      const below = phi[i - 1];
      let q = areas[i] * uFace[i] * scale * Math.min(law.demand(above), law.supply(below));
      const sender = (above * volumes[i]) / dt;
      const room = ((blood.maxPacking - below) * volumes[i - 1]) / dt;
      if (q > sender) q = sender;
      if (q > room) q = room;
      flux[i] = q > 0 ? q : 0;
    }
    for (let i = 1; i < n; i++) {
      const q = flux[i];
      phi[i - 1] += (dt / volumes[i - 1]) * q;
      phi[i] -= (dt / volumes[i]) * q;
    }

    t += dt;
    steps++;
    if (steps > 5e6) throw new Error('step limit exceeded');
    if (t >= target - 1e-12) {
      record(nextSample, lam, agg);
      nextSample++;
    }
  }

  let mass1 = 0;
  for (let i = 0; i < n; i++) mass1 += volumes[i] * phi[i];

  const times = Float64Array.from(sampleTimes);
  const fallMm = new Float64Array(nSamples);
  for (let i = 0; i < nSamples; i++) fallMm[i] = (fillHeight - interfaceH[i]) / MM;

  const finished = (typeof performance !== 'undefined' ? performance : Date).now();
  const wallFactors = new Float64Array(n);
  for (let i = 0; i < n; i++) wallFactors[i] = (uFace[i] + uFace[i + 1]) / (2 * u0);

  return {
    label: geometry.name,
    geometry,
    blood,
    config,
    law,
    wallFactors,
    aggregation,
    times,
    zFaces,
    zCenters,
    volumes,
    phi: phiHistory,
    interface: interfaceH,
    sediment: sedimentH,
    enhancement,
    fallMm,
    fillHeight,
    stokesVelocity: u0,
    massError: mass0 > 0 ? Math.abs(mass1 - mass0) / mass0 : 0,
    steps,
    runtimeMs: finished - started,
  };
}

// ---------------------------------------------------------------- metrics
/** Fall of the boundary [mm] at a given time; NaN past the simulated end. */
export function fallAt(result, seconds) {
  if (seconds > result.times[result.times.length - 1] + 1e-9) return NaN;
  return interpolate(seconds, result.times, result.fallMm);
}

export const esr = (result, hours = 1) => fallAt(result, hours * HOUR);

export const katzIndex = (result) => 0.5 * (esr(result, 1) + 0.5 * esr(result, 2));

/** Steepest slope of the sedimentation curve [mm/h]. */
export function maxSettlingRate(result) {
  let peak = 0;
  for (let i = 1; i < result.times.length; i++) {
    const dt = (result.times[i] - result.times[i - 1]) / HOUR;
    if (dt > 0) peak = Math.max(peak, (result.fallMm[i] - result.fallMm[i - 1]) / dt);
  }
  return peak;
}

export const sedimentMm = (result, index = -1) => {
  const i = index < 0 ? result.sediment.length + index : index;
  return result.sediment[i] / MM;
};

// ------------------------------------------------------------ flow field
/**
 * The flow inside the tube, which the concentration field fixes completely.
 *
 * Nothing enters or leaves a closed tube and both phases are incompressible, so
 * the net volumetric flux through every cross-section is zero:
 *
 *     phi * v_cells = (1 - phi) * v_plasma
 *
 * The solver already has the cell flux f = u_local * psi(phi); dividing by each
 * phase's own volume fraction gives that phase's velocity. The rising plasma is
 * the physical reason a crowded suspension settles so slowly, and why a
 * constriction or a narrow gap throttles the column: it has to get back up.
 */
export function localVelocityScale(result, index) {
  const out = new Float64Array(result.wallFactors.length);
  const scale = result.stokesVelocity * result.enhancement[index] * result.aggregation[index];
  for (let i = 0; i < out.length; i++) out[i] = scale * result.wallFactors[i];
  return out;
}

export function velocityField(result, index) {
  const phi = result.phi[index];
  const scale = localVelocityScale(result, index);
  const cells = new Float64Array(phi.length);
  const plasma = new Float64Array(phi.length);
  for (let i = 0; i < phi.length; i++) {
    const flux = scale[i] * result.law.shape(phi[i]);
    cells[i] = phi[i] > 1e-9 ? flux / phi[i] : 0;
    plasma[i] = phi[i] < 1 - 1e-9 ? flux / (1 - phi[i]) : 0;
  }
  return { cells, plasma };
}

export function velocityFieldMmPerHour(result, index) {
  const { cells, plasma } = velocityField(result, index);
  const factor = HOUR / MM;
  for (let i = 0; i < cells.length; i++) {
    cells[i] *= factor;
    plasma[i] *= factor;
  }
  return { cells, plasma };
}

/** Plasma pushed up through each cross-section [mL/h]. */
export function plasmaThroughput(result, index) {
  const phi = result.phi[index];
  const scale = localVelocityScale(result, index);
  const out = new Float64Array(phi.length);
  for (let i = 0; i < phi.length; i++) {
    out[i] = scale[i] * result.law.shape(phi[i]) * area(result.geometry, result.zCenters[i])
      * 1e6 * HOUR;
  }
  return out;
}

// ------------------------------------------------------------- geometries
export const GEOMETRIES = {
  westergren: () => cylinder(200, 2.5, { name: 'Westergren' }),
  wintrobe: () => cylinder(100, 2.8, { name: 'Wintrobe' }),
  micro: () => cylinder(75, 1.1, { name: 'Capillary' }),
  funnel: () => taper(200, 1.2, 4.0, { name: 'Funnel' }),
  'inverted-funnel': () => taper(200, 4.0, 1.2, { name: 'Inverted' }),
  hourglass: () => hourglass(200, 4.0, 1.2, 0.5, { name: 'Hourglass' }),
  bulb: () => bulb(200, 2.5, 6.0, 0.5, 0.1, { name: 'Bulb' }),
  stepped: () => stepped([[70, 1.5], [60, 3.0], [70, 5.0]], { name: 'Stepped' }),
  annular: () => annularCone(ANNULAR_DEFAULTS.lengthMm, ANNULAR_DEFAULTS.bottomDiameterMm,
                             ANNULAR_DEFAULTS.angleDeg, ANNULAR_DEFAULTS.gapMm,
                             { name: 'Cone in cone' }),
};

export const ANNULAR_DEFAULTS = {
  lengthMm: 120,
  bottomDiameterMm: 8,
  angleDeg: 12,
  gapMm: 1.5,
};

export function getGeometry(name, tiltDeg = 0, options = {}) {
  let geo;
  if (name === 'annular') {
    const p = { ...ANNULAR_DEFAULTS, ...options };
    geo = annularCone(p.lengthMm, p.bottomDiameterMm, p.angleDeg, p.gapMm,
                      { name: 'Cone in cone' });
  } else {
    const factory = GEOMETRIES[name];
    if (!factory) throw new Error(`unknown geometry ${name}`);
    geo = factory();
  }
  geo.tiltDeg = tiltDeg;
  geo.key = name;
  return geo;
}
