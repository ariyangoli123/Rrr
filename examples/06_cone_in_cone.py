"""A cone standing inside another cone, with the blood filling the gap.

This is a settling tube built as a lamella (inclined plate) settler.  Every wall
is inclined, so a cell falls only the width of the gap before landing on the
outer cone and sliding down it, while clear plasma is released under the inner
cone and rises.  That is the Boycott effect with the tube standing perfectly
upright -- and both knobs that control it, the cone angle and the gap, are
things you can build rather than mistakes you can make.

Writes ``examples/output/cone_in_cone.png`` and ``.../cone_flow.png``.
"""

from pathlib import Path

import numpy as np

from bloodsed import AnnularCone, BloodProperties, Cylinder, SimulationConfig, simulate
from bloodsed.flows import peak_velocities
from bloodsed.metrics import format_table
from bloodsed.plotting import comparison, flow_report, save

OUT = Path(__file__).parent / "output"

blood = BloodProperties(hematocrit=0.45)
config = SimulationConfig(duration_h=2.0, n_cells=400)

tubes = [
    Cylinder(120, 8.0, name="plain tube"),
    AnnularCone(120, 8.0, 0.0, 1.5, name="annulus, no incline"),
    AnnularCone(120, 8.0, 12.0, 1.5, name="12° cone, 1.5 mm gap"),
    AnnularCone(120, 8.0, 30.0, 1.5, name="30° cone, 1.5 mm gap"),
    AnnularCone(120, 8.0, 12.0, 0.6, name="12° cone, 0.6 mm gap"),
]

results = []
for tube in tubes:
    print(f"simulating {tube.describe()}")
    results.append(simulate(tube, blood, config, label=tube.name))

print()
print(format_table(results))
print()
print("A vertical annulus has no inclined surface, so it settles like a plain tube.")
print("Opening the cones out, or narrowing the gap, turns every wall into a")
print("settling surface -- which is exactly what a lamella settler is for.")
print()

for result in results[2:]:
    index = int(np.argmin(np.abs(result.times - 3600.0)))
    flow = peak_velocities(result, index)
    print(f"{result.label:24s} plasma rises at {flow['plasma_max_mm_per_h']:5.1f} mm/h, "
          f"enhancement x{flow['enhancement']:.2f}")

print()
print("wrote", save(comparison(results, title="A cone inside a cone"),
                    OUT / "cone_in_cone.png"))
best = results[-1]
print("wrote", save(flow_report(best, int(np.argmin(np.abs(best.times - 3600.0)))),
                    OUT / "cone_flow.png"))
