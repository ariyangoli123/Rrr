"""The flow field: two phases, one closed vessel, no net volume flux."""

import numpy as np
import pytest

from bloodsed.blood import BloodProperties
from bloodsed.flows import (
    cell_flux, local_velocity_scale, peak_velocities, plasma_throughput,
    tracer_positions, velocity_field, velocity_field_mm_per_hour,
)
from bloodsed.geometry import Cylinder, get_geometry
from bloodsed.solver import SimulationConfig, simulate
from bloodsed.units import HOUR, MM

BLOOD = BloodProperties()


@pytest.fixture(scope="module")
def result():
    return simulate(Cylinder(200, 2.5), BLOOD, SimulationConfig(duration_h=2, n_cells=300))


def test_the_two_phases_balance_exactly(result):
    """phi * v_cells = (1 - phi) * v_plasma -- nothing enters or leaves."""
    phi = result.phi[30]
    cells, plasma = velocity_field(result, 30)
    assert np.allclose(phi * cells, (1.0 - phi) * plasma, atol=1e-15)


def test_cells_fall_and_plasma_rises(result):
    cells, plasma = velocity_field(result, 30)
    assert np.all(cells >= 0)
    assert np.all(plasma >= 0)


def test_nothing_moves_in_the_clear_plasma_or_the_packed_sediment(result):
    index = 60
    cells, plasma = velocity_field(result, index)
    z = result.z_centers
    above = z > result.interface[index] + 5 * MM
    assert np.all(cells[above] < 1e-12)      # m/s -- nothing measurable
    assert np.all(plasma[above] < 1e-12)


def test_a_lone_cell_falls_at_the_stokes_velocity(result):
    """Where the suspension is vanishingly dilute, hindrance disappears."""
    cells, _ = velocity_field_mm_per_hour(result, 60)
    phi = result.phi[60]
    dilute = (phi > 1e-6) & (phi < 1e-3)
    if dilute.any():
        free = result.stokes_velocity / MM * HOUR * result.wall_factors[dilute]
        assert np.allclose(cells[dilute], free, rtol=1e-6)


def test_the_bulk_falls_at_the_hindered_velocity(result):
    """Richardson-Zaki, retarded by the wall and ramped by rouleaux formation."""
    index = 20
    cells, _ = velocity_field_mm_per_hour(result, index)
    phi = result.phi[index]
    bulk = np.isclose(phi, BLOOD.hematocrit, atol=1e-6)
    expected = (result.stokes_velocity / MM * HOUR
                * (1 - BLOOD.hematocrit / BLOOD.max_packing) ** BLOOD.hindrance_exponent
                * result.wall_factors[bulk].mean()
                * result.aggregation[index])
    assert cells[bulk].mean() == pytest.approx(expected, rel=1e-3)


def test_the_flux_is_the_product_of_the_two(result):
    phi = result.phi[30]
    cells, _ = velocity_field(result, 30)
    assert np.allclose(cell_flux(result, 30), phi * cells, atol=1e-18)


def test_the_velocity_scale_carries_the_enhancement():
    tilted = simulate(Cylinder(200, 2.5, tilt_deg=20.0), BLOOD,
                      SimulationConfig(duration_h=1, n_cells=200))
    upright = simulate(Cylinder(200, 2.5), BLOOD,
                       SimulationConfig(duration_h=1, n_cells=200))
    assert local_velocity_scale(tilted, 30).max() > local_velocity_scale(upright, 30).max()


def test_a_throat_forces_the_plasma_through_a_smaller_opening():
    result = simulate(get_geometry("hourglass"), BLOOD,
                      SimulationConfig(duration_h=2, n_cells=300))
    _, plasma = velocity_field_mm_per_hour(result, 40)
    z = result.z_centers
    throat = np.argmin(np.abs(z - 0.1))
    wide = np.argmin(np.abs(z - 0.17))
    assert plasma[throat] > plasma[wide]


def test_throughput_is_reported_in_millilitres(result):
    flow = plasma_throughput(result, 40)
    assert flow.max() > 0
    assert flow.max() < 1.0  # a 1 mL tube cannot move litres per hour


def test_the_headline_numbers_are_finite(result):
    summary = peak_velocities(result, 40)
    assert summary["cells_max_mm_per_h"] > 0
    assert summary["plasma_max_mm_per_h"] > 0
    assert summary["time_min"] == pytest.approx(40.0)


def test_tracers_land_inside_the_blood(result):
    tracers = tracer_positions(result, 40, n_cells=50, n_plasma=30)
    for key, count in (("cells", 50), ("plasma", 30)):
        heights, speeds = tracers[key]
        assert len(heights) == count
        assert np.all(heights >= 0) and np.all(heights <= result.fill_height + 1e-9)
        assert np.all(speeds >= 0)
