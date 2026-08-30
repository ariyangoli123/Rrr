"""bloodsed -- blood sedimentation (ESR) in tubes of arbitrary geometry.

Quick start::

    from bloodsed import BloodProperties, get_geometry, simulate

    result = simulate(get_geometry("westergren"), BloodProperties(hematocrit=0.45))
    print(result.esr(1.0), "mm in the first hour")
"""

from __future__ import annotations

__version__ = "0.1.0"

from .blood import PRESETS as BLOOD_PRESETS, BloodProperties, get_blood
from .config import Scenario, blood_from_dict, geometry_from_dict, simconfig_from_dict
from .flux import FLUX_LAWS, FluxLaw, make_flux_law
from .flows import (
    cell_flux,
    peak_velocities,
    plasma_throughput,
    velocity_field,
    velocity_field_mm_per_hour,
)
from .geometry import (
    GEOMETRY_SETS,
    PRESETS as GEOMETRY_PRESETS,
    AnnularCone,
    Bulb,
    Cone,
    Cylinder,
    FunctionTube,
    Hourglass,
    Profile,
    Stepped,
    Taper,
    TubeGeometry,
    from_spec,
    get_geometry,
)
from .inclination import BoycottModel
from .metrics import format_table, summarise
from .solver import SimulationConfig, SimulationResult, simulate

__all__ = [
    "__version__",
    "BloodProperties",
    "BLOOD_PRESETS",
    "get_blood",
    "TubeGeometry",
    "AnnularCone",
    "Cylinder",
    "Taper",
    "Cone",
    "Hourglass",
    "Bulb",
    "Stepped",
    "Profile",
    "FunctionTube",
    "GEOMETRY_PRESETS",
    "GEOMETRY_SETS",
    "get_geometry",
    "from_spec",
    "FluxLaw",
    "FLUX_LAWS",
    "make_flux_law",
    "BoycottModel",
    "velocity_field",
    "velocity_field_mm_per_hour",
    "cell_flux",
    "plasma_throughput",
    "peak_velocities",
    "SimulationConfig",
    "SimulationResult",
    "simulate",
    "summarise",
    "format_table",
    "Scenario",
    "blood_from_dict",
    "geometry_from_dict",
    "simconfig_from_dict",
]
