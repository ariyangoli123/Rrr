"""Benchmark against a plain tilted tube — v1.1 addendum §D, marked CRITICAL.

Addendum §D: *"every performance claim must be reported against TWO references, not
one. This is what a patent examiner and a journal referee ask."*

    Reference 1 — vertical Westergren: 3.15-3.90x acceleration.
    Reference 2 — a plain tube of the SAME volume and height, tilted to the SAME angle.

The second reference is the uncomfortable one, and this module exists to keep it in
view. A plain iso-volume tube is 7.14 mm across, so ``L/D = 7.01`` — **below** the
saturation ceiling ``L/d ~ 12``. It therefore collects the full PNK benefit, while the
cone throws away everything it has above 12. The cone's advantage over a tilted plain
tube is only 1.35-1.50x, not 5x.

The mandatory warning
---------------------
That same plain tube tilted to 20 degrees reaches ``E ~ 3.34`` — level with these
cones. US 5,594,164 reports about 3x from tilting alone, and commercial instruments
tilt 18-20 degrees. **A claim of large speed superiority over simply tilting is not
defensible.** The defensible claims are: operation in the vertical position,
elimination of sensitivity to mounting angle, and more range in the same volume.

What is missing
---------------
``E_plain`` is a PNK **prediction**, never a measurement. The control experiment — a
plain 7.14 x 50 mm tube at 0, 10 and 20 degrees — has never been run. Addendum §D calls
it *"the most important unmeasured quantity in the project"* (unknown U12, missing datum
M04). Every comparison below therefore pits a measurement against a theory, and says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..registry import load_yaml, measured
from ..tiers import N_EQUALS_1, Result, ResultSet, Tier
from ..units import check_positive
from .geometry import Cone, from_library, range_ceiling
from .kinetics import E_pnk

__all__ = [
    "PlainTube",
    "plain_tube_equivalent",
    "E_plain",
    "benchmark",
    "tilt_warning",
]


@dataclass(frozen=True, slots=True)
class PlainTube:
    """A plain cylindrical tube matched in volume and column height."""

    volume_mm3: float
    length_mm: float

    @property
    def area_mm2(self) -> float:
        return self.volume_mm3 / self.length_mm

    @property
    def diameter_mm(self) -> float:
        return math.sqrt(4.0 * self.area_mm2 / math.pi)

    @property
    def l_over_d(self) -> float:
        return self.length_mm / self.diameter_mm


def plain_tube_equivalent(cone: Cone) -> ResultSet:
    """The iso-volume, iso-height plain tube — addendum §D.

    For the 2000 mm^3 family: ``A = 40.0 mm^2``, ``D = 7.14 mm``, ``L/D = 7.01``.
    """
    plain = PlainTube(volume_mm3=cone.volume_numeric(), length_mm=cone.length_mm)
    return ResultSet(
        title=f"PLAIN-TUBE EQUIVALENT of {cone.tube_id or 'cone'}",
        results=(
            Result.exact("plain_area", plain.area_mm2, "mm^2",
                         source="v1.1 addendum §D"),
            Result.exact("plain_diameter", plain.diameter_mm, "mm"),
            Result.exact("plain_L_over_D", plain.l_over_d, ""),
            Result.exact(
                "plain_below_saturation_ceiling", bool(plain.l_over_d < 12.0), "",
                notes=("L/D is BELOW the L/d ~ 12 saturation ceiling, so the plain "
                       "tube collects the full PNK benefit while the cone wastes "
                       "everything it has above 12 — that is why the ratio is 1.4 and "
                       "not 5 (addendum §D)",),
            ),
        ),
        notes=("same volume, same column height, as required by addendum §D",),
    )


def E_plain(theta_deg: float, plain: PlainTube) -> Result:
    """PNK enhancement of the plain tube at a given tilt — a PREDICTION, not a datum.

    Always ESTIMATED and always carrying the missing-measurement note: the control
    experiment has never been run (unknown U12).
    """
    check_positive("plain_diameter", plain.diameter_mm)
    th = math.radians(theta_deg)
    value = math.cos(th) + plain.l_over_d * math.sin(th)
    note = load_yaml("calibration.yaml")["benchmark"]["plain_tube"]["note"]
    return Result.estimated(
        "E_plain", value, "x",
        source="PNK prediction; v1.1 addendum §D",
        notes=(
            " ".join(str(note).split()),
            f"tilt {theta_deg:.3f} deg, L/D {plain.l_over_d:.2f}",
            "PREDICTION, NOT A MEASUREMENT — unknown U12, missing datum M04",
        ),
    )


def tilt_warning() -> Result:
    """The mandatory warning of addendum §D, printed by every benchmark."""
    bench = load_yaml("calibration.yaml")["benchmark"]["plain_tube"]
    lo, hi = bench["commercial_tilt_deg"]
    plain = PlainTube(volume_mm3=2000.0, length_mm=50.0)
    e20 = E_plain(20.0, plain).value
    return Result.estimated(
        "tilt_comparison_warning", e20, "x",
        source="v1.1 addendum §D",
        flags=("TILT_CLAIM_NOT_DEFENSIBLE",),
        notes=(
            f"the SAME plain tube tilted to 20 deg reaches E ~ {e20:.2f}, level with "
            "these cones",
            f"{bench['patent_reference']}; commercial instruments tilt {lo:.0f}-{hi:.0f} deg",
            "A CLAIM OF LARGE SPEED SUPERIORITY OVER SIMPLY TILTING IS NOT DEFENSIBLE.",
            "Defensible claims: operation in the vertical position; no sensitivity to "
            "mounting angle; more range in the same volume.",
        ),
    )


def benchmark(
    cone: Cone, hematocrit: float = 0.45, *, phi_pack: float = 0.90
) -> ResultSet:
    """Both references, side by side — addendum §D."""
    plain_set = plain_tube_equivalent(cone)
    plain = PlainTube(volume_mm3=cone.volume_numeric(), length_mm=cone.length_mm)

    e_plain = E_plain(cone.theta_o_deg, plain)
    e_measured = _measured_E(cone.tube_id)

    gap = cone.gap_perpendicular(cone.x_bl_mm)
    if not cone.is_constant_gap:
        gap = 0.5 * (gap + cone.gap_perpendicular(cone.x_base_mm))
    e_cone_pnk = E_pnk(cone.theta_o_deg, cone.length_mm, gap)

    results: list[Result] = list(plain_set) + [e_plain, e_cone_pnk]

    if e_measured is not None:
        results.append(e_measured)
        ratio = e_measured.value / e_plain.value
        results.append(
            Result(
                name="cone_over_plain_tilted",
                value=ratio,
                unit="x",
                tier=Tier.ESTIMATED,      # a measurement over a prediction
                source="v1.1 addendum §D",
                flags=(N_EQUALS_1,),
                notes=(
                    "MEASUREMENT (cone) divided by PREDICTION (plain tube). Not a "
                    "measured ratio. The control experiment has never been run "
                    "(unknown U12).",
                    "n = 1",
                ),
            )
        )
        results.append(
            Result.calibrated(
                "cone_over_vertical_westergren", e_measured.value, "x",
                source="reference 1: vertical Westergren",
                flags=(N_EQUALS_1,),
                notes=("n = 1",),
            )
        )

    # Range comparison: cone against the plain tube (addendum §D, 1.29-1.40x).
    ceiling = range_ceiling(cone, hematocrit, phi_pack)
    plain_ceiling = plain.volume_mm3 * (1.0 - hematocrit / phi_pack) / plain.area_mm2
    results.append(ceiling.rename("cone_range_ceiling"))
    results.append(
        Result.estimated(
            "plain_range_ceiling", plain_ceiling, "mm",
            source="v1.1 addendum §D",
            notes=(f"rides on phi_pack = {phi_pack:.2f} (unknown U01)",),
        )
    )
    results.append(
        Result.estimated(
            "range_advantage", float(ceiling.value) / plain_ceiling, "x",
            source="v1.1 addendum §D",
            notes=("addendum §D quotes 1.29-1.40x for the family",),
        )
    )
    results.append(tilt_warning())

    return ResultSet(
        title=f"BENCHMARK — {cone.tube_id or 'cone'} against TWO references",
        results=tuple(results),
        notes=(
            "Reference 1: vertical Westergren. Reference 2: an iso-volume, iso-height "
            "plain tube tilted to the same angle (addendum §D).",
            "E_plain is a PNK PREDICTION. The control experiment — a plain 7.14 x 50 mm "
            "tube at 0, 10 and 20 degrees — has never been run. Addendum §D calls it "
            "the most important unmeasured quantity in the project (U12 / M04).",
            "Every ratio below compares a measurement on one side with a theory on the "
            "other. It is not a measured comparison.",
        ),
    )


def _measured_E(tube_id: str) -> Result | None:
    if not tube_id:
        return None
    for entry in measured()["enhancement"]["entries"]:
        if entry["tube"] == tube_id:
            return Result.calibrated(
                "E_cone_measured", float(entry["E_measured"]), "x",
                source="sample 1; spec §5.2",
                flags=(N_EQUALS_1,),
                notes=("n = 1",),
            )
    return None
