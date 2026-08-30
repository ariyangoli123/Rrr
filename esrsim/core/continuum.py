"""Continuum research layer — RESEARCH_ONLY. Never in an engineering decision path.

Spec §6: *"for the paper. Do not use in the engineering decision path."*

This module exists to make one thing computable and one thing explicitly *not*
computable.

Computable: the sedimentation Rayleigh number ``Lambda`` and the Acrivos-Herbolzheimer
clear-layer thickness scaling. Spec §G.7 notes that ``Lambda`` has never been computed
for this geometry, so it is worth having even though it decides nothing.

Not computable: anything that needs the compressive yield stress ``Py(phi)`` or the
hydraulic resistance ``R(phi)`` of whole blood. **Neither function has been measured
anywhere in the world literature** (unknown U05). They are registered here as
:class:`UnknownMaterialFunction`, and calling one returns UNKNOWN — never a number,
never a plausible-looking placeholder. That is the difference between a research layer
and fiction.

References
----------
Kynch, Trans Faraday Soc 1952.
Acrivos & Herbolzheimer, J Fluid Mech 1979;92:435-457.
Herbolzheimer & Acrivos, J Fluid Mech 1981;108:485-499.
Darras et al., PRL 2022; Dasanna et al., PRE 2022; John et al., PNAS Nexus 2024.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..tiers import RESEARCH_ONLY_FLAG, Result, ResultSet, Tier
from ..units import G, check_fraction, check_positive, mm_to_m
from .fluid import Fluid, load_fluid
from .geometry import Cone

__all__ = [
    "UnknownMaterialFunction",
    "UNKNOWN_MATERIAL_FUNCTIONS",
    "Py",
    "R",
    "sedimentation_rayleigh",
    "clear_layer_thickness",
    "kynch_flux",
    "continuum_report",
]


@dataclass(frozen=True, slots=True)
class UnknownMaterialFunction:
    """A material function that has never been measured for whole blood.

    Calling it returns an UNKNOWN :class:`~esrsim.tiers.Result`. There is no mode in
    which it produces a number.
    """

    name: str
    symbol: str
    description: str
    unit: str
    references: tuple[str, ...]

    def __call__(self, phi: float) -> Result:
        return Result.unknown(
            f"{self.symbol}({phi:.3f})",
            why=(
                f"{self.description} has NOT been measured for whole blood anywhere in "
                "the world literature. Whole-blood sedimentation is gel collapse, and "
                f"{self.symbol} is one of the two material functions that would be "
                "needed to model it. Without it no first-principles prediction is "
                "possible (spec §0, §6; unknown U05)."
            ),
            experiment=(
                "Not closable at this bench. It would take a dedicated rheological "
                "programme on whole blood — compressional rheometry against volume "
                "fraction — of the kind that does not yet exist in the literature. "
                f"Nearest work: {', '.join(self.references)}."
            ),
            flags=(RESEARCH_ONLY_FLAG,),
        )


#: The two functions of unknown U05. Registered, never implemented.
UNKNOWN_MATERIAL_FUNCTIONS: dict[str, UnknownMaterialFunction] = {
    "Py": UnknownMaterialFunction(
        name="compressive_yield_stress",
        symbol="Py",
        description="the compressive yield stress of the sedimenting network",
        unit="Pa",
        references=("Darras et al., PRL 2022", "John et al., PNAS Nexus 2024"),
    ),
    "R": UnknownMaterialFunction(
        name="hydraulic_resistance",
        symbol="R",
        description="the hydraulic resistance of the sedimenting network",
        unit="Pa.s/m^2",
        references=("Dasanna et al., PRE 2022", "Darras et al., PRL 2022"),
    ),
}

Py = UNKNOWN_MATERIAL_FUNCTIONS["Py"]
R = UNKNOWN_MATERIAL_FUNCTIONS["R"]


def sedimentation_rayleigh(
    cone: Cone,
    fluid: Fluid | str = "blood_fresh",
    *,
    hematocrit: float = 0.45,
    rho_cells: float = 1125.0,
    u_stokes_mm_min: float = 0.22,
) -> Result:
    """``Lambda = H^2 g (rho_s - rho) c0 / (mu v0)`` — Acrivos & Herbolzheimer 1979.

    Spec §G.7 records that this number has never been computed for this geometry.
    RESEARCH_ONLY: it decides nothing here, and two of its inputs (``rho_cells`` and
    the Stokes velocity of a single erythrocyte) are literature values that were not
    measured for this sample.
    """
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    check_fraction("hematocrit", hematocrit)
    check_positive("u_stokes_mm_min", u_stokes_mm_min)

    h_m = mm_to_m(cone.length_mm)
    v0 = mm_to_m(u_stokes_mm_min) / 60.0            # m/s
    mu = f.mu_low_shear.value
    delta_rho = rho_cells - f.rho.value
    lam = h_m**2 * G * delta_rho * hematocrit / (mu * v0)

    return Result.research_only(
        "sedimentation_rayleigh_Lambda", lam, "",
        source="Acrivos & Herbolzheimer, J Fluid Mech 1979;92:435",
        notes=(
            "spec §G.7: never computed for this geometry before",
            f"rho_cells = {rho_cells:g} kg/m^3 and the single-cell Stokes velocity "
            f"{u_stokes_mm_min:g} mm/min are LITERATURE values, not measured here",
            "PNK is only the limiting case Lambda -> infinity with a stable interface "
            "(spec §6)",
            "RESEARCH ONLY — must not enter an engineering decision (spec §6)",
        ),
    )


def clear_layer_thickness(
    cone: Cone, lam: Result | None = None, **kwargs
) -> Result:
    """``delta* ~ L Lambda^(-1/3)`` — Acrivos & Herbolzheimer 1979."""
    lam = lam if lam is not None else sedimentation_rayleigh(cone, **kwargs)
    if lam.tier is Tier.UNKNOWN or lam.value is None:
        return lam.derive("clear_layer_thickness", None, "mm")
    value = cone.length_mm * float(lam.value) ** (-1.0 / 3.0)
    return lam.derive(
        "clear_layer_thickness", value, "mm",
        source="Acrivos & Herbolzheimer 1979",
        notes=("delta(x) ~ x^(1/3) along the wall",
               "RESEARCH ONLY — must not enter an engineering decision"),
    )


def kynch_flux(phi: float, u_s_mm_min: float, n: float | None = None) -> Result:
    """Kynch batch-settling flux ``f_bk(phi) = phi u_s (1 - phi)^n`` — Kynch 1952.

    The Richardson-Zaki exponent ``n`` is not fitted anywhere in this project. Calling
    without it returns UNKNOWN rather than borrowing a value from a different
    suspension.
    """
    check_fraction("phi", phi)
    if n is None:
        return Result.unknown(
            "kynch_flux",
            why=(
                "the Richardson-Zaki exponent n has never been fitted for this blood, "
                "this haematocrit or this geometry. Borrowing n from a monodisperse "
                "hard-sphere suspension would produce a confident-looking number with "
                "no support behind it."
            ),
            experiment=(
                "Fit n to a batch-settling series across at least four haematocrits in "
                "a vertical Westergren tube, then re-run this function with the fitted "
                "value."
            ),
            flags=(RESEARCH_ONLY_FLAG,),
        )
    return Result.research_only(
        "kynch_flux", phi * u_s_mm_min * (1.0 - phi) ** n, "mm/min",
        source="Kynch, Trans Faraday Soc 1952",
        notes=(f"Richardson-Zaki n = {n:g}, supplied by the caller and NOT fitted here",
               "RESEARCH ONLY — must not enter an engineering decision"),
    )


def continuum_report(cone: Cone, fluid: Fluid | str = "blood_fresh") -> ResultSet:
    """The research layer, clearly fenced off from the decision path."""
    lam = sedimentation_rayleigh(cone, fluid)
    results = [
        lam,
        clear_layer_thickness(cone, lam),
        kynch_flux(0.45, 0.22),
        Py(0.45),
        R(0.45),
    ]
    return ResultSet(
        title=f"CONTINUUM RESEARCH LAYER — {cone.tube_id or 'cone'}",
        results=tuple(results),
        notes=(
            "FOR THE PAPER ONLY. Spec §6: do not use in the engineering decision path.",
            "Py(phi) and R(phi) have never been measured for whole blood anywhere. "
            "They are registered as unknown material functions and return no number "
            "(unknown U05).",
            "PNK is only the limiting case Lambda -> infinity with a stable interface.",
        ),
    )
