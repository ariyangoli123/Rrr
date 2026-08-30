"""Method-comparison statistics and the ICSH acceptance criteria.

All EXACT: these are definitions and standard estimators, not models of anything.

The one thing that is easy to get wrong here, and that the build prompt calls out
explicitly, is the acceptance limit. It is **5 mm**, from the ICSH 2011 review. The
6 mm figure that circulates belongs to EN ISO 13079:2011 Annex B, which is a tube
*contamination* test and has nothing to do with comparing methods. Passing 6.0 to
:func:`icsh_2011_check` raises.

References
----------
Jou et al., ICSH review, Int J Lab Hematol 2011;33(2):125-132.
Kratz et al., ICSH 2017, Int J Lab Hematol 2017;39(5):448-457.
Passing & Bablok, J Clin Chem Clin Biochem 1983;21:709-720.
Bland & Altman, Lancet 1986;1:307-310.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
from scipy import stats

from ..registry import assert_no_six_millimetre
from ..tiers import Result, ResultSet, Tier

__all__ = [
    "ICSH_2011_LIMIT_MM",
    "ICSH_2017_MIN_SAMPLES",
    "ICSH_2017_MIN_PER_TERTILE",
    "ICSH_2017_INTERVAL_MM",
    "ICSH_2017_VERIFICATION_SAMPLES",
    "passing_bablok",
    "bland_altman",
    "icsh_2011_check",
    "icsh_2017_design_check",
    "feasibility_check",
    "validation_report",
]

#: ICSH 2011: 95 percent of differences within 5 mm. NOT 6 mm.
ICSH_2011_LIMIT_MM = 5.0
ICSH_2011_MIN_FRACTION = 0.95

#: ICSH 2017 study design (spec §8.1).
ICSH_2017_MIN_SAMPLES = 60
ICSH_2017_MIN_PER_TERTILE = 20
ICSH_2017_INTERVAL_MM = (2.0, 120.0)
ICSH_2017_VERIFICATION_SAMPLES = 30


# ------------------------------------------------------------- Passing-Bablok


def passing_bablok(
    x: Sequence[float], y: Sequence[float], confidence: float = 0.95
) -> ResultSet:
    """Passing-Bablok regression with distribution-free confidence intervals.

    Non-parametric: the slope is the shifted median of all pairwise slopes, which makes
    it robust to error in *both* variables — the right choice when comparing two
    measurement methods, neither of which is a reference without error.

    Returns slope, intercept and their confidence intervals. A slope interval that
    excludes 1, or an intercept interval that excludes 0, indicates proportional or
    constant bias respectively.
    """
    xa, ya = _as_pair(x, y)
    n = len(xa)
    if n < 3:
        return ResultSet(
            f"PASSING-BABLOK (n = {n})",
            (
                Result.unknown(
                    "pb_slope",
                    why=f"only {n} paired observations; the estimator needs at least 3",
                    experiment="collect more paired measurements",
                ),
            ),
        )

    slopes: list[float] = []
    for i in range(n - 1):
        dx = xa[i + 1 :] - xa[i]
        dy = ya[i + 1 :] - ya[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            s = np.where(dx != 0, dy / dx, np.nan)
        slopes.extend(v for v in s.tolist() if v is not None and math.isfinite(v))

    if not slopes:
        return ResultSet(
            f"PASSING-BABLOK (n = {n})",
            (
                Result.unknown(
                    "pb_slope",
                    why="every pair of x values is identical; no slope is defined",
                    experiment="measure samples spanning a range of ESR values",
                ),
            ),
        )

    arr = np.sort(np.asarray(slopes, dtype=float))
    # Passing-Bablok offset: shift past the slopes below -1 (Passing & Bablok 1983).
    n_neg = int(np.sum(arr < -1.0))
    k = len(arr)
    slope = float(_shifted_median(arr, n_neg))
    intercept = float(np.median(ya - slope * xa))

    # Passing & Bablok 1983 §2.2: M1 = (N - C)/2, M2 = N - M1 + 1, both 1-indexed and
    # both shifted past the K slopes below -1; subtract 1 for 0-indexed arrays.
    z = stats.norm.ppf(1.0 - (1.0 - confidence) / 2.0)
    c = z * math.sqrt(n * (n - 1) * (2 * n + 5) / 18.0)
    m1 = int(round((k - c) / 2.0))
    m2 = k - m1 + 1
    lo_i = max(0, min(k - 1, m1 + n_neg - 1))
    hi_i = max(0, min(k - 1, m2 + n_neg - 1))
    slope_lo, slope_hi = float(arr[min(lo_i, hi_i)]), float(arr[max(lo_i, hi_i)])
    int_lo = float(np.median(ya - slope_hi * xa))
    int_hi = float(np.median(ya - slope_lo * xa))

    r = float(np.corrcoef(xa, ya)[0, 1]) if n > 1 else float("nan")

    proportional = not (slope_lo <= 1.0 <= slope_hi)
    constant = not (int_lo <= 0.0 <= int_hi)

    results = [
        Result.exact("pb_slope", slope, "",
                     source="Passing & Bablok 1983",
                     notes=(f"{confidence:.0%} CI [{slope_lo:.4f}, {slope_hi:.4f}]",)),
        Result.exact("pb_slope_ci_low", slope_lo, ""),
        Result.exact("pb_slope_ci_high", slope_hi, ""),
        Result.exact("pb_intercept", intercept, "mm",
                     notes=(f"{confidence:.0%} CI [{int_lo:.4f}, {int_hi:.4f}]",)),
        Result.exact("pb_intercept_ci_low", int_lo, "mm"),
        Result.exact("pb_intercept_ci_high", int_hi, "mm"),
        Result.exact("pearson_r", r, ""),
        Result.exact("proportional_bias", bool(proportional), "",
                     flags=("PROPORTIONAL_BIAS",) if proportional else (),
                     notes=("slope CI excludes 1: PROPORTIONAL BIAS, a fail condition "
                            "(spec §8.2)",) if proportional else ()),
        Result.exact("constant_bias", bool(constant), "",
                     notes=("intercept CI excludes 0: constant bias",) if constant else ()),
    ]
    return ResultSet(
        title=f"PASSING-BABLOK (n = {n})",
        results=tuple(results),
        notes=("non-parametric; tolerates error in both methods",),
    )


def _shifted_median(sorted_slopes: np.ndarray, offset: int) -> float:
    k = len(sorted_slopes)
    if k % 2 == 1:
        return float(sorted_slopes[min(k - 1, (k - 1) // 2 + offset)])
    i = min(k - 1, k // 2 - 1 + offset)
    j = min(k - 1, k // 2 + offset)
    return float((sorted_slopes[i] + sorted_slopes[j]) / 2.0)


# --------------------------------------------------------------- Bland-Altman


def bland_altman(
    x: Sequence[float], y: Sequence[float], confidence: float = 0.95
) -> ResultSet:
    """Bland-Altman agreement: bias, limits of agreement, proportional-bias test.

    The proportional-bias test regresses the difference on the mean; a slope
    significantly different from zero means the bias is not constant across the
    measuring interval, which ICSH 2017 forbids (spec §8.1).
    """
    xa, ya = _as_pair(x, y)
    n = len(xa)
    if n < 2:
        return ResultSet(
            f"BLAND-ALTMAN (n = {n})",
            (
                Result.unknown(
                    "ba_bias",
                    why=f"only {n} paired observations",
                    experiment="collect more paired measurements",
                ),
            ),
        )
    diff = ya - xa
    mean = (ya + xa) / 2.0
    bias = float(np.mean(diff))
    sd = float(np.std(diff, ddof=1)) if n > 1 else 0.0
    z = stats.norm.ppf(1.0 - (1.0 - confidence) / 2.0)
    loa_lo, loa_hi = bias - z * sd, bias + z * sd

    if n >= 3 and float(np.ptp(mean)) > 0:
        reg = stats.linregress(mean, diff)
        p_value = float(reg.pvalue)
        prop_slope = float(reg.slope)
    else:
        p_value, prop_slope = float("nan"), 0.0

    proportional = math.isfinite(p_value) and p_value < 0.05
    results = [
        Result.exact("ba_bias", bias, "mm", source="Bland & Altman 1986"),
        Result.exact("ba_sd_of_differences", sd, "mm"),
        Result.exact("ba_loa_lower", loa_lo, "mm",
                     notes=(f"{confidence:.0%} limits of agreement",)),
        Result.exact("ba_loa_upper", loa_hi, "mm"),
        Result.exact("ba_proportional_slope", prop_slope, "mm/mm"),
    ]
    if math.isfinite(p_value):
        results.append(
            Result.exact(
                "ba_proportional_bias_pvalue", p_value, "",
                flags=("PROPORTIONAL_BIAS",) if proportional else (),
                notes=("difference-vs-mean slope differs from zero: PROPORTIONAL BIAS, "
                       "a fail condition (spec §8.2)",) if proportional else (),
            )
        )
    else:
        results.append(
            Result.unknown(
                "ba_proportional_bias_pvalue",
                why="the means do not span a range, so bias against level cannot be tested",
                experiment="include samples across the whole 2-120 mm interval",
            )
        )
    return ResultSet(
        title=f"BLAND-ALTMAN (n = {n})",
        results=tuple(results),
        notes=("ICSH 2017 requires the bias to be CONSTANT across the interval",),
    )


# ----------------------------------------------------------------- ICSH checks


def icsh_2011_check(
    differences: Sequence[float], limit_mm: float = ICSH_2011_LIMIT_MM
) -> ResultSet:
    """ICSH 2011: 95 percent of differences within 5 mm.

    Raises
    ------
    ValueError
        If ``limit_mm`` is 6.0. That number is from EN ISO 13079:2011 Annex B (tube
        contamination testing) and is not a method-comparison criterion.
    """
    assert_no_six_millimetre(limit_mm, "icsh_2011_check(limit_mm=...)")
    d = np.abs(np.asarray(list(differences), dtype=float))
    if d.size == 0:
        return ResultSet(
            "ICSH 2011",
            (
                Result.unknown(
                    "pct_within_limit",
                    why="no differences supplied",
                    experiment="run the comparison against a Westergren reference",
                ),
            ),
        )
    pct = float(np.mean(d <= limit_mm))
    passes = pct >= ICSH_2011_MIN_FRACTION
    return ResultSet(
        title=f"ICSH 2011 ACCEPTANCE (n = {d.size})",
        results=(
            Result.exact("acceptance_limit", limit_mm, "mm",
                         source="Jou et al., Int J Lab Hematol 2011;33(2):125-132",
                         notes=("5 mm, NOT 6 mm. The 6 mm figure is EN ISO "
                                "13079:2011 Annex B, tube contamination testing.",)),
            Result.exact("pct_within_limit", 100.0 * pct, "%"),
            Result.exact("max_difference", float(d.max()), "mm"),
            Result.exact("passes", bool(passes), "",
                         flags=() if passes else ("ICSH_2011_FAIL",),
                         notes=(f"needs >= {100 * ICSH_2011_MIN_FRACTION:.0f}%",)),
        ),
    )


@dataclass(frozen=True, slots=True)
class StudySample:
    """One sample in a method-comparison study."""

    reference_esr: float
    hematocrit: float | None = None
    resolved: bool = True


def icsh_2017_design_check(
    samples: Sequence[StudySample | float],
    interval_mm: tuple[float, float] = ICSH_2017_INTERVAL_MM,
) -> ResultSet:
    """ICSH 2017 study-design requirements — spec §8.1.

    * at least 60 samples spanning 2-120 mm
    * at least 20 in each third of the interval
    * haematocrit within the reference interval
    * bias constant across the interval
    """
    values = [
        s.reference_esr if isinstance(s, StudySample) else float(s) for s in samples
    ]
    arr = np.asarray(values, dtype=float)
    lo, hi = interval_mm
    edges = (lo, lo + (hi - lo) / 3.0, lo + 2.0 * (hi - lo) / 3.0, hi)
    tertiles = [
        int(np.sum((arr >= edges[i]) & (arr < edges[i + 1] if i < 2 else arr <= edges[3])))
        for i in range(3)
    ]
    in_interval = int(np.sum((arr >= lo) & (arr <= hi)))
    n_total = int(arr.size)

    enough_total = in_interval >= ICSH_2017_MIN_SAMPLES
    enough_each = all(t >= ICSH_2017_MIN_PER_TERTILE for t in tertiles)
    passes = enough_total and enough_each

    results = [
        Result.exact("n_total", n_total, "samples",
                     source="Kratz et al., Int J Lab Hematol 2017;39(5):448-457"),
        Result.exact("n_in_interval", in_interval, "samples",
                     notes=(f"interval {lo:g}-{hi:g} mm",)),
        Result.exact("n_tertile_1", tertiles[0], "samples",
                     notes=(f"{edges[0]:.0f}-{edges[1]:.0f} mm",)),
        Result.exact("n_tertile_2", tertiles[1], "samples",
                     notes=(f"{edges[1]:.0f}-{edges[2]:.0f} mm",)),
        Result.exact("n_tertile_3", tertiles[2], "samples",
                     notes=(f"{edges[2]:.0f}-{edges[3]:.0f} mm",)),
        Result.exact("passes", bool(passes), "",
                     flags=() if passes else ("ICSH_2017_DESIGN_FAIL",),
                     notes=(f"needs >= {ICSH_2017_MIN_SAMPLES} total and "
                            f">= {ICSH_2017_MIN_PER_TERTILE} per tertile",)),
    ]
    if isinstance(next(iter(samples), None), StudySample):
        unresolved = [s for s in samples if isinstance(s, StudySample) and not s.resolved]
        if unresolved:
            results.append(
                Result.exact(
                    "n_unresolved", len(unresolved), "samples",
                    flags=("UNRESOLVED_SAMPLES",),
                    notes=("these samples produced no distinguishable reading; they "
                           "cannot count toward a tertile",),
                )
            )
    return ResultSet(
        title=f"ICSH 2017 STUDY DESIGN (n = {n_total})",
        results=tuple(results),
        notes=(
            "haematocrit must be within the reference interval",
            "bias must be constant across the interval — proportional bias is a fail",
            "mathematical conversion to a Westergren equivalent is permitted",
            f"laboratory verification needs >= {ICSH_2017_VERIFICATION_SAMPLES} samples",
        ),
    )


# ------------------------------------------------------------ feasibility check


def feasibility_check(
    reading_of: Callable[[float], float | None],
    range_ceiling: Result,
    *,
    resolution_mm: float = 0.5,
    interval_mm: tuple[float, float] = ICSH_2017_INTERVAL_MM,
    label: str = "readout",
) -> ResultSet:
    """Can the ICSH 2017 study actually be run with this geometry and readout?

    Build prompt: *"can >= 20 samples above ESR 60 be resolved? With a 30-35 mm range
    ceiling and fixed-time readout the answer is no, because ESR 60 and ESR 120 both
    sit at the ceiling. The tool must say so plainly rather than emit numbers that
    hide it."*

    Two samples count as resolvable only if their readings differ by more than
    ``resolution_mm`` — the practical limit of reading a boundary against a scale.

    Parameters
    ----------
    reading_of
        ESR -> reading; ``None`` where the strategy gives no reading.
    resolution_mm
        Smallest reliably distinguishable difference in the reading. 0.5 mm.
    """
    lo, hi = interval_mm
    top_third_lo = lo + 2.0 * (hi - lo) / 3.0

    grid = [lo + (hi - lo) * i / 200.0 for i in range(201)]
    readings = [(e, reading_of(e)) for e in grid]

    # A distinct "bin" is a stretch of ESR whose reading differs from its neighbour
    # by more than the resolution: that is how many values the readout can tell apart.
    def distinguishable(sub: list[tuple[float, float | None]]) -> int:
        usable = [(e, v) for e, v in sub if v is not None]
        if not usable:
            return 0
        count, last = 1, usable[0][1]
        for _e, v in usable[1:]:
            if abs(v - last) > resolution_mm:
                count += 1
                last = v
        return count

    top = [(e, v) for e, v in readings if e >= top_third_lo]
    n_top = distinguishable(top)
    n_all = distinguishable(readings)

    top_usable = [(e, v) for e, v in top if v is not None]
    top_span = (
        abs(top_usable[-1][1] - top_usable[0][1]) if len(top_usable) >= 2 else 0.0
    )

    can_run = n_top >= ICSH_2017_MIN_PER_TERTILE

    results: list[Result] = [
        range_ceiling.rename("range_ceiling"),
        Result.exact("readout_resolution", resolution_mm, "mm",
                     notes=("smallest reliably distinguishable reading difference",)),
        Result.exact("distinguishable_levels_full_interval", n_all, "levels",
                     notes=(f"over ESR {lo:g}-{hi:g} mm/h",)),
        Result.exact("distinguishable_levels_top_tertile", n_top, "levels",
                     notes=(f"over ESR {top_third_lo:.0f}-{hi:g} mm/h",)),
        Result.exact("reading_span_top_tertile", top_span, "mm",
                     notes=(f"total reading movement from ESR {top_third_lo:.0f} to "
                            f"{hi:g}",)),
    ]

    if can_run:
        results.append(
            Result.exact(
                "icsh_2017_feasible", True, "",
                notes=(f"{n_top} distinguishable levels in the top tertile, "
                       f"at least {ICSH_2017_MIN_PER_TERTILE} needed",),
            )
        )
        notes = (f"{label}: the top tertile is resolvable.",)
    else:
        results.append(
            Result.exact(
                "icsh_2017_feasible", False, "",
                flags=("ICSH_2017_INFEASIBLE",),
                notes=(
                    f"only {n_top} distinguishable levels between ESR "
                    f"{top_third_lo:.0f} and {hi:g} mm/h, but ICSH 2017 needs at least "
                    f"{ICSH_2017_MIN_PER_TERTILE} samples in that tertile — and samples "
                    "that read the same are not 20 measurements, they are one "
                    "measurement repeated",
                    f"the whole top tertile moves the reading by {top_span:.2f} mm",
                ),
            )
        )
        results.append(
            Result.unknown(
                "esr_above_top_tertile",
                why=(
                    f"With a range ceiling of "
                    f"{range_ceiling.format_value()} mm and this readout, ESR "
                    f"{top_third_lo:.0f} and ESR {hi:g} both sit at or near the "
                    "ceiling. The study cannot be run as designed: the readings do not "
                    "separate, so no amount of sampling fills the top tertile."
                ),
                experiment=(
                    "switch to a time-to-threshold readout, which is monotonic and "
                    "cannot saturate; or read earlier than the ceiling is reached; or "
                    "raise the ceiling, which first requires measuring phi_pack "
                    "(unknown U01)"
                ),
                flags=("ICSH_2017_INFEASIBLE",),
            )
        )
        notes = (
            f"{label}: THE ICSH 2017 STUDY CANNOT BE RUN with this combination.",
            "This is a design verdict, not a warning to be tuned away.",
        )

    return ResultSet(
        title=f"ICSH 2017 FEASIBILITY — {label}",
        results=tuple(results),
        notes=notes,
    )


# --------------------------------------------------------------------- report


def validation_report(
    reference: Sequence[float],
    device: Sequence[float],
    samples: Sequence[StudySample | float] | None = None,
) -> ResultSet:
    """Everything ICSH asks for, in one block."""
    ref, dev = _as_pair(reference, device)
    diffs = (dev - ref).tolist()
    pb = passing_bablok(ref, dev)
    ba = bland_altman(ref, dev)
    acc = icsh_2011_check(diffs)
    design = icsh_2017_design_check(samples if samples is not None else ref.tolist())

    proportional = bool(pb.get("proportional_bias") and pb["proportional_bias"].value) or (
        ba.get("ba_proportional_bias_pvalue") is not None
        and ba["ba_proportional_bias_pvalue"].tier is not Tier.UNKNOWN
        and "PROPORTIONAL_BIAS" in ba["ba_proportional_bias_pvalue"].flags
    )
    overall = bool(
        acc["passes"].value and design["passes"].value and not proportional
    )

    merged = tuple(pb) + tuple(ba) + tuple(acc) + tuple(design)
    sub_titles = tuple(b.title for b in (pb, ba, acc, design))
    verdict = Result.exact(
        "icsh_overall_pass", overall, "",
        flags=() if overall else ("ICSH_FAIL",),
        notes=(
            "proportional bias is an automatic fail (spec §8.2)" if proportional else "",
        ) if proportional else (),
    )
    return ResultSet(
        title="ICSH VALIDATION",
        results=merged + (verdict,),
        notes=sub_titles + (
            "acceptance limit is 5 mm (ICSH 2011). 6 mm belongs to EN ISO 13079:2011 "
            "Annex B, tube contamination testing, and is refused by this package.",
        ),
    )


def _as_pair(x: Sequence[float], y: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    xa = np.asarray(list(x), dtype=float)
    ya = np.asarray(list(y), dtype=float)
    if xa.shape != ya.shape:
        raise ValueError(f"paired series must match: {xa.shape} vs {ya.shape}")
    mask = np.isfinite(xa) & np.isfinite(ya)
    return xa[mask], ya[mask]
