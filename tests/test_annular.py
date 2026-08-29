"""The cone-inside-a-cone geometry and the inclined-wall settling it produces."""

import math

import numpy as np
import pytest

from bloodsed.blood import BloodProperties
from bloodsed.geometry import AnnularCone, Cylinder, Taper, from_spec, get_geometry
from bloodsed.inclination import BoycottModel
from bloodsed.solver import SimulationConfig, simulate
from bloodsed.units import MM

IDEAL = BloodProperties(aggregation_time_s=0.0)


def config(**kwargs) -> SimulationConfig:
    base = dict(duration_h=1.0, n_cells=300, aggregation_lag=False)
    base.update(kwargs)
    return SimulationConfig(**base)


# -- the shape ---------------------------------------------------------
def test_the_flow_area_is_the_annulus_between_the_cones():
    tube = AnnularCone(100, 10.0, 15.0, 2.0)
    z = 0.05
    outer = float(tube.radius(z))
    inner = float(tube.inner_radius(z))
    assert inner > 0
    assert float(tube.area(z)) == pytest.approx(math.pi * (outer ** 2 - inner ** 2))


def test_the_gap_is_measured_perpendicular_to_the_wall():
    angle = 30.0
    tube = AnnularCone(100, 12.0, angle, 2.0)
    radial = float(tube.radius(0.03) - tube.inner_radius(0.03))
    assert radial == pytest.approx(2.0 * MM / math.cos(math.radians(angle)), rel=1e-9)


def test_the_hydraulic_diameter_is_twice_the_gap():
    tube = AnnularCone(100, 12.0, 0.0, 2.0)
    assert float(tube.hydraulic_diameter(0.05)) == pytest.approx(2 * 2.0 * MM, rel=1e-9)


def test_a_plain_tube_still_reports_its_bore():
    tube = Cylinder(100, 2.5)
    assert float(tube.hydraulic_diameter(0.05)) == pytest.approx(2.5 * MM, rel=1e-12)
    assert not tube.has_core


def test_the_annulus_has_a_core_and_a_smaller_volume_than_the_outer_cone():
    tube = AnnularCone(100, 10.0, 12.0, 1.5)
    assert tube.has_core
    outer_only = Taper(100, 10.0, 2 * float(tube.radius(tube.length)) / MM)
    assert tube.volume() < outer_only.volume()


def test_cones_that_would_meet_are_rejected():
    """An inner cone steeper than the outer one closes the gap part way up."""
    with pytest.raises(ValueError, match="no room for blood"):
        AnnularCone(100, 10.0, 8.0, 1.0, inner_angle_deg=20.0)


def test_a_gap_wider_than_the_bottom_just_starts_the_core_higher_up():
    """The inner cone's tip sits above the floor; the section below is a circle."""
    tube = AnnularCone(100, 1.0, 10.0, 3.0)
    assert tube.tip_height > 0.0
    assert float(tube.inner_radius(0.0)) == 0.0
    assert float(tube.inner_radius(tube.length)) > 0.0


def test_the_annulus_parses_from_a_spec():
    tube = from_spec("annulus:L=150,D=6,angle=20,gap=1.5")
    assert tube.length == pytest.approx(0.15)
    assert tube.angle_deg == 20.0
    assert tube.gap == pytest.approx(1.5 * MM)


# -- the projected wall area ------------------------------------------
def test_a_straight_tube_has_no_inclined_wall():
    assert Cylinder(200, 2.5).wall_projection(0.0, 0.2) == pytest.approx(0.0)


def test_a_cone_projects_the_circle_it_opens_out_to():
    tube = Taper(200, 1.2, 4.0)
    expected = math.pi * ((2.0 * MM) ** 2 - (0.6 * MM) ** 2)
    assert tube.wall_projection(0.0, tube.length) == pytest.approx(expected, rel=1e-3)


def test_a_bulge_projects_both_of_its_shoulders():
    tube = get_geometry("bulb")
    one_way = math.pi * ((3.0 * MM) ** 2 - (1.25 * MM) ** 2)
    assert tube.wall_projection(0.0, tube.length) == pytest.approx(2 * one_way, rel=0.02)


def test_the_annular_cone_projects_both_of_its_walls():
    tube = AnnularCone(100, 10.0, 15.0, 2.0)
    outer = math.pi * (float(tube.radius(tube.length)) ** 2 - float(tube.radius(0)) ** 2)
    inner = math.pi * (float(tube.inner_radius(tube.length)) ** 2
                       - float(tube.inner_radius(0)) ** 2)
    assert tube.wall_projection(0.0, tube.length) == pytest.approx(outer + inner, rel=1e-3)


# -- what it does to the settling -------------------------------------
def test_a_vertical_annulus_settles_like_a_plain_tube():
    """No inclined surface anywhere, so nothing to enhance."""
    straight = simulate(get_geometry("annular-straight"), IDEAL, config()).esr(1.0)
    plain = simulate(Cylinder(120, 8.0), IDEAL, config()).esr(1.0)
    assert straight == pytest.approx(plain, rel=0.05)


def test_inclining_the_walls_speeds_the_column_up():
    readings = [
        simulate(AnnularCone(120, 8.0, angle, 1.5), IDEAL, config()).esr(1.0)
        for angle in (0.0, 6.0, 12.0, 25.0)
    ]
    assert readings == sorted(readings)
    assert readings[-1] > 2 * readings[0]


def test_a_narrower_gap_clears_plasma_faster():
    readings = [
        simulate(AnnularCone(120, 8.0, 12.0, gap), IDEAL, config()).esr(1.0)
        for gap in (3.0, 1.5, 0.6)
    ]
    assert readings == sorted(readings)


def test_the_wall_term_can_be_switched_off():
    cfg_on = config()
    cfg_off = config(boycott=BoycottModel(walls=False))
    tube = get_geometry("annular-cone")
    assert simulate(tube, IDEAL, cfg_on).esr(1.0) > 1.5 * simulate(
        get_geometry("annular-cone"), IDEAL, cfg_off).esr(1.0)


def test_switching_the_wall_term_off_leaves_a_straight_tube_alone():
    tube = Cylinder(200, 2.5)
    with_walls = simulate(tube, IDEAL, config()).esr(1.0)
    without = simulate(Cylinder(200, 2.5), IDEAL, config(boycott=BoycottModel(walls=False))).esr(1.0)
    assert with_walls == pytest.approx(without, rel=1e-9)


def test_tilting_an_annular_settler_adds_to_its_own_incline():
    upright = simulate(AnnularCone(120, 8.0, 12.0, 1.5), IDEAL, config()).esr(1.0)
    tilted = simulate(AnnularCone(120, 8.0, 12.0, 1.5, tilt_deg=20.0), IDEAL,
                      config()).esr(1.0)
    assert tilted > upright


def test_the_annulus_conserves_cell_volume_and_stays_physical():
    result = simulate(get_geometry("annular-cone"), IDEAL, config(duration_h=3))
    assert result.mass_error < 1e-12
    assert result.phi.min() >= 0.0
    assert result.phi.max() <= IDEAL.max_packing + 1e-12
