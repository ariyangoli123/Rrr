# esrsim

Design-analysis toolkit for an accelerated ESR device: a thin conical annular gap
between two coaxial upward-pointing cones, operating vertically, exploiting the Boycott
effect.

---

## What this tool cannot do

**It is not a first-principles simulator and must never be used as one.**

Whole-blood sedimentation is gel collapse. Modelling it from first principles needs two
material functions — the compressive yield stress `Py(φ)` and the hydraulic resistance
`R(φ)` — and **neither has been measured for whole blood anywhere in the world
literature.** Any code that produces a confident sedimentation prediction from geometry
alone is producing fiction, and is worse than no code.

So this package:

- ❌ **does not predict** the ESR of an unknown sample
- ❌ **does not simulate mixing** (no two-phase CFD; it is not credible at this scale)
- ❌ **does not replace experimental calibration**

What it does:

- ✅ solves the **geometry exactly**, from first principles
- ✅ reproduces the **kinetics** with a phenomenological model calibrated on **n = 1**
- ✅ evaluates capillarity and mixing as **threshold tests**, not simulations
- ✅ keeps every **unknown explicit and traceable**

**The entire experimental dataset is one blood sample**, measured in three tubes in one
session, and two of its three predictors are collinear at r = 0.986. Every kinetics
output in this package says so on every row.

---

## Provenance tiers

Every public function returns a `Result` carrying exactly one tier. There is no quiet
mode and no way to suppress them.

| Tier | Meaning |
|---|---|
| `EXACT` | From geometry or a definition. No fitted parameter. |
| `CALIBRATED` | Fitted to this project's data, **inside** its fitted range. |
| `EXTRAPOLATED` | A `CALIBRATED` result used outside that range. Assigned automatically. |
| `ESTIMATED` | From literature or assumed. Not measured here. |
| `HYPOTHESIS` | A competing model with no winner selected by evidence. |
| `RESEARCH_ONLY` | Rides on a material function nobody has measured. Paper only. |
| `UNKNOWN` | **Cannot be computed. Carries no number — ever.** |

Four rules are enforced structurally, not by comment:

1. An `UNKNOWN` result has `value is None` plus a written reason **and** the specific
   experiment that would make it computable. Constructing one without both raises.
2. Tiers propagate: a composite takes the **weakest** tier of its inputs.
3. A `CALIBRATED` result evaluated outside its fitted range auto-retags to
   `EXTRAPOLATED` and carries `EXTRAPOLATION_UNSAFE`.
4. `tests/test_integrity.py` walks every public callable in the package and fails if any
   can return an untagged value.

```python
>>> from esrsim.core import geometry, kinetics
>>> cone = geometry.from_library("T070")
>>> kinetics.E_empirical(20.0)          # above the 16° limit of spec §5.2
Result(name='E_empirical', ..., tier=<Tier.EXTRAPOLATED>, flags=('N_EQUALS_1',
'COLLINEARITY_WARNING', 'EXTRAPOLATION_UNSAFE'), ...)
```

---

## Install

```bash
pip install -e ".[dev]"
pytest            # 341 passed, 4 skipped — 95% coverage
```

Python 3.11+. Depends on numpy, scipy, pydantic and pyyaml — that is all. Charts are
hand-written inline SVG, so reports open offline with no CDN and no plotting library. No
CFD or FEM dependency.

---

## Interactive app

```bash
esrsim serve          # opens http://127.0.0.1:8000 in your browser
```

A local app with a tube selector, live sliders for gap, volume, column length,
haematocrit, φ_pack, ESR and readout time, and twelve views: tube comparison, geometry,
charts, kinetics, capillary and mixing, readout and error budget, ICSH feasibility,
benchmark, design rules, parameter sweeps, the continuum research layer, and the open
questions register.

**The browser does no physics.** Every number is computed by the same Python functions
the CLI and the test suite use, serialised with its tier, flags, notes and — for
`UNKNOWN` — the reason and the experiment that closes it. Re-implementing the models in
JavaScript would give two implementations of one calculation, and for a package whose
whole premise is traceability, two answers for one number is the failure mode.

