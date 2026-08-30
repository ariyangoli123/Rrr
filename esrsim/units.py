"""The unit convention, stated once and obeyed everywhere.

Build prompt: *"Geometry and physics in SI internally, millimetres at the boundary,
with the unit convention documented once and obeyed everywhere."*

The convention
--------------
**Boundary (everything a caller passes in or reads out of a public function):**

===================  ======================================================
length               millimetre (mm)
area                 square millimetre (mm^2)
volume               cubic millimetre (mm^3); 1000 mm^3 == 1 mL == 1 cc
angle                degrees for user-facing arguments named ``*_deg``;
                     radians only for arguments named ``*_rad``
time                 minute (min) for ESR kinetics, second (s) for ingest laps
ESR                  millimetre per hour (mm/h), Westergren-equivalent
velocity             millimetre per minute (mm/min)
haematocrit          volume fraction in [0, 1], never percent
===================  ======================================================

**Internal (any physics that mixes mass, length and time):** strict SI —
metre, kilogram, second, pascal, newton per metre. Convert on the way in with
:func:`mm_to_m` and friends, convert back on the way out.

Why: surface tension, density and yield stress only compose correctly in SI. A
capillary length computed as ``sqrt(sigma/(rho*g))`` with sigma in N/m and rho in
kg/m^3 comes out in metres; returning that as "2.32" without the conversion is the
classic sign-and-unit error the spec's regression tests exist to catch.

Every :class:`~esrsim.tiers.Result` carries its unit string explicitly, so a report
never has to guess.
"""

from __future__ import annotations

import math

__all__ = [
    "G",
    "MM_PER_M",
    "MM3_PER_ML",
    "mm_to_m",
    "m_to_mm",
    "mm2_to_m2",
    "mm3_to_m3",
    "m3_to_mm3",
    "deg_to_rad",
    "rad_to_deg",
    "mm_per_hour_to_mm_per_min",
    "minutes_to_seconds",
    "check_positive",
    "check_fraction",
    "check_angle_deg",
]

#: Standard gravity, m/s^2. The project record uses 9.81 throughout (spec §C
#: pressure table reproduces only with 9.81, not 9.80665), so 9.81 it is.
G: float = 9.81

MM_PER_M: float = 1000.0
MM3_PER_ML: float = 1000.0


def mm_to_m(x: float) -> float:
    """millimetre -> metre."""
    return x / MM_PER_M


def m_to_mm(x: float) -> float:
    """metre -> millimetre."""
    return x * MM_PER_M


def mm2_to_m2(x: float) -> float:
    """square millimetre -> square metre."""
    return x / MM_PER_M**2


def mm3_to_m3(x: float) -> float:
    """cubic millimetre -> cubic metre."""
    return x / MM_PER_M**3


def m3_to_mm3(x: float) -> float:
    """cubic metre -> cubic millimetre."""
    return x * MM_PER_M**3


def deg_to_rad(x: float) -> float:
    return math.radians(x)


def rad_to_deg(x: float) -> float:
    return math.degrees(x)


def mm_per_hour_to_mm_per_min(x: float) -> float:
    """ESR (mm/h) -> settling velocity (mm/min)."""
    return x / 60.0


def minutes_to_seconds(x: float) -> float:
    return x * 60.0


# --------------------------------------------------------------------- guards


def check_positive(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    return value


def check_fraction(name: str, value: float) -> float:
    if not math.isfinite(value) or not (0.0 <= value <= 1.0):
        raise ValueError(
            f"{name} must be a volume fraction in [0, 1], got {value!r} "
            "(haematocrit is never a percentage here)"
        )
    return value


def check_angle_deg(name: str, value: float) -> float:
    if not math.isfinite(value) or not (0.0 < value < 90.0):
        raise ValueError(f"{name} must be a half-angle in (0, 90) degrees, got {value!r}")
    return value
