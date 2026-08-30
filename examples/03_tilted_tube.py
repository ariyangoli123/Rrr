"""The Boycott effect: why a tilted tube reports a falsely high ESR.

A 3 degree tilt -- easy to miss on a bench -- is enough to change the result.
"""

from bloodsed import BloodProperties, Cylinder, SimulationConfig, simulate

blood = BloodProperties(hematocrit=0.45)
config = SimulationConfig(duration_h=1.0)

baseline = None
for tilt in (0.0, 1.0, 3.0, 5.0, 10.0, 30.0):
    tube = Cylinder(200, 2.5, tilt_deg=tilt, name=f"tilt {tilt:g} deg")
    result = simulate(tube, blood, config)
    baseline = baseline or result.esr(1.0)
    print(f"tilt {tilt:5.1f} deg   ESR {result.esr(1.0):5.1f} mm   "
          f"({result.esr(1.0) / baseline:4.2f}x upright)")
