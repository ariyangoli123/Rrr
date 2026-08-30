"""Figures.

One visual system across every figure:

* **Sequential** red ramp, single hue, light -> dark, for the red-cell volume
  fraction (a magnitude).  The palest step is clear plasma.
* **Categorical** palette in fixed slot order for comparing cases; a case keeps
  its colour in every panel of a figure, so the curve, the bar and the tube
  thumbnail always agree.
* Recessive grid and axes, 2 px data lines, text in ink rather than in the
  series colour, and a legend whenever more than one case is plotted.

Everything is committed to a light surface, and the palette carries direct
labels and a printed summary table as its contrast relief.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize

from .geometry import TubeGeometry
from .metrics import summarise
from .solver import SimulationResult
from .units import MM

# -- theme -------------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8985"
GRID = "#e6e5e1"

#: categorical slots, fixed order, never cycled
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

#: single-hue sequential ramp for the cell volume fraction
BLOOD_STEPS = ["#fdeceb", "#f9c9c5", "#f1a29c", "#e5726c",
               "#d34b45", "#b3302c", "#8a1f1e", "#5c1213"]
BLOOD_CMAP = LinearSegmentedColormap.from_list("blood", BLOOD_STEPS)


def apply_style() -> None:
    """Set the shared matplotlib defaults."""
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_2,
        "axes.titlecolor": INK,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.labelsize": 9.5,
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "legend.labelcolor": INK_2,
        "lines.linewidth": 2.0,
        "font.size": 10,
        "figure.dpi": 130,
    })


def color_for(index: int) -> str:
    """Categorical colour for case ``index`` (folds to grey past eight)."""
    return SERIES[index] if index < len(SERIES) else "#77766f"


def _tidy(ax) -> None:
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


# -- tube drawing ------------------------------------------------------
def _rotate(x, y, x0, y0, tilt_deg):
    """Rotate drawing coordinates about the foot of the tube.

    Only meaningful when the two axes share a unit and the axes aspect is equal
    -- otherwise a rotation shears.  Use ``exaggeration`` mode for that.
    """
    if not tilt_deg:
        return np.asarray(x), np.asarray(y)
    angle = math.radians(tilt_deg)
    cos, sin = math.cos(angle), math.sin(angle)
    dx, dy = np.asarray(x) - x0, np.asarray(y) - y0
    return x0 + dx * cos - dy * sin, y0 + dx * sin + dy * cos


def tube_exaggeration(geometry: TubeGeometry, fraction: float = 0.30) -> float:
    """How much to widen a tube so its shape is visible beside its height.

    A 2.5 mm bore next to a 200 mm column is a hairline.  This returns the
    factor that makes the widest part span ``fraction`` of the tube's height,
    so both axes can stay in millimetres and a tilt can be a true rotation.
    """
    z = np.linspace(0.0, geometry.length, 200)
    widest = 2.0 * float(geometry.radius(z).max())
    return fraction * geometry.length / max(widest, 1e-9)


def draw_tube(ax, geometry: TubeGeometry, phi: np.ndarray | None = None,
              z_faces: np.ndarray | None = None, *, x_center: float = 0.0,
              half_width: float = 0.45, vmax: float = 0.9,
              outline: str = MUTED, tilt: bool = False,
              exaggeration: float | None = None,
              min_band: float = 0.03) -> None:
    """Draw one tube, optionally filled with a concentration profile.

    Two coordinate modes:

    * default -- the widest part of the tube spans ``2 * half_width`` in
      whatever units the axes already use.  Good for putting several tubes side
      by side; cannot be tilted, because the axes do not share a unit.
    * ``exaggeration`` -- both axes are millimetres, with the radius multiplied
      by that factor.  Set ``ax.set_aspect("equal")`` and a tilt becomes a true
      rotation, which is the only way the Boycott effect looks like itself.

    A tube with an inner core (an annular settler) is drawn as two walls with
    the blood in the gap between them.  A real annular gap is a millimetre
    inside a vessel tens of millimetres across, so the band is widened inward to
    at least ``min_band`` of the tube's height -- the outer wall stays true.
    """
    if z_faces is None:
        n = 400 if phi is None else len(phi) + 1
        z_faces = np.linspace(0.0, geometry.length, n)
    outer = geometry.radius(z_faces)
    inner = geometry.inner_radius(z_faces)
    z_mm = z_faces / MM

    if exaggeration is not None:
        outer_scaled = exaggeration * outer / MM
        inner_scaled = exaggeration * inner / MM
    else:
        scale = half_width / outer.max()
        outer_scaled = scale * outer
        inner_scaled = scale * inner

    annular = bool(np.any(inner_scaled > 0))
    if annular and min_band > 0:
        span = z_mm[-1] - z_mm[0] if exaggeration is not None else 2.0 * half_width
        floor_band = min_band * span
        thin = (outer_scaled - inner_scaled) < floor_band
        inner_scaled = np.where(thin, np.maximum(outer_scaled - floor_band, 0.0),
                                inner_scaled)

    angle = geometry.tilt_deg if tilt else 0.0
    if angle and exaggeration is None:
        raise ValueError("tilting needs exaggeration mode so both axes share a unit")
    foot = (x_center, z_mm[0])

    if phi is not None:
        values = np.asarray(phi)[:, None]
        bands = ([(inner_scaled, outer_scaled), (-outer_scaled, -inner_scaled)]
                 if annular else [(-outer_scaled, outer_scaled)])
        for lo, hi in bands:
            x = np.column_stack([x_center + lo, x_center + hi])
            y = np.column_stack([z_mm, z_mm])
            xr, yr = _rotate(x, y, *foot, angle)
            ax.pcolormesh(xr, yr, values, cmap=BLOOD_CMAP, norm=Normalize(0.0, vmax),
                          shading="flat", rasterized=True)

    walls = [-outer_scaled, outer_scaled]
    if annular:
        walls += [-inner_scaled, inner_scaled]
    for offsets in walls:
        xr, yr = _rotate(x_center + offsets, z_mm, *foot, angle)
        ax.plot(xr, yr, color=outline, lw=1.1, solid_joinstyle="round")
    xr, yr = _rotate(np.array([x_center - outer_scaled[0], x_center + outer_scaled[0]]),
                     np.array([z_mm[0], z_mm[0]]), *foot, angle)
    ax.plot(xr, yr, color=outline, lw=1.1)


# -- individual panels -------------------------------------------------
def settling_curves(results: Sequence[SimulationResult], ax=None, *,
                    title: str = "Sedimentation curve",
                    mark_readings: bool = True):
    """Fall of the plasma/cell boundary against time -- the ESR curve."""
    apply_style()
    if ax is None:
        _, ax = plt.subplots(figsize=(7.2, 4.4))
    _tidy(ax)

    n = len(results)
    ends = [res.fall_mm[-1] for res in results]
    span = max(ends) - min(ends)
    gaps = np.diff(np.sort(ends))
    # direct labels only while they fit: few series, and end points far enough
    # apart that the text will not collide
    label_ends = 2 <= n <= 4 and span > 0 and (gaps.min() > 0.08 * span)

    for i, res in enumerate(results):
        color = color_for(i)
        ax.plot(res.times_min, res.fall_mm, color=color, label=res.label,
                zorder=3, solid_capstyle="round")
        if label_ends:
            ax.annotate(res.label, (res.times_min[-1], res.fall_mm[-1]),
                        xytext=(6, 0), textcoords="offset points",
                        color=INK_2, fontsize=9, va="center", zorder=4)

    if mark_readings:
        blend = ax.get_xaxis_transform()
        for hours in (1.0, 2.0):
            t = hours * 60.0
            if t <= results[0].times_min[-1] + 1e-9:
                ax.axvline(t, color=GRID, lw=1.0, zorder=1)
                ax.annotate(f"{hours:g} h", (t, 1.0), xycoords=blend, xytext=(3, -3),
                            textcoords="offset points", color=MUTED, fontsize=8.5,
                            va="top", ha="left", zorder=2)

    ax.set_xlabel("time (min)")
    ax.set_ylabel("fall of the boundary (mm)")
    ax.set_title(title)
    ax.invert_yaxis()
    ax.set_xlim(0, max(r.times_min[-1] for r in results))
    if n >= 2:
        ax.legend(loc="lower left", ncols=1 if n <= 4 else 2)
    if label_ends:
        ax.margins(x=0.18)
    return ax


def concentration_map(result: SimulationResult, ax=None, *, colorbar: bool = True,
                      title: str | None = None):
    """Height-versus-time map of the red-cell volume fraction."""
    apply_style()
    if ax is None:
        _, ax = plt.subplots(figsize=(7.2, 4.4))
    _tidy(ax)
    ax.grid(False)

    t_edges = _edges(result.times_min)
    z_edges = result.z_faces / MM
    mesh = ax.pcolormesh(t_edges, z_edges, result.phi.T, cmap=BLOOD_CMAP,
                         norm=Normalize(0.0, result.blood.max_packing),
                         shading="flat", rasterized=True)
    ax.plot(result.times_min, result.interface_mm, color=INK, lw=1.8, zorder=3)
    ax.plot(result.times_min, result.sediment_mm, color=INK, lw=1.4, ls="--", zorder=3)
    # direct labels, haloed so they stay legible over any fill
    k = max(int(0.30 * len(result.times_min)), 1)
    _halo(ax.annotate("plasma boundary", (result.times_min[k], result.interface_mm[k]),
                      xytext=(0, -12), textcoords="offset points", color=INK,
                      fontsize=8.5, ha="center", va="top", zorder=4))
    _halo(ax.annotate("sediment top", (result.times_min[-1], result.sediment_mm[-1]),
                      xytext=(-4, 8), textcoords="offset points", color=INK,
                      fontsize=8.5, ha="right", va="bottom", zorder=4))

    ax.set_xlabel("time (min)")
    ax.set_ylabel("height above tube bottom (mm)")
    ax.set_title(title or f"{result.label} - concentration field")
    if colorbar:
        cb = ax.figure.colorbar(mesh, ax=ax, pad=0.02)
        cb.set_label("red-cell volume fraction", color=INK_2, fontsize=9)
        cb.outline.set_visible(False)
        cb.ax.tick_params(color=MUTED, labelcolor=MUTED, labelsize=8.5)
    return ax


def tube_snapshots(result: SimulationResult, times_min: Sequence[float] | None = None,
                   ax=None, *, colorbar: bool = True, title: str | None = None):
    """The tube itself at a few times, filled with the concentration profile."""
    apply_style()
    if times_min is None:
        end = result.times_min[-1]
        times_min = [0.0, end * 0.125, end * 0.25, end * 0.5, end]
    picks = [int(np.argmin(np.abs(result.times_min - t))) for t in times_min]

    if ax is None:
        _, ax = plt.subplots(figsize=(1.15 * len(picks) + 1.4, 4.8))
    _tidy(ax)
    ax.grid(False)

    pitch = 1.25
    vmax = result.blood.max_packing
    for column, k in enumerate(picks):
        draw_tube(ax, result.geometry, result.phi[k], result.z_faces,
                  x_center=column * pitch, half_width=0.34, vmax=vmax)
        ax.annotate(f"{result.times_min[k]:.0f}", (column * pitch, 0.0),
                    xycoords=ax.get_xaxis_transform(), xytext=(0, -14),
                    textcoords="offset points", ha="center", va="top",
                    color=INK_2, fontsize=8.5, annotation_clip=False)

    ax.set_ylim(0.0, result.z_faces[-1] / MM)
    ax.set_xlim(-0.75, (len(picks) - 1) * pitch + 0.75)
    ax.set_xticks([])
    ax.set_xlabel("minutes  (width exaggerated)", labelpad=16)
    ax.set_ylabel("height above tube bottom (mm)")
    ax.set_title(title or f"{result.label}")
    if colorbar:
        sm = plt.cm.ScalarMappable(cmap=BLOOD_CMAP, norm=Normalize(0.0, vmax))
        cb = ax.figure.colorbar(sm, ax=ax, pad=0.03)
        cb.set_label("red-cell volume fraction", color=INK_2, fontsize=9)
        cb.outline.set_visible(False)
        cb.ax.tick_params(color=MUTED, labelcolor=MUTED, labelsize=8.5)
    return ax


def esr_bars(results: Sequence[SimulationResult], ax=None, *, hours: float = 1.0,
             title: str | None = None):
    """One bar per case: the ESR reading."""
    apply_style()
    if ax is None:
        _, ax = plt.subplots(figsize=(6.4, 3.6))
    _tidy(ax)
    ax.grid(axis="y", visible=False)

    values = [r.esr(hours) for r in results]
    colors = [color_for(i) for i in range(len(results))]
    y = np.arange(len(results))[::-1]
    ax.barh(y, values, height=0.62, color=colors, zorder=3)
    for yi, value in zip(y, values):
        ax.annotate(f"{value:.1f}", (value, yi), xytext=(5, 0),
                    textcoords="offset points", va="center", color=INK_2, fontsize=9)
    ax.set_yticks(y)
    ax.set_yticklabels([r.label for r in results], fontsize=8.5, color=INK_2)
    ax.set_xlabel(f"ESR at {hours:g} h (mm)")
    ax.set_title(title or f"Reading after {hours:g} hour" + ("s" if hours != 1 else ""))
    ax.margins(x=0.16)
    return ax


def geometry_gallery(geometries: Sequence[TubeGeometry], ax=None, *,
                     title: str = "Tube geometries"):
    """Outlines of several tubes side by side, to scale in height."""
    apply_style()
    if ax is None:
        _, ax = plt.subplots(figsize=(1.15 * len(geometries) + 1.0, 4.4))
    _tidy(ax)
    ax.grid(False)
    widest = max(float(g.radius(np.linspace(0, g.length, 200)).max()) for g in geometries)
    for i, geo in enumerate(geometries):
        z = np.linspace(0.0, geo.length, 400)
        r = 0.42 * geo.radius(z) / widest
        ax.fill_betweenx(z / MM, i - r, i + r, color=SERIES[i % len(SERIES)],
                         alpha=0.18, zorder=2)
        ax.plot(i - r, z / MM, color=color_for(i), lw=1.4)
        ax.plot(i + r, z / MM, color=color_for(i), lw=1.4)
        ax.annotate(_wrap(geo.name, 13), (i, 0.0), xycoords=ax.get_xaxis_transform(),
                    xytext=(0, -12), textcoords="offset points", ha="center",
                    va="top", color=INK_2, fontsize=8.5, annotation_clip=False)
    ax.set_ylim(0.0, max(g.length for g in geometries) / MM)
    ax.set_xlim(-0.6, len(geometries) - 0.4)
    ax.set_xticks([])
    ax.set_ylabel("height (mm)")
    ax.set_title(title)
    return ax


# -- composed figures --------------------------------------------------
def case_report(result: SimulationResult, *, times_min: Sequence[float] | None = None):
    """Three-panel figure for a single run."""
    apply_style()
    fig = plt.figure(figsize=(13.0, 5.2), layout="constrained")
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.25, 1.35])
    tube_snapshots(result, times_min, ax=fig.add_subplot(gs[0, 0]), colorbar=False,
                   title="Column")
    settling_curves([result], ax=fig.add_subplot(gs[0, 1]),
                    title="Sedimentation curve")
    concentration_map(result, ax=fig.add_subplot(gs[0, 2]))
    info = summarise(result)
    fig.suptitle(
        f"{result.label}  |  Hct {info['hematocrit']:.0%}, "
        f"aggregate {info['aggregate_um']:.0f} um  |  "
        f"ESR 1 h = {info['esr_1h_mm']:.1f} mm",
        color=INK, fontsize=12.5, fontweight="bold")
    return fig


def comparison(results: Sequence[SimulationResult], *, hours: float = 1.0,
               title: str = "Sedimentation in tubes of different geometry"):
    """Gallery of shapes, the curves, and the readings, in one figure."""
    apply_style()
    fig = plt.figure(figsize=(13.5, 8.6), layout="constrained")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.35, 1.0])

    ax_gallery = fig.add_subplot(gs[0, :])
    _tidy(ax_gallery)
    ax_gallery.grid(False)
    vmax = max(r.blood.max_packing for r in results)
    widest = max(float(r.geometry.radius(np.linspace(0, r.geometry.length, 200)).max())
                 for r in results)
    for i, res in enumerate(results):
        r_max = float(res.geometry.radius(np.linspace(0, res.geometry.length, 200)).max())
        draw_tube(ax_gallery, res.geometry, res.phi[-1], res.z_faces, x_center=i,
                  half_width=0.40 * r_max / widest, vmax=vmax, outline=color_for(i))
        ax_gallery.annotate(_wrap(res.label, 13), (i, 0.0),
                            xycoords=ax_gallery.get_xaxis_transform(),
                            xytext=(0, -12), textcoords="offset points",
                            ha="center", va="top", color=INK_2, fontsize=8.5,
                            annotation_clip=False)
    ax_gallery.set_ylim(0.0, max(r.geometry.length for r in results) / MM)
    ax_gallery.set_xlim(-0.6, len(results) - 0.4)
    ax_gallery.set_xticks([])
    ax_gallery.set_ylabel("height (mm)")
    ax_gallery.set_title(f"Final state after {results[0].times_h[-1]:g} h "
                         f"(tube widths to scale with each other, exaggerated)")
    sm = plt.cm.ScalarMappable(cmap=BLOOD_CMAP, norm=Normalize(0.0, vmax))
    cb = fig.colorbar(sm, ax=ax_gallery, pad=0.01, fraction=0.03)
    cb.set_label("red-cell volume fraction", color=INK_2, fontsize=9)
    cb.outline.set_visible(False)
    cb.ax.tick_params(color=MUTED, labelcolor=MUTED, labelsize=8.5)

    settling_curves(results, ax=fig.add_subplot(gs[1, 0]))
    esr_bars(results, ax=fig.add_subplot(gs[1, 1]), hours=hours)

    fig.suptitle(title, color=INK, fontsize=13.5, fontweight="bold")
    return fig


def animate(result: SimulationResult, path: str | Path, *, fps: int = 12,
            step: int = 1):
    """Write a GIF of the tube emptying out.  Needs Pillow."""
    from matplotlib.animation import FuncAnimation, PillowWriter

    apply_style()
    fig, (ax_tube, ax_curve) = plt.subplots(
        1, 2, figsize=(7.0, 4.8), gridspec_kw={"width_ratios": [1.0, 1.6]})
    _tidy(ax_tube)
    _tidy(ax_curve)
    ax_tube.grid(False)

    frames = range(0, len(result.times), max(step, 1))
    vmax = result.blood.max_packing
    z_mm = result.z_faces / MM

    ax_curve.set_xlim(0, result.times_min[-1])
    ax_curve.set_ylim(max(result.fall_mm) * 1.05 + 1e-9, 0)
    ax_curve.set_xlabel("time (min)")
    ax_curve.set_ylabel("fall of the boundary (mm)")
    ax_curve.set_title("Sedimentation curve")
    (line,) = ax_curve.plot([], [], color=SERIES[0], zorder=3)
    ax_tube.set_ylim(0, z_mm[-1])
    ax_tube.set_xlim(-0.6, 0.6)
    ax_tube.set_xticks([])
    ax_tube.set_ylabel("height (mm)")
    clock = ax_tube.set_title("0 min")

    def render(k: int):
        for artist in list(ax_tube.collections) + list(ax_tube.lines):
            artist.remove()
        draw_tube(ax_tube, result.geometry, result.phi[k], result.z_faces,
                  half_width=0.36, vmax=vmax)
        line.set_data(result.times_min[:k + 1], result.fall_mm[:k + 1])
        clock.set_text(f"{result.times_min[k]:.0f} min   "
                       f"fall {result.fall_mm[k]:.1f} mm")
        return ()

    anim = FuncAnimation(fig, render, frames=frames, blit=False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    anim.save(path, writer=PillowWriter(fps=fps))
    plt.close(fig)
    return path


def flow_arrows(ax, result: SimulationResult, index: int = -1, *, x_center: float = 0.0,
                exaggeration: float = 1.0, rows: int = 12, tilt: bool = False) -> None:
    """Arrows for the two phases, drawn along gravity rather than along the tube.

    Cells fall vertically whatever angle the tube is at -- that is precisely why
    tilting one speeds it up -- so the arrows stay vertical while the tube
    leans.  Lengths are proportional to the local speed, taken straight from the
    solved field (see :mod:`bloodsed.flows`).
    """
    from .flows import velocity_field_mm_per_hour

    geo = result.geometry
    cells, plasma = velocity_field_mm_per_hour(result, index)
    fastest = max(float(np.max(plasma)), float(np.max(cells[result.phi[index] > 0.01]))
                  if np.any(result.phi[index] > 0.01) else 0.0, 1e-9)
    span = result.fill_height / MM
    scale = 0.06 * span / fastest
    angle = geo.tilt_deg if tilt else 0.0
    foot = (x_center, 0.0)

    for row in range(rows):
        z = (row + 0.5) * result.fill_height / rows
        i = int(np.argmin(np.abs(result.z_centers - z)))
        z_mm = result.z_centers[i] / MM
        outer = exaggeration * float(geo.radius(z)) / MM
        inner = exaggeration * float(geo.inner_radius(z)) / MM
        offset = 0.45 * (outer + inner) if inner > 0 else 0.45 * outer
        for side, speed, color, direction in (
            (-offset, min(cells[i], fastest), BLOOD_STEPS[6], -1.0),
            (offset, plasma[i], "#2f7d9e", 1.0),
        ):
            if speed * scale < 0.004 * span:
                continue
            x, y = _rotate(np.array([x_center + side]), np.array([z_mm]), *foot, angle)
            tip_x, tip_y = x[0], y[0] + direction * speed * scale
            ax.annotate("", xy=(tip_x, tip_y), xytext=(x[0], y[0]),
                        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.5,
                                        shrinkA=0, shrinkB=0, mutation_scale=10), zorder=5)


def velocity_profile(result: SimulationResult, index: int = -1, ax=None, *,
                     title: str | None = None):
    """Speed of each phase against height, in the unit the reading uses.

    The two curves are not independent: nothing leaves the tube, so the plasma
    displaced by the falling cells has to come back up past them, and
    ``phi * v_cells = (1 - phi) * v_plasma`` at every height.
    """
    from .flows import velocity_field_mm_per_hour

    apply_style()
    if ax is None:
        _, ax = plt.subplots(figsize=(5.2, 4.6))
    _tidy(ax)

    cells, plasma = velocity_field_mm_per_hour(result, index)
    z = result.z_mm
    # the speeds span three decades -- a lone cell in the clear plasma falls at
    # nearly the free Stokes speed, the packed sediment at nothing -- so the
    # axis is logarithmic rather than dominated by the fast tail
    floor = 0.05
    sparse = result.phi[index] < 1e-4
    ax.plot(np.where(sparse, np.nan, np.maximum(cells, floor)), z,
            color=BLOOD_STEPS[5], lw=2, label="cells, falling", zorder=3)
    ax.plot(np.maximum(plasma, floor), z, color="#2f7d9e", lw=2,
            label="plasma, rising", zorder=3)
    ax.set_xscale("log")
    ax.axhline(result.interface_mm[index], color=INK, lw=1.2, ls="--", zorder=2)
    _halo(ax.annotate("plasma boundary", (ax.get_xlim()[1], result.interface_mm[index]),
                      xytext=(-4, 5), textcoords="offset points", ha="right",
                      color=INK, fontsize=8.5, zorder=4))
    ax.set_xlabel("speed (mm/h)")
    ax.set_ylabel("height above tube bottom (mm)")
    ax.set_title(title or f"Flow at {result.times_min[index]:.0f} min")
    ax.legend(loc="lower right")
    ax.set_xlim(floor, max(float(np.max(cells)), float(np.max(plasma)), 1.0) * 1.6)
    return ax


def flow_report(result: SimulationResult, index: int = -1):
    """The tube with its flow field beside the profile that produced it."""
    apply_style()
    fig = plt.figure(figsize=(11.0, 5.4), layout="constrained")
    gs = fig.add_gridspec(1, 3, width_ratios=[1.05, 1.0, 1.15])

    ax_tube = fig.add_subplot(gs[0, 0])
    _tidy(ax_tube)
    ax_tube.grid(False)
    ax_tube.set_aspect("equal")
    tilt = result.geometry.tilt_deg != 0
    exaggeration = tube_exaggeration(result.geometry)
    draw_tube(ax_tube, result.geometry, result.phi[index], result.z_faces,
              exaggeration=exaggeration, vmax=result.blood.max_packing, tilt=tilt)
    flow_arrows(ax_tube, result, index, exaggeration=exaggeration, tilt=tilt)
    span = result.geometry.length / MM
    lean = math.sin(math.radians(result.geometry.tilt_deg)) * span
    ax_tube.set_ylim(-0.03 * span, 1.05 * span)
    ax_tube.set_xlim(-0.25 * span, 0.25 * span + lean)
    ax_tube.set_xticks([])
    ax_tube.set_ylabel("height above tube bottom (mm)")
    ax_tube.set_title(f"{result.times_min[index]:.0f} min")
    if tilt:
        _halo(ax_tube.annotate(f"tilted {result.geometry.tilt_deg:g}°",
                               (0.5, 0.01), xycoords="axes fraction", ha="center",
                               color=INK_2, fontsize=9))

    velocity_profile(result, index, ax=fig.add_subplot(gs[0, 1]))
    concentration_map(result, ax=fig.add_subplot(gs[0, 2]))
    fig.suptitle(f"{result.label} — flow field", color=INK, fontsize=12.5,
                 fontweight="bold")
    return fig


def save(fig, path: str | Path, *, dpi: int = 150) -> Path:
    """Save and close a figure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def _halo(artist, width: float = 2.6):
    """Outline text in the surface colour so it survives any background."""
    import matplotlib.patheffects as pe
    artist.set_path_effects([pe.withStroke(linewidth=width, foreground=SURFACE)])
    return artist


