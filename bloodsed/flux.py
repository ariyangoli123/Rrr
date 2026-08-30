"""Hindered settling flux laws and the numerical (Godunov) flux.

Batch sedimentation of a suspension is a scalar conservation law
(Kynch, 1952).  For a red-cell volume fraction ``phi`` the downward solids
flux is

    f(phi) = u0 * psi(phi)          [m/s]

where ``u0`` is the single-aggregate Stokes velocity and ``psi`` is a
dimensionless *shape function* that vanishes twice: at ``phi = 0`` (no cells to
carry) and at ``phi = phi_max`` (the sediment is packed and cannot compact
further).  Between those it has a single maximum -- that unimodality is what
lets us use the supply/demand form of the Godunov flux, which is monotone,
mass conservative, and keeps ``phi`` inside ``[0, phi_max]`` even where the
tube contracts and funnels cells into a smaller cross-section.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class FluxLaw:
    """Base class: a unimodal, dimensionless settling flux ``psi(phi)``."""

    name = "flux"

    def __init__(self, exponent: float, max_packing: float):
        if max_packing <= 0.0 or max_packing > 1.0:
            raise ValueError("max_packing must be in (0, 1]")
        self.exponent = float(exponent)
        self.max_packing = float(max_packing)
        self._phi_star = self._peak_location()
        self._psi_max = float(self.shape(np.array([self._phi_star]))[0])

    # -- to implement --------------------------------------------------
    def shape(self, phi: np.ndarray) -> np.ndarray:
        """Dimensionless downward solids flux, ``f / u0`` [-]."""
        raise NotImplementedError

    def _peak_location(self) -> float:
        raise NotImplementedError

    # -- derived -------------------------------------------------------
    @property
    def phi_star(self) -> float:
        """Concentration of maximum flux; separates 'free' from 'congested'."""
        return self._phi_star

    @property
    def psi_max(self) -> float:
        """Maximum dimensionless flux."""
        return self._psi_max

    def settling_velocity(self, phi: np.ndarray) -> np.ndarray:
        """Velocity of the cells themselves, ``f / phi`` [-] (times ``u0``)."""
        phi = np.asarray(phi, dtype=float)
        out = np.zeros_like(phi)
        nz = phi > 0.0
        out[nz] = self.shape(phi[nz]) / phi[nz]
        out[~nz] = 1.0
        return out

    def demand(self, phi: np.ndarray) -> np.ndarray:
        """How much flux the cell *above* a face can send [-].

        Free-flowing suspension sends what it carries; a congested cell can
        still unload at the maximum rate.
        """
        phi = np.asarray(phi, dtype=float)
        return np.where(phi <= self._phi_star, self.shape(phi), self._psi_max)

    def supply(self, phi: np.ndarray) -> np.ndarray:
        """How much flux the cell *below* a face can accept [-].

        This is what stops the sediment growing past ``phi_max`` and what makes
        a constriction jam: ``supply(phi_max) = 0``.
        """
        phi = np.asarray(phi, dtype=float)
        return np.where(phi <= self._phi_star, self._psi_max, self.shape(phi))

    def godunov(self, phi_above: np.ndarray, phi_below: np.ndarray) -> np.ndarray:
        """Downward dimensionless flux across a face [-], never negative.

        The exact Godunov flux for a unimodal flux function is
        ``min(demand of the sender, supply of the receiver)``.
        """
        return np.minimum(self.demand(phi_above), self.supply(phi_below))

    def max_wave_speed(self) -> float:
        """Bound on ``|d psi / d phi|``, used for the CFL condition."""
        return 1.0

    def describe(self) -> str:
        return (
            f"{self.name}(n={self.exponent:g}, phi_max={self.max_packing:g}): "
            f"peak flux {self.psi_max:.4f}*u0 at phi={self.phi_star:.3f}"
        )


class HinderedPacking(FluxLaw):
    """``psi = phi * (1 - phi/phi_max)^n``.

    The flux goes to zero smoothly at the packing limit, so the sediment builds
    up as a continuous compaction front.  This is the default.
    """

    name = "hindered-packing"

    def shape(self, phi: np.ndarray) -> np.ndarray:
        phi = np.asarray(phi, dtype=float)
        x = np.clip(phi / self.max_packing, 0.0, 1.0)
        return np.clip(phi, 0.0, None) * (1.0 - x) ** self.exponent

    def _peak_location(self) -> float:
        return self.max_packing / (self.exponent + 1.0)


class RichardsonZaki(FluxLaw):
    """``psi = phi * (1 - phi)^n``, cut off at the packing limit.

    The textbook hindered settling correlation.  The cut-off makes the sediment
    a sharp plug at exactly ``phi_max``.
    """

    name = "richardson-zaki"

    def shape(self, phi: np.ndarray) -> np.ndarray:
        phi = np.asarray(phi, dtype=float)
        x = np.clip(phi, 0.0, 1.0)
        psi = x * (1.0 - x) ** self.exponent
        return np.where(phi >= self.max_packing, 0.0, psi)

    def _peak_location(self) -> float:
        peak = 1.0 / (self.exponent + 1.0)
        return min(peak, self.max_packing * (1.0 - 1e-9))


class FreeSettling(FluxLaw):
    """``psi = phi`` -- no hindrance at all, only the packing limit.

    Not physical for whole blood; useful as a teaching/verification case
    because the interface then falls at exactly the Stokes velocity.
    """

    name = "free"

    def shape(self, phi: np.ndarray) -> np.ndarray:
        phi = np.asarray(phi, dtype=float)
        return np.where(phi >= self.max_packing, 0.0, np.clip(phi, 0.0, None))

    def _peak_location(self) -> float:
        return self.max_packing * (1.0 - 1e-9)


FLUX_LAWS: dict[str, type[FluxLaw]] = {
    "hindered-packing": HinderedPacking,
    "richardson-zaki": RichardsonZaki,
    "free": FreeSettling,
}


def make_flux_law(name: str, exponent: float, max_packing: float) -> FluxLaw:
    """Build a flux law by name."""
    try:
        cls = FLUX_LAWS[name]
    except KeyError:
        raise KeyError(
            f"unknown flux law {name!r}; available: {', '.join(sorted(FLUX_LAWS))}"
        ) from None
    return cls(exponent, max_packing)


def wall_factor(particle_diameter: float, tube_diameter: np.ndarray,
                floor: float = 0.05) -> np.ndarray:
    """Faxen wall-retardation factor for settling in a tube [-].

    ``K = 1 - 2.104 L + 2.089 L^3 - 0.948 L^5`` with ``L = d_particle/d_tube``.
    Negligible for a 60 um rouleau in a 2.5 mm Westergren tube (~5 %), but it
    is what makes a sub-millimetre capillary read low.
    """
    lam = np.clip(np.asarray(tube_diameter, dtype=float), 1e-12, None)
    lam = np.clip(particle_diameter / lam, 0.0, 0.8)
    k = 1.0 - 2.104 * lam + 2.089 * lam ** 3 - 0.948 * lam ** 5
    return np.clip(k, floor, 1.0)
