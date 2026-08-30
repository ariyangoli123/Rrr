"""Two ways to describe a tube the library does not ship with.

* ``Profile``      -- a measured table of (height, diameter) points.
* ``FunctionTube`` -- any Python function of height.

Writes ``examples/output/custom.png``.
"""

from pathlib import Path

import numpy as np

from bloodsed import BloodProperties, FunctionTube, Profile, SimulationConfig, simulate
from bloodsed.metrics import format_table
from bloodsed.plotting import comparison, save

OUT = Path(__file__).parent / "output"

# a vacuum tube: conical tip, straight barrel, slight shoulder at the neck
measured = Profile(
    heights_mm=[0, 6, 12, 80, 92, 100],
    diameters_mm=[1.0, 4.0, 8.0, 8.0, 6.5, 5.0],
    name="vacuum tube (measured)",
)

# a corrugated tube -- a sinusoidal bore, to show an analytic profile
corrugated = FunctionTube(
    100,
    lambda z_mm: 4.0 + 1.6 * np.sin(2 * np.pi * z_mm / 20.0),
    name="corrugated bore",
)

blood = BloodProperties(hematocrit=0.42, aggregate_diameter_um=80.0)
config = SimulationConfig(duration_h=2.0, n_cells=500)

results = [simulate(tube, blood, config, label=tube.name)
           for tube in (measured, corrugated)]
print(format_table(results))
print("wrote", save(comparison(results), OUT / "custom.png"))
