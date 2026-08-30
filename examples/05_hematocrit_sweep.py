"""Why the ESR is corrected for anemia.

Sweeping the hematocrit shows how strongly a thin sample inflates the reading,
even though nothing about the cells themselves has changed.  This is the whole
motivation for the Katz index.

Writes ``examples/output/hematocrit.png``.
"""

from pathlib import Path

from bloodsed import BloodProperties, SimulationConfig, get_geometry, simulate
from bloodsed.plotting import save, sweep_figure

OUT = Path(__file__).parent / "output"
VALUES = [0.20, 0.30, 0.40, 0.45, 0.50, 0.60]

tube = get_geometry("westergren")
config = SimulationConfig(duration_h=2.0)

results = []
for hematocrit in VALUES:
    blood = BloodProperties(hematocrit=hematocrit)
    results.append(simulate(tube, blood, config, label=f"Hct {hematocrit:.0%}"))
    print(f"Hct {hematocrit:.0%}  ESR {results[-1].esr(1.0):6.1f} mm")

print("wrote", save(sweep_figure(results, VALUES, "hematocrit"), OUT / "hematocrit.png"))
