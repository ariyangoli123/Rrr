import numpy as np
import pytest

from bloodsed.blood import BloodProperties
from bloodsed.geometry import Cylinder, Stepped, Taper, get_geometry
from bloodsed.inclination import BoycottModel
from bloodsed.solver import SimulationConfig, simulate
from bloodsed.units import mm_per_hour

IDEAL = BloodProperties(aggregation_time_s=0.0)


def ideal_config(**kwargs) -> SimulationConfig:
    """No lag, no wall drag -- the cases with a closed-form answer."""
    base = dict(duration_h=1.0, n_cells=400, wall_correction=False,
                aggregation_lag=False)
    base.update(kwargs)
    return SimulationConfig(**base)


def test_cell_volume_is_conserved_to_machine_precision():
    for name in ("westergren", "funnel", "inverted-funnel", "hourglass", "stepped"):
        result = simulate(get_geometry(name), IDEAL, ideal_config())
        assert result.mass_error < 1e-12


def test_concentration_stays_physical_everywhere():
    for name in ("funnel", "hourglass", "stepped", "waist"):
        result = simulate(get_geometry(name), IDEAL, ideal_config(duration_h=3.0))
        assert result.phi.min() >= 0.0
        assert result.phi.max() <= IDEAL.max_packing + 1e-12


def test_free_settling_falls_at_the_stokes_velocity():
    """With hindrance switched off the boundary must fall at u0.

    The tolerance is looser than for the hindered law on purpose: a linear flux
    makes the front a contact discontinuity, which a first-order upwind scheme
    smears.  The laws that describe real blood are non-linear, and their front
    is a self-sharpening shock -- see the Kynch test below, which is exact.
    """
    result = simulate(Cylinder(200, 2.5), IDEAL,
                      ideal_config(flux_law="free", duration_h=0.25))
    expected = 0.25 * mm_per_hour(IDEAL.stokes_velocity())
    assert result.esr(0.25) == pytest.approx(expected, rel=2e-2)


def test_free_settling_stops_when_the_boundary_meets_the_sediment():
    """A 45 % sample packed at 90 % fills half the tube; the fall caps there."""
    result = simulate(Cylinder(200, 2.5), IDEAL,
                      ideal_config(flux_law="free", duration_h=1.0))
    assert result.esr(1.0) == pytest.approx(200.0 * (1 - 0.45 / 0.9), rel=1e-3)
    assert result.mass_error < 1e-12


def test_hindered_settling_matches_the_kynch_shock_speed():
    result = simulate(Cylinder(200, 2.5), IDEAL, ideal_config())
    expected = mm_per_hour(IDEAL.stokes_velocity()) * (1 - 0.45 / 0.9) ** 4.65
    assert result.esr(1.0) == pytest.approx(expected, rel=1e-5)


@pytest.mark.parametrize("n_cells", [200, 400, 800])
def test_the_reading_does_not_depend_on_the_mesh(n_cells):
    result = simulate(Cylinder(200, 2.5), IDEAL, ideal_config(n_cells=n_cells))
    expected = mm_per_hour(IDEAL.stokes_velocity()) * (1 - 0.45 / 0.9) ** 4.65
    assert result.esr(1.0) == pytest.approx(expected, rel=1e-5)


def test_the_curve_advances_smoothly_rather_than_cell_by_cell():
    result = simulate(Cylinder(200, 2.5), IDEAL, ideal_config())
    steps = np.diff(result.fall_mm)
    assert steps.min() > 0
    assert steps.max() - steps.min() < 1e-6 * steps.mean()


def test_a_crowded_sample_settles_more_slowly():
    readings = []
    for hematocrit in (0.25, 0.45, 0.65):
        blood = BloodProperties(hematocrit=hematocrit, aggregation_time_s=0.0)
        readings.append(simulate(Cylinder(200, 2.5), blood, ideal_config()).esr(1.0))
    assert readings[0] > readings[1] > readings[2]


def test_geometry_changes_the_reading_in_the_expected_direction():
    """Narrowing downward concentrates the cells and slows them; widening dilutes."""
    cfg = ideal_config(duration_h=2.0)
    narrowing = simulate(Taper(200, 1.2, 4.0), IDEAL, cfg).esr(2.0)
    straight = simulate(Cylinder(200, 2.5), IDEAL, cfg).esr(2.0)
    widening = simulate(Taper(200, 4.0, 1.2), IDEAL, cfg).esr(2.0)
    assert narrowing < straight < widening


