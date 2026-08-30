"""Manufacturing export — the DRIVING / DERIVED drawing sheet of addendum §A.

Addendum §A states the manufacturing rule plainly: *"diameters must be driven from the
small end. The base diameter is a derived output and must never be an input, because
the diameter changes by 2 tan(theta) per millimetre and any rounding at the base
propagates undiminished up to the blood line."*

So this module emits a sheet where every dimension is labelled ``DRIVING`` or
``DERIVED``, and refuses to emit a base diameter as a driving dimension.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Literal

from ..core import geometry as geo
from ..core.geometry import Cone
from ..tiers import Result, ResultSet

__all__ = ["DimensionRole", "drawing_sheet", "stl_parameters", "to_json"]

DimensionRole = Literal["DRIVING", "DERIVED"]


def _dim(name: str, value: Any, unit: str, role: DimensionRole, **kw: Any) -> Result:
    notes = tuple(kw.pop("notes", ()))
    if role == "DERIVED":
        notes += ("DERIVED — never a manufacturing input (addendum §A)",)
    else:
        notes += ("DRIVING — cut to this",)
    return Result.exact(name, value, unit, flags=(role,), notes=notes,
                        source="v1.1 addendum §A", **kw)


def drawing_sheet(
    cone: Cone,
    *,
    step_w_mm: float | None = 0.30,
    upper_angle_offset_deg: float = -2.0,
    cylinder_height_mm: float = 12.0,
) -> ResultSet:
    """A dimension sheet with every entry labelled DRIVING or DERIVED."""
    a, b = cone.x_bl_mm, cone.x_base_mm

    results: list[Result] = [
        _dim("generation", cone.generation, "", "DRIVING",
             notes=("Gen-B: blood line IS the mouth, cone body 50.000 mm"
                    if cone.generation == "B"
                    else "Gen-A: 5.000 mm mouth, 3.000 mm above the blood line — "
                         "SUPERSEDED",)),
        _dim("theta_outer", cone.theta_o_deg, "deg", "DRIVING"),
        _dim("theta_inner", cone.theta_i_deg, "deg", "DRIVING"),
        _dim("delta_apex_offset", cone.delta_mm, "mm", "DRIVING"),
        _dim("x_apex_to_bloodline", a, "mm", "DRIVING"),
        _dim("column_length", cone.length_mm, "mm", "DRIVING"),
        _dim("cone_body_length", cone.cone_body_mm, "mm", "DRIVING"),
        _dim("gap_at_bloodline", cone.gap_perpendicular(a), "mm", "DRIVING"),
        _dim("d_outer_at_bloodline", cone.d_outer(a), "mm", "DRIVING",
             notes=("the small end: drive the taper from here",)),
        _dim("d_inner_at_bloodline", cone.d_inner(a), "mm", "DRIVING"),
        _dim("cylinder_height_above_bloodline", cylinder_height_mm, "mm", "DRIVING"),
        # --- derived ---
        _dim("d_outer_at_base", cone.d_outer(b), "mm", "DERIVED"),
        _dim("d_inner_at_base", cone.d_inner(b), "mm", "DERIVED"),
        _dim("gap_at_base", cone.gap_perpendicular(b), "mm", "DERIVED"),
        _dim("clearance_at_bloodline", cone.clearance_radial(a), "mm", "DERIVED"),
        _dim("volume", cone.volume_numeric(), "mm^3", "DERIVED"),
        _dim("diameter_change_per_mm", 2.0 * math.tan(cone.theta_o), "mm/mm", "DERIVED",
             notes=("this is why the base must not be a driving dimension: any "
                    "rounding there reaches the blood line undiminished",)),
    ]

    if step_w_mm is not None:
        step = geo.stepped_upper_cone(
            cone, w_mm=step_w_mm, upper_angle_offset_deg=upper_angle_offset_deg
        )
        results += [
            _dim("step_width_w", step_w_mm, "mm", "DRIVING",
                 notes=("edge must be SHARP: a 0.1 mm fillet costs ~0.1 mm of level "
                        "certainty, a 0.3 mm fillet removes the edge",)),
            _dim("theta_upper", step["theta_upper"].value, "deg", "DRIVING"),
            _dim("d_upper_base", step["d_upper_base"].value, "mm", "DERIVED"),
            _dim("tip_height_above_bloodline", step["tip_height_above_bloodline"].value,
                 "mm", "DERIVED"),
            _dim("land_area", step["land_area"].value, "mm^2", "DERIVED"),
            _dim("clearance_above_min", step["clearance_above_min"].value, "mm",
                 "DERIVED"),
        ]

    return ResultSet(
        title=f"DRAWING SHEET — {cone.tube_id or 'cone'} (Gen-{cone.generation})",
        results=tuple(results),
        notes=(
            "MANUFACTURING RULE (addendum §A): drive diameters from the SMALL end. The "
            "base diameter is an output and must never be an input.",
            f"the outer diameter changes by {2 * math.tan(cone.theta_o):.4f} mm per mm "
            "of depth, so a rounding error at the base arrives undiminished at the "
            "blood line",
            "measured gaps run 1-4 percent above nominal after 3000-grit polishing "
            "(spec §G.6); the TAPER base measured 0.923 against a 0.900 nominal",
        ),
    )


def stl_parameters(cone: Cone, **kwargs: Any) -> dict[str, Any]:
    """A flat parameter dict for a CAD/STL generator, DRIVING dimensions only."""
    sheet = drawing_sheet(cone, **kwargs)
    return {
        r.name: r.value for r in sheet if "DRIVING" in r.flags
    } | {"_note": "DRIVING dimensions only; derived values must be recomputed by the "
                  "CAD model, never transcribed (addendum §A)"}


def to_json(results: ResultSet, indent: int = 2) -> str:
    """Serialise any result set, tiers included."""
    return json.dumps(results.to_dict(), indent=indent, ensure_ascii=False)
