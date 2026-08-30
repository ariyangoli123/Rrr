"""The single-page UI served by ``esrsim serve``.

One module-level string. No build step, no bundler, no CDN — the page works with the
network unplugged, which matters because the whole point of the server is that it runs
on your bench.

The JavaScript here does **no physics**. It fetches JSON from the Python API and
renders it. Every value it displays arrives with a tier already attached, and the
renderer refuses to draw a row without one.
"""

from __future__ import annotations

__all__ = ["PAGE"]

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>esrsim — ESR device design</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{
  --bg:#f6f7f9; --panel:#fff; --ink:#1a202c; --muted:#718096; --line:#e2e8f0;
  --EXACT:#1b7f3b; --CALIBRATED:#2b6cb0; --EXTRAPOLATED:#b7791f; --ESTIMATED:#805ad5;
  --HYPOTHESIS:#c05621; --RESEARCH_ONLY:#718096; --UNKNOWN:#c53030;
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--bg);color:var(--ink)}
header{background:#fff;border-bottom:1px solid var(--line);padding:12px 20px;
  position:sticky;top:0;z-index:20}
header h1{margin:0;font-size:17px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
header .ver{color:var(--muted);font-weight:400;font-size:12px}
.warn{background:#fff5f5;border-left:4px solid var(--UNKNOWN);padding:8px 12px;
  margin:8px 20px;border-radius:4px;font-size:12.5px;color:#742a2a}
.layout{display:flex;gap:16px;padding:16px 20px;align-items:flex-start;flex-wrap:wrap}
aside{flex:0 0 290px;position:sticky;top:64px}
main{flex:1;min-width:420px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:14px;margin-bottom:14px}
.card h2{margin:0 0 10px;font-size:14px;letter-spacing:.02em;text-transform:uppercase;
  color:var(--muted)}
label{display:block;font-size:12px;color:var(--muted);margin:10px 0 3px}
select,input[type=number]{width:100%;padding:6px 8px;border:1px solid var(--line);
  border-radius:5px;font:inherit;background:#fff}
input[type=range]{width:100%}
.row{display:flex;gap:8px;align-items:center}
.row output{font-variant-numeric:tabular-nums;font-weight:600;min-width:64px;
  text-align:right}
nav{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:12px}
nav button{border:1px solid var(--line);background:#fff;padding:7px 12px;
  border-radius:6px;cursor:pointer;font:inherit}
nav button[aria-selected=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{text-align:left;background:#f7fafc;padding:6px 8px;border-bottom:1px solid var(--line);
  font-weight:600;position:sticky;top:0}
td{padding:5px 8px;border-bottom:1px solid #f0f4f8;vertical-align:top}
td.n{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
td.v{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
td.u{color:var(--muted)}
td.no{color:#4a5568;font-size:11.5px;max-width:400px}
.pill{color:#fff;padding:1px 7px;border-radius:9px;font-size:10.5px;font-weight:700;
  display:inline-block;white-space:nowrap}
.flag{background:#fffaf0;color:#9c4221;padding:1px 5px;border-radius:3px;
  font-size:10px;font-family:ui-monospace,monospace;margin-right:3px;
  display:inline-block}
.unk{background:#fff5f5;border-left:3px solid var(--UNKNOWN);padding:6px 10px;
  margin:4px 0;border-radius:3px;font-size:12px}
.unk b{color:#742a2a}
.legend{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px}
.note{color:var(--muted);font-size:12px;margin:6px 0 0;padding-left:14px}
svg{background:#fff;border:1px solid var(--line);border-radius:6px;width:100%}
.tick{font-size:10px;fill:var(--muted)}
.axis{font-size:11px;fill:#4a5568}
.err{background:#fff5f5;color:#742a2a;padding:10px;border-radius:6px}
.loading{color:var(--muted);padding:20px;text-align:center}
.scroll{overflow-x:auto}
details{margin-top:6px}
summary{cursor:pointer;color:var(--muted);font-size:12px}
</style></head><body>

<header>
  <h1>esrsim <span class="ver" id="ver"></span>
    <span class="pill" style="background:var(--UNKNOWN)">NOT A SIMULATOR</span></h1>
</header>
<div class="warn" id="disclaimer"></div>

<div class="layout">
<aside>
  <div class="card">
    <h2>Geometry</h2>
    <label>Tube</label>
    <select id="tube"></select>
    <div id="customBox" hidden>
      <label>Gap at blood line <span id="gapV"></span> mm</label>
      <input type="range" id="gap" min="0.30" max="1.60" step="0.01" value="0.70">
      <label>Column volume <span id="volV"></span> mm³</label>
      <input type="range" id="volume" min="800" max="4000" step="50" value="2000">
      <label>Column length <span id="lenV"></span> mm</label>
      <input type="range" id="length" min="20" max="120" step="1" value="50">
      <p class="note">θ is solved so the volume target is met, exactly as the six
      library tubes were cut.</p>
    </div>
    <label>Blood-line convention</label>
    <select id="generation">
      <option value="B">Gen-B — blood line is the mouth (current)</option>
      <option value="A">Gen-A — 5.000 mm mouth, 3 mm above (superseded)</option>
    </select>
    <label>Step width w <span id="stepV"></span> mm</label>
    <input type="range" id="step_w" min="0.05" max="1.30" step="0.01" value="0.30">
  </div>

  <div class="card">
    <h2>Sample &amp; readout</h2>
    <label>Fluid</label><select id="fluid"></select>
    <label>Haematocrit <span id="hctV"></span></label>
    <input type="range" id="hct" min="0.20" max="0.70" step="0.01" value="0.45">
    <label>φ_pack <span id="phiV"></span> — ASSUMED, unknown U01</label>
    <input type="range" id="phi_pack" min="0.80" max="0.98" step="0.01" value="0.90">
    <label>ESR (mm/h) <span id="esrV"></span></label>
    <input type="range" id="esr" min="2" max="120" step="1" value="30">
    <label>Readout time (min) <span id="roV"></span></label>
    <input type="range" id="readout" min="2" max="60" step="1" value="15">
    <label>Readout mode</label>
    <select id="mode">
      <option value="FIXED_TIME_HEIGHT">Height at a fixed time</option>
      <option value="TIME_TO_THRESHOLD">Time to cross a threshold</option>
      <option value="CONDITIONAL">Two-stage conditional</option>
      <option value="DELTA_H">Δh over a window (rejected — shown to fail)</option>
    </select>
  </div>

  <div class="card">
    <h2>Provenance tiers</h2>
    <div class="legend" id="legend"></div>
    <p class="note">Every number carries one. UNKNOWN carries no number at all — only
    the reason and the experiment that closes it.</p>
  </div>
</aside>

<main>
  <nav id="tabs"></nav>
  <div id="view"><div class="loading">loading…</div></div>
</main>
</div>

<script>
const TABS = [
  ["compare","Compare tubes"], ["geometry","Geometry"], ["charts","Charts"],
  ["kinetics","Kinetics"], ["capillary","Capillary & mixing"],
  ["readout","Readout & error"], ["validate","ICSH feasibility"],
  ["benchmark","Benchmark"], ["rules","Design rules"], ["sweep","Sweep"],
  ["continuum","Continuum (research)"], ["unknowns","Open questions"],
];
let active = "compare", meta = null;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function params(){
  const p = new URLSearchParams();
  const tube = $("tube").value;
  p.set("tube", tube);
  if (tube === "custom"){
    p.set("gap", $("gap").value);
    p.set("volume", $("volume").value);
    p.set("length", $("length").value);
  }
  for (const k of ["generation","fluid","hct","phi_pack","esr","readout","step_w","mode"])
    p.set(k, $(k).value);
  return p.toString();
}

async function api(path, extra){
  const r = await fetch(`/api/${path}?${params()}${extra ? "&"+extra : ""}`);
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

/* ---- rendering. Nothing renders without a tier. ---- */
function pill(t){ return `<span class="pill" style="background:var(--${t})">${t}</span>`; }

function resultRow(r){
  if (!r.tier) throw new Error("untagged value reached the UI: " + r.name);
  const flags = (r.flags||[]).map(f=>`<span class="flag">${esc(f)}</span>`).join("");
  let notes = (r.notes||[]).map(esc).join("<br>");
  if (r.tier === "UNKNOWN")
    notes = `<div class="unk"><b>Why:</b> ${esc(r.why_unknown)}<br>
             <b>Resolve by:</b> ${esc(r.experiment)}</div>` + notes;
  const v = r.value === null || r.value === undefined ? "—"
    : (typeof r.value === "number" ? (Number.isInteger(r.value) ? r.value
        : r.value.toPrecision(5).replace(/\.?0+$/,""))
      : (typeof r.value === "boolean" ? (r.value ? "yes" : "no") : esc(r.value)));
  return `<tr><td class="n">${esc(r.name)}</td><td class="v">${v}</td>
    <td class="u">${esc(r.unit||"")}</td><td>${pill(r.tier)}</td>
    <td>${flags}</td><td class="no">${notes}</td></tr>`;
}

function blockHtml(b){
  const notes = (b.notes||[]).filter(Boolean)
    .map(n=>`<p class="note">• ${esc(n)}</p>`).join("");
  return `<div class="card"><h2>${esc(b.title)} ${pill(b.tier)}</h2>
    <div class="scroll"><table><thead><tr><th>quantity</th><th>value</th><th>unit</th>
    <th>tier</th><th>flags</th><th>notes</th></tr></thead>
    <tbody>${b.results.map(resultRow).join("")}</tbody></table></div>${notes}</div>`;
}

function chart(spec){
  const W=760,H=300,L=62,B=44,T=14,R=16;
  const xs=spec.series.flatMap(s=>s.x), ys=spec.series.flatMap(s=>s.y).filter(v=>v!=null);
  if(!xs.length||!ys.length) return "<p class='note'>(no data)</p>";
  let x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(0,...ys),y1=Math.max(...ys);
  if(x1<=x0)x1=x0+1; if(y1<=y0)y1=y0+1;
  const px=x=>L+(x-x0)/(x1-x0)*(W-L-R), py=y=>H-B-(y-y0)/(y1-y0)*(H-B-T);
  const cols=["#2b6cb0","#c05621","#1b7f3b","#805ad5"];
  let g="";
  for(let i=0;i<6;i++){
    const gx=x0+(x1-x0)*i/5, gy=y0+(y1-y0)*i/5;
    g+=`<line x1="${px(gx)}" y1="${T}" x2="${px(gx)}" y2="${H-B}" stroke="#eee"/>
        <line x1="${L}" y1="${py(gy)}" x2="${W-R}" y2="${py(gy)}" stroke="#eee"/>
        <text class="tick" x="${px(gx)}" y="${H-B+15}" text-anchor="middle">
          ${(+gx.toPrecision(4))}</text>
        <text class="tick" x="${L-8}" y="${py(gy)+4}" text-anchor="end">
          ${(+gy.toPrecision(4))}</text>`;
  }
  spec.series.forEach((s,i)=>{
    const pts=s.x.map((x,j)=>s.y[j]==null?null:`${px(x).toFixed(1)},${py(s.y[j]).toFixed(1)}`)
      .filter(Boolean).join(" ");
    g+=`<polyline points="${pts}" fill="none" stroke="${cols[i%4]}" stroke-width="2"/>
        <text class="tick" x="${W-R-8}" y="${T+16+16*i}" fill="${cols[i%4]}"
          text-anchor="end">${esc(s.name)}</text>`;
  });
  g+=`<line x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}" stroke="#333"/>
      <line x1="${L}" y1="${T}" x2="${L}" y2="${H-B}" stroke="#333"/>
      <text class="axis" x="${W/2}" y="${H-6}" text-anchor="middle">${esc(spec.x_label)}</text>
      <text class="axis" x="14" y="${H/2}" text-anchor="middle"
        transform="rotate(-90 14 ${H/2})">${esc(spec.y_label)}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img">${g}</svg>
    <p class="note">${pill(spec.tier)} ${esc(spec.note||"")}</p>`;
}

function gridTable(rows, cols){
  const head = cols.map(c=>`<th>${esc(c[0])}</th>`).join("");
  const body = rows.map(r=>{
    const byName = Object.fromEntries(r.results.map(x=>[x.name,x]));
    const cells = cols.map(c=>{
      const res = byName[c[1]];
      if(!res) return "<td class='v'>—</td>";
      const v = res.value===null ? "?" : (typeof res.value==="number"
        ? res.value.toFixed(c[2]??3)
        : (typeof res.value==="boolean" ? (res.value?"yes":"no") : esc(res.value)));
      const colour = res.tier==="UNKNOWN" ? "color:var(--UNKNOWN);font-weight:700" : "";
      return `<td class="v" style="${colour}">${v}</td>`;
    }).join("");
    return `<tr><td class="n">${esc(r.label)}</td>${cells}<td>${pill(r.tier)}</td></tr>`;
  }).join("");
  return `<div class="scroll"><table><thead><tr><th>design</th>${head}<th>tier</th>
    </tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">“?” means UNDECIDABLE on the present evidence — that is NOT a pass.</p>`;
}

const GRID_COLS = [
  ["gap (mm)","gap",3],["θ (°)","theta",3],["V (mm³)","volume",0],
  ["clearance","clearance",4],["E","speed_E",2],["range (mm)","range_ceiling",2],
  ["uneven (mm)","bloodline_unevenness_worst",2],["fill (×)","fill_resistance",2],
  ["mixing","mixing",0],["ICSH","icsh_feasible",0],
];

async function render(){
  const view = $("view");
  view.innerHTML = "<div class='loading'>computing…</div>";
  try{
    if (active === "charts"){
      const c = await api("curves");
      const titles = {
        sedimentation: "Boundary height against time",
        area: "Cross-section profile down the column",
        sensitivity: "Readout sensitivity dh/dESR — model vs record",
      };
      view.innerHTML = ["sedimentation","area","sensitivity"].map(k =>
        `<div class="card"><h2>${titles[k]}</h2>${chart(c[k])}</div>`).join("");
    } else if (active === "compare"){
      const d = await api("compare");
      view.innerHTML = `<div class="card"><h2>All six library tubes</h2>
        ${gridTable(d.rows, GRID_COLS)}</div>`;
    } else if (active === "sweep"){
      const p = $("sweepParam") ? $("sweepParam").value : "gap";
      const ranges = {gap:[0.4,1.5], theta:[8,20], volume:[1200,3000], length:[30,80]};
      const [lo,hi] = ranges[p];
      const d = await api("sweep", `param=${p}&lo=${lo}&hi=${hi}&steps=9`);
      view.innerHTML = `<div class="card"><h2>Sweep</h2>
        <label>Parameter</label>
        <select id="sweepParam" style="max-width:220px">
        ${["gap","theta","volume","length"].map(x=>
          `<option ${x===p?"selected":""}>${x}</option>`).join("")}</select>
        ${gridTable(d.rows, GRID_COLS)}</div>`;
      $("sweepParam").onchange = render;
    } else if (active === "unknowns"){
      const d = await api("unknowns");
      view.innerHTML = `<div class="card"><h2>Unknowns — ${d.unknowns.length}</h2>
        ${d.unknowns.map(u=>`<div class="unk"><b>${esc(u.id)} — ${esc(u.name)}</b>
          ${pill("UNKNOWN")} <i>${esc(u.status)}</i><br>${esc(u.desc)}<br>
          <b>Resolve by:</b> ${esc(u.how_to_resolve)}
          ${u.note?`<details><summary>note</summary>${esc(u.note)}</details>`:""}
          </div>`).join("")}</div>
        <div class="card"><h2>Missing data — ${d.missing.length}</h2>
        ${d.missing.map(m=>`<div class="unk"><b>${esc(m.id)} — ${esc(m.name)}</b><br>
          ${esc(m.what_it_is)}<br><b>Closes with:</b> ${esc(m.closes_with)}</div>`)
          .join("")}</div>`;
    } else {
      const d = await api(active);
      view.innerHTML = d.blocks.map(blockHtml).join("");
    }
  } catch(e){
    view.innerHTML = `<div class="card err"><b>Error:</b> ${esc(e.message)}</div>`;
  }
}

function syncLabels(){
  $("gapV").textContent = (+$("gap").value).toFixed(2);
  $("volV").textContent = $("volume").value;
  $("lenV").textContent = $("length").value;
  $("hctV").textContent = (+$("hct").value).toFixed(2);
  $("phiV").textContent = (+$("phi_pack").value).toFixed(2);
  $("esrV").textContent = $("esr").value;
  $("roV").textContent = $("readout").value;
  $("stepV").textContent = (+$("step_w").value).toFixed(2);
  $("customBox").hidden = $("tube").value !== "custom";
}

let timer = null;
function changed(){ syncLabels(); clearTimeout(timer); timer = setTimeout(render, 220); }

(async function init(){
  meta = await (await fetch("/api/meta")).json();
  $("ver").textContent = "v" + meta.version;
  $("disclaimer").innerHTML = "<b>What this tool cannot do.</b> " + esc(meta.disclaimer);
  $("legend").innerHTML = meta.tiers.map(pill).join(" ");
  $("tube").innerHTML = meta.tubes.map(t =>
    `<option value="${t.id}">${t.id} — gap ${t.gap.toFixed(3)} mm, ${t.theta}°,
     ${t.volume} mm³</option>`).join("") +
    `<option value="custom">custom…</option>`;
  $("tube").value = "T070";
  $("fluid").innerHTML = meta.fluids.map(f =>
    `<option value="${f.id}">${esc(f.label)}</option>`).join("");
  // Match the CLI/API default. Left to the list order this would select water, and
  // the UI would quietly show water numbers for a blood instrument.
  $("fluid").value = "blood_fresh";
  $("tabs").innerHTML = TABS.map(([k,l]) =>
    `<button data-k="${k}" aria-selected="${k===active}">${l}</button>`).join("");
  $("tabs").onclick = e => {
    const k = e.target.dataset && e.target.dataset.k;
    if(!k) return;
    active = k;
    [...$("tabs").children].forEach(b =>
      b.setAttribute("aria-selected", b.dataset.k === k));
    render();
  };
  document.querySelectorAll("aside select, aside input")
    .forEach(el => el.addEventListener("input", changed));
  syncLabels();
  render();
})();
</script></body></html>
"""
