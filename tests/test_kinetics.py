"""Kinetics regression tests — ESR_SIMULATOR_SPEC.md §10, tests/test_kinetics.py.

Three of the tests spec §10 mandates need sample 1's raw trace, which is not in this
repository. They are gated by tests/conftest.py::require_measured against the
missing-data register rather than being quietly dropped or fed invented numbers.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from esrsim.core import geometry as geo
from esrsim.core import kinetics as kin
from esrsim.core import readout as ro
from esrsim.tiers import Tier
from tests.conftest import require_measured

T090 = geo.from_library("T090")
T060 = geo.from_library("T060")
TAPER = geo.from_library("TAPER")

#: Spec §5.2 mandatory validation table: tube -> (gap used, E_PNK, E_sat, E_measured).
VALIDATION_TABLE = {
    "T090": (0.90, 10.6, 3.06, 3.15),
    "TAPER": (0.70, 16.5, 3.58, 3.75),
    "T060": (0.60, 23.9, 4.26, 3.90),
}


# --------------------------------------------------------- enhancement models


def test_sample1_E_values_reproduced() -> None:
    """Spec §10: the §5.2 validation table, to +/- 0.05.

    The model columns (E_PNK, E_saturated) are reproduced from geometry and the
    calibrated constants. The E_measured column is data and is checked to be loaded
    faithfully, not "reproduced" — no model in this package hits it to 0.05, which is
    the point of the companion test below.
    """
    for tube_id, (gap, e_pnk, e_sat, e_meas) in VALIDATION_TABLE.items():
        cone = geo.from_library(tube_id)
        assert kin.E_pnk(cone.theta_o_deg, 50.0, gap).value == pytest.approx(
            e_pnk, abs=0.05
        ), tube_id
        assert kin.E_saturated(cone.theta_o_deg, 50.0, gap).value == pytest.approx(
            e_sat, abs=0.05
        ), tube_id
        models = kin.enhancement_models(cone.theta_o_deg, 50.0, gap, tube_id=tube_id)
        assert models["E_measured"].value == pytest.approx(e_meas, abs=1e-9), tube_id


def test_no_model_actually_reproduces_the_measurements() -> None:
    """The honest companion: every model misses the measured E by more than 0.05.

    E_saturated is the closest and still misses T060 by 0.36. Nothing in this package
    may be presented as reproducing the measurements.
    """
    worst = 0.0
    for tube_id, (gap, _pnk, _sat, e_meas) in VALIDATION_TABLE.items():
        cone = geo.from_library(tube_id)
        best = min(
            abs(kin.E_empirical(cone.theta_o_deg).value - e_meas),
            abs(kin.E_saturated(cone.theta_o_deg, 50.0, gap).value - e_meas),
        )
        worst = max(worst, best)
    assert worst > 0.05, (
        "a model now reproduces the measurements to 0.05; if that is real rather than "
        "overfitting, update this test and the README's claims"
    )


def test_e_pnk_is_labelled_a_ceiling_not_a_prediction() -> None:
    """Spec §5.2: 'asymptotic ceiling, NOT prediction'."""
    result = kin.E_pnk(9.973, 50.0, 0.90)
    assert result.tier is Tier.ESTIMATED
    assert any("CEILING, NOT A PREDICTION" in n for n in result.notes)
    assert result.value > 3 * 3.15, "PNK should overshoot the measurement threefold"


def test_all_three_models_are_always_reported_together() -> None:
    """Build prompt: 'All three enhancement models displayed together, never one'."""
    models = kin.enhancement_models(13.466, 50.0, 0.70, tube_id="T070")
    for name in ("E_PNK", "E_empirical", "E_saturated"):
        assert models.get(name) is not None, name


def test_collinearity_warning_fires_at_r_0986() -> None:
    """Spec §10: the calibration set has corr(L/d, sin theta) = 0.986."""
    result = kin.collinearity_warning()
    assert result.value == pytest.approx(0.986, abs=0.002)
    assert "COLLINEARITY_WARNING" in result.flags


def test_collinearity_warning_is_on_every_model_call() -> None:
    """Spec §5.2 requires the warning whenever an E model is fitted or evaluated."""
    for result in (kin.E_empirical(12.0), kin.E_saturated(12.0, 50.0, 0.7)):
        assert "COLLINEARITY_WARNING" in result.flags, result.name


def test_collinearity_does_not_warn_on_independent_predictors() -> None:
    """The warning must be a real test, not a constant."""
    result = kin.collinearity_warning(
        l_over_d=[80.0, 40.0, 80.0, 40.0],
        sin_theta=[0.17, 0.17, 0.28, 0.28],
    )
    assert abs(result.value) < 0.9
    assert "COLLINEARITY_WARNING" not in result.flags


def test_extrapolation_above_16_degrees_is_unsafe() -> None:
    """Spec §5.2: 'extrapolation above 16 degrees is not permitted'."""
    safe = kin.E_empirical(15.0)
    assert "EXTRAPOLATION_UNSAFE" not in safe.flags

    unsafe = kin.E_empirical(20.0)
    assert "EXTRAPOLATION_UNSAFE" in unsafe.flags
    assert unsafe.tier is Tier.EXTRAPOLATED
    assert any("NOT SAFE" in n for n in unsafe.notes)


def test_calibrated_result_retags_outside_its_fitted_range() -> None:
    """Build prompt: automatic re-tagging, not a manual call."""
    inside = kin.E_saturated(12.0, 50.0, 0.7)
    assert inside.tier is Tier.CALIBRATED
    outside = kin.E_saturated(8.0, 50.0, 0.7)      # below the fitted 9.973 deg
    assert outside.tier is Tier.EXTRAPOLATED
    assert "EXTRAPOLATION_UNSAFE" in outside.flags


def test_every_kinetics_result_says_n_equals_1() -> None:
    """Build prompt: 'The whole dataset is n = 1. That fact must appear in every
    kinetics output.'"""
    report = kin.kinetics_report(T090, esr_mm_h=13.0, t_min=15.0)
    for result in report:
        assert "N_EQUALS_1" in result.flags, result.name


