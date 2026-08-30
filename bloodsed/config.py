"""Building runs from plain dictionaries, YAML or JSON.

A scenario file keeps an experiment reproducible::

    blood:
      preset: inflammation
      hematocrit: 0.38
    geometry: "cone:L=200,Dbot=1.2,Dtop=4"
    config:
      duration_h: 2
      n_cells: 800
    compare:
      - westergren
      - funnel
      - "hourglass:L=200,Dend=4,Dthroat=1,at=0.4"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Sequence

from .blood import PRESETS as BLOOD_PRESETS, BloodProperties, get_blood
from .geometry import TubeGeometry, from_spec, get_geometry
from .inclination import BoycottModel
from .solver import SimulationConfig


def blood_from_dict(data: dict[str, Any] | str | None) -> BloodProperties:
    """Build blood properties from a preset name and/or explicit overrides."""
    if data is None:
        return BloodProperties()
    if isinstance(data, str):
        return get_blood(data)
    data = dict(data)
    preset = data.pop("preset", None)
    base = get_blood(preset) if preset else BloodProperties()
    known = {f.name for f in fields(BloodProperties)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown blood keys: {', '.join(sorted(unknown))}")
    return BloodProperties(**{**base.to_dict(), **data})


def geometry_from_dict(data: dict[str, Any] | str) -> TubeGeometry:
    """Build a geometry from a spec string or a mapping."""
    if isinstance(data, str):
        return from_spec(data)
    data = dict(data)
    tilt = data.pop("tilt_deg", None)
    label = data.pop("label", None)
    if "spec" in data:
        geo = from_spec(str(data.pop("spec")))
    elif "preset" in data:
        geo = get_geometry(str(data.pop("preset")))
    else:
        raise ValueError("a geometry mapping needs 'spec' or 'preset'")
    if data:
        raise ValueError(f"unexpected geometry keys: {', '.join(sorted(data))}")
    if tilt is not None:
        geo.tilt_deg = float(tilt)
    if label:
        geo.name = str(label)
    return geo


def simconfig_from_dict(data: dict[str, Any] | None) -> SimulationConfig:
    """Build a :class:`SimulationConfig`, including its nested Boycott model."""
    if not data:
        return SimulationConfig()
    data = dict(data)
    boycott = data.pop("boycott", None)
    known = {f.name for f in fields(SimulationConfig)} - {"boycott"}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
    cfg = SimulationConfig(**data)
    if boycott:
        if not isinstance(boycott, dict):
            raise ValueError("'boycott' must be a mapping")
        cfg.boycott = BoycottModel(**boycott)
    return cfg


@dataclass
class Scenario:
    """A blood sample, one or more tubes, and the numerical settings."""

    blood: BloodProperties
    geometries: list[TubeGeometry]
    config: SimulationConfig
    title: str = "bloodsed scenario"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scenario":
        blood = blood_from_dict(data.get("blood"))
        config = simconfig_from_dict(data.get("config"))
        entries: Sequence[Any]
        if "compare" in data:
            entries = data["compare"]
        elif "geometry" in data:
            entries = [data["geometry"]]
        else:
            entries = ["westergren"]
        geometries = [geometry_from_dict(entry) for entry in entries]
        return cls(blood=blood, geometries=geometries, config=config,
                   title=str(data.get("title", "bloodsed scenario")))

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        """Read a scenario from ``.yaml``/``.yml`` or ``.json``."""
        path = Path(path)
        text = path.read_text()
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover - depends on env
                raise ImportError(
                    "PyYAML is needed for .yaml scenarios; use .json instead"
                ) from exc
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a mapping at the top level")
        return cls.from_dict(data)
