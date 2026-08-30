"""Cone geometry — EXACT, from first principles.

Everything in this module is exact solid geometry. There is no fitted parameter and no
model assumption anywhere in it, so every result is tier ``EXACT``. The one exception
is :func:`range_ceiling`, which needs the packing fraction ``phi_pack`` (unknown U01)
and therefore inherits the weaker tier of that assumption.

Model (spec §2.1)
-----------------
A perfect cone, apex upward, axis ``z`` pointing down, origin at the **outer cone's
apex**. ``x`` is distance from that apex along the axis::

    D_outer(x) = 2 x tan(theta_o)
    D_inner(x) = 2 max(0, x - Delta) tan(theta_i)
    clearance_radial(x) = (D_outer(x) - D_inner(x)) / 2
    gap_perpendicular(x) = clearance_radial(x) cos(theta_o)
    A(x) = pi/4 (D_outer(x)^2 - D_inner(x)^2)
    V = integral of A(x) dx from x_bl to x_bl + L

For a constant gap (``theta_i == theta_o``)::

    Delta = d / sin(theta)
    clearance = d / cos(theta)      (constant over the whole length)

Blood-line convention (spec §2.2, addendum §A)
----------------------------------------------
Two generations are supported and every output is labelled with the one in force:

* **Gen-A** (superseded): mouth diameter fixed at 5.000 mm, blood line 3.000 mm below
  the mouth, cone body 53.000 mm.
* **Gen-B** (current): the blood line *is* the mouth, cone body 50.000 mm, plain
  cylinder above it.

Both generations describe the *same cone*; only the mouth position and the labelling
change. The tube library is stored with its Gen-A construction rule because that is how
the parts were cut, and every published dimension reproduces from it to 4 dp.

References
----------
ESR_SIMULATOR_SPEC.md §2 and v1.1 addendum §A, §B.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Literal, Sequence

from scipy.integrate import quad
from scipy.optimize import brentq

from ..registry import tube_library
from ..tiers import GEN_A, GEN_B, Result, ResultSet, Tier
from ..units import check_angle_deg, check_fraction, check_positive

__all__ = [
    "Generation",
    "Cone",
    "StepUpper",
    "Counterbore",
    "from_library",
    "list_tubes",
    "geometry_report",
    "range_ceiling",
    "phi_pack_sensitivity",
    "stepped_upper_cone",
    "counterbore",
    "shift_inner_cone",
    "taper_opening_rate",
]

Generation = Literal["A", "B"]

#: Gen-A construction constants (spec §2.2, addendum §A).
GEN_A_MOUTH_DIAMETER_MM = 5.000
GEN_A_BLOOD_LINE_OFFSET_MM = 3.000


@dataclass(frozen=True, slots=True)
class Cone:
    """A conical annulus, fully specified. Lengths in mm, angles in degrees.

    Parameters
    ----------
    theta_o_deg, theta_i_deg
        Half-angles of the outer and inner cones.
    delta_mm
        Axial offset of the inner apex below the outer apex.
    x_bl_mm
        Distance from the outer apex down to the blood line.
    length_mm
        Blood column length below the blood line (50.0 mm throughout the project).
    generation
        ``"A"`` or ``"B"``; see the module docstring.
    tube_id
        Library identifier, if this came from ``tubes.yaml``.
    """

    theta_o_deg: float
    theta_i_deg: float
    delta_mm: float
    x_bl_mm: float
    length_mm: float = 50.0
    generation: Generation = "B"
    tube_id: str = ""

    def __post_init__(self) -> None:
        check_angle_deg("theta_o_deg", self.theta_o_deg)
        check_angle_deg("theta_i_deg", self.theta_i_deg)
        check_positive("x_bl_mm", self.x_bl_mm)
        check_positive("length_mm", self.length_mm)
        if self.delta_mm < 0:
            raise ValueError(f"delta_mm must be >= 0, got {self.delta_mm}")
        if self.generation not in ("A", "B"):
            raise ValueError(f"generation must be 'A' or 'B', got {self.generation!r}")

    # ------------------------------------------------------------- primitives

    @property
    def theta_o(self) -> float:
        """Outer half-angle in radians."""
        return math.radians(self.theta_o_deg)

    @property
    def theta_i(self) -> float:
        """Inner half-angle in radians."""
        return math.radians(self.theta_i_deg)

    @property
    def is_constant_gap(self) -> bool:
        return abs(self.theta_o_deg - self.theta_i_deg) < 1e-12

    @property
    def x_base_mm(self) -> float:
        """Apex-to-base distance: ``x_bl + L`` (spec §2.1)."""
        return self.x_bl_mm + self.length_mm

    @property
    def generation_flag(self) -> str:
        return GEN_A if self.generation == "A" else GEN_B

    def d_outer(self, x: float) -> float:
        """``D_outer(x) = 2 x tan(theta_o)`` — spec §2.1."""
        return 2.0 * x * math.tan(self.theta_o)

    def d_inner(self, x: float) -> float:
        """``D_inner(x) = 2 max(0, x - Delta) tan(theta_i)`` — spec §2.1."""
        return 2.0 * max(0.0, x - self.delta_mm) * math.tan(self.theta_i)

    def clearance_radial(self, x: float) -> float:
        """Radial clearance ``(D_outer - D_inner)/2`` — spec §2.1."""
        return (self.d_outer(x) - self.d_inner(x)) / 2.0

    def gap_perpendicular(self, x: float) -> float:
        """Gap measured perpendicular to the wall: ``clearance cos(theta_o)``."""
        return self.clearance_radial(x) * math.cos(self.theta_o)

    def area(self, x: float) -> float:
        """Annular cross-section ``A(x)`` in mm^2 — spec §2.1."""
        return math.pi / 4.0 * (self.d_outer(x) ** 2 - self.d_inner(x) ** 2)

    def area_at_height(self, h: float) -> float:
        """Cross-section ``h`` mm below the blood line."""
        return self.area(self.x_bl_mm + h)

    def outer_wall_area(self, x1: float, x2: float) -> float:
        """Lateral area of the outer cone between ``x1`` and ``x2``, mm^2.

        ``S = pi tan(theta) sec(theta) (x2^2 - x1^2)``. This is the upward-facing
        surface that sheds clear plasma in the Boycott mechanism, so it is the
        geometric quantity the kinetics module needs.
        """
        lo, hi = (x1, x2) if x2 >= x1 else (x2, x1)
        t = math.tan(self.theta_o)
        return math.pi * t / math.cos(self.theta_o) * (hi**2 - lo**2)

    # ---------------------------------------------------------------- volumes

    def _volume_antiderivative(self, x: float) -> float:
        """Exact antiderivative of ``A(x)``.

        ``A(x) = pi [x^2 tan^2(theta_o) - max(0, x - Delta)^2 tan^2(theta_i)]`` is a
        piecewise polynomial, so::

            F(x) = pi/3 [x^3 tan^2(theta_o) - max(0, x - Delta)^3 tan^2(theta_i)]

        integrates it exactly. This matters: the descent solver evaluates cumulative
        volumes inside an ODE right-hand side, and quadrature there is thousands of
        times slower for no gain in accuracy.
        """
        t_o = math.tan(self.theta_o)
        t_i = math.tan(self.theta_i)
        inner = max(0.0, x - self.delta_mm)
        return math.pi / 3.0 * (x**3 * t_o * t_o - inner**3 * t_i * t_i)

    def volume_between(self, x_from: float, x_to: float) -> float:
        """Exact annulus volume between two axial positions, mm^3."""
        return self._volume_antiderivative(x_to) - self._volume_antiderivative(x_from)

    def volume_numeric(self, h_from: float = 0.0, h_to: float | None = None) -> float:
        """``integral A dx`` by quadrature, over a height window below the blood line.

        Kept as genuine quadrature so that
        ``test_closed_form_volume_matches_numeric_integration`` compares two independent
        computations rather than one formula against itself. Hot paths use
        :meth:`volume_between` and :meth:`cumulative_volume`, which are exact.
        """
        h_to = self.length_mm if h_to is None else h_to
        val, _err = quad(
            self.area, self.x_bl_mm + h_from, self.x_bl_mm + h_to, limit=200
        )
        return float(val)

    @property
    def volume_mm3(self) -> float:
        """Column volume, exact and cheap."""
        return self.volume_between(self.x_bl_mm, self.x_base_mm)

    def volume_closed_form(self) -> float:
        """Closed-form column volume — spec §2.1, valid for a constant gap.

        ``V = pi tan^2(theta) [Delta (b^2 - a^2) - Delta^2 (b - a)]`` with
        ``a = x_bl``, ``b = x_base``.

        Raises
        ------
        ValueError
            If the cone is tapered, where the closed form does not apply.
        """
        if not self.is_constant_gap:
            raise ValueError(
                "the closed-form volume of spec §2.1 assumes theta_i == theta_o; "
                "this cone is tapered, use volume_numeric()"
            )
        a, b, D = self.x_bl_mm, self.x_base_mm, self.delta_mm
        return math.pi * math.tan(self.theta_o) ** 2 * (
            D * (b**2 - a**2) - D**2 * (b - a)
        )

    def cumulative_volume(self, h: float) -> float:
        """``C(h) = integral of A(x_bl + s) ds`` from 0 to h — spec §5.4. Exact."""
        return self.volume_between(self.x_bl_mm, self.x_bl_mm + h)

    def height_for_volume(self, target_mm3: float) -> float:
        """Invert ``C(h) = target``. Returns the height below the blood line, mm."""
        total = self.volume_mm3
        if target_mm3 <= 0:
            return 0.0
        if target_mm3 >= total:
            return self.length_mm
        return float(
            brentq(
                lambda h: self.cumulative_volume(h) - target_mm3,
                0.0,
                self.length_mm,
                xtol=1e-12,
                rtol=8.9e-16,
            )
        )

    # ------------------------------------------------------- generation views

    @property
    def mouth_diameter_mm(self) -> float:
        """Outer diameter at the mouth, per the generation in force."""
        if self.generation == "A":
            return GEN_A_MOUTH_DIAMETER_MM
        return self.d_outer(self.x_bl_mm)

    @property
    def blood_line_offset_mm(self) -> float:
        """Mouth-to-blood-line distance. 3.000 in Gen-A, 0.000 in Gen-B."""
        return GEN_A_BLOOD_LINE_OFFSET_MM if self.generation == "A" else 0.0

    @property
    def cone_body_mm(self) -> float:
        """Length of cone body: 53.000 in Gen-A, 50.000 in Gen-B (addendum §A)."""
        return self.length_mm + self.blood_line_offset_mm

    def as_generation(self, generation: Generation) -> "Cone":
        """The same cone, relabelled under the other blood-line convention.

        The solid does not change: theta, Delta and x_bl are untouched. Only the mouth
        position, mouth diameter and body length are reported differently.
        """
        return replace(self, generation=generation)


# ------------------------------------------------------------------- optional features


@dataclass(frozen=True, slots=True)
class StepUpper:
    """Stepped upper cone above the blood line — addendum §B, approved design.

    The inner cone runs down to the blood line, then a square 90-degree step of radial
    width ``w`` and a continuation cone up to a sharp tip.
    """

    w_mm: float
    theta_upper_deg: float
    d_upper_base_mm: float
    tip_height_mm: float
    land_area_mm2: float
    clearance_above_min_mm: float
    clearance_above_at_tip_mm: float
    constant_clearance: bool


@dataclass(frozen=True, slots=True)
class Counterbore:
    """Coaxial counterbore from the top — spec §2.4a."""

    d_cb_mm: float
    x_cb_bottom_mm: float
    depth_below_bloodline_mm: float
    clearance_at_bl_mm: float


# ---------------------------------------------------------------------- constructors


def list_tubes() -> tuple[str, ...]:
    """Identifiers in the tube library."""
    return tuple(tube_library()["tubes"])


def from_library(tube_id: str, generation: Generation | None = None) -> Cone:
    """Build a :class:`Cone` from ``tubes.yaml``.

    ``x_bl`` is reconstructed from the Gen-A construction rule recorded in the library
    header (``x_bl = 2.5/tan(theta_o) + 3.000``), which reproduces every published
    dimension of all six tubes to 4 decimal places.
    """
    lib = tube_library()
    tubes = lib["tubes"]
    if tube_id not in tubes:
        raise KeyError(f"unknown tube {tube_id!r}; library has {list(tubes)}")
    spec = tubes[tube_id]
    meta = lib["meta"]

    theta_o_deg = float(spec["theta_o_deg"])
    theta_i_deg = float(spec["theta_i_deg"])
    length = float(meta["column_length_mm"])
    mouth_d = float(meta["gen_a_mouth_diameter_mm"])
    bl_offset = float(meta["gen_a_blood_line_offset_mm"])

    x_bl = (mouth_d / 2.0) / math.tan(math.radians(theta_o_deg)) + bl_offset

    gap = float(spec["gap_mm"])
    if abs(theta_i_deg - theta_o_deg) < 1e-12:
        delta = gap / math.sin(math.radians(theta_o_deg))
    else:
        # Tapered: Delta follows from gap(x_bl) = gap_top (spec §2.1).
        clearance_bl = gap / math.cos(math.radians(theta_o_deg))
        t_o = math.tan(math.radians(theta_o_deg))
        t_i = math.tan(math.radians(theta_i_deg))
        delta = (clearance_bl - x_bl * (t_o - t_i)) / t_i

    return Cone(
        theta_o_deg=theta_o_deg,
        theta_i_deg=theta_i_deg,
        delta_mm=delta,
        x_bl_mm=x_bl,
        length_mm=length,
        generation=generation or spec.get("generation", "B"),
        tube_id=tube_id,
    )


# --------------------------------------------------------------------------- results


def _exact(name: str, value: Any, unit: str, cone: Cone, **kw: Any) -> Result:
    kw.setdefault("source", "ESR_SIMULATOR_SPEC.md §2 (exact cone algebra)")
    flags = tuple(kw.pop("flags", ())) + (cone.generation_flag,)
    return Result.exact(name, value, unit, flags=flags, **kw)


def geometry_report(cone: Cone) -> ResultSet:
    """Every exact dimension of a cone, tagged and ready to print.

    All EXACT: geometry needs no fitted parameter.
    """
    a, b = cone.x_bl_mm, cone.x_base_mm
    v_num = cone.volume_numeric()

    results: list[Result] = [
        _exact("generation", cone.generation, "", cone,
               notes=("Gen-B: blood line IS the mouth, cone body 50.000 mm "
                      "(addendum §A)",) if cone.generation == "B" else
                     ("Gen-A: mouth 5.000 mm, blood line 3.000 mm below it, "
                      "cone body 53.000 mm — SUPERSEDED",)),
        _exact("theta_outer", cone.theta_o_deg, "deg", cone),
        _exact("theta_inner", cone.theta_i_deg, "deg", cone),
        _exact("delta_apex_offset", cone.delta_mm, "mm", cone),
        _exact("x_bloodline", a, "mm", cone),
        _exact("x_base", b, "mm", cone),
        _exact("column_length", cone.length_mm, "mm", cone),
        _exact("cone_body_length", cone.cone_body_mm, "mm", cone),
        _exact("mouth_diameter", cone.mouth_diameter_mm, "mm", cone),
        _exact("blood_line_offset", cone.blood_line_offset_mm, "mm", cone),
        _exact("d_outer_bloodline", cone.d_outer(a), "mm", cone),
        _exact("d_inner_bloodline", cone.d_inner(a), "mm", cone),
        _exact("d_outer_base", cone.d_outer(b), "mm", cone,
               notes=("DERIVED — never a manufacturing input (addendum §A)",)),
        _exact("d_inner_base", cone.d_inner(b), "mm", cone,
               notes=("DERIVED — never a manufacturing input (addendum §A)",)),
        _exact("clearance_bloodline", cone.clearance_radial(a), "mm", cone),
        _exact("clearance_base", cone.clearance_radial(b), "mm", cone),
        _exact("gap_bloodline", cone.gap_perpendicular(a), "mm", cone),
        _exact("gap_base", cone.gap_perpendicular(b), "mm", cone),
        _exact("area_bloodline", cone.area(a), "mm^2", cone),
        _exact("area_base", cone.area(b), "mm^2", cone),
        _exact("area_ratio_base_over_bl", cone.area(b) / cone.area(a), "", cone),
        _exact("volume_numeric", v_num, "mm^3", cone),
    ]

    if cone.is_constant_gap:
        v_cf = cone.volume_closed_form()
        results.append(_exact("volume_closed_form", v_cf, "mm^3", cone,
                              source="ESR_SIMULATOR_SPEC.md §2.1 closed form"))
        results.append(
            _exact("volume_closed_vs_numeric_pct", 100.0 * abs(v_cf - v_num) / v_num,
                   "%", cone, notes=("acceptance: < 0.05% (build prompt stage 1)",))
        )
    else:
        rate = taper_opening_rate(cone)
        results.append(rate)
        results.append(
            _exact("gap_opening_over_column", rate.value * cone.length_mm, "mm", cone)
        )

    notes = ["all dimensions EXACT: solid geometry, no fitted parameter"]
    if cone.tube_id:
        spec = tube_library()["tubes"].get(cone.tube_id, {})
        if spec.get("outside_isovolume_family"):
            notes.append(
                f"{cone.tube_id} is OUTSIDE the iso-volume family "
                f"({spec['volume_target_mm3']:.0f} mm^3, not 2000) and must not be "
                "compared directly with the rest (spec §2.3)"
            )
        for n in spec.get("notes", ()):
            notes.append(" ".join(str(n).split()))

    title = f"GEOMETRY {cone.tube_id or '(ad hoc)'} — Gen-{cone.generation}"
    return ResultSet(title=title, results=tuple(results), notes=tuple(notes))


def taper_opening_rate(cone: Cone) -> Result:
    """Gap opening rate ``(tan theta_o - tan theta_i) cos theta_o`` — spec §2.1."""
    rate = (math.tan(cone.theta_o) - math.tan(cone.theta_i)) * math.cos(cone.theta_o)
    return _exact("gap_opening_rate", rate, "mm/mm", cone)


# ------------------------------------------------------------------- range ceiling


def range_ceiling(
    cone: Cone,
    hematocrit: float,
    phi_pack: float = 0.90,
    *,
    phi_pack_assumed: bool = True,
) -> Result:
    """Deepest readable boundary height — spec §5.5.

    ``V_free_plasma = V (1 - Hct/phi_pack)``; the ceiling is the smallest ``h`` with
    ``C(h) >= V_free_plasma``.

    The geometry is exact but ``phi_pack`` is unknown U01 (assumed 0.90, plausible over
    [0.85, 0.95]), so the result is ESTIMATED, not EXACT, whenever the default is used.
    Pass ``phi_pack_assumed=False`` only if the packing fraction has actually been
    measured for the sample in hand.

    This same conservation also fixes where the descending boundary meets the rising
    sediment, which is why :mod:`esrsim.core.kinetics` stops there.
    """
    check_fraction("hematocrit", hematocrit)
    check_fraction("phi_pack", phi_pack)
    if hematocrit >= phi_pack:
        return Result.unknown(
            "range_ceiling",
            why=(
                f"haematocrit {hematocrit:.3f} is not below the packing fraction "
                f"{phi_pack:.3f}; there is no free plasma to clear and the boundary "
                "never forms"
            ),
            experiment="measure the packed cell volume of this sample directly",
        )

    total = cone.volume_mm3
    v_free = total * (1.0 - hematocrit / phi_pack)
    h = cone.height_for_volume(v_free)

    if phi_pack_assumed:
        return Result.estimated(
            "range_ceiling",
            h,
            "mm",
            source="ESR_SIMULATOR_SPEC.md §5.5",
            notes=(
                f"V_free_plasma = {v_free:.1f} mm^3 of {total:.1f} mm^3",
                "rides on phi_pack (unknown U01, assumed 0.90, range 0.85-0.95); "
                "run phi_pack_sensitivity() for the band",
            ),
        )
    return Result.exact(
        "range_ceiling",
        h,
        "mm",
        source="ESR_SIMULATOR_SPEC.md §5.5 with a measured phi_pack",
        notes=(f"V_free_plasma = {v_free:.1f} mm^3 of {total:.1f} mm^3",),
    )


def phi_pack_sensitivity(
    cone: Cone,
    hematocrit: float,
    phi_range: Sequence[float] = (0.85, 0.90, 0.95),
) -> ResultSet:
    """Range ceiling across the plausible ``phi_pack`` band — spec §5.5.

    Spec §5.5: *"The program must report sensitivity analysis over phi_pack in
    [0.85, 0.95] by default."* Called automatically by every range and feasibility
    report.
    """
    results = [
        range_ceiling(cone, hematocrit, phi)
        .rename(f"range_ceiling@phi_{phi:.2f}")
        .with_notes(f"phi_pack = {phi:.2f} (unknown U01)")
        for phi in phi_range
    ]
    values = [r.value for r in results if r.value is not None]
    spread = max(values) - min(values) if values else 0.0
    results.append(
        Result.estimated(
            "range_ceiling_spread",
            spread,
            "mm",
            source="ESR_SIMULATOR_SPEC.md §5.5",
            notes=(
                "spread of the readable range across the plausible packing band; "
                "this much of the measuring range is undetermined until phi_pack is "
                "measured (unknown U01)",
            ),
        )
    )
    return ResultSet(
        title=f"RANGE CEILING SENSITIVITY — {cone.tube_id or 'cone'}, Hct {hematocrit:.2f}",
        results=tuple(results),
        notes=(
            "phi_pack has never been measured in this project. Every range figure "
            "rides on it (unknown U01).",
        ),
    )


# --------------------------------------------------------------- optional features


def stepped_upper_cone(
    cone: Cone,
    w_mm: float = 0.30,
    upper_angle_offset_deg: float = -2.0,
    probe_height_mm: float = 3.0,
) -> ResultSet:
    """Stepped upper cone above the blood line — addendum §B, approved design.

    ``theta_upper = theta_inner + upper_angle_offset``. With an offset of exactly zero
    the clearance above the blood line is *constant* at ``clearance_working + w``; with
    the recommended -2 degrees it converges slightly upward, so the **minimum** over the
    probed height is what gets reported and flagged.
    """
    check_positive("w_mm", w_mm)
    a = cone.x_bl_mm
    clearance_working = cone.clearance_radial(a)
    d_inner_bl = cone.d_inner(a)
    d_upper_base = d_inner_bl - 2.0 * w_mm
    if d_upper_base <= 0:
        raise ValueError(
            f"step width {w_mm} mm consumes the whole inner cone "
            f"(D_inner at blood line = {d_inner_bl:.4f} mm)"
        )
    theta_upper_deg = cone.theta_i_deg + upper_angle_offset_deg
    check_angle_deg("theta_upper_deg", theta_upper_deg)
    theta_upper = math.radians(theta_upper_deg)
    tip_height = (d_upper_base / 2.0) / math.tan(theta_upper)

    def clearance_above(t: float) -> float:
        """Clearance ``t`` mm above the blood line (addendum §B)."""
        d_o = cone.d_outer(a - t)
        d_u = d_upper_base - 2.0 * t * math.tan(theta_upper)
        return (d_o - max(0.0, d_u)) / 2.0

    probe = min(probe_height_mm, tip_height, a - 1e-6)
    samples = [clearance_above(t) for t in _linspace(0.0, probe, 41)]
    c_min, c_tip = min(samples), samples[-1]
    constant = abs(upper_angle_offset_deg) < 1e-12

    land_area = math.pi * ((d_inner_bl / 2.0) ** 2 - (d_upper_base / 2.0) ** 2)

    results = [
        _exact("step_width_w", w_mm, "mm", cone),
        _exact("theta_upper", theta_upper_deg, "deg", cone),
        _exact("d_upper_base", d_upper_base, "mm", cone),
        _exact("tip_height_above_bloodline", tip_height, "mm", cone),
        _exact("land_area", land_area, "mm^2", cone,
               notes=("film anchor area; w affects only this and manufacturability — "
                      "smaller is better (addendum §B)",)),
        _exact("clearance_working", clearance_working, "mm", cone),
        _exact("clearance_above_at_step", samples[0], "mm", cone),
        _exact("clearance_above_min", c_min, "mm", cone,
               notes=(f"minimum over {probe:.2f} mm above the blood line",)),
        _exact("clearance_above_at_probe_top", c_tip, "mm", cone),
        _exact("clearance_convergence", samples[0] - c_tip, "mm", cone),
    ]

    notes = [
        "approved design: T070 with Gen-B and this step gave a flat blood line after "
        "repeated mixing AND good mixing, with water and 2-week-old blood (addendum §B)",
        "Gibbs pinning strength is independent of w: pinning_range = 90 - theta_cone",
        "the edge must be sharp: a 0.1 mm fillet costs ~0.1 mm of level certainty, a "
        "0.3 mm fillet removes the edge altogether (addendum §B)",
        "concave corner at the foot of the upper cone is 98-104 deg; the wicking "
        "condition (half-angle + contact angle < 90 deg) is NOT met, so no wicking",
    ]
    if constant:
        notes.append(
            "theta_upper == theta_inner: clearance above the blood line is CONSTANT at "
            f"clearance_working + w = {clearance_working + w_mm:.4f} mm"
        )

    # R01, no constriction (spec §9.1).
    ok = c_min >= clearance_working - 1e-12
    results.append(
        _exact("no_constriction_R01", bool(ok), "", cone,
               flags=() if ok else ("R01_VIOLATED",),
               notes=(f"min clearance above ({c_min:.4f}) vs working "
                      f"({clearance_working:.4f}) mm",)))

    return ResultSet(
        title=f"STEPPED UPPER CONE — {cone.tube_id or 'cone'}",
        results=tuple(results),
        notes=tuple(notes),
    )


def counterbore(cone: Cone, d_cb_mm: float) -> ResultSet:
    """Coaxial counterbore from the top — spec §2.4a."""
    check_positive("d_cb_mm", d_cb_mm)
    x_cb_bottom = (d_cb_mm / 2.0) / math.tan(cone.theta_o)
    depth = x_cb_bottom - cone.x_bl_mm
    clearance_at_bl = (d_cb_mm - cone.d_inner(cone.x_bl_mm)) / 2.0
    return ResultSet(
        title=f"COUNTERBORE D={d_cb_mm:.3f} — {cone.tube_id or 'cone'}",
        results=(
            _exact("d_counterbore", d_cb_mm, "mm", cone),
            _exact("x_counterbore_bottom", x_cb_bottom, "mm", cone),
            _exact("depth_below_bloodline", depth, "mm", cone),
            _exact("clearance_at_bloodline", clearance_at_bl, "mm", cone),
        ),
        notes=("spec §2.4a",),
    )


def shift_inner_cone(cone: Cone, shift_mm: float) -> ResultSet:
    """Move the inner cone down by ``shift_mm`` — spec §4.4.

    ``d(clearance)/d(shift) = tan(theta_inner)``, exactly. The warning the spec
    attaches is reproduced here: a shift changes the gap over the **whole** length, so
    the volume rises steeply and the tube leaves the iso-volume family.
    """
    shifted = replace(cone, delta_mm=cone.delta_mm + shift_mm, tube_id=
                      f"{cone.tube_id}+shift{shift_mm:g}" if cone.tube_id else "")
    a = cone.x_bl_mm
    v0, v1 = cone.volume_numeric(), shifted.volume_numeric()
    results = [
        _exact("shift", shift_mm, "mm", cone),
        _exact("d_clearance_d_shift", math.tan(cone.theta_i), "mm/mm", cone,
               source="ESR_SIMULATOR_SPEC.md §4.4"),
        _exact("clearance_before", cone.clearance_radial(a), "mm", cone),
        _exact("clearance_after", shifted.clearance_radial(a), "mm", cone),
        _exact("gap_before", cone.gap_perpendicular(a), "mm", cone),
        _exact("gap_after", shifted.gap_perpendicular(a), "mm", cone),
        _exact("gap_after_at_base", shifted.gap_perpendicular(shifted.x_base_mm),
               "mm", cone),
        _exact("volume_before", v0, "mm^3", cone),
        _exact("volume_after", v1, "mm^3", cone),
        _exact("volume_change_pct", 100.0 * (v1 - v0) / v0, "%", cone),
    ]
    return ResultSet(
        title=f"INNER-CONE SHIFT {shift_mm:+.3f} mm — {cone.tube_id or 'cone'}",
        results=tuple(results),
        notes=(
            "WARNING (spec §4.4): a shift changes the gap over the whole length, so "
            "the volume rises steeply and the tube leaves the iso-volume family.",
            "This is one arm of the decisive collinearity experiment of spec §9.3.",
        ),
    )


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n < 2:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]
