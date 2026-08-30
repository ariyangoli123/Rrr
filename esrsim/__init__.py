"""esrsim — design-analysis toolkit for an accelerated ESR device.

What this package is NOT
------------------------
It is **not** a first-principles simulator of blood sedimentation and it must never be
used as one. Whole-blood sedimentation is gel collapse. The two material functions that
would be needed to model it — compressive yield stress ``Py(phi)`` and hydraulic
resistance ``R(phi)`` — have not been measured for whole blood anywhere in the world
literature (spec §0, §6; unknown U05).

So this package:

* does not predict the ESR of an unknown sample;
* does not run two-phase CFD of mixing;
* does not replace experimental calibration.

What it does:

* solves the geometry exactly, from first principles;
* reproduces the kinetics with a phenomenological model calibrated on **n = 1 sample**;
* evaluates capillary and mixing criteria as threshold tests, not simulations;
* keeps every unknown parameter explicit and traceable.

Every public function returns a :class:`~esrsim.tiers.Result` carrying one of
``EXACT``, ``CALIBRATED``, ``EXTRAPOLATED``, ``ESTIMATED``, ``HYPOTHESIS``,
``RESEARCH_ONLY`` or ``UNKNOWN``. An ``UNKNOWN`` result carries no number at all — only
the reason and the experiment that would close it.
"""

from __future__ import annotations

from .tiers import Result, ResultSet, Tier
from .registry import open_questions, unknowns, missing_data

__version__ = "1.1.0"

__all__ = [
    "Result",
    "ResultSet",
    "Tier",
    "open_questions",
    "unknowns",
    "missing_data",
    "__version__",
]
