"""CLI and report tests.

Build prompt: *"Every report, plot and CLI output shows tiers. No exceptions, no quiet
mode."* These tests hold that line at the output boundary, where it is easiest to lose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from esrsim import cli, report
from esrsim.core import geometry as geo
from esrsim.tiers import Tier

ALL_TIER_NAMES = {t.name for t in Tier}


def run(capsys, *argv: str) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


# ------------------------------------------------------------------------- CLI


@pytest.mark.parametrize(
    "argv",
    [
        ("geometry", "T070", "--report"),
        ("geometry", "T060", "--step", "0.3", "--counterbore", "7.0", "--shift", "2.0"),
        ("kinetics", "--tube", "T090", "--esr", "20"),
        ("kinetics", "--decisive"),
        ("benchmark", "--tube", "T060"),
        ("rules", "T070"),
        ("export", "T070"),
        ("unknowns"),
        ("validate", "--feasibility", "--tube", "T090"),
    ],
)
def test_cli_subcommands_run_and_show_tiers(capsys, argv) -> None:
    argv = (argv,) if isinstance(argv, str) else argv
    out = run(capsys, *argv)
    assert out.strip()
    assert any(name in out for name in ALL_TIER_NAMES), argv


def test_cli_geometry_labels_the_generation(capsys) -> None:
    assert "GENERATION_A" in run(capsys, "geometry", "T070", "--generation", "A")
    assert "GENERATION_B" in run(capsys, "geometry", "T070", "--generation", "B")


def test_cli_unknowns_names_experiments(capsys) -> None:
    out = run(capsys, "unknowns")
    assert "resolve by:" in out
    assert "U05" in out and "M04" in out


def test_cli_readout_delta_h_is_refused(capsys) -> None:
    out = run(capsys, "readout", "--tube", "T090", "--mode", "delta_h")
    assert "NON_MONOTONIC" in out
    assert "UNKNOWN" in out


def test_cli_readout_time_to_threshold_is_accepted(capsys) -> None:
    out = run(capsys, "readout", "--tube", "T090", "--mode", "time_to_threshold")
    assert "readout_accepted[TIME_TO_THRESHOLD]" in out


def test_cli_explore_compare(capsys) -> None:
    out = run(capsys, "explore", "--compare", "T090", "T060")
    assert "T090" in out and "T060" in out
    assert "NOT a pass" in out


def test_cli_explore_sweep(capsys) -> None:
    out = run(capsys, "explore", "--sweep", "theta", "--range", "10", "16",
              "--steps", "3")
    assert "theta=10" in out


def test_cli_report_text(capsys) -> None:
    out = run(capsys, "report", "--tube", "T070")
    assert "WHAT THIS TOOL DOES NOT DO" in out
    assert "UNKNOWNS THESE NUMBERS RIDE ON" in out
    assert "n = 1" in out


def test_cli_report_open_questions(capsys) -> None:
    out = run(capsys, "report", "--tube", "T070", "--open-questions")
    assert "OPEN QUESTIONS" in out


def test_cli_report_writes_html_and_json(tmp_path: Path, capsys) -> None:
    html_path, json_path = tmp_path / "r.html", tmp_path / "r.json"
    run(capsys, "report", "--tube", "T090", "--html", str(html_path),
        "--json", str(json_path))
    assert html_path.is_file() and json_path.is_file()
    import json

    blocks = json.loads(json_path.read_text(encoding="utf-8"))
    assert blocks and all("tier" in b for b in blocks)


def test_cli_export_json_has_only_driving_dimensions(capsys) -> None:
    import json

    out = run(capsys, "export", "T070", "--json")
    params = json.loads(out)
    assert "d_outer_at_base" not in params
    assert "d_outer_at_bloodline" in params


def test_cli_validate_reads_a_csv(tmp_path: Path, capsys) -> None:
    p = tmp_path / "results.csv"
    rows = "\n".join(f"{v},{v + 0.4}" for v in range(2, 121, 2))
    p.write_text("reference,device\n" + rows + "\n", encoding="utf-8")
    out = run(capsys, "validate", "--data", str(p))
    assert "PASSING-BABLOK" in out and "BLAND-ALTMAN" in out
    assert "acceptance_limit" in out


def test_cli_validate_drops_empty_pairs(tmp_path: Path, capsys) -> None:
    """Empty means NOT MEASURED; the pair is dropped, never zeroed."""
    p = tmp_path / "results.csv"
    p.write_text("reference,device\n10,10.5\n20,\n30,31\n", encoding="utf-8")
    out = run(capsys, "validate", "--data", str(p))
    assert "n = 2" in out


def test_cli_validate_rejects_a_bad_header(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="reference"):
        cli.main(["validate", "--data", str(p)])


def test_cli_version(capsys) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0


# ---------------------------------------------------------------------- report


def test_full_report_covers_every_module() -> None:
    blocks = report.full_report(geo.from_library("T070"), include_continuum=True)
    titles = " ".join(b.title for b in blocks)
    for word in ("GEOMETRY", "CAPILLARY", "MIXING", "KINETICS", "BENCHMARK",
                 "DESIGN RULES", "DRAWING SHEET", "CONTINUUM"):
        assert word in titles, word


def test_text_report_prints_the_unknowns_it_rides_on() -> None:
    """Spec §11: 'print, in EVERY report, the list of unknowns involved'."""
    blocks = report.full_report(geo.from_library("T070"))
    text = report.render_text(blocks)
    for unknown_id in ("U01", "U02", "U05"):
        assert unknown_id in text, unknown_id
    assert "RESOLVE BY:" in text
    assert "MISSING DATA" in text


def test_text_report_opens_with_what_it_cannot_do() -> None:
    text = report.render_text(report.full_report(geo.from_library("T090")))
    head = text[: text.index("GEOMETRY")]
    assert "does not predict" in head
    assert "n = 1" in head


def test_html_report_is_standalone_and_offline() -> None:
    html = report.render_html(report.full_report(geo.from_library("T070")))
    assert html.startswith("<!doctype html>")
    for external in ("http://", "https://", "<script"):
        assert external not in html, f"{external} would break offline use"


def test_html_report_shows_every_tier_and_the_legend() -> None:
    html = report.render_html(report.full_report(geo.from_library("T070")))
    assert "Provenance tiers" in html
    for tier in ("EXACT", "CALIBRATED", "UNKNOWN", "HYPOTHESIS"):
        assert tier in html, tier


def test_html_report_charts_use_linear_real_axes() -> None:
    """Spec §12: the time axis must be linear and real, not categorical."""
    cone = geo.from_library("T090")
    from esrsim.core.kinetics import descent

    times = [float(t) for t in range(0, 61, 2)]
    run_ = descent(cone, 20.0, 0.45, t_max_min=60.0)
    html = report.render_html(
        report.full_report(cone),
        curves=[("ESR 20", times, [run_.height(t) for t in times])],
        area_profile=([0.0, 25.0, 50.0], [cone.area_at_height(h)
                                          for h in (0.0, 25.0, 50.0)]),
    )
    assert "<svg" in html and "polyline" in html
    assert "time (min)" in html
    # Tick labels must be real numbers spanning the data, not category names.
    assert ">60<" in html or ">60.0<" in html or ">48<" in html


def test_html_report_lists_unknowns_with_their_experiments() -> None:
    html = report.render_html(report.full_report(geo.from_library("T070")))
    assert "Resolve by:" in html
    assert "Closes with:" in html
    assert "M04" in html


def test_html_escapes_untrusted_text() -> None:
    from esrsim.tiers import Result, ResultSet

    block = ResultSet("T", (Result.exact("<script>x</script>", 1.0),))
    html = report.render_html([block])
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
