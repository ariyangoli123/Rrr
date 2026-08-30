"""Data ingest — the conventions enforced at the boundary, not by convention.

Every rule below is a hard rule from the build prompt and spec §8.3. They are enforced
by rejection, not by warning, because each one silently corrupts a calibration if it
slips through:

* ``t = 0`` is **tube placement**, not the moment the boundary becomes readable. A
  trace whose first row is the readable moment has the whole haze phase deleted from it.
* **Empty means not measured. Empty never means zero.** A blank cell read as 0.0 mm is
  a measurement that never happened.
* **Lap entries under 5 seconds are rejected** as phantom double-taps on the
  chronometer.
* **Mandatory fields**, rejected on absence: haematocrit, temperature, draw time,
  operator, ``rest_before_mixing_s``, fill method, tube id, sample age.
* **Sample age over 240 minutes is rejected**, not warned about (addendum §C).
* **No smoothing.** Ever.

References
----------
ESR_SIMULATOR_SPEC.md §8.3, v1.1 addendum §C.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..tiers import Result, ResultSet

__all__ = [
    "MANDATORY_FIELDS",
    "MIN_LAP_SECONDS",
    "MAX_SAMPLE_AGE_MIN",
    "RunMetadata",
    "Lap",
    "Run",
    "IngestError",
    "read_csv",
    "ingest_report",
]

#: Rejected on absence (build prompt, spec §8.3).
MANDATORY_FIELDS: tuple[str, ...] = (
    "hematocrit",
    "temperature_c",
    "draw_time",
    "operator",
    "tube_id",
    "sample_age_min",
    "rest_before_mixing_s",
    "fill_method",
)

#: Laps shorter than this are phantom double-taps (build prompt).
MIN_LAP_SECONDS = 5.0

#: Blood older than this is not admissible for ESR. Rejected, not warned (addendum §C).
MAX_SAMPLE_AGE_MIN = 240.0


class IngestError(ValueError):
    """A record that must not enter a calibration."""


class RunMetadata(BaseModel):
    """Pre-analytical metadata. All fields mandatory; absence is a rejection."""

    model_config = ConfigDict(frozen=True, extra="allow")

    hematocrit: float = Field(ge=0.0, le=1.0)
    temperature_c: float
    draw_time: str
    operator: str
    tube_id: str
    sample_age_min: float = Field(ge=0.0)
    rest_before_mixing_s: float = Field(ge=0.0)
    fill_method: str

    @field_validator("draw_time", "operator", "tube_id", "fill_method")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not str(v).strip():
            raise ValueError("empty means NOT MEASURED, which is a rejection here")
        return str(v).strip()

    @field_validator("hematocrit")
    @classmethod
    def _hct_is_a_fraction(cls, v: float) -> float:
        if v > 1.0:
            raise ValueError(
                f"haematocrit {v} looks like a percentage; this package uses a volume "
                "fraction in [0, 1]"
            )
        return v

    @model_validator(mode="after")
    def _sample_age(self) -> "RunMetadata":
        if self.sample_age_min > MAX_SAMPLE_AGE_MIN:
            raise ValueError(
                f"sample age {self.sample_age_min:g} min exceeds the "
                f"{MAX_SAMPLE_AGE_MIN:g} min limit. Blood over 4 hours old is not "
                "admissible for any ESR measurement (addendum §C). This record is "
                "REJECTED, not flagged."
            )
        return self


@dataclass(frozen=True, slots=True)
class Lap:
    """One chronometer lap: seconds from tube placement, and boundary height in mm."""

    t_s: float
    height_mm: float

    @property
    def t_min(self) -> float:
        return self.t_s / 60.0


class Run(BaseModel):
    """One accepted run: metadata plus a raw, unsmoothed trace."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    metadata: RunMetadata
    laps: tuple[Lap, ...]
    rejected_laps: tuple[tuple[float, str], ...] = ()
    source_path: str = ""

    @property
    def times_min(self) -> tuple[float, ...]:
        return tuple(lap.t_min for lap in self.laps)

    @property
    def heights_mm(self) -> tuple[float, ...]:
        return tuple(lap.height_mm for lap in self.laps)


