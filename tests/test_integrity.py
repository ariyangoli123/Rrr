"""Integrity tests — ESR_SIMULATOR_SPEC.md §10 tests/test_integrity.py.

These are the tests that make the tier discipline structural rather than a convention
someone has to remember. If this file passes, no public function in the package can
hand back an untagged number, an UNKNOWN cannot carry a value, and a refuted hypothesis
cannot re-enter a decision path.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import typing
from types import ModuleType

import pytest

import esrsim
from esrsim import registry
from esrsim.core import capillary as cap
from esrsim.core import continuum as cont
from esrsim.core import geometry as geo
from esrsim.tiers import Result, ResultSet, Tier, UntaggedValueError, weakest


def _all_modules() -> list[ModuleType]:
    mods = [esrsim, esrsim.tiers, esrsim.units, esrsim.registry]
    for info in pkgutil.walk_packages(esrsim.__path__, prefix="esrsim."):
        mods.append(importlib.import_module(info.name))
    return mods


#: Functions that legitimately do not return a Result: constructors, loaders, renderers
#: and the CLI. Each is listed by name so that adding a new untagged public function is
#: a deliberate act with a test change attached, not an accident.
_EXEMPT: dict[str, set[str]] = {
    "esrsim.units": {"*"},                      # pure unit conversions and guards
    "esrsim.cli": {"*"},                        # argparse plumbing; prints tiers
    "esrsim.report": {"full_report", "render_text", "render_html"},
    "esrsim.registry": {
        "load_yaml", "tube_library", "fluid_library", "measured", "unknowns",
        "missing_data", "unknowns_for", "missing_for", "by_id", "unknowns_block",
        "assert_no_six_millimetre", "tier_of", "mixing_validation_gap",
    },
    "esrsim.tiers": {"*"},                      # defines the mechanism itself
    # The API layer serialises Result objects to JSON, so its handlers return dicts by
    # construction. The tier is not lost — it travels inside the payload — and the real
    # guarantee for this layer is enforced at the wire by
    # tests/test_server.py::test_every_value_reaching_the_browser_has_a_tier, which
    # test_api_layer_has_a_wire_level_tier_check below asserts still exists.
    "esrsim.server": {"*"},
    "esrsim.ui": {"*"},                         # one module-level HTML string
    "esrsim.core.geometry": {"from_library", "list_tubes"},
    "esrsim.core.fluid": {"load_fluid", "list_fluids"},
    "esrsim.core.kinetics": {"constants", "logistic", "descent", "height_at"},
    "esrsim.core.readout": {"detect_non_monotonic", "accept_readout",
                            "saturation_check", "esr_error_from_level_shift",
                            "area_ratio"},
    "esrsim.core.benchmark": {"E_plain", "tilt_warning"},
    "esrsim.core.continuum": {"Py", "R", "sedimentation_rayleigh",
                              "clear_layer_thickness", "kynch_flux"},
    "esrsim.design.explorer": {"solve_theta_for_volume", "design_point", "sweep",
                               "compare", "render_sweep"},
    "esrsim.design.export": {"stl_parameters", "to_json"},
    "esrsim.design.rules": {"evaluate_rules"},
    "esrsim.calibration.ingest": {"read_csv"},
    "esrsim.calibration.validate": {
        "passing_bablok", "bland_altman", "icsh_2011_check",
        "icsh_2017_design_check", "feasibility_check", "validation_report",
    },
}


def _is_tagged_annotation(annotation: object) -> bool:
    """True if the annotation is Result/ResultSet-shaped."""
    if annotation in (Result, ResultSet):
        return True
    text = str(annotation)
    return "Result" in text


def test_every_output_has_provenance_label() -> None:
    """Spec §10 and the build prompt: no public function returns an untagged value.

    Checked statically over the whole package: every public function either returns a
    Result/ResultSet, or is on the small exemption list above (loaders, renderers,
    constructors and the CLI), which is itself part of this test.
    """
    offenders: list[str] = []
    for module in _all_modules():
        exempt = _EXEMPT.get(module.__name__, set())
        if "*" in exempt:
            continue
        for name, fn in vars(module).items():
            if name.startswith("_") or not inspect.isfunction(fn):
                continue
            if fn.__module__ != module.__name__ or name in exempt:
                continue
            hints = typing.get_type_hints(fn) if fn.__annotations__ else {}
            ret = hints.get("return", inspect.Signature.empty)
            if ret is inspect.Signature.empty:
                offenders.append(f"{module.__name__}.{name}: no return annotation")
            elif not _is_tagged_annotation(ret):
                offenders.append(f"{module.__name__}.{name} -> {ret}")
    assert not offenders, (
        "public functions returning untagged values:\n  " + "\n  ".join(offenders)
    )


def test_api_layer_has_a_wire_level_tier_check() -> None:
    """esrsim.server is exempted above only because a stricter test covers it.

    If that wire-level test is ever deleted or renamed, the exemption becomes a hole
    and this fails, rather than the hole opening silently.
    """
    from pathlib import Path

    source = Path(__file__).with_name("test_server.py").read_text(encoding="utf-8")
    assert "def test_every_value_reaching_the_browser_has_a_tier" in source
    assert 'assert result["tier"]' in source


def test_exempt_functions_still_return_tagged_values_where_they_claim_to() -> None:
    """The exemption list is for shape, not for licence: the exempt functions that do
    return Results must still return tagged ones."""
    samples = [
        geo.range_ceiling(geo.from_library("T090"), 0.45),
        cont.Py(0.45),
        cont.sedimentation_rayleigh(geo.from_library("T090")),
    ]
    for result in samples:
        assert isinstance(result, Result)
        assert isinstance(result.tier, Tier)


def test_a_result_cannot_be_built_without_a_tier() -> None:
    """The default tier is UNKNOWN, and UNKNOWN refuses to carry a value."""
    with pytest.raises(UntaggedValueError):
        Result(name="sneaky", value=42.0)


def test_unknown_never_returns_a_number() -> None:
    """Build prompt: 'UNKNOWN returns None plus a written explanation'."""
    with pytest.raises(UntaggedValueError, match="must not carry a value"):
        Result(name="x", value=1.0, tier=Tier.UNKNOWN,
               why_unknown="w", experiment="e")


def test_unknown_requires_a_reason_and_an_experiment() -> None:
    with pytest.raises(UntaggedValueError, match="why_unknown and experiment"):
        Result(name="x", value=None, tier=Tier.UNKNOWN)


def test_non_unknown_tier_cannot_carry_none() -> None:
    with pytest.raises(UntaggedValueError, match="use Result.unknown"):
        Result(name="x", value=None, tier=Tier.EXACT)


def test_non_finite_values_are_refused() -> None:
    with pytest.raises(UntaggedValueError, match="non-finite"):
        Result.exact("x", float("nan"))


def test_tiers_propagate_to_the_weakest() -> None:
    """Build prompt: 'any composite result takes the weakest tier of its inputs'."""
    exact = Result.exact("a", 1.0)
    estimated = Result.estimated("b", 2.0)
    hypothesis = Result.hypothesis("c", 3.0)
    assert weakest([exact, estimated]) is Tier.ESTIMATED
    assert weakest([exact, estimated, hypothesis]) is Tier.HYPOTHESIS
    composite = exact.derive("d", 6.0, others=(estimated, hypothesis))
    assert composite.tier is Tier.HYPOTHESIS


def test_an_unknown_input_makes_the_whole_composite_unknown() -> None:
    known = Result.exact("a", 1.0)
    unknown = Result.unknown("b", why="not measured", experiment="measure it")
    composite = known.derive("c", 2.0, others=(unknown,))
    assert composite.tier is Tier.UNKNOWN
    assert composite.value is None
    assert "measure it" in composite.experiment


def test_flags_propagate_through_composites() -> None:
    flagged = Result.calibrated("a", 1.0, flags=("COLLINEARITY_WARNING",))
    composite = flagged.derive("b", 2.0, others=(Result.exact("c", 1.0),))
    assert "COLLINEARITY_WARNING" in composite.flags


def test_calibrated_outside_range_is_retagged_extrapolated() -> None:
    """Build prompt: 'automatically re-tagged EXTRAPOLATED and carries a warning'."""
    r = Result.calibrated("x", 1.0, fitted_range={"theta_deg": (10.0, 16.0)})
    assert r.enforce_range(theta_deg=12.0).tier is Tier.CALIBRATED
    outside = r.enforce_range(theta_deg=25.0)
    assert outside.tier is Tier.EXTRAPOLATED
    assert "EXTRAPOLATION_UNSAFE" in outside.flags
    assert any("25" in n for n in outside.notes)


def test_tier_ladder_order() -> None:
    """Weaker claims must sort later, since propagation is a max()."""
    assert (
        Tier.EXACT < Tier.CALIBRATED < Tier.EXTRAPOLATED < Tier.ESTIMATED
        < Tier.HYPOTHESIS < Tier.RESEARCH_ONLY < Tier.UNKNOWN
    )


# ------------------------------------------------------------ registered unknowns


def test_unknown_material_functions_are_registered() -> None:
    """Spec §10. Py(phi) and R(phi) must be registered and must never return a number."""
    assert set(cont.UNKNOWN_MATERIAL_FUNCTIONS) == {"Py", "R"}
    for symbol, fn in cont.UNKNOWN_MATERIAL_FUNCTIONS.items():
        result = fn(0.45)
        assert result.tier is Tier.UNKNOWN
        assert result.value is None
        assert "never been measured" in result.why_unknown.lower() \
            or "not been measured" in result.why_unknown.lower()
        assert result.experiment


def test_continuum_results_are_research_only() -> None:
    """Spec §6: 'do not use in the engineering decision path'."""
    result = cont.sedimentation_rayleigh(geo.from_library("T090"))
    assert result.tier is Tier.RESEARCH_ONLY
    assert "RESEARCH_ONLY" in result.flags
    assert any("RESEARCH ONLY" in n for n in result.notes)


def test_kynch_refuses_to_borrow_a_richardson_zaki_exponent() -> None:
    assert cont.kynch_flux(0.45, 0.22).tier is Tier.UNKNOWN
    assert cont.kynch_flux(0.45, 0.22, n=4.65).tier is Tier.RESEARCH_ONLY


def test_refuted_hypotheses_are_not_used_in_decisions() -> None:
    """Spec §10. The three refuted hypotheses must not appear in any decision path.

    Checked two ways: they return UNKNOWN when asked about directly, and no decision
    function anywhere in the package mentions them in its source.
    """
    for name in cap.REFUTED_HYPOTHESES:
        note = cap.refuted_hypothesis_note(name)
        assert note.tier is Tier.UNKNOWN
        assert "REFUTED_HYPOTHESIS" in note.flags

    decision_sources = []
    for module in _all_modules():
        if module.__name__ in ("esrsim.core.capillary",):
            continue      # the registry of refuted hypotheses lives here
        try:
            decision_sources.append((module.__name__, inspect.getsource(module)))
        except (OSError, TypeError):
            continue
    for mod_name, source in decision_sources:
        for refuted in ("eotvos", "Eotvos", "Eötvös"):
            assert refuted not in source, (
                f"{mod_name} mentions the refuted Eotvos bubble criterion"
            )


def test_mixing_never_returns_a_fitted_threshold() -> None:
    """Spec §F: 'with four points and entangled variables NO threshold may be fitted'."""
    for clearance in (0.69, 0.72, 0.77):
        verdict = cap.mixing_criterion(cap.MixingGeometry(True, clearance, 1.0))
        assert verdict["mixing_passes"].tier is Tier.UNKNOWN, clearance


def test_missing_experimental_data_is_declared() -> None:
    """Every data-gated test must have a registered gap that names what closes it.

    This test does not skip. If a gated test loses its register entry, the gate would
    silently start failing tests instead of skipping them, and this catches that.
    """
    gated = {
        "tests/test_kinetics.py::test_sample1_heights_at_fixed_times",
        "tests/test_kinetics.py::test_range_consumption_89_to_91_percent",
        "tests/test_kinetics.py::test_haze_spread_is_12_percent",
        "tests/test_benchmark.py::test_plain_tube_E_is_measured_not_predicted",
    }
    declared = {n for m in registry.missing_data() for n in m.needed_by}
    assert gated <= declared, f"undeclared gated tests: {gated - declared}"
    for datum in registry.missing_data():
        assert datum.closes_with.strip(), f"{datum.id} declares no way to close it"
        assert datum.why_absent.strip(), f"{datum.id} does not say why it is absent"


def test_every_unknown_names_the_experiment_that_closes_it() -> None:
    """Build prompt: open_questions() must print 'the specific experiment' for each."""
    for u in registry.unknowns():
        assert u.how_to_resolve.strip(), f"{u.id} has no resolution path"
        assert u.affects or u.status == "NOT_MEASURED_WORLDWIDE", u.id


def test_open_questions_is_all_unknown_and_carries_experiments() -> None:
    questions = registry.open_questions()
    assert questions.tier is Tier.UNKNOWN
    assert len(questions) >= 12
    for q in questions:
        assert q.value is None
        assert q.why_unknown and q.experiment


def test_open_questions_states_n_equals_1() -> None:
    assert any("n = 1" in n for n in registry.open_questions().notes)


# ------------------------------------------------------------------ six millimetre


def test_six_millimetre_is_refused_everywhere() -> None:
    """Build prompt: 'If 6 mm appears anywhere, refuse and explain'."""
    with pytest.raises(ValueError, match="EN ISO 13079"):
        registry.assert_no_six_millimetre(6.0)
    registry.assert_no_six_millimetre(5.0)      # must not raise


def test_no_module_hardcodes_six_as_an_acceptance_limit() -> None:
    from esrsim.calibration import validate as val

    assert val.ICSH_2011_LIMIT_MM == 5.0
    source = inspect.getsource(val)
    assert "6 mm is not" not in source or "EN ISO" in source


# ------------------------------------------------------------------ no quiet mode


def test_rendering_always_includes_the_tier() -> None:
    """Build prompt: 'Every report, plot and CLI output shows tiers. No exceptions,
    no quiet mode.'"""
    for result in geo.geometry_report(geo.from_library("T090")):
        assert result.tier.name in result.line()