# ------------------------------------------------------------------- lag phase


def test_lag_law_reproduces_the_recorded_point() -> None:
    """Spec §5.3 and §G.5: ESR 13 gave 8.43 minutes of haze."""
    assert kin.lag_minutes(13.0).value == pytest.approx(8.0, abs=0.5)


def test_lag_law_is_always_flagged_disputed() -> None:
    """Spec §5.3: the ESR 8 record contradicts the law; never present it as settled."""
    for esr in (5.0, 13.0, 60.0):
        result = kin.lag_minutes(esr)
        assert "LAG_LAW_DISPUTED" in result.flags, esr


def test_lag_law_has_a_floor() -> None:
    assert kin.lag_minutes(1000.0).value == pytest.approx(1.5)


def test_lag_law_extrapolates_outside_its_two_points() -> None:
    """It was fitted to ESR 8 and 13. Everything else is extrapolation."""
    assert kin.lag_minutes(13.0).tier is Tier.CALIBRATED
    assert kin.lag_minutes(60.0).tier is Tier.EXTRAPOLATED


def test_haze_is_biological_not_geometric() -> None:
    """Spec §5.3: a 2.25x speed difference produced only a 12 percent haze spread."""
    record = kin.constants()["lag"]["biological_not_geometric"]
    assert record["spread_observed_percent"] == 12
    assert record["spread_predicted_percent"] == 225


def test_haze_spread_is_12_percent(sample_001: dict) -> None:
    """Spec §10. GATED: per-tube haze durations are not in the record (M03)."""
    per_tube = require_measured(
        sample_001, "haze.spread_per_tube",
        "tests/test_kinetics.py::test_haze_spread_is_12_percent",
    )
    values = [float(v) for v in per_tube.values()]
    spread = 100.0 * (max(values) - min(values)) / min(values)
    assert spread == pytest.approx(12.0, abs=3.0)


# ---------------------------------------------------------------- descent model


def test_descent_early_E_equals_the_saturated_model() -> None:
    """The volumetric model must reduce to E_saturated while the column is long.

    This is the check that ties the geometric descent back to the calibrated
    enhancement: if it drifts, one of the two is wrong.
    """
    for tube_id, (gap, _pnk, e_sat, _meas) in VALIDATION_TABLE.items():
        cone = geo.from_library(tube_id)
        run = kin.descent(cone, 13.0, 0.45, include_lag=False, t_max_min=5.0)
        realised = run.height(5.0) / (13.0 / 60.0 * 5.0)
        assert realised == pytest.approx(e_sat, abs=0.05), tube_id


def test_range_ceiling_falls_out_of_the_same_bookkeeping() -> None:
    """The descent stops exactly where spec §5.5's ceiling formula says it should."""
    run = kin.descent(T090, 60.0, 0.45, t_max_min=200.0)
    ceiling = geo.range_ceiling(T090, 0.45).value
    assert run.height(200.0) == pytest.approx(ceiling, rel=1e-3)


