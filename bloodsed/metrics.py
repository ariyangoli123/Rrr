"""Clinical and numerical read-outs from a finished simulation."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from .solver import SimulationResult
from .units import HOUR, MM, to_mm


def esr(result: SimulationResult, hours: float = 1.0) -> float:
    """Sedimentation reading [mm] at ``hours`` -- the ESR itself."""
    return result.esr(hours)


def katz_index(result: SimulationResult) -> float:
    """Katz index ``(ESR_1h + ESR_2h/2) / 2`` [mm].

    A single number that damps the hematocrit sensitivity of the plain
    one-hour reading.  Requires at least 2 h of simulated time.
    """
    return 0.5 * (result.esr(1.0) + 0.5 * result.esr(2.0))


def max_settling_rate(result: SimulationResult) -> float:
    """Steepest slope of the sedimentation curve [mm/h].

    In a classic ESR curve this is the plateau of the second phase, after
    rouleaux have formed and before the sediment starts to pack.
    """
    if result.times.size < 3:
        return float("nan")
    rate = np.gradient(result.fall_mm, result.times_h)
    return float(np.max(rate))


def lag_time_min(result: SimulationResult, fraction: float = 0.5) -> float:
    """Time [min] until the settling rate first reaches ``fraction`` of its peak.

    The rouleaux formation phase.
    """
    if result.times.size < 3:
        return float("nan")
    rate = np.gradient(result.fall_mm, result.times_h)
    peak = np.max(rate)
    if peak <= 0:
        return float("nan")
    hit = np.flatnonzero(rate >= fraction * peak)
    if hit.size == 0:
        return float("nan")
    return float(result.times[hit[0]] / 60.0)


def time_to_fall(result: SimulationResult, fall_mm: float) -> float:
    """Time [min] at which the boundary has dropped ``fall_mm``; NaN if never."""
    curve = result.fall_mm
    if curve[-1] < fall_mm:
        return float("nan")
    return float(np.interp(fall_mm, curve, result.times) / 60.0)


def sediment_height_mm(result: SimulationResult, index: int = -1) -> float:
    """Height [mm] of the packed-cell column at a sample index."""
    return float(result.sediment_mm[index])


def packed_cell_fraction(result: SimulationResult) -> float:
    """Mean red-cell volume fraction inside the sediment at the final time."""
    phi = result.phi[-1]
    z = result.z_centers
    top = result.sediment[-1]
    mask = (z <= top) & (phi > 0)
    if not np.any(mask):
        return float("nan")
    v = result.volumes[mask]
    return float(np.sum(v * phi[mask]) / np.sum(v))


def compaction_ratio(result: SimulationResult) -> float:
    """Initial blood column height / final sediment height [-].

    How many times the sample has concentrated.  A tube that narrows toward
    the bottom compacts more for the same cell load.
    """
    top = result.sediment[-1]
    if top <= 0:
        return float("nan")
    return float(result.fill_height / top)


def summarise(result: SimulationResult) -> dict:
    """All the read-outs in one dictionary."""
    duration_h = result.times[-1] / HOUR
    out = {
        "label": result.label,
        "geometry": result.geometry.name,
        "tilt_deg": result.geometry.tilt_deg,
        "tube_length_mm": to_mm(result.geometry.length),
        "tube_volume_ml": result.geometry.volume() * 1e6,
        "fill_height_mm": to_mm(result.fill_height),
        "hematocrit": result.blood.hematocrit,
        "aggregate_um": result.blood.aggregate_diameter_um,
        "stokes_mm_per_h": result.stokes_velocity / MM * HOUR,
        "esr_1h_mm": result.esr(1.0) if duration_h >= 1.0 else float("nan"),
        "esr_2h_mm": result.esr(2.0) if duration_h >= 2.0 else float("nan"),
        "katz_index_mm": katz_index(result) if duration_h >= 2.0 else float("nan"),
        "final_fall_mm": float(result.fall_mm[-1]),
        "max_rate_mm_per_h": max_settling_rate(result),
        "lag_time_min": lag_time_min(result),
        "sediment_mm": sediment_height_mm(result),
        "packed_cell_fraction": packed_cell_fraction(result),
        "compaction_ratio": compaction_ratio(result),
        "mean_enhancement": float(np.mean(result.enhancement)),
        "duration_h": duration_h,
        "n_cells": result.config.n_cells,
        "n_steps": result.n_steps,
        "mass_error": result.mass_error,
        "runtime_s": result.wall_clock_s,
    }
    return out


#: columns shown by :func:`format_table`, as (key, header, format)
TABLE_COLUMNS: list[tuple[str, str, str]] = [
    ("label", "case", "s"),
    ("tube_volume_ml", "vol mL", "6.2f"),
    ("fill_height_mm", "fill mm", "7.1f"),
    ("esr_1h_mm", "ESR 1h", "6.2f"),
    ("esr_2h_mm", "ESR 2h", "6.2f"),
    ("katz_index_mm", "Katz", "6.2f"),
    ("max_rate_mm_per_h", "peak mm/h", "9.2f"),
    ("lag_time_min", "lag min", "7.1f"),
    ("sediment_mm", "sed mm", "6.1f"),
    ("packed_cell_fraction", "packed", "6.3f"),
]


def format_table(results: Sequence[SimulationResult]) -> str:
    """Aligned plain-text comparison table."""
    rows = [summarise(r) for r in results]
    width = max([len(str(r["label"])) for r in rows] + [len("case")])
    header = f"{'case':<{width}}"
    for key, head, _ in TABLE_COLUMNS[1:]:
        header += f"  {head:>9}"
    lines = [header, "-" * len(header)]
    for row in rows:
        line = f"{str(row['label']):<{width}}"
        for key, _, fmt in TABLE_COLUMNS[1:]:
            value = row.get(key, float("nan"))
            line += "  " + (f"{value:>9.2f}" if not _is_nan(value) else f"{'-':>9}")
        lines.append(line)
    return "\n".join(lines)


def _is_nan(value) -> bool:
    try:
        return bool(np.isnan(value))
    except TypeError:
        return False


def write_timeseries_csv(result: SimulationResult, path: str | Path) -> Path:
    """Write the settling curve of one run as CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_min", "fall_mm", "interface_mm", "sediment_mm",
                         "boycott_factor", "aggregation_factor"])
        for i, t in enumerate(result.times):
            writer.writerow([
                f"{t / 60.0:.4f}",
                f"{result.fall_mm[i]:.5f}",
                f"{result.interface_mm[i]:.5f}",
                f"{result.sediment_mm[i]:.5f}",
                f"{result.enhancement[i]:.5f}",
                f"{result.aggregation[i]:.5f}",
            ])
    return path


def write_profile_csv(result: SimulationResult, path: str | Path,
                      times_min: Iterable[float] | None = None) -> Path:
    """Write concentration profiles (one column per requested time) as CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if times_min is None:
        times_min = [0.0, 15.0, 30.0, 60.0, 120.0]
    picks = []
    for tm in times_min:
        t = tm * 60.0
        if t <= result.times[-1] + 1e-9:
            picks.append((tm, int(np.argmin(np.abs(result.times - t)))))
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["height_mm", "diameter_mm"] + [f"phi_t{tm:g}min" for tm, _ in picks])
        diam = result.geometry.diameter(result.z_centers) / MM
        for i, z in enumerate(result.z_mm):
            writer.writerow([f"{z:.4f}", f"{diam[i]:.4f}"]
                            + [f"{result.phi[k][i]:.6f}" for _, k in picks])
    return path


def write_summary_csv(results: Sequence[SimulationResult], path: str | Path) -> Path:
    """Write one summary row per run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [summarise(r) for r in results]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path
