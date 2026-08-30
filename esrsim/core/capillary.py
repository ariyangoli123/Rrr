"""Capillarity, blood-line level, Gibbs pinning and the mixing criterion.

This module runs **threshold tests, not dynamic simulation** (spec §4). Nothing here
integrates an equation of motion, and nothing here predicts whether mixing will work —
it evaluates the criteria the project has actually established, and refuses to answer
where the evidence contradicts itself.

Three claims are tiered very differently and must not be confused:

* **Gibbs pinning** (§4.2) — geometry plus a literature hysteresis range. Confirmed
  experimentally, and the margin is about 2x, so it is reported as ESTIMATED.
* **Blood-line unevenness** (§4.1) — two competing models, *neither of which fits*.
  Both are computed, both are returned, and no winner is selected: HYPOTHESIS.
* **Mixing** (§4.3) — a topological continuity criterion, the only causally confirmed
  model. Its clearance threshold is contradictory, so queries inside the disputed band
  return UNKNOWN rather than a guess.

References
----------
ESR_SIMULATOR_SPEC.md §4, v1.1 addendum §B, §C, §F.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ..registry import by_id
from ..tiers import (
    REFUTED_HYPOTHESIS,
    UNRESOLVED_CONTRADICTION,
    Result,
    ResultSet,
    Tier,
)
from ..units import check_positive
from .fluid import Fluid, K_constant, capillary_length, load_fluid, mixing_validation_note
from .geometry import Cone

__all__ = [
    "BLOOD_HYSTERESIS_DEG",
    "MIXING_EVIDENCE",
    "MIXING_DISPUTED_BAND",
    "REFUTED_HYPOTHESES",
    "MixingGeometry",
    "blood_line_unevenness",
    "gibbs_pinning",
    "meniscus_bulge_tolerance",
    "mixing_criterion",
    "refuted_hypothesis_note",
    "max_step_width",
    "fill_resistance",
    "capillary_rise",
    "capillary_report",
]

#: Contact-angle hysteresis of blood on printed resin, degrees (spec §4.2).
BLOOD_HYSTERESIS_DEG = (20.0, 40.0)

#: The four mixing observations of spec §4.3. clearance (mm) -> passed.
MIXING_EVIDENCE: dict[float, bool] = {
    0.696: True,    # TAPER + 6.7 mm drill — weak pass
    0.720: True,    # T070, new design
    0.766: False,   # T060 + 7.0 mm drill — the contradiction
    0.914: True,    # T090
}

#: No threshold may be fitted inside this band (spec §4.3, unknown U02).
MIXING_DISPUTED_BAND = (0.69, 0.77)

#: Nominal working threshold quoted by design rule R02, inside the disputed band.
MIXING_NOMINAL_THRESHOLD = 0.72

#: Hypotheses the project tested and refuted (spec §4.3, addendum §F). Implemented
#: nowhere; listed so that tests can assert they are not used in decisions.
REFUTED_HYPOTHESES: tuple[str, ...] = (
    "eotvos_bubble_passage",       # bubbles move freely even at gap 0.6
    "symmetry_breaking_grooves",   # several grooves had no effect
    "pinned_liquid_bridge_stability",  # did not break with a groove
)


# ------------------------------------------------------- blood-line unevenness


def blood_line_unevenness(
    cone: Cone,
    fluid: Fluid | str = "blood_fresh",
    *,
    eccentricity_mm: float = 0.035,
) -> ResultSet:
    """Both competing unevenness models, side by side, with no winner picked.

    Spec §4.1::

        model A (eccentricity) : delta_h = K * 2e / (d^2 - e^2)
        model B (hysteresis)   : delta_h = K * delta_cos_theta / d

    Neither reproduces the observations, which are a **step** (gap 0.90 -> ~0 mm,
    gap 0.60 -> ~4 mm, gap 0.50 -> ~3-4 mm) while both models are continuous. The spec
    requires the program to flag that inconsistency explicitly, so the returned set
    carries ``UNRESOLVED_CONTRADICTION`` and the deviation of each model from the
    nearest observation.

    The spec records one piece of evidence favouring model B: the level was flat on the
    first fill and only broke up after several mixes, which hysteresis explains (it has
    no restoring force) and eccentricity does not (it would act from the start). That is
    reported as a note. It is not enough to select a model, so both stay HYPOTHESIS.
    """
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    k = K_constant(f)
    d = cone.gap_perpendicular(cone.x_bl_mm)
    check_positive("gap", d)
    e = eccentricity_mm

    if d <= e:
        model_a: Result = Result.unknown(
            "delta_h_model_A_eccentricity",
            why=f"eccentricity {e} mm is not smaller than the gap {d:.4f} mm; the "
                "inner cone would touch the outer wall and the annulus closes",
            experiment="measure the actual concentricity of the assembled part",
        )
    else:
        model_a = k.derive(
            "delta_h_model_A_eccentricity",
            k.value * 2.0 * e / (d * d - e * e), "mm",
            source="ESR_SIMULATOR_SPEC.md §4.1 model A",
            notes=(f"eccentricity e = {e:.4f} mm (CALIBRATED, spec §4.1)",),
        )
        model_a = Result(
            name=model_a.name, value=model_a.value, unit="mm", tier=Tier.HYPOTHESIS,
            source=model_a.source, flags=model_a.flags, notes=model_a.notes,
        )

    model_b = Result(
        name="delta_h_model_B_hysteresis",
        value=k.value * f.delta_cos_theta.value / d,
        unit="mm",
        tier=Tier.HYPOTHESIS,
        source="ESR_SIMULATOR_SPEC.md §4.1 model B",
        flags=f.delta_cos_theta.flags,
        notes=f.delta_cos_theta.notes
        + (f"delta_cos_theta = {f.delta_cos_theta.value:.3f}",),
    )

    observed = _nearest_observation(d)
    results: list[Result] = [
        k.rename("K"),
        Result.exact("gap_at_bloodline", d, "mm"),
        model_a,
        model_b,
    ]
    notes = [
        "TWO COMPETING MODELS, NO WINNER. Spec §4.1 requires both to be reported.",
        "Observations are a STEP (gap 0.90 -> ~0 mm, 0.60 -> ~4 mm, 0.50 -> ~3-4 mm); "
        "BOTH models are continuous, so neither explains the data.",
        "Evidence favouring model B: the level was flat on the first fill and broke up "
        "only after several mixes. Hysteresis has no restoring force; eccentricity "
        "would have acted from the start.",
        "Against model B: delta_cos_theta is itself contradictory — 0.41 from the "
        "lamella experiment against 0.14 from the cone experiment (unknown U07).",
    ]
    if observed is not None:
        obs_gap, obs_value = observed
        results.append(
            Result.calibrated(
                "observed_unevenness", obs_value, "mm",
                source="ESR_SIMULATOR_SPEC.md §4.1 observations",
                notes=(f"nearest observation, at gap {obs_gap:.2f} mm",),
            )
        )
        for model in (model_a, model_b):
            if model.value is None:
                continue
            results.append(
                Result(
                    name=f"{model.name}_minus_observed",
                    value=model.value - obs_value,
                    unit="mm",
                    tier=Tier.HYPOTHESIS,
                    flags=(UNRESOLVED_CONTRADICTION,),
                    notes=("model minus nearest observation",),
                )
            )

    unknown = by_id("U08")
    return ResultSet(
        title=f"BLOOD-LINE UNEVENNESS — {cone.tube_id or 'cone'}, {f.label}",
        results=tuple(results),
        notes=tuple(notes + [f"unknown {unknown.id}: {unknown.how_to_resolve.strip()}"]),
    )


#: Observations of spec §4.1: nominal gap -> observed unevenness, mm.
_UNEVENNESS_OBSERVATIONS: dict[float, float] = {0.90: 0.0, 0.60: 4.0, 0.50: 3.5}


def _nearest_observation(gap_mm: float) -> tuple[float, float] | None:
    if not _UNEVENNESS_OBSERVATIONS:
        return None
    best = min(_UNEVENNESS_OBSERVATIONS, key=lambda g: abs(g - gap_mm))
    if abs(best - gap_mm) > 0.12:
        return None
    return best, _UNEVENNESS_OBSERVATIONS[best]


# --------------------------------------------------------------- Gibbs pinning


def gibbs_pinning(
    cone: Cone, hysteresis_deg: tuple[float, float] = BLOOD_HYSTERESIS_DEG
) -> ResultSet:
    """Pinning range on the sharp edge — spec §4.2, confirmed experimentally.

    ``gibbs_range = 90 - theta_cone`` (degrees). The interface stays pinned to the edge
    as long as the required contact-angle excursion is inside that range. Blood's
    hysteresis is 20-40 degrees, so the margin is roughly 2x.

    Addendum §B: pinning strength is independent of the step width ``w``.
    """
    theta_deg = cone.theta_o_deg
    gibbs = 90.0 - theta_deg
    hyst_lo, hyst_hi = hysteresis_deg
    margin = gibbs / hyst_hi

    passes = gibbs > hyst_hi
    return ResultSet(
        title=f"GIBBS PINNING — {cone.tube_id or 'cone'}",
        results=(
            Result.exact("cone_half_angle", theta_deg, "deg"),
            Result.exact("gibbs_range", gibbs, "deg",
                         source="ESR_SIMULATOR_SPEC.md §4.2"),
            Result.estimated("blood_hysteresis_range", hyst_hi, "deg",
                             source="literature, 20-40 deg",
                             notes=(f"range {hyst_lo:.0f}-{hyst_hi:.0f} deg",)),
            Result.estimated("pinning_margin", margin, "x",
                             source="ESR_SIMULATOR_SPEC.md §4.2",
                             notes=("gibbs_range / worst-case hysteresis",)),
            Result.estimated("R03_gibbs_range", bool(passes), "",
                             flags=() if passes else ("R03_VIOLATED",),
                             source="ESR_SIMULATOR_SPEC.md §9.1 R03"),
        ),
        notes=(
            "confirmed experimentally (spec §4.2)",
            "independent of the step width w (addendum §B)",
            "the edge must stay sharp: a 0.1 mm fillet costs ~0.1 mm of level "
            "certainty; a 0.3 mm fillet removes the edge altogether",
        ),
    )


def meniscus_bulge_tolerance(cone: Cone) -> ResultSet:
    """How much fill-volume error a pinned meniscus can absorb — spec §4.2.

    ::

        h_bulge_max ~ clearance / 2
        V_bulge     ~ A(x_bl) * (2/3) * h_bulge_max

    For T090 this is about 4.5 mm^3, i.e. 0.22 percent of the column. That is far below
    the 30 mm^3 that a 2 mm level shift represents, which is exactly why addendum §E
    concludes that **filling must be volumetric, not "pour to the line"**.
    """
    clearance = cone.clearance_radial(cone.x_bl_mm)
    a_bl = cone.area(cone.x_bl_mm)
    h_bulge = clearance / 2.0
    v_bulge = a_bl * (2.0 / 3.0) * h_bulge
    total = cone.volume_numeric()
    return ResultSet(
        title=f"MENISCUS BULGE TOLERANCE — {cone.tube_id or 'cone'}",
        results=(
            Result.exact("h_bulge_max", h_bulge, "mm",
                         source="ESR_SIMULATOR_SPEC.md §4.2"),
            Result.exact("V_bulge", v_bulge, "mm^3"),
            Result.exact("tolerance_pct_of_volume", 100.0 * v_bulge / total, "%"),
            Result.exact("level_shift_equivalent", v_bulge / a_bl, "mm",
                         notes=("the level error this bulge can absorb before the "
                                "meniscus de-pins",)),
        ),
        notes=(
            "far below the ~30 mm^3 that a 2 mm level shift represents, so filling "
            "must be VOLUMETRIC, not 'pour to the line' (addendum §E)",
        ),
    )


# ------------------------------------------------------------ mixing criterion


@dataclass(frozen=True, slots=True)
class MixingGeometry:
    """The three facts the mixing criterion needs (spec §4.3)."""

    guide_surface_continuous: bool
    clearance_working_mm: float
    clearance_above_min_mm: float
    description: str = ""


def mixing_criterion(geometry: MixingGeometry) -> ResultSet:
    """The confirmed topological-continuity criterion — spec §4.3.

    ::

        MIXING PASSES IFF
          (1) a continuous solid path runs from the working annulus to the air space
          (2) no constriction: min(clearance_above) >= clearance_working
          (3) clearance_working >= threshold

    Conditions (1) and (2) are checkable and confirmed. Condition (3) is **not
    resolvable**: 0.696 mm passed while 0.766 mm failed. Inside
    ``MIXING_DISPUTED_BAND`` this function returns UNKNOWN rather than a prediction,
    exactly as spec §4.3 requires.

    This is a criterion, not a simulation. Spec §F: *"the program must not simulate
    mixing."*
    """
    c_work = geometry.clearance_working_mm
    c_above = geometry.clearance_above_min_mm
    lo, hi = MIXING_DISPUTED_BAND

    cond1 = Result.calibrated(
        "guide_surface_continuous", bool(geometry.guide_surface_continuous), "",
        source="ESR_SIMULATOR_SPEC.md §4.3 condition 1",
        notes=("confirmed: a 3 mm rod restored mixing; the stepped upper cone is "
               "better than the rod (addendum §F)",),
    )
    cond2_ok = c_above >= c_work - 1e-12
    cond2 = Result.exact(
        "no_constriction", bool(cond2_ok), "",
        source="ESR_SIMULATOR_SPEC.md §4.3 condition 2",
        flags=() if cond2_ok else ("R01_VIOLATED",),
        notes=(f"min clearance above = {c_above:.4f} mm, working = {c_work:.4f} mm",),
    )

    results: list[Result] = [
        Result.exact("clearance_working", c_work, "mm"),
        Result.exact("clearance_above_min", c_above, "mm"),
        cond1,
        cond2,
    ]

    if lo <= c_work <= hi:
        cond3: Result = Result.unknown(
            "mixing_threshold_met",
            why=(
                f"clearance {c_work:.4f} mm falls inside the disputed band "
                f"[{lo}, {hi}] mm. The evidence is contradictory: "
                f"{_evidence_string()}. 0.696 mm passed but 0.766 mm failed, and with "
                "four points and entangled variables NO threshold may be fitted "
                "(unknown U02)."
            ),
            experiment=by_id("U02").how_to_resolve.strip(),
            flags=(UNRESOLVED_CONTRADICTION,),
        )
        verdict: Result = Result.unknown(
            "mixing_passes",
            why=(
                "condition (3) is undecidable at this clearance; conditions (1) and (2) "
                f"are {'met' if (cond1.value and cond2_ok) else 'NOT met'}"
            ),
            experiment=by_id("U02").how_to_resolve.strip(),
            flags=(UNRESOLVED_CONTRADICTION,),
        )
    else:
        met = c_work > hi
        cond3 = Result.calibrated(
            "mixing_threshold_met", bool(met), "",
            source="ESR_SIMULATOR_SPEC.md §4.3 condition 3",
            fitted_range={"clearance_mm": (0.512, 0.914)},
            notes=(
                f"clearance {c_work:.4f} mm is "
                f"{'above' if met else 'below'} the disputed band [{lo}, {hi}] mm",
                f"evidence: {_evidence_string()}",
            ),
        ).enforce_range(clearance_mm=c_work)
        all_ok = bool(cond1.value) and cond2_ok and met
        verdict = cond3.derive(
            "mixing_passes", all_ok, "",
            others=(cond1, cond2),
            source="ESR_SIMULATOR_SPEC.md §4.3",
        )

    results += [cond3, verdict, mixing_validation_note()]

    return ResultSet(
        title=f"MIXING CRITERION — {geometry.description or 'geometry'}",
        results=tuple(results),
        notes=(
            "This is a threshold test on a confirmed topological criterion, not a "
            "simulation. The program does not simulate mixing (addendum §F).",
            "REFUTED and not used in any decision: "
            + ", ".join(REFUTED_HYPOTHESES),
            f"evidence base: {_evidence_string()}",
        ),
    )


def _evidence_string() -> str:
    return ", ".join(
        f"{c:.3f}->{'pass' if ok else 'FAIL'}" for c, ok in sorted(MIXING_EVIDENCE.items())
    )


def refuted_hypothesis_note(name: str) -> Result:
    """A refuted hypothesis, kept only as a historical reference (spec §4.3)."""
    if name not in REFUTED_HYPOTHESES:
        raise KeyError(f"{name!r} is not in the refuted list: {REFUTED_HYPOTHESES}")
    reasons = {
        "eotvos_bubble_passage": "REFUTED: bubbles move freely even at a 0.6 mm gap",
        "symmetry_breaking_grooves": "REFUTED: several grooves had no experimental effect",
        "pinned_liquid_bridge_stability": "REFUTED: the bridge did not break with a groove",
    }
    return Result.unknown(
        f"refuted::{name}",
        why=reasons[name] + ". Retained as a historical reference only; it must not "
        "enter any engineering decision (spec §4.3).",
        experiment="none — this hypothesis has already been tested and rejected",
        flags=(REFUTED_HYPOTHESIS,),
    )


# ----------------------------------------------------------- fill and capillarity


def fill_resistance(cone: Cone, reference_gap_mm: float = 0.90) -> Result:
    """Filling resistance relative to the d = 0.90 reference — spec §4.5.

    ``fill_resistance ~ 1/d^3``. EXACT as a scaling; it is a ratio of geometries, not
    a pressure.
    """
    d = cone.gap_perpendicular(cone.x_bl_mm)
    return Result.exact(
        "fill_resistance", (reference_gap_mm / d) ** 3, "x",
        source="ESR_SIMULATOR_SPEC.md §4.5",
        notes=(f"relative to a {reference_gap_mm:.2f} mm gap; scaling only, not a "
               "pressure",),
    )


def capillary_rise(cone: Cone, fluid: Fluid | str = "blood_fresh") -> Result:
    """``capillary_rise = K / d`` in mm — spec §4.5."""
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    k = K_constant(f)
    d = cone.gap_perpendicular(cone.x_bl_mm)
    return k.derive(
        "capillary_rise", k.value / d, "mm",
        source="ESR_SIMULATOR_SPEC.md §4.5",
        notes=(f"gap {d:.4f} mm",),
    )


def max_step_width(fluid: Fluid | str = "blood_fresh") -> Result:
    """Upper bound on the step width ``w`` — addendum §B: half the capillary length."""
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    lc = capillary_length(f)
    return lc.derive(
        "w_max", lc.value / 2.0, "mm",
        source="v1.1 addendum §B",
        notes=(
            f"half the capillary length ({lc.value:.3f} mm)",
            "spec §9.1 R04 quotes 1.20 mm as the ceiling while the addendum derives "
            "Lc/2 = 1.16 mm; the derived value is used and the nominal one noted",
        ),
    )


def capillary_report(
    cone: Cone, fluid: Fluid | str = "blood_fresh"
) -> ResultSet:
    """Everything capillary for one cone and fluid, in one block."""
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    uneven = blood_line_unevenness(cone, f)
    pinning = gibbs_pinning(cone)
    bulge = meniscus_bulge_tolerance(cone)
    return ResultSet(
        title=f"CAPILLARY — {cone.tube_id or 'cone'}, {f.label}",
        results=tuple(uneven) + tuple(pinning) + tuple(bulge)
        + (fill_resistance(cone), capillary_rise(cone, f), max_step_width(f)),
        notes=uneven.notes + pinning.notes + bulge.notes,
    )
