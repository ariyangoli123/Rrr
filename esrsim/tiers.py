"""Provenance tiers and the result container that carries them.

This module is the structural guarantee demanded by ESR_SIMULATOR_SPEC.md §0 and by
the build prompt: *no number leaves this package without a tier attached*.

The tier ladder reconciles the two vocabularies used in the project record:

    build prompt : EXACT, CALIBRATED, HYPOTHESIS, UNKNOWN  (+ auto EXTRAPOLATED)
    spec §0/§13  : EXACT, CALIBRATED, ESTIMATED, RESEARCH_ONLY

Both are kept, ordered weakest-last, so ``max()`` over inputs is the propagation rule.

Ladder (strongest to weakest)
-----------------------------
EXACT          Derived from geometry or from a definition. No fitted parameter.
CALIBRATED     Fitted to measured data from *this* project, inside its fitted range.
EXTRAPOLATED   A CALIBRATED result evaluated outside its fitted range. Auto-assigned.
ESTIMATED      Taken from literature or assumed. Not measured here.
HYPOTHESIS     A competing model with no winner selected by evidence.
RESEARCH_ONLY  Depends on a material function nobody has measured. Paper only.
UNKNOWN        Cannot be computed. ``value is None``, always.

References
----------
ESR_SIMULATOR_SPEC.md §0 (the golden rule), §11 (unknowns register), §13 (coding rules).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Tier",
    "Result",
    "ResultSet",
    "combine_tier",
    "weakest",
    "UntaggedValueError",
]


class Tier(IntEnum):
    """Provenance tier. Higher ordinal == weaker claim. See module docstring."""

    EXACT = 0
    CALIBRATED = 1
    EXTRAPOLATED = 2
    ESTIMATED = 3
    HYPOTHESIS = 4
    RESEARCH_ONLY = 5
    UNKNOWN = 6

    @property
    def label(self) -> str:
        return self.name

    @property
    def is_numeric(self) -> bool:
        """UNKNOWN never carries a number. Everything else may."""
        return self is not Tier.UNKNOWN


# Warning flags the spec names explicitly. Plain strings so they survive YAML/JSON
# round-trips unchanged; asserted on by the test suite.
COLLINEARITY = "COLLINEARITY_WARNING"
EXTRAPOLATION_UNSAFE = "EXTRAPOLATION_UNSAFE"
UNRESOLVED_CONTRADICTION = "UNRESOLVED_CONTRADICTION"
LAG_LAW_DISPUTED = "LAG_LAW_DISPUTED"
NON_MONOTONIC = "NON_MONOTONIC"
SATURATED = "SATURATED"
RESEARCH_ONLY_FLAG = "RESEARCH_ONLY"
N_EQUALS_1 = "N_EQUALS_1"
MODEL_RECORD_MISMATCH = "MODEL_RECORD_MISMATCH"
REFUTED_HYPOTHESIS = "REFUTED_HYPOTHESIS"
MIXING_VALIDATION_GAP = "MIXING_VALIDATION_GAP"
GEN_A = "GENERATION_A"
GEN_B = "GENERATION_B"


class UntaggedValueError(TypeError):
    """Raised when a public function tries to hand back a bare number."""


@dataclass(frozen=True, slots=True)
class Result:
    """A single tagged quantity.

    Every public function in :mod:`esrsim` returns one of these (or a
    :class:`ResultSet` of them). ``value`` is ``None`` if and only if the tier is
    :attr:`Tier.UNKNOWN`.

    Attributes
    ----------
    name        Short identifier, e.g. ``"clearance_working"``.
    value       The number, or ``None`` for UNKNOWN. Millimetre-family units at the
                boundary; see :mod:`esrsim.units`.
    unit        Explicit unit string. ``""`` for dimensionless.
    tier        Provenance tier.
    source      Where the number came from: a spec section, a paper, or a fit.
    fitted_range
                For CALIBRATED results: ``{"theta_deg": (9.973, 15.970), ...}``.
                Evaluating outside it auto-retags to EXTRAPOLATED via
                :meth:`enforce_range`.
    flags       Warning strings (module-level constants above).
    notes       Free text shown in reports.
    why_unknown For UNKNOWN results: what blocks the computation.
    experiment  For UNKNOWN results: the measurement that would make it computable.
    inputs      Names of the results this one was derived from.
    """

    name: str
    value: float | int | bool | str | None
    unit: str = ""
    tier: Tier = Tier.UNKNOWN
    source: str = ""
    fitted_range: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    why_unknown: str = ""
    experiment: str = ""
    inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.tier is Tier.UNKNOWN:
            if self.value is not None:
                raise UntaggedValueError(
                    f"{self.name!r}: an UNKNOWN result must not carry a value "
                    "(spec §0: UNKNOWN never returns a number)"
                )
            if not self.why_unknown or not self.experiment:
                raise UntaggedValueError(
                    f"{self.name!r}: an UNKNOWN result needs both why_unknown and "
                    "experiment (build prompt: 'a written explanation of why it "
                    "cannot be computed and which experiment would make it computable')"
                )
        else:
            if self.value is None:
                raise UntaggedValueError(
                    f"{self.name!r}: tier {self.tier.name} carries value None; use "
                    "Result.unknown() instead"
                )
            if isinstance(self.value, float) and not math.isfinite(self.value):
                raise UntaggedValueError(
                    f"{self.name!r}: non-finite value {self.value!r}"
                )

    # ---------------------------------------------------------------- builders

    @classmethod
    def exact(cls, name: str, value: Any, unit: str = "", **kw: Any) -> "Result":
        return cls(name=name, value=value, unit=unit, tier=Tier.EXACT, **kw)

    @classmethod
    def calibrated(cls, name: str, value: Any, unit: str = "", **kw: Any) -> "Result":
        return cls(name=name, value=value, unit=unit, tier=Tier.CALIBRATED, **kw)

    @classmethod
    def estimated(cls, name: str, value: Any, unit: str = "", **kw: Any) -> "Result":
        return cls(name=name, value=value, unit=unit, tier=Tier.ESTIMATED, **kw)

    @classmethod
    def hypothesis(cls, name: str, value: Any, unit: str = "", **kw: Any) -> "Result":
        return cls(name=name, value=value, unit=unit, tier=Tier.HYPOTHESIS, **kw)

    @classmethod
    def research_only(cls, name: str, value: Any, unit: str = "", **kw: Any) -> "Result":
        kw.setdefault("flags", ())
        kw["flags"] = tuple(kw["flags"]) + (RESEARCH_ONLY_FLAG,)
        return cls(name=name, value=value, unit=unit, tier=Tier.RESEARCH_ONLY, **kw)

    @classmethod
    def unknown(cls, name: str, why: str, experiment: str, **kw: Any) -> "Result":
        """The only way to express 'no number'. See build prompt, §'must not go wrong'."""
        return cls(
            name=name,
            value=None,
            tier=Tier.UNKNOWN,
            why_unknown=why,
            experiment=experiment,
            **kw,
        )

    # ------------------------------------------------------------- derivation

    def with_flags(self, *flags: str) -> "Result":
        new = tuple(f for f in flags if f not in self.flags)
        return replace(self, flags=self.flags + new)

    def with_notes(self, *notes: str) -> "Result":
        return replace(self, notes=self.notes + tuple(notes))

    def rename(self, name: str) -> "Result":
        """Same result under a different name, e.g. when tabulating a sweep."""
        return replace(self, name=name)

    def enforce_range(self, **values: float) -> "Result":
        """Auto-retag CALIBRATED -> EXTRAPOLATED outside the fitted range.

        Build prompt: "A CALIBRATED result evaluated outside its fitted range is
        automatically re-tagged EXTRAPOLATED and carries a warning."
        """
        if self.tier is not Tier.CALIBRATED or not self.fitted_range:
            return self
        outside: list[str] = []
        for key, (lo, hi) in self.fitted_range.items():
            if key in values and not (lo <= values[key] <= hi):
                outside.append(f"{key}={values[key]:.4g} outside [{lo:.4g}, {hi:.4g}]")
        if not outside:
            return self
        return replace(
            self,
            tier=Tier.EXTRAPOLATED,
            flags=self.flags + (EXTRAPOLATION_UNSAFE,),
            notes=self.notes + tuple(f"extrapolated: {o}" for o in outside),
        )

    def derive(
        self,
        name: str,
        value: Any,
        unit: str = "",
        *,
        others: Sequence["Result"] = (),
        **kw: Any,
    ) -> "Result":
        """Build a composite result that inherits the *weakest* tier of its inputs."""
        parents = (self, *others)
        return _compose(name, value, unit, parents, **kw)

    def __hash__(self) -> int:
        # frozen=True generates a __hash__ that recurses into every field, and
        # fitted_range is a Mapping, so the generated one raises TypeError. Hash the
        # identifying fields instead, so a Result can go in a set or dict key.
        return hash((self.name, self.value, self.unit, self.tier, self.flags))

    # ------------------------------------------------------------- rendering

    def format_value(self, digits: int = 4) -> str:
        if self.value is None:
            return "—"
        if isinstance(self.value, bool):
            return "yes" if self.value else "no"
        if isinstance(self.value, float):
            return f"{self.value:.{digits}g}"
        return str(self.value)

    def line(self, digits: int = 4) -> str:
        """One-line rendering. Tier is never omitted (build prompt: 'No exceptions')."""
        unit = f" {self.unit}" if self.unit else ""
        flags = f"  [{', '.join(self.flags)}]" if self.flags else ""
        return f"{self.name:<34} {self.format_value(digits):>12}{unit:<7} " \
               f"{self.tier.name:<13}{flags}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "tier": self.tier.name,
            "source": self.source,
            "fitted_range": {k: list(v) for k, v in self.fitted_range.items()},
            "flags": list(self.flags),
            "notes": list(self.notes),
            "why_unknown": self.why_unknown,
            "experiment": self.experiment,
            "inputs": list(self.inputs),
        }


def _compose(
    name: str,
    value: Any,
    unit: str,
    parents: Sequence[Result],
    **kw: Any,
) -> Result:
    tier = weakest(parents)
    flags = tuple(dict.fromkeys(f for p in parents for f in p.flags))
    flags = tuple(dict.fromkeys(flags + tuple(kw.pop("flags", ()))))
    inputs = tuple(dict.fromkeys(p.name for p in parents)) + tuple(kw.pop("inputs", ()))
    if tier is Tier.UNKNOWN:
        blocked = [p for p in parents if p.tier is Tier.UNKNOWN]
        return Result.unknown(
            name,
            why=kw.pop("why", "depends on " + ", ".join(p.name for p in blocked)),
            experiment=kw.pop(
                "experiment",
                "; ".join(dict.fromkeys(p.experiment for p in blocked if p.experiment)),
            ),
            flags=flags,
            inputs=inputs,
            **kw,
        )
    kw.pop("why", None)
    kw.pop("experiment", None)
    return Result(
        name=name, value=value, unit=unit, tier=tier, flags=flags, inputs=inputs, **kw
    )


def weakest(results: Iterable[Result]) -> Tier:
    """Tier propagation rule: a composite is only as strong as its weakest input."""
    tiers = [r.tier for r in results]
    return max(tiers) if tiers else Tier.EXACT


def combine_tier(*results: Result) -> Tier:
    """Alias of :func:`weakest` for call sites that read better with varargs."""
    return weakest(results)


@dataclass(frozen=True, slots=True)
class ResultSet:
    """An ordered, named bundle of :class:`Result` objects.

    Its own tier is the weakest member's, so a report header can state the strength
    of the whole block at a glance.
    """

    title: str
    results: tuple[Result, ...]
    notes: tuple[str, ...] = ()

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, key: str | int) -> Result:
        if isinstance(key, int):
            return self.results[key]
        for r in self.results:
            if r.name == key:
                return r
        raise KeyError(key)

    def get(self, key: str) -> Result | None:
        try:
            return self[key]
        except KeyError:
            return None

    @property
    def tier(self) -> Tier:
        return weakest(self.results)

    @property
    def flags(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f for r in self.results for f in r.flags))

    @property
    def unknowns(self) -> tuple[Result, ...]:
        return tuple(r for r in self.results if r.tier is Tier.UNKNOWN)

    def with_notes(self, *notes: str) -> "ResultSet":
        return ResultSet(self.title, self.results, self.notes + tuple(notes))

    def extend(self, *results: Result) -> "ResultSet":
        return ResultSet(self.title, self.results + results, self.notes)

    def render(self, digits: int = 4) -> str:
        head = f"{self.title}  [{self.tier.name}]"
        lines = [head, "-" * max(len(head), 72)]
        lines += [r.line(digits) for r in self.results]
        for r in self.unknowns:
            lines.append(f"    ! {r.name}: {r.why_unknown}")
            lines.append(f"      resolve by: {r.experiment}")
        lines += [f"    note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "tier": self.tier.name,
            "notes": list(self.notes),
            "results": [r.to_dict() for r in self.results],
        }
