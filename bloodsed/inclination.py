"""The Boycott effect: why a tilted tube reads a falsely high ESR.

Boycott (1920) noticed that blood settles faster in an inclined tube.  The
reason is geometric: cells settling vertically clear a layer along the raised
wall, that clear plasma runs up the tube, and the sediment slides down the
lowered wall.  The clear fluid is produced over the *horizontal projection* of
the tube rather than just its cross-section.

Ponder (1925) and Nakamura & Kuroda (1937) turned that into the "PNK theory":
for a suspension column of length ``L`` in a tube of gap ``d`` tilted by
``theta`` from vertical, clear fluid appears at a rate proportional to

    A * cos(theta) + d * L * sin(theta)

so, relative to the same tube standing upright, the interface moves along the
tube axis faster by

    Lambda = cos(theta) + eta * (4 * L * sin(theta)) / (pi * d)

The ``4/pi`` converts the projected side area of a circular tube to an
equivalent multiple of its cross-section.  ``eta`` is an efficiency factor:
pure PNK (``eta = 1``) is derived for a wide gap where the clear layer drains
instantly, and it badly overpredicts narrow bore tubes, where the returning
plasma has to squeeze through a millimetre-scale channel.  The default
``eta = 0.08`` reproduces the clinical rule of thumb that a 3 degree tilt of a
Westergren tube inflates the ESR by roughly 30 %.  Set ``efficiency=1.0`` for
the textbook formula.

This is a 1-D surrogate for a genuinely 2-D flow, so treat tilted results as
"how much faster, roughly" rather than as a prediction to three digits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BoycottModel:
    """Axial settling enhancement of a tilted tube.

    Attributes
    ----------
    model:
        ``"pnk"`` for the projected-area theory, ``"none"`` to keep the tube
        upright in the physics (a tilt then only reduces gravity by cos theta),
        or ``"constant"`` to force a user supplied factor.
    efficiency:
        The ``eta`` above.  1.0 is textbook PNK; the default is calibrated to
        narrow clinical tubes.
    max_enhancement:
        Hard cap on ``Lambda``.  Keeps a near-horizontal tube from producing a
        nonsense settling velocity, and keeps the time step sane.
    constant_factor:
        Used when ``model == "constant"``.
    """

    model: str = "pnk"
    efficiency: float = 0.08
    max_enhancement: float = 10.0
    constant_factor: float = 1.0

    def __post_init__(self) -> None:
        if self.model not in ("pnk", "none", "constant"):
            raise ValueError("model must be 'pnk', 'none' or 'constant'")
        if self.efficiency < 0.0:
            raise ValueError("efficiency must be non-negative")
        if self.max_enhancement < 1.0:
            raise ValueError("max_enhancement must be at least 1")

    def factor(self, tilt_deg: float, suspension_length: float, gap: float) -> float:
        """Multiplier on the axial settling velocity [-].

        Parameters
        ----------
        tilt_deg:
            Tilt away from vertical, in degrees.
        suspension_length:
            Current axial length of the still-suspended column [m].
        gap:
            Characteristic inner diameter of that column [m].
        """
        theta = math.radians(tilt_deg)
        if self.model == "constant":
            return float(self.constant_factor)
        cos_t = math.cos(theta)
        if self.model == "none" or tilt_deg == 0.0:
            return max(cos_t, 0.0)
        if gap <= 0.0 or suspension_length <= 0.0:
            return max(cos_t, 0.0)
        side = self.efficiency * 4.0 * suspension_length * abs(math.sin(theta)) / (math.pi * gap)
        return float(min(max(cos_t, 0.0) + side, self.max_enhancement))