def _edges(centers: np.ndarray) -> np.ndarray:
    centers = np.asarray(centers, dtype=float)
    if centers.size == 1:
        return np.array([centers[0] - 0.5, centers[0] + 0.5])
    mid = 0.5 * (centers[:-1] + centers[1:])
    return np.concatenate([[centers[0] - (mid[0] - centers[0])], mid,
                           [centers[-1] + (centers[-1] - mid[-1])]])


def _wrap(text: str, width: int = 16) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines[:3])


def sweep_figure(results: Sequence[SimulationResult], values: Sequence[float],
                 parameter: str):
    """ESR against a swept parameter, with the underlying curves beside it."""
    apply_style()
    labels = {
        "hematocrit": "hematocrit (cell volume fraction)",
        "aggregate": "rouleaux diameter (um)",
        "tilt": "tilt from vertical (degrees)",
        "viscosity": "plasma viscosity (Pa.s)",
    }
    fig = plt.figure(figsize=(11.5, 4.6), layout="constrained")
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.15])

    ax = fig.add_subplot(gs[0, 0])
    _tidy(ax)
    esr1 = [r.esr(1.0) for r in results]
    ax.plot(values, esr1, color=SERIES[0], marker="o", markersize=5, zorder=3)
    for x, y in zip(values, esr1):
        ax.annotate(f"{y:.1f}", (x, y), xytext=(0, 7), textcoords="offset points",
                    ha="center", color=INK_2, fontsize=8.5)
    ax.set_xlabel(labels.get(parameter, parameter))
    ax.set_ylabel("ESR at 1 h (mm)")
    ax.set_title(f"Effect of {parameter} on the one-hour reading")
    ax.margins(y=0.18)

    settling_curves(results, ax=fig.add_subplot(gs[0, 1]),
                    title="Underlying sedimentation curves")
    return fig
