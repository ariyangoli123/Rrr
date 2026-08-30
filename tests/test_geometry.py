"""Geometry regression tests — ESR_SIMULATOR_SPEC.md §10, tests/test_geometry.py.

These encode verified numbers and catch sign and unit errors immediately, which is why
the build prompt asks for them first.
"""

from __future__ import annotations

import math

import pytest

from esrsim.core import geometry as geo
from esrsim.registry import tube_library
from esrsim.tiers import Tier

ALL_TUBES = list(geo.list_tubes())

#: The published table is quoted to 4 decimal places, so 5e-5 is "reproduces to 4 dp".
DP4 = 5e-5

#: Exception: the published `clearance` column is internally inconsistent with
#: d/cos(theta) by up to 2e-4 (T090 lists 0.9140 where d/cos(theta) = 0.91381; T080
#: lists 0.8163 where it is 0.81638). The construction rule is not in doubt — every
#: other column lands inside 5e-5 — so the tolerance is widened only here, and only
#: enough to cover the table's own rounding.
CLEARANCE_TOL = 2.5e-4


@pytest.fixture(scope="module")
def library() -> dict:
    return tube_library()["tubes"]


def test_tube_library_reproduces_all_dimensions_to_4dp(library: dict) -> None:
    """Spec §10. All six tubes, every published column, from the construction rule."""
    for tube_id in ALL_TUBES:
        cone = geo.from_library(tube_id)
        pub = library[tube_id]["published"]
        a, b = cone.x_bl_mm, cone.x_base_mm

        assert cone.delta_mm == pytest.approx(pub["delta_mm"], abs=DP4), tube_id
        assert cone.d_outer(a) == pytest.approx(pub["d_bloodline_mm"], abs=DP4), tube_id
        assert cone.d_inner(a) == pytest.approx(
            pub["d_inner_bloodline_mm"], abs=DP4
        ), tube_id
        assert cone.d_outer(b) == pytest.approx(pub["d_base_mm"], abs=DP4), tube_id
        assert cone.clearance_radial(a) == pytest.approx(
            pub["clearance_mm"], abs=CLEARANCE_TOL
        ), tube_id


def test_published_clearance_column_is_the_only_loose_one(library: dict) -> None:
    """Document the table inconsistency instead of hiding it in a wide tolerance.

    Every published column except `clearance` reproduces inside 5e-5. `clearance` is
    off by up to 2e-4 because the table rounds it independently of theta.
    """
    worst = max(
        abs(
            geo.from_library(t).clearance_radial(geo.from_library(t).x_bl_mm)
            - library[t]["published"]["clearance_mm"]
        )
        for t in ALL_TUBES
    )
    assert DP4 < worst < CLEARANCE_TOL, (
        f"published clearance column now agrees to {worst:.2e}; if the table was "
        "corrected, tighten CLEARANCE_TOL to DP4"
    )


@pytest.mark.parametrize("tube_id", ALL_TUBES)
def test_published_volume_is_reproduced(tube_id: str, library: dict) -> None:
    """Volumes land on target within the rounding of the published 3-dp angles."""
    cone = geo.from_library(tube_id)
    target = library[tube_id]["published"]["volume_mm3"]
    err_pct = 100.0 * abs(cone.volume_numeric() - target) / target
    assert err_pct < 0.05, f"{tube_id}: {err_pct:.4f}% off {target} mm^3"


@pytest.mark.parametrize("tube_id", [t for t in ALL_TUBES if t != "TAPER"])
def test_closed_form_volume_matches_numeric_integration(tube_id: str) -> None:
    """Spec §10: error < 0.1%. Build prompt tightens it to < 0.05%."""
    cone = geo.from_library(tube_id)
    closed = cone.volume_closed_form()
    numeric = cone.volume_numeric()
    err_pct = 100.0 * abs(closed - numeric) / numeric
    assert err_pct < 0.05, f"{tube_id}: closed form vs numeric {err_pct:.5f}%"


def test_closed_form_rejects_tapered_cone() -> None:
    """The spec's closed form assumes theta_i == theta_o; it must refuse otherwise."""
    with pytest.raises(ValueError, match="theta_i == theta_o"):
        geo.from_library("TAPER").volume_closed_form()


