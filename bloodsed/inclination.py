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

Inclined *walls* work the same way, and a tube does not have to be tilted to
have them.  In a cone, cells land on the upward-facing wall and slide down it
while clear plasma is released under the downward-facing side -- which is the
whole principle of a lamella settler, and of the cone-inside-a-cone geometry.
The projected-area argument covers this too, and for an axisymmetric wall the
horizontal projection between two heights is exactly the change in the circle it
encloses, so it needs no angle estimate at all (see
:meth:`~bloodsed.geometry.TubeGeometry.wall_projection`).  The two contributions
are projections of different surfaces, so they add:

    Lambda = cos(theta) + eta * [ 4 L sin(theta) / (pi d) + P_wall / A ]

A straight tube has ``P_wall = 0``, so nothing about the tilted-tube behaviour
above changes.

This is a 1-D surrogate for a genuinely 2-D flow, so treat both terms as "how
much faster, roughly" rather than as predictions to three digits.  In
particular the enhancement is applied along the whole column, while the
surfaces producing it may sit in one part of the vessel.
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
    walls:
        Count the projection of inclined *walls* as settling surface, so a cone
        settles faster than a cylinder even standing upright.  Turn it off to
        get tilt-only behaviour.
    """

    model: str = "pnk"
    efficiency: float = 0.08
    max_enhancement: float = 10.0
    constant_factor: float = 1.0
    walls: bool = True

    def __post_init__(self) -> None:
        if self.model not in ("pnk", "none", "constant"):
            raise ValueError("model must be 'pnk', 'none' or 'constant'")
        if self.efficiency < 0.0:
            raise ValueError("efficiency must be non-negative")
        if self.max_enhancement < 1.0:
            raise ValueError("max_enhancement must be at least 1")

    def factor(self, tilt_deg: float, suspension_length: float, gap: float,
               wall_projection: float = 0.0, area: float = 0.0) -> float:
        """Multiplier on the axial settling velocity [-].

        Parameters
        ----------
        tilt_deg:
            Tilt away from vertical, in degrees.
        suspension_length:
            Current axial length of the still-suspended column [m].
        gap:
            Characteristic hydraulic diameter of that column [m].
        wall_projection:
            Horizontal projection of the inclined walls inside the suspended
            column [m^2].  Zero for a straight tube.
        area:
            Flow area where the boundary currently sits [m^2], which is what
            converts a production rate into a boundary speed.
        """
        theta = math.radians(tilt_deg)
        if self.model == "constant":
            return float(self.constant_factor)
        cos_t = max(math.cos(theta), 0.0)
        if self.model == "none":
            return cos_t

        projected = 0.0
        if gap > 0.0 and suspension_length > 0.0:
            projected += 4.0 * suspension_length * abs(math.sin(theta)) / (math.pi * gap)
        if self.walls and wall_projection > 0.0 and area > 0.0:
            projected += wall_projection / area
        if projected <= 0.0:
            return cos_t
        return float(min(cos_t + self.efficiency * projected, self.max_enhancement))