def test_boundary_never_rises() -> None:
    run = kin.descent(T060, 25.0, 0.45, t_max_min=90.0)
    heights = [run.height(t) for t in np.linspace(0, 90, 200)]
    assert all(b >= a - 1e-9 for a, b in zip(heights, heights[1:]))


def test_saturation_detected_above_esr_55_at_15min() -> None:
    """Spec §10, driven by the real model rather than an analytic stand-in."""
    assert kin.height_at(T090, 55.0, 15.0).tier is Tier.UNKNOWN
    assert kin.height_at(T090, 53.0, 15.0).tier is not Tier.UNKNOWN


def test_saturated_height_returns_no_number() -> None:
    result = kin.height_at(T090, 80.0, 15.0)
    assert result.value is None
    assert "SATURATED" in result.flags
    assert result.experiment


def test_descent_refuses_impossible_hematocrit() -> None:
    with pytest.raises(ValueError, match="no boundary forms"):
        kin.descent(T090, 20.0, hematocrit=0.95, phi_pack=0.90)


def test_e_varies_with_time_in_a_varying_cross_section() -> None:
    """Spec §5.4: 'in a varying cross-section, E is a function of time'."""
    run = kin.descent(T090, 30.0, 0.45, include_lag=False, t_max_min=60.0)
    realised = [run.E_average(t) for t in (5.0, 15.0, 30.0, 55.0)]
    assert max(realised) - min(realised) > 0.1, realised


def test_model_and_record_disagree_on_when_E_peaks() -> None:
    """Unknown U09, asserted so the mismatch cannot be lost silently.

    The record puts the maximum near minute 15; this model's realised E climbs to a
    plateau and only falls once the boundary nears the ceiling.
    """
    run = kin.descent(T090, 13.0, 0.45, t_max_min=60.0)
    times = [t for t in np.linspace(run.lag_min + 1.0, 55.0, 60)]
    peak = max(times, key=run.E_average)
    assert peak > 25.0, (
        f"the model now peaks at {peak:.1f} min; if it peaks near 15 the record and "
        "the model agree and unknown U09 should be closed"
    )


def test_sensitivity_disagrees_with_the_record(sample_001: dict) -> None:
    """Unknown U10. Asserted as a known mismatch so it stays visible."""
    recorded = {
        int(e["esr_mm_h"]): float(e["dh_dESR"])
        for e in sample_001["readout_sensitivity"]["entries"]
    }
    model = {esr: kin.sensitivity(T090, float(esr), 15.0).value for esr in recorded}
    assert model[30] == pytest.approx(recorded[30], rel=0.1), "they agree near ESR 30"
    assert abs(model[13] - recorded[13]) > 0.15, "and disagree at ESR 13"
    assert abs(model[40] - recorded[40]) > 0.15, "and at ESR 40"


def test_sample1_heights_at_fixed_times(sample_001: dict) -> None:
    """Spec §10. GATED: the 10/15/20/30/45 min height table is not in the repo (M01)."""
    table = require_measured(
        sample_001, "raw_trace.heights_at_fixed_times_min",
        "tests/test_kinetics.py::test_sample1_heights_at_fixed_times",
    )
    esr = float(sample_001["range_consumption"].get("esr_mm_h") or 13.0)
    for t_min, expected in table.items():
        got = kin.height_at(T090, esr, float(t_min))
        assert got.value == pytest.approx(float(expected), abs=1.5), t_min


def test_range_consumption_89_to_91_percent(sample_001: dict) -> None:
    """Spec §10 and §5.5. GATED: sample 1's final height is not recorded (M02)."""
    final_height = require_measured(
        sample_001, "range_consumption.final_height_mm",
        "tests/test_kinetics.py::test_range_consumption_89_to_91_percent",
    )
    cone = geo.from_library(sample_001["range_consumption"]["tube"])
    ceiling = geo.range_ceiling(cone, 0.45).value
    consumed = 100.0 * float(final_height) / ceiling
    lo, hi = sample_001["range_consumption"]["acceptance_band_percent"]
    assert lo <= consumed <= hi, f"{consumed:.1f}% of a {ceiling:.2f} mm ceiling"


# ------------------------------------------------------------------ logistic fit


