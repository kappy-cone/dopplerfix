"""Diagnostic figures."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import physics as ph
from .audio import Spectrogram
from .fit import EventFit
from .synth import Scenario


def plot_scenario(sc: Scenario, spec: Spectrogram, fits: list[EventFit], path: Path) -> None:
    """Spectrogram with truth curves / ridges / fits, plus a plan view of the geometry."""
    fig = plt.figure(figsize=(16, 10), constrained_layout=True)
    gs = fig.add_gridspec(2, 3)
    ax = fig.add_subplot(gs[0, :])
    Z = spec.power_db - spec.noise_floor_db()[:, None]
    fmax = min(max(3.3 * max(s.f0_hz for s in sc.event_specs), 300), spec.f_hz[-1])
    m = spec.f_hz <= fmax
    ax.pcolormesh(spec.t_s, spec.f_hz[m], Z[m], vmin=0, vmax=35, cmap="magma", shading="auto",
                  rasterized=True)
    t_track = sc.tracks[0].t_s - sc.start_time_s
    for k, (enu, s) in enumerate(zip(sc.track_enu, sc.specs)):
        if s.role != "event":
            continue
        cur = ph.predicted_curve(t_track, enu, sc.mic_enu, sc.c)
        for h in range(1, len(sc.params.harmonic_amps) + 1):
            ax.plot(cur.t_received_s, h * s.f0_hz * cur.ratio, "--", color="cyan", lw=0.7,
                    label="truth (general model)" if (k == 0 and h == 1) else None)
    for i, e in enumerate(fits):
        ax.plot(e.ridge.t_s, e.ridge.f_hz, ".", ms=1.2, color="lime", label="ridge" if i == 0 else None)
        tt = np.linspace(e.ridge.t_s[0], e.ridge.t_s[-1], 500)
        ax.plot(tt, e.model(tt), "-", color="white", lw=1.0, label="fit" if i == 0 else None)
        ax.axvspan(e.window.start_s, e.window.end_s, color="white", alpha=0.06)
    ax.set_xlim(0, spec.t_s[-1])
    ax.set_ylim(spec.f_hz[m][0], fmax)
    ax.set_xlabel("recording time (s)")
    ax.set_ylabel("frequency (Hz)")
    ax.set_title(f"Spectrogram, excess over per-bin median (dB); SNR {sc.params.snr_db:g} dB")
    ax.legend(loc="upper right", fontsize=8)

    # Plan view.
    axm = fig.add_subplot(gs[1, 0])
    style = {"decoy": ("--", 0.9), "twin": (":", 0.7), "background": (":", 0.4)}
    for enu, s in zip(sc.track_enu, sc.specs):
        if s.role != "event":
            ls, a = style[s.role]
            win = (t_track > -60) & (t_track < sc.params.duration_s + 30)
            axm.plot(enu[win, 0] / 1000, enu[win, 1] / 1000, ls, lw=0.8, alpha=a, color="gray")
    for d in sc.decoys:
        if d.target_enu is not None:
            axm.plot(d.target_enu[0] / 1000, d.target_enu[1] / 1000, "kx", ms=8)
    ev_enu = [e for e, s in zip(sc.track_enu, sc.specs) if s.role == "event"]
    for enu, s, tru in zip(ev_enu, sc.event_specs, sc.truth):
        win = (t_track > -60) & (t_track < sc.params.duration_s + 30)
        axm.plot(enu[win, 0] / 1000, enu[win, 1] / 1000, lw=1, label=f"{s.callsign} ({s.cls})")
        i = int(np.argmin(np.abs(t_track - tru.t0_rel_s)))
        axm.plot([enu[i, 0] / 1000, sc.mic_enu[0] / 1000], [enu[i, 1] / 1000, sc.mic_enu[1] / 1000], ":k", lw=0.8)
        j = int(np.argmin(np.abs(t_track - (tru.t0_rel_s - 20))))
        axm.annotate("", xy=(enu[j + 8, 0] / 1000, enu[j + 8, 1] / 1000), xytext=(enu[j, 0] / 1000, enu[j, 1] / 1000),
                     arrowprops=dict(arrowstyle="->"))
    axm.plot(sc.mic_enu[0] / 1000, sc.mic_enu[1] / 1000, "r*", ms=14, label="hidden mic")
    lim = 1.3 * max(np.abs(sc.mic_enu[:2]).max() / 1000 + 2.0, 4.0)
    axm.set_xlim(-lim, lim)
    axm.set_ylim(-lim, lim)
    axm.set_aspect("equal")
    axm.set_xlabel("east (km)")
    axm.set_ylabel("north (km)")
    axm.set_title("Plan view (ENU about region centre)")
    axm.legend(fontsize=7, loc="lower left")

    # Truth vs fit table.
    axt = fig.add_subplot(gs[1, 1:])
    axt.axis("off")
    rows = []
    for tru, s in zip(sc.truth, sc.event_specs):
        e = min(fits, key=lambda e: abs(e.t_cpa_received_s - tru.t_cpa_received_rel_s)) if fits else None
        if e is None or abs(e.t_cpa_received_s - tru.t_cpa_received_rel_s) > 10:
            rows.append([s.callsign, f"{s.turn_rate_deg_s:+.2f}", f"{tru.v_mps:.1f}", "—", f"{tru.d_m:.0f}", "—",
                         f"{tru.t0_rel_s:.2f}", "—", "missed"])
            continue
        rows.append([
            s.callsign, f"{s.turn_rate_deg_s:+.2f}",
            f"{tru.v_mps:.1f}", f"{e.v_mps:.1f}±{e.sigma['v_mps']:.1f} ({100 * (e.v_mps / tru.v_mps - 1):+.1f}%)",
            f"{tru.d_m:.0f}", f"{e.d_m:.0f}±{e.sigma['d_m']:.0f} ({100 * (e.d_m / tru.d_m - 1):+.1f}%)",
            f"{tru.t0_rel_s:.2f}", f"{e.t0_s:.2f}±{e.sigma['t0_s']:.2f} ({e.t0_s - tru.t0_rel_s:+.2f})",
            f"{e.rms_hz:.2f} Hz",
        ])
    tab = axt.table(cellText=rows, loc="center",
                    colLabels=["event", "turn °/s", "v true", "v fit (m/s)", "d true", "d fit (m)", "t0 true", "t0 fit (s)", "rms"],
                    colWidths=[0.07, 0.07, 0.07, 0.19, 0.07, 0.19, 0.08, 0.19, 0.07])
    tab.auto_set_font_size(False)
    tab.set_fontsize(8)
    tab.scale(1, 1.6)
    axt.set_title("Per-event fit vs truth (t0 = emission CPA time, recording-relative)")
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Per-run figures (milestone 4; scoring-dependent parts stubbed until milestone 5)
# ---------------------------------------------------------------------------
STAGE_LABELS = {"grid": "grid (in region)", "cpa": "CPA prefilter", "offset": "offset (shared Δt)",
                "silence": "silence"}


def plot_funnel(counts: dict, path: Path, title: str = "") -> None:
    """Cascade survivor funnel (log scale)."""
    names = [k for k in STAGE_LABELS if k in counts]
    vals = [max(int(counts[k]), 0) for k in names]
    fig, ax = plt.subplots(figsize=(7, 3.6), constrained_layout=True)
    y = np.arange(len(names))[::-1]
    ax.barh(y, [max(v, 0.8) for v in vals], color=["#9aa5b1", "#4c78a8", "#f58518", "#54a24b"][:len(vals)])
    for yy, v in zip(y, vals):
        ax.text(max(v, 1) * 1.15, yy, f"{v:,}", va="center", fontsize=9)
    ax.set_yticks(y, [STAGE_LABELS[n] for n in names])
    ax.set_xscale("log")
    ax.set_xlim(0.8, max(max(vals), 1) * 8)
    ax.set_xlabel("cells surviving")
    ax.set_title(title or "Cascade survivors per stage")
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_candidates_spectrogram(spec: Spectrogram, events, candidates: list[dict], path: Path,
                                top: int = 3, truth_t_s: list[float] | None = None) -> None:
    """Display spectrogram with event ridges and each top candidate's modeled curves (general model).

    Pre-scoring (milestone 4): curves use the characterization f0 and the
    candidate's cascade Δt (midpoint of its feasible interval).
    """
    fig, ax = plt.subplots(figsize=(14, 5.5), constrained_layout=True)
    ax.pcolormesh(spec.t_s, spec.f_hz, spec.power_db, vmin=0, vmax=30, cmap="magma", shading="auto", rasterized=True)
    for k, e in enumerate(events):
        ax.plot(e.ridge_t_s, e.ridge_f_hz, ".", ms=1.0, color="lime", label="ridge" if k == 0 else None)
    colors = ["cyan", "white", "orange", "violet", "yellow"]
    for n, cand in enumerate(candidates[:top]):
        for k, cur in enumerate(cand["curves"]):
            if cur is None:
                continue
            ax.plot(cur["t_rel_s"], cur["f0_hz"] * np.asarray(cur["ratio"]), "--", lw=1.0, color=colors[n % 5],
                    label=f"#{cand['interim_rank']} {cand['label']}" if k == 0 else None)
    for t in truth_t_s or []:
        ax.axvline(t, color="red", lw=0.6, alpha=0.6)
    ax.set_xlim(spec.t_s[0], spec.t_s[-1])
    ax.set_ylim(spec.f_hz[0], spec.f_hz[-1])
    ax.set_xlabel("recording time (s)")
    ax.set_ylabel("frequency (Hz)")
    ax.set_title("Ridges and modeled curves of the top candidates (interim cascade ranking; "
                 "f0 from the per-event fit — locked scoring is milestone 5)")
    ax.legend(loc="upper right", fontsize=8)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_score_histogram(values: dict[str, np.ndarray], path: Path, xlabel: str) -> None:
    fig, ax = plt.subplots(figsize=(7, 3.6), constrained_layout=True)
    allv = np.concatenate([np.asarray(v, float) for v in values.values()]) if values else np.zeros(0)
    bins = np.linspace(0, max(float(np.nanmax(allv)) if allv.size else 1.0, 1e-3), 30)
    for (name, v), col in zip(values.items(), ["#4c78a8", "#54a24b", "#f58518"]):
        ax.hist(np.asarray(v, float), bins=bins, alpha=0.6, label=f"{name} (n={len(v)})", color=col)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("cells")
    ax.legend(fontsize=8)
    fig.savefig(path, dpi=110)
    plt.close(fig)


VIEWER_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Doppler run __RUN_ID__</title>
<script src="__PLOTLY__"></script>
<style>body{font-family:system-ui,sans-serif;margin:12px;background:#111;color:#ddd}
.ctl{display:flex;flex-wrap:wrap;gap:16px;align-items:center;margin:8px 0}
label{font-size:13px} input[type=range]{width:220px} select{font-size:13px}
.note{font-size:12px;color:#aaa} code{color:#9cf}</style></head>
<body>
<h3 style="margin:4px 0">Run <code>__RUN_ID__</code></h3>
<div class="note">Spectrogram downsampled for display only. Modeled curves: general track model at the candidate's
cascade Δt with the per-event fit f0. <b>Scores, locked Δt and per-event f0 fits arrive in milestone 5</b>
(score field shows the interim cascade-consistency value).</div>
<div class="ctl"><label>Candidate <select id="cand"></select></label><span id="info" class="note"></span></div>
<div class="ctl" id="sliders"></div>
<div id="plot" style="width:100%;height:620px"></div>
<script>
const D = __DATA__;
const MAGMA=[[0,'#000004'],[0.13,'#1c1044'],[0.25,'#4f127b'],[0.38,'#812581'],[0.5,'#b5367a'],[0.63,'#e55064'],[0.75,'#fb8761'],[0.88,'#fec287'],[1,'#fcfdbf']];
function b64u8(s){const b=atob(s),a=new Uint8Array(b.length);for(let i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a;}
const Z8=b64u8(D.spec.z_b64), nt=D.spec.t.length, nf=D.spec.f.length, z=[];
for(let i=0;i<nf;i++){const row=new Array(nt);for(let j=0;j<nt;j++)row[j]=Z8[i*nt+j]*D.spec.db_step;z.push(row);}
const sel=document.getElementById('cand');
D.candidates.forEach((c,i)=>{const o=document.createElement('option');o.value=i;
 o.text=`#${c.interim_rank}  ${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}  (interim ${c.interim_cost.toFixed(3)})`;sel.appendChild(o);});
let state={};
function sliders(c){const box=document.getElementById('sliders');box.innerHTML='';
 state={dt:c.dt_s,f0:c.curves.map(k=>k?k.f0_hz:null)};
 const add=(lbl,min,max,step,val,fn)=>{const l=document.createElement('label');const s=document.createElement('input');
  s.type='range';s.min=min;s.max=max;s.step=step;s.value=val;const v=document.createElement('span');v.textContent=' '+(+val).toFixed(2);
  s.oninput=()=>{v.textContent=' '+(+s.value).toFixed(2);fn(+s.value);draw();};l.append(lbl+' ',s,v);box.appendChild(l);};
 add('Δt (s)',c.dt_lo_s,c.dt_hi_s,0.01,c.dt_s,x=>state.dt=x);
 c.curves.forEach((k,e)=>{if(!k)return;add(`f0 event ${e} (Hz)`,(k.f0_hz*0.95).toFixed(2),(k.f0_hz*1.05).toFixed(2),0.01,k.f0_hz,x=>state.f0[e]=x);});}
function draw(){const c=D.candidates[+sel.value];
 const tr=[{type:'heatmap',x:D.spec.t,y:D.spec.f,z:z,colorscale:MAGMA,zmin:0,zmax:30,showscale:false,hoverinfo:'skip'}];
 D.events.forEach((e,k)=>tr.push({type:'scattergl',mode:'markers',x:e.t,y:e.f,marker:{size:2,color:'lime'},name:`ridge ${k}`}));
 c.curves.forEach((k,e)=>{if(!k)return;const sh=state.dt-c.dt_s;
  tr.push({type:'scatter',mode:'lines',x:k.t_rel_s.map(t=>t-sh),y:k.ratio.map(r=>r*state.f0[e]),line:{color:'cyan',dash:'dash',width:1.5},name:`model ev ${e} (${k.icao24})`});});
 Plotly.react('plot',tr,{paper_bgcolor:'#111',plot_bgcolor:'#111',font:{color:'#ddd'},margin:{l:60,r:10,t:10,b:40},
  xaxis:{title:'recording time (s)'},yaxis:{title:'frequency (Hz)'},legend:{x:1,xanchor:'right',y:1}});
 document.getElementById('info').textContent=`cell ${c.cell_id}  tracks ${c.curves.map(k=>k?k.icao24:'–').join(', ')}  `+
  `Δt ∈ [${c.dt_lo_s.toFixed(2)}, ${c.dt_hi_s.toFixed(2)}] s  score: ${c.score===null?'— (milestone 5)':c.score}`;}
sel.onchange=()=>{sliders(D.candidates[+sel.value]);draw();};
if(D.candidates.length){sliders(D.candidates[0]);draw();}else{document.getElementById('info').textContent='no candidates';draw&&Plotly.newPlot('plot',[{type:'heatmap',x:D.spec.t,y:D.spec.f,z:z,colorscale:MAGMA,zmin:0,zmax:30}]);}
</script></body></html>
"""


