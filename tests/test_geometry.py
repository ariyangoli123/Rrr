import math

import numpy as np
import pytest

from bloodsed.geometry import (
    Bulb, Cylinder, Hourglass, Profile, Stepped, Taper, from_spec, get_geometry,
    PRESETS, GEOMETRY_SETS,
)
from bloodsed.units import MM


def test_cylinder_volume_is_analytic():
    tube = Cylinder(200, 2.5)
    expected = math.pi * (1.25e-3) ** 2 * 0.2
    assert tube.volume() == pytest.approx(expected, rel=1e-10)


def test_frustum_volume_is_analytic():
    tube = Taper(200, 1.2, 4.0)
    r0, r1, length = 0.6e-3, 2.0e-3, 0.2
    expected = math.pi * length / 3.0 * (r0 * r0 + r0 * r1 + r1 * r1)
    assert tube.volume() == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("name", list(PRESETS))
def test_cell_volumes_sum_to_the_tube_volume(name):
    tube = get_geometry(name)
    faces = np.linspace(0.0, tube.length, 401)
    assert tube.cell_volumes(faces).sum() == pytest.approx(tube.volume(), rel=1e-4)


def test_height_of_volume_inverts_volume_below():
    tube = Taper(200, 1.0, 5.0)
    for height in (0.02, 0.11, 0.19):
        volume = tube.volume_below(height)
        assert tube.height_of_volume(volume) == pytest.approx(height, abs=1e-4)


def test_stepped_profile_is_piecewise_constant():
    tube = Stepped([(50, 1.0), (50, 3.0)])
    assert tube.diameter(0.01) == pytest.approx(1.0 * MM)
    assert tube.diameter(0.07) == pytest.approx(3.0 * MM)
    assert tube.length == pytest.approx(0.1)


def test_hourglass_is_narrowest_at_the_throat():
    tube = Hourglass(200, 4.0, 1.0, 0.5)
    z = np.linspace(0, tube.length, 501)
    assert z[int(np.argmin(tube.radius(z)))] == pytest.approx(0.1, abs=1e-3)


def test_bulb_widens_only_near_its_centre():
    tube = Bulb(200, 2.5, 6.0, 0.5, 0.1)
    assert tube.diameter(0.1) == pytest.approx(6.0 * MM, rel=1e-6)
    assert tube.diameter(0.0) == pytest.approx(2.5 * MM, rel=1e-3)


def test_profile_interpolates_and_round_trips_through_csv(tmp_path):
    path = tmp_path / "tube.csv"
    path.write_text("height_mm,diameter_mm\n0,1.0\n100,3.0\n")
    tube = Profile.from_csv(path)
    assert tube.length == pytest.approx(0.1)
    assert tube.diameter(0.05) == pytest.approx(2.0 * MM)


def test_mean_diameter_averages_over_the_requested_span():
    tube = Taper(100, 1.0, 3.0)
    assert tube.mean_diameter(0.0, tube.length) == pytest.approx(2.0 * MM, rel=1e-6)


@pytest.mark.parametrize("spec,expected_length_mm", [
    ("westergren", 200.0),
    ("cylinder:L=150,D=2.5", 150.0),
    ("cone:L=200,Dbot=1.2,Dtop=4", 200.0),
    ("hourglass:L=180,Dend=4,Dthroat=1,at=0.4", 180.0),
    ("bulb:L=200,D=2.5,Dbulge=6,pos=0.5,width=0.1", 200.0),
    ("stepped:20x1,180x3", 200.0),
])
def test_specs_parse(spec, expected_length_mm):
    assert from_spec(spec).length == pytest.approx(expected_length_mm * MM)


def test_tilt_can_be_added_to_any_spec():
    assert from_spec("westergren:tilt=3").tilt_deg == 3.0
    assert from_spec("cylinder:L=200,D=2.5,tilt=7").tilt_deg == 7.0
    assert from_spec("stepped:20x1,180x3,tilt=5").tilt_deg == 5.0


@pytest.mark.parametrize("spec", ["nope", "cylinder:L=200", "westergren:D=3",
                                  "stepped:20-1", "cylinder:200,2.5"])
def test_bad_specs_raise(spec):
    with pytest.raises(ValueError):
        from_spec(spec)


def test_geometry_sets_only_name_real_presets():
    for members in GEOMETRY_SETS.values():
        for name in members:
            assert name in PRESETS


@pytest.mark.parametrize("kwargs", [
    {"length_mm": 0, "diameter_mm": 2.0},
    {"length_mm": 100, "diameter_mm": -1.0},
])
def test_degenerate_cylinders_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Cylinder(**kwargs)


def test_profile_needs_increasing_heights_from_zero():
    with pytest.raises(ValueError):
        Profile([10, 0], [1, 2])
    with pytest.raises(ValueError):
        Profile([5, 10], [1, 2])


@pytest.mark.parametrize("text,count", [
    ("westergren", 1),
    ("westergren,wintrobe", 2),
    ("westergren,westergren:tilt=3,westergren:tilt=15", 3),
    ("cone:L=200,Dbot=1.2,Dtop=4", 1),
    ("stepped:20x1,180x3", 1),
    ("westergren;cone:L=200,Dbot=1,Dtop=4", 2),
    ("cone:L=200,Dbot=1,Dtop=4;stepped:20x1,180x3", 2),
])
def test_spec_lists_split_on_the_right_commas(text, count):
    from bloodsed.geometry import split_specs
    specs = split_specs(text)
    assert len(specs) == count
    for spec in specs:
        from_spec(spec)  # every fragment must parse on its own
