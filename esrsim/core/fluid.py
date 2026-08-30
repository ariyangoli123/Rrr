"""Fluid properties and the capillary constants derived from them.

Densities and surface tensions are read from ``fluids.yaml`` with their own tiers —
almost all ESTIMATED, because they come from the literature rather than from a
measurement on the actual sample. The *derived* quantities (capillary length, the
constant ``K``, Bond number) are exact functions of those inputs, so they inherit the
inputs' tier rather than claiming EXACT.

Internally SI throughout; lengths come back in mm at the boundary
(see :mod:`esrsim.units`).

References
----------
ESR_SIMULATOR_SPEC.md §3, v1.1 addendum §C.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..registry import fluid_library, mixing_validation_gap
from ..tiers import MIXING_VALIDATION_GAP, Result, ResultSet, Tier
from ..units import G, m_to_mm, mm_to_m

__all__ = [
    "Fluid",
    "load_fluid",
    "list_fluids",
    "capillary_length",
    "K_constant",
    "bond_number",
    "capillary_barrier",
    "hydrostatic_driver",
    "driver_to_barrier_ratio",
    "fluid_report",
    "mixing_validation_note",
]


@dataclass(frozen=True, slots=True)
class Fluid:
    """One fluid, with each property carrying its own tier."""

    key: str
    label: str
    rho: Result           # kg/m^3
    sigma: Result         # N/m
    mu_high_shear: Result  # Pa.s
    mu_low_shear: Result   # Pa.s
    tau_y: Result          # Pa
    delta_cos_theta: Result

    @property
    def properties(self) -> tuple[Result, ...]:
        return (
            self.rho, self.sigma, self.mu_high_shear,
            self.mu_low_shear, self.tau_y, self.delta_cos_theta,
        )

    @property
    def tier(self) -> Tier:
        return max(p.tier for p in self.properties)


def _prop(key: str, name: str, spec: dict[str, Any]) -> Result:
    tier = Tier[str(spec.get("tier", "ESTIMATED")).upper()]
    notes: list[str] = []
    flags: list[str] = []
    conflict = spec.get("conflict")
    if conflict:
        notes.append(
            f"CONTRADICTORY: {conflict['from']} gives {conflict['with']}, this value "
            f"is from the cone experiment (unknown {conflict.get('unknown_id', '?')})"
        )
        flags.append("CONTRADICTORY_VALUE")
    if spec.get("unknown_id"):
        notes.append(f"unknown {spec['unknown_id']}")
    return Result(
        name=f"{key}.{name}",
        value=float(spec["value"]),
        unit=str(spec.get("unit", "")),
        tier=tier,
        source=" ".join(str(spec.get("source", "")).split()),
        flags=tuple(flags),
        notes=tuple(notes),
    )


def list_fluids() -> tuple[str, ...]:
    return tuple(fluid_library()["fluids"])


def load_fluid(key: str) -> Fluid:
    """Load one fluid from ``fluids.yaml``."""
    fluids = fluid_library()["fluids"]
    if key not in fluids:
        raise KeyError(f"unknown fluid {key!r}; library has {list(fluids)}")
    spec = fluids[key]
    return Fluid(
        key=key,
        label=spec.get("label", key),
        rho=_prop(key, "rho", spec["rho"]),
        sigma=_prop(key, "sigma", spec["sigma"]),
        mu_high_shear=_prop(key, "mu_high_shear", spec["mu_high_shear"]),
        mu_low_shear=_prop(key, "mu_low_shear", spec["mu_low_shear"]),
        tau_y=_prop(key, "tau_y", spec["tau_y"]),
        delta_cos_theta=_prop(key, "delta_cos_theta", spec["delta_cos_theta"]),
    )


# ---------------------------------------------------------------- derived constants


def capillary_length(fluid: Fluid) -> Result:
    """``Lc = sqrt(sigma / (rho g))`` in mm — spec §3. Blood: 2.32 mm."""
    lc_m = math.sqrt(fluid.sigma.value / (fluid.rho.value * G))
    return fluid.sigma.derive(
        "capillary_length", m_to_mm(lc_m), "mm",
        others=(fluid.rho,),
        source="ESR_SIMULATOR_SPEC.md §3",
    )


def K_constant(fluid: Fluid) -> Result:
    """``K = 2 sigma / (rho g)`` in mm^2 — spec §3. Blood: 10.76 mm^2.

    This is the constant that both blood-line unevenness models are built on.
    """
    k_m2 = 2.0 * fluid.sigma.value / (fluid.rho.value * G)
    return fluid.sigma.derive(
        "K", k_m2 * 1e6, "mm^2",
        others=(fluid.rho,),
        source="ESR_SIMULATOR_SPEC.md §3",
    )


def bond_number(fluid: Fluid, gap_mm: float) -> Result:
    """``Bo = rho g d^2 / sigma`` — spec §3, dimensionless."""
    d = mm_to_m(gap_mm)
    bo = fluid.rho.value * G * d * d / fluid.sigma.value
    return fluid.sigma.derive(
        "bond_number", bo, "",
        others=(fluid.rho,),
        source="ESR_SIMULATOR_SPEC.md §3",
        notes=(f"gap {gap_mm:.3f} mm",),
    )


def capillary_barrier(fluid: Fluid, gap_mm: float) -> Result:
    """Capillary pressure barrier ``2 sigma / d`` in Pa — addendum §C."""
    pa = 2.0 * fluid.sigma.value / mm_to_m(gap_mm)
    return fluid.sigma.derive(
        "capillary_barrier", pa, "Pa",
        source="v1.1 addendum §C",
        notes=(f"gap {gap_mm:.3f} mm",),
    )


def hydrostatic_driver(fluid: Fluid, column_mm: float) -> Result:
    """Hydrostatic driving pressure ``rho g h`` in Pa — addendum §C."""
    pa = fluid.rho.value * G * mm_to_m(column_mm)
    return fluid.rho.derive(
        "hydrostatic_driver", pa, "Pa",
        source="v1.1 addendum §C",
        notes=(f"column {column_mm:.1f} mm",),
    )


def driver_to_barrier_ratio(
    fluid: Fluid, gap_mm: float, column_mm: float = 50.0
) -> Result:
    """Driver / barrier — addendum §C. Water 2.38, fresh blood 3.25, aged 3.76."""
    driver = hydrostatic_driver(fluid, column_mm)
    barrier = capillary_barrier(fluid, gap_mm)
    return driver.derive(
        "driver_over_barrier", driver.value / barrier.value, "",
        others=(barrier,),
        source="v1.1 addendum §C",
        notes=(f"gap {gap_mm:.3f} mm, column {column_mm:.1f} mm",),
    )


def fluid_report(fluid: Fluid, gap_mm: float = 0.70, column_mm: float = 50.0) -> ResultSet:
    """All properties and derived constants for one fluid."""
    return ResultSet(
        title=f"FLUID — {fluid.label}",
        results=fluid.properties
        + (
            capillary_length(fluid),
            K_constant(fluid),
            bond_number(fluid, gap_mm),
            capillary_barrier(fluid, gap_mm),
            hydrostatic_driver(fluid, column_mm),
            driver_to_barrier_ratio(fluid, gap_mm, column_mm),
        ),
        notes=(
            "surface tension and viscosity are literature values, not measurements on "
            "the sample in hand",
        ),
    )


def mixing_validation_note() -> Result:
    """The mixing evidence gap — addendum §C, printed in every mixing report.

    Addendum §C: *"the program must print this clause in every mixing report."*
    """
    gap = mixing_validation_gap()
    return Result.estimated(
        "mixing_validation_basis",
        f"validated with {', '.join(gap['validated_with'])}; NOT with "
        f"{', '.join(gap['not_validated_with'])}",
        "",
        source="v1.1 addendum §C",
        flags=(MIXING_VALIDATION_GAP,),
        notes=(
            "never probed: " + ", ".join(gap["what_was_never_probed"]),
            "argument offered: " + " ".join(str(gap["argument_offered"]).split()),
            "why that is not enough: "
            + " ".join(str(gap["why_that_is_not_enough"]).split()),
            "DECISIVE TEST: " + " ".join(str(gap["decisive_test"]).split()),
            "HARD RULE: " + " ".join(str(gap["hard_rule"]).split()),
        ),
    )
