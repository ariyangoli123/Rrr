"""Unit helpers.

Everything inside the solver is SI (metre, second, kilogram).  Public
constructors take the units that people actually use for blood tubes
(millimetre, micrometre, hour), so the conversion happens once, at the edge.
"""

from __future__ import annotations

#: metres in a millimetre
MM = 1.0e-3
#: metres in a micrometre
UM = 1.0e-6
#: seconds in a minute
MINUTE = 60.0
#: seconds in an hour
HOUR = 3600.0

#: standard gravity [m/s^2]
GRAVITY = 9.80665


def mm(value: float) -> float:
    """Millimetres -> metres."""
    return value * MM


def to_mm(value: float) -> float:
    """Metres -> millimetres."""
    return value / MM


def um(value: float) -> float:
    """Micrometres -> metres."""
    return value * UM


def hours(value: float) -> float:
    """Hours -> seconds."""
    return value * HOUR


def to_hours(value: float) -> float:
    """Seconds -> hours."""
    return value / HOUR


def mm_per_hour(value_si: float) -> float:
    """Velocity in m/s -> mm/h (the clinical unit for ESR)."""
    return value_si / MM * HOUR
