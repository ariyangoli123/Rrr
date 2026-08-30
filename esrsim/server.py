"""Local web UI — ``esrsim serve``.

The browser is only the interface. **Every number is computed by the same Python
functions the CLI and the test suite use**, serialised through
:meth:`esrsim.tiers.Result.to_dict`, which carries the tier, flags, notes and — for
UNKNOWN results — the reason and the experiment that would close it.

That is deliberate. Re-implementing the physics in JavaScript would give two
implementations of one calculation, and they would drift. For a package whose entire
premise is that every number is traceable to a provenance tier, two answers for one
number is the failure mode, not a convenience.

Built on :mod:`http.server` from the standard library: no Flask, no FastAPI, no CDN.
The page works with the network cable unplugged.

Security
--------
Binds to 127.0.0.1 by default and refuses to serve anything off disk. Every numeric
input is parsed and range-checked before it reaches a model function. Binding to a
non-loopback address requires an explicit ``--host``, and the CLI warns when you do.
"""

from __future__ import annotations

import json
import socket
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

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
from .design.export import drawing_sheet
from .design.rules import evaluate_rules
from .registry import missing_data, open_questions, unknowns
from .tiers import ResultSet
from .ui import PAGE

__all__ = ["serve", "build_app", "ApiError"]


class ApiError(Exception):
    """A bad request. Carries an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------- parameters


@dataclass(frozen=True, slots=True)
class Params:
    """Validated query parameters. Every bound here is a real physical limit."""

    raw: Mapping[str, list[str]]

    def _one(self, key: str) -> str | None:
        values = self.raw.get(key)
        return values[0] if values else None

    def str_(self, key: str, default: str, allowed: tuple[str, ...] | None = None) -> str:
        value = self._one(key) or default
        if allowed is not None and value not in allowed:
            raise ApiError(f"{key}={value!r} is not one of {list(allowed)}")
        return value

    def num(
        self, key: str, default: float, lo: float, hi: float
    ) -> float:
        text = self._one(key)
        if text is None or text == "":
            return default
        try:
            value = float(text)
        except ValueError as exc:
            raise ApiError(f"{key}={text!r} is not a number") from exc
        if not (lo <= value <= hi):
            raise ApiError(f"{key}={value:g} is outside [{lo:g}, {hi:g}]")
        return value

    def flag(self, key: str, default: bool = False) -> bool:
        text = self._one(key)
        if text is None:
            return default
        return text.lower() in ("1", "true", "yes", "on")

    def tube(self) -> str:
        return self.str_("tube", "T070", tuple(geo.list_tubes()) + ("custom",))

    def fluid(self) -> str:
        return self.str_("fluid", "blood_fresh", tuple(list_fluids()))

    def hct(self) -> float:
        return self.num("hct", 0.45, 0.05, 0.85)

    def phi_pack(self) -> float:
        return self.num("phi_pack", 0.90, 0.50, 0.99)

    def esr(self) -> float:
        return self.num("esr", 30.0, 1.0, 200.0)

    def readout_min(self) -> float:
        return self.num("readout", 15.0, 1.0, 120.0)

    def cone(self) -> geo.Cone:
        """Either a library tube or a custom geometry built from gap/volume/length."""
        tube = self.tube()
        generation = self.str_("generation", "B", ("A", "B"))
        if tube != "custom":
            return geo.from_library(tube, generation=generation)  # type: ignore[arg-type]

        gap = self.num("gap", 0.70, 0.20, 3.00)
        volume = self.num("volume", 2000.0, 200.0, 20000.0)
        length = self.num("length", 50.0, 10.0, 200.0)
        theta = self._one("theta")
        theta_deg = (
            self.num("theta", 13.466, 2.0, 45.0) if theta not in (None, "", "auto")
            else expl.solve_theta_for_volume(gap, volume, length)
        )
        import math

        x_bl = (geo.GEN_A_MOUTH_DIAMETER_MM / 2.0) / math.tan(math.radians(theta_deg)) \
            + geo.GEN_A_BLOOD_LINE_OFFSET_MM
        return geo.Cone(
            theta_o_deg=theta_deg,
            theta_i_deg=theta_deg,
            delta_mm=gap / math.sin(math.radians(theta_deg)),
            x_bl_mm=x_bl,
            length_mm=length,
            generation=generation,  # type: ignore[arg-type]
            tube_id=f"custom gap {gap:.3f} / {theta_deg:.3f}°",
        )


# ------------------------------------------------------------------- API handlers


def _blocks(*sets: ResultSet) -> dict[str, Any]:
    return {"blocks": [s.to_dict() for s in sets]}


def api_meta(_p: Params) -> dict[str, Any]:
    return {
        "version": __version__,
        "tubes": [
            {
                "id": t,
                "gap": round(geo.from_library(t).gap_perpendicular(
                    geo.from_library(t).x_bl_mm), 4),
                "theta": geo.from_library(t).theta_o_deg,
                "volume": round(geo.from_library(t).volume_mm3, 1),
            }
            for t in geo.list_tubes()
        ],
        "fluids": [{"id": f, "label": load_fluid(f).label} for f in list_fluids()],
        "tiers": [
            "EXACT", "CALIBRATED", "EXTRAPOLATED", "ESTIMATED",
            "HYPOTHESIS", "RESEARCH_ONLY", "UNKNOWN",
        ],
        "disclaimer": (
            "Not a first-principles simulator. It does not predict the ESR of an "
            "unknown sample, does not simulate mixing, and does not replace "
            "experimental calibration. The whole kinetics dataset is n = 1."
        ),
    }


def api_geometry(p: Params) -> dict[str, Any]:
    cone = p.cone()
    sets = [geo.geometry_report(cone), geo.phi_pack_sensitivity(cone, p.hct())]
    w = p.num("step_w", 0.30, 0.05, 2.00)
    try:
        sets.append(geo.stepped_upper_cone(
            cone, w_mm=w,
            upper_angle_offset_deg=p.num("upper_offset", -2.0, -8.0, 8.0),
        ))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    sets.append(drawing_sheet(cone, step_w_mm=w))
    return _blocks(*sets)


def api_capillary(p: Params) -> dict[str, Any]:
    cone = p.cone()
    fluid = load_fluid(p.fluid())
    step = geo.stepped_upper_cone(cone, w_mm=p.num("step_w", 0.30, 0.05, 2.00))
    mixing = cap.mixing_criterion(
        cap.MixingGeometry(
            guide_surface_continuous=p.flag("guide_surface", True),
            clearance_working_mm=cone.clearance_radial(cone.x_bl_mm),
            clearance_above_min_mm=step["clearance_above_min"].value,
            description=f"{cone.tube_id or 'cone'} + stepped upper cone",
        )
    )
    return _blocks(cap.capillary_report(cone, fluid), mixing, fluid_report(fluid))


def api_kinetics(p: Params) -> dict[str, Any]:
    cone = p.cone()
    return _blocks(
        kin.kinetics_report(cone, p.esr(), p.readout_min(), p.hct(),
                            phi_pack=p.phi_pack()),
        kin.decisive_experiment(p.hct()),
    )


def api_readout(p: Params) -> dict[str, Any]:
    cone, hct, phi = p.cone(), p.hct(), p.phi_pack()
    t = p.readout_min()
    mode = ro.ReadoutMode[p.str_(
        "mode", "FIXED_TIME_HEIGHT", tuple(m.name for m in ro.ReadoutMode)
    )]
    ceiling = geo.range_ceiling(cone, hct, phi)

    def height(esr: float, when: float) -> float:
        return kin.descent(cone, esr, hct, phi_pack=phi,
                           t_max_min=max(when, 1.0)).height(when)

    window = p.num("window", 3.0, 0.5, 30.0)
    if mode is ro.ReadoutMode.DELTA_H:
        mapping: Callable[[float], float | None] = \
            lambda e: height(e, t) - height(e, max(t - window, 0.1))
    elif mode is ro.ReadoutMode.TIME_TO_THRESHOLD:
        threshold = p.num("threshold", 10.0, 1.0, 40.0)

        def mapping(esr: float) -> float | None:  # type: ignore[misc]
            run = kin.descent(cone, esr, hct, phi_pack=phi, t_max_min=180.0)
            reached = run.time_to(threshold)
            return None if reached is None else -reached
    else:
        mapping = lambda e: height(e, t)  # noqa: E731

    grid = [2.0 + 0.5 * i for i in range(int((120.0 - 2.0) / 0.5) + 1)]
    sensitivity = [
        (esr, height(esr, t), kin.sensitivity(cone, esr, t, hct, phi_pack=phi).value)
        for esr in (13.0, 20.0, 30.0, 40.0)
    ]
    return _blocks(
        ro.readout_report(cone, mode, mapping, ceiling, grid),
        ro.error_budget(cone, ceiling, sensitivity),
        ro.recorded_sensitivity(),
    )


def api_validate(p: Params) -> dict[str, Any]:
    cone, hct, phi = p.cone(), p.hct(), p.phi_pack()
    t = p.readout_min()
    ceiling = geo.range_ceiling(cone, hct, phi)

    def reading(esr: float) -> float:
        return kin.descent(cone, esr, hct, phi_pack=phi, t_max_min=t).height(t)

    return _blocks(
        val.feasibility_check(
            reading, ceiling,
            resolution_mm=p.num("resolution", 0.5, 0.01, 5.0),
            label=f"{cone.tube_id or 'cone'}, fixed-time {t:g} min",
        )
    )


def api_benchmark(p: Params) -> dict[str, Any]:
    return _blocks(bench.benchmark(p.cone(), p.hct(), phi_pack=p.phi_pack()))


def api_rules(p: Params) -> dict[str, Any]:
    return _blocks(evaluate_rules(
        p.cone(), p.fluid(),
        step_w_mm=p.num("step_w", 0.30, 0.05, 2.00),
        upper_angle_offset_deg=p.num("upper_offset", -2.0, -8.0, 8.0),
        hematocrit=p.hct(), phi_pack=p.phi_pack(),
    ))


def api_continuum(p: Params) -> dict[str, Any]:
    return _blocks(cont.continuum_report(p.cone(), p.fluid()))


def api_unknowns(_p: Params) -> dict[str, Any]:
    return {
        "blocks": [open_questions().to_dict()],
        "unknowns": [
            {
                "id": u.id, "name": u.name, "desc": u.desc, "status": u.status,
                "affects": list(u.affects),
                "how_to_resolve": " ".join(u.how_to_resolve.split()),
                "note": " ".join(u.note.split()),
            }
            for u in unknowns()
        ],
        "missing": [
            {
                "id": m.id, "name": m.name,
                "what_it_is": " ".join(m.what_it_is.split()),
                "why_absent": " ".join(m.why_absent.split()),
                "closes_with": " ".join(m.closes_with.split()),
                "needed_by": list(m.needed_by),
            }
            for m in missing_data()
        ],
    }


def api_sweep(p: Params) -> dict[str, Any]:
    param = p.str_("param", "gap", ("gap", "theta", "volume", "length"))
    lo = p.num("lo", 0.5, 0.05, 20000.0)
    hi = p.num("hi", 1.5, 0.05, 20000.0)
    steps = int(p.num("steps", 9, 2, 40))
    if hi <= lo:
        raise ApiError(f"hi ({hi:g}) must exceed lo ({lo:g})")
    values = [lo + (hi - lo) * i / (steps - 1) for i in range(steps)]
    theta = p._one("theta")
    rows = expl.sweep(
        param, values,
        gap_mm=p.num("gap", 0.70, 0.20, 3.00),
        volume_mm3=p.num("volume", 2000.0, 200.0, 20000.0),
        length_mm=p.num("length", 50.0, 10.0, 200.0),
        theta_deg=(float(theta) if theta not in (None, "", "auto") else None),
        fluid=p.fluid(), hematocrit=p.hct(), phi_pack=p.phi_pack(),
    )
    return {
        "param": param,
        "rows": [
            {
                "label": r.label, "tier": r.tier.name,
                "results": r.results.to_dict()["results"],
            }
            for r in rows
        ],
    }


def api_compare(p: Params) -> dict[str, Any]:
    rows = expl.compare(
        list(geo.list_tubes()), fluid=p.fluid(),
        hematocrit=p.hct(), phi_pack=p.phi_pack(),
    )
    return {
        "rows": [
            {
                "label": r.label, "tier": r.tier.name,
                "results": r.results.to_dict()["results"],
            }
            for r in rows
        ]
    }


def api_curves(p: Params) -> dict[str, Any]:
    """Chart data. Linear, real axes — spec §12."""
    cone, hct, phi = p.cone(), p.hct(), p.phi_pack()
    t_max = p.num("t_max", 60.0, 5.0, 240.0)
    times = [t_max * i / 120.0 for i in range(121)]
    curves = []
    for esr in (10.0, 30.0, 60.0):
        run = kin.descent(cone, esr, hct, phi_pack=phi, t_max_min=t_max)
        curves.append({
            "name": f"ESR {esr:g}",
            "x": times,
            "y": [run.height(t) for t in times],
        })
    depths = [cone.length_mm * i / 79.0 for i in range(80)]
    esrs = [5.0 + 55.0 * i / 23.0 for i in range(24)]
    return {
        "sedimentation": {
            "series": curves, "x_label": "time (min)",
            "y_label": "boundary height (mm)",
            "tier": "CALIBRATED", "note": "n = 1; flattens at the range ceiling (U01)",
        },
        "area": {
            "series": [{"name": "A(x)", "x": depths,
                        "y": [cone.area_at_height(d) for d in depths]}],
            "x_label": "depth below blood line (mm)", "y_label": "area (mm²)",
            "tier": "EXACT", "note": "exact geometry",
        },
        "sensitivity": {
            "series": [{
                "name": "model", "x": esrs,
                "y": [kin.sensitivity(cone, e, p.readout_min(), hct,
                                      phi_pack=phi).value for e in esrs],
            }, {
                "name": "recorded", "x": [13.0, 20.0, 30.0, 40.0],
                "y": [0.83, 0.79, 0.60, 0.28],
            }],
            "x_label": "ESR (mm/h)", "y_label": "dh/dESR (mm per mm/h)",
            "tier": "CALIBRATED",
            "note": "the two columns disagree except near ESR 30 — unknown U10, "
                    "reported rather than tuned away",
        },
    }


ROUTES: dict[str, Callable[[Params], dict[str, Any]]] = {
    "/api/meta": api_meta,
    "/api/geometry": api_geometry,
    "/api/capillary": api_capillary,
    "/api/kinetics": api_kinetics,
    "/api/readout": api_readout,
    "/api/validate": api_validate,
    "/api/benchmark": api_benchmark,
    "/api/rules": api_rules,
    "/api/continuum": api_continuum,
    "/api/unknowns": api_unknowns,
    "/api/sweep": api_sweep,
    "/api/compare": api_compare,
    "/api/curves": api_curves,
}


# ------------------------------------------------------------------------ server


def build_app() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"esrsim/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return

            handler = ROUTES.get(path)
            if handler is None:
                self._send(
                    404,
                    json.dumps({"error": f"no such endpoint: {path}",
                                "endpoints": sorted(ROUTES)}).encode(),
                    "application/json; charset=utf-8",
                )
                return

            params = Params(parse_qs(parsed.query))
            try:
                payload = handler(params)
                status = 200
            except ApiError as exc:
                payload, status = {"error": str(exc)}, exc.status
            except (ValueError, KeyError) as exc:
                payload, status = {"error": f"{type(exc).__name__}: {exc}"}, 400
            except Exception as exc:  # pragma: no cover - surfaced to the UI
                payload, status = {"error": f"{type(exc).__name__}: {exc}"}, 500

            body = json.dumps(payload, allow_nan=False, default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

    return Handler


def _free_port(host: str, port: int) -> int:
    """If the requested port is taken, take the next free one rather than crashing."""
    for candidate in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise ApiError(f"no free port in {port}..{port + 19}", status=500)


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    open_browser: bool = True,
    forever: bool = True,
) -> ThreadingHTTPServer:
    """Start the local UI. Loopback-only unless ``host`` says otherwise."""
    port = _free_port(host, port)
    httpd = ThreadingHTTPServer((host, port), build_app())
    url = f"http://{host}:{port}/"

    print(f"esrsim {__version__} — local UI at {url}")
    print("Every number is computed in Python and carries its provenance tier.")
    print("NOT a first-principles simulator. Kinetics dataset: n = 1.")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: bound to {host}, which is reachable from other machines.")
    print("Ctrl-C to stop.")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    if forever:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")
        finally:
            httpd.server_close()
    return httpd