def _parse_optional_float(raw: str | None, field: str) -> float | None:
    """Empty means NOT MEASURED and returns None. It never becomes 0.0."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise IngestError(f"{field}: {raw!r} is not a number") from exc
    if not math.isfinite(value):
        raise IngestError(f"{field}: {raw!r} is not finite")
    return value


def read_csv(path: str | Path) -> Run:
    """Read one chronometer CSV, rejecting anything that would corrupt a calibration.

    Expected layout: a metadata header of ``# key: value`` comment lines, then a table
    with ``t_s`` (seconds from **tube placement**) and ``height_mm`` columns.

    Raises
    ------
    IngestError
        On a missing mandatory field, a sample older than 240 minutes, a non-monotonic
        time column, or a first lap that is not at or near t = 0 (which would mean the
        trace starts at the readable moment rather than at placement).
    """
    p = Path(path)
    if not p.is_file():
        raise IngestError(f"no such file: {p}")

    meta_raw: dict[str, str] = {}
    data_lines: list[str] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("#"):
                body = stripped.lstrip("#").strip()
                if ":" in body:
                    key, _, value = body.partition(":")
                    meta_raw[key.strip()] = value.strip()
            elif stripped:
                data_lines.append(line)

    missing = [
        f for f in MANDATORY_FIELDS
        if f not in meta_raw or not str(meta_raw[f]).strip()
    ]
    if missing:
        raise IngestError(
            f"{p.name}: mandatory field(s) absent: {', '.join(missing)}. Empty means "
            "NOT MEASURED, never zero, and a run without these cannot be calibrated "
            "against (spec §8.3). rest_before_mixing_s in particular has never been "
            "controlled in any run to date and is mandatory from now on (addendum §C)."
        )

    typed: dict[str, Any] = dict(meta_raw)
    for numeric in ("hematocrit", "temperature_c", "sample_age_min",
                    "rest_before_mixing_s"):
        value = _parse_optional_float(meta_raw.get(numeric), numeric)
        if value is None:
            raise IngestError(f"{p.name}: {numeric} is empty, which means NOT MEASURED")
        typed[numeric] = value

    try:
        metadata = RunMetadata(**typed)
    except Exception as exc:  # pydantic validation
        raise IngestError(f"{p.name}: {exc}") from exc

    reader = csv.DictReader(data_lines)
    if reader.fieldnames is None:
        raise IngestError(f"{p.name}: no data header row")
    for required in ("t_s", "height_mm"):
        if required not in reader.fieldnames:
            raise IngestError(
                f"{p.name}: missing column {required!r}; found {reader.fieldnames}"
            )

    laps: list[Lap] = []
    rejected: list[tuple[float, str]] = []
    last_t: float | None = None
    for row_no, row in enumerate(reader, start=2):
        t = _parse_optional_float(row.get("t_s"), f"row {row_no} t_s")
        h = _parse_optional_float(row.get("height_mm"), f"row {row_no} height_mm")
        if t is None:
            raise IngestError(
                f"{p.name} row {row_no}: t_s is empty. Time is never optional."
            )
        if h is None:
            # Not measured at this lap. Dropped, never coerced to zero.
            rejected.append((t, "height_mm empty: NOT MEASURED, not zero"))
            continue
        if last_t is not None:
            if t <= last_t:
                raise IngestError(
                    f"{p.name} row {row_no}: t_s {t:g} s is not after the previous lap "
                    f"({last_t:g} s); the time column must increase"
                )
            if t - last_t < MIN_LAP_SECONDS:
                rejected.append(
                    (t, f"lap {t - last_t:.2f} s < {MIN_LAP_SECONDS:g} s: phantom "
                        "double-tap")
                )
                continue
        laps.append(Lap(t_s=t, height_mm=h))
        last_t = t

    if not laps:
        raise IngestError(f"{p.name}: no usable laps after validation")

    if laps[0].t_s > 60.0:
        raise IngestError(
            f"{p.name}: the first lap is at t = {laps[0].t_s:g} s. t = 0 must be TUBE "
            "PLACEMENT, not the moment the boundary became readable (spec §8.3). A "
            "trace that starts later has the whole haze phase deleted from it."
        )

    return Run(
        metadata=metadata,
        laps=tuple(laps),
        rejected_laps=tuple(rejected),
        source_path=str(p),
    )


def ingest_report(run: Run) -> ResultSet:
    """What was accepted, what was rejected and why."""
    results = [
        Result.exact("tube_id", run.metadata.tube_id, ""),
        Result.exact("operator", run.metadata.operator, ""),
        Result.exact("hematocrit", run.metadata.hematocrit, ""),
        Result.exact("temperature", run.metadata.temperature_c, "C"),
        Result.exact("sample_age", run.metadata.sample_age_min, "min",
                     notes=(f"limit {MAX_SAMPLE_AGE_MIN:g} min (addendum §C)",)),
        Result.exact("rest_before_mixing", run.metadata.rest_before_mixing_s, "s",
                     notes=("never controlled in any run to date; the decisive fresh-"
                            "blood test needs 120-180 s (addendum §C)",)),
        Result.exact("fill_method", run.metadata.fill_method, ""),
        Result.exact("laps_accepted", len(run.laps), ""),
        Result.exact("laps_rejected", len(run.rejected_laps), ""),
        Result.exact("first_lap", run.laps[0].t_s, "s",
                     notes=("t = 0 is tube placement (spec §8.3)",)),
        Result.exact("last_lap", run.laps[-1].t_s, "s"),
    ]
    return ResultSet(
        title=f"INGEST — {Path(run.source_path).name or 'run'}",
        results=tuple(results),
        notes=tuple(
            f"rejected lap at {t:g} s: {why}" for t, why in run.rejected_laps
        ) + ("no smoothing was applied (spec §5.1, §8.3)",),
    )
