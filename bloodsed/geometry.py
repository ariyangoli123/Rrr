"""Tube geometries.

A geometry is a radius profile ``r(z)`` along the tube axis, with ``z`` measured
from the closed bottom (0) to the open top (``length``).  The solver only ever
asks for the cross-sectional area at the cell faces and the volume of each cell,
so any shape you can describe as ``r(z)`` drops straight in.

A geometry may also have an *inner* boundary -- a core the blood flows around,
as in :class:`AnnularCone` -- in which case the flow area is the annulus between
the two walls and the characteristic width is the hydraulic diameter rather than
the bore.

All constructors take millimetres, because that is how tubes are specified.
Every attribute and method returns SI (metres).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from .units import MM, to_mm

ArrayLike = np.ndarray | float


class TubeGeometry:
    """Base class for every tube shape.

    Subclasses only have to implement :meth:`radius_at`.
    """

    #: human readable label used in plots and reports
    name: str = "tube"
    #: axial length of the tube [m]
    length: float = 0.0
    #: tilt away from vertical [degrees]; 0 is a perfectly upright tube
    tilt_deg: float = 0.0

    # -- shape ---------------------------------------------------------
    def radius_at(self, z: ArrayLike) -> np.ndarray:
        """Inner radius [m] at axial position ``z`` [m]."""
        raise NotImplementedError

    def radius(self, z: ArrayLike) -> np.ndarray:
        """Inner radius [m], with ``z`` clipped to the tube."""
        z = np.clip(np.asarray(z, dtype=float), 0.0, self.length)
        r = np.asarray(self.radius_at(z), dtype=float)
        if np.any(r <= 0.0):
            raise ValueError(f"{self.name}: radius must be positive everywhere")
        return r

    def inner_radius_at(self, z: ArrayLike) -> np.ndarray:
        """Radius [m] of the inner wall the blood flows around; 0 if there is none."""
        return np.zeros_like(np.asarray(z, dtype=float))

    def inner_radius(self, z: ArrayLike) -> np.ndarray:
        """Inner-wall radius [m], clipped to the tube and to the outer wall."""
        z = np.clip(np.asarray(z, dtype=float), 0.0, self.length)
        inner = np.asarray(self.inner_radius_at(z), dtype=float)
        return np.clip(inner, 0.0, self.radius_at(z))

    @property
    def has_core(self) -> bool:
        """True when the blood flows around an inner wall."""
        return bool(np.any(self.inner_radius(np.linspace(0.0, self.length, 64)) > 0.0))

    def area(self, z: ArrayLike) -> np.ndarray:
        """Flow area [m^2] -- the annulus between the walls when there is a core."""
        outer = self.radius(z)
        inner = self.inner_radius(z)
        return math.pi * (outer * outer - inner * inner)

    def diameter(self, z: ArrayLike) -> np.ndarray:
        """Outer bore [m]."""
        return 2.0 * self.radius(z)

    def hydraulic_diameter(self, z: ArrayLike) -> np.ndarray:
        """``4 A / P`` [m] -- the width that matters to a settling cell.

        For a plain tube this is the bore.  For an annulus it is twice the gap,
        which is why a narrow annular settler drags harder on the cells near its
        walls and clears plasma so much faster.
        """
        outer = self.radius(z)
        inner = self.inner_radius(z)
        perimeter = 2.0 * math.pi * (outer + inner)
        return np.where(perimeter > 0.0, 4.0 * self.area(z) / np.maximum(perimeter, 1e-300),
                        2.0 * outer)

    # -- integration ---------------------------------------------------
    #: axial positions where the profile jumps or kinks; the integration grid
    #: brackets each one so a step is never smeared across a trapezoid
    breakpoints: tuple[float, ...] = ()

    #: points used for the cached cumulative-volume table
    _CUM_POINTS = 20001

    def _grid(self, z0: float, z1: float, intervals: int) -> np.ndarray:
        grid = np.linspace(z0, z1, intervals + 1)
        inner = [b for b in self.breakpoints if z0 < b < z1]
        if inner:
            eps = 1e-9 * max(self.length, 1e-9)
            extra = np.asarray([[b - eps, b + eps] for b in inner]).ravel()
            grid = np.unique(np.concatenate([grid, np.clip(extra, z0, z1)]))
        return grid

    def _cumulative(self) -> tuple[np.ndarray, np.ndarray]:
        """Cached ``(z, volume below z)`` table, the basis of every integral."""
        cached = getattr(self, "_cum_cache", None)
        if cached is None:
            grid = self._grid(0.0, self.length, self._CUM_POINTS - 1)
            a = self.area(grid)
            cum = np.concatenate(
                [[0.0], np.cumsum(np.diff(grid) * 0.5 * (a[:-1] + a[1:]))])
            cached = (grid, cum)
            self._cum_cache = cached
        return cached

    def _wall_projection_table(self) -> tuple[np.ndarray, np.ndarray]:
        """Cached ``(z, projected wall area below z)`` [m, m^2].

        A wall that is not vertical is a settling surface: cells land on its
        upward-facing side and slide off, and clear plasma is released under its
        downward-facing side.  What sets the rate is the surface's *horizontal
        projection*, and for an axisymmetric wall that projection between two
        heights is exactly the change in the circle it encloses -- so the total
        projected area is the total variation of ``pi r^2`` along the tube, with
        no angles to estimate.  A straight tube has none of it; a cone has a lot.
        """
        cached = getattr(self, "_wall_cache", None)
        if cached is None:
            grid = self._grid(0.0, self.length, 4096)
            outer = math.pi * self.radius(grid) ** 2
            inner = math.pi * self.inner_radius(grid) ** 2
            variation = np.abs(np.diff(outer)) + np.abs(np.diff(inner))
            cached = (grid, np.concatenate([[0.0], np.cumsum(variation)]))
            self._wall_cache = cached
        return cached

    def wall_projection(self, z_lo: float, z_hi: float) -> float:
        """Horizontal projection [m^2] of the walls between two heights."""
        if z_hi <= z_lo:
            return 0.0
        grid, cum = self._wall_projection_table()
        lo = float(np.interp(np.clip(z_lo, 0.0, self.length), grid, cum))
        hi = float(np.interp(np.clip(z_hi, 0.0, self.length), grid, cum))
        return max(hi - lo, 0.0)

    def cell_volumes(self, z_faces: np.ndarray) -> np.ndarray:
        """Volume [m^3] of each cell delimited by ``z_faces``.

        Taken as differences of the cumulative table, so the cell volumes
        always sum to :meth:`volume` -- including across a step, where a naive
        per-cell quadrature would lose a slice.
        """
        grid, cum = self._cumulative()
        return np.diff(np.interp(np.asarray(z_faces, dtype=float), grid, cum))

    def volume(self) -> float:
        """Total internal volume [m^3]."""
        return float(self._cumulative()[1][-1])

    def volume_below(self, z: float) -> float:
        """Internal volume [m^3] between the bottom and height ``z``."""
        grid, cum = self._cumulative()
        return float(np.interp(float(np.clip(z, 0.0, self.length)), grid, cum))

    def height_of_volume(self, volume: float) -> float:
        """Inverse of :meth:`volume_below`: fill height holding ``volume``."""
        grid, cum = self._cumulative()
        return float(np.interp(float(np.clip(volume, 0.0, cum[-1])), cum, grid))

    def _hydraulic_table(self) -> tuple[np.ndarray, np.ndarray]:
        """Cached ``(z, integral of the hydraulic diameter below z)``."""
        cached = getattr(self, "_hyd_cache", None)
        if cached is None:
            grid = self._grid(0.0, self.length, 4096)
            d = self.hydraulic_diameter(grid)
            cached = (grid, np.concatenate(
                [[0.0], np.cumsum(np.diff(grid) * 0.5 * (d[:-1] + d[1:]))]))
            self._hyd_cache = cached
        return cached

    def mean_diameter(self, z_lo: float = 0.0, z_hi: float | None = None) -> float:
        """Length-averaged hydraulic diameter [m] over ``[z_lo, z_hi]``.

        The characteristic gap of the Boycott model and of the wall drag: the
        bore for a plain tube, twice the gap for an annulus.  Read from a cached
        table, because the solver asks for it on every time step.
        """
        z_hi = self.length if z_hi is None else z_hi
        if z_hi <= z_lo:
            return float(self.hydraulic_diameter(z_lo))
        grid, cum = self._hydraulic_table()
        lo = float(np.interp(np.clip(z_lo, 0.0, self.length), grid, cum))
        hi = float(np.interp(np.clip(z_hi, 0.0, self.length), grid, cum))
        return (hi - lo) / (z_hi - z_lo)

    # -- reporting -----------------------------------------------------
    @property
    def tilt_rad(self) -> float:
        return math.radians(self.tilt_deg)

    def describe(self) -> str:
        d0 = to_mm(float(self.diameter(0.0)))
        d1 = to_mm(float(self.diameter(self.length)))
        tilt = f", tilt {self.tilt_deg:g} deg" if self.tilt_deg else ""
        core = ""
        if self.has_core:
            gap = to_mm(float(self.hydraulic_diameter(0.5 * self.length))) / 2.0
            core = f", annular gap {gap:.2f} mm"
        return (
            f"{self.name}: L={to_mm(self.length):.0f} mm, "
            f"D {d0:.2f} -> {d1:.2f} mm (bottom -> top), "
            f"V={self.volume() * 1e6:.2f} mL{core}{tilt}"
        )

    def to_dict(self) -> dict:
        data = {"kind": type(self).__name__, "name": self.name, "tilt_deg": self.tilt_deg}
        data.update(getattr(self, "_params", {}))
        return data

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} {self.describe()}>"


# ----------------------------------------------------------------------
# concrete shapes
# ----------------------------------------------------------------------
class Cylinder(TubeGeometry):
    """Straight tube of constant bore -- the Westergren/Wintrobe standard."""

    def __init__(self, length_mm: float, diameter_mm: float, *, name: str | None = None,
                 tilt_deg: float = 0.0):
        _check_positive(length_mm=length_mm, diameter_mm=diameter_mm)
        self.length = length_mm * MM
        self._radius = 0.5 * diameter_mm * MM
        self.tilt_deg = tilt_deg
        self.name = name or f"cylinder D{diameter_mm:g}"
        self._params = {"length_mm": length_mm, "diameter_mm": diameter_mm}

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        return np.full_like(np.asarray(z, dtype=float), self._radius)


class Taper(TubeGeometry):
    """Linear taper (a frustum): bore changes steadily from bottom to top.

    ``bottom_diameter_mm < top_diameter_mm`` gives a funnel that narrows
    downward, which concentrates the cells as they fall.  The reverse widens
    downward and dilutes them.
    """

    def __init__(self, length_mm: float, bottom_diameter_mm: float, top_diameter_mm: float,
                 *, name: str | None = None, tilt_deg: float = 0.0):
        _check_positive(length_mm=length_mm, bottom_diameter_mm=bottom_diameter_mm,
                        top_diameter_mm=top_diameter_mm)
        self.length = length_mm * MM
        self._r0 = 0.5 * bottom_diameter_mm * MM
        self._r1 = 0.5 * top_diameter_mm * MM
        self.tilt_deg = tilt_deg
        shape = "funnel" if top_diameter_mm > bottom_diameter_mm else "inverted funnel"
        self.name = name or f"{shape} D{bottom_diameter_mm:g}-{top_diameter_mm:g}"
        self._params = {"length_mm": length_mm, "bottom_diameter_mm": bottom_diameter_mm,
                        "top_diameter_mm": top_diameter_mm}

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        return self._r0 + (self._r1 - self._r0) * (z / self.length)


#: ``Cone`` reads better than ``Taper`` in scripts; same class.
Cone = Taper


class Hourglass(TubeGeometry):
    """Two opposed cones meeting at a throat -- a local constriction."""

    def __init__(self, length_mm: float, end_diameter_mm: float, throat_diameter_mm: float,
                 throat_position: float = 0.5, *, name: str | None = None, tilt_deg: float = 0.0):
        _check_positive(length_mm=length_mm, end_diameter_mm=end_diameter_mm,
                        throat_diameter_mm=throat_diameter_mm)
        if not 0.0 < throat_position < 1.0:
            raise ValueError("throat_position must be in (0, 1)")
        self.length = length_mm * MM
        self._r_end = 0.5 * end_diameter_mm * MM
        self._r_throat = 0.5 * throat_diameter_mm * MM
        self._zt = throat_position * self.length
        self.breakpoints = (self._zt,)
        self.tilt_deg = tilt_deg
        self.name = name or f"hourglass D{end_diameter_mm:g}/{throat_diameter_mm:g}"
        self._params = {"length_mm": length_mm, "end_diameter_mm": end_diameter_mm,
                        "throat_diameter_mm": throat_diameter_mm,
                        "throat_position": throat_position}

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        lower = self._r_end + (self._r_throat - self._r_end) * (z / self._zt)
        upper = self._r_throat + (self._r_end - self._r_throat) * (
            (z - self._zt) / (self.length - self._zt)
        )
        return np.where(z <= self._zt, lower, upper)


class Bulb(TubeGeometry):
    """Cylinder with a smooth (Gaussian) bulge or waist somewhere along it."""

    def __init__(self, length_mm: float, diameter_mm: float, bulge_diameter_mm: float,
                 bulge_position: float = 0.5, bulge_width: float = 0.12,
                 *, name: str | None = None, tilt_deg: float = 0.0):
        _check_positive(length_mm=length_mm, diameter_mm=diameter_mm,
                        bulge_diameter_mm=bulge_diameter_mm)
        if not 0.0 <= bulge_position <= 1.0:
            raise ValueError("bulge_position must be in [0, 1]")
        if bulge_width <= 0.0:
            raise ValueError("bulge_width must be positive")
        self.length = length_mm * MM
        self._r = 0.5 * diameter_mm * MM
        self._rb = 0.5 * bulge_diameter_mm * MM
        self._zc = bulge_position * self.length
        self._w = bulge_width * self.length
        self.tilt_deg = tilt_deg
        kind = "bulb" if bulge_diameter_mm > diameter_mm else "waist"
        self.name = name or f"{kind} D{diameter_mm:g}/{bulge_diameter_mm:g}"
        self._params = {"length_mm": length_mm, "diameter_mm": diameter_mm,
                        "bulge_diameter_mm": bulge_diameter_mm,
                        "bulge_position": bulge_position, "bulge_width": bulge_width}

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        bump = np.exp(-0.5 * ((z - self._zc) / self._w) ** 2)
        return self._r + (self._rb - self._r) * bump



class AnnularCone(TubeGeometry):
    """A cone standing inside another cone, with the blood filling the gap.

    This is a settling tube built as a lamella (inclined plate) settler.  The
    outer cone opens upward at ``angle_deg`` from vertical; the inner cone runs
    parallel to it, ``gap_mm`` away measured perpendicular to the wall, so the
    blood occupies an annulus of constant gap all the way round.

    Two things make it behave unlike any straight tube, and both are already in
    the physics rather than bolted on:

    * every wall is inclined, so cells only fall the width of the gap before
      landing on the outer cone and sliding down it, while clear plasma is
      released under the inner cone and rises -- the Boycott effect without
      tilting anything;
    * the gap sets the hydraulic diameter, so a narrow gap clears plasma faster
      and drags harder on the cells at the same time.

    Below the inner cone's tip the annulus closes and the section is a plain
    circle, which is what gives the shape its funnel-like bottom.

    Parameters
    ----------
    length_mm:
        Axial height of the assembly.
    bottom_diameter_mm:
        Bore of the *outer* cone at the bottom.
    angle_deg:
        Half-angle of the cones, from vertical.  0 gives a straight annulus
        (and no inclined-wall effect at all).
    gap_mm:
        Perpendicular gap between the two cones.
    inner_angle_deg:
        Half-angle of the inner cone if it is not parallel to the outer one.
    """

    def __init__(self, length_mm: float, bottom_diameter_mm: float = 6.0,
                 angle_deg: float = 15.0, gap_mm: float = 2.0,
                 inner_angle_deg: float | None = None, *, name: str | None = None,
                 tilt_deg: float = 0.0):
        _check_positive(length_mm=length_mm, bottom_diameter_mm=bottom_diameter_mm,
                        gap_mm=gap_mm)
        if not 0.0 <= angle_deg < 80.0:
            raise ValueError("angle_deg must be in [0, 80)")
        inner_angle = angle_deg if inner_angle_deg is None else inner_angle_deg
        if not 0.0 <= inner_angle < 80.0:
            raise ValueError("inner_angle_deg must be in [0, 80)")

        self.length = length_mm * MM
        self.angle_deg = float(angle_deg)
        self.inner_angle_deg = float(inner_angle)
        self.gap = gap_mm * MM
        self._r0 = 0.5 * bottom_diameter_mm * MM
        self._slope = math.tan(math.radians(angle_deg))
        self._inner_slope = math.tan(math.radians(inner_angle))
        # the gap is perpendicular to the wall; radially it is wider
        self._radial_gap = self.gap / math.cos(math.radians(angle_deg))
        self.tilt_deg = tilt_deg
        self.name = name or f"annular cone {angle_deg:g} deg, {gap_mm:g} mm gap"
        self._params = {"length_mm": length_mm, "bottom_diameter_mm": bottom_diameter_mm,
                        "angle_deg": angle_deg, "gap_mm": gap_mm,
                        "inner_angle_deg": inner_angle}
        probe = np.linspace(0.0, self.length, 512)
        clearance = self.radius(probe) - self.inner_radius(probe)
        if float(clearance.min()) <= 1e-6:
            where = to_mm(float(probe[int(np.argmin(clearance))]))
            raise ValueError(
                f"the two cones meet at {where:.0f} mm, leaving no room for blood; "
                f"widen bottom_diameter_mm, narrow the gap, or keep inner_angle_deg "
                f"no steeper than angle_deg"
            )

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        return self._r0 + self._slope * z

    def inner_radius_at(self, z: ArrayLike) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        # the inner cone starts where its own wall would leave the outer one
        inner = self._r0 - self._radial_gap + self._inner_slope * z
        return np.maximum(inner, 0.0)

    @property
    def tip_height(self) -> float:
        """Height [m] at which the inner cone begins; 0 if it starts at the bottom."""
        if self._r0 - self._radial_gap >= 0:
            return 0.0
        if self._inner_slope <= 0:
            return self.length
        return min((self._radial_gap - self._r0) / self._inner_slope, self.length)


class Stepped(TubeGeometry):
    """Stack of straight sections of different bore, listed bottom to top."""

    def __init__(self, segments: Sequence[tuple[float, float]], *, name: str | None = None,
                 tilt_deg: float = 0.0):
        if not segments:
            raise ValueError("Stepped needs at least one (length_mm, diameter_mm) segment")
        lengths = []
        radii = []
        for seg_len, seg_dia in segments:
            _check_positive(segment_length_mm=seg_len, segment_diameter_mm=seg_dia)
            lengths.append(seg_len * MM)
            radii.append(0.5 * seg_dia * MM)
        self._edges = np.concatenate([[0.0], np.cumsum(lengths)])
        self._radii = np.asarray(radii)
        self.length = float(self._edges[-1])
        self.breakpoints = tuple(self._edges[1:-1])
        self.tilt_deg = tilt_deg
        self.name = name or "stepped " + "/".join(f"{d:g}" for _, d in segments)
        self._params = {"segments": [list(s) for s in segments]}

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        idx = np.clip(np.searchsorted(self._edges, z, side="right") - 1, 0, len(self._radii) - 1)
        return self._radii[idx]


class Profile(TubeGeometry):
    """Arbitrary shape given as a table of ``(height, diameter)`` points.

    Points are in millimetres, must be sorted by height, and are interpolated
    linearly.  This is the escape hatch for a measured or CAD-derived tube.
    """

    def __init__(self, heights_mm: Sequence[float], diameters_mm: Sequence[float],
                 *, name: str | None = None, tilt_deg: float = 0.0):
        h = np.asarray(heights_mm, dtype=float)
        d = np.asarray(diameters_mm, dtype=float)
        if h.size < 2 or h.size != d.size:
            raise ValueError("need at least two matching height/diameter points")
        if np.any(np.diff(h) <= 0):
            raise ValueError("heights must be strictly increasing")
        if np.any(d <= 0):
            raise ValueError("diameters must be positive")
        if h[0] != 0.0:
            raise ValueError("the first height point must be 0 (the tube bottom)")
        self._h = h * MM
        self._r = 0.5 * d * MM
        self.length = float(self._h[-1])
        self.breakpoints = tuple(self._h[1:-1])
        self.tilt_deg = tilt_deg
        self.name = name or "custom profile"
        self._params = {"heights_mm": h.tolist(), "diameters_mm": d.tolist()}

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        return np.interp(np.asarray(z, dtype=float), self._h, self._r)

    @classmethod
    def from_csv(cls, path: str, **kwargs) -> "Profile":
        """Read ``height_mm,diameter_mm`` rows (a header line is tolerated)."""
        data = np.genfromtxt(path, delimiter=",", names=None, dtype=float,
                             comments="#", skip_header=0)
        data = np.atleast_2d(data)
        if np.isnan(data[0]).any():  # header row
            data = data[1:]
        return cls(data[:, 0], data[:, 1], **kwargs)


class FunctionTube(TubeGeometry):
    """Shape defined by a Python callable ``diameter_mm(z_mm) -> mm``."""

    def __init__(self, length_mm: float, diameter_fn: Callable[[np.ndarray], np.ndarray],
                 *, name: str | None = None, tilt_deg: float = 0.0):
        _check_positive(length_mm=length_mm)
        self.length = length_mm * MM
        self._fn = diameter_fn
        self.tilt_deg = tilt_deg
        self.name = name or "function profile"
        self._params = {"length_mm": length_mm}

    def radius_at(self, z: ArrayLike) -> np.ndarray:
        z_mm = np.asarray(z, dtype=float) / MM
        return 0.5 * np.asarray(self._fn(z_mm), dtype=float) * MM


def _check_positive(**values: float) -> None:
    for key, value in values.items():
        if value is None or value <= 0:
            raise ValueError(f"{key} must be positive (got {value!r})")


# ----------------------------------------------------------------------
# named geometries
# ----------------------------------------------------------------------
def _presets() -> dict[str, Callable[[], TubeGeometry]]:
    return {
        # clinical standards
        "westergren": lambda: Cylinder(200, 2.5, name="Westergren 2.5x200"),
        "wintrobe": lambda: Cylinder(100, 2.8, name="Wintrobe 2.8x100"),
        "micro": lambda: Cylinder(75, 1.1, name="micro-capillary 1.1x75"),
        "wide": lambda: Cylinder(200, 8.0, name="wide bore 8x200"),
        # shape studies, all holding roughly the Westergren column height
        "funnel": lambda: Taper(200, 1.2, 4.0, name="funnel (narrows down)"),
        "inverted-funnel": lambda: Taper(200, 4.0, 1.2, name="inverted funnel (widens down)"),
        "hourglass": lambda: Hourglass(200, 4.0, 1.2, 0.5, name="hourglass (mid throat)"),
        "bulb": lambda: Bulb(200, 2.5, 6.0, 0.5, 0.10, name="bulb (mid bulge)"),
        "waist": lambda: Bulb(200, 4.0, 1.5, 0.5, 0.10, name="waist (mid constriction)"),
        "stepped": lambda: Stepped([(70, 1.5), (60, 3.0), (70, 5.0)], name="stepped 1.5/3/5"),
        "conical-tip": lambda: Stepped([(20, 1.0), (180, 3.0)], name="conical-tip reservoir"),
        # cone inside a cone: a lamella settler, fast even standing upright
        "annular-cone": lambda: AnnularCone(120, 8.0, 12.0, 1.5, name="annular cone 12 deg / 1.5 mm"),
        "annular-narrow": lambda: AnnularCone(120, 8.0, 12.0, 0.6, name="annular cone 12 deg / 0.6 mm"),
        "annular-steep": lambda: AnnularCone(120, 8.0, 30.0, 1.5, name="annular cone 30 deg / 1.5 mm"),
        "annular-straight": lambda: AnnularCone(120, 8.0, 0.0, 1.5, name="annular cylinder (no incline)"),
        # a tilted Westergren -- the classic pre-analytical error
        "westergren-tilt3": lambda: Cylinder(200, 2.5, name="Westergren tilted 3 deg", tilt_deg=3.0),
        "westergren-tilt15": lambda: Cylinder(200, 2.5, name="Westergren tilted 15 deg", tilt_deg=15.0),
    }


PRESETS: dict[str, Callable[[], TubeGeometry]] = _presets()

#: geometry sets used by ``bloodsed compare``
GEOMETRY_SETS: dict[str, list[str]] = {
    "clinical": ["westergren", "wintrobe", "micro", "wide"],
    "shapes": ["westergren", "funnel", "inverted-funnel", "hourglass", "bulb", "stepped"],
    "annular": ["westergren", "annular-straight", "annular-cone", "annular-steep",
                "annular-narrow"],
    "tilt": ["westergren", "westergren-tilt3", "westergren-tilt15"],
    "all": list(PRESETS),
}


def get_geometry(name: str) -> TubeGeometry:
    """Build a preset geometry by name."""
    try:
        return PRESETS[name]()
    except KeyError:
        raise KeyError(
            f"unknown geometry {name!r}; available: {', '.join(sorted(PRESETS))}"
        ) from None


_SPEC_BUILDERS: dict[str, type[TubeGeometry]] = {
    "cylinder": Cylinder,
    "taper": Taper,
    "cone": Taper,
    "frustum": Taper,
    "hourglass": Hourglass,
    "bulb": Bulb,
    "stepped": Stepped,
    "profile": Profile,
    "annulus": AnnularCone,
    "annular": AnnularCone,
    "conecone": AnnularCone,
}

_SPEC_ALIASES = {
    "l": "length_mm", "length": "length_mm",
    "d": "diameter_mm", "diameter": "diameter_mm",
    "dbot": "bottom_diameter_mm", "bottom": "bottom_diameter_mm",
    "dtop": "top_diameter_mm", "top": "top_diameter_mm",
    "dend": "end_diameter_mm", "end": "end_diameter_mm",
    "dthroat": "throat_diameter_mm", "throat": "throat_diameter_mm",
    "at": "throat_position", "pos": "bulge_position",
    "dbulge": "bulge_diameter_mm", "bulge": "bulge_diameter_mm",
    "width": "bulge_width",
    "tilt": "tilt_deg",
    "angle": "angle_deg", "a": "angle_deg",
    "gap": "gap_mm", "g": "gap_mm",
    "inner": "inner_angle_deg", "ainner": "inner_angle_deg",
}


#: aliases that mean something different for one shape
_SPEC_KIND_ALIASES: dict[str, dict[str, str]] = {
    "annulus": {"diameter_mm": "bottom_diameter_mm"},
    "annular": {"diameter_mm": "bottom_diameter_mm"},
    "conecone": {"diameter_mm": "bottom_diameter_mm"},
}


def from_spec(spec: str) -> TubeGeometry:
    """Parse a geometry from a compact string.

    Examples::

        westergren
        westergren:tilt=3
        cylinder:L=200,D=2.5
        cone:L=200,Dbot=1.2,Dtop=4
        hourglass:L=200,Dend=4,Dthroat=1,at=0.4
        bulb:L=200,D=2.5,Dbulge=6,pos=0.5,width=0.1
        stepped:20x1,180x3
        annulus:L=150,D=6,angle=15,gap=2
    """
    spec = spec.strip()
    kind, _, rest = spec.partition(":")
    kind = kind.strip().lower()
    rest = rest.strip()

    # A bare preset name, optionally with a tilt.  Anything else falls through
    # to the explicit shape builders (some names, e.g. "stepped", are both).
    if kind in PRESETS:
        kv = _try_parse_kv(rest)
        if kv is not None and set(kv) <= {"tilt_deg"}:
            geo = PRESETS[kind]()
            if "tilt_deg" in kv:
                geo.tilt_deg = float(kv["tilt_deg"])
            return geo
        if kind not in _SPEC_BUILDERS:
            raise ValueError(
                f"preset {kind!r} only accepts 'tilt='; got {rest!r}. Use an "
                f"explicit shape such as 'cylinder:L=200,D=2.5' instead."
            )

    if kind not in _SPEC_BUILDERS:
        raise ValueError(
            f"unknown geometry spec {spec!r}. Use a preset "
            f"({', '.join(sorted(PRESETS))}) or a shape "
            f"({', '.join(sorted(_SPEC_BUILDERS))})."
        )

    if kind == "profile":
        raise ValueError("build a Profile from CSV with Profile.from_csv(), not a spec string")

    if kind == "stepped":
        segments = []
        tilt = 0.0
        for chunk in rest.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" in chunk:
                key, _, value = chunk.partition("=")
                if _SPEC_ALIASES.get(key.strip().lower()) != "tilt_deg":
                    raise ValueError(f"stepped only accepts LxD segments and tilt=, got {chunk!r}")
                tilt = float(value)
                continue
            seg_len, sep, seg_dia = chunk.partition("x")
            if not sep:
                raise ValueError(f"stepped segments look like '20x1.5' (length x diameter), got {chunk!r}")
            segments.append((float(seg_len), float(seg_dia)))
        return Stepped(segments, tilt_deg=tilt)

    kwargs = _parse_kv(rest)
    for source, destination in _SPEC_KIND_ALIASES.get(kind, {}).items():
        if source in kwargs:
            kwargs[destination] = kwargs.pop(source)
    builder = _SPEC_BUILDERS[kind]
    try:
        return builder(**{k: float(v) for k, v in kwargs.items()})  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"bad parameters for {kind!r}: {exc}") from None


def split_specs(text: str) -> list[str]:
    """Split a user supplied list of geometries into individual specs.

    A comma separates geometries *and* the parameters inside one spec, so the
    split has to know which is which::

        westergren,wintrobe                     -> two tubes
        westergren,westergren:tilt=3            -> two tubes
        cone:L=200,Dbot=1.2,Dtop=4              -> one tube
        stepped:20x1,180x3                      -> one tube

    A fragment continues the previous spec when it is a ``key=value`` pair or a
    ``length x diameter`` segment.  Use ``;`` to separate geometries explicitly
    if you would rather not rely on that.
    """
    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]
    specs: list[str] = []
    for fragment in text.split(","):
        fragment = fragment.strip()
        if not fragment:
            continue
        continues = specs and (
            (":" not in fragment and "=" in fragment)
            or re.fullmatch(r"[\d.]+x[\d.]+", fragment) is not None
        )
        if continues:
            specs[-1] += "," + fragment
        else:
            specs.append(fragment)
    return specs


def _parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        if not sep:
            raise ValueError(f"expected key=value, got {chunk!r}")
        key = key.strip().lower()
        out[_SPEC_ALIASES.get(key, key)] = value.strip()
    return out


def _try_parse_kv(text: str) -> dict[str, str] | None:
    try:
        return _parse_kv(text)
    except ValueError:
        return None
