"""Ingest conventions — build prompt 'Conventions to enforce at the data layer'."""

from __future__ import annotations

from pathlib import Path

import pytest

from esrsim.calibration import ingest as ing

GOOD_META = """# hematocrit: 0.45
# temperature_c: 22.5
# draw_time: 2026-08-30T09:15
# operator: MM
# tube_id: T090
# sample_age_min: 35
# rest_before_mixing_s: 150
# fill_method: volumetric pipette 2.000 mL
"""


def write(tmp_path: Path, meta: str, rows: str, name: str = "run.csv") -> Path:
    p = tmp_path / name
    p.write_text(meta + "t_s,height_mm\n" + rows, encoding="utf-8")
    return p


def test_accepts_a_well_formed_run(tmp_path: Path) -> None:
    p = write(tmp_path, GOOD_META, "0,0.0\n60,1.2\n120,3.4\n300,9.1\n")
    run = ing.read_csv(p)
    assert len(run.laps) == 4
    assert run.metadata.hematocrit == 0.45
    assert run.times_min[-1] == pytest.approx(5.0)


@pytest.mark.parametrize("field", ing.MANDATORY_FIELDS)
def test_rejects_on_any_missing_mandatory_field(tmp_path: Path, field: str) -> None:
    """Build prompt: 'Mandatory fields, reject on absence'."""
    meta = "\n".join(
        line for line in GOOD_META.strip().splitlines()
        if not line.startswith(f"# {field}:")
    ) + "\n"
    p = write(tmp_path, meta, "0,0.0\n60,1.2\n")
    with pytest.raises(ing.IngestError, match=field):
        ing.read_csv(p)


def test_rest_before_mixing_is_mandatory(tmp_path: Path) -> None:
    """Addendum §C singles this field out: never controlled, mandatory from now on."""
    assert "rest_before_mixing_s" in ing.MANDATORY_FIELDS


def test_empty_field_is_not_measured_never_zero(tmp_path: Path) -> None:
    """Build prompt: 'Empty means not measured. Empty never means zero.'"""
    meta = GOOD_META.replace("# hematocrit: 0.45", "# hematocrit: ")
    p = write(tmp_path, meta, "0,0.0\n60,1.2\n")
    with pytest.raises(ing.IngestError, match="NOT MEASURED"):
        ing.read_csv(p)


def test_empty_height_is_dropped_not_zeroed(tmp_path: Path) -> None:
    p = write(tmp_path, GOOD_META, "0,0.0\n60,\n120,3.4\n300,9.1\n")
    run = ing.read_csv(p)
    assert len(run.laps) == 3
    assert 0.0 not in [lap.height_mm for lap in run.laps[1:]]
    assert any("NOT MEASURED" in why for _t, why in run.rejected_laps)


def test_rejects_laps_under_5_seconds(tmp_path: Path) -> None:
    """Build prompt: 'Reject lap entries under 5 seconds (phantom double-taps).'"""
    p = write(tmp_path, GOOD_META, "0,0.0\n60,1.2\n62,1.2\n120,3.4\n300,9.1\n")
    run = ing.read_csv(p)
    assert len(run.laps) == 4
    assert any("phantom" in why for _t, why in run.rejected_laps)


def test_rejects_sample_older_than_240_minutes(tmp_path: Path) -> None:
    """Addendum §C: 'REJECTED, not warned about'."""
    meta = GOOD_META.replace("# sample_age_min: 35", "# sample_age_min: 300")
    p = write(tmp_path, meta, "0,0.0\n60,1.2\n")
    with pytest.raises(ing.IngestError) as excinfo:
        ing.read_csv(p)
    assert "240" in str(excinfo.value)
    assert "REJECTED" in str(excinfo.value)


def test_accepts_exactly_240_minutes(tmp_path: Path) -> None:
    meta = GOOD_META.replace("# sample_age_min: 35", "# sample_age_min: 240")
    p = write(tmp_path, meta, "0,0.0\n60,1.2\n")
    assert ing.read_csv(p).metadata.sample_age_min == 240.0


def test_rejects_a_trace_that_starts_at_the_readable_moment(tmp_path: Path) -> None:
    """Build prompt: 't = 0 is when the tube is placed, not when the boundary becomes
    readable.'"""
    p = write(tmp_path, GOOD_META, "480,4.0\n540,5.1\n600,6.2\n")
    with pytest.raises(ing.IngestError, match="TUBE PLACEMENT"):
        ing.read_csv(p)


def test_rejects_non_increasing_time(tmp_path: Path) -> None:
    p = write(tmp_path, GOOD_META, "0,0.0\n120,3.4\n60,1.2\n")
    with pytest.raises(ing.IngestError, match="must increase"):
        ing.read_csv(p)


def test_rejects_hematocrit_given_as_a_percentage(tmp_path: Path) -> None:
    meta = GOOD_META.replace("# hematocrit: 0.45", "# hematocrit: 45")
    p = write(tmp_path, meta, "0,0.0\n60,1.2\n")
    with pytest.raises(ing.IngestError):
        ing.read_csv(p)


def test_rejects_missing_columns(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text(GOOD_META + "time,height\n0,0\n", encoding="utf-8")
    with pytest.raises(ing.IngestError, match="t_s"):
        ing.read_csv(p)


def test_ingest_report_states_no_smoothing(tmp_path: Path) -> None:
    p = write(tmp_path, GOOD_META, "0,0.0\n60,1.2\n120,3.4\n300,9.1\n")
    report = ing.ingest_report(ing.read_csv(p))
    assert any("no smoothing" in n for n in report.notes)
    assert report["laps_accepted"].value == 4
