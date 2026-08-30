"""Blood properties and the single-aggregate (Stokes) settling velocity."""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

from .units import GRAVITY, UM, mm_per_hour


@dataclass
class BloodProperties:
    """Physical properties of the blood sample being sedimented.

    The two knobs that dominate the erythrocyte sedimentation rate are
    ``hematocrit`` (how crowded the suspension is) and
    ``aggregate_diameter_um`` (how big the rouleaux stacks are, which is what
    fibrinogen / acute phase proteins actually change).

    Attributes
    ----------
    hematocrit:
        Initial red-cell volume fraction of the well mixed sample, 0..1.
    plasma_density, rbc_density:
        Densities in kg/m^3.  Their difference drives the settling.
    plasma_viscosity:
        Plasma dynamic viscosity in Pa*s (~1.6e-3 at 20 C, ~1.2e-3 at 37 C).
    aggregate_diameter_um:
        Effective hydrodynamic diameter of a settling rouleau, in micrometres.
        A single erythrocyte is ~7.8 um; rouleaux of tens of cells reach
        50-150 um, which is why an inflamed sample settles so much faster.
    aggregate_shape_factor:
        Multiplier <= 1 on the Stokes velocity accounting for the non-spherical
        (stacked-coin) shape and internal permeability of a rouleau.
    max_packing:
        Red-cell volume fraction of the fully packed sediment.  Settling stops
        here, which is what produces the packed-cell column at the bottom.
    hindrance_exponent:
        Richardson-Zaki exponent ``n`` of the hindered settling law.
    aggregation_time_s:
        Time constant of the rouleaux formation lag phase.  Set to 0 to start
        at full speed.
    temperature_c:
        Recorded for reporting only; viscosity is not derived from it.
    """

    hematocrit: float = 0.45
    plasma_density: float = 1025.0
    rbc_density: float = 1093.0
    plasma_viscosity: float = 1.6e-3
    aggregate_diameter_um: float = 60.0
    aggregate_shape_factor: float = 0.6
    max_packing: float = 0.90
    hindrance_exponent: float = 4.65
    aggregation_time_s: float = 300.0
    temperature_c: float = 20.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.hematocrit < 1.0:
            raise ValueError("hematocrit must be in [0, 1)")
        if not 0.0 < self.max_packing <= 1.0:
            raise ValueError("max_packing must be in (0, 1]")
        if self.hematocrit >= self.max_packing:
            raise ValueError(
                f"hematocrit ({self.hematocrit}) must be below max_packing "
                f"({self.max_packing}); the sample is already packed"
            )
        if self.aggregate_diameter_um <= 0:
            raise ValueError("aggregate_diameter_um must be positive")
        if self.plasma_viscosity <= 0:
            raise ValueError("plasma_viscosity must be positive")
        if self.rbc_density <= self.plasma_density:
            raise ValueError("rbc_density must exceed plasma_density to settle")
        if self.hindrance_exponent < 0:
            raise ValueError("hindrance_exponent must be non-negative")
        if self.aggregation_time_s < 0:
            raise ValueError("aggregation_time_s must be non-negative")

    # ------------------------------------------------------------------
    @property
    def aggregate_diameter(self) -> float:
        """Aggregate diameter in metres."""
        return self.aggregate_diameter_um * UM

    @property
    def density_difference(self) -> float:
        """rho_rbc - rho_plasma [kg/m^3]."""
        return self.rbc_density - self.plasma_density

    def stokes_velocity(self) -> float:
        """Terminal velocity of one isolated aggregate in still plasma [m/s].

        ``u0 = k * (rho_s - rho_f) * g * d^2 / (18 * mu)``  -- creeping flow,
        with ``k`` the shape factor.  This is the *unhindered* velocity; the
        suspension settles far more slowly (see :mod:`bloodsed.flux`).
        """
        d = self.aggregate_diameter
        return (
            self.aggregate_shape_factor
            * self.density_difference
            * GRAVITY
            * d * d
            / (18.0 * self.plasma_viscosity)
        )

    def reynolds_number(self) -> float:
        """Particle Reynolds number of a settling aggregate.

        Should be << 1 for the Stokes law above to hold.
        """
        u = self.stokes_velocity()
        return self.plasma_density * u * self.aggregate_diameter / self.plasma_viscosity

    def aggregation_factor(self, t: float) -> float:
        """Fraction of the terminal velocity reached at time ``t`` [s].

        Models the lag phase: rouleaux need time to form, so the interface is
        nearly stationary for the first few minutes.
        """
        tau = self.aggregation_time_s
        if tau <= 0.0:
            return 1.0
        return 1.0 - math.exp(-t / tau)

    def describe(self) -> str:
        return (
            f"Hct={self.hematocrit:.0%}, aggregate d={self.aggregate_diameter_um:.0f} um, "
            f"mu={self.plasma_viscosity * 1e3:.2f} mPa.s, "
            f"u_stokes={mm_per_hour(self.stokes_velocity()):.0f} mm/h, "
            f"Re={self.reynolds_number():.1e}"
        )

    def to_dict(self) -> dict:
        return asdict(self)


#: Ready made samples spanning the clinical range.  ``aggregate_diameter_um``
#: is the stand-in for the fibrinogen / acute-phase-protein load.
PRESETS: dict[str, BloodProperties] = {
    "normal": BloodProperties(
        hematocrit=0.45, aggregate_diameter_um=60.0, aggregation_time_s=300.0
    ),
    "normal-female": BloodProperties(
        hematocrit=0.41, aggregate_diameter_um=68.0, aggregation_time_s=300.0
    ),
    "anemic": BloodProperties(
        hematocrit=0.28, aggregate_diameter_um=70.0, aggregation_time_s=300.0
    ),
    "polycythemic": BloodProperties(
        hematocrit=0.62, aggregate_diameter_um=55.0, aggregation_time_s=300.0
    ),
    "inflammation": BloodProperties(
        hematocrit=0.40, aggregate_diameter_um=110.0, aggregation_time_s=240.0
    ),
    "severe-inflammation": BloodProperties(
        hematocrit=0.35, aggregate_diameter_um=160.0, aggregation_time_s=180.0
    ),
    "newborn": BloodProperties(
        hematocrit=0.55, aggregate_diameter_um=35.0, aggregation_time_s=420.0
    ),
}


def get_blood(name: str) -> BloodProperties:
    """Look up a preset sample by name."""
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(
            f"unknown blood preset {name!r}; available: {', '.join(sorted(PRESETS))}"
        ) from None
