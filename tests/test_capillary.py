"""Capillary, pinning and mixing tests — ESR_SIMULATOR_SPEC.md §4, addendum §B/§C/§F."""

from __future__ import annotations

import math

import pytest

from esrsim.core import capillary as cap
from esrsim.core import fluid as fl
from esrsim.core import geometry as geo
from esrsim.tiers import Tier

BLOOD = fl.load_fluid("blood_fresh")
WATER = fl.load_fluid("water")
AGED = fl.load_fluid("blood_aged_2w")


# --------------------------------------------------------------- fluid constants


def test_capillary_length_of_blood_is_232mm() -> None:
    """Spec §3."""
    assert fl.capillary_length(BLOOD).value == pytest.approx(2.32, abs=0.01)


def test_K_constant_of_blood_is_1076mm2() -> None:
    """Spec §3."""
    assert fl.K_constant(BLOOD).value == pytest.approx(10.76, abs=0.02)


def test_derived_constants_inherit_the_estimated_tier() -> None:
    """sigma and rho for blood are literature values, so nothing derived is EXACT."""
    assert fl.capillary_length(BLOOD).tier is Tier.ESTIMATED
    assert fl.capillary_length(WATER).tier is Tier.EXACT


def test_driver_to_barrier_ratios_match_the_addendum() -> None:
    """Addendum §C: water 2.38, fresh blood 3.25, aged blood 3.76 at a 0.70 mm gap."""
    expected = ((WATER, 2.38), (BLOOD, 3.25), (AGED, 3.76))
    for fluid, want in expected:
        got = fl.driver_to_barrier_ratio(fluid, gap_mm=0.70, column_mm=50.0)
        assert got.value == pytest.approx(want, abs=0.02), fluid.key


def test_capillary_barrier_and_driver_pressures() -> None:
    """Addendum §C table: barrier 206/160/137 Pa, driver 489/519/515 Pa."""
    for fluid, barrier, driver in ((WATER, 206, 489), (BLOOD, 160, 519), (AGED, 137, 515)):
        assert fl.capillary_barrier(fluid, 0.70).value == pytest.approx(barrier, abs=1)
        assert fl.hydrostatic_driver(fluid, 50.0).value == pytest.approx(driver, abs=1)


def test_aged_blood_sigma_carries_its_unknown() -> None:
    """Unknown U06: the value is a direction, not a measurement."""
    assert any("U06" in n for n in AGED.sigma.notes)


def test_delta_cos_theta_is_flagged_contradictory() -> None:
    """Unknown U07: 0.41 from the lamella experiment against 0.14 from the cone."""
    assert "CONTRADICTORY_VALUE" in BLOOD.delta_cos_theta.flags
    assert any("0.41" in n for n in BLOOD.delta_cos_theta.notes)


# ------------------------------------------------------- blood-line unevenness


def test_both_unevenness_models_are_returned_with_no_winner() -> None:
    """Spec §4.1: both must be implemented and both results reported."""
    result = cap.blood_line_unevenness(geo.from_library("T060"))
    a = result["delta_h_model_A_eccentricity"]
    b = result["delta_h_model_B_hysteresis"]
    assert a.tier is Tier.HYPOTHESIS and b.tier is Tier.HYPOTHESIS
    assert any("NO WINNER" in n for n in result.notes)


def test_unevenness_models_match_the_spec_formulas() -> None:
    cone = geo.from_library("T060")
    k = fl.K_constant(BLOOD).value
    d = cone.gap_perpendicular(cone.x_bl_mm)
    e = 0.035
    result = cap.blood_line_unevenness(cone, eccentricity_mm=e)
    assert result["delta_h_model_A_eccentricity"].value == pytest.approx(
        k * 2 * e / (d * d - e * e), rel=1e-9
    )
    assert result["delta_h_model_B_hysteresis"].value == pytest.approx(
        k * BLOOD.delta_cos_theta.value / d, rel=1e-9
    )


def test_neither_model_explains_the_step_at_gap_090() -> None:
    """Spec §4.1: at gap 0.90 the observation is ~0 mm; both models predict ~1-2 mm."""
    result = cap.blood_line_unevenness(geo.from_library("T090"))
    assert result["observed_unevenness"].value == 0.0
    for name in ("delta_h_model_A_eccentricity", "delta_h_model_B_hysteresis"):
        assert result[f"{name}_minus_observed"].value > 0.5, name
        assert "UNRESOLVED_CONTRADICTION" in result[f"{name}_minus_observed"].flags


