import numpy as np
import pytest

from bloodsed.flux import FLUX_LAWS, make_flux_law, wall_factor


@pytest.mark.parametrize("name", sorted(FLUX_LAWS))
def test_flux_vanishes_at_both_ends(name):
    law = make_flux_law(name, 4.65, 0.9)
    assert law.shape(np.array([0.0]))[0] == pytest.approx(0.0)
    assert law.shape(np.array([0.9]))[0] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("name", sorted(FLUX_LAWS))
def test_flux_is_unimodal_which_is_what_godunov_needs(name):
    law = make_flux_law(name, 4.65, 0.9)
    phi = np.linspace(0.0, 0.9, 2001)
    psi = law.shape(phi)
    peak = int(np.argmax(psi))
    assert np.all(np.diff(psi[:peak + 1]) >= -1e-12)
    assert np.all(np.diff(psi[peak:]) <= 1e-12)
    assert law.phi_star == pytest.approx(phi[peak], abs=2e-3)


@pytest.mark.parametrize("name", sorted(FLUX_LAWS))
def test_a_packed_cell_can_accept_nothing(name):
    law = make_flux_law(name, 4.65, 0.9)
    assert law.supply(np.array([0.9]))[0] == pytest.approx(0.0, abs=1e-12)
    assert law.godunov(np.array([0.45]), np.array([0.9]))[0] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("name", sorted(FLUX_LAWS))
def test_godunov_never_exceeds_what_either_side_allows(name):
    law = make_flux_law(name, 4.65, 0.9)
    grid = np.linspace(0.0, 0.9, 61)
    above, below = np.meshgrid(grid, grid, indexing="ij")
    flux = law.godunov(above.ravel(), below.ravel())
    assert np.all(flux >= -1e-15)
    assert np.all(flux <= law.psi_max + 1e-12)
    # nothing settles out of clear plasma
    assert np.all(law.godunov(np.zeros(10), grid[:10]) == pytest.approx(0.0))


def test_hindered_settling_slows_down_as_the_suspension_crowds():
    law = make_flux_law("hindered-packing", 4.65, 0.9)
    speeds = law.settling_velocity(np.array([0.05, 0.2, 0.45, 0.7]))
    assert np.all(np.diff(speeds) < 0)
    assert speeds[2] == pytest.approx((1 - 0.45 / 0.9) ** 4.65, rel=1e-12)


def test_free_settling_has_no_hindrance():
    law = make_flux_law("free", 4.65, 0.9)
    assert law.settling_velocity(np.array([0.45]))[0] == pytest.approx(1.0)


def test_wall_factor_shrinks_as_the_tube_narrows():
    factors = wall_factor(60e-6, np.array([10e-3, 2.5e-3, 1.0e-3, 0.3e-3]))
    assert np.all(np.diff(factors) < 0)
    assert factors[0] == pytest.approx(1.0, abs=0.02)
    assert np.all((factors > 0.0) & (factors <= 1.0))


def test_unknown_flux_law_names_the_alternatives():
    with pytest.raises(KeyError, match="hindered-packing"):
        make_flux_law("nope", 4.65, 0.9)
