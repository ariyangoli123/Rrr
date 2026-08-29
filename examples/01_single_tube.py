"""Simulate one Westergren tube and print the clinical read-outs."""

from bloodsed import BloodProperties, SimulationConfig, get_geometry, simulate
from bloodsed.metrics import katz_index, lag_time_min, max_settling_rate

blood = BloodProperties(hematocrit=0.45, aggregate_diameter_um=60.0)
tube = get_geometry("westergren")
result = simulate(tube, blood, SimulationConfig(duration_h=2.0))

print(tube.describe())
print(blood.describe())
print()
print(f"ESR at 1 h        {result.esr(1.0):6.1f} mm")
print(f"ESR at 2 h        {result.esr(2.0):6.1f} mm")
print(f"Katz index        {katz_index(result):6.1f} mm")
print(f"peak rate         {max_settling_rate(result):6.1f} mm/h")
print(f"lag phase         {lag_time_min(result):6.1f} min")
print(f"sediment height   {result.sediment_mm[-1]:6.1f} mm")
print(f"cell volume drift {result.mass_error:6.1e} (should be ~0)")
