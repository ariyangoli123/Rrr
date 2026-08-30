"""Validation regression tests — ESR_SIMULATOR_SPEC.md §10, tests/test_validation.py."""

from __future__ import annotations

import math

import numpy as np
import pytest

from esrsim.calibration import validate as val
from esrsim.core import geometry as geo
from esrsim.tiers import Tier

T090 = geo.from_library("T090")
CEILING = geo.range_ceiling(T090, hematocrit=0.45)


# ------------------------------------------------------------------- ICSH 2011


def test_icsh_2011_uses_5mm_not_6mm() -> None:
    """Spec §10 and §8.1. The acceptance limit is 5 mm."""
    assert val.ICSH_2011_LIMIT_MM == 5.0
    check = val.icsh_2011_check([0.0, 1.0, 2.0])
    assert check["acceptance_limit"].value == 5.0


def test_six_millimetre_is_refused_with_the_reason() -> None:
    """Build prompt: 'If 6 mm appears anywhere, refuse and explain'."""
    with pytest.raises(ValueError) as excinfo:
        val.icsh_2011_check([1.0, 2.0], limit_mm=6.0)
    message = str(excinfo.value)
    assert "EN ISO 13079" in message
    assert "contamination" in message
    assert "5" in message


def test_icsh_2011_boundary_is_inclusive_at_5mm() -> None:
    exactly_five = val.icsh_2011_check([5.0] * 10)
    assert exactly_five["pct_within_limit"].value == 100.0
    just_over = val.icsh_2011_check([5.0001] * 10)
    assert just_over["pct_within_limit"].value == 0.0


def test_icsh_2011_needs_95_percent() -> None:
    diffs = [1.0] * 94 + [9.0] * 6          # 94% within 5 mm
    assert val.icsh_2011_check(diffs)["passes"].value is False
    diffs = [1.0] * 96 + [9.0] * 4          # 96% within 5 mm
    assert val.icsh_2011_check(diffs)["passes"].value is True


def test_icsh_2011_refuses_to_judge_no_data() -> None:
    result = val.icsh_2011_check([])
    assert result["pct_within_limit"].tier is Tier.UNKNOWN


# ------------------------------------------------------------------- ICSH 2017


def test_icsh_2017_rejects_fewer_than_20_per_tertile() -> None:
    """Spec §10. 60 samples is not enough if they are not spread across the interval."""
    lumped = [10.0] * 40 + [50.0] * 15 + [100.0] * 15   # 70 samples, tertile 2 short
    check = val.icsh_2017_design_check(lumped)
    assert check["n_total"].value == 70
    assert check["n_tertile_2"].value < val.ICSH_2017_MIN_PER_TERTILE
    assert check["passes"].value is False
    assert "ICSH_2017_DESIGN_FAIL" in check["passes"].flags


def test_icsh_2017_accepts_a_properly_spread_study() -> None:
    spread = (
        list(np.linspace(2, 40, 22))
        + list(np.linspace(41, 80, 21))
        + list(np.linspace(81, 120, 21))
    )
    check = val.icsh_2017_design_check(spread)
    assert check["n_total"].value == 64
    assert all(
        check[f"n_tertile_{i}"].value >= val.ICSH_2017_MIN_PER_TERTILE
        for i in (1, 2, 3)
    )
    assert check["passes"].value is True


def test_icsh_2017_rejects_fewer_than_60_total() -> None:
    spread = list(np.linspace(2, 120, 59))
    assert val.icsh_2017_design_check(spread)["passes"].value is False


def test_icsh_2017_does_not_count_unresolved_samples_as_measurements() -> None:
    samples = [val.StudySample(reference_esr=e, resolved=e < 60) for e in
               np.linspace(2, 120, 66)]
    check = val.icsh_2017_design_check(samples)
    assert check["n_unresolved"].value > 0
    assert "UNRESOLVED_SAMPLES" in check["n_unresolved"].flags


# ------------------------------------------------------------ proportional bias


def test_proportional_bias_causes_failure() -> None:
    """Spec §10 and §8.2: proportional bias is an automatic fail."""
    rng = np.random.default_rng(11)
    truth = rng.uniform(2, 120, 90)
    reference = truth + rng.normal(0, 1.0, 90)
    device = 1.25 * truth + rng.normal(0, 1.0, 90)     # 25 percent proportional bias

    pb = val.passing_bablok(reference, device)
    assert pb["proportional_bias"].value is True
    assert "PROPORTIONAL_BIAS" in pb["proportional_bias"].flags

    ba = val.bland_altman(reference, device)
    assert "PROPORTIONAL_BIAS" in ba["ba_proportional_bias_pvalue"].flags

    report = val.validation_report(reference, device)
    assert report["icsh_overall_pass"].value is False


