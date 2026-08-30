"""Unit convention tests.

Build prompt: *"Geometry and physics in SI internally, millimetres at the boundary,
with the unit convention documented once and obeyed everywhere."* These tests hold the
boundary, because a surface tension in N/m composed with a density in kg/m^3 returns
metres, and returning that as "2.32" is exactly the class of error the spec's
regression tests exist to catch.
"""

from __future__ import annotations

import math

import pytest

from esrsim import units as u
from esrsim.core.fluid import K_constant, capillary_length, load_fluid


def test_gravity_is_the_value_the_project_record_uses() -> None:
    """Addendum §C's pressure table reproduces only with 9.81, not 9.80665."""
    assert u.G == 9.81
    blood = load_fluid("blood_fresh")
    driver = blood.rho.value * u.G * u.mm_to_m(50.0)
    assert driver == pytest.approx(519.0, abs=1.0)


@pytest.mark.parametrize(
    "forward,back,value",
    [
        (u.mm_to_m, u.m_to_mm, 12.345),
        (u.m_to_mm, u.mm_to_m, 0.0123),
        (u.mm3_to_m3, u.m3_to_mm3, 2000.0),
        (u.deg_to_rad, u.rad_to_deg, 13.466),
    ],
)
def test_conversions_round_trip(forward, back, value) -> None:
    assert back(forward(value)) == pytest.approx(value, rel=1e-12)


def test_conversion_magnitudes_are_the_right_way_round() -> None:
    """A sign or direction error here would be invisible in a round trip."""
    assert u.mm_to_m(1000.0) == pytest.approx(1.0)
    assert u.m_to_mm(1.0) == pytest.approx(1000.0)
    assert u.mm2_to_m2(1e6) == pytest.approx(1.0)
    assert u.mm3_to_m3(1e9) == pytest.approx(1.0)
    assert u.deg_to_rad(180.0) == pytest.approx(math.pi)


def test_esr_to_velocity() -> None:
    """ESR 60 mm/h is 1 mm/min."""
    assert u.mm_per_hour_to_mm_per_min(60.0) == pytest.approx(1.0)
    assert u.minutes_to_seconds(2.5) == pytest.approx(150.0)


def test_capillary_length_comes_back_in_millimetres() -> None:
    """The SI-internal / mm-boundary rule, checked on the quantity that breaks it.

    sqrt(sigma/(rho g)) with sigma in N/m is metres. Blood's is 2.32e-3 m; the boundary
    must report 2.32 mm, not 0.00232.
    """
    lc = capillary_length(load_fluid("blood_fresh"))
    assert lc.unit == "mm"
    assert lc.value == pytest.approx(2.32, abs=0.01)
    assert 1.0 < lc.value < 10.0, "returned in metres instead of millimetres"


def test_K_constant_comes_back_in_square_millimetres() -> None:
    k = K_constant(load_fluid("blood_fresh"))
    assert k.unit == "mm^2"
    assert k.value == pytest.approx(10.76, abs=0.02)


# ------------------------------------------------------------------------ guards


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
def test_check_positive_rejects(bad: float) -> None:
    with pytest.raises(ValueError, match="finite and > 0"):
        u.check_positive("gap", bad)


def test_check_positive_accepts() -> None:
    assert u.check_positive("gap", 0.7) == 0.7


@pytest.mark.parametrize("bad", [-0.01, 1.01, 45.0, float("nan")])
def test_check_fraction_rejects(bad: float) -> None:
    with pytest.raises(ValueError, match="volume fraction"):
        u.check_fraction("hematocrit", bad)


def test_check_fraction_message_warns_about_percentages() -> None:
    """Hct 45 instead of 0.45 is the commonest way to poison a calculation here."""
    with pytest.raises(ValueError, match="never a percentage"):
        u.check_fraction("hematocrit", 45.0)


@pytest.mark.parametrize("bad", [0.0, 90.0, 120.0, -5.0, float("nan")])
def test_check_angle_rejects(bad: float) -> None:
    with pytest.raises(ValueError, match="half-angle"):
        u.check_angle_deg("theta", bad)


def test_check_angle_accepts_the_library_range() -> None:
    for theta in (9.973, 13.466, 16.232):
        assert u.check_angle_deg("theta", theta) == theta
