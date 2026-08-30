"""Readout regression tests — ESR_SIMULATOR_SPEC.md §10, tests/test_readout.py.

The readout module is deliberately model-agnostic: it screens a *supplied* mapping.
These tests therefore use analytic mappings, so a failure here is a fault in the
screening logic and not in the kinetics. The integration tests that drive the screen
with the real calibrated kinetics live in tests/test_kinetics.py.
"""

from __future__ import annotations

import math

import pytest

from esrsim.core import geometry as geo
from esrsim.core import readout as ro
from esrsim.tiers import Tier

T090 = geo.from_library("T090")
CEILING = geo.range_ceiling(T090, hematocrit=0.45)


# --------------------------------------------------------------- monotonicity


def test_time_to_threshold_is_monotonic() -> None:
    """Spec §10. Time to cross a threshold falls monotonically as ESR rises.

    The mapping is probed in the "decreasing" sense, which is the sense a
    time-to-threshold readout actually has.
    """

    def time_to_10mm(esr: float) -> float:
        lag = max(1.5, 14.5 - 5.85 * math.log10(esr))
        rate = (esr / 60.0) * 3.06
        return lag + max(0.0, (10.0 - 4.0)) / rate

    result = ro.detect_non_monotonic(time_to_10mm, direction="decreasing")
    assert result.value is True
    assert "NON_MONOTONIC" not in result.flags
    assert result.tier is Tier.EXACT


def test_delta_h_is_flagged_non_monotonic() -> None:
    """Spec §10 and §7.1. Delta-h over a fixed window is non-monotonic in ESR.

    The mechanism is the haze phase: at low ESR the boundary only becomes readable
    *inside* the window, so it appears to jump the full 4 mm readable height plus a
    little; at moderate ESR it has been descending steadily since before the window
    opened, and the difference over three minutes is small. Delta-h therefore rises,
    collapses, and rises again.
    """

    def height(esr: float, t: float) -> float:
        lag = max(1.5, 14.5 - 5.85 * math.log10(esr))
        if t <= lag:
            return 0.0
        return 4.0 + (esr / 60.0) * 3.06 * (t - lag)

    def delta_h(esr: float) -> float:
        return height(esr, 15.0) - height(esr, 12.0)

    result = ro.detect_non_monotonic(delta_h, name="delta_h_12_to_15min")
    assert result.value is False
    assert "NON_MONOTONIC" in result.flags
    assert any("COLLISION" in n for n in result.notes), result.notes


def test_delta_h_collision_is_reported_with_both_rates() -> None:
    """The failure must be visible, with the two ESRs that share a reading named."""

    def delta_h(esr: float) -> float:
        lag = lambda e: max(1.5, 14.5 - 5.85 * math.log10(e))  # noqa: E731
        h = lambda e, t: 0.0 if t <= lag(e) else 4.0 + (e / 60.0) * 3.06 * (t - lag(e))  # noqa: E731
        return h(esr, 15.0) - h(esr, 12.0)

    result = ro.detect_non_monotonic(delta_h)
    collisions = [n for n in result.notes if n.startswith("COLLISION")]
    assert collisions
    # The widest collision must span a clinically meaningful stretch of ESR.
    first = collisions[0]
    lo = float(first.split("ESR ")[1].split(" ")[0])
    hi = float(first.split("ESR ")[2].split(" ")[0])
    assert hi - lo > 10.0, f"collision spans only {hi - lo:g} mm/h: {first}"


def test_delta_h_readout_is_refused_outright() -> None:
    """A non-invertible readout must yield UNKNOWN, never a number."""

    def delta_h(esr: float) -> float:
        lag = max(1.5, 14.5 - 5.85 * math.log10(esr))
        h = lambda t: 0.0 if t <= lag else 4.0 + (esr / 60.0) * 3.06 * (t - lag)  # noqa: E731
        return h(15.0) - h(12.0)

    verdict = ro.accept_readout(delta_h, ro.ReadoutMode.DELTA_H)
    assert verdict.tier is Tier.UNKNOWN
    assert verdict.value is None
    assert "NON_MONOTONIC" in verdict.flags
    assert verdict.experiment