def test_logistic_recovers_known_parameters() -> None:
    t = np.linspace(0.0, 60.0, 25)
    h = kin.logistic(t, 32.0, 0.18, 22.0)
    fit = kin.fit_logistic(t, h)
    assert fit["H_max"].value == pytest.approx(32.0, rel=1e-3)
    assert fit["k"].value == pytest.approx(0.18, rel=1e-3)
    assert fit["t_mid"].value == pytest.approx(22.0, rel=1e-3)
    assert fit["r_squared"].value == pytest.approx(1.0, abs=1e-6)


def test_logistic_is_three_parameter_only() -> None:
    """Spec §5.1: the four-parameter form diverged and must not be offered."""
    assert kin.constants()["logistic"]["parameters"] == ["H_max", "k", "t_mid"]
    import inspect

    params = list(inspect.signature(kin.logistic).parameters)
    assert params == ["t", "h_max", "k", "t_mid"]


def test_logistic_refuses_too_few_points() -> None:
    fit = kin.fit_logistic([0.0, 10.0, 20.0], [0.0, 5.0, 9.0])
    assert fit["logistic_fit"].tier is Tier.UNKNOWN
    assert fit["logistic_fit"].experiment


def test_logistic_fit_is_calibrated_and_says_n_equals_1() -> None:
    t = np.linspace(0.0, 60.0, 20)
    fit = kin.fit_logistic(t, kin.logistic(t, 30.0, 0.2, 20.0))
    assert fit.tier is Tier.CALIBRATED
    assert all("N_EQUALS_1" in r.flags for r in fit)


# ------------------------------------------------------------ decisive experiment


def test_decisive_experiment_states_both_predictions_and_refuses_a_verdict() -> None:
    """Spec §9.3 and unknown U04."""
    exp = kin.decisive_experiment()
    assert exp["gap_arm_A"].value == pytest.approx(0.600, abs=0.002)
    assert exp["gap_arm_B"].value == pytest.approx(1.150, abs=0.003)
    assert exp["theta_both_arms"].value == pytest.approx(15.970, abs=1e-6)

    # PNK says E roughly halves; the empirical law says nothing changes.
    assert exp["PNK_prediction_ratio"].value == pytest.approx(0.54, abs=0.05)
    assert exp["empirical_prediction_ratio"].value == 1.0

    verdict = exp["E_law_verdict"]
    assert verdict.tier is Tier.UNKNOWN
    assert verdict.value is None
    assert "T060" in verdict.experiment


def test_decisive_experiment_reports_the_volume_confound() -> None:
    """Arm B is not iso-volume with arm A; the write-up has to say so."""
    exp = kin.decisive_experiment()
    assert exp["volume_arm_B"].value > 3500
    assert any("volume" in n for n in exp.notes)


def test_pnk_prediction_matches_the_spec_numbers() -> None:
    """Spec §G.1: PNK predicts 23.9 -> 12.9 across the two arms."""
    exp = kin.decisive_experiment()
    assert exp["E_PNK_arm_A"].value == pytest.approx(23.9, abs=0.1)
    assert exp["E_PNK_arm_B"].value == pytest.approx(12.9, abs=0.3)


# ------------------------------------------------------- integration with readout


def test_delta_h_readout_on_the_real_model_is_non_monotonic() -> None:
    """Spec §7.1, driven by the calibrated kinetics rather than a stand-in."""

    def delta_h(esr: float) -> float | None:
        run = kin.descent(T090, esr, 0.45, t_max_min=15.0)
        return run.height(15.0) - run.height(12.0)

    grid = [2.0 + 0.5 * i for i in range(60)]
    result = ro.detect_non_monotonic(delta_h, grid, name="delta_h_12_to_15min")
    assert result.value is False
    assert "NON_MONOTONIC" in result.flags


def test_delta_h_gives_about_43mm_at_esr_2() -> None:
    """Spec §7.1 quotes 4.3 mm for ESR 2 over the 12-15 minute window."""
    run = kin.descent(T090, 2.0, 0.45, t_max_min=15.0)
    assert run.height(15.0) - run.height(12.0) == pytest.approx(4.3, abs=0.4)


def test_fixed_time_readout_saturates_and_is_therefore_infeasible() -> None:
    """The two failures compose: saturation makes the ICSH top tertile unreachable."""
    from esrsim.calibration.validate import feasibility_check

    def reading(esr: float) -> float:
        return kin.descent(T090, esr, 0.45, t_max_min=15.0).height(15.0)

    check = feasibility_check(
        reading, geo.range_ceiling(T090, 0.45), label="fixed-time 15 min, T090"
    )
    assert check["icsh_2017_feasible"].value is False