def test_a_downward_narrowing_tube_concentrates_the_suspension():
    """More cells arrive from above than the shrinking area can pass on."""
    result = simulate(Taper(200, 1.2, 4.0), IDEAL, ideal_config(duration_h=2.0))
    z = result.z_centers
    band = (z > 0.03) & (z < 0.08)
    assert result.phi[-1][band].mean() > IDEAL.hematocrit


def test_a_downward_widening_tube_dilutes_the_suspension():
    result = simulate(Taper(200, 4.0, 1.2), IDEAL, ideal_config(duration_h=2.0))
    z = result.z_centers
    band = (z > 0.05) & (z < 0.12)
    assert result.phi[-1][band].mean() < IDEAL.hematocrit


def test_a_throat_throttles_the_whole_column():
    cfg = ideal_config(duration_h=2.0)
    throttled = simulate(get_geometry("hourglass"), IDEAL, cfg).esr(2.0)
    straight = simulate(Cylinder(200, 2.5), IDEAL, cfg).esr(2.0)
    assert throttled < straight


def test_tilting_the_tube_inflates_the_reading():
    upright = simulate(Cylinder(200, 2.5), IDEAL, ideal_config()).esr(1.0)
    tilted = simulate(Cylinder(200, 2.5, tilt_deg=3.0), IDEAL, ideal_config()).esr(1.0)
    steep = simulate(Cylinder(200, 2.5, tilt_deg=15.0), IDEAL, ideal_config()).esr(1.0)
    assert upright < tilted < steep
    assert 1.2 < tilted / upright < 1.6  # the ~30 % clinical rule of thumb


def test_the_boycott_model_can_be_switched_off():
    cfg = ideal_config(boycott=BoycottModel(model="none"))
    upright = simulate(Cylinder(200, 2.5), IDEAL, cfg).esr(1.0)
    tilted = simulate(Cylinder(200, 2.5, tilt_deg=30.0), IDEAL, cfg).esr(1.0)
    assert tilted < upright  # only the cosine of gravity remains


def test_the_rouleaux_lag_delays_the_fall():
    slow = BloodProperties(aggregation_time_s=900.0)
    with_lag = simulate(Cylinder(200, 2.5), slow,
                        SimulationConfig(duration_h=1.0, n_cells=400,
                                         wall_correction=False)).esr(1.0)
    without = simulate(Cylinder(200, 2.5), IDEAL, ideal_config()).esr(1.0)
    assert with_lag < without


def test_the_sediment_grows_from_the_bottom():
    result = simulate(Cylinder(200, 2.5), IDEAL, ideal_config(duration_h=4.0))
    assert result.sediment_mm[0] == pytest.approx(0.0)
    assert np.all(np.diff(result.sediment_mm) >= -1e-9)
    assert result.sediment_mm[-1] > 5.0


def test_partial_filling_shortens_the_column_but_not_the_rate():
    full = simulate(Cylinder(200, 2.5), IDEAL, ideal_config())
    half = simulate(Cylinder(200, 2.5), IDEAL, ideal_config(fill_fraction=0.5))
    assert half.fill_height == pytest.approx(0.1, abs=1e-3)
    assert half.esr(1.0) == pytest.approx(full.esr(1.0), rel=2e-3)


def test_the_column_settles_out_and_comes_to_rest():
    result = simulate(Cylinder(50, 2.5), IDEAL, ideal_config(duration_h=12.0))
    assert result.phi[-1].max() > 0.9 * IDEAL.max_packing
    assert result.fall_mm[-1] > 0.5 * 50.0 * (1 - 0.45 / 0.9)
    settled = result.fall_mm[-1] - result.fall_mm[-10]
    assert settled < 0.02 * result.fall_mm[-1]


def test_a_step_in_the_bore_does_not_break_conservation():
    tube = Stepped([(40, 1.0), (60, 4.0), (100, 2.0)])
    result = simulate(tube, IDEAL, ideal_config(duration_h=2.0, n_cells=300))
    assert result.mass_error < 1e-12
    assert result.phi.max() <= IDEAL.max_packing + 1e-12


@pytest.mark.parametrize("kwargs", [
    {"n_cells": 4},
    {"cfl": 1.5},
    {"duration_h": 0},
    {"fill_fraction": 0.0},
    {"sample_interval_s": 0},
])
def test_impossible_configs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        SimulationConfig(**kwargs)
