"""Report generator — text and standalone HTML.

Spec §11: *"the program must read this file and print, in EVERY report, the list of
unknowns involved."* Spec §12 asks for a standalone HTML dashboard with a **linear,
real** time axis, not a categorical one.

Build prompt: *"Every report, plot and CLI output shows tiers. No exceptions, no quiet
mode."* There is deliberately no ``--brief`` flag anywhere in this package.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from .core import benchmark as bench
from .core import capillary as cap
from .core import continuum as cont
from .core import geometry as geo
from .core import kinetics as kin
from .core.fluid import Fluid, fluid_report, load_fluid
from .core.geometry import Cone
from .design.export import drawing_sheet
from .design.rules import evaluate_rules
from .registry import Unknown, missing_data, open_questions, unknowns, unknowns_for
from .tiers import Result, ResultSet, Tier

__all__ = ["full_report", "render_text", "render_html", "TIER_COLOURS"]

TIER_COLOURS: dict[str, str] = {
    "EXACT": "#1b7f3b",
    "CALIBRATED": "#2b6cb0",
    "EXTRAPOLATED": "#b7791f",
    "ESTIMATED": "#805ad5",
    "HYPOTHESIS": "#c05621",
    "RESEARCH_ONLY": "#718096",
    "UNKNOWN": "#c53030",
}

#: Topics touched by a full report, used to select the unknowns to print.
_REPORT_TOPICS = (
    "range_ceiling", "saturation", "volume_budget", "icsh_feasibility",
    "mixing_rule_R02", "design_explorer", "kinetics_lag", "readout_fixed_time",
    "delta_h_readout", "E_models", "extrapolation", "continuum_model",
    "capillary_barrier", "mixing_argument", "blood_line_unevenness_model_B",
    "capillary_unevenness", "design_rule_R08", "kinetics_descent",
    "readout_sensitivity", "error_budget", "mixing_rules", "device_validation",
    "benchmark", "patentability_claims",
)


def full_report(
    cone: Cone,
    fluid: Fluid | str = "blood_fresh",
    *,
    esr_mm_h: float = 30.0,
    readout_min: float = 15.0,
    hematocrit: float = 0.45,
    phi_pack: float = 0.90,
    include_continuum: bool = False,
) -> tuple[ResultSet, ...]:
    """Every block the project needs about one design, in one pass."""
    f = load_fluid(fluid) if isinstance(fluid, str) else fluid
    blocks = [
        geo.geometry_report(cone),
        geo.phi_pack_sensitivity(cone, hematocrit),
        geo.stepped_upper_cone(cone),
        fluid_report(f, cone.gap_perpendicular(cone.x_bl_mm), cone.length_mm),
        cap.capillary_report(cone, f),
        _mixing_block(cone),
        kin.kinetics_report(cone, esr_mm_h, readout_min, hematocrit,
                            phi_pack=phi_pack),
        bench.benchmark(cone, hematocrit, phi_pack=phi_pack),
        evaluate_rules(cone, f, hematocrit=hematocrit, phi_pack=phi_pack),
        drawing_sheet(cone),
    ]
    if include_continuum:
        blocks.append(cont.continuum_report(cone, f))
    return tuple(blocks)


def _mixing_block(cone: Cone) -> ResultSet:
    step = geo.stepped_upper_cone(cone)
    return cap.mixing_criterion(
        cap.MixingGeometry(
            guide_surface_continuous=True,
            clearance_working_mm=cone.clearance_radial(cone.x_bl_mm),
            clearance_above_min_mm=step["clearance_above_min"].value,
            description=f"{cone.tube_id or 'cone'} + stepped upper cone",
        )
    )


# ------------------------------------------------------------------------- text


def render_text(
    blocks: Sequence[ResultSet], *, title: str = "", show_unknowns: bool = True
) -> str:
    """Plain-text report. Tiers always shown; there is no quiet mode."""
    out: list[str] = []
    if title:
        out += ["=" * 78, title, "=" * 78, ""]
    out += [_disclaimer(), ""]
    for block in blocks:
        out.append(block.render())
        out.append("")
    if show_unknowns:
        out.append(_unknowns_section(blocks))
    return "\n".join(out)


def _disclaimer() -> str:
    return "\n".join([
        "WHAT THIS TOOL DOES NOT DO",
        "-" * 78,
        "It does not predict the ESR of an unknown sample. It does not simulate mixing.",
        "It does not replace experimental calibration. Whole-blood sedimentation is gel",
        "collapse, and the two material functions it would need — Py(phi) and R(phi) —",
        "have never been measured for whole blood anywhere (spec §0, unknown U05).",
        "The entire kinetics dataset is n = 1.",
    ])


def _unknowns_section(blocks: Sequence[ResultSet]) -> str:
    """Spec §11: print the unknowns this report's numbers ride on."""
    involved = _involved_unknowns(blocks)
    lines = ["", "=" * 78, "UNKNOWNS THESE NUMBERS RIDE ON", "=" * 78]
    if not involved:
        lines.append("(none of the registered unknowns affect this report)")
    for u in involved:
        lines.append(u.render())
        lines.append("")
    gaps = list(missing_data())
    if gaps:
        lines += ["MISSING DATA", "-" * 78]
        for m in gaps:
            lines.append(m.render())
            lines.append("")
    lines.append(
        "Run `esrsim report --open-questions` for the full register with the "
        "experiment that closes each one."
    )
    return "\n".join(lines)


