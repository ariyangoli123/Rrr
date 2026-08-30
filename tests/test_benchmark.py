"""Benchmark tests — v1.1 addendum §D, marked CRITICAL in the spec."""

from __future__ import annotations

import pytest

from esrsim.core import benchmark as bench
from esrsim.core import geometry as geo
from esrsim.tiers import Tier


def test_plain_tube_equivalent_is_714mm_and_LD_701() -> None:
    """Addendum §D: A = 40.0 mm^2, D = 7.14 mm, L/D = 7.01."""
    plain = bench.plain_tube_equivalent(geo.from_library("T090"))
    assert plain["plain_area"].value == pytest.approx(40.0, abs=0.01)
    assert plain["plain_diameter"].value == pytest.approx(7.14, abs=0.01)
    assert plain["plain_L_over_D"].value == pytest.approx(7.01, abs=0.01)


def test_plain_tube_sits_below_the_saturation_ceiling() -> None:
    """Addendum §D's explanation of why the ratio is 1.4 and not 5."""
    plain = bench.plain_tube_equivalent(geo.from_library("T090"))
    assert plain["plain_below_saturation_ceiling"].value is True


def test_E_plain_matches_the_addendum_table() -> None:
    """Addendum §D: 2.20, 2.50, 2.89 at 9.973, 12.520 and 15.970 degrees."""
    plain = bench.PlainTube(volume_mm3=2000.0, length_mm=50.0)
    for theta, want in ((9.973, 2.20), (12.520, 2.50), (15.970, 2.89)):
        assert bench.E_plain(theta, plain).value == pytest.approx(want, abs=0.01), theta


def test_cone_over_plain_ratios_are_135_to_150() -> None:
    """Addendum §D: 1.43, 1.50, 1.35 — not 5."""
    for tube_id, want in (("T090", 1.43), ("TAPER", 1.50), ("T060", 1.35)):
        result = bench.benchmark(geo.from_library(tube_id))
        assert result["cone_over_plain_tilted"].value == pytest.approx(
            want, abs=0.02
        ), tube_id


def test_range_advantage_is_129_to_140() -> None:
    """Addendum §D: the cone reads 1.29-1.40x the plain tube's range."""
    for tube_id in ("T090", "TAPER", "T060"):
        advantage = bench.benchmark(geo.from_library(tube_id))["range_advantage"].value
        assert 1.25 <= advantage <= 1.45, f"{tube_id}: {advantage:.3f}"


def test_tilt_warning_is_printed_and_says_34() -> None:
    """Addendum §D's mandatory warning: the same plain tube at 20 deg reaches ~3.34."""
    warning = bench.tilt_warning()
    assert warning.value == pytest.approx(3.34, abs=0.02)
    assert "TILT_CLAIM_NOT_DEFENSIBLE" in warning.flags
    assert any("NOT DEFENSIBLE" in n for n in warning.notes)
    assert any("US 5,594,164" in n for n in warning.notes)


def test_benchmark_always_carries_the_tilt_warning() -> None:
    for tube_id in geo.list_tubes():
        result = bench.benchmark(geo.from_library(tube_id))
        assert result.get("tilt_comparison_warning") is not None, tube_id


def test_defensible_claims_are_named() -> None:
    """Addendum §D lists what CAN be claimed; the tool must say so, not just refuse."""
    notes = " ".join(bench.tilt_warning().notes)
    assert "vertical position" in notes
    assert "mounting angle" in notes
    assert "range" in notes


def test_E_plain_is_never_labelled_a_measurement() -> None:
    """Addendum §D: E_plain is a PNK prediction. The control has never been run."""
    plain = bench.PlainTube(volume_mm3=2000.0, length_mm=50.0)
    result = bench.E_plain(12.0, plain)
    assert result.tier is Tier.ESTIMATED
    assert any("NOT A MEASUREMENT" in n for n in result.notes)
    assert any("U12" in n for n in result.notes)


def test_ratio_is_a_measurement_over_a_prediction_and_says_so() -> None:
    result = bench.benchmark(geo.from_library("T090"))
    ratio = result["cone_over_plain_tilted"]
    assert ratio.tier is Tier.ESTIMATED, "a measurement/prediction ratio is not CALIBRATED"
    assert any("PREDICTION" in n for n in ratio.notes)


def test_benchmark_reports_both_references() -> None:
    """Addendum §D: 'every performance claim must be reported against TWO references'."""
    result = bench.benchmark(geo.from_library("T060"))
    assert result.get("cone_over_vertical_westergren") is not None
    assert result.get("cone_over_plain_tilted") is not None


def test_plain_tube_E_is_measured_not_predicted(sample_001: dict, require_measured) -> None:
    """GATED: the control run does not exist (M04 / U12).

    When it is committed, this test becomes the check that the measured plain-tube E
    agrees with the PNK prediction the whole benchmark currently leans on.
    """
    from esrsim.registry import DATA_ROOT
    import yaml

    path = DATA_ROOT / "measured" / "benchmark_control.yaml"
    record = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    measured_e = require_measured(
        record, "plain_tube.E_measured",
        "tests/test_benchmark.py::test_plain_tube_E_is_measured_not_predicted",
    )
    plain = bench.PlainTube(volume_mm3=2000.0, length_mm=50.0)
    for angle, value in measured_e.items():
        predicted = bench.E_plain(float(angle), plain).value
        assert value == pytest.approx(predicted, rel=0.25), angle