def test_unevenness_reports_the_evidence_favouring_model_b() -> None:
    """Spec §4.1 records it, so the report must carry it — without picking a winner."""
    result = cap.blood_line_unevenness(geo.from_library("T070"))
    assert any("flat on the first fill" in n for n in result.notes)
    assert any("unknown U08" in n or "U08" in n for n in result.notes)


def test_unevenness_refuses_when_eccentricity_closes_the_annulus() -> None:
    cone = geo.from_library("T050")
    result = cap.blood_line_unevenness(cone, eccentricity_mm=0.9)
    assert result["delta_h_model_A_eccentricity"].tier is Tier.UNKNOWN


# --------------------------------------------------------------- Gibbs pinning


def test_gibbs_range_matches_the_spec_table() -> None:
    """Spec §4.2: T090 80.0, T070 76.5, T060 74.0 degrees."""
    for tube_id, want in (("T090", 80.0), ("T070", 76.5), ("T060", 74.0)):
        result = cap.gibbs_pinning(geo.from_library(tube_id))
        assert result["gibbs_range"].value == pytest.approx(want, abs=0.05), tube_id


def test_pinning_margin_is_about_twofold() -> None:
    """Spec §4.2: blood hysteresis 20-40 deg gives roughly a 2x margin."""
    for tube_id in ("T090", "T070", "T060"):
        margin = cap.gibbs_pinning(geo.from_library(tube_id))["pinning_margin"].value
        assert 1.8 <= margin <= 2.1, tube_id


def test_gibbs_passes_design_rule_r03_for_every_tube() -> None:
    for tube_id in geo.list_tubes():
        result = cap.gibbs_pinning(geo.from_library(tube_id))
        assert result["R03_gibbs_range"].value is True, tube_id


def test_pinning_is_independent_of_step_width() -> None:
    """Addendum §B: 'Gibbs pinning strength is independent of w'."""
    cone = geo.from_library("T070")
    base = cap.gibbs_pinning(cone)["gibbs_range"].value
    for w in (0.20, 0.30, 1.00):
        step = geo.stepped_upper_cone(cone, w_mm=w)
        assert step["step_width_w"].value == w
        assert cap.gibbs_pinning(cone)["gibbs_range"].value == base


# ------------------------------------------------------------ bulge tolerance


def test_t090_bulge_tolerance_is_022_percent() -> None:
    """Spec §4.2 and addendum §E: V_bulge ~ 4.5 mm^3 = 0.22 percent of 2000."""
    bulge = cap.meniscus_bulge_tolerance(geo.from_library("T090"))
    assert bulge["V_bulge"].value == pytest.approx(4.5, abs=0.15)
    assert bulge["tolerance_pct_of_volume"].value == pytest.approx(0.22, abs=0.01)


def test_bulge_tolerance_forces_volumetric_filling() -> None:
    """The bulge absorbs far less than a 2 mm level shift, so 'pour to the line' fails."""
    cone = geo.from_library("T090")
    bulge = cap.meniscus_bulge_tolerance(cone)
    assert bulge["level_shift_equivalent"].value < 0.5
    assert any("VOLUMETRIC" in n for n in bulge.notes)


# ------------------------------------------------------------ mixing criterion


def test_mixing_returns_unknown_inside_the_disputed_band() -> None:
    """Spec §4.3: 0.696 passed and 0.766 failed, so 0.69-0.77 is undecidable."""
    for clearance in (0.70, 0.72, 0.76):
        verdict = cap.mixing_criterion(
            cap.MixingGeometry(True, clearance, clearance + 0.2)
        )
        assert verdict["mixing_passes"].tier is Tier.UNKNOWN, clearance
        assert "UNRESOLVED_CONTRADICTION" in verdict["mixing_passes"].flags


def test_mixing_passes_clearly_above_the_band() -> None:
    verdict = cap.mixing_criterion(cap.MixingGeometry(True, 0.914, 1.20))
    assert verdict["mixing_passes"].value is True