def _involved_unknowns(blocks: Sequence[ResultSet]) -> tuple[Unknown, ...]:
    """Unknowns named by the report's own flags, tiers and topics."""
    selected = {u.id: u for u in unknowns_for(*_REPORT_TOPICS)}
    text = " ".join(
        " ".join(r.notes) + " " + r.why_unknown + " " + r.source
        for block in blocks for r in block
    )
    for u in unknowns():
        if u.id in text or u.name in text:
            selected[u.id] = u
    return tuple(sorted(selected.values(), key=lambda u: u.id))


# ------------------------------------------------------------------------- HTML


def render_html(
    blocks: Sequence[ResultSet],
    *,
    title: str = "esrsim report",
    curves: Sequence[tuple[str, Sequence[float], Sequence[float]]] = (),
    area_profile: tuple[Sequence[float], Sequence[float]] | None = None,
    sensitivity_curve: tuple[Sequence[float], Sequence[float]] | None = None,
) -> str:
    """A standalone HTML dashboard — spec §12.

    Charts are inline SVG with **linear, real** axes (spec §12 is explicit that the time
    axis must not be categorical). No external assets, no CDN: the file opens offline.
    """
    esc = html.escape
    parts: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{esc(title)}</title>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        _CSS,
        "</head><body>",
        f"<h1>{esc(title)}</h1>",
        "<div class='disclaimer'><h2>What this tool does not do</h2>"
        "<p>It does not predict the ESR of an unknown sample, does not simulate mixing, "
        "and does not replace experimental calibration. Whole-blood sedimentation is gel "
        "collapse and the two material functions it would need &mdash; "
        "<code>Py(&phi;)</code> and <code>R(&phi;)</code> &mdash; have never been "
        "measured for whole blood anywhere. <strong>The entire kinetics dataset is "
        "n&nbsp;=&nbsp;1.</strong></p></div>",
        _tier_legend(),
    ]

    if curves:
        parts.append("<h2>Sedimentation curves</h2>")
        parts.append(_svg_lines(curves, "time (min)", "boundary height (mm)"))
        parts.append("<p class='cap'>Linear, real time axis (spec &sect;12). Curves are "
                     "CALIBRATED on n&nbsp;=&nbsp;1 and flatten at the range ceiling, "
                     "which itself rides on the assumed packing fraction (U01).</p>")
    if area_profile is not None:
        parts.append("<h2>Cross-section profile</h2>")
        parts.append(_svg_lines(
            [("A(x)", area_profile[0], area_profile[1])],
            "depth below blood line (mm)", "area (mm²)",
        ))
        parts.append("<p class='cap'>EXACT geometry. The area roughly triples down the "
                     "column, which is what makes the level-shift error split between "
                     "the two reading methods.</p>")
    if sensitivity_curve is not None:
        parts.append("<h2>Readout sensitivity dh/dESR</h2>")
        parts.append(_svg_lines(
            [("model", sensitivity_curve[0], sensitivity_curve[1])],
            "ESR (mm/h)", "dh/dESR (mm per mm/h)",
        ))
        parts.append("<p class='cap'>The recorded sensitivity falls monotonically "
                     "(0.83, 0.79, 0.60, 0.28 at ESR 13, 20, 30, 40); the model's rises "
                     "then falls. They agree only near ESR 30 &mdash; unknown "
                     "<strong>U10</strong>, reported rather than tuned away.</p>")

    for block in blocks:
        parts.append(_html_block(block))

    parts.append("<h2>Unknowns these numbers ride on</h2>")
    for u in _involved_unknowns(blocks):
        parts.append(
            f"<div class='unknown'><h3>{esc(u.id)} &mdash; {esc(u.name)} "
            f"<span class='pill' style='background:{TIER_COLOURS['UNKNOWN']}'>"
            f"{esc(u.status)}</span></h3><p>{esc(u.desc)}</p>"
            f"<p class='resolve'><strong>Resolve by:</strong> "
            f"{esc(' '.join(u.how_to_resolve.split()))}</p></div>"
        )
    parts.append("<h2>Missing data</h2>")
    for m in missing_data():
        parts.append(
            f"<div class='unknown'><h3>{esc(m.id)} &mdash; {esc(m.name)}</h3>"
            f"<p>{esc(' '.join(m.what_it_is.split()))}</p>"
            f"<p class='resolve'><strong>Closes with:</strong> "
            f"{esc(' '.join(m.closes_with.split()))}</p></div>"
        )
    parts.append(
        f"<footer>generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by esrsim"
        "</footer></body></html>"
    )
    return "\n".join(parts)


