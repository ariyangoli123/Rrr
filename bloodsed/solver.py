"""The sedimentation solver.

Model
-----
Let ``phi(z, t)`` be the red-cell volume fraction on the tube axis, ``z``
measured up from the closed bottom, and ``A(z)`` the cross-sectional area.
Cells settle, plasma flows up to replace them, and nothing leaves the tube, so
the cell volume obeys a conservation law with a variable coefficient:

    d/dt [ A(z) phi ]  =  d/dz [ A(z) f(phi) ]          f = downward flux

Discretised as finite volumes, cell ``i`` spanning ``[z_i-1/2, z_i+1/2]`` with
volume ``V_i = integral of A dz``:

    V_i dphi_i/dt = Q_{i+1/2} - Q_{i-1/2}
    Q_{k}         = A_k * u_k * godunov(phi_above, phi_below)     [m^3/s]

with ``Q = 0`` at the closed bottom and at the free surface.  The scheme is
monotone, conserves cell volume to machine precision, and keeps
``0 <= phi <= phi_max``.

Because the flux carries ``A(z)``, geometry enters the physics rather than
being a cosmetic detail: where the tube narrows downward, more cells arrive
than can leave, and the suspension concentrates until it is locally packed;
where it widens downward, the suspension is diluted and settles faster.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .blood import BloodProperties
from .flux import FluxLaw, make_flux_law, wall_factor
from .geometry import TubeGeometry
from .inclination import BoycottModel
from .units import HOUR, MM, to_mm


@dataclass
class SimulationConfig:
    """Everything the solver needs besides the blood and the tube.

    Attributes
    ----------
    duration_h:
        Simulated time, in hours.  Clinical ESR is read at 1 h (Westergren).
    n_cells:
        Axial finite-volume cells.  600 resolves a 200 mm tube to 0.33 mm.
    sample_interval_s:
        How often the full profile is stored.
    cfl:
        Courant number; must stay below 1 for the explicit scheme.
    fill_fraction / fill_height_mm:
        How much of the tube is filled with blood.  Give one or the other;
        ``fill_height_mm`` wins.  The fill level is snapped to the nearest
        cell face so the initial ESR reading is exactly zero.
    flux_law, hindrance_exponent_override:
        See :mod:`bloodsed.flux`.  The exponent defaults to the blood's.
    wall_correction:
        Apply the Faxen tube-wall retardation, using the *local* diameter.
    aggregation_lag:
        Ramp the settling velocity up over ``blood.aggregation_time_s`` to
        represent rouleaux formation.
    boycott:
        Tilted-tube model; see :mod:`bloodsed.inclination`.
    interface_fraction:
        The plasma/cell boundary is read where ``phi`` crosses this fraction of
        the initial hematocrit.  0.5 is the mid-point of the shock.
    sediment_fraction:
        The sediment boundary is read where ``phi`` crosses this fraction of
        the way from the initial hematocrit up to ``phi_max``.
    """

    duration_h: float = 2.0
    n_cells: int = 600
    sample_interval_s: float = 60.0
    cfl: float = 0.4
    fill_fraction: float = 1.0
    fill_height_mm: float | None = None
    flux_law: str = "hindered-packing"
    hindrance_exponent_override: float | None = None
    wall_correction: bool = True
    aggregation_lag: bool = True
    boycott: BoycottModel = field(default_factory=BoycottModel)
    interface_fraction: float = 0.5
    sediment_fraction: float = 0.5
    max_steps: int = 20_000_000

    def __post_init__(self) -> None:
        if self.duration_h <= 0:
            raise ValueError("duration_h must be positive")
        if self.n_cells < 10:
            raise ValueError("n_cells must be at least 10")
        if not 0.0 < self.cfl < 1.0:
            raise ValueError("cfl must be in (0, 1)")
        if not 0.0 < self.fill_fraction <= 1.0:
            raise ValueError("fill_fraction must be in (0, 1]")
        if self.sample_interval_s <= 0:
            raise ValueError("sample_interval_s must be positive")
        if not 0.0 < self.interface_fraction < 1.0:
            raise ValueError("interface_fraction must be in (0, 1)")
        if not 0.0 < self.sediment_fraction <= 1.0:
            raise ValueError("sediment_fraction must be in (0, 1]")

    @property
    def duration_s(self) -> float:
        return self.duration_h * HOUR


@dataclass
class SimulationResult:
    """Output of :func:`simulate`.

    ``phi`` is ``(n_samples, n_cells)``; ``interface`` and ``sediment`` are
    heights in metres above the tube bottom.  Use the ``*_mm`` helpers and
    :mod:`bloodsed.metrics` for clinical numbers.
    """

    label: str
    geometry: TubeGeometry
    blood: BloodProperties
    config: SimulationConfig
    times: np.ndarray            # [s]
    z_faces: np.ndarray          # [m]
    z_centers: np.ndarray        # [m]
    areas: np.ndarray            # [m^2] at faces
    volumes: np.ndarray          # [m^3] per cell
    phi: np.ndarray              # (n_samples, n_cells)
    interface: np.ndarray        # [m]
    sediment: np.ndarray         # [m]
    enhancement: np.ndarray      # Boycott factor per sample [-]
    aggregation: np.ndarray      # rouleaux ramp per sample [-]
    fill_height: float           # [m]
    stokes_velocity: float       # [m/s]
    mass_error: float            # relative drift in total cell volume
    n_steps: int
    wall_clock_s: float

    # -- convenience ---------------------------------------------------
    @property
    def times_min(self) -> np.ndarray:
        return self.times / 60.0

    @property
    def times_h(self) -> np.ndarray:
        return self.times / HOUR

    @property
    def fall_mm(self) -> np.ndarray:
        """How far the plasma/cell boundary has dropped [mm] -- the ESR curve."""
        return (self.fill_height - self.interface) / MM

    @property
    def interface_mm(self) -> np.ndarray:
        return self.interface / MM

    @property
    def sediment_mm(self) -> np.ndarray:
        return self.sediment / MM

    @property
    def z_mm(self) -> np.ndarray:
        return self.z_centers / MM

    def fall_at(self, t_seconds: float) -> float:
        """Interpolated fall [mm] at a given time; NaN past the simulated end."""
        if t_seconds > self.times[-1] + 1e-9:
            return float("nan")
        return float(np.interp(t_seconds, self.times, self.fall_mm))

    def esr(self, hours: float = 1.0) -> float:
        """Erythrocyte sedimentation rate reading [mm] after ``hours``."""
        return self.fall_at(hours * HOUR)

    def profile_at(self, t_seconds: float) -> np.ndarray:
        """Nearest stored ``phi`` profile."""
        idx = int(np.argmin(np.abs(self.times - t_seconds)))
        return self.phi[idx]

    def cell_volume_total(self) -> float:
        """Total red-cell volume in the tube [m^3] (constant by construction)."""
        return float(np.sum(self.volumes * self.phi[-1]))

    def summary(self) -> dict:
        from .metrics import summarise
        return summarise(self)


def simulate(geometry: TubeGeometry,
             blood: BloodProperties | None = None,
             config: SimulationConfig | None = None,
             *,
             label: str | None = None,
             progress: Callable[[float], None] | None = None) -> SimulationResult:
    """Run a batch sedimentation to completion.

    Parameters
    ----------
    geometry:
        Any :class:`~bloodsed.geometry.TubeGeometry`.
    blood:
        Sample properties; defaults to a normal adult sample.
    config:
        Numerical and protocol settings.
    label:
        Name for plots and tables; defaults to the geometry name.
    progress:
        Optional callback receiving the completed fraction, 0..1.
    """
    blood = blood or BloodProperties()
    config = config or SimulationConfig()
    started = time.perf_counter()

    n = config.n_cells
    z_faces = np.linspace(0.0, geometry.length, n + 1)
    z_centers = 0.5 * (z_faces[:-1] + z_faces[1:])
    areas = geometry.area(z_faces)
    volumes = geometry.cell_volumes(z_faces)
    if np.any(volumes <= 0.0):
        raise ValueError("geometry produced a non-positive cell volume")

    law = make_flux_law(
        config.flux_law,
        config.hindrance_exponent_override
        if config.hindrance_exponent_override is not None
        else blood.hindrance_exponent,
        blood.max_packing,
    )

    # --- initial condition: well mixed blood up to the fill level ------
    if config.fill_height_mm is not None:
        fill_target = config.fill_height_mm * MM
    else:
        fill_target = config.fill_fraction * geometry.length
    fill_target = float(np.clip(fill_target, z_faces[1], geometry.length))
    fill_index = int(np.argmin(np.abs(z_faces - fill_target)))
    fill_index = max(fill_index, 1)
    fill_height = float(z_faces[fill_index])

    phi = np.zeros(n, dtype=float)
    phi[:fill_index] = blood.hematocrit
    mass0 = float(np.sum(volumes * phi))

    # --- per-face settling velocity ------------------------------------
    u0 = blood.stokes_velocity()
    if config.wall_correction:
        u_face = u0 * wall_factor(blood.aggregate_diameter, geometry.diameter(z_faces))
    else:
        u_face = np.full(n + 1, u0)
    u_face_int = u_face[1:-1]                     # interior faces only
    area_int = areas[1:-1]
    flow_int = area_int * u_face_int              # [m^3/s] at full speed

    # CFL geometric factor: dt = cfl * g_min / (u0 * ramp * lambda)
    q_face = areas * (u_face / u0)                # [m^2], speed factored out
    denom = np.maximum(q_face[:-1], q_face[1:]) * law.max_wave_speed()
    g_min = float(np.min(volumes / np.maximum(denom, 1e-300)))

    # --- sampling grid --------------------------------------------------
    n_samples = int(math.floor(config.duration_s / config.sample_interval_s)) + 1
    sample_times = np.arange(n_samples) * config.sample_interval_s
    if sample_times[-1] < config.duration_s - 1e-9:
        sample_times = np.append(sample_times, config.duration_s)
        n_samples += 1

    phi_hist = np.zeros((n_samples, n), dtype=float)
    iface_hist = np.zeros(n_samples)
    sed_hist = np.zeros(n_samples)
    lam_hist = np.ones(n_samples)
    agg_hist = np.ones(n_samples)

    iface_threshold = config.interface_fraction * blood.hematocrit
    sed_threshold = blood.hematocrit + config.sediment_fraction * (
        blood.max_packing - blood.hematocrit)

    cum_volume = np.concatenate([[0.0], np.cumsum(volumes)])

    def read_interface() -> float:
        return _interface_height(z_faces, cum_volume, volumes, phi,
                                 iface_threshold, fill_height)

    def record(k: int, lam: float, agg: float) -> None:
        phi_hist[k] = phi
        iface_hist[k] = read_interface()
        sed_hist[k] = _crossing_height(z_centers, phi, sed_threshold, fill_height, default=0.0)
        lam_hist[k] = lam
        agg_hist[k] = agg

    record(0, 1.0, blood.aggregation_factor(0.0) if config.aggregation_lag else 1.0)

    if geometry.tilt_deg == 0.0 or config.boycott.model in ("none", "constant"):
        fixed_lambda: float | None = config.boycott.factor(geometry.tilt_deg, 0.0, 1.0)
    else:
        fixed_lambda = None

    t = 0.0
    steps = 0
    next_sample = 1
    max_dt = config.sample_interval_s

    while next_sample < n_samples:
        target = sample_times[next_sample]

        # Boycott enhancement, driven by the length of the still-suspended
        # column.  An upright tube needs none of this.
        if fixed_lambda is not None:
            lam = fixed_lambda
        else:
            top = read_interface()
            bottom = _crossing_height(z_centers, phi, sed_threshold, fill_height, default=0.0)
            gap = geometry.mean_diameter(bottom, max(top, bottom + 1e-6))
            lam = config.boycott.factor(geometry.tilt_deg, max(top - bottom, 0.0), gap)

        dt = min(max_dt, target - t)
        agg_end = blood.aggregation_factor(t + dt) if config.aggregation_lag else 1.0
        speed = u0 * lam * max(agg_end, 1e-9)
        dt_cfl = config.cfl * g_min / speed
        dt = min(dt, dt_cfl)
        if dt <= 0.0:
            break
        agg = blood.aggregation_factor(t + 0.5 * dt) if config.aggregation_lag else 1.0

        scale = lam * agg
        # downward volumetric flux across every interior face
        q = flow_int * scale * law.godunov(phi[1:], phi[:-1])
        # A face can never move more cells than the sender holds, nor more than
        # the receiver has room for.  With the smooth flux laws these limits are
        # slack; with a flux that jumps at the packing limit they are what keeps
        # phi inside [0, phi_max] without discarding cells.
        np.minimum(q, phi[1:] * volumes[1:] / dt, out=q)
        np.minimum(q, (blood.max_packing - phi[:-1]) * volumes[:-1] / dt, out=q)
        np.maximum(q, 0.0, out=q)
        phi[:-1] += (dt / volumes[:-1]) * q
        phi[1:] -= (dt / volumes[1:]) * q

        t += dt
        steps += 1
        if steps > config.max_steps:
            raise RuntimeError(
                f"exceeded max_steps ({config.max_steps}); the geometry may have "
                f"an extremely small cell volume relative to its area"
            )

        if t >= target - 1e-12:
            record(next_sample, lam, agg)
            next_sample += 1
            if progress is not None:
                progress(t / config.duration_s)

    mass1 = float(np.sum(volumes * phi))
    mass_error = abs(mass1 - mass0) / mass0 if mass0 > 0 else 0.0

    return SimulationResult(
        label=label or geometry.name,
        geometry=geometry,
        blood=blood,
        config=config,
        times=sample_times,
        z_faces=z_faces,
        z_centers=z_centers,
        areas=areas,
        volumes=volumes,
        phi=phi_hist,
        interface=iface_hist,
        sediment=sed_hist,
        enhancement=lam_hist,
        aggregation=agg_hist,
        fill_height=fill_height,
        stokes_velocity=u0,
        mass_error=mass_error,
        n_steps=steps,
        wall_clock_s=time.perf_counter() - started,
    )


def _interface_height(z_faces: np.ndarray, cum_volume: np.ndarray, volumes: np.ndarray,
                      phi: np.ndarray, threshold: float, ceiling: float,
                      rise_tol: float = 0.02, max_walk: int = 200) -> float:
    """Height of the plasma/cell boundary [m], resolved below one cell.

    Reading a threshold crossing makes the curve climb a visible staircase as
    the front crosses each cell.  Instead we place a sharp boundary carrying the
    same cell volume as the real profile, referenced to the undisturbed
    suspension below the front:

        cells above the anchor  =  phi_ref * (volume between the anchor and h)

    The anchor is walked down from the crossing until the profile stops
    climbing, so it clears the front however wide it is -- one cell for the
    shock the default flux law produces, many for a smeared contact.  Starting
    the sum at a local anchor rather than at the tube bottom keeps a
    concentrated or diluted column further down from biasing the reading.
    """
    above = np.flatnonzero(phi >= threshold)
    if above.size == 0:
        return 0.0
    k = int(above[-1])

    lo = max(k - max_walk, 0)
    anchor = lo
    if k > lo:
        segment = np.maximum(phi[lo:k + 1], 1e-30)
        # ratio of each cell to the one above it; the front is where it exceeds 1
        settled = np.flatnonzero(segment[:-1] / segment[1:] <= 1.0 + rise_tol)
        if settled.size:
            anchor = lo + int(settled[-1]) + 1

    # sample the reference just *below* the anchor -- above it lies the front
    reference = float(np.median(phi[max(anchor - 2, 0):anchor + 1]))
    if reference <= 1e-12:
        return float(min(z_faces[k + 1], ceiling))
    cells_above = float(np.sum(volumes[anchor:] * phi[anchor:]))
    target = cum_volume[anchor] + cells_above / reference
    height = float(np.interp(target, cum_volume, z_faces))
    return float(min(height, ceiling))


def _crossing_height(z_centers: np.ndarray, phi: np.ndarray, threshold: float,
                     ceiling: float, default: float = 0.0) -> float:
    """Highest ``z`` where ``phi`` crosses ``threshold`` from above [m].

    Linear interpolation between cell centres, so the reading does not jump by
    a whole cell as the front advances.
    """
    above = np.flatnonzero(phi >= threshold)
    if above.size == 0:
        return default
    i = int(above[-1])
    if i >= phi.size - 1:
        return float(min(z_centers[i], ceiling))
    lo, hi = phi[i], phi[i + 1]
    if lo <= hi:
        return float(min(z_centers[i], ceiling))
    frac = (lo - threshold) / (lo - hi)
    z = z_centers[i] + frac * (z_centers[i + 1] - z_centers[i])
    return float(min(z, ceiling))
