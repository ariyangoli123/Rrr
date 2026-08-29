"""Dump reference results from the Python model for the JavaScript port to match.

Run from the repository root:  python3 web/make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

from bloodsed.blood import BloodProperties, get_blood
from bloodsed.geometry import get_geometry
from bloodsed.metrics import sediment_height_mm
from bloodsed.solver import SimulationConfig, simulate

CASES = [
    ("westergren", "normal", 0.0),
    ("wintrobe", "normal", 0.0),
    ("micro", "normal", 0.0),
    ("funnel", "normal", 0.0),
    ("inverted-funnel", "normal", 0.0),
    ("hourglass", "normal", 0.0),
    ("bulb", "normal", 0.0),
    ("stepped", "normal", 0.0),
    ("westergren", "anemic", 0.0),
    ("westergren", "inflammation", 0.0),
    ("westergren", "polycythemic", 0.0),
    ("westergren", "severe-inflammation", 0.0),
    ("westergren", "normal", 3.0),
    ("westergren", "normal", 15.0),
    ("hourglass", "inflammation", 15.0),
]

N_CELLS = 400
DURATION_H = 2.0


def main() -> None:
    fixtures = []
    for geometry_name, blood_name, tilt in CASES:
        geometry = get_geometry(geometry_name)
        geometry.tilt_deg = tilt
        blood = get_blood(blood_name)
        result = simulate(geometry, blood,
                          SimulationConfig(duration_h=DURATION_H, n_cells=N_CELLS))
        fixtures.append({
            "geometry": geometry_name,
            "blood": blood_name,
            "tilt_deg": tilt,
            "esr_1h": result.esr(1.0),
            "esr_2h": result.esr(2.0),
            "sediment_mm": sediment_height_mm(result),
            "mass_error": result.mass_error,
        })
        print(f"{geometry_name:16s} {blood_name:20s} tilt {tilt:4.1f} -> "
              f"{result.esr(1.0):7.3f} / {result.esr(2.0):7.3f} mm")

    payload = {
        "generated_by": "web/make_fixtures.py",
        "n_cells": N_CELLS,
        "duration_h": DURATION_H,
        "cases": fixtures,
    }
    path = Path(__file__).with_name("fixtures.json")
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