def test_monotonic_readout_is_accepted() -> None:
    verdict = ro.accept_readout(lambda e: 0.25 * e, ro.ReadoutMode.FIXED_TIME_HEIGHT)
    assert verdict.value is True
    assert verdict.tier is Tier.EXACT


def test_detector_refuses_to_judge_an_empty_mapping() -> None:
    result = ro.detect_non_monotonic(lambda _e: None)
    assert result.tier is Tier.UNKNOWN
    assert result.experiment


def test_flat_mapping_is_accepted_as_monotonic_but_useless() -> None:
    """A constant reading is technically non-decreasing; the collision scan is what
    exposes it, not the monotonicity test."""
    flat = ro.detect_non_monotonic(lambda _e: 7.0)
    assert flat.value is True


# ------------------------------------------------------------------ saturation


def test_saturation_detected_above_esr_55_at_15min() -> None:
    """Spec §10. T090 at the 15-minute readout saturates above ESR 55.

    The height model is the one the spec describes in §5.3 and §5.4: a haze phase
    ending at 4.0 mm, then descent at u_s * E with E at its saturated value.
    """

    def height_at_15(esr: float) -> float:
        lag = max(1.5, 14.5 - 5.85 * math.log10(esr))
        ceiling = CEILING.value
        return min(ceiling, 4.0 + (esr / 60.0) * 3.063 * max(0.0, 15.0 - lag))

    assert ro.saturation_check(height_at_15(55.0), CEILING).tier is Tier.UNKNOWN
    assert ro.saturation_check(height_at_15(45.0), CEILING).tier is not Tier.UNKNOWN


def test_saturation_returns_no_number() -> None:
    """Spec §7.3: 'do not return a numeric value'."""
    saturated = ro.saturation_check(0.99 * CEILING.value, CEILING)
    assert saturated.tier is Tier.UNKNOWN
    assert saturated.value is None
    assert "SATURATED" in saturated.flags
    assert "phi_pack" in saturated.experiment or "time-to-threshold" in saturated.experiment


def test_saturation_headroom_reported_when_not_saturated() -> None:
    head = ro.saturation_check(0.5 * CEILING.value, CEILING)
    assert head.value == pytest.approx(50.0, abs=0.1)


def test_saturation_inherits_the_ceiling_tier() -> None:
    """The ceiling rides on phi_pack, so headroom cannot be EXACT."""
    head = ro.saturation_check(10.0, CEILING)
    assert head.tier is Tier.ESTIMATED


# ---------------------------------------------------------------- error budget


def test_area_ratio_and_level_shift_signs() -> None:
    """Spec §7.2: reading from a mark loses -Delta*r, from the surface +Delta*(1-r)."""
    errs = ro.level_shift_error(T090, h_interface_mm=15.0, delta_level_mm=2.0)
    r = errs["area_ratio_r"].value
    assert 0.4 <= r <= 0.6
    assert errs["error_from_mark"].value == pytest.approx(-2.0 * r)
    assert errs["error_from_surface"].value == pytest.approx(2.0 * (1.0 - r))


def test_both_methods_cost_about_half_the_shift_at_mid_column() -> None:
    """Addendum §E: 'both methods give roughly 0.5*Delta'."""
    errs = ro.level_shift_error(T090, h_interface_mm=15.0, delta_level_mm=2.0)
    for key in ("error_from_mark", "error_from_surface"):
        assert abs(errs[key].value) == pytest.approx(1.0, abs=0.15), key


def test_the_half_shift_rule_is_flagged_where_it_stops_holding() -> None:
    """Near the ceiling r falls below 0.35 and the two methods stop being equivalent."""
    errs = ro.level_shift_error(T090, h_interface_mm=CEILING.value, delta_level_mm=2.0)
    assert errs["area_ratio_r"].value < 0.35
    assert any("OUTSIDE the 0.4-0.6 band" in n for n in errs.notes)


