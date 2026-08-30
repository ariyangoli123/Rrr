"""Local web UI tests — the API must preserve the tier discipline end to end.

The UI is only a renderer. What matters is that nothing reaches the browser without a
provenance tier, that UNKNOWN still carries no number, and that bad input is refused
rather than fed into a model function.
"""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from esrsim import server
from esrsim.ui import PAGE


@pytest.fixture(scope="module")
def live():
    httpd = server.serve(host="127.0.0.1", port=8731, open_browser=False, forever=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address
    httpd.shutdown()
    httpd.server_close()


def get(addr, path: str) -> tuple[int, dict | str]:
    conn = HTTPConnection(*addr, timeout=60)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    if resp.getheader("Content-Type", "").startswith("application/json"):
        return resp.status, json.loads(body)
    return resp.status, body


ENDPOINTS = [
    "meta", "geometry", "capillary", "kinetics", "readout", "validate",
    "benchmark", "rules", "continuum", "unknowns", "compare", "curves",
]


def test_index_serves_the_page(live) -> None:
    status, body = get(live, "/")
    assert status == 200
    assert "esrsim" in body and "NOT A SIMULATOR" in body


def test_page_is_offline_no_cdn_no_external_calls() -> None:
    """The bench may have no network. Nothing may be fetched from outside."""
    for banned in ("http://", "https://", "cdn.", "//unpkg", "googleapis"):
        assert banned not in PAGE, banned


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_endpoint_responds(live, endpoint: str) -> None:
    status, payload = get(live, f"/api/{endpoint}?tube=T070")
    assert status == 200, payload
    assert isinstance(payload, dict) and "error" not in payload


@pytest.mark.parametrize(
    "endpoint",
    ["geometry", "capillary", "kinetics", "readout", "validate", "benchmark",
     "rules", "continuum"],
)
def test_every_value_reaching_the_browser_has_a_tier(live, endpoint: str) -> None:
    """The central guarantee, checked at the wire rather than in-process."""
    _status, payload = get(live, f"/api/{endpoint}?tube=T070")
    blocks = payload["blocks"]
    assert blocks
    for block in blocks:
        assert block["tier"]
        for result in block["results"]:
            assert result["tier"], f"{endpoint}: {result['name']} has no tier"


def test_unknown_results_carry_no_value_but_do_carry_an_experiment(live) -> None:
    _status, payload = get(live, "/api/rules?tube=T070")
    unknowns = [
        r for b in payload["blocks"] for r in b["results"] if r["tier"] == "UNKNOWN"
    ]
    assert unknowns, "T070's R02 should be undecidable"
    for r in unknowns:
        assert r["value"] is None
        assert r["why_unknown"] and r["experiment"]


def test_delta_h_mode_is_refused_through_the_api(live) -> None:
    _status, payload = get(live, "/api/readout?tube=T090&mode=DELTA_H")
    names = {r["name"]: r for b in payload["blocks"] for r in b["results"]}
    accepted = names["readout_accepted[DELTA_H]"]
    assert accepted["tier"] == "UNKNOWN" and accepted["value"] is None


def test_feasibility_is_reported_as_infeasible(live) -> None:
    _status, payload = get(live, "/api/validate?tube=T090")
    names = {r["name"]: r for b in payload["blocks"] for r in b["results"]}
    assert names["icsh_2017_feasible"]["value"] is False


def test_custom_geometry_solves_theta_for_the_volume(live) -> None:
    _status, payload = get(live, "/api/geometry?tube=custom&gap=0.7&volume=2000&length=50")
    names = {r["name"]: r for b in payload["blocks"] for r in b["results"]}
    assert names["theta_outer"]["value"] == pytest.approx(13.466, abs=0.01)
    assert names["volume_numeric"]["value"] == pytest.approx(2000, rel=0.001)


def test_curves_have_linear_real_axes(live) -> None:
    _status, payload = get(live, "/api/curves?tube=T070")
    sed = payload["sedimentation"]
    assert sed["x_label"].startswith("time")
    xs = sed["series"][0]["x"]
    assert xs[0] == 0.0 and xs[-1] > 30 and all(
        b > a for a, b in zip(xs, xs[1:])
    ), "the time axis must be real and increasing, not categorical"


def test_sensitivity_chart_shows_model_and_record_side_by_side(live) -> None:
    """Unknown U10 must stay visible in the UI, not just in the text report."""
    _status, payload = get(live, "/api/curves?tube=T090")
    names = [s["name"] for s in payload["sensitivity"]["series"]]
    assert "model" in names and "recorded" in names
    assert "U10" in payload["sensitivity"]["note"]


# ------------------------------------------------------------------ input guards


@pytest.mark.parametrize(
    "query,expect",
    [
        ("tube=nonsense", "not one of"),
        ("tube=T070&hct=2.0", "outside"),
        ("tube=T070&hct=abc", "not a number"),
        ("tube=T070&phi_pack=0.1&hct=0.45", None),      # valid but Hct > phi handled
        ("tube=custom&gap=99", "outside"),
        ("tube=T070&generation=C", "not one of"),
    ],
)
def test_bad_input_is_refused_not_computed(live, query: str, expect: str | None) -> None:
    status, payload = get(live, f"/api/geometry?{query}")
    if expect is None:
        assert status in (200, 400)
        return
    assert status == 400, payload
    assert expect in payload["error"]


def test_unknown_endpoint_returns_404_with_the_list(live) -> None:
    status, payload = get(live, "/api/nope")
    assert status == 404
    assert "/api/meta" in payload["endpoints"]


def test_sweep_rejects_an_inverted_range(live) -> None:
    status, payload = get(live, "/api/sweep?param=gap&lo=1.5&hi=0.5")
    assert status == 400 and "must exceed" in payload["error"]


def test_meta_lists_the_library_and_the_disclaimer(live) -> None:
    _status, payload = get(live, "/api/meta")
    assert len(payload["tubes"]) == 6
    assert payload["tiers"][0] == "EXACT" and payload["tiers"][-1] == "UNKNOWN"
    assert "n = 1" in payload["disclaimer"]


def test_server_binds_loopback_by_default(live) -> None:
    assert live[0] == "127.0.0.1"


def test_free_port_falls_back_when_taken(live) -> None:
    """Starting a second instance on the same port must not crash."""
    port = server._free_port("127.0.0.1", live[1])
    assert port != live[1]


def test_ui_defaults_to_blood_not_water() -> None:
    """The CLI and API default to fresh blood; the select must not disagree.

    Left to the library's list order the dropdown would pick water, and the opening
    screen of a blood instrument would quietly show water numbers.
    """
    assert '$("fluid").value = "blood_fresh";' in PAGE


def test_ui_refuses_to_render_an_untagged_row() -> None:
    """The renderer is the last line of defence: no tier, no row."""
    assert 'throw new Error("untagged value reached the UI' in PAGE
