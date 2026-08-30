"""Command line interface.

    bloodsed list
    bloodsed run westergren --hours 2 --out results/
    bloodsed compare shapes --blood inflammation --out results/
    bloodsed sweep hematocrit 0.2,0.3,0.4,0.5,0.6 --out results/
    bloodsed run --scenario my_experiment.yaml
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from .blood import PRESETS as BLOOD_PRESETS, BloodProperties, get_blood
from .config import Scenario
from .flux import FLUX_LAWS
from .geometry import (GEOMETRY_SETS, PRESETS as GEOMETRY_PRESETS, TubeGeometry,
                       from_spec, split_specs)
from .inclination import BoycottModel
from .metrics import (
    format_table,
    write_profile_csv,
    write_summary_csv,
    write_timeseries_csv,
)
from .solver import SimulationConfig, SimulationResult, simulate


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bloodsed",
        description="Simulate blood sedimentation (ESR) in tubes of different geometry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  bloodsed list\n"
            "  bloodsed run westergren --hours 2 --out results\n"
            "  bloodsed run 'cone:L=200,Dbot=1.2,Dtop=4' --blood inflammation\n"
            "  bloodsed compare shapes --out results\n"
            "  bloodsed compare westergren,westergren:tilt=3,westergren:tilt=15\n"
            "  bloodsed sweep hematocrit 0.2,0.3,0.45,0.55,0.65 --out results\n"
            "  bloodsed run annular-cone --flow --out results\n"
            "  bloodsed compare annular --out results\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="show the built-in geometries, samples and flux laws")
    listing.set_defaults(func=cmd_list)

    run = sub.add_parser("run", help="simulate one tube")
    run.add_argument("geometry", nargs="?", default="westergren",
                     help="preset name or spec, e.g. 'cylinder:L=200,D=2.5' "
                          "(default: westergren)")
    _add_common(run)
    run.add_argument("--animate", action="store_true", help="also write an animated GIF")
    run.add_argument("--flow", action="store_true",
                     help="also write the flow field: phase velocities and tracer arrows")
    run.set_defaults(func=cmd_run)

    compare = sub.add_parser("compare", help="simulate several tubes and compare them")
    compare.add_argument("geometries", nargs="?", default="shapes",
                         help="a named set (%s), or a list of specs separated by "
                              "',' (or ';' if a spec has its own commas)"
                              % ", ".join(GEOMETRY_SETS))
    _add_common(compare)
    compare.set_defaults(func=cmd_compare)

    sweep = sub.add_parser("sweep", help="vary one parameter and plot the effect on the ESR")
    sweep.add_argument("parameter", choices=["hematocrit", "aggregate", "tilt", "viscosity"],
                       help="what to vary")
    sweep.add_argument("values", help="comma separated values")
    sweep.add_argument("--geometry", default="westergren", help="tube to use (default: westergren)")
    _add_common(sweep)
    sweep.set_defaults(func=cmd_sweep)

    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    sample = parser.add_argument_group("sample")
    sample.add_argument("--blood", default=None, metavar="PRESET",
                        help="blood preset (%s)" % ", ".join(BLOOD_PRESETS))
    sample.add_argument("--hematocrit", type=float, default=None,
                        help="red-cell volume fraction, 0..1")
    sample.add_argument("--aggregate-um", type=float, default=None,
                        help="rouleaux diameter in micrometres (drives the ESR)")
    sample.add_argument("--viscosity", type=float, default=None,
                        help="plasma viscosity in Pa.s (e.g. 0.0016)")
    sample.add_argument("--max-packing", type=float, default=None,
                        help="packed-cell volume fraction (default 0.90)")
    sample.add_argument("--exponent", type=float, default=None,
                        help="Richardson-Zaki hindrance exponent (default 4.65)")
    sample.add_argument("--lag-min", type=float, default=None,
                        help="rouleaux formation time constant, in minutes")

    protocol = parser.add_argument_group("protocol")
    protocol.add_argument("--hours", type=float, default=2.0, help="simulated time (default 2)")
    protocol.add_argument("--fill", type=float, default=1.0,
                          help="filled fraction of the tube, 0..1 (default 1)")
    protocol.add_argument("--tilt", type=float, default=None,
                          help="tilt from vertical in degrees, applied to every tube")

    numerics = parser.add_argument_group("numerics")
    numerics.add_argument("--cells", type=int, default=600, help="axial cells (default 600)")
    numerics.add_argument("--sample-s", type=float, default=60.0,
                          help="profile sampling interval in seconds (default 60)")
    numerics.add_argument("--cfl", type=float, default=0.4, help="Courant number (default 0.4)")
    numerics.add_argument("--flux", default="hindered-packing", choices=sorted(FLUX_LAWS),
                          help="hindered settling law")
    numerics.add_argument("--no-wall", action="store_true",
                          help="disable the Faxen wall correction")
    numerics.add_argument("--no-lag", action="store_true",
                          help="disable the rouleaux formation lag")
    numerics.add_argument("--boycott-model", default="pnk", choices=["pnk", "none", "constant"],
                          help="inclination model (default pnk)")
    numerics.add_argument("--boycott-efficiency", type=float, default=0.08,
                          help="PNK efficiency; 1.0 is the textbook formula (default 0.08)")

    output = parser.add_argument_group("output")
    output.add_argument("--out", default=None, metavar="DIR",
                        help="write figures and CSV here (default: no files)")
    output.add_argument("--no-plots", action="store_true", help="skip the figures")
    output.add_argument("--scenario", default=None, metavar="FILE",
                        help="YAML/JSON scenario; command line options still override it")
    output.add_argument("--quiet", action="store_true", help="only print the table")


# ----------------------------------------------------------------------
def _blood_from_args(args, base: BloodProperties | None = None) -> BloodProperties:
    blood = base or (get_blood(args.blood) if args.blood else BloodProperties())
    changes: dict = {}
    if args.hematocrit is not None:
        changes["hematocrit"] = args.hematocrit
    if args.aggregate_um is not None:
        changes["aggregate_diameter_um"] = args.aggregate_um
    if args.viscosity is not None:
        changes["plasma_viscosity"] = args.viscosity
    if args.max_packing is not None:
        changes["max_packing"] = args.max_packing
    if args.exponent is not None:
        changes["hindrance_exponent"] = args.exponent
    if args.lag_min is not None:
        changes["aggregation_time_s"] = args.lag_min * 60.0
    return replace(blood, **changes) if changes else blood


def _config_from_args(args, base: SimulationConfig | None = None) -> SimulationConfig:
    boycott = BoycottModel(model=args.boycott_model, efficiency=args.boycott_efficiency)
    cfg = base or SimulationConfig()
    return replace(
        cfg,
        duration_h=args.hours,
        n_cells=args.cells,
        sample_interval_s=args.sample_s,
        cfl=args.cfl,
        fill_fraction=args.fill,
        flux_law=args.flux,
        wall_correction=not args.no_wall,
        aggregation_lag=not args.no_lag,
        boycott=boycott,
    )


def _scenario_defaults(args) -> Scenario | None:
    return Scenario.load(args.scenario) if getattr(args, "scenario", None) else None


def _resolve_geometries(text: str, tilt: float | None) -> list[TubeGeometry]:
    names = GEOMETRY_SETS.get(text.strip())
    entries = names if names else split_specs(text)
    if not entries:
        raise ValueError("no geometry given")
    geometries = [from_spec(entry) for entry in entries]
    if tilt is not None:
        for geo in geometries:
            geo.tilt_deg = tilt
    return geometries


def _run_all(geometries: Sequence[TubeGeometry], blood: BloodProperties,
             config: SimulationConfig, quiet: bool) -> list[SimulationResult]:
    results = []
    for geo in geometries:
        if not quiet:
            print(f"  simulating {geo.describe()}", flush=True)
        results.append(simulate(geo, blood, config, label=geo.name))
    return results


def _report(results: Sequence[SimulationResult], args, *, title: str,
            comparison: bool) -> None:
    print()
    print(format_table(results))
    print()
    worst = max(r.mass_error for r in results)
    if not args.quiet:
        print(f"cell volume conserved to {worst:.1e} relative; "
              f"{sum(r.n_steps for r in results)} time steps in "
              f"{sum(r.wall_clock_s for r in results):.1f} s")

    if not args.out:
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_summary_csv(results, out / "summary.csv")
    for res in results:
        stem = _slug(res.label)
        write_timeseries_csv(res, out / f"curve_{stem}.csv")
        write_profile_csv(res, out / f"profiles_{stem}.csv")

    if not args.no_plots:
        from . import plotting

        if comparison and len(results) > 1:
            plotting.save(plotting.comparison(results, title=title), out / "comparison.png")
        for res in results:
            plotting.save(plotting.case_report(res), out / f"case_{_slug(res.label)}.png")
    print(f"wrote {len(list(out.iterdir()))} files to {out}/")


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "case"


# ----------------------------------------------------------------------
def cmd_list(args) -> int:
    print("tube geometries")
    for name in GEOMETRY_PRESETS:
        print(f"  {name:22s} {GEOMETRY_PRESETS[name]().describe()}")
    print("\ngeometry sets (for 'bloodsed compare')")
    for name, members in GEOMETRY_SETS.items():
        print(f"  {name:22s} {', '.join(members)}")
    print("\nblood samples")
    for name, blood in BLOOD_PRESETS.items():
        print(f"  {name:22s} {blood.describe()}")
    print("\nhindered settling laws")
    for name in FLUX_LAWS:
        print(f"  {name}")
    print("\ngeometry specs")
    print("  cylinder:L=200,D=2.5            straight bore")
    print("  cone:L=200,Dbot=1.2,Dtop=4      linear taper (funnel)")
    print("  hourglass:L=200,Dend=4,Dthroat=1,at=0.5")
    print("  bulb:L=200,D=2.5,Dbulge=6,pos=0.5,width=0.1")
    print("  stepped:20x1,180x3              stacked sections, length x diameter")
    print("  annulus:L=150,D=6,angle=15,gap=2  cone inside a cone, blood in the gap")
    print("  any of the above plus ',tilt=3' to incline the tube")
    return 0


def cmd_run(args) -> int:
    scenario = _scenario_defaults(args)
    blood = _blood_from_args(args, scenario.blood if scenario else None)
    config = _config_from_args(args, scenario.config if scenario else None)
    if scenario and args.geometry == "westergren":
        geometries = scenario.geometries[:1]
        if args.tilt is not None:
            geometries[0].tilt_deg = args.tilt
    else:
        geometries = _resolve_geometries(args.geometry, args.tilt)[:1]

    if not args.quiet:
        print(f"blood: {blood.describe()}")
    results = _run_all(geometries, blood, config, args.quiet)
    _report(results, args, title=results[0].label, comparison=False)

    if args.flow and args.out:
        from . import plotting
        from .flows import peak_velocities

        index = int(np.argmin(np.abs(results[0].times - 3600.0)))
        path = plotting.save(plotting.flow_report(results[0], index),
                             Path(args.out) / f"flow_{_slug(results[0].label)}.png")
        flow = peak_velocities(results[0], index)
        print(f"flow at {flow['time_min']:.0f} min: cells {flow['cells_max_mm_per_h']:.1f} mm/h, "
              f"plasma {flow['plasma_max_mm_per_h']:.1f} mm/h, "
              f"Boycott x{flow['enhancement']:.2f}")
        print(f"wrote {path}")
    elif args.flow:
        print("--flow needs --out DIR", file=sys.stderr)

    if args.animate and args.out:
        from . import plotting
        path = plotting.animate(results[0], Path(args.out) / f"{_slug(results[0].label)}.gif")
        print(f"wrote {path}")
    elif args.animate:
        print("--animate needs --out DIR", file=sys.stderr)
    return 0


def cmd_compare(args) -> int:
    scenario = _scenario_defaults(args)
    blood = _blood_from_args(args, scenario.blood if scenario else None)
    config = _config_from_args(args, scenario.config if scenario else None)
    if scenario and args.geometries == "shapes":
        geometries = scenario.geometries
        if args.tilt is not None:
            for geo in geometries:
                geo.tilt_deg = args.tilt
    else:
        geometries = _resolve_geometries(args.geometries, args.tilt)

    if not args.quiet:
        print(f"blood: {blood.describe()}")
    results = _run_all(geometries, blood, config, args.quiet)
    _report(results, args, title="Sedimentation in tubes of different geometry",
            comparison=True)
    return 0


def cmd_sweep(args) -> int:
    values = [float(v) for v in args.values.split(",") if v.strip()]
    if not values:
        print("no values to sweep", file=sys.stderr)
        return 2
    base_blood = _blood_from_args(args)
    config = _config_from_args(args)

    results: list[SimulationResult] = []
    for value in values:
        geo = from_spec(args.geometry)
        blood = base_blood
        if args.parameter == "hematocrit":
            blood = replace(base_blood, hematocrit=value)
            label = f"Hct {value:.0%}"
        elif args.parameter == "aggregate":
            blood = replace(base_blood, aggregate_diameter_um=value)
            label = f"aggregate {value:g} um"
        elif args.parameter == "viscosity":
            blood = replace(base_blood, plasma_viscosity=value)
            label = f"mu {value * 1e3:g} mPa.s"
        else:
            geo.tilt_deg = value
            label = f"tilt {value:g} deg"
        if not args.quiet:
            print(f"  simulating {label}", flush=True)
        results.append(simulate(geo, blood, config, label=label))

    print()
    print(format_table(results))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        write_summary_csv(results, out / f"sweep_{args.parameter}.csv")
        if not args.no_plots:
            from . import plotting

            fig = plotting.sweep_figure(results, values, args.parameter)
            plotting.save(fig, out / f"sweep_{args.parameter}.png")
        print(f"wrote results to {out}/")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