Built on the standard library's `http.server`: no Flask, no FastAPI, no CDN. It binds to
loopback only and works with the network cable unplugged. The JavaScript renderer
throws rather than draw a row that arrives without a tier.

---

## CLI

```bash
esrsim geometry T070 --report --step 0.30      # exact cone algebra, both generations
esrsim kinetics --tube T070 --esr 45 --hct 0.42
esrsim kinetics --decisive                     # the collinearity-breaking experiment
esrsim readout --tube T070 --mode time_to_threshold
esrsim validate --feasibility --tube T090      # can the ICSH 2017 study be run?
esrsim benchmark --tube T070                   # vs Westergren AND a tilted plain tube
esrsim explore --sweep gap --range 0.5 1.5 --steps 11
esrsim explore --compare T090 T070 T060 TAPER
esrsim report --tube T070 --html report.html
esrsim rules T070 --step 0.30 --upper-angle-offset -2
esrsim export T070 --json                      # DRIVING dimensions for CAD
esrsim unknowns                                # the honest core
esrsim serve                                   # the interactive app
```

---

## The three functions that matter most

### `feasibility_check()` — can the study actually be run?

```
ICSH 2017 FEASIBILITY — T090, fixed-time 15 min  [UNKNOWN]
range_ceiling                             32.23 mm     ESTIMATED
distinguishable_levels_top_tertile            1 levels EXACT
icsh_2017_feasible                           no        EXACT      [ICSH_2017_INFEASIBLE]
esr_above_top_tertile                         —        UNKNOWN
    ! With a range ceiling of 32.23 mm and this readout, ESR 81 and ESR 120 both sit
      at or near the ceiling. The study cannot be run as designed.
```

**Every tube in the library fails this**, and that is the correct answer, not a bug. A
fixed-time readout cannot fill the ICSH 2017 top tertile because ESR 60 and ESR 120 give
the same reading. Twenty samples that read alike are one measurement repeated twenty
times.

### `detect_non_monotonic()` — refuse an uninvertible readout

The Δh-over-a-window readout fails: over 12→15 min it gives ≈4.2 mm for **both** ESR 2
and ESR 27. Silent non-monotonicity is more dangerous than saturation because it is
invisible in the output, so the mode is implemented **only to demonstrate its failure**,
and `accept_readout()` returns `UNKNOWN` for it rather than a number.

### `open_questions()` — every unresolved question, and the experiment that closes it

Twelve registered unknowns and four registered data gaps, each naming its resolution
path. Printed at the foot of every report.

---

## What the tool found

Reproduced exactly from the spec's own validation tables (see `tests/`):

- all six tubes' published dimensions, to 4 decimal places, from the construction rule
- `E_PNK` = 10.6 / 16.5 / 23.9 and `E_saturated` = 3.06 / 3.58 / 4.26
- collinearity r = 0.986, range ceiling 32.2 mm, saturation first biting at ESR 55
- the plain-tube benchmark: D = 7.14 mm, L/D = 7.01, ratios 1.43 / 1.50 / 1.35

Findings the tool surfaces rather than smooths over:

- **The published `clearance` column is internally inconsistent** with `d/cos θ` by up
  to 2×10⁻⁴ mm. Every other column reproduces inside 5×10⁻⁵.
- **`r = A(bl)/A(interface)` is 0.4–0.6 only at mid-column.** Near the range ceiling it
  falls to 0.18–0.31, so addendum §E's "both reading methods cost about half the level
  shift" does not hold at depth — reading from a fixed mark becomes the better of the two.
- **The descent model disagrees with the recorded `dh/dESR`** (U10). The record falls
  monotonically 0.83 → 0.28; the model rises then falls. They agree only near ESR 30.
  Reported side by side and flagged, **not** tuned onto the record.
