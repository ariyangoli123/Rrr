"""Design rules R01-R10 — ESR_SIMULATOR_SPEC.md §9.1.

Each rule is a function returning a :class:`~esrsim.tiers.Result` whose value is the
pass/fail boolean, with the margin in the notes. Rules inherit the tier of whatever
they test: R01 is EXACT geometry, R02 is UNKNOWN inside the disputed clearance band,
R06 rides on the assumed packing fraction.

A rule that cannot be decided returns UNKNOWN. It never guesses a pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from ..core import capillary as cap
from ..core import geometry as geo
from ..core.fluid import Fluid, load_fluid
from ..core.geometry import Cone
from ..registry import by_id
from ..tiers import UNRESOLVED_CONTRADICTION, Result, ResultSet, Tier

__all__ = [
    "RULES",
    "evaluate_rules",
    "r01_no_constriction",
    "r02_mixing_threshold",
    "r03_gibbs_range",
    "r04_step_width",
    "r05_volume_target",
    "r06_range_vs_saturation",
    "r07_fill_resistance",
    "r08_bloodline_flatness",
    "r09_print_tolerance",
    "r10_tip_fits_cylinder",
]


@dataclass(frozen=True, slots=True)
class DesignContext:
    """Everything the rules need beyond the cone itself."""

    cone: Cone
    fluid: Fluid
    step_w_mm: float = 0.30
    upper_angle_offset_deg: float = -2.0
    cylinder_height_mm: float = 12.0
    volume_target_mm3: float | None = None
    hematocrit: float = 0.45
    phi_pack: float = 0.90
    max_esr_mm_h: float = 120.0


def _rule(name: str, passes: bool, margin: float, unit: str, tier: Tier,
          source: str, **kw) -> Result:
    notes = tuple(kw.pop("notes", ())) + (
        f"margin {margin:+.4g} {unit}".strip(),
    )
    flags = tuple(kw.pop("flags", ())) + ((f"{name.split('_')[0].upper()}_VIOLATED",)
                                          if not passes else ())
    return Result(
        name=name, value=bool(passes), unit="", tier=tier, source=source,
        flags=flags, notes=notes, **kw,
    )


def r01_no_constriction(ctx: DesignContext) -> Result:
    """R01: ``min(clearance_above) >= clearance_working``. EXACT geometry."""
    step = geo.stepped_upper_cone(
        ctx.cone, w_mm=ctx.step_w_mm,
        upper_angle_offset_deg=ctx.upper_angle_offset_deg,
    )
    working = step["clearance_working"].value
    above = step["clearance_above_min"].value
    return _rule("R01_no_constriction", above >= working - 1e-12, above - working,
                 "mm", Tier.EXACT, "spec §9.1 R01")


def r02_mixing_threshold(ctx: DesignContext) -> Result:
    """R02: ``clearance_working >= 0.72``, UNRESOLVED over 0.69-0.77.

    Spec §9.1 quotes 0.72 as the threshold and immediately marks 0.69-0.77 unresolved.
    Since 0.72 lies *inside* its own disputed band, this rule can never return a
    confident pass at the nominal threshold. That is not a bug in the rule; it is the
    state of the evidence (unknown U02).
    """
    clearance = ctx.cone.clearance_radial(ctx.cone.x_bl_mm)
    lo, hi = cap.MIXING_DISPUTED_BAND
    if lo <= clearance <= hi:
        return Result.unknown(
            "R02_mixing_threshold",
            why=(
                f"clearance {clearance:.4f} mm is inside the disputed band "
                f"[{lo}, {hi}] mm. Evidence: {cap._evidence_string()}. 0.696 passed and "
                "0.766 failed, so no threshold may be fitted (unknown U02). Note that "
                f"the nominal threshold {cap.MIXING_NOMINAL_THRESHOLD} quoted by spec "
                "§9.1 itself lies inside this band."
            ),
            experiment=by_id("U02").how_to_resolve.strip(),
            flags=(UNRESOLVED_CONTRADICTION,),
        )
    passes = clearance > hi
    return _rule("R02_mixing_threshold", passes, clearance - hi, "mm",
                 Tier.CALIBRATED, "spec §9.1 R02",
                 notes=(f"evidence: {cap._evidence_string()}",))


def r03_gibbs_range(ctx: DesignContext) -> Result:
    """R03: ``(90 - theta) > blood hysteresis``. Spec §9.1."""
    pinning = cap.gibbs_pinning(ctx.cone)
    gibbs = pinning["gibbs_range"].value
    hyst = pinning["blood_hysteresis_range"].value
    return _rule("R03_gibbs_range", gibbs > hyst, gibbs - hyst, "deg",
                 Tier.ESTIMATED, "spec §9.1 R03",
                 notes=("hysteresis range is a literature value, 20-40 deg",))


def r04_step_width(ctx: DesignContext) -> Result:
    """R04: ``0.20 <= w <= Lc/2``. Spec §9.1 quotes 1.20; addendum §B derives 1.16."""
    w_max = cap.max_step_width(ctx.fluid)
    lo, hi = 0.20, float(w_max.value)
    w = ctx.step_w_mm
    passes = lo <= w <= hi
    margin = min(w - lo, hi - w)
    return _rule("R04_step_width", passes, margin, "mm", Tier.ESTIMATED,
                 "spec §9.1 R04, ceiling from addendum §B",
                 notes=(f"allowed {lo:.2f}-{hi:.2f} mm (Lc/2); spec §9.1 quotes 1.20 mm "
                        "as the ceiling, the addendum derives 1.16 — the derived value "
                        "is used",))


def r05_volume_target(ctx: DesignContext) -> Result:
    """R05: ``|V - V_target| / V_target < 0.02``. EXACT."""
    target = ctx.volume_target_mm3
    if target is None:
        lib = geo.tube_library()["tubes"].get(ctx.cone.tube_id, {})
        target = lib.get("volume_target_mm3")
    if not target:
        return Result.unknown(
            "R05_volume_target",
            why="no volume target given and none in the tube library for this geometry",
            experiment="state the intended column volume for this design",
        )
    v = ctx.cone.volume_numeric()
    rel = abs(v - target) / target
    return _rule("R05_volume_target", rel < 0.02, 0.02 - rel, "", Tier.EXACT,
                 "spec §9.1 R05",
                 notes=(f"V = {v:.1f} mm^3 against a {target:.0f} mm^3 target",))


def r06_range_vs_saturation(ctx: DesignContext) -> Result:
    """R06: the range ceiling must cover ESR up to 120 mm/h.

    Rides on phi_pack (unknown U01), so it can never be stronger than ESTIMATED.
    The honest answer for the current family is that it fails: see
    :func:`esrsim.calibration.validate.feasibility_check`.
    """
    from ..core.kinetics import descent

    ceiling = geo.range_ceiling(ctx.cone, ctx.hematocrit, ctx.phi_pack)
    if ceiling.tier is Tier.UNKNOWN:
        return ceiling.derive("R06_range_vs_saturation", None, "")
    run = descent(ctx.cone, ctx.max_esr_mm_h, ctx.hematocrit,
                  phi_pack=ctx.phi_pack, t_max_min=15.0)
    h = run.height(15.0)
    headroom = float(ceiling.value) - h
    passes = h < 0.98 * float(ceiling.value)
    return _rule("R06_range_vs_saturation", passes, headroom, "mm", Tier.ESTIMATED,
                 "spec §9.1 R06",
                 notes=(f"at ESR {ctx.max_esr_mm_h:g} mm/h the 15-minute reading is "
                        f"{h:.2f} mm of a {ceiling.value:.2f} mm ceiling",
                        "rides on phi_pack (unknown U01)"))


def r07_fill_resistance(ctx: DesignContext) -> Result:
    """R07: ``(0.90/d)^3 <= 3.0``. EXACT scaling."""
    res = cap.fill_resistance(ctx.cone).value
    return _rule("R07_fill_resistance", res <= 3.0, 3.0 - res, "x", Tier.EXACT,
                 "spec §9.1 R07",
                 notes=(f"relative filling resistance {res:.3f}x the 0.90 mm reference",))


def r08_bloodline_flatness(ctx: DesignContext) -> Result:
    """R08: predicted unevenness <= 1.0 mm, or a pinning edge is present.

    Both unevenness models are HYPOTHESIS and neither fits the data, so this rule is
    reported at HYPOTHESIS tier using the *worse* of the two — the conservative choice
    when you cannot tell which model is right.
    """
    uneven = cap.blood_line_unevenness(ctx.cone, ctx.fluid)
    values = [
        r.value for r in uneven
        if r.name.startswith("delta_h_model") and not r.name.endswith("_minus_observed")
        and r.value is not None
    ]
    if not values:
        return Result.unknown(
            "R08_bloodline_flatness",
            why="neither unevenness model could be evaluated for this geometry",
            experiment=by_id("U08").how_to_resolve.strip(),
        )
    worst = max(values)
    return _rule("R08_bloodline_flatness", worst <= 1.0, 1.0 - worst, "mm",
                 Tier.HYPOTHESIS, "spec §9.1 R08",
                 flags=(UNRESOLVED_CONTRADICTION,),
                 notes=("worse of two competing models, neither of which fits the "
                        "observations (unknown U08)",
                        "a sharp pinning edge satisfies this rule independently — the "
                        "stepped upper cone made the T070 blood line flat in practice "
                        "(addendum §B)"))


def r09_print_tolerance(ctx: DesignContext) -> Result:
    """R09: ``Delta >= 2.0 mm`` so a +/-0.1 mm print error stays under 5 percent."""
    delta = ctx.cone.delta_mm
    return _rule("R09_print_tolerance", delta >= 2.0, delta - 2.0, "mm", Tier.EXACT,
                 "spec §9.1 R09",
                 notes=(f"a +/-0.1 mm print error is {100 * 0.1 / delta:.1f}% of Delta",))


def r10_tip_fits_cylinder(ctx: DesignContext) -> Result:
    """R10: the upper cone's tip must fit inside the cylinder above the blood line."""
    step = geo.stepped_upper_cone(
        ctx.cone, w_mm=ctx.step_w_mm,
        upper_angle_offset_deg=ctx.upper_angle_offset_deg,
    )
    tip = step["tip_height_above_bloodline"].value
    return _rule("R10_tip_fits_cylinder", tip <= ctx.cylinder_height_mm,
                 ctx.cylinder_height_mm - tip, "mm", Tier.EXACT, "spec §9.1 R10",
                 notes=(f"tip {tip:.2f} mm against a {ctx.cylinder_height_mm:.2f} mm "
                        "cylinder",))