def test_cli_has_no_quiet_or_brief_flag() -> None:
    from esrsim.cli import build_parser

    text = build_parser().format_help()
    for banned in ("--quiet", "--brief", "--no-tiers", "--plain"):
        assert banned not in text, f"{banned} would allow tiers to be suppressed"


def test_resultset_takes_the_weakest_member_tier() -> None:
    rs = ResultSet("t", (Result.exact("a", 1.0), Result.hypothesis("b", 2.0)))
    assert rs.tier is Tier.HYPOTHESIS


def test_result_serialises_its_tier() -> None:
    d = Result.calibrated("x", 1.0, "mm").to_dict()
    assert d["tier"] == "CALIBRATED"


def test_no_magic_numbers_the_yaml_is_the_source() -> None:
    """Spec §13: 'no magic numbers in the code. All of them in YAML.'

    Checked over the AST rather than the source text, so a constant quoted in a
    docstring (where it belongs, as documentation) does not count as hardcoded. What
    must not appear is a numeric *literal* in executable code: changing the YAML has to
    change the computation, not just the prose.
    """
    import ast

    from esrsim.core import kinetics as kin

    consts = kin.constants()
    calibrated_values = {
        consts["enhancement"]["a_empirical"]["value"],
        consts["enhancement"]["lambda_eff"]["value"],
        consts["lag"]["law"]["intercept"],
        consts["lag"]["law"]["slope"],
        consts["lag"]["readable_height"]["value"],
        consts["enhancement"]["collinearity"]["pearson_r"],
    }
    assert 11.57 in calibrated_values and 12.0 in calibrated_values

    # Float literals only, and matched by identity of type: the calibrated constants
    # are all floats, whereas an incidental integer such as a minimum sample count is
    # structural rather than empirical and is not what spec §13 is about.
    tree = ast.parse(inspect.getsource(kin))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) is float
    }
    hardcoded = literals & {float(v) for v in calibrated_values}
    assert not hardcoded, (
        f"calibrated constants appear as literals in kinetics.py: {hardcoded}. "
        "They belong in calibration.yaml only (spec §13)."
    )


def test_every_calibrated_constant_carries_a_source_and_range() -> None:
    """Build prompt: 'Every empirical constant carries its source and fitted range'."""
    from esrsim.core import kinetics as kin

    enh = kin.constants()["enhancement"]
    for key in ("a_empirical", "lambda_eff"):
        assert enh[key]["source"], key
        assert enh[key]["fitted_range"], key
        assert enh[key]["caveat"], key