@pytest.mark.parametrize("tube_id", [t for t in ALL_TUBES if t != "TAPER"])
def test_clearance_equals_gap_over_cos_theta(tube_id: str, library: dict) -> None:
    """Spec §10. Constant-gap identity, checked over the whole column."""
    cone = geo.from_library(tube_id)
    gap = library[tube_id]["gap_mm"]
    expected = gap / math.cos(cone.theta_o)
    for h in (0.0, 12.5, 25.0, 37.5, 50.0):
        assert cone.clearance_radial(cone.x_bl_mm + h) == pytest.approx(
            expected, abs=1e-9
        ), f"{tube_id} at h={h}"


@pytest.mark.parametrize("tube_id", [t for t in ALL_TUBES if t != "TAPER"])
def test_delta_equals_gap_over_sin_theta(tube_id: str, library: dict) -> None:
    """Spec §10."""
    cone = geo.from_library(tube_id)
    gap = library[tube_id]["gap_mm"]
    assert cone.delta_mm == pytest.approx(gap / math.sin(cone.theta_o), abs=1e-9)


@pytest.mark.parametrize("tube_id", [t for t in ALL_TUBES if t != "TAPER"])
def test_perpendicular_gap_is_the_nominal_gap(tube_id: str, library: dict) -> None:
    """gap_perpendicular = clearance * cos(theta) recovers the nominal gap exactly."""
    cone = geo.from_library(tube_id)
    assert cone.gap_perpendicular(cone.x_bl_mm) == pytest.approx(
        library[tube_id]["gap_mm"], abs=1e-9
    )


def test_taper_angle_difference_gives_040mm_over_50mm() -> None:
    """Spec §10: the taper opens 0.40 mm over the 50 mm column."""
    cone = geo.from_library("TAPER")
    rate = geo.taper_opening_rate(cone)
    assert rate.tier is Tier.EXACT
    assert rate.value * cone.length_mm == pytest.approx(0.40, abs=0.005)


def test_taper_runs_from_050_to_090() -> None:
    """Addendum: gap 0.50 at the blood line, 0.90 at the base."""
    cone = geo.from_library("TAPER")
    assert cone.gap_perpendicular(cone.x_bl_mm) == pytest.approx(0.500, abs=0.001)
    assert cone.gap_perpendicular(cone.x_base_mm) == pytest.approx(0.900, abs=0.001)


# ------------------------------------------------------------------- generations


def test_gen_a_and_gen_b_describe_the_same_solid() -> None:
    """Addendum §A: only the mouth position and labelling change, not the cone."""
    b = geo.from_library("T070", generation="B")
    a = b.as_generation("A")
    assert a.theta_o_deg == b.theta_o_deg
    assert a.delta_mm == b.delta_mm
    assert a.x_bl_mm == b.x_bl_mm
    assert a.volume_numeric() == pytest.approx(b.volume_numeric(), rel=1e-12)


def test_gen_b_mouth_is_the_bloodline_diameter() -> None:
    """Addendum §A: mouth diameter in Gen-B equals D at the blood line."""
    expected = {
        "T090": 6.0550, "T080": 6.2227, "T070": 6.4367,
        "T060": 6.7171, "T050": 6.7468, "TAPER": 6.3324,
    }
    for tube_id, d_mouth in expected.items():
        cone = geo.from_library(tube_id, generation="B")
        assert cone.mouth_diameter_mm == pytest.approx(d_mouth, abs=DP4), tube_id
        assert cone.blood_line_offset_mm == 0.0
        assert cone.cone_body_mm == pytest.approx(50.0)


def test_gen_a_keeps_the_old_convention() -> None:
    """Gen-A: 5.000 mm mouth, 3.000 mm above the blood line, 53.000 mm body."""
    cone = geo.from_library("T070", generation="A")
    assert cone.mouth_diameter_mm == pytest.approx(5.000)
    assert cone.blood_line_offset_mm == pytest.approx(3.000)
    assert cone.cone_body_mm == pytest.approx(53.000)


def test_every_geometry_result_is_labelled_with_its_generation() -> None:
    """Addendum §A: 'the program must label the generation in every output'."""
    for generation in ("A", "B"):
        report = geo.geometry_report(geo.from_library("T060", generation=generation))
        flag = f"GENERATION_{generation}"
        assert all(flag in r.flags for r in report), generation


