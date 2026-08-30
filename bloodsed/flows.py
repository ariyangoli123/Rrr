"""The flow field inside the tube.

Nothing here is drawn from imagination: batch settling in a closed vessel fixes
the flow completely once the concentration field is known.  No blood enters or
leaves, and both phases are incompressible, so the *net* volumetric flux through
every cross-section is zero:

    phi * v_cells  =  (1 - phi) * v_plasma

The solver already computes the cell flux ``f = u_local * psi(phi)``, the volume
of cells crossing unit area per unit time.  Dividing it by each phase's own
volume fraction gives the velocity of that phase:

    v_cells  = f / phi          downward, the speed a rouleau actually falls
    v_plasma = f / (1 - phi)    upward, the return flow that hinders it

That upward plasma is the physical reason a crowded suspension settles so
slowly, and it is why a narrow gap or a constriction throttles the whole column:
the plasma has to get back up through it.
"""

from __future__ import annotations

import numpy as np

from .solver import SimulationResult
from .units import HOUR, MM


def local_velocity_scale(result: SimulationResult, index: int = -1) -> np.ndarray:
    """The settling velocity scale at each cell centre [m/s].

    The Stokes velocity, retarded by the tube wall, sped up by the Boycott
    enhancement, and ramped by however much rouleaux formation has progressed.
    """
    return (result.stokes_velocity
            * result.wall_factors
            * result.enhancement[index]
            * result.aggregation[index])


def cell_flux(result: SimulationResult, index: int = -1) -> np.ndarray:
    """Downward volume of cells crossing unit area per unit time [m/s]."""
    phi = result.phi[index]
    return local_velocity_scale(result, index) * result.law.shape(phi)


def velocity_field(result: SimulationResult, index: int = -1) -> tuple[np.ndarray, np.ndarray]:
    """``(cells down, plasma up)`` velocities at every cell centre [m/s].

    Both are zero in the clear plasma above the boundary (no cells to drag
    anything) and in the packed sediment (nothing can move).
    """
    phi = result.phi[index]
    flux = cell_flux(result, index)
    cells = np.divide(flux, phi, out=np.zeros_like(flux), where=phi > 1e-9)
    plasma = np.divide(flux, 1.0 - phi, out=np.zeros_like(flux), where=phi < 1.0 - 1e-9)
    return cells, plasma


def velocity_field_mm_per_hour(result: SimulationResult,
                               index: int = -1) -> tuple[np.ndarray, np.ndarray]:
    """The same field in the unit the readings are taken in."""
    cells, plasma = velocity_field(result, index)
    return cells / MM * HOUR, plasma / MM * HOUR


def plasma_throughput(result: SimulationResult, index: int = -1) -> np.ndarray:
    """Plasma pushed upward through each cross-section [mL/h].

    Where the tube narrows this is squeezed through a smaller opening, which is
    what makes a throat throttle the column above it.
    """
    areas = result.geometry.area(result.z_centers)
    return cell_flux(result, index) * areas * 1e6 * HOUR


def peak_velocities(result: SimulationResult, index: int = -1) -> dict:
    """Headline numbers for the flow field at one sample."""
    cells, plasma = velocity_field_mm_per_hour(result, index)
    throughput = plasma_throughput(result, index)
    return {
        "time_min": float(result.times[index] / 60.0),
        "cells_max_mm_per_h": float(np.max(cells)),
        "plasma_max_mm_per_h": float(np.max(plasma)),
        "plasma_throughput_ml_per_h": float(np.max(throughput)),
        "enhancement": float(result.enhancement[index]),
        "aggregation": float(result.aggregation[index]),
    }


def tracer_positions(result: SimulationResult, index: int, n_cells: int = 60,
                     n_plasma: int = 40, seed: int = 0) -> dict:
    """Sample points for drawing the flow, with the local velocity at each.

    Cell tracers are drawn where there are cells to draw (their density follows
    the local volume fraction); plasma tracers are drawn where there is room.
    Returned heights are in metres and velocities in m/s, positive downward for
    cells and upward for plasma.
    """
    rng = np.random.default_rng(seed)
    phi = result.phi[index]
    z = result.z_centers
    cells, plasma = velocity_field(result, index)

    weights_cells = phi * result.volumes
    weights_plasma = (1.0 - phi) * result.volumes * (plasma > 0)
    out = {}
    for key, weights, count, speed in (
        ("cells", weights_cells, n_cells, cells),
        ("plasma", weights_plasma, n_plasma, plasma),
    ):
        total = float(weights.sum())
        if total <= 0:
            out[key] = (np.zeros(0), np.zeros(0))
            continue
        picks = rng.choice(len(z), size=count, p=weights / total)
        jitter = (rng.random(count) - 0.5) * (z[1] - z[0])
        out[key] = (np.clip(z[picks] + jitter, 0.0, result.fill_height), speed[picks])
    return out
