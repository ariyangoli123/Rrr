"""Design-space explorer — spec §9.2, and the interactive sweep the build prompt asks for.

The prompt asks for an explorer that *"sweeps gap, angle, volume and column length while
showing speed, range, clearance, blood-line unevenness, fill resistance and ICSH
feasibility together"*. Showing them together is the point: every one of these
quantities can be improved at another's expense, and a sweep that reports speed alone
will happily recommend a tube that cannot be filled, cannot be mixed, or cannot support
the ICSH study.

Each swept row carries the tier of its weakest column, so a row that looks attractive
because its mixing verdict is UNKNOWN cannot be mistaken for a row that passes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from scipy.optimize import brentq

from ..calibration.validate import feasibility_check
from ..core import capillary as cap
from ..core import geometry as geo
from ..core.fluid import Fluid, load_fluid
from ..core.geometry import Cone
from ..core.kinetics import E_saturated, descent
from ..tiers import Result, ResultSet, Tier
from .rules import evaluate_rules

__all__ = ["SweepRow", "design_point", "sweep", "solve_theta_for_volume", "compare"]


def solve_theta_for_volume(
    gap_mm: float,
    volume_mm3: float = 2000.0,
    length_mm: float = 50.0,
    *,
    mouth_diameter_mm: float = geo.GEN_A_MOUTH_DIAMETER_MM,
    blood_line_offset_mm: float = geo.GEN_A_BLOOD_LINE_OFFSET_MM,
) -> float:
    """Half-angle that gives ``volume_mm3`` at this gap, under the family's own rule.

    This is the construction the six library tubes were cut to, so a swept design is
    directly comparable with them.
    """
    def volume_of(theta_deg: float) -> float:
        th = math.radians(theta_deg)
        x_bl = (mouth_diameter_mm / 2.0) / math.tan(th) + blood_line_offset_mm
        delta = gap_mm / math.sin(th)
        a, b = x_bl, x_bl + length_mm
        return math.pi * math.tan(th) ** 2 * (
            delta * (b**2 - a**2) - delta**2 * (b - a)
        )

    return float(brentq(lambda t: volume_of(t) - volume_mm3, 2.0, 45.0, xtol=1e-10))


def _cone_for(
    gap_mm: float, volume_mm3: float, length_mm: float, theta_deg: float | None = None
) -> Cone:
    theta = theta_deg if theta_deg is not None else solve_theta_for_volume(
        gap_mm, volume_mm3, length_mm
    )
    th = math.radians(theta)
    x_bl = (geo.GEN_A_MOUTH_DIAMETER_MM / 2.0) / math.tan(th) \
        + geo.GEN_A_BLOOD_LINE_OFFSET_MM
    return Cone(
        theta_o_deg=theta,
        theta_i_deg=theta,
        delta_mm=gap_mm / math.sin(th),
        x_bl_mm=x_bl,
        length_mm=length_mm,
        generation="B",
        tube_id=f"gap{gap_mm:.3f}_th{theta:.3f}",
    )


@dataclass(frozen=True, slots=True)
class SweepRow:
    """One design point, with every competing consideration side by side."""

    label: str
    gap_mm: float
    theta_deg: float
    volume_mm3: float
    length_mm: float
    results: ResultSet

    @property
    def tier(self) -> Tier:
        return self.results.tier

    def get(self, name: str) -> Any:
        r = self.results.get(name)
        return None if r is None else r.value


def design_point(
    cone: Cone,
    fluid: Fluid | str = "blood_fresh",
    *,
    hematocrit: float = 0.45,
    phi_pack: float = 0.90,
    readout_min: float = 15.0,
    esr_reference: float = 30.0,
    step_w_mm: float = 0.30,
) -> ResultSet:
    """Speed, range, clearance, unevenness, fill resistance and feasibility, together."""
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    # The blood-line gap is the tightest point, so it is what filling and capillarity
    # see. Speed uses the mean gap on a tapered cone, matching how the TAPER entry was
    # reduced in the spec's own E table. Both are reported so the row is internally
    # consistent rather than mixing the two silently.
    gap_bl = cone.gap_perpendicular(cone.x_bl_mm)
    gap_for_speed = gap_bl
    if not cone.is_constant_gap:
        gap_for_speed = 0.5 * (gap_bl + cone.gap_perpendicular(cone.x_base_mm))

    ceiling = geo.range_ceiling(cone, hematocrit, phi_pack)
    speed = E_saturated(cone.theta_o_deg, cone.length_mm, gap_for_speed)
    clearance = cone.clearance_radial(cone.x_bl_mm)

    uneven = cap.blood_line_unevenness(cone, f)
    uneven_values = [
        r.value for r in uneven
        if r.name.startswith("delta_h_model")
        and not r.name.endswith("_minus_observed") and r.value is not None
    ]

    step = geo.stepped_upper_cone(cone, w_mm=step_w_mm)
    mixing = cap.mixing_criterion(
        cap.MixingGeometry(
            True, clearance, step["clearance_above_min"].value, cone.tube_id
        )
    )

    def reading(esr: float) -> float:
        return descent(cone, esr, hematocrit, phi_pack=phi_pack,
                       t_max_min=readout_min).height(readout_min)

    feas = feasibility_check(reading, ceiling, label=f"fixed-time {readout_min:g} min")

    results = [
        Result.exact("gap", gap_bl, "mm",
                     notes=("at the blood line, the tightest point",)),
        Result.exact("theta", cone.theta_o_deg, "deg"),
        Result.exact("volume", cone.volume_numeric(), "mm^3"),
        Result.exact("column_length", cone.length_mm, "mm"),
        Result.exact("clearance", clearance, "mm"),
        speed.rename("speed_E"),
        ceiling.rename("range_ceiling"),
        Result(
            name="bloodline_unevenness_worst",
            value=max(uneven_values) if uneven_values else 0.0,
            unit="mm",
            tier=Tier.HYPOTHESIS,
            source="spec §4.1, worse of two non-fitting models",
            notes=("neither model fits the observations (unknown U08)",),
        ),
        cap.fill_resistance(cone).rename("fill_resistance"),
        mixing["mixing_passes"].rename("mixing"),
        feas["icsh_2017_feasible"].rename("icsh_feasible")
        if feas.get("icsh_2017_feasible") is not None else
        Result.unknown("icsh_feasible", why="feasibility not evaluable",
                       experiment="supply a readout mapping"),
        Result.exact("d_base", cone.d_outer(cone.x_base_mm), "mm",
                     notes=("DERIVED, never a manufacturing input (addendum §A)",)),
    ]
    if not cone.is_constant_gap:
        results.insert(
            1,
            Result.exact("gap_mean_for_speed", gap_for_speed, "mm",
                         notes=("tapered cone: speed uses the mean gap, filling and "
                                "capillarity use the blood-line gap",)),
        )
    return ResultSet(
        title=f"DESIGN POINT — {cone.tube_id or 'cone'}",
        results=tuple(results),
        notes=(
            "Speed, range, clearance, unevenness, filling and ICSH feasibility are "
            "shown together because they trade against each other.",
            "A row whose mixing verdict is UNKNOWN has NOT passed mixing.",
        ),
    )


def sweep(
    param: str,
    values: Sequence[float],
    *,
    gap_mm: float = 0.70,
    volume_mm3: float = 2000.0,
    length_mm: float = 50.0,
    theta_deg: float | None = None,
    fluid: Fluid | str = "blood_fresh",
    **kwargs,
) -> tuple[SweepRow, ...]:
    """Sweep one of ``gap``, ``theta``, ``volume`` or ``length`` — spec §9.2.

    Examples
    --------
    ``sweep("theta", range(8, 21))`` at fixed gap and volume, or the key experiment of
    spec §9.2, ``sweep("gap", [0.5 ... 1.5])`` at fixed angle and column length.
    """
    param = param.lower()
    if param not in ("gap", "theta", "volume", "length"):
        raise ValueError(
            f"param must be one of gap, theta, volume, length; got {param!r}"
        )

    rows: list[SweepRow] = []
    for value in values:
        g, v, L, th = gap_mm, volume_mm3, length_mm, theta_deg
        if param == "gap":
            g = float(value)
            # Spec §9.2's key experiment fixes the angle and sweeps the gap, which
            # means the volume is free to move. That is the point of the experiment.
            th = theta_deg
        elif param == "theta":
            th = float(value)
        elif param == "volume":
            v = float(value)
            th = None
        else:
            L = float(value)
            th = None

        cone = _cone_for(g, v, L, th)
        results = design_point(cone, fluid, **kwargs)
        rows.append(
            SweepRow(
                label=f"{param}={value:g}",
                gap_mm=cone.gap_perpendicular(cone.x_bl_mm),
                theta_deg=cone.theta_o_deg,
                volume_mm3=cone.volume_numeric(),
                length_mm=cone.length_mm,
                results=results,
            )
        )
    return tuple(rows)


def compare(tube_ids: Iterable[str], **kwargs) -> tuple[SweepRow, ...]:
    """Design points for named library tubes, side by side."""
    rows = []
    for tube_id in tube_ids:
        cone = geo.from_library(tube_id)
        rows.append(
            SweepRow(
                label=tube_id,
                gap_mm=cone.gap_perpendicular(cone.x_bl_mm),
                theta_deg=cone.theta_o_deg,
                volume_mm3=cone.volume_numeric(),
                length_mm=cone.length_mm,
                results=design_point(cone, **kwargs),
            )
        )
    return tuple(rows)


def render_sweep(rows: Sequence[SweepRow]) -> str:
    """A tier-annotated table. Every column shows its tier; there is no quiet mode."""
    cols = [
        ("gap", "gap", "mm", 6, 3),
        ("theta", "theta", "deg", 7, 3),
        ("V", "volume", "mm3", 8, 1),
        ("clear", "clearance", "mm", 7, 4),
        ("E", "speed_E", "x", 6, 2),
        ("range", "range_ceiling", "mm", 7, 2),
        ("uneven", "bloodline_unevenness_worst", "mm", 7, 2),
        ("fill", "fill_resistance", "x", 6, 2),
    ]
    head = f"{'design':<16}" + "".join(f"{c[0]:>{c[3]}}" for c in cols) \
        + f"{'mixing':>10}{'ICSH':>7}{'tier':>15}"
    lines = [head, "-" * len(head)]
    for row in rows:
        cells = ""
        for _label, name, _unit, width, digits in cols:
            v = row.get(name)
            cells += f"{'—':>{width}}" if v is None else f"{v:>{width}.{digits}f}"
        mixing = row.get("mixing")
        icsh = row.get("icsh_feasible")
        lines.append(
            f"{row.label:<16}{cells}"
            f"{_yn(mixing):>10}{_yn(icsh):>7}{row.tier.name:>15}"
        )
    lines.append("")
    lines.append("mixing/ICSH: '?' means UNDECIDABLE on the present evidence, which is "
                 "NOT a pass.")
    return "\n".join(lines)


def _yn(value: Any) -> str:
    if value is None:
        return "?"
    return "yes" if value else "no"