RULES: tuple[Callable[[DesignContext], Result], ...] = (
    r01_no_constriction,
    r02_mixing_threshold,
    r03_gibbs_range,
    r04_step_width,
    r05_volume_target,
    r06_range_vs_saturation,
    r07_fill_resistance,
    r08_bloodline_flatness,
    r09_print_tolerance,
    r10_tip_fits_cylinder,
)


def evaluate_rules(
    cone: Cone,
    fluid: Fluid | str = "blood_fresh",
    **kwargs,
) -> ResultSet:
    """Run all ten rules against one design."""
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    ctx = DesignContext(cone=cone, fluid=f, **kwargs)
    results = [rule(ctx) for rule in RULES]

    decided = [r for r in results if r.tier is not Tier.UNKNOWN]
    failed = [r for r in decided if r.value is False]
    undecided = [r for r in results if r.tier is Tier.UNKNOWN]

    counts = (
        Result.exact("rules_decided", len(decided), "of 10", source="spec §9.1"),
        Result.exact("rules_passed", len(decided) - len(failed), "of 10",
                     source="spec §9.1",
                     notes=tuple(f"FAILED: {r.name}" for r in failed)),
        Result.exact("rules_undecidable", len(undecided), "of 10",
                     source="spec §9.1",
                     notes=tuple(f"UNDECIDABLE: {r.name}" for r in undecided)),
    )

    # The overall verdict obeys the same rule as everything else: if any rule could not
    # be decided, there is no verdict — an undecidable rule is not a pass.
    if undecided:
        verdict = Result.unknown(
            "design_verdict",
            why=(
                f"{len(undecided)} of 10 rules cannot be decided on the present "
                "evidence ("
                + ", ".join(r.name for r in undecided)
                + f"), and {len(failed)} of the {len(decided)} decidable rules failed"
                + (": " + ", ".join(r.name for r in failed) if failed else "")
                + ". A design is not approved by ignoring the rules that have no answer."
            ),
            experiment="; ".join(
                dict.fromkeys(r.experiment for r in undecided if r.experiment)
            ),
            flags=tuple(dict.fromkeys(f for r in undecided for f in r.flags)),
        )
    else:
        verdict = Result(
            name="design_verdict",
            value=not failed,
            unit="",
            tier=max((r.tier for r in results), default=Tier.EXACT),
            source="spec §9.1",
            notes=tuple(f"FAILED: {r.name}" for r in failed) or ("all ten rules pass",),
        )

    return ResultSet(
        title=f"DESIGN RULES — {cone.tube_id or 'cone'}",
        results=tuple(results) + counts + (verdict,),
        notes=(
            "An undecidable rule is not a pass. R02 in particular cannot be decided "
            "for any clearance in 0.69-0.77 mm, and the nominal threshold quoted by "
            "the spec itself lies inside that band (unknown U02).",
        ),
    )
