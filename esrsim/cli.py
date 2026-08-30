"""Command line interface.

    esrsim geometry  T070 --report
    esrsim kinetics  --tube T070 --esr 45 --hct 0.42
    esrsim readout   --tube T070 --mode time_to_threshold
    esrsim validate  --icsh 2017 --data results.csv
    esrsim benchmark --tube T070
    esrsim explore   --sweep gap --range 0.5 1.5 --steps 11
    esrsim report    --tube T070 --html out.html
    esrsim rules     T070 --step 0.30 --upper-angle-offset -2
    esrsim unknowns
    esrsim serve                                 # local web UI in your browser

Build prompt: *"Every report, plot and CLI output shows tiers. No exceptions, no quiet
mode."* There is deliberately no ``--brief`` or ``--quiet`` flag.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import numpy as np

from . import __version__
from .calibration import validate as val
from .core import benchmark as bench
from .core import capillary as cap
from .core import continuum as cont
from .core import geometry as geo
from .core import kinetics as kin
from .core import readout as ro
from .core.fluid import fluid_report, list_fluids, load_fluid
from .design import explorer as expl
from .design.export import drawing_sheet, stl_parameters, to_json
from .design.rules import evaluate_rules
from .registry import open_questions
from .report import full_report, render_html, render_text
from .tiers import ResultSet, Tier

__all__ = ["main", "build_parser"]

_BANNER = (
    "esrsim — accelerated ESR device design analysis\n"
    "NOT a first-principles sedimentation simulator. Kinetics dataset: n = 1.\n"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="esrsim",
        description=_BANNER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"esrsim {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def tube_args(sp: argparse.ArgumentParser, positional: bool = False) -> None:
        if positional:
            sp.add_argument("tube", nargs="?", default="T070",
                            help="tube id from the library (default T070)")
        else:
            sp.add_argument("--tube", default="T070", help="tube id (default T070)")
        sp.add_argument("--generation", choices=["A", "B"], default=None,
                        help="blood-line convention; default is the library's")
        sp.add_argument("--fluid", default="blood_fresh",
                        choices=list(list_fluids()))
        sp.add_argument("--hct", type=float, default=0.45, help="haematocrit fraction")
        sp.add_argument("--phi-pack", type=float, default=0.90,
                        help="assumed sediment packing fraction (unknown U01)")

    g = sub.add_parser("geometry", help="exact cone geometry")
    tube_args(g, positional=True)
    g.add_argument("--report", action="store_true", help="full geometry block")
    g.add_argument("--step", type=float, default=None,
                   help="stepped upper cone width w (mm)")
    g.add_argument("--counterbore", type=float, default=None,
                   help="counterbore diameter (mm)")
    g.add_argument("--shift", type=float, default=None,
                   help="inner-cone shift (mm)")

    k = sub.add_parser("kinetics", help="enhancement models, lag, descent")
    tube_args(k)
    k.add_argument("--esr", type=float, default=30.0, help="Westergren ESR (mm/h)")
    k.add_argument("--readout", type=float, default=15.0, help="readout time (min)")
    k.add_argument("--decisive", action="store_true",
                   help="print the collinearity-breaking experiment (spec §9.3)")

    r = sub.add_parser("readout", help="screen a readout strategy")
    tube_args(r)
    r.add_argument("--mode", default="fixed_time_height",
                   choices=[m.name.lower() for m in ro.ReadoutMode])
    r.add_argument("--readout", type=float, default=15.0)
    r.add_argument("--window", type=float, default=3.0,
                   help="window length for delta_h mode (min)")
    r.add_argument("--threshold", type=float, default=10.0,
                   help="height threshold for time_to_threshold mode (mm)")

    v = sub.add_parser("validate", help="ICSH method comparison")
    v.add_argument("--icsh", choices=["2011", "2017", "both"], default="both")
    v.add_argument("--data", type=Path, default=None,
                   help="CSV with reference,device columns")
    v.add_argument("--feasibility", action="store_true",
                   help="can the ICSH 2017 study be run with this tube?")
    tube_args(v)
    v.add_argument("--readout", type=float, default=15.0)

    b = sub.add_parser("benchmark", help="against Westergren AND a tilted plain tube")
    tube_args(b)

    e = sub.add_parser("explore", help="sweep the design space")
    e.add_argument("--sweep", default="gap",
                   choices=["gap", "theta", "volume", "length"])
    e.add_argument("--range", nargs=2, type=float, default=None,
                   metavar=("LO", "HI"))
    e.add_argument("--steps", type=int, default=9)
    e.add_argument("--gap", type=float, default=0.70)
    e.add_argument("--theta", type=float, default=None)
    e.add_argument("--volume", type=float, default=2000.0)
    e.add_argument("--length", type=float, default=50.0)
    e.add_argument("--fluid", default="blood_fresh", choices=list(list_fluids()))
    e.add_argument("--hct", type=float, default=0.45)
    e.add_argument("--phi-pack", type=float, default=0.90)
    e.add_argument("--compare", nargs="*", default=None,
                   help="compare named library tubes instead of sweeping")

    rep = sub.add_parser("report", help="full report, text or HTML")
    tube_args(rep)
    rep.add_argument("--esr", type=float, default=30.0)
    rep.add_argument("--readout", type=float, default=15.0)
    rep.add_argument("--html", type=Path, default=None, help="write a standalone page")
    rep.add_argument("--json", type=Path, default=None)
    rep.add_argument("--continuum", action="store_true",
                     help="include the RESEARCH_ONLY continuum layer")
    rep.add_argument("--open-questions", action="store_true",
                     help="print the full unresolved register and exit")

    ru = sub.add_parser("rules", help="design rules R01-R10")
    tube_args(ru, positional=True)
    ru.add_argument("--step", type=float, default=0.30)
    ru.add_argument("--upper-angle-offset", type=float, default=-2.0)

    ex = sub.add_parser("export", help="DRIVING/DERIVED drawing sheet")
    tube_args(ex, positional=True)
    ex.add_argument("--step", type=float, default=0.30)
    ex.add_argument("--json", action="store_true", help="STL parameter dict")

    sub.add_parser("unknowns", help="the unresolved register")

    sv = sub.add_parser("serve", help="local web UI in your browser")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1",
                    help="loopback by default; anything else is reachable from other "
                         "machines on your network")
    sv.add_argument("--no-browser", action="store_true",
                    help="do not open a browser window")
    return p


def _cone(args: argparse.Namespace) -> geo.Cone:
    tube = getattr(args, "tube", None) or "T070"
    return geo.from_library(tube, generation=getattr(args, "generation", None))


def _emit(*blocks: ResultSet) -> None:
    for block in blocks:
        print(block.render())
        print()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command

    if cmd == "unknowns":
        print(open_questions().render())
        return 0

    if cmd == "serve":
        from .server import serve

        serve(host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0

    if cmd == "geometry":
        cone = _cone(args)
        blocks = [geo.geometry_report(cone)]
        if args.report:
            blocks.append(geo.phi_pack_sensitivity(cone, args.hct))
        if args.step is not None:
            blocks.append(geo.stepped_upper_cone(cone, w_mm=args.step))
        if args.counterbore is not None:
            blocks.append(geo.counterbore(cone, args.counterbore))
        if args.shift is not None:
            blocks.append(geo.shift_inner_cone(cone, args.shift))
        _emit(*blocks)
        return 0

    if cmd == "kinetics":
        if args.decisive:
            _emit(kin.decisive_experiment(args.hct))
            return 0
        cone = _cone(args)
        _emit(kin.kinetics_report(cone, args.esr, args.readout, args.hct,
                                  phi_pack=args.phi_pack))
        return 0

    if cmd == "readout":
        cone = _cone(args)
        mode = ro.ReadoutMode[args.mode.upper()]
        ceiling = geo.range_ceiling(cone, args.hct, args.phi_pack)
        mapping = _mapping_for(cone, mode, args)
        _emit(ro.readout_report(cone, mode, mapping, ceiling))
        return 0

    if cmd == "validate":
        cone = _cone(args)
        blocks: list[ResultSet] = []
        if args.data is not None:
            ref, dev = _read_pairs(args.data)
            blocks.append(val.validation_report(ref, dev))
        if args.feasibility or args.data is None:
            ceiling = geo.range_ceiling(cone, args.hct, args.phi_pack)

            def reading(esr: float) -> float:
                return kin.descent(cone, esr, args.hct, phi_pack=args.phi_pack,
                                   t_max_min=args.readout).height(args.readout)

            blocks.append(val.feasibility_check(
                reading, ceiling, label=f"{cone.tube_id}, fixed-time "
                                        f"{args.readout:g} min"))
        _emit(*blocks)
        return 0

    if cmd == "benchmark":
        _emit(bench.benchmark(_cone(args), args.hct, phi_pack=args.phi_pack))
        return 0

    if cmd == "explore":
        if args.compare is not None:
            rows = expl.compare(args.compare or list(geo.list_tubes()),
                                fluid=args.fluid, hematocrit=args.hct,
                                phi_pack=args.phi_pack)
        else:
            lo, hi = args.range if args.range else _default_range(args.sweep)
            values = np.linspace(lo, hi, args.steps)
            rows = expl.sweep(
                args.sweep, values, gap_mm=args.gap, volume_mm3=args.volume,
                length_mm=args.length, theta_deg=args.theta, fluid=args.fluid,
                hematocrit=args.hct, phi_pack=args.phi_pack,
            )
        print(expl.render_sweep(rows))
        return 0

    if cmd == "report":
        if args.open_questions:
            print(open_questions().render())
            return 0
        cone = _cone(args)
        blocks = full_report(cone, args.fluid, esr_mm_h=args.esr,
                             readout_min=args.readout, hematocrit=args.hct,
                             phi_pack=args.phi_pack,
                             include_continuum=args.continuum)
        if args.html:
            args.html.write_text(
                render_html(blocks, title=f"esrsim — {cone.tube_id} (Gen-"
                                          f"{cone.generation})",
                            **_chart_data(cone, args)),
                encoding="utf-8",
            )
            print(f"wrote {args.html}")
        if args.json:
            args.json.write_text(
                "[\n" + ",\n".join(to_json(b) for b in blocks) + "\n]",
                encoding="utf-8",
            )
            print(f"wrote {args.json}")
        if not args.html and not args.json:
            print(render_text(blocks, title=f"esrsim report — {cone.tube_id}"))
        return 0

    if cmd == "rules":
        _emit(evaluate_rules(_cone(args), args.fluid, step_w_mm=args.step,
                             upper_angle_offset_deg=args.upper_angle_offset,
                             hematocrit=args.hct, phi_pack=args.phi_pack))
        return 0

    if cmd == "export":
        cone = _cone(args)
        if args.json:
            import json

            print(json.dumps(stl_parameters(cone, step_w_mm=args.step), indent=2))
        else:
            _emit(drawing_sheet(cone, step_w_mm=args.step))
        return 0

    raise AssertionError(f"unhandled command {cmd!r}")


def _default_range(param: str) -> tuple[float, float]:
    return {
        "gap": (0.5, 1.5),        # the key experiment of spec §9.2
        "theta": (8.0, 20.0),
        "volume": (1500.0, 2500.0),
        "length": (30.0, 70.0),
    }[param]


def _mapping_for(cone: geo.Cone, mode: ro.ReadoutMode, args: argparse.Namespace):
    """Build the reading mapping for a readout mode from the calibrated kinetics."""
    t = args.readout

    def h(esr: float, when: float) -> float:
        return kin.descent(cone, esr, args.hct, phi_pack=args.phi_pack,
                           t_max_min=max(when, 1.0)).height(when)

    if mode is ro.ReadoutMode.FIXED_TIME_HEIGHT:
        return lambda esr: h(esr, t)
    if mode is ro.ReadoutMode.DELTA_H:
        return lambda esr: h(esr, t) - h(esr, t - args.window)
    if mode is ro.ReadoutMode.TIME_TO_THRESHOLD:
        def time_to(esr: float) -> float | None:
            run = kin.descent(cone, esr, args.hct, phi_pack=args.phi_pack,
                              t_max_min=180.0)
            reached = run.time_to(args.threshold)
            # Negated so that "higher ESR reads higher" and the monotonicity scan
            # runs in its natural increasing sense.
            return None if reached is None else -reached
        return time_to
    # CONDITIONAL: height early, falling back to time-to-threshold once saturated.
    def conditional(esr: float) -> float | None:
        run = kin.descent(cone, esr, args.hct, phi_pack=args.phi_pack, t_max_min=180.0)
        height = run.height(t)
        if height < 0.98 * run.ceiling_mm:
            return height
        reached = run.time_to(0.98 * run.ceiling_mm)
        return None if reached is None else run.ceiling_mm + (t - reached)
    return conditional


def _chart_data(cone: geo.Cone, args: argparse.Namespace) -> dict:
    times = np.linspace(0.0, 60.0, 121)
    curves = []
    for esr in (10.0, 30.0, 60.0):
        run = kin.descent(cone, esr, args.hct, phi_pack=args.phi_pack, t_max_min=60.0)
        curves.append((f"ESR {esr:g}", times.tolist(),
                       [run.height(float(t)) for t in times]))
    depths = np.linspace(0.0, cone.length_mm, 80)
    areas = [cone.area_at_height(float(d)) for d in depths]
    esrs = np.linspace(5.0, 60.0, 24)
    sens = [kin.sensitivity(cone, float(e), args.readout, args.hct,
                            phi_pack=args.phi_pack).value for e in esrs]
    return {
        "curves": curves,
        "area_profile": (depths.tolist(), areas),
        "sensitivity_curve": (esrs.tolist(), sens),
    }


def _read_pairs(path: Path) -> tuple[list[float], list[float]]:
    import csv

    ref: list[float] = []
    dev: list[float] = []
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(l for l in fh if not l.lstrip().startswith("#"))
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: no header row")
        cols = {c.lower().strip(): c for c in reader.fieldnames}
        for want in ("reference", "device"):
            if want not in cols:
                raise SystemExit(
                    f"{path}: need columns 'reference' and 'device'; "
                    f"found {reader.fieldnames}"
                )
        for row in reader:
            r, d = row[cols["reference"]].strip(), row[cols["device"]].strip()
            if r == "" or d == "":
                # Empty means NOT MEASURED. The pair is dropped, never zeroed.
                continue
            ref.append(float(r))
            dev.append(float(d))
    if not ref:
        raise SystemExit(f"{path}: no usable pairs")
    return ref, dev


if __name__ == "__main__":
    sys.exit(main())
