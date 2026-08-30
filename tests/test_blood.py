import math

import pytest

from bloodsed.blood import BloodProperties, get_blood
from bloodsed.units import mm_per_hour


def test_stokes_velocity_matches_the_closed_form():
    blood = BloodProperties(aggregate_diameter_um=60.0, aggregate_shape_factor=1.0,
                            plasma_viscosity=1.6e-3, rbc_density=1093.0,
                            plasma_density=1025.0)
    expected = 68.0 * 9.80665 * (60e-6) ** 2 / (18 * 1.6e-3)
    assert blood.stokes_velocity() == pytest.approx(expected, rel=1e-12)


def test_bigger_aggregates_settle_faster_as_the_square():
    small = BloodProperties(aggregate_diameter_um=50.0)
    large = BloodProperties(aggregate_diameter_um=100.0)
    assert large.stokes_velocity() == pytest.approx(4 * small.stokes_velocity(), rel=1e-12)


def test_creeping_flow_holds_for_every_preset():
    for blood in (get_blood(name) for name in
                  ("normal", "anemic", "inflammation", "severe-inflammation")):
        assert blood.reynolds_number() < 0.1


def test_normal_sample_lands_in_the_clinical_range():
    blood = get_blood("normal")
    hindered = mm_per_hour(blood.stokes_velocity()) * (1 - 0.45 / 0.9) ** 4.65
    assert 3.0 < hindered < 15.0


def test_aggregation_ramps_from_zero_to_one():
    blood = BloodProperties(aggregation_time_s=300.0)
    assert blood.aggregation_factor(0.0) == 0.0
    assert blood.aggregation_factor(300.0) == pytest.approx(1 - math.exp(-1))
    assert blood.aggregation_factor(1e5) == pytest.approx(1.0)
    assert BloodProperties(aggregation_time_s=0.0).aggregation_factor(0.0) == 1.0


@pytest.mark.parametrize("kwargs", [
    {"hematocrit": 1.2},
    {"hematocrit": 0.95, "max_packing": 0.9},
    {"aggregate_diameter_um": 0.0},
    {"plasma_viscosity": 0.0},
    {"rbc_density": 1000.0},
    {"max_packing": 1.5},
    {"aggregation_time_s": -1.0},
])
def test_impossible_samples_are_rejected(kwargs):
    with pytest.raises(ValueError):
        BloodProperties(**kwargs)


def test_unknown_preset_names_the_alternatives():
    with pytest.raises(KeyError, match="normal"):
        get_blood("nope")