# ---------------------------------------------------------------- range ceiling


def test_range_ceiling_matches_the_recorded_band() -> None:
    """Addendum §D: the cones read 32.0-34.6 mm of range, the plain tube 24.7 mm."""
    for tube_id in ("T090", "TAPER", "T060"):
        cone = geo.from_library(tube_id)
        ceiling = geo.range_ceiling(cone, hematocrit=0.45, phi_pack=0.90)
        assert 31.5 <= ceiling.value <= 35.0, f"{tube_id}: {ceiling.value:.2f} mm"


def test_range_ceiling_inherits_the_phi_pack_assumption() -> None:
    """phi_pack is unknown U01, so the ceiling can never be EXACT while it is assumed."""
    cone = geo.from_library("T090")
    assumed = geo.range_ceiling(cone, 0.45)
    assert assumed.tier is Tier.ESTIMATED
    assert any("U01" in n for n in assumed.notes)

    measured = geo.range_ceiling(cone, 0.45, 0.90, phi_pack_assumed=False)
    assert measured.tier is Tier.EXACT


def test_phi_pack_sensitivity_is_reported_by_default() -> None:
    """Spec §5.5 requires the [0.85, 0.95] band as a default output."""
    cone = geo.from_library("T090")
    band = geo.phi_pack_sensitivity(cone, hematocrit=0.45)
    names = [r.name for r in band]
    assert "range_ceiling@phi_0.85" in names
    assert "range_ceiling@phi_0.95" in names
    assert band["range_ceiling_spread"].value > 1.0, (
        "the packing assumption should move the ceiling by more than a millimetre; "
        "if it does not, U01 has stopped mattering and the register should say so"
    )


def test_range_ceiling_refuses_when_hematocrit_exceeds_packing() -> None:
    """No boundary forms; the honest answer is UNKNOWN with no number."""
    result = geo.range_ceiling(geo.from_library("T090"), hematocrit=0.95, phi_pack=0.90)
    assert result.tier is Tier.UNKNOWN
    assert result.value is None
    assert result.experiment


# ------------------------------------------------------ optional geometry features


def test_stepped_upper_cone_with_equal_angle_gives_constant_clearance() -> None:
    """Addendum §B key rule: theta_upper == theta_inner -> clearance_working + w."""
    cone = geo.from_library("T070")
    step = geo.stepped_upper_cone(cone, w_mm=0.30, upper_angle_offset_deg=0.0)
    working = step["clearance_working"].value
    assert step["clearance_above_min"].value == pytest.approx(working + 0.30, abs=1e-6)
    assert step["clearance_convergence"].value == pytest.approx(0.0, abs=1e-9)


def test_stepped_upper_cone_converges_about_011mm_over_3mm() -> None:
    """Addendum §B: with theta_upper = theta_inner - 2 deg, ~0.11 mm over 3 mm."""
    step = geo.stepped_upper_cone(
        geo.from_library("T070"), w_mm=0.30, upper_angle_offset_deg=-2.0,
        probe_height_mm=3.0,
    )
    assert step["clearance_convergence"].value == pytest.approx(0.11, abs=0.02)


def test_stepped_upper_cone_never_constricts() -> None:
    """Design rule R01: minimum clearance above the blood line >= working clearance."""
    for tube_id in ("T070", "T060", "T090"):
        step = geo.stepped_upper_cone(geo.from_library(tube_id))
        assert step["no_constriction_R01"].value is True, tube_id


def test_step_width_cannot_eat_the_inner_cone() -> None:
    with pytest.raises(ValueError, match="consumes the whole inner cone"):
        geo.stepped_upper_cone(geo.from_library("T070"), w_mm=5.0)


def test_shift_relation_is_tan_theta_inner() -> None:
    """Spec §4.4: d(clearance)/d(shift) = tan(theta_inner). T060 0.286, TAPER 0.214."""
    for tube_id, expected in (("T060", 0.2861), ("TAPER", 0.2138)):
        shifted = geo.shift_inner_cone(geo.from_library(tube_id), 1.0)
        assert shifted["d_clearance_d_shift"].value == pytest.approx(
            expected, abs=5e-4
        ), tube_id