- **The model puts peak E near minute 45, the record near minute 15** (U09).
- **Design rule R02's own threshold (0.72 mm) lies inside its own unresolved band**
  (0.69–0.77). It can therefore never return a confident pass, including for T070 — the
  design that passed mixing experimentally.

---

## Layout

```
esrsim/
├── tiers.py            # the tier ladder and Result — the structural guarantee
├── units.py            # SI internally, millimetres at the boundary, stated once
├── registry.py         # YAML loaders, unknowns register, open_questions()
├── core/
│   ├── geometry.py     # EXACT cone algebra, both generations, range ceiling
│   ├── fluid.py        # fluid properties, each carrying its own tier
│   ├── capillary.py    # HYPOTHESIS: two unevenness models, no winner picked
│   ├── kinetics.py     # CALIBRATED on n = 1: three E models, always together
│   ├── continuum.py    # RESEARCH_ONLY: Λ, and Py/R registered as unmeasurable
│   ├── readout.py      # EXACT: monotonicity screen and error budget
│   └── benchmark.py    # against Westergren AND a tilted plain tube
├── calibration/        # ingest conventions, Passing-Bablok, Bland-Altman, ICSH
├── design/             # rules R01-R10, sweeps, DRIVING/DERIVED drawing sheet
├── data/               # tubes, fluids, calibration constants, unknowns, gaps
├── report.py           # text and standalone HTML, linear real axes
└── cli.py
```

---

## Data-layer conventions (enforced by rejection, not warning)

- `t = 0` is **tube placement**, not the moment the boundary becomes readable
- **empty means not measured — never zero**
- lap entries under 5 s are rejected as phantom double-taps
- mandatory fields rejected on absence: Hct, temperature, draw time, operator, tube id,
  sample age, `rest_before_mixing_s`, fill method
- sample age over 240 min is **rejected**, not warned about
- **no smoothing**, ever; three-parameter logistic only
- the acceptance criterion is **5 mm** (ICSH 2011, Jou et al.). Passing `6.0` raises,
  with an explanation that it belongs to EN ISO 13079:2011 Annex B, tube contamination
  testing — not method comparison.

---

## Known gaps in this repository

Four tests are **gated** because the data they need is not here. They skip with a loud,
registered reason naming exactly what would close them, and
`test_missing_experimental_data_is_declared` fails if any gate loses its entry. Nothing
skips silently and nothing was invented to make a suite go green.

| id | missing | closes with |
|---|---|---|
| M01 | sample 1's raw chronometer trace | commit `data/samples/sample_001.csv` |
| M02 | sample 1's final packed height | add `final_height_mm` to `measured/sample_001.yaml` |
| M03 | per-tube haze durations | add `haze_min` per tube |
| M04 | the plain-tube control run | run a 7.14 × 50 mm tube at 0°, 10°, 20° |

M04 is the one that matters most: addendum §D calls it *"the most important unmeasured
quantity in the project."* Until it exists, every benchmark ratio compares a measurement
on one side with a PNK theory on the other, and the package says so on every such row.

---

## References

Jou et al., ICSH review, Int J Lab Hematol 2011;33(2):125–132 · Kratz et al., ICSH 2017,
Int J Lab Hematol 2017;39(5):448–457 · CLSI H02-A5 (2011) · EN ISO 13079:2011 · Acrivos
& Herbolzheimer, J Fluid Mech 1979;92:435–457 · Herbolzheimer & Acrivos, J Fluid Mech
1981;108:485–499 · Borhan & Acrivos, Phys Fluids 1988;31:3488 · Dobashi et al.,
Biorheology 1987–1994 · Hocking & O'Brien, Biorheology 1987;24(5):473–482 · Kynch, Trans
Faraday Soc 1952 · Darras et al., PRL 2022 · Dasanna et al., PRE 2022 · John et al.,
PNAS Nexus 2024 · Anestis 1981 · Bürger, Damasceno & Karlsen 2004 · Reyes, Arratia &
Ihle, arXiv:2412.20284 (2025)
