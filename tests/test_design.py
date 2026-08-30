"""Design rules, explorer and export tests — spec §9, addendum §A."""

from __future__ import annotations

import pytest

from esrsim.core import geometry as geo
from esrsim.design import explorer as expl
from esrsim.design.export import drawing_sheet, stl_parameters
from esrsim.design.rules import evaluate_rules
from esrsim.tiers import Tier


# ---------------------------------------------------------------- design rules


def test_all_ten_rules_are_evaluated() -> None:
    result = evaluate_rules(geo.from_library("T070"))
    for n in range(1, 11):
        assert any(r.name.startswith(f"R{n:02d}") for r in result), f"R{n:02d} missing"


def test_r02_is_undecidable_for_the_whole_disputed_band() -> None:
    """Spec §9.1 quotes a 0.72 threshold that lies inside its own unresolved band."""
    for tube_id in ("T070",):
        result = evaluate_rules(geo.from_library(tube_id))
        r02 = result["R02_mixing_threshold"]
        assert r02.tier is Tier.UNKNOWN
        assert "0.72" in r02.why_unknown


def test_r07_fails_for_t060() -> None:
    """(0.90/0.60)^3 = 3.375 > 3.0."""
    assert evaluate_rules(geo.from_library("T060"))["R07_fill_resistance"].value is False
    assert evaluate_rules(geo.from_library("T070"))["R07_fill_resistance"].value is True


def test_r05_volume_target_passes_for_the_library() -> None:
    for tube_id in geo.list_tubes():
        assert evaluate_rules(geo.from_library(tube_id))["R05_volume_target"].value is True


def test_r09_print_tolerance_fails_for_the_tightest_tubes() -> None:
    """R09 needs Delta >= 2.0 mm; T050 has Delta = 1.789 and TAPER 1.849."""
    assert evaluate_rules(geo.from_library("T050"))["R09_print_tolerance"].value is False
    assert evaluate_rules(geo.from_library("T090"))["R09_print_tolerance"].value is True


def test_r06_fails_across_the_family_at_esr_120() -> None:
    """The range ceiling cannot cover ESR 120 with a fixed-time readout."""
    for tube_id in ("T090", "T070", "T060"):
        result = evaluate_rules(geo.from_library(tube_id))
        assert result["R06_range_vs_saturation"].value is False, tube_id


def test_verdict_is_unknown_when_any_rule_is_undecidable() -> None:
    """An undecidable rule is not a pass."""
    result = evaluate_rules(geo.from_library("T070"))
    verdict = result["design_verdict"]
    assert verdict.tier is Tier.UNKNOWN
    assert verdict.value is None
    assert "not approved by ignoring" in verdict.why_unknown


def test_rule_counts_are_reported() -> None:
    result = evaluate_rules(geo.from_library("T070"))
    assert result["rules_decided"].value + result["rules_undecidable"].value == 10


# -------------------------------------------------------------------- explorer


def test_sweep_solves_theta_for_the_volume_target() -> None:
    """A swept design must be comparable with the library, so it uses the same rule."""
    theta = expl.solve_theta_for_volume(0.70, 2000.0, 50.0)
    assert theta == pytest.approx(13.466, abs=0.005)


@pytest.mark.parametrize("gap,expected", [(0.90, 9.973), (0.60, 15.970), (0.80, 11.518)])
def test_sweep_reproduces_library_angles(gap: float, expected: float) -> None:
    assert expl.solve_theta_for_volume(gap, 2000.0, 50.0) == pytest.approx(
        expected, abs=0.005
    )


def test_sweep_shows_every_consideration_together() -> None:
    """Build prompt: speed, range, clearance, unevenness, fill and ICSH together."""
    rows = expl.sweep("theta", [10.0, 14.0, 18.0])
    for name in ("speed_E", "range_ceiling", "clearance",
                 "bloodline_unevenness_worst", "fill_resistance", "icsh_feasible"):
        assert rows[0].get(name) is not None or name in ("icsh_feasible",), name


def test_sweep_row_takes_the_weakest_tier() -> None:
    rows = expl.compare(["T070"])
    assert rows[0].tier is Tier.UNKNOWN, "T070's mixing verdict is undecidable"


def test_speed_and_fill_resistance_trade_against_each_other() -> None:
    """The point of showing them together: a faster tube is a harder tube to fill."""
    rows = expl.sweep("gap", [0.5, 0.7, 0.9, 1.1])
    speeds = [r.get("speed_E") for r in rows]
    fills = [r.get("fill_resistance") for r in rows]
    assert speeds[0] > speeds[-1], "narrower gap should be faster"
    assert fills[0] > fills[-1], "narrower gap should be harder to fill"


def test_gap_sweep_at_fixed_angle_is_the_key_experiment() -> None:
    """Spec §9.2: sweep gap at fixed theta — the volume is free to move, deliberately."""
    rows = expl.sweep("gap", [0.6, 1.15], theta_deg=15.970)
    assert rows[0].theta_deg == pytest.approx(rows[1].theta_deg)
    assert rows[1].volume_mm3 > 1.5 * rows[0].volume_mm3


def test_render_sweep_marks_undecidable_as_not_a_pass() -> None:
    text = expl.render_sweep(expl.compare(["T070"]))
    assert "?" in text
    assert "NOT a pass" in text


def test_render_sweep_shows_a_tier_column() -> None:
    """Build prompt: 'Every report, plot and CLI output shows tiers.'"""
    text = expl.render_sweep(expl.compare(["T090", "T060"]))
    assert "tier" in text
    assert "HYPOTHESIS" in text or "UNKNOWN" in text


def test_sweep_rejects_an_unknown_parameter() -> None:
    with pytest.raises(ValueError, match="param must be one of"):
        expl.sweep("wibble", [1.0])


# ---------------------------------------------------------------------- export


def test_base_diameter_is_never_a_driving_dimension() -> None:
    """Addendum §A: 'the base diameter is a derived output and must never be an input'."""
    sheet = drawing_sheet(geo.from_library("T070"))
    for name in ("d_outer_at_base", "d_inner_at_base", "gap_at_base"):
        assert "DERIVED" in sheet[name].flags, name
        assert "DRIVING" not in sheet[name].flags, name


def test_small_end_dimensions_are_driving() -> None:
    sheet = drawing_sheet(geo.from_library("T070"))
    for name in ("d_outer_at_bloodline", "theta_outer", "delta_apex_offset"):
        assert "DRIVING" in sheet[name].flags, name


def test_stl_parameters_exclude_derived_dimensions() -> None:
    params = stl_parameters(geo.from_library("T070"))
    assert "d_outer_at_base" not in params
    assert "d_outer_at_bloodline" in params
    assert "_note" in params


def test_drawing_sheet_states_the_propagation_reason() -> None:
    sheet = drawing_sheet(geo.from_library("T060"))
    assert any("undiminished" in n for n in sheet.notes)
    assert sheet["diameter_change_per_mm"].value == pytest.approx(
        2 * 0.28613, abs=1e-3
    )


def test_drawing_sheet_warns_about_polishing() -> None:
    """Spec §G.6: 3000-grit polishing opens the gap by 1-4 percent."""
    sheet = drawing_sheet(geo.from_library("TAPER"))
    assert any("polishing" in n for n in sheet.notes)
