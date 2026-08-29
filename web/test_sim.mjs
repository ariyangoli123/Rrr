/**
 * Check the JavaScript solver against reference values from the Python model.
 *
 *   python3 web/make_fixtures.py   # regenerate the reference values
 *   node web/test_sim.mjs          # compare
 *
 * The two implementations solve the same equations with the same scheme, so
 * upright cases have to agree to a few parts in 100 000.  Tilted cases get a
 * looser bound: the Boycott factor is recomputed from quadratures whose grids
 * differ slightly between the two ports, and it is a calibrated approximation
 * to begin with.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  BLOOD_PRESETS, annularCone, area, cellVolumes, cylinder, esr, getGeometry,
  hydraulicDiameter, innerRadius, makeBlood, makeFluxLaw, radius, sedimentMm,
  simulate, stepped, stokesVelocity, taper, tubeVolume, velocityField,
  velocityFieldMmPerHour, wallFactor, wallProjection,
} from './sim.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(readFileSync(join(here, 'fixtures.json'), 'utf8'));

let failures = 0;
let checks = 0;

function check(name, actual, expected, tolerance) {
  checks++;
  const spread = Math.abs(actual - expected) / Math.max(Math.abs(expected), 1e-12);
  const ok = spread <= tolerance;
  if (!ok) {
    failures++;
    console.log(`  FAIL ${name}: got ${actual}, expected ${expected} (off by ${(spread * 100).toFixed(4)} %)`);
  }
  return ok;
}

function assert(name, condition) {
  checks++;
  if (!condition) {
    failures++;
    console.log(`  FAIL ${name}`);
  }
}

// -- agreement with the Python model ----------------------------------
console.log(`comparing ${fixtures.cases.length} cases against the Python model`);
const ANNULAR_FIXTURES = {
  'annular-straight': [120, 8.0, 0.0, 1.5],
  'annular-cone': [120, 8.0, 12.0, 1.5],
  'annular-steep': [120, 8.0, 30.0, 1.5],
  'annular-narrow': [120, 8.0, 12.0, 0.6],
};

for (const item of fixtures.cases) {
  const spec = ANNULAR_FIXTURES[item.geometry];
  const geometry = spec
    ? annularCone(...spec, { tiltDeg: item.tilt_deg, name: item.geometry })
    : getGeometry(item.geometry, item.tilt_deg);
  const result = simulate(geometry, BLOOD_PRESETS[item.blood], {
    durationH: fixtures.duration_h,
    nCells: fixtures.n_cells,
  });
  const tolerance = item.tilt_deg > 0 ? 5e-3 : 1e-4;
  const label = `${item.geometry}/${item.blood}/tilt${item.tilt_deg}`;
  check(`${label} ESR 1 h`, esr(result, 1), item.esr_1h, tolerance);
  check(`${label} ESR 2 h`, esr(result, 2), item.esr_2h, tolerance);
  check(`${label} sediment`, sedimentMm(result), item.sediment_mm, Math.max(tolerance, 2e-3));
  assert(`${label} conserves cell volume`, result.massError < 1e-12);

  const flow = velocityFieldMmPerHour(result, 60);
  check(`${label} fastest cells`, Math.max(...flow.cells), item.cells_max_mm_per_h, tolerance);
  check(`${label} fastest plasma`, Math.max(...flow.plasma), item.plasma_max_mm_per_h, tolerance);
  check(`${label} enhancement`, result.enhancement[60], item.enhancement, tolerance);
}

// -- closed-form checks, independent of the fixtures -------------------
const ideal = { aggregationTimeS: 0 };
const idealConfig = { durationH: 1, nCells: 400, wallCorrection: false, aggregationLag: false };
const blood = makeBlood(ideal);
const u0mmPerHour = (stokesVelocity(blood) / 1e-3) * 3600;

const kynch = simulate(cylinder(200, 2.5), ideal, idealConfig);
check('Kynch shock speed', esr(kynch, 1), u0mmPerHour * (1 - 0.45 / 0.9) ** 4.65, 1e-5);

const free = simulate(cylinder(200, 2.5), ideal, { ...idealConfig, fluxLaw: 'free', durationH: 0.25 });
check('free settling velocity', esr(free, 0.25), 0.25 * u0mmPerHour, 2e-2);

const capped = simulate(cylinder(200, 2.5), ideal, { ...idealConfig, fluxLaw: 'free' });
check('free settling stops at the sediment', esr(capped, 1), 200 * (1 - 0.45 / 0.9), 1e-3);

for (const n of [200, 400, 800]) {
  const meshed = simulate(cylinder(200, 2.5), ideal, { ...idealConfig, nCells: n });
  check(`mesh independence (${n} cells)`, esr(meshed, 1), esr(kynch, 1), 1e-5);
}

// -- invariants --------------------------------------------------------
for (const name of ['westergren', 'funnel', 'inverted-funnel', 'hourglass', 'stepped', 'bulb']) {
  const result = simulate(getGeometry(name), {}, { durationH: 3, nCells: 300 });
  let lo = Infinity;
  let hi = -Infinity;
  for (const profile of result.phi) {
    for (const value of profile) {
      if (value < lo) lo = value;
      if (value > hi) hi = value;
    }
  }
  assert(`${name} keeps phi >= 0`, lo >= -1e-12);
  assert(`${name} keeps phi <= phi_max`, hi <= 0.9 + 1e-12);
  assert(`${name} conserves cell volume`, result.massError < 1e-12);
}

// -- geometry --------------------------------------------------------
const frustum = taper(200, 1.2, 4.0);
const r0 = 0.6e-3;
const r1 = 2.0e-3;
check('frustum volume', tubeVolume(frustum),
  (Math.PI * 0.2 / 3) * (r0 * r0 + r0 * r1 + r1 * r1), 1e-6);

const stepTube = stepped([[50, 1.0], [50, 3.0]]);
check('stepped volume', tubeVolume(stepTube),
  Math.PI * ((0.5e-3) ** 2 * 0.05 + (1.5e-3) ** 2 * 0.05), 1e-6);

const faces = new Float64Array(301);
for (let i = 0; i <= 300; i++) faces[i] = (stepTube.length * i) / 300;
check('cell volumes sum to the tube volume',
  cellVolumes(stepTube, faces).reduce((a, b) => a + b, 0), tubeVolume(stepTube), 1e-9);

// -- flux law --------------------------------------------------------
const law = makeFluxLaw('hindered-packing', 4.65, 0.9);
check('flux vanishes when packed', law.shape(0.9), 0, 1e-12);
check('packed cells accept nothing', law.supply(0.9), 0, 1e-12);
check('hindered velocity at Hct 45 %', law.shape(0.45) / 0.45, (1 - 0.45 / 0.9) ** 4.65, 1e-12);
assert('wall drag grows as the bore narrows',
  wallFactor(60e-6, 10e-3) > wallFactor(60e-6, 2.5e-3) &&
  wallFactor(60e-6, 2.5e-3) > wallFactor(60e-6, 0.3e-3));

// -- the annular settler ----------------------------------------------
const annulus = annularCone(100, 12, 30, 2);
check('annular flow area', area(annulus, 0.05),
  Math.PI * (radius(annulus, 0.05) ** 2 - innerRadius(annulus, 0.05) ** 2), 1e-12);
check('gap measured perpendicular to the wall',
  radius(annulus, 0.03) - innerRadius(annulus, 0.03),
  (2 * 1e-3) / Math.cos((30 * Math.PI) / 180), 1e-9);
check('hydraulic diameter is twice the gap',
  hydraulicDiameter(annularCone(100, 12, 0, 2), 0.05), 2 * 2e-3, 1e-9);
check('a plain tube reports its bore', hydraulicDiameter(cylinder(100, 2.5), 0.05), 2.5e-3, 1e-12);
check('a straight tube has no inclined wall', wallProjection(cylinder(200, 2.5), 0, 0.2), 0, 1e-12);
check('a cone projects the circle it opens out to',
  wallProjection(taper(200, 1.2, 4.0), 0, 0.2),
  Math.PI * ((2e-3) ** 2 - (0.6e-3) ** 2), 2e-3);

assert('cones that would meet are rejected', (() => {
  try { annularCone(100, 10, 8, 1, { innerAngleDeg: 20 }); return false; } catch { return true; }
})());

const idealAnn = { aggregationTimeS: 0 };
const annConfig = { durationH: 1, nCells: 300, aggregationLag: false };
const byAngle = [0, 6, 12, 25].map((a) =>
  esr(simulate(annularCone(120, 8, a, 1.5), idealAnn, annConfig), 1));
assert('a steeper cone settles faster', byAngle.every((v, i) => i === 0 || v > byAngle[i - 1]));
assert('inclining the walls more than doubles the reading', byAngle[3] > 2 * byAngle[0]);

const byGap = [3, 1.5, 0.6].map((g) =>
  esr(simulate(annularCone(120, 8, 12, g), idealAnn, annConfig), 1));
assert('a narrower gap clears plasma faster', byGap.every((v, i) => i === 0 || v > byGap[i - 1]));

const wallsOff = esr(simulate(annularCone(120, 8, 12, 1.5), idealAnn,
  { ...annConfig, boycott: { walls: false } }), 1);
assert('the wall term can be switched off', byGap[1] > 1.5 * wallsOff);
check('switching walls off leaves a straight tube alone',
  esr(simulate(cylinder(200, 2.5), idealAnn, { ...annConfig, boycott: { walls: false } }), 1),
  esr(simulate(cylinder(200, 2.5), idealAnn, annConfig), 1), 1e-12);

// -- the flow field ---------------------------------------------------
const flowRun = simulate(cylinder(200, 2.5), {}, { durationH: 2, nCells: 300 });
const { cells: vCells, plasma: vPlasma } = velocityField(flowRun, 30);
const phiAt = flowRun.phi[30];
let worstBalance = 0;
for (let i = 0; i < phiAt.length; i++) {
  worstBalance = Math.max(worstBalance,
    Math.abs(phiAt[i] * vCells[i] - (1 - phiAt[i]) * vPlasma[i]));
}
assert('the two phases balance exactly', worstBalance < 1e-15);
assert('cells fall and plasma rises', vCells.every((v) => v >= 0) && vPlasma.every((v) => v >= 0));

const late = velocityField(flowRun, 60);
let stillAbove = 0;
for (let i = 0; i < flowRun.zCenters.length; i++) {
  if (flowRun.zCenters[i] > flowRun.interface[60] + 5e-3) {
    stillAbove = Math.max(stillAbove, late.cells[i], late.plasma[i]);
  }
}
assert('nothing moves in the clear plasma', stillAbove < 1e-12);

console.log(`\n${checks - failures}/${checks} checks passed`);
if (failures) {
  console.error(`${failures} FAILED`);
  process.exit(1);
}