def test_t060_shift_2mm_reproduces_gap_1150_and_volume_blowup() -> None:
    """Spec §4.4: T060 + 2 mm shift -> gap 1.150, V = 3727 mm^3."""
    shifted = geo.shift_inner_cone(geo.from_library("T060"), 2.0)
    assert shifted["gap_after"].value == pytest.approx(1.150, abs=0.002)
    assert shifted["volume_after"].value == pytest.approx(3727, rel=0.01)


def test_taper_shift_4mm_reproduces_gap_1335_to_1735() -> None:
    """Spec §4.4: TAPER + 4 mm shift -> gap 1.335 -> 1.735, V = 4035 mm^3."""
    shifted = geo.shift_inner_cone(geo.from_library("TAPER"), 4.0)
    assert shifted["gap_after"].value == pytest.approx(1.335, abs=0.003)
    assert shifted["gap_after_at_base"].value == pytest.approx(1.735, abs=0.003)
    assert shifted["volume_after"].value == pytest.approx(4035, rel=0.01)


def test_counterbore_geometry() -> None:
    """Spec §2.4a."""
    cone = geo.from_library("T060")
    cb = geo.counterbore(cone, d_cb_mm=7.0)
    assert cb["x_counterbore_bottom"].value == pytest.approx(
        3.5 / math.tan(cone.theta_o), abs=1e-9
    )
    assert cb["clearance_at_bloodline"].value == pytest.approx(
        (7.0 - cone.d_inner(cone.x_bl_mm)) / 2.0, abs=1e-9
    )


def test_t060_with_7mm_counterbore_gives_the_contradictory_0766() -> None:
    """Spec §4.3: this is the clearance that FAILED mixing while 0.696 passed."""
    cb = geo.counterbore(geo.from_library("T060"), d_cb_mm=7.0)
    assert cb["clearance_at_bloodline"].value == pytest.approx(0.766, abs=0.002)


# ------------------------------------------------------------------- area profile


def test_area_ratio_r_matches_the_spec_band_at_mid_column() -> None:
    """Spec §7.2 / addendum §E: r = A(bl)/A(interface) ~ 0.4-0.6.

    True at a mid-column interface. It is NOT true near the range ceiling, where r
    falls to 0.18-0.31 — see the companion test below; the error budget has to use the
    r at the actual interface depth rather than the quoted band.
    """
    for tube_id in ALL_TUBES:
        cone = geo.from_library(tube_id)
        r = cone.area(cone.x_bl_mm) / cone.area_at_height(15.0)
        assert 0.35 <= r <= 0.60, f"{tube_id}: r at h=15 mm is {r:.3f}"


def test_area_ratio_falls_below_the_quoted_band_near_the_ceiling() -> None:
    """The 0.4-0.6 band is a shallow-interface figure and must not be applied at depth.

    Addendum §E concludes from r ~ 0.4-0.6 that "both methods give about half the level
    shift as error". At the range ceiling r is 0.18-0.31, so reading from a fixed mark
    costs only ~0.2-0.3 of the shift while reading from the real surface costs ~0.7-0.8.
    The two methods are NOT equivalent at depth.
    """
    for tube_id in ALL_TUBES:
        cone = geo.from_library(tube_id)
        ceiling = geo.range_ceiling(cone, hematocrit=0.45).value
        r = cone.area(cone.x_bl_mm) / cone.area_at_height(ceiling)
        assert r < 0.35, f"{tube_id}: r at the ceiling is {r:.3f}"


def test_cumulative_volume_inverts_cleanly() -> None:
    cone = geo.from_library("T080")
    for h in (1.0, 7.5, 22.0, 44.0):
        assert cone.height_for_volume(cone.cumulative_volume(h)) == pytest.approx(
            h, abs=1e-6
        )


def test_geometry_report_is_entirely_exact() -> None:
    """Geometry has no fitted parameter, so nothing in the report may be weaker."""
    report = geo.geometry_report(geo.from_library("T090"))
    assert report.tier is Tier.EXACT
    assert report["volume_closed_vs_numeric_pct"].value < 0.05


def test_t050_is_flagged_as_outside_the_isovolume_family() -> None:
    """Spec §2.3 warning must reach the report, not just the YAML."""
    report = geo.geometry_report(geo.from_library("T050"))
    assert any("OUTSIDE the iso-volume family" in n for n in report.notes)
