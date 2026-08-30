"""Shared fixtures, and the gate for tests that need data this repository lacks.

Spec §10 mandates regression tests against sample 1's raw trace. That trace is not in
the repository and the spec does not reproduce it. Rather than invent numbers — which
could only be labelled CALIBRATED, and would be a lie — those tests are gated.

The gate is deliberately strict: a test may skip **only** if its gap is declared in
``esrsim/data/missing_data.yaml`` with a `closes_with` field naming the experiment or
file that would close it. An undeclared gap fails. Committing the referenced data turns
the skip into a hard assertion with no code change.
"""

from __future__ import annotations

from typing import Any

import pytest

from esrsim.registry import measured, missing_data


def _walk(data: Any, path: str) -> Any:
    """Fetch ``a.b.c`` out of nested mappings, returning None if any hop is missing."""
    node = data
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


@pytest.fixture(scope="session")
def sample_001() -> dict:
    return measured("sample_001")


@pytest.fixture(scope="session")
def declared_gaps() -> dict[str, Any]:
    return {m.id: m for m in missing_data()}


def require_measured(sample: dict, path: str, test_id: str) -> Any:
    """Return the recorded value at ``path``, or skip with the registered reason.

    Fails — rather than skips — if the value is absent and the gap is not declared in
    the missing-data register. Silence is not an option in either direction.
    """
    value = _walk(sample, path)
    if value is not None:
        return value

    for datum in missing_data():
        if any(test_id in needed for needed in datum.needed_by):
            if not datum.closes_with:
                pytest.fail(
                    f"{datum.id} gates {test_id} but declares no `closes_with`; a gap "
                    "must always name what would close it"
                )
            pytest.skip(
                f"{datum.id} ({datum.name}) not in repository. "
                f"{' '.join(datum.why_absent.split())} "
                f"CLOSES WITH: {' '.join(datum.closes_with.split())}"
            )

    pytest.fail(
        f"{test_id} needs measured value {path!r}, which is absent from the record and "
        "NOT declared in esrsim/data/missing_data.yaml. Every gap must be registered — "
        "add an entry with `needed_by` naming this test."
    )