def _tier_legend() -> str:
    items = "".join(
        f"<span class='pill' style='background:{colour}'>{name}</span>"
        for name, colour in TIER_COLOURS.items()
    )
    return (
        "<div class='legend'><strong>Provenance tiers, strongest to weakest:</strong> "
        f"{items}<p class='cap'>Every number on this page carries one. UNKNOWN carries "
        "no number at all &mdash; only the reason and the experiment that would close "
        "it.</p></div>"
    )


def _html_block(block: ResultSet) -> str:
    esc = html.escape
    rows = []
    for r in block:
        colour = TIER_COLOURS.get(r.tier.name, "#666")
        flags = "".join(f"<code>{esc(f)}</code> " for f in r.flags)
        notes = "<br>".join(esc(n) for n in r.notes)
        if r.tier is Tier.UNKNOWN:
            notes = (
                f"<em>{esc(r.why_unknown)}</em><br><strong>Resolve by:</strong> "
                f"{esc(r.experiment)}"
            ) + (f"<br>{notes}" if notes else "")
        rows.append(
            f"<tr><td class='n'>{esc(r.name)}</td>"
            f"<td class='v'>{esc(r.format_value())}</td>"
            f"<td class='u'>{esc(r.unit)}</td>"
            f"<td><span class='pill' style='background:{colour}'>{r.tier.name}</span>"
            f"</td><td class='f'>{flags}</td><td class='no'>{notes}</td></tr>"
        )
    block_colour = TIER_COLOURS.get(block.tier.name, "#666")
    notes = "".join(f"<li>{esc(n)}</li>" for n in block.notes if n)
    return (
        f"<h2>{esc(block.title)} <span class='pill' style='background:{block_colour}'>"
        f"{block.tier.name}</span></h2>"
        "<table><thead><tr><th>quantity</th><th>value</th><th>unit</th><th>tier</th>"
        "<th>flags</th><th>notes</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + (f"<ul class='notes'>{notes}</ul>" if notes else "")
    )