def test_unbiased_method_passes_everything() -> None:
    rng = np.random.default_rng(5)
    truth = np.concatenate([
        rng.uniform(2, 41, 22), rng.uniform(41, 81, 22), rng.uniform(81, 120, 22)
    ])
    reference = truth + rng.normal(0, 0.8, truth.size)
    device = truth + rng.normal(0, 0.8, truth.size)
    report = val.validation_report(reference, device)
    assert report["icsh_overall_pass"].value is True


def test_passing_bablok_recovers_a_known_slope() -> None:
    rng = np.random.default_rng(2)
    truth = rng.uniform(2, 120, 80)
    x = truth + rng.normal(0, 1.0, 80)
    y = 1.25 * truth + rng.normal(0, 1.25, 80)
    pb = val.passing_bablok(x, y)
    assert pb["pb_slope"].value == pytest.approx(1.25, rel=0.06)


def test_passing_bablok_confidence_interval_covers_the_truth() -> None:
    """A nominal 95 percent interval should cover the true slope about 95 percent
    of the time when both methods carry error, which is the model it assumes."""
    covered = 0
    trials = 120
    for seed in range(trials):
        rng = np.random.default_rng(seed)
        truth = rng.uniform(2, 120, 60)
        x = truth + rng.normal(0, 1.2, 60)
        y = truth + rng.normal(0, 1.2, 60)
        pb = val.passing_bablok(x, y)
        covered += pb["pb_slope_ci_low"].value <= 1.0 <= pb["pb_slope_ci_high"].value
    assert 0.88 <= covered / trials <= 1.0, f"coverage {covered / trials:.3f}"


def test_bland_altman_bias_and_limits() -> None:
    x = np.arange(1.0, 61.0)
    y = x + 3.0
    ba = val.bland_altman(x, y)
    assert ba["ba_bias"].value == pytest.approx(3.0)
    assert ba["ba_sd_of_differences"].value == pytest.approx(0.0, abs=1e-9)


def test_passing_bablok_refuses_too_few_points() -> None:
    pb = val.passing_bablok([1.0, 2.0], [1.0, 2.0])
    assert pb["pb_slope"].tier is Tier.UNKNOWN


# ------------------------------------------------------------ feasibility check


def test_fixed_time_readout_cannot_run_the_icsh_2017_study() -> None:
    """Build prompt: with a 30-35 mm ceiling and a fixed-time readout, ESR 60 and
    ESR 120 both sit at the ceiling, so the top tertile cannot be filled."""

    def reading(esr: float) -> float:
        lag = max(1.5, 14.5 - 5.85 * math.log10(esr))
        return min(CEILING.value, 4.0 + (esr / 60.0) * 3.063 * max(0.0, 15.0 - lag))

    check = val.feasibility_check(reading, CEILING, label="fixed-time 15 min")
    assert check["icsh_2017_feasible"].value is False
    assert "ICSH_2017_INFEASIBLE" in check.flags
    verdict = check["esr_above_top_tertile"]
    assert verdict.tier is Tier.UNKNOWN and verdict.value is None
    assert "ceiling" in verdict.why_unknown


def test_infeasibility_is_stated_plainly_not_hidden_in_numbers() -> None:
    """The verdict must appear in the notes, not only as a flag on one row."""

    def reading(esr: float) -> float:
        return min(CEILING.value, 0.5 * esr)

    check = val.feasibility_check(reading, CEILING)
    assert any("CANNOT BE RUN" in n for n in check.notes)


def test_top_tertile_reading_span_is_reported() -> None:
    def reading(esr: float) -> float:
        return min(CEILING.value, 0.5 * esr)

    check = val.feasibility_check(reading, CEILING)
    # ESR 81..120 all clamp to the ceiling, so the span is zero.
    assert check["reading_span_top_tertile"].value == pytest.approx(0.0, abs=1e-9)


def test_time_to_threshold_readout_is_feasible() -> None:
    """A monotonic, unsaturable readout resolves the top tertile."""

    def time_to_threshold(esr: float) -> float:
        lag = max(1.5, 14.5 - 5.85 * math.log10(esr))
        return -(lag + 6.0 / ((esr / 60.0) * 3.063))   # negated: higher ESR reads higher

    check = val.feasibility_check(
        time_to_threshold, CEILING, resolution_mm=0.01, label="time-to-threshold"
    )
    assert check["icsh_2017_feasible"].value is True


def test_feasibility_inherits_the_ceiling_tier() -> None:
    check = val.feasibility_check(lambda e: 0.1 * e, CEILING)
    assert check["range_ceiling"].tier is Tier.ESTIMATED
