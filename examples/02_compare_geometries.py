"""The headline experiment: the same blood in six different tubes.

Writes ``examples/output/comparison.png``.
"""

from pathlib import Path

from bloodsed import BloodProperties, SimulationConfig, get_geometry, simulate
from bloodsed.metrics import format_table
from bloodsed.plotting import comparison, save

OUT = Path(__file__).parent / "output"
NAMES = ["westergren", "funnel", "inverted-funnel", "hourglass", "bulb", "stepped"]

blood = BloodProperties(hematocrit=0.45)
config = SimulationConfig(duration_h=2.0, n_cells=600)

results = []
for name in NAMES:
    tube = get_geometry(name)
    print(f"simulating {tube.describe()}")
    results.append(simulate(tube, blood, config, label=tube.name))

print()
print(format_table(results))
print()
print("wrote", save(comparison(results), OUT / "comparison.png"))
