import pytest

from bloodsed.cli import main


def test_list_runs(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "westergren" in out and "inflammation" in out


def test_run_prints_a_table(capsys):
    assert main(["run", "westergren", "--hours", "1", "--cells", "150", "--quiet"]) == 0
    assert "ESR 1h" in capsys.readouterr().out


def test_run_accepts_a_spec_and_writes_files(tmp_path, capsys):
    code = main(["run", "cone:L=200,Dbot=1.2,Dtop=4", "--hours", "1", "--cells", "150",
                 "--out", str(tmp_path), "--no-plots", "--quiet"])
    assert code == 0
    names = {p.name for p in tmp_path.iterdir()}
    assert "summary.csv" in names
    assert any(n.startswith("curve_") for n in names)


def test_compare_runs_a_named_set(tmp_path, capsys):
    code = main(["compare", "tilt", "--hours", "1", "--cells", "150",
                 "--out", str(tmp_path), "--no-plots", "--quiet"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("Westergren") >= 3


def test_compare_accepts_an_explicit_list(capsys):
    assert main(["compare", "westergren,wintrobe", "--hours", "1",
                 "--cells", "150", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "Wintrobe" in out


def test_sweep_varies_the_parameter(capsys):
    assert main(["sweep", "hematocrit", "0.3,0.5", "--hours", "1",
                 "--cells", "150", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "Hct 30%" in out and "Hct 50%" in out


def test_blood_options_reach_the_model(capsys):
    main(["run", "westergren", "--hours", "1", "--cells", "150",
          "--aggregate-um", "150", "--quiet"])
    high = _esr(capsys.readouterr().out)
    main(["run", "westergren", "--hours", "1", "--cells", "150",
          "--aggregate-um", "40", "--quiet"])
    low = _esr(capsys.readouterr().out)
    assert high > low


def test_tilt_option_applies_to_every_tube(capsys):
    main(["run", "westergren", "--hours", "1", "--cells", "150", "--quiet"])
    upright = _esr(capsys.readouterr().out)
    main(["run", "westergren", "--hours", "1", "--cells", "150",
          "--tilt", "10", "--quiet"])
    tilted = _esr(capsys.readouterr().out)
    assert tilted > upright


def test_scenario_file_is_honoured(tmp_path, capsys):
    scenario = tmp_path / "s.json"
    scenario.write_text('{"blood": {"hematocrit": 0.25}, '
                        '"compare": ["westergren", "wintrobe"], '
                        '"config": {"duration_h": 1, "n_cells": 150}}')
    assert main(["compare", "--scenario", str(scenario), "--hours", "1",
                 "--cells", "150", "--quiet"]) == 0
    assert "Wintrobe" in capsys.readouterr().out


def test_a_bad_geometry_exits_with_an_error(capsys):
    assert main(["run", "banana", "--quiet"]) == 2
    assert "error:" in capsys.readouterr().err


def _esr(text: str) -> float:
    line = [l for l in text.splitlines() if "Westergren" in l][-1]
    return float(line.split()[-7])