def write_viewer(path: Path, run_id: str, spec: Spectrogram, events, candidates: list[dict],
                 plotly_rel: str = "../plotly.min.js", db_max: float = 40.0) -> None:
    """Offline viewer: references one shared plotly.min.js; spectrogram stored as base64 uint8."""
    import base64

    step = db_max / 255.0
    z8 = np.clip(np.round(np.nan_to_num(spec.power_db, nan=0.0) / step), 0, 255).astype(np.uint8)
    data = {
        "spec": {"t": np.round(spec.t_s, 2).tolist(), "f": np.round(spec.f_hz, 1).tolist(), "db_step": step,
                 "z_b64": base64.b64encode(z8.tobytes()).decode()},
        "events": [{"t": np.round(e.ridge_t_s[::2], 2).tolist(), "f": np.round(e.ridge_f_hz[::2], 2).tolist()}
                   for e in events],
        "candidates": [{**{k: c[k] for k in ("cell_id", "lat", "lon", "dt_s", "dt_lo_s", "dt_hi_s", "interim_cost",
                                            "interim_rank")},
                        "score": None,
                        "curves": [None if k is None else {"icao24": k["icao24"], "f0_hz": float(k["f0_hz"]),
                                                           "t_rel_s": np.round(k["t_rel_s"], 3).tolist(),
                                                           "ratio": np.round(k["ratio"], 6).tolist()}
                                   for k in c["curves"]]} for c in candidates],
    }
    html = (VIEWER_TEMPLATE.replace("__RUN_ID__", run_id).replace("__PLOTLY__", plotly_rel)
            .replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    Path(path).write_text(html)
