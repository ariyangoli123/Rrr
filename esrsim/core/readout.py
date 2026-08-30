"""Readout modes, monotonicity screening and the level-shift error budget.

Everything here is EXACT: it is error propagation through known geometry plus a scan of
a *supplied* readout mapping. This module deliberately does not know how heights are
produced — it takes a callable — so it stays independent of the calibrated kinetics and
can screen any proposed readout strategy, including ones this project has not modelled.

References
----------
ESR_SIMULATOR_SPEC.md §7 (readout and error propagation), v1.1 addendum §E.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

from ..registry import measured
from ..tiers import (
    MODEL_RECORD_MISMATCH,
    NON_MONOTONIC,
    SATURATED,
    Result,
    ResultSet,
    Tier,
)
from ..units import check_positive
from .geometry import Cone

__all__ = [
    "ReadoutMode",
    "ReadingMapping",
    "detect_non_monotonic",
    "accept_readout",
    "area_ratio",
    "level_shift_error",
    "esr_error_from_level_shift",
    "saturation_check",
    "error_budget",
    "readout_report",
    "SATURATION_FRACTION",
]

#: Spec §7.3: at or above this fraction of the range ceiling, refuse to return a number.
SATURATION_FRACTION = 0.98

#: A readout mapping: ESR (mm/h) -> the instrument's reading. ``None`` means the
#: strategy produces no reading at that ESR (boundary not yet readable, or saturated).
ReadingMapping = Callable[[float], float | None]


class ReadoutMode(Enum):
    """The four readout strategies of spec §7.1.

    ``DELTA_H`` is implemented *only to demonstrate its failure* (spec §7.1): the height
    difference over a fixed window is non-monotonic in ESR, so two different ESRs give
    the same reading. :func:`detect_non_monotonic` finds this automatically.
    """

    FIXED_TIME_HEIGHT = "height at a fixed time"
    TIME_TO_THRESHOLD = "time to cross a threshold"
    CONDITIONAL = "two-stage conditional readout"
    DELTA_H = "height difference over a window"

    @property
    def is_rejected(self) -> bool:
        return self is ReadoutMode.DELTA_H

    @property
    def rejection_reason(self) -> str:
        if not self.is_rejected:
            return ""
        return (
            "spec §7.1: Delta-h over a fixed window is NON-MONOTONIC in ESR. Two "
            "different sedimentation rates produce the same reading, and unlike "
            "saturation the failure is invisible in the output."
        )


# ------------------------------------------------------------------ monotonicity


@dataclass(frozen=True, slots=True)
class Collision:
    """Two ESR values that a mapping cannot tell apart."""

    esr_a: float
    esr_b: float
    reading_a: float
    reading_b: float

    def describe(self, unit: str = "mm") -> str:
        return (
            f"ESR {self.esr_a:g} -> {self.reading_a:.2f} {unit} and "
            f"ESR {self.esr_b:g} -> {self.reading_b:.2f} {unit}: "
            "two rates, one reading"
        )


def detect_non_monotonic(
    mapping: ReadingMapping,
    esr_grid: Sequence[float] | None = None,
    *,
    direction: str = "increasing",
    tolerance: float = 1e-9,
    name: str = "readout_monotonic",
) -> Result:
    """Scan a readout mapping for non-monotonicity in ESR.

    Build prompt: *"scan a proposed readout mapping for non-monotonicity in ESR and
    refuse to accept it ... Silent non-monotonicity is more dangerous than saturation
    because it is invisible in the output."*

    Parameters
    ----------
    mapping
        ESR (mm/h) -> reading, or ``None`` where the strategy yields no reading.
    esr_grid
        ESR values to probe. Defaults to 1..120 mm/h in 0.5 steps, which is the ICSH
        2017 measuring interval (spec §8.1) plus the low tail where the haze phase
        does its damage.
    direction
        ``"increasing"`` or ``"decreasing"`` — the sense the reading *should* have.

    Returns
    -------
    Result
        ``value`` is ``True`` when the mapping is monotonic. When it is not, the result
        is flagged ``NON_MONOTONIC`` and the colliding pairs are listed in the notes.
        EXACT: this is a scan of the supplied mapping, not a model of anything.
    """
    if esr_grid is None:
        esr_grid = [1.0 + 0.5 * i for i in range(239)]  # 1.0 .. 120.0
    if direction not in ("increasing", "decreasing"):
        raise ValueError("direction must be 'increasing' or 'decreasing'")

    probed = [(e, mapping(e)) for e in esr_grid]
    usable = [(e, v) for e, v in probed if v is not None]
    if len(usable) < 2:
        return Result.unknown(
            name,
            why="the mapping produced fewer than two readings over the probed ESR grid",
            experiment="widen the ESR grid, or fix the readout strategy so it produces "
            "readings across the measuring interval",
        )

    sign = 1.0 if direction == "increasing" else -1.0
    violations: list[Collision] = []
    for (e0, v0), (e1, v1) in zip(usable, usable[1:]):
        if sign * (v1 - v0) < -tolerance:
            violations.append(Collision(e0, e1, v0, v1))

    # Report the widest-separated pair that shares a reading: the most damaging case.
    collisions = _find_collisions(usable, tolerance=max(0.05, tolerance))

    n_unreadable = len(probed) - len(usable)
    notes = [
        f"probed {len(probed)} ESR values over "
        f"[{min(esr_grid):g}, {max(esr_grid):g}] mm/h",
    ]
    if n_unreadable:
        notes.append(f"{n_unreadable} of them produced no reading at all")

    if not violations:
        return Result.exact(
            name, True, "",
            source="ESR_SIMULATOR_SPEC.md §7.1",
            notes=tuple(notes + ["monotonic over the probed interval"]),
        )

    worst = max(violations, key=lambda c: abs(c.reading_a - c.reading_b))
    notes.append(f"{len(violations)} monotonicity violations")
    notes.append(f"steepest reversal: {worst.describe()}")
    for c in collisions[:4]:
        notes.append(f"COLLISION: {c.describe()}")
    return Result.exact(
        name, False, "",
        source="ESR_SIMULATOR_SPEC.md §7.1",
        flags=(NON_MONOTONIC,),
        notes=tuple(notes),
    )


def _find_collisions(
    usable: Sequence[tuple[float, float]], tolerance: float
) -> list[Collision]:
    """Pairs of well-separated ESR values that produce indistinguishable readings."""
    out: list[Collision] = []
    n = len(usable)
    for i in range(n):
        e0, v0 = usable[i]
        for j in range(n - 1, i, -1):
            e1, v1 = usable[j]
            if e1 - e0 < 2.0:
                break
            if abs(v1 - v0) <= tolerance:
                out.append(Collision(e0, e1, v0, v1))
                break
    out.sort(key=lambda c: c.esr_b - c.esr_a, reverse=True)
    return out


def accept_readout(
    mapping: ReadingMapping,
    mode: ReadoutMode,
    esr_grid: Sequence[float] | None = None,
) -> Result:
    """Accept or refuse a readout strategy.

    A non-monotonic mapping is refused: the result is UNKNOWN, so no ESR number can be
    produced through it at all. That is the point — a strategy that cannot be inverted
    must not silently return values.
    """
    mono = detect_non_monotonic(mapping, esr_grid, name=f"monotonic[{mode.name}]")
    if mono.value is True:
        return Result.exact(
            f"readout_accepted[{mode.name}]", True, "",
            source="ESR_SIMULATOR_SPEC.md §7.1",
            notes=(f"{mode.value}: monotonic, invertible",) + mono.notes,
        )
    return Result.unknown(
        f"readout_accepted[{mode.name}]",
        why=(
            f"{mode.value} is not invertible: the reading is not monotonic in ESR, so a "
            "reading cannot be mapped back to a single rate. "
            + (mode.rejection_reason or "")
            + " Detail: " + "; ".join(n for n in mono.notes if n.startswith("COLLISION"))
        ).strip(),
        experiment=(
            "choose a monotonic strategy (time-to-threshold is monotonic and cannot "
            "saturate), or restrict the reported interval to a stretch where this "
            "mapping is monotonic and refuse readings outside it"
        ),
        flags=(NON_MONOTONIC,),
    )


# ------------------------------------------------------------------ error budget


def area_ratio(cone: Cone, h_interface_mm: float) -> Result:
    """``r = A(x_bl) / A(x_bl + h)`` — spec §7.2. EXACT."""
    r = cone.area(cone.x_bl_mm) / cone.area_at_height(h_interface_mm)
    return Result.exact(
        "area_ratio_r", r, "",
        source="ESR_SIMULATOR_SPEC.md §7.2",
        notes=(f"interface at h = {h_interface_mm:.2f} mm below the blood line",),
    )


def level_shift_error(
    cone: Cone, h_interface_mm: float, delta_level_mm: float
) -> ResultSet:
    """Height error from a mean-level shift of ``delta_level_mm`` — spec §7.2.

    ::

        error(reading from a fixed mark)   = -Delta * r
        error(reading from the real surface) = +Delta * (1 - r)

    Addendum §E adds: since ``r ~ 0.4-0.6``, *"both methods give roughly half the level
    shift as error"*. That holds at a mid-column interface. Near the range ceiling
    ``r`` falls to 0.18-0.31, and the two methods stop being equivalent — reading from a
    fixed mark becomes the better of the two. This function reports the actual ``r`` at
    the actual interface depth rather than the quoted band, and says so when they differ.
    """
    r_res = area_ratio(cone, h_interface_mm)
    r = r_res.value
    from_mark = -delta_level_mm * r
    from_surface = +delta_level_mm * (1.0 - r)

    notes = [
        f"level shift Delta = {delta_level_mm:+.3f} mm",
        f"r = {r:.3f} at this interface depth",
    ]
    if not (0.4 <= r <= 0.6):
        notes.append(
            f"r = {r:.3f} is OUTSIDE the 0.4-0.6 band quoted in addendum §E, so the "
            "'both methods cost about half the shift' rule does not apply here: "
            f"fixed mark costs {abs(from_mark):.3f} mm, real surface "
            f"{abs(from_surface):.3f} mm"
        )

    return ResultSet(
        title=f"LEVEL-SHIFT ERROR — interface at {h_interface_mm:.2f} mm",
        results=(
            r_res,
            Result.exact("error_from_mark", from_mark, "mm",
                         source="ESR_SIMULATOR_SPEC.md §7.2"),
            Result.exact("error_from_surface", from_surface, "mm",
                         source="ESR_SIMULATOR_SPEC.md §7.2"),
            Result.exact("error_worse_of_the_two",
                         max(abs(from_mark), abs(from_surface)), "mm",
                         source="ESR_SIMULATOR_SPEC.md §7.2"),
        ),
        notes=tuple(notes),
    )


def esr_error_from_level_shift(
    cone: Cone,
    h_interface_mm: float,
    delta_level_mm: float,
    dh_dESR: float,
    *,
    dh_dESR_result: Result | None = None,
) -> Result:
    """``ESR_error = |h_error| / (dh/dESR)`` — spec §7.2.

    The sensitivity ``dh/dESR`` is *not* geometry: it comes from the kinetics model or
    from the recorded table, so the result inherits that tier. Pass the sensitivity as a
    :class:`~esrsim.tiers.Result` via ``dh_dESR_result`` to get correct propagation.
    """
    if dh_dESR <= 0:
        return Result.unknown(
            "esr_error",
            why=(
                f"sensitivity dh/dESR = {dh_dESR:.4g} mm per mm/h is not positive; the "
                "reading no longer responds to the rate, so a level error maps to an "
                "unbounded ESR error"
            ),
            experiment="read earlier, or use a tube with more range at this ESR",
        )
    err_set = level_shift_error(cone, h_interface_mm, delta_level_mm)
    h_err = err_set["error_worse_of_the_two"].value
    value = abs(h_err) / dh_dESR

    if dh_dESR_result is None:
        return Result.exact(
            "esr_error", value, "mm/h",
            source="ESR_SIMULATOR_SPEC.md §7.2",
            notes=(f"height error {h_err:.3f} mm / sensitivity {dh_dESR:.3f}",),
        )
    return dh_dESR_result.derive(
        "esr_error", value, "mm/h",
        others=(err_set["error_worse_of_the_two"],),
        source="ESR_SIMULATOR_SPEC.md §7.2",
        notes=(f"height error {h_err:.3f} mm / sensitivity {dh_dESR:.3f}",),
    )


def saturation_check(h_mm: float, range_ceiling: Result) -> Result:
    """Spec §7.3: at or above 98 percent of the ceiling, return no number.

    Returns an UNKNOWN result when saturated — the strongest available statement is
    "at least this much", and the spec forbids emitting a value that looks like a
    measurement.
    """
    if range_ceiling.tier is Tier.UNKNOWN or range_ceiling.value is None:
        return range_ceiling.derive("saturated", None, "")
    ceiling = float(range_ceiling.value)
    threshold = SATURATION_FRACTION * ceiling
    if h_mm >= threshold:
        return Result.unknown(
            "esr_from_reading",
            why=(
                f"SATURATED: the boundary is at {h_mm:.2f} mm, at or past "
                f"{SATURATION_FRACTION:.0%} of the {ceiling:.2f} mm range ceiling. "
                "Every rate above this point gives the same reading, so the reading "
                "carries no information about how much higher the true rate is."
            ),
            experiment=(
                "read earlier, switch to a time-to-threshold readout (monotonic and "
                "unsaturable), or increase the range ceiling — which means measuring "
                "phi_pack first, since the ceiling rides on it (unknown U01)"
            ),
            flags=(SATURATED,),
        )
    headroom = 100.0 * (1.0 - h_mm / ceiling)
    return range_ceiling.derive(
        "saturation_headroom", headroom, "%",
        source="ESR_SIMULATOR_SPEC.md §7.3",
        notes=(f"boundary at {h_mm:.2f} mm of a {ceiling:.2f} mm ceiling",),
    )


# --------------------------------------------------------------- recorded table


def recorded_sensitivity(sample_id: str = "sample_001") -> ResultSet:
    """The recorded dh/dESR table — spec §7.2 / addendum §E, T090 at 15 min."""
    rec = measured(sample_id)["readout_sensitivity"]
    results = [
        Result.calibrated(
            f"dh_dESR@ESR{entry['esr_mm_h']:g}", float(entry["dh_dESR"]), "mm/(mm/h)",
            source=f"{sample_id}: spec §7.2 validation table",
            fitted_range={"esr_mm_h": (13.0, 40.0), "readout_time_min": (15.0, 15.0)},
            notes=("n = 1 sample",),
        )
        for entry in rec["entries"]
    ]
    return ResultSet(
        title=f"RECORDED READOUT SENSITIVITY — {rec['tube']} at "
              f"{rec['readout_time_min']} min",
        results=tuple(results),
        notes=(
            "n = 1. One sample, one tube, one session.",
            "The record falls monotonically from 0.83 to 0.28 as ESR rises, which the "
            "spec attributes to approaching the range ceiling (spec §7.2).",
        ),
    )


def error_budget(
    cone: Cone,
    range_ceiling: Result,
    sensitivity: Sequence[tuple[float, float, float]],
    level_shifts_mm: Sequence[float] = (1.0, 2.0, 3.0, 4.0),
    *,
    design_target_mm: float = 2.0,
    compare_with_record: bool = True,
) -> ResultSet:
    """Full readout error budget — spec §7.2, addendum §E.

    Parameters
    ----------
    sensitivity
        ``(esr, h_interface_mm, dh_dESR)`` triples, from the kinetics model or from the
        recorded table.
    design_target_mm
        Addendum §E design target for the level shift. Default 2.0 mm.

    Notes
    -----
    Addendum §E: *"systematic bias is absorbed entirely by calibration; only random
    scatter eats the budget."* The two are reported separately below, because a report
    that lumps them overstates the problem.
    """
    results: list[Result] = []
    for esr, h_iface, dh in sensitivity:
        for delta in level_shifts_mm:
            err = esr_error_from_level_shift(cone, h_iface, delta, dh)
            results.append(err.rename(f"esr_error@ESR{esr:g}_delta{delta:g}mm"))

    a_bl = cone.area(cone.x_bl_mm)
    results.append(
        Result.exact(
            "fill_volume_for_design_target", design_target_mm * a_bl, "mm^3",
            source="v1.1 addendum §E",
            notes=(
                f"a {design_target_mm:.1f} mm level shift is "
                f"{design_target_mm * a_bl:.1f} mm^3 at the blood line "
                f"(A = {a_bl:.2f} mm^2)",
                "filling must be VOLUMETRIC, not 'pour to the line'",
            ),
        )
    )
    total_v = cone.volume_numeric()
    results.append(
        Result.exact(
            "fill_tolerance_pct_of_volume",
            100.0 * design_target_mm * a_bl / total_v, "%",
            source="v1.1 addendum §E",
        )
    )

    notes = [
        "systematic bias is absorbed by calibration; only RANDOM scatter eats the "
        "budget. Pipette repeatability matters more than its absolute accuracy "
        "(addendum §E).",
        f"design target: level shift below {design_target_mm:.1f} mm.",
    ]

    if compare_with_record:
        rec = recorded_sensitivity()
        mismatches: list[str] = []
        for esr, _h, dh_model in sensitivity:
            key = f"dh_dESR@ESR{esr:g}"
            recorded = rec.get(key)
            if recorded is None or recorded.value is None:
                continue
            rel = abs(dh_model - recorded.value) / recorded.value
            results.append(
                Result.calibrated(
                    f"dh_dESR_recorded@ESR{esr:g}", recorded.value, "mm/(mm/h)",
                    source=recorded.source, notes=("n = 1 sample",),
                )
            )
            if rel > 0.15:
                mismatches.append(
                    f"ESR {esr:g}: model {dh_model:.3f} vs record {recorded.value:.3f} "
                    f"({100 * rel:.0f}% apart)"
                )
        if mismatches:
            results.append(
                Result.calibrated(
                    "model_vs_record_sensitivity", float(len(mismatches)), "points",
                    source="unknowns.yaml U10",
                    flags=(MODEL_RECORD_MISMATCH,),
                    notes=tuple(mismatches)
                    + (
                        "The model is NOT tuned onto the record. Both columns are "
                        "printed and the disagreement is unknown U10.",
                    ),
                )
            )
            notes.append(
                "MODEL/RECORD MISMATCH on readout sensitivity — see unknown U10. The "
                "recorded sensitivity falls monotonically with ESR; the volumetric "
                "model's rises then falls. They agree only near ESR 30."
            )

    return ResultSet(
        title=f"READOUT ERROR BUDGET — {cone.tube_id or 'cone'}",
        results=tuple(results),
        notes=tuple(notes),
    )


def readout_report(
    cone: Cone,
    mode: ReadoutMode,
    mapping: ReadingMapping,
    range_ceiling: Result,
    esr_grid: Sequence[float] | None = None,
) -> ResultSet:
    """Screen one readout strategy end to end: monotonicity, then acceptance."""
    mono = detect_non_monotonic(mapping, esr_grid, name=f"monotonic[{mode.name}]")
    accepted = accept_readout(mapping, mode, esr_grid)
    results = [
        Result.exact("readout_mode", mode.value, "",
                     source="ESR_SIMULATOR_SPEC.md §7.1"),
        mono,
        accepted,
        range_ceiling.rename("range_ceiling"),
    ]
    notes = []
    if mode.is_rejected:
        notes.append(mode.rejection_reason)
        notes.append(
            "This mode is implemented ONLY to demonstrate its failure (spec §7.1). It "
            "must not be used to report a result."
        )
    return ResultSet(
        title=f"READOUT SCREEN — {mode.name} on {cone.tube_id or 'cone'}",
        results=tuple(results),
        notes=tuple(notes),
    )