def test_mixing_fails_clearly_below_the_band() -> None:
    verdict = cap.mixing_criterion(cap.MixingGeometry(True, 0.512, 0.80))
    assert verdict["mixing_passes"].value is False


def test_mixing_fails_without_a_continuous_guide_surface() -> None:
    """Spec §4.3 condition 1, confirmed by the rod and the stepped cone."""
    verdict = cap.mixing_criterion(cap.MixingGeometry(False, 0.914, 1.20))
    assert verdict["mixing_passes"].value is False


def test_mixing_fails_on_a_constriction() -> None:
    """Spec §4.3 condition 2."""
    verdict = cap.mixing_criterion(cap.MixingGeometry(True, 0.914, 0.80))
    assert verdict["no_constriction"].value is False
    assert verdict["mixing_passes"].value is False


def test_mixing_never_fits_a_threshold_to_four_points() -> None:
    """Spec §F: 'with four points and entangled variables NO threshold may be fitted'."""
    verdict = cap.mixing_criterion(cap.MixingGeometry(True, 0.72, 0.95))
    why = verdict["mixing_threshold_met"].why_unknown
    assert "0.696" in why and "0.766" in why
    assert "U02" in why


def test_every_mixing_report_prints_the_validation_gap() -> None:
    """Addendum §C: 'the program must print this clause in every mixing report'."""
    verdict = cap.mixing_criterion(cap.MixingGeometry(True, 0.914, 1.20))
    basis = verdict["mixing_validation_basis"]
    assert "MIXING_VALIDATION_GAP" in basis.flags
    assert "blood_fresh" in str(basis.value)
    assert any("240" in n or "4 hours" in n for n in basis.notes)
    assert any("rest" in n.lower() for n in basis.notes)


def test_t070_with_the_approved_step_is_still_undecidable() -> None:
    """The approved design passed experimentally, but its clearance sits in the
    disputed band, so the criterion must still refuse to predict."""
    cone = geo.from_library("T070")
    step = geo.stepped_upper_cone(cone)
    verdict = cap.mixing_criterion(
        cap.MixingGeometry(
            True,
            step["clearance_working"].value,
            step["clearance_above_min"].value,
            "T070 + stepped upper cone",
        )
    )
    assert verdict["no_constriction"].value is True
    assert verdict["mixing_passes"].tier is Tier.UNKNOWN


# --------------------------------------------------------- refuted hypotheses


def test_refuted_hypotheses_are_listed_and_never_return_a_number() -> None:
    """Spec §4.3 and addendum §F."""
    assert "eotvos_bubble_passage" in cap.REFUTED_HYPOTHESES
    for name in cap.REFUTED_HYPOTHESES:
        note = cap.refuted_hypothesis_note(name)
        assert note.tier is Tier.UNKNOWN
        assert note.value is None
        assert "REFUTED" in note.why_unknown


def test_unknown_refuted_hypothesis_raises() -> None:
    with pytest.raises(KeyError):
        cap.refuted_hypothesis_note("something_made_up")


# ------------------------------------------------------------- fill resistance


def test_fill_resistance_scales_as_inverse_cube() -> None:
    """Spec §4.5, relative to the 0.90 mm reference."""
    assert cap.fill_resistance(geo.from_library("T090")).value == pytest.approx(1.0, abs=0.01)
    t060 = cap.fill_resistance(geo.from_library("T060")).value
    assert t060 == pytest.approx((0.90 / 0.60) ** 3, rel=0.01)
    assert t060 > 3.0, "T060 fails design rule R07"


def test_capillary_rise_is_K_over_d() -> None:
    cone = geo.from_library("T070")
    k = fl.K_constant(BLOOD).value
    d = cone.gap_perpendicular(cone.x_bl_mm)
    assert cap.capillary_rise(cone, BLOOD).value == pytest.approx(k / d, rel=1e-9)


def test_max_step_width_is_half_the_capillary_length() -> None:
    """Addendum §B: w ceiling = Lc/2 = 1.16 mm for blood."""
    w_max = cap.max_step_width(BLOOD)
    assert w_max.value == pytest.approx(1.16, abs=0.01)
    assert any("1.20" in n for n in w_max.notes), "the spec/addendum conflict must be noted"
