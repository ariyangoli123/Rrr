import csv
import math

import numpy as np
import pytest

from bloodsed.blood import BloodProperties
from bloodsed.geometry import Cylinder, get_geometry
from bloodsed.metrics import (
    format_table, katz_index, lag_time_min, max_settling_rate, summarise,
    time_to_fall, write_profile_csv, write_summary_csv, write_timeseries_csv,
)
from bloodsed.solver import SimulationConfig, simulate

BLOOD = BloodProperties()


@pytest.fixture(scope="module")
def result():
    return simulate(Cylinder(200, 2.5), BLOOD, SimulationConfig(duration_h=2, n_cells=300))


def test_the_reading_grows_with_time(result):
    assert 0 < result.esr(0.5) < result.esr(1.0) < result.esr(2.0)


def test_katz_index_follows_its_definition(result):
    assert katz_index(result) == pytest.approx(
        0.5 * (result.esr(1.0) + 0.5 * result.esr(2.0)))


def test_asking_beyond_the_simulated_time_gives_nan(result):
    assert math.isnan(result.esr(5.0))


def test_the_lag_phase_is_detected(result):
    assert 0.0 <= lag_time_min(result) < 30.0
    assert max_settling_rate(result) > 0.0


def test_time_to_fall_inverts_the_curve(result):
    minutes = time_to_fall(result, result.esr(1.0))
    assert minutes == pytest.approx(60.0, abs=1.5)
    assert math.isnan(time_to_fall(result, 1e6))


def test_summary_has_the_clinical_fields(result):
    summary = summarise(result)
    for key in ("esr_1h_mm", "esr_2h_mm", "katz_index_mm", "sediment_mm",
                "packed_cell_fraction", "mass_error", "hematocrit"):
        assert key in summary
    assert summary["mass_error"] < 1e-12
    assert 0.45 < summary["packed_cell_fraction"] <= BLOOD.max_packing


def test_table_lists_every_case(result):
    other = simulate(get_geometry("funnel"), BLOOD, SimulationConfig(duration_h=1, n_cells=200))
    table = format_table([result, other])
    assert result.label in table and other.label in table
    assert len(table.splitlines()) == 4


def test_timeseries_csv_round_trips(result, tmp_path):
    path = write_timeseries_csv(result, tmp_path / "curve.csv")
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == len(result.times)
    assert float(rows[-1]["fall_mm"]) == pytest.approx(result.fall_mm[-1], rel=1e-4)


def test_profile_csv_has_a_column_per_requested_time(result, tmp_path):
    path = write_profile_csv(result, tmp_path / "profiles.csv", times_min=[0, 60, 120])
    header = path.open().readline().strip().split(",")
    assert header[:2] == ["height_mm", "diameter_mm"]
    assert len(header) == 5


def test_summary_csv_has_one_row_per_run(result, tmp_path):
    path = write_summary_csv([result, result], tmp_path / "summary.csv")
    assert len(list(csv.DictReader(path.open()))) == 2