def _svg_lines(
    series: Sequence[tuple[str, Sequence[float], Sequence[float]]],
    x_label: str,
    y_label: str,
    width: int = 720,
    height: int = 320,
) -> str:
    """Inline SVG line chart on linear, real axes."""
    pad_l, pad_b, pad_t, pad_r = 58, 44, 16, 16
    xs = [x for _n, X, _Y in series for x in X]
    ys = [y for _n, _X, Y in series for y in Y]
    if not xs or not ys:
        return "<p class='cap'>(no data)</p>"
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(min(ys), 0.0), max(ys)
    x1 = x1 if x1 > x0 else x0 + 1.0
    y1 = y1 if y1 > y0 else y0 + 1.0

    def px(x: float) -> float:
        return pad_l + (x - x0) / (x1 - x0) * (width - pad_l - pad_r)

    def py(y: float) -> float:
        return height - pad_b - (y - y0) / (y1 - y0) * (height - pad_b - pad_t)

    colours = ["#2b6cb0", "#c05621", "#1b7f3b", "#805ad5", "#b7791f"]
    body = [
        f"<svg viewBox='0 0 {width} {height}' role='img' width='100%'>",
        f"<rect x='0' y='0' width='{width}' height='{height}' fill='#fff'/>",
    ]
    for i in range(6):
        gx = x0 + (x1 - x0) * i / 5
        gy = y0 + (y1 - y0) * i / 5
        body.append(
            f"<line x1='{px(gx):.1f}' y1='{pad_t}' x2='{px(gx):.1f}' "
            f"y2='{height - pad_b}' stroke='#eee'/>"
            f"<line x1='{pad_l}' y1='{py(gy):.1f}' x2='{width - pad_r}' "
            f"y2='{py(gy):.1f}' stroke='#eee'/>"
            f"<text x='{px(gx):.1f}' y='{height - pad_b + 16}' class='tick' "
            f"text-anchor='middle'>{gx:.4g}</text>"
            f"<text x='{pad_l - 8}' y='{py(gy) + 4:.1f}' class='tick' "
            f"text-anchor='end'>{gy:.4g}</text>"
        )
    body.append(
        f"<line x1='{pad_l}' y1='{height - pad_b}' x2='{width - pad_r}' "
        f"y2='{height - pad_b}' stroke='#333'/>"
        f"<line x1='{pad_l}' y1='{pad_t}' x2='{pad_l}' y2='{height - pad_b}' "
        "stroke='#333'/>"
    )
    for i, (name, X, Y) in enumerate(series):
        pts = " ".join(f"{px(x):.2f},{py(y):.2f}" for x, y in zip(X, Y))
        colour = colours[i % len(colours)]
        body.append(
            f"<polyline points='{pts}' fill='none' stroke='{colour}' "
            "stroke-width='2'/>"
        )
        body.append(
            f"<text x='{width - pad_r - 8}' y='{pad_t + 16 + 16 * i}' "
            f"fill='{colour}' text-anchor='end' class='tick'>"
            f"{html.escape(name)}</text>"
        )
    body.append(
        f"<text x='{(width) / 2:.0f}' y='{height - 6}' text-anchor='middle' "
        f"class='axis'>{html.escape(x_label)}</text>"
        f"<text x='14' y='{height / 2:.0f}' text-anchor='middle' class='axis' "
        f"transform='rotate(-90 14 {height / 2:.0f})'>{html.escape(y_label)}</text>"
        "</svg>"
    )
    return "".join(body)


_CSS = """<style>
:root { color-scheme: light; }
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       margin: 0 auto; max-width: 1100px; padding: 24px; color: #1a202c; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 8px; border-bottom: 2px solid #edf2f7;
     padding-bottom: 4px; }
h3 { font-size: 14px; margin: 12px 0 4px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th { text-align: left; background: #f7fafc; padding: 6px 8px; font-weight: 600;
     border-bottom: 1px solid #e2e8f0; }
td { padding: 5px 8px; border-bottom: 1px solid #f0f4f8; vertical-align: top; }
td.n { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
td.v { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
td.u { color: #718096; }
td.no { color: #4a5568; font-size: 11.5px; max-width: 420px; }
td.f code { background: #fffaf0; color: #9c4221; padding: 1px 4px; border-radius: 3px;
            font-size: 10.5px; }
.pill { color: #fff; padding: 1px 7px; border-radius: 9px; font-size: 10.5px;
        font-weight: 700; letter-spacing: .3px; margin-right: 4px;
        display: inline-block; }
.legend { background: #f7fafc; padding: 12px 14px; border-radius: 6px; margin: 12px 0; }
.disclaimer { background: #fff5f5; border-left: 4px solid #c53030; padding: 10px 14px;
              border-radius: 4px; margin: 12px 0; }
.disclaimer h2 { border: 0; margin: 0 0 6px; font-size: 15px; color: #c53030; }
.unknown { background: #fffaf0; border-left: 4px solid #b7791f; padding: 8px 14px;
           margin: 8px 0; border-radius: 4px; }
.resolve { color: #2d3748; font-size: 12.5px; }
.cap { color: #718096; font-size: 12px; margin: 6px 0 0; }
ul.notes { color: #4a5568; font-size: 12px; margin: 6px 0 0 18px; padding: 0; }
.tick { font-size: 10px; fill: #718096; }
.axis { font-size: 11px; fill: #4a5568; }
svg { border: 1px solid #e2e8f0; border-radius: 6px; }
footer { margin-top: 32px; color: #a0aec0; font-size: 11px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>"""
