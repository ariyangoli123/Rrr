"""Loaders for the YAML data layer, and the honest core: :func:`open_questions`.

Spec §13: *"no magic numbers in the code. All of them in YAML."* Every empirical
constant this package uses is read from :mod:`esrsim.data`, never inlined.

Spec §11: the unknowns register must be read and *"the list of unknowns involved
printed in every report"*.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .tiers import Result, ResultSet, Tier

__all__ = [
    "DATA_ROOT",
    "load_yaml",
    "tube_library",
    "fluid_library",
    "unknowns",
    "missing_data",
    "measured",
    "Unknown",
    "MissingDatum",
    "open_questions",
    "unknowns_for",
    "missing_for",
    "mixing_validation_gap",
]

DATA_ROOT = Path(str(resources.files("esrsim") / "data"))


@functools.lru_cache(maxsize=None)
def load_yaml(relative_path: str) -> dict[str, Any]:
    """Read one YAML file from the package data directory. Cached, read-only."""
    path = DATA_ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"data file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return loaded


def tube_library() -> dict[str, Any]:
    """The six-tube library of spec §2.3 / addendum §A."""
    return load_yaml("tubes.yaml")


def fluid_library() -> dict[str, Any]:
    """Fluid properties of spec §3 / addendum §C."""
    return load_yaml("fluids.yaml")


def measured(sample_id: str = "sample_001") -> dict[str, Any]:
    """Recorded observations for one sample. n = 1 for the whole project."""
    return load_yaml(f"measured/{sample_id}.yaml")


def mixing_validation_gap() -> dict[str, Any]:
    """Addendum §C. Printed verbatim by every mixing report."""
    return fluid_library()["mixing_validation_gap"]


# --------------------------------------------------------------------- unknowns


@dataclass(frozen=True, slots=True)
class Unknown:
    """One entry of the unknowns register (spec §11)."""

    id: str
    name: str
    desc: str
    status: str
    affects: tuple[str, ...]
    how_to_resolve: str
    note: str = ""
    assumed: Any = None
    range: tuple[float, float] | None = None
    evidence: Mapping[str, Any] | None = None
    reason: str = ""

    def line(self) -> str:
        assumed = f"  assumed={self.assumed!r}" if self.assumed is not None else ""
        rng = f"  range={list(self.range)}" if self.range else ""
        return f"{self.id}  {self.name:<32} {self.status:<24}{assumed}{rng}"

    def render(self) -> str:
        out = [self.line(), f"      {self.desc}"]
        if self.reason:
            out.append(f"      reason: {self.reason}")
        if self.evidence:
            out.append(f"      evidence: {dict(self.evidence)}")
        out.append(f"      RESOLVE BY: {_squash(self.how_to_resolve)}")
        if self.note:
            out.append(f"      note: {_squash(self.note)}")
        return "\n".join(out)


@dataclass(frozen=True, slots=True)
class MissingDatum:
    """One entry of the missing-data register."""

    id: str
    name: str
    what_it_is: str
    why_absent: str
    closes_with: str
    needed_by: tuple[str, ...] = ()
    expected_path: str = ""
    unknown_id: str = ""
    consequence: str = ""

    def render(self) -> str:
        out = [f"{self.id}  {self.name}", f"      {_squash(self.what_it_is)}"]
        out.append(f"      absent because: {_squash(self.why_absent)}")
        if self.needed_by:
            out.append(f"      needed by: {', '.join(self.needed_by)}")
        out.append(f"      CLOSES WITH: {_squash(self.closes_with)}")
        if self.consequence:
            out.append(f"      consequence: {_squash(self.consequence)}")
        return "\n".join(out)


def _squash(text: str) -> str:
    return " ".join(str(text).split())


@functools.lru_cache(maxsize=1)
def unknowns() -> tuple[Unknown, ...]:
    """The unknowns register, spec §11 as amended by addendum §G."""
    raw = load_yaml("unknowns.yaml")["unknowns"]
    out: list[Unknown] = []
    for item in raw:
        rng = item.get("range")
        out.append(
            Unknown(
                id=item["id"],
                name=item["name"],
                desc=_squash(item.get("desc", "")),
                status=item.get("status", "UNRESOLVED"),
                affects=tuple(item.get("affects", ())),
                how_to_resolve=item.get("how_to_resolve", ""),
                note=item.get("note", ""),
                assumed=item.get("assumed"),
                range=(float(rng[0]), float(rng[1])) if rng else None,
                evidence=item.get("evidence") or item.get("values_conflicting"),
                reason=item.get("reason", ""),
            )
        )
    return tuple(out)


@functools.lru_cache(maxsize=1)
def missing_data() -> tuple[MissingDatum, ...]:
    raw = load_yaml("missing_data.yaml")["missing"]
    return tuple(
        MissingDatum(
            id=item["id"],
            name=item["name"],
            what_it_is=item.get("what_it_is", ""),
            why_absent=item.get("why_absent", ""),
            closes_with=item.get("closes_with", ""),
            needed_by=tuple(item.get("needed_by", ())),
            expected_path=item.get("expected_path", ""),
            unknown_id=item.get("unknown_id", ""),
            consequence=item.get("consequence", ""),
        )
        for item in raw
    )


def unknowns_for(*topics: str) -> tuple[Unknown, ...]:
    """Every unknown whose ``affects`` list touches one of ``topics``.

    Used by every report to print the unknowns its own numbers ride on.
    """
    wanted = {t.lower() for t in topics}
    return tuple(
        u for u in unknowns() if any(a.lower() in wanted for a in u.affects)
    )


def missing_for(test_id: str) -> MissingDatum | None:
    """The missing-data entry that gates ``test_id``, if it is declared."""
    for m in missing_data():
        if any(test_id in n for n in m.needed_by):
            return m
    return None


def by_id(unknown_id: str) -> Unknown:
    for u in unknowns():
        if u.id == unknown_id:
            return u
    raise KeyError(unknown_id)


# ---------------------------------------------------------------- open questions


def open_questions() -> ResultSet:
    """Every unresolved question with the experiment that would close it.

    Build prompt: *"This is the program's honest core."*

    Each unknown becomes an UNKNOWN-tier :class:`~esrsim.tiers.Result`, so the register
    obeys the same tier discipline as every other output: no value, a written reason,
    and a named experiment.
    """
    results: list[Result] = []
    for u in unknowns():
        why = u.desc
        if u.reason:
            why += f" — {u.reason}"
        if u.evidence:
            why += f" — evidence: {dict(u.evidence)}"
        if u.note:
            why += f" — {_squash(u.note)}"
        results.append(
            Result.unknown(
                name=f"{u.id}:{u.name}",
                why=f"[{u.status}] {why}",
                experiment=_squash(u.how_to_resolve) or "no bench experiment can close this",
                source="unknowns.yaml",
                notes=tuple(f"affects: {a}" for a in u.affects),
            )
        )
    for m in missing_data():
        results.append(
            Result.unknown(
                name=f"{m.id}:{m.name}",
                why=f"[MISSING DATA] {_squash(m.what_it_is)} — {_squash(m.why_absent)}",
                experiment=_squash(m.closes_with),
                source="missing_data.yaml",
                notes=tuple(f"needed by: {n}" for n in m.needed_by),
            )
        )
    return ResultSet(
        title="OPEN QUESTIONS — nothing below is answered by this program",
        results=tuple(results),
        notes=(
            "The whole experimental dataset is n = 1.",
            "Two material functions of whole blood, Py(phi) and R(phi), have never been "
            "measured anywhere; no first-principles prediction is possible (spec §0).",
        ),
    )


def unknowns_block(*topics: str) -> str:
    """Rendered unknowns section for a report footer."""
    items = unknowns_for(*topics) if topics else unknowns()
    if not items:
        return ""
    lines = ["", "UNKNOWNS THIS RESULT RIDES ON", "-" * 72]
    lines += [u.render() for u in items]
    return "\n".join(lines)


def assert_no_six_millimetre(value: float, context: str = "") -> None:
    """Refuse the 6 mm acceptance criterion wherever it appears.

    Build prompt: *"Acceptance criterion is 5 mm (ICSH 2011, Jou et al.). If 6 mm
    appears anywhere, refuse and explain that it belongs to EN ISO 13079:2011 Annex B,
    tube contamination testing."*
    """
    if abs(float(value) - 6.0) < 1e-9:
        raise ValueError(
            f"6 mm is not a method-comparison acceptance criterion{': ' + context if context else ''}. "
            "The ICSH 2011 review (Jou et al., Int J Lab Hematol 2011;33(2):125-132) sets "
            "95 percent of differences within 5 mm. The 6 mm figure belongs to "
            "EN ISO 13079:2011 Annex B, which is a tube contamination test, not a "
            "comparison of methods. Use 5.0."
        )


def tier_of(spec: Mapping[str, Any] | Sequence[Any] | str) -> Tier:
    """Read a ``tier:`` field out of a YAML fragment, defaulting to ESTIMATED."""
    if isinstance(spec, Mapping):
        return Tier[str(spec.get("tier", "ESTIMATED")).upper()]
    return Tier[str(spec).upper()]
