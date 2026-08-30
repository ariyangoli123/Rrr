"""Sedimentation kinetics — CALIBRATED on n = 1, and never anything stronger.

THE WHOLE DATASET IS ONE SAMPLE. Three tubes, one blood draw, one operator, one
session. Every function in this module stamps ``N_EQUALS_1`` on its output, because a
number fitted to three points and quoted to three significant figures invites a
confidence nobody here has earned.

What this module is
-------------------
A phenomenological reproduction of measured kinetics, plus the exact geometry of how a
boundary descends through a varying cross-section. It is **not** a sedimentation model.
Whole-blood sedimentation is gel collapse and the material functions it needs, ``Py(phi)``
and ``R(phi)``, have never been measured (spec §0, §6; unknown U05).

The three enhancement models (spec §5.2)
----------------------------------------
All three are always reported together, never one alone::

    E_PNK(theta, L, d)     = cos(theta) + (L/d) sin(theta)
    E_empirical(theta)     = 1 + a sin(theta),                     a = 11.57
    E_saturated(theta,L,d) = cos(theta) + min(L/d, Lambda) sin(theta),  Lambda = 12

``E_PNK`` is an **asymptotic ceiling, not a prediction** — it overshoots the measured
values by 3x. The other two are fits to the same three points.

The descent model (spec §5.4)
-----------------------------
``dV_clear/dt = u_s A_projected(t)``, with the projected area of the settling surface
taken as the Boycott construction generalised to an annulus::

    Lambda_geom(t) = S_outer(interface -> sediment) / A(interface)
    E_local(t)     = cos(theta) + min(Lambda_geom(t), Lambda_eff) sin(theta)
    dh/dt          = u_s E_local(t)

``Lambda_geom`` is the annular analogue of ``L/d``: for a straight annulus of length L
and gap d it equals ``L/d`` exactly, so the model reduces to PNK in the limit the
literature covers. It varies with time because both the interface and the sediment top
move, which is why spec §5.4 says E is a function of time in a varying cross-section.

Three independent checks this model passes
------------------------------------------
* early-time ``E`` equals ``E_saturated``: 3.063 for T090 against the spec's 3.06;
* the range ceiling falls out of the same cell-conservation bookkeeping, giving
  32.2 mm for T090 against the spec's 32.0-34.6 band, and 25.0 mm for a plain tube
  against 24.7;
* saturation at the 15-minute readout first bites at ESR 55, which is the threshold
  spec §10 mandates.

One check it fails
------------------
The recorded ``dh/dESR`` at 15 minutes falls monotonically (0.83, 0.79, 0.60, 0.28 at
ESR 13, 20, 30, 40) while this model's rises then falls (0.49, 0.54, 0.60, 0.63). They
agree only near ESR 30. That is unknown **U10**, it is reported rather than tuned away,
and :func:`esrsim.core.readout.error_budget` prints both columns side by side.

References
----------
ESR_SIMULATOR_SPEC.md §5, §7.3; Anestis 1981; Buerger, Damasceno & Karlsen 2004.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq, curve_fit

from ..registry import load_yaml, measured
from ..tiers import (
    COLLINEARITY,
    EXTRAPOLATION_UNSAFE,
    LAG_LAW_DISPUTED,
    N_EQUALS_1,
    Result,
    ResultSet,
    Tier,
)
from ..units import check_fraction, check_positive, mm_per_hour_to_mm_per_min
from .geometry import Cone, range_ceiling

__all__ = [
    "constants",
    "E_pnk",
    "E_empirical",
    "E_saturated",
    "enhancement_models",
    "collinearity_warning",
    "lag_minutes",
    "Descent",
    "descent",
    "height_at",
    "sensitivity",
    "logistic",
    "fit_logistic",
    "decisive_experiment",
    "kinetics_report",
]


def constants() -> dict:
    """The calibrated constants block from ``calibration.yaml``."""
    return load_yaml("calibration.yaml")


def _enh() -> dict:
    return constants()["enhancement"]


def _n1(*extra: str) -> tuple[str, ...]:
    """Every kinetics result says n = 1."""
    return (N_EQUALS_1,) + tuple(extra)


_N1_NOTE = (
    "n = 1: the entire dataset is one blood sample measured in three tubes in one "
    "session",
)


# ------------------------------------------------------------- enhancement models


def E_pnk(theta_deg: float, length_mm: float, gap_mm: float) -> Result:
    """``E = cos(theta) + (L/d) sin(theta)`` — the PNK asymptotic ceiling.

    Spec §5.2 is explicit that this is a **ceiling, not a prediction**: it gives 10.6,
    16.5 and 23.9 where the measurements are 3.15, 3.75 and 3.90. It is reported so the
    gap between the idealised limit and reality stays visible.
    """
    check_positive("gap_mm", gap_mm)
    th = math.radians(theta_deg)
    value = math.cos(th) + (length_mm / gap_mm) * math.sin(th)
    return Result.estimated(
        "E_PNK", value, "x",
        source="Ponder-Nakamura-Kuroda limit; spec §5.2",
        flags=_n1(),
        notes=_N1_NOTE + (
            "ASYMPTOTIC CEILING, NOT A PREDICTION — it overshoots the measured values "
            "by about 3x (spec §5.2)",
        ),
    )


def E_empirical(theta_deg: float) -> Result:
    """``E = 1 + a sin(theta)``, ``a = 11.57`` — fitted to three points.

    Auto-retags to EXTRAPOLATED outside 9.973-15.970 degrees, and additionally carries
    ``EXTRAPOLATION_UNSAFE`` above 16 degrees, where spec §5.2 forbids extrapolation
    outright because the residuals are concave.
    """
    enh = _enh()
    a_spec = enh["a_empirical"]
    a = float(a_spec["value"])
    th = math.radians(theta_deg)
    result = Result.calibrated(
        "E_empirical", 1.0 + a * math.sin(th), "x",
        source=a_spec["source"],
        fitted_range={
            "theta_deg": tuple(a_spec["fitted_range"]["theta_deg"])  # type: ignore[arg-type]
        },
        flags=_n1(COLLINEARITY),
        notes=_N1_NOTE + (
            f"a = {a} fitted to 3 points; residuals "
            f"{a_spec['residuals']} are concave, so E saturates with angle",
            " ".join(str(a_spec["caveat"]).split()),
        ),
    ).enforce_range(theta_deg=theta_deg)
    return _guard_extrapolation(result, theta_deg)


def E_saturated(theta_deg: float, length_mm: float, gap_mm: float) -> Result:
    """``E = cos(theta) + min(L/d, Lambda_eff) sin(theta)``, ``Lambda_eff = 12``.

    The best of the three at reproducing the measurements, but note the caveat in
    ``calibration.yaml``: ``L/d`` never drops below 55 in the calibration set, so the
    ``min()`` always selects ``Lambda_eff`` there and the data contains **no**
    information about the ``L/d`` dependence. That is unknown U04.
    """
    check_positive("gap_mm", gap_mm)
    enh = _enh()
    lam_spec = enh["lambda_eff"]
    lam = float(lam_spec["value"])
    th = math.radians(theta_deg)
    l_over_d = length_mm / gap_mm
    value = math.cos(th) + min(l_over_d, lam) * math.sin(th)
    result = Result.calibrated(
        "E_saturated", value, "x",
        source=lam_spec["source"],
        fitted_range={
            "theta_deg": tuple(lam_spec["fitted_range"]["theta_deg"]),  # type: ignore[arg-type]
        },
        flags=_n1(COLLINEARITY),
        notes=_N1_NOTE + (
            f"Lambda_eff = {lam}; L/d = {l_over_d:.1f}, so min() selects "
            f"{'Lambda_eff' if lam < l_over_d else 'L/d'}",
            " ".join(str(lam_spec["caveat"]).split()),
        ),
    ).enforce_range(theta_deg=theta_deg)
    return _guard_extrapolation(result, theta_deg)


def _guard_extrapolation(result: Result, theta_deg: float) -> Result:
    """Spec §5.2: above 16 degrees, extrapolation is not permitted."""
    limit = float(_enh()["extrapolation"]["theta_deg_max"])
    if theta_deg <= limit:
        return result
    return result.with_flags(EXTRAPOLATION_UNSAFE).with_notes(
        f"theta = {theta_deg:.3f} deg is above the {limit:.1f} deg limit of spec §5.2. "
        "The residuals of the fit are concave, so E saturates with angle and "
        "extrapolation here is NOT SAFE. Treat this number as an upper bound only."
    )


def collinearity_warning(
    l_over_d: Sequence[float] | None = None,
    sin_theta: Sequence[float] | None = None,
) -> Result:
    """Pearson r between the two predictors — spec §5.2.

    Spec §5.2: *"the program must compute the collinearity coefficient of the variables
    when fitting any E model and warn if it is > 0.9."* Called on every E-model
    evaluation, not just on fits, because a prediction from a collinear fit is exactly
    as untrustworthy as the fit itself.
    """
    enh = _enh()["collinearity"]
    threshold = float(enh["warn_above"])
    if l_over_d is None or sin_theta is None:
        entries = measured()["enhancement"]["entries"]
        l_over_d = [float(e["L_over_d"]) for e in entries]
        sin_theta = [math.sin(math.radians(float(e["theta_deg"]))) for e in entries]

    x = np.asarray(list(l_over_d), dtype=float)
    y = np.asarray(list(sin_theta), dtype=float)
    if x.size < 3 or float(np.ptp(x)) == 0 or float(np.ptp(y)) == 0:
        return Result.unknown(
            "collinearity_r",
            why=f"cannot compute a correlation from {x.size} points with no spread",
            experiment="measure tubes whose L/d and angle vary independently — that is "
            "the decisive experiment of spec §9.3",
        )
    r = float(np.corrcoef(x, y)[0, 1])
    warn = abs(r) > threshold
    return Result.calibrated(
        "collinearity_r", r, "",
        source=enh["source"],
        flags=_n1(COLLINEARITY) if warn else _n1(),
        notes=_N1_NOTE + ((
            f"|r| = {abs(r):.3f} exceeds {threshold}: L/d and sin(theta) cannot be "
            "separated in this set. A coefficient attributed to one could belong to "
            "the other (unknown U04).",
            " ".join(str(enh["consequence"]).split()),
        ) if warn else (f"|r| = {abs(r):.3f}, below the {threshold} warning level",)),
    )


def enhancement_models(
    theta_deg: float, length_mm: float, gap_mm: float, *, tube_id: str = ""
) -> ResultSet:
    """All three E models side by side — spec §5.2 requires them reported together.

    Build prompt: *"All three enhancement models displayed together, never one.
    Collinearity warning on every call."*
    """
    pnk = E_pnk(theta_deg, length_mm, gap_mm)
    emp = E_empirical(theta_deg)
    sat = E_saturated(theta_deg, length_mm, gap_mm)
    coll = collinearity_warning()

    results = [pnk, emp, sat, coll]
    spread = max(r.value for r in (pnk, emp, sat)) - min(
        r.value for r in (pnk, emp, sat)
    )
    results.append(
        Result.estimated(
            "E_model_spread", spread, "x",
            flags=_n1(),
            notes=_N1_NOTE + (
                "the three models disagree by this much; there is no basis in the data "
                "for choosing between them at this geometry",
            ),
        )
    )

    recorded = _recorded_E(tube_id)
    if recorded is not None:
        results.append(recorded)
        for model in (pnk, emp, sat):
            results.append(
                Result(
                    name=f"{model.name}_minus_measured",
                    value=model.value - recorded.value,
                    unit="x",
                    tier=max(model.tier, recorded.tier),
                    flags=_n1(),
                    notes=_N1_NOTE,
                )
            )

    return ResultSet(
        title=f"ENHANCEMENT MODELS — {tube_id or 'geometry'} "
              f"(theta {theta_deg:.3f} deg, L/d {length_mm / gap_mm:.1f})",
        results=tuple(results),
        notes=(
            "n = 1. Three tubes, one sample, one session.",
            "E_PNK is an asymptotic ceiling, NOT a prediction.",
            "L/d and sin(theta) are collinear at r = 0.986 in the calibration set, so "
            "no model here can tell an angle effect from a gap effect (unknown U04).",
            "The decisive experiment is on the bench: T060 as cut against T060 with the "
            "inner cone shifted 2 mm, at the same angle (spec §9.3).",
        ),
    )


def _recorded_E(tube_id: str) -> Result | None:
    if not tube_id:
        return None
    for entry in measured()["enhancement"]["entries"]:
        if entry["tube"] == tube_id:
            return Result.calibrated(
                "E_measured", float(entry["E_measured"]), "x",
                source="sample 1; spec §5.2 validation table",
                flags=_n1(),
                notes=_N1_NOTE,
            )
    return None


# --------------------------------------------------------------------- lag phase


def lag_minutes(esr_mm_h: float) -> Result:
    """Haze-phase duration — spec §5.3, **DISPUTED**.

    ``lag(ESR) = max(1.5, 14.5 - 5.85 log10(ESR))``, fitted to two historical points.
    The record also says ESR 8 produced no haze at all, which this law contradicts, so
    every result is flagged ``LAG_LAW_DISPUTED`` (unknown U03).

    Spec §5.3: *"never use a fixed number."*
    """
    check_positive("esr_mm_h", esr_mm_h)
    law = constants()["lag"]["law"]
    value = max(
        float(law["floor_min"]),
        float(law["intercept"]) - float(law["slope"]) * math.log10(esr_mm_h),
    )
    lo, hi = law["fitted_range"]["esr_mm_h"]
    return Result.calibrated(
        "lag", value, "min",
        source=law["source"],
        fitted_range={"esr_mm_h": (float(lo), float(hi))},
        flags=_n1(LAG_LAW_DISPUTED),
        notes=_N1_NOTE + (
            "fitted to TWO points",
            " ".join(str(law["caveat"]).split()),
        ),
    ).enforce_range(esr_mm_h=esr_mm_h)


def readable_height() -> Result:
    """Boundary height at which it first becomes readable — spec §5.3, ~4.0 mm."""
    spec = constants()["lag"]["readable_height"]
    return Result.calibrated(
        "readable_height", float(spec["value"]), spec["unit"],
        source=spec["source"], flags=_n1(), notes=_N1_NOTE,
    )


# ------------------------------------------------------------------ descent model


@dataclass(frozen=True, slots=True)
class Descent:
    """A solved descent: height against time, plus the machinery that produced it."""

    cone: Cone
    esr_mm_h: float
    hematocrit: float
    phi_pack: float
    ceiling_mm: float
    include_lag: bool
    lag_min: float
    h0_mm: float
    _times: tuple[float, ...]
    _heights: tuple[float, ...]

    def height(self, t_min: float) -> float:
        """Boundary height (mm below the blood line) at ``t`` minutes."""
        if t_min <= self._times[0]:
            return 0.0 if self.include_lag else self._heights[0]
        return float(np.interp(t_min, self._times, self._heights))

    def time_to(self, h_mm: float) -> float | None:
        """Minutes until the boundary reaches ``h_mm``; ``None`` if it never does."""
        if h_mm > self._heights[-1] + 1e-9:
            return None
        return float(np.interp(h_mm, self._heights, self._times))

    @property
    def saturated_at(self) -> float | None:
        """First time the boundary reaches 98 percent of the ceiling."""
        target = 0.98 * self.ceiling_mm
        if self._heights[-1] < target:
            return None
        return float(np.interp(target, self._heights, self._times))

    def E_average(self, t_min: float) -> float:
        """Enhancement realised by time ``t``: cone height over Westergren height."""
        westergren = mm_per_hour_to_mm_per_min(self.esr_mm_h) * t_min
        if westergren <= 0:
            return float("nan")
        return self.height(t_min) / westergren


def _sediment_top(cone: Cone, h_mm: float, hematocrit: float, phi_pack: float) -> float:
    """Axial position of the top of the packed sediment, mm from the outer apex.

    Cell conservation with a clear zone at Hct 0, a suspension at the initial Hct and a
    sediment at ``phi_pack`` gives ``V_sed = C(h) Hct / (phi_pack - Hct)``. Note that
    the interface meets the sediment exactly when ``C(h) = V (1 - Hct/phi_pack)``, which
    is the range ceiling of spec §5.5 — the same bookkeeping produces both.
    """
    v_sed = cone.cumulative_volume(h_mm) * hematocrit / (phi_pack - hematocrit)
    total = cone.volume_mm3
    if v_sed >= total:
        return cone.x_bl_mm
    # Height (from the blood line) at which the volume BELOW equals v_sed.
    h_sed = cone.height_for_volume(total - v_sed)
    return cone.x_bl_mm + h_sed


def _lambda_geom(cone: Cone, h_mm: float, hematocrit: float, phi_pack: float) -> float:
    """``S_outer(interface -> sediment) / A(interface)``: the annular analogue of L/d."""
    x_i = cone.x_bl_mm + h_mm
    x_s = _sediment_top(cone, h_mm, hematocrit, phi_pack)
    if x_s <= x_i:
        return 0.0
    return cone.outer_wall_area(x_i, x_s) / cone.area(x_i)


def descent(
    cone: Cone,
    esr_mm_h: float,
    hematocrit: float = 0.45,
    *,
    phi_pack: float = 0.90,
    include_lag: bool = True,
    t_max_min: float = 60.0,
) -> Descent:
    """Solve ``dh/dt = u_s E_local(h)`` — spec §5.4.

    ``u_s`` is the Westergren settling velocity, ``ESR/60`` mm/min. Treating the
    Westergren reading as a constant velocity over the hour is an approximation, and it
    is one of the reasons this model's sensitivity curve does not match the recorded one
    (unknown U10).
    """
    check_positive("esr_mm_h", esr_mm_h)
    check_fraction("hematocrit", hematocrit)
    check_fraction("phi_pack", phi_pack)
    if hematocrit >= phi_pack:
        raise ValueError(
            f"haematocrit {hematocrit} is not below phi_pack {phi_pack}; no free "
            "plasma exists and no boundary forms"
        )

    ceiling = range_ceiling(cone, hematocrit, phi_pack)
    ceiling_mm = float(ceiling.value)
    lam_eff = float(_enh()["lambda_eff"]["value"])
    th = cone.theta_o
    cos_t, sin_t = math.cos(th), math.sin(th)
    u_s = mm_per_hour_to_mm_per_min(esr_mm_h)

    lag = float(lag_minutes(esr_mm_h).value) if include_lag else 0.0
    h0 = float(readable_height().value) if include_lag else 0.0
    h0 = min(h0, 0.999 * ceiling_mm)

    def rhs(_t: float, y: np.ndarray) -> list[float]:
        h = min(max(float(y[0]), 0.0), ceiling_mm - 1e-9)
        e_local = cos_t + min(_lambda_geom(cone, h, hematocrit, phi_pack), lam_eff) * sin_t
        return [u_s * e_local]

    # max_step keeps the solver from stepping over the roll-off near the ceiling,
    # where E_local falls steeply; 5 minutes is comfortably inside that feature and
    # roughly five times faster than the 1-minute cap it replaced.
    t_eval = np.linspace(lag, max(t_max_min, lag + 1e-6), 241)
    sol = solve_ivp(
        rhs, (lag, t_eval[-1]), [h0], t_eval=t_eval, rtol=1e-8, atol=1e-10, max_step=5.0
    )
    heights = np.minimum(sol.y[0], ceiling_mm)
    heights = np.maximum.accumulate(heights)   # the boundary never rises

    return Descent(
        cone=cone,
        esr_mm_h=esr_mm_h,
        hematocrit=hematocrit,
        phi_pack=phi_pack,
        ceiling_mm=ceiling_mm,
        include_lag=include_lag,
        lag_min=lag,
        h0_mm=h0,
        _times=tuple(float(t) for t in sol.t),
        _heights=tuple(float(h) for h in heights),
    )


def height_at(
    cone: Cone,
    esr_mm_h: float,
    t_min: float,
    hematocrit: float = 0.45,
    *,
    phi_pack: float = 0.90,
    include_lag: bool = True,
) -> Result:
    """Boundary height at a fixed time — the FIXED_TIME_HEIGHT readout.

    Returns UNKNOWN when the boundary has saturated: spec §7.3 forbids returning a
    numeric value there, because every higher rate gives the same reading.
    """
    run = descent(
        cone, esr_mm_h, hematocrit, phi_pack=phi_pack, include_lag=include_lag,
        t_max_min=max(t_min, 1.0),
    )
    h = run.height(t_min)
    if h >= 0.98 * run.ceiling_mm:
        return Result.unknown(
            "height",
            why=(
                f"SATURATED at t = {t_min:g} min: the boundary is at {h:.2f} mm of a "
                f"{run.ceiling_mm:.2f} mm ceiling. Every ESR above about "
                f"{esr_mm_h:.0f} mm/h reads the same here, so the reading carries no "
                "information about the rate."
            ),
            experiment=(
                "read earlier, or switch to a time-to-threshold readout, which is "
                "monotonic and cannot saturate"
            ),
            flags=_n1("SATURATED"),
        )
    flags = _n1(LAG_LAW_DISPUTED) if include_lag else _n1()
    return Result.calibrated(
        "height", h, "mm",
        source="ESR_SIMULATOR_SPEC.md §5.4 volumetric descent",
        fitted_range={"theta_deg": tuple(_enh()["lambda_eff"]["fitted_range"]["theta_deg"])},
        flags=flags,
        notes=_N1_NOTE + (
            f"ESR {esr_mm_h:g} mm/h, t = {t_min:g} min, lag = {run.lag_min:.2f} min",
            f"realised E = {run.E_average(t_min):.3f} against the Westergren reference",
        ) + (("the lag law is DISPUTED (unknown U03)",) if include_lag else ()),
    ).enforce_range(theta_deg=cone.theta_o_deg)


def sensitivity(
    cone: Cone,
    esr_mm_h: float,
    t_min: float = 15.0,
    hematocrit: float = 0.45,
    *,
    phi_pack: float = 0.90,
    include_lag: bool = True,
    step: float = 0.5,
) -> Result:
    """``dh/dESR`` at a fixed readout time — spec §7.2.

    This is the quantity that disagrees with the record (unknown U10). It is computed
    from the model and returned as such; the comparison lives in
    :func:`esrsim.core.readout.error_budget`.
    """
    def h_of(e: float) -> float:
        run = descent(cone, e, hematocrit, phi_pack=phi_pack,
                      include_lag=include_lag, t_max_min=max(t_min, 1.0))
        return run.height(t_min)

    lo = max(0.5, esr_mm_h - step)
    hi = esr_mm_h + step
    value = (h_of(hi) - h_of(lo)) / (hi - lo)
    return Result.calibrated(
        "dh_dESR", value, "mm/(mm/h)",
        source="ESR_SIMULATOR_SPEC.md §5.4 model, differenced numerically",
        flags=_n1(),
        notes=_N1_NOTE + (
            f"central difference over ESR +/- {step:g} at t = {t_min:g} min",
            "the recorded sensitivity falls monotonically with ESR while this model's "
            "rises then falls; they agree only near ESR 30 (unknown U10)",
        ),
    )


# ------------------------------------------------------------- logistic fit (§5.1)


def logistic(t: np.ndarray | float, h_max: float, k: float, t_mid: float):
    """``h(t) = H_max / (1 + exp(-k (t - t_mid)))`` — spec §5.1, three parameters."""
    return h_max / (1.0 + np.exp(-k * (np.asarray(t, dtype=float) - t_mid)))


def fit_logistic(
    times_min: Sequence[float], heights_mm: Sequence[float]
) -> ResultSet:
    """Three-parameter logistic fit — spec §5.1.

    Spec §5.1 is emphatic on two points, both enforced here:

    * **three parameters only** — the four-parameter form diverged;
    * **no smoothing of the raw data**, ever. This function fits what it is given.
    """
    t = np.asarray(list(times_min), dtype=float)
    h = np.asarray(list(heights_mm), dtype=float)
    mask = np.isfinite(t) & np.isfinite(h)
    t, h = t[mask], h[mask]
    # Three parameters plus one residual degree of freedom.
    min_points = len(constants()["logistic"]["parameters"]) + 1
    if t.size < min_points:
        return ResultSet(
            "LOGISTIC FIT",
            (
                Result.unknown(
                    "logistic_fit",
                    why=f"{t.size} usable points for a "
                    f"{len(constants()['logistic']['parameters'])}-parameter fit; at "
                    f"least {min_points} are needed for any residual degrees of freedom",
                    experiment="log the boundary at 30-second intervals over the first "
                    "20 minutes",
                ),
            ),
        )

    p0 = [max(float(h.max()) * 1.05, 1e-6), 0.2, float(np.median(t))]
    try:
        popt, pcov = curve_fit(logistic, t, h, p0=p0, maxfev=20000)
    except (RuntimeError, ValueError) as exc:
        return ResultSet(
            "LOGISTIC FIT",
            (
                Result.unknown(
                    "logistic_fit",
                    why=f"the three-parameter logistic did not converge: {exc}",
                    experiment="check that t = 0 is tube placement and that no lap "
                    "under 5 seconds slipped through ingest",
                ),
            ),
        )

    perr = np.sqrt(np.diag(pcov)) if np.all(np.isfinite(pcov)) else np.full(3, np.nan)
    resid = h - logistic(t, *popt)
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((h - h.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    names = ("H_max", "k", "t_mid")
    units = ("mm", "1/min", "min")
    results = []
    for name, unit, value, err in zip(names, units, popt, perr):
        note = (f"standard error {err:.4g}",) if math.isfinite(err) else (
            "standard error not estimable from the covariance",
        )
        results.append(
            Result.calibrated(
                name, float(value), unit,
                source="ESR_SIMULATOR_SPEC.md §5.1 three-parameter logistic",
                fitted_range={"t_min": (float(t.min()), float(t.max()))},
                flags=_n1(),
                notes=_N1_NOTE + note,
            )
        )
    results.append(
        Result.calibrated("r_squared", float(r2), "", flags=_n1(), notes=_N1_NOTE)
    )
    results.append(
        Result.calibrated(
            "residual_rms", float(math.sqrt(ss_res / t.size)), "mm",
            flags=_n1(), notes=_N1_NOTE,
        )
    )
    return ResultSet(
        title=f"LOGISTIC FIT (n = {t.size} points)",
        results=tuple(results),
        notes=(
            "three parameters only: the four-parameter form diverged (spec §5.1)",
            "no smoothing was applied to the input (spec §5.1, §8.3)",
        ),
    )


# ---------------------------------------------------------- decisive experiment


def decisive_experiment(hematocrit: float = 0.45) -> ResultSet:
    """The collinearity-breaking experiment — spec §9.3, unknown U04.

    T060 as cut (gap 0.600, L/d 83.3) against T060 with the inner cone shifted 2 mm
    (gap 1.150, L/d 43.5), at the **same** 15.970 degree angle.

    * PNK predicts E roughly halves, 23.9 -> 12.9.
    * The empirical angle-only law predicts no change at all.

    One measurement separates them. Until it exists, this function returns the two
    predictions and an UNKNOWN verdict — it does not guess which is right.
    """
    from .geometry import from_library, shift_inner_cone

    base = from_library("T060")
    shift_mm = 2.0
    shifted_set = shift_inner_cone(base, shift_mm)
    gap_a = base.gap_perpendicular(base.x_bl_mm)
    gap_b = float(shifted_set["gap_after"].value)
    length = base.length_mm

    pnk_a = E_pnk(base.theta_o_deg, length, gap_a)
    pnk_b = E_pnk(base.theta_o_deg, length, gap_b)
    emp = E_empirical(base.theta_o_deg)
    sat_a = E_saturated(base.theta_o_deg, length, gap_a)
    sat_b = E_saturated(base.theta_o_deg, length, gap_b)

    results = [
        Result.exact("theta_both_arms", base.theta_o_deg, "deg",
                     notes=("identical in both arms — that is the point",)),
        Result.exact("gap_arm_A", gap_a, "mm"),
        Result.exact("gap_arm_B", gap_b, "mm",
                     notes=(f"inner cone shifted {shift_mm:g} mm",)),
        Result.exact("L_over_d_arm_A", length / gap_a, ""),
        Result.exact("L_over_d_arm_B", length / gap_b, ""),
        Result.exact("volume_arm_B", float(shifted_set["volume_after"].value), "mm^3",
                     notes=("arm B leaves the iso-volume family — a confound the "
                            "experiment cannot avoid and must report",)),
        pnk_a.rename("E_PNK_arm_A"),
        pnk_b.rename("E_PNK_arm_B"),
        Result.estimated(
            "PNK_prediction_ratio", pnk_b.value / pnk_a.value, "",
            flags=_n1(),
            notes=_N1_NOTE + ("PNK says E falls by this factor",),
        ),
        emp.rename("E_empirical_both_arms"),
        Result.calibrated(
            "empirical_prediction_ratio", 1.0, "",
            source="the angle-only law has no gap term",
            flags=_n1(COLLINEARITY),
            notes=_N1_NOTE + ("the empirical law says E does not change at all",),
        ),
        sat_a.rename("E_saturated_arm_A"),
        sat_b.rename("E_saturated_arm_B"),
        Result.unknown(
            "E_law_verdict",
            why=(
                "The two laws make opposite predictions and the measurement has not "
                f"been made. PNK: E falls from {pnk_a.value:.1f} to {pnk_b.value:.1f}, "
                f"a factor of {pnk_b.value / pnk_a.value:.2f}. The empirical angle-only "
                "law: no change. In the existing set L/d and sin(theta) are collinear "
                "at r = 0.986, so no fit to that set can decide it (unknown U04)."
            ),
            experiment=(
                "Run both arms in one session with one blood sample: T060 as cut and "
                "T060 with the inner cone shifted 2.0 mm, same angle, same operator. "
                "Report the measured E for each and feed them back through "
                "esrsim.core.kinetics.decisive_experiment()."
            ),
            flags=_n1(COLLINEARITY),
        ),
    ]
    return ResultSet(
        title="DECISIVE EXPERIMENT — does E depend on L/d or only on angle? (spec §9.3)",
        results=tuple(results),
        notes=(
            "n = 1 in the existing set, and its two predictors are collinear.",
            "The parts are on the bench (spec §G.1).",
            "Arm B's volume is 86 percent higher than arm A's, so a difference in E "
            "could also be a volume effect; that confound is unavoidable with a shift "
            "and must be stated in any write-up.",
        ),
    )


# ------------------------------------------------------------------------ report


def kinetics_report(
    cone: Cone,
    esr_mm_h: float = 13.0,
    t_min: float = 15.0,
    hematocrit: float = 0.45,
    *,
    phi_pack: float = 0.90,
) -> ResultSet:
    """Enhancement models, lag, descent and sensitivity for one geometry."""
    gap = cone.gap_perpendicular(cone.x_bl_mm)
    if not cone.is_constant_gap:
        gap = 0.5 * (gap + cone.gap_perpendicular(cone.x_base_mm))

    models = enhancement_models(cone.theta_o_deg, cone.length_mm, gap,
                                tube_id=cone.tube_id)
    run = descent(cone, esr_mm_h, hematocrit, phi_pack=phi_pack, t_max_min=60.0)
    ceiling = range_ceiling(cone, hematocrit, phi_pack)

    extra = [
        lag_minutes(esr_mm_h),
        readable_height(),
        height_at(cone, esr_mm_h, t_min, hematocrit, phi_pack=phi_pack),
        sensitivity(cone, esr_mm_h, t_min, hematocrit, phi_pack=phi_pack),
        ceiling.rename("range_ceiling"),
        Result.calibrated(
            "E_realised_at_readout", run.E_average(t_min), "x",
            source="ESR_SIMULATOR_SPEC.md §5.4",
            flags=_n1(),
            notes=_N1_NOTE + ("cone height / Westergren height at the readout time",),
        ),
    ]
    sat_t = run.saturated_at
    if sat_t is not None:
        extra.append(
            Result.calibrated(
                "saturates_at", sat_t, "min",
                source="ESR_SIMULATOR_SPEC.md §7.3",
                flags=_n1("SATURATED"),
                notes=_N1_NOTE + (
                    f"at ESR {esr_mm_h:g} mm/h the boundary reaches 98 percent of the "
                    f"{run.ceiling_mm:.2f} mm ceiling at this time; readings after it "
                    "carry no rate information",
                ),
            )
        )

    # Every row of a kinetics table carries the n = 1 stamp, including rows that came
    # in from geometry: an unflagged row in a printed table reads as exempt from the
    # caveat, which is exactly the misreading the build prompt is guarding against.
    stamped = tuple(r.with_flags(N_EQUALS_1) for r in tuple(models) + tuple(extra))

    return ResultSet(
        title=f"KINETICS — {cone.tube_id or 'cone'}, ESR {esr_mm_h:g} mm/h, "
              f"Hct {hematocrit:.2f}",
        results=stamped,
        notes=models.notes + (
            "The lag law is DISPUTED (unknown U03) and phi_pack is ASSUMED (U01).",
            "This is a phenomenological reproduction, not a sedimentation model. No "
            "first-principles prediction of whole-blood sedimentation is possible: "
            "Py(phi) and R(phi) have never been measured (spec §0, unknown U05).",
        ),
    )