def test_esr_error_reproduces_the_recorded_table() -> None:
    """Spec §7.2 validation table: ESR 13 / 30 / 40 against Delta = 1, 2, 3 mm.

    Only the *sensitivity* column is taken from the record; the height error is
    computed here from the geometry, so this checks the propagation arithmetic.
    """
    expected = {
        (13, 1.0): 0.6, (13, 2.0): 1.2, (13, 3.0): 1.8,
        (30, 1.0): 0.8, (30, 2.0): 1.7, (30, 3.0): 2.5,
        (40, 1.0): 1.8, (40, 2.0): 3.6, (40, 3.0): 5.4,
    }
    sensitivity = {13: 0.83, 30: 0.60, 40: 0.28}
    # The record's own arithmetic uses 0.5*Delta as the height error, i.e. r = 0.5.
    h_iface = geo.from_library("T090").height_for_volume(
        geo.from_library("T090").cumulative_volume(15.0)
    )
    for (esr, delta), want in expected.items():
        err = ro.esr_error_from_level_shift(T090, h_iface, delta, sensitivity[esr])
        assert err.value == pytest.approx(want, rel=0.12), (esr, delta, err.value)


def test_esr_error_refuses_when_sensitivity_is_zero() -> None:
    err = ro.esr_error_from_level_shift(T090, 20.0, 2.0, 0.0)
    assert err.tier is Tier.UNKNOWN
    assert err.value is None


def test_error_budget_flags_the_model_record_mismatch() -> None:
    """Unknown U10: the volumetric model's sensitivity does not match the record.

    The budget must print both and flag the disagreement rather than reconcile it.
    """
    model = [(13.0, 8.7, 0.49), (30.0, 18.0, 0.60), (40.0, 24.2, 0.63)]
    budget = ro.error_budget(T090, CEILING, model)
    assert "MODEL_RECORD_MISMATCH" in budget.flags
    mismatch = budget["model_vs_record_sensitivity"]
    assert mismatch.value >= 2, "ESR 13 and 40 disagree; 30 agrees"
    assert any("U10" in n for n in budget.notes) or "U10" in mismatch.source


def test_error_budget_fill_target_is_30mm3_for_t090() -> None:
    """Addendum §E: Delta < 2 mm on T090 (A = 14.76 mm^2) is 30 mm^3 = 1.5% of volume."""
    budget = ro.error_budget(T090, CEILING, [(13.0, 8.7, 0.83)])
    assert budget["fill_volume_for_design_target"].value == pytest.approx(30.0, abs=0.5)
    assert budget["fill_tolerance_pct_of_volume"].value == pytest.approx(1.5, abs=0.05)


def test_recorded_sensitivity_is_calibrated_and_says_n_equals_1() -> None:
    rec = ro.recorded_sensitivity()
    assert rec.tier is Tier.CALIBRATED
    assert all(any("n = 1" in n for n in r.notes) for r in rec)


def test_recorded_sensitivity_extrapolates_outside_its_fitted_range() -> None:
    """A CALIBRATED result evaluated outside its fitted range must retag itself."""
    rec = ro.recorded_sensitivity()["dh_dESR@ESR13"]
    assert rec.tier is Tier.CALIBRATED
    outside = rec.enforce_range(esr_mm_h=100.0)
    assert outside.tier is Tier.EXTRAPOLATED
    assert "EXTRAPOLATION_UNSAFE" in outside.flags


# ------------------------------------------------------------------- reporting


def test_delta_h_report_states_it_exists_only_to_fail() -> None:
    report = ro.readout_report(
        T090, ro.ReadoutMode.DELTA_H, lambda e: -abs(e - 30.0), CEILING
    )
    assert any("demonstrate its failure" in n for n in report.notes)
    assert report.tier is Tier.UNKNOWN
