import json

import pytest

from bloodsed.config import Scenario, blood_from_dict, geometry_from_dict, simconfig_from_dict


def test_blood_preset_can_be_partially_overridden():
    blood = blood_from_dict({"preset": "inflammation", "hematocrit": 0.30})
    assert blood.hematocrit == 0.30
    assert blood.aggregate_diameter_um == 110.0


def test_blood_accepts_a_bare_preset_name():
    assert blood_from_dict("anemic").hematocrit == 0.28


def test_unknown_blood_keys_are_reported():
    with pytest.raises(ValueError, match="unknown blood keys"):
        blood_from_dict({"hemotocrit": 0.4})


def test_geometry_from_spec_or_mapping():
    assert geometry_from_dict("westergren").tilt_deg == 0.0
    geo = geometry_from_dict({"preset": "westergren", "tilt_deg": 5, "label": "tilted"})
    assert geo.tilt_deg == 5.0 and geo.name == "tilted"
    assert geometry_from_dict({"spec": "cylinder:L=100,D=2"}).length == pytest.approx(0.1)


def test_geometry_mapping_needs_a_source():
    with pytest.raises(ValueError, match="spec"):
        geometry_from_dict({"tilt_deg": 3})


def test_config_carries_the_nested_boycott_model():
    cfg = simconfig_from_dict({"duration_h": 3, "boycott": {"efficiency": 0.5}})
    assert cfg.duration_h == 3
    assert cfg.boycott.efficiency == 0.5


def test_unknown_config_keys_are_reported():
    with pytest.raises(ValueError, match="unknown config keys"):
        simconfig_from_dict({"duration_hours": 3})


def test_scenario_round_trips_through_json(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({
        "title": "demo",
        "blood": {"preset": "normal", "hematocrit": 0.5},
        "compare": ["westergren", "cone:L=200,Dbot=1,Dtop=4"],
        "config": {"duration_h": 1, "n_cells": 200},
    }))
    scenario = Scenario.load(path)
    assert scenario.title == "demo"
    assert scenario.blood.hematocrit == 0.5
    assert len(scenario.geometries) == 2
    assert scenario.config.n_cells == 200


def test_scenario_defaults_to_a_westergren_tube():
    assert Scenario.from_dict({}).geometries[0].name.startswith("Westergren")
