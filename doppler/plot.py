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
    style = {"decoy": ("--", 0.9), "twin": (":", 0.7), "background": (":", 0.4), "near": ("-.", 0.8)}
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

    Curves use each candidate's locked-score Δt and per-event f0.
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
            rat = np.array([np.nan if r is None else r for r in cur["ratio"]], float)
            tt = cur["t0_s"] + cur["step_s"] * np.arange(len(rat))
            ax.plot(tt, cur["f0_hz"] * rat, "--", lw=1.0, color=colors[n % 5],
                    label=f"#{cand['rank']} {cand['label']} (score {cand['score']:.2f})" if k == 0 else None)
    for t in truth_t_s or []:
        ax.axvline(t, color="red", lw=0.6, alpha=0.6)
    ax.set_xlim(spec.t_s[0], spec.t_s[-1])
    ax.set_ylim(spec.f_hz[0], spec.f_hz[-1])
    ax.set_xlabel("recording time (s)")
    ax.set_ylabel("frequency (Hz)")
    ax.set_title("Ridges and modeled curves of the top candidates (locked N-event score: shared Δt, f0 per event)")
    ax.legend(loc="upper right", fontsize=8)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_score_histogram(values: dict[str, np.ndarray], path: Path, xlabel: str,
                         true_score: float | None = None) -> None:
    fig, ax = plt.subplots(figsize=(7, 3.6), constrained_layout=True)
    allv = np.concatenate([np.asarray(v, float) for v in values.values()]) if values else np.zeros(0)
    bins = np.linspace(0, max(float(np.nanmax(allv)) if allv.size else 1.0, 1e-3), 30)
    for (name, v), col in zip(values.items(), ["#4c78a8", "#54a24b", "#f58518"]):
        ax.hist(np.asarray(v, float), bins=bins, alpha=0.6, label=f"{name} (n={len(v)})", color=col)
    if true_score is not None:
        ax.axvline(true_score, color="red", lw=1.2, label="true cell")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("cells")
    ax.legend(fontsize=8)
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# viewer.html: candidate comparison panels + per-event fit mode (read-only diagnostics)
# ---------------------------------------------------------------------------
def _nan_list(a, nd: int) -> list:
    a = np.asarray(a, float)
    return [None if not np.isfinite(v) else round(float(v), nd) for v in a]


def candidate_view(d: dict, events, ts, prior, c: float, spec=None, step_s: float = 0.25) -> dict:
    """Everything the viewer needs for one candidate: per-event modeled ratio on a received-time grid
    (at the candidate's scored Δt) wide enough for the Δt slider, plus its CPA geometry readouts."""
    from . import physics as ph
    from .geo import ecef_to_geodetic
    from .score import modeled_ratio

    span = max(abs(d["dt_lo_s"] - d["dt_s"]), abs(d["dt_hi_s"] - d["dt_s"])) + 2.0
    curves = []
    for e, (ev, k_, cp) in enumerate(zip(events, d["tracks"], d["cpa"])):
        if k_ < 0 or cp is None:
            curves.append(None)       # event left unmatched by this candidate
            continue
        tt = np.arange(ev.ridge_t_s.min() - 30 - span, ev.ridge_t_s.max() + 30 + span, step_s)
        ratio, _, _, ok = modeled_ratio(d.get("xyz_est", d["xyz"]), k_, ts, prior.start_s + d["dt_s"] + tt, c)
        f0, v, dist = float(d["f0"][e]), float(cp["speed_mps"]), float(cp["r_m"])
        fpre, fpost = ph.straight_asymptotes(f0, v, c)
        _, _, alt = ecef_to_geodetic(cp["pos"])
        curves.append({
            "icao24": str(ts.icao24[k_]), "callsign": str(ts.callsign[k_]), "f0_hz": f0,
            "t0_s": float(tt[0]), "step_s": step_s, "ratio": _nan_list(np.where(ok, ratio, np.nan), 7),
            "rms_hz": float(d["rms"][e]), "n_missing": int(d["n_missing"][e]),
            "geom": {"v_mps": v, "d_m": dist, "t0_rel_s": cp["t_emit_s"] - prior.start_s - d["dt_s"],
                     "t_rx_rel_s": cp["t_emit_s"] + dist / c - prior.start_s - d["dt_s"], "alt_m": float(alt),
                     "f_pre_hz": fpre, "f_post_hz": fpost, "max_roc_hz_s": ph.straight_max_rate(f0, v, dist, c)},
        })
    return {"cell_id": int(d["cell_id"]), "rank": int(d["rank"]), "score": float(d["score"]),
            "interim_rank": int(d["interim_rank"]), "lat": float(d.get("est_lat", d["lat"])),
            "lon": float(d.get("est_lon", d["lon"])),
            "dt_s": float(d["dt_s"]), "dt_lo_s": float(d["dt_lo_s"]), "dt_hi_s": float(d["dt_hi_s"]),
            "label": f"{d['lat']:.4f},{d['lon']:.4f}", "curves": curves}


def event_window_spectrogram(x, fs: float, t_lo: float, t_hi: float, f_lo: float, f_hi: float,
                             max_t: int = 1000, max_f: int = 300, floor_db: float = 60.0):
    """Display-only spectrogram of one event window: dB re the window maximum, clipped to −floor_db, uint8."""
    from .audio import SpectrogramParams, compute_spectrogram

    afs = float(min(fs, max(4.0 * f_hi, 500.0)))
    spec = compute_spectrogram(x, fs, SpectrogramParams(afs, 1.0, 0.75, min(f_hi, afs / 2)), (t_lo, t_hi))
    m = spec.f_hz >= f_lo
    P, f, t = spec.power_db[m], spec.f_hz[m], spec.t_s
    gt, gf = int(np.ceil(P.shape[1] / max_t)), int(np.ceil(P.shape[0] / max_f))
    if gt > 1:
        n = (P.shape[1] // gt) * gt
        P, t = P[:, :n].reshape(P.shape[0], -1, gt).max(-1), t[:n].reshape(-1, gt).mean(-1)
    if gf > 1:
        n = (P.shape[0] // gf) * gf
        P, f = P[:n].reshape(-1, gf, P.shape[1]).max(1), f[:n].reshape(-1, gf).mean(-1)
    db = np.clip(P - P.max(), -floor_db, 0.0)
    z8 = np.round((db + floor_db) / floor_db * 255).astype(np.uint8)
    return t, f, z8, floor_db


def write_viewer(path: Path, run_id: str, x, fs: float, start_s: float, events, fits, candidates: list[dict],
                 plotly_rel: str = "../plotly.min.js", viewer_cfg: dict | None = None,
                 score_params=None, c: float = 343.4) -> None:
    """Offline viewer (references one shared plotly.min.js). Two read-only modes:

    * Compare: one panel per event; the selected candidate's modeled curve with one shared Δt slider and an
      f0 slider per event; recording-fit vs candidate-geometry readouts; live score using the scorer's
      formula (pooled Huber, ridge-scatter units, unmodelable points penalised); snapshot log.
    * Event fit: free f0, v, d, t0 straight-line fit per event with f_pre / f_post / max RoC and a snapshot log.
    """
    import base64

    from .score import ScoreParams, huber, prepare_ridges

    vc = viewer_cfg or {}
    sp = score_params or ScoreParams()
    ridges = prepare_ridges(events, sp)
    dur = len(x) / fs
    evs = []
    for e, (ev, ft, rd) in enumerate(zip(events, fits, ridges)):
        t_lo, t_hi = max(0.0, float(ev.ridge_t_s.min()) - 20), min(dur, float(ev.ridge_t_s.max()) + 20)
        f_lo, f_hi = 0.85 * float(ev.ridge_f_hz.min()), 1.15 * float(ev.ridge_f_hz.max())
        t, f, z8, floor_db = event_window_spectrogram(x, fs, t_lo, t_hi, f_lo, f_hi,
                                                      int(vc.get("event_time_px", 1000)),
                                                      int(vc.get("event_freq_px", 300)))
        sg = ft.sigma
        evs.append({
            "idx": e, "cls": ["prop", "jet", "unknown"][ev.cls],
            "spec": {"t": np.round(t, 3).tolist(), "f": np.round(f, 2).tolist(), "floor_db": floor_db,
                     "z_b64": base64.b64encode(z8.tobytes()).decode()},
            "ridge": {"t": np.round(rd.t_s, 3).tolist(), "f": np.round(rd.f_hz, 4).tolist(), "sigma": rd.sigma_hz},
            "fit": {"f0_hz": ft.f0_hz, "v_mps": ft.v_mps, "d_m": ft.d_m, "t0_s": ft.t0_s,
                    "t_rx_s": ft.t_cpa_received_s, "sigma": {k: float(v) for k, v in sg.items()},
                    "f_pre_hz": ft.f_pre_hz, "f_post_hz": ft.f_post_hz,
                    "max_roc_hz_s": float(ft.f0_hz * ft.v_mps ** 2 / (c * ft.d_m))},
        })
    data = {"run_id": run_id, "start_utc_s": start_s, "c": c, "huber_k": sp.huber_k,
            "miss_cost": float(huber(np.array(3.0 * sp.huber_k), sp.huber_k)),
            "events": evs, "candidates": candidates}
    html = (VIEWER_TEMPLATE.replace("__RUN_ID__", run_id).replace("__PLOTLY__", plotly_rel)
            .replace("__DATA__", json.dumps(data, separators=(",", ":"), allow_nan=False)))
    Path(path).write_text(html)


VIEWER_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Doppler run __RUN_ID__</title>
<script src="__PLOTLY__"></script>
<style>
body{font-family:system-ui,sans-serif;margin:12px;background:#f4f5f7;color:#222}
h2{margin:4px 0 8px} .card{background:#fff;border-radius:6px;padding:10px 14px;margin:8px 0;box-shadow:0 1px 2px #0002}
.tabs button{font-size:14px;padding:6px 14px;margin-right:6px;border:1px solid #bbb;background:#eee;border-radius:4px;cursor:pointer}
.tabs button.on{background:#1f6feb;color:#fff;border-color:#1f6feb}
.row{display:grid;grid-template-columns:190px 1fr 110px;gap:8px;align-items:center;margin:3px 0;font-size:13px}
input[type=range]{width:100%} table{border-collapse:collapse;font-size:12.5px} td,th{padding:2px 8px;border-bottom:1px solid #eee;text-align:right}
th:first-child,td:first-child{text-align:left} .note{font-size:12px;color:#666} .score{font-size:14px;font-weight:600}
.btn{font-size:12px;padding:4px 10px;border-radius:4px;border:0;background:#1f6feb;color:#fff;cursor:pointer;margin-right:6px}
.btn.grey{background:#888} .panels{display:grid;gap:8px} .warn{color:#b00}
</style></head><body>
<h2>Doppler run <code>__RUN_ID__</code></h2>
<div class="tabs"><button id="tabC" class="on">Compare candidates</button><button id="tabF">Event fit</button></div>
<div id="C">
 <div class="card"><div class="row"><b>Candidate</b><select id="cand"></select><span></span></div>
  <div id="cinfo" class="note"></div>
  <div id="csl"></div>
  <div><span class="score" id="live"></span> <span class="note" id="opt"></span></div>
  <label class="note"><input type="checkbox" id="showfit" checked> recording's straight-line fit</label>
  <label class="note"><input type="checkbox" id="showothers"> next 2 candidates</label>
  <div class="note">Read-only diagnostic: the candidate curve is fixed by the cell and the ADS-B track (v, d, t0 are not free).
   Only the shared Δt and each event's f0 move it — exactly the scorer's free parameters.</div></div>
 <div class="panels" id="cpanels"></div>
 <div class="card"><b>Snapshot log</b> <span class="note">MIN/MAX per parameter over this candidate's snapshots, BEST = midpoint</span><br>
  <button class="btn" id="csnap">Take snapshot</button><button class="btn grey" id="cclear">Clear log</button>
  <button class="btn grey" id="cdl">Download JSON</button><div id="clog" class="note"></div></div>
</div>
<div id="F" style="display:none">
 <div class="card"><div class="row"><b>Event</b><select id="fev"></select><span></span></div>
  <div id="fsl"></div><div id="fread"></div>
  <div class="note">Free straight-line model (per-event characterization). Independent of any candidate; never used for scoring.</div></div>
 <div class="card"><div id="fplot" style="height:460px"></div></div>
 <div class="card"><b>Snapshot log</b> <span class="note">MIN/MAX auto-calculated, BEST = midpoint</span><br>
  <button class="btn" id="fsnap">Take snapshot</button><button class="btn grey" id="fclear">Clear log</button>
  <button class="btn grey" id="fdl">Download JSON</button><div id="flog" class="note"></div></div>
</div>
<script>
const D = __DATA__;
const C = D.c, K = D.huber_k;
const PAL=[[0,'#08306b'],[0.35,'#2171b5'],[0.6,'#6baed6'],[0.8,'#c6dbef'],[1,'#ffffff']];
function b64u8(s){const b=atob(s),a=new Uint8Array(b.length);for(let i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a;}
function hms(t){t=Math.max(0,t);const h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=t%60;
 return `${h}:${String(m).padStart(2,'0')}:${s.toFixed(s%1?1:0).padStart(2,'0')}`;}
function utc(t){return new Date((D.start_utc_s+t)*1000).toISOString().replace('.000Z','Z');}
function ticks(a,b){const st=[1,2,5,10,15,30,60,120,300,600,900,1800,3600];const w=b-a;let s=st.find(x=>w/x<=8)||3600;
 const v=[];for(let x=Math.ceil(a/s)*s;x<=b;x+=s)v.push(x);return {tickvals:v,ticktext:v.map(hms)};}
function huber(z){const a=Math.abs(z);return a<=K?0.5*z*z:K*(a-0.5*K);}
function straight(tr,f0,v,d,t0){const s=tr-t0,c2=C*C,v2=v*v;const tau=(c2*s-Math.sqrt(c2*v2*s*s+d*d*(c2-v2)))/(c2-v2);
 const r=Math.sqrt(d*d+v2*tau*tau);return f0*C/(C+v2*tau/r);}
const Z=D.events.map(e=>{const a=b64u8(e.spec.z_b64),nt=e.spec.t.length,nf=e.spec.f.length,z=[];
 for(let i=0;i<nf;i++){const r=new Array(nt);for(let j=0;j<nt;j++)r[j]=a[i*nt+j]/255*e.spec.floor_db-e.spec.floor_db;z.push(r);}return z;});
function heat(e){return {type:'heatmap',x:e.spec.t,y:e.spec.f,z:Z[e.idx],colorscale:PAL,zmin:-e.spec.floor_db,zmax:0,
 colorbar:{title:'dB',len:0.9},hoverinfo:'skip'};}
function layout(e,extra){const tk=ticks(e.spec.t[0],e.spec.t[e.spec.t.length-1]);
 return Object.assign({margin:{l:60,r:20,t:30,b:45},xaxis:Object.assign({title:'elapsed time (HH:MM:SS)',range:[e.spec.t[0],e.spec.t[e.spec.t.length-1]]},tk),
  yaxis:{title:'frequency (Hz)',range:[e.spec.f[0],e.spec.f[e.spec.f.length-1]]},legend:{x:1,xanchor:'right',y:1,bgcolor:'rgba(255,255,255,0.75)',font:{size:11}},
  title:{text:`event ${e.idx} (${e.cls}) — ${hms(e.spec.t[0])} to ${hms(e.spec.t[e.spec.t.length-1])}  UTC ${utc(e.fit.t_rx_s)}`,font:{size:13}}},extra||{});}
// ---------- compare ----------
const sel=document.getElementById('cand');
D.candidates.forEach((c,i)=>{const o=document.createElement('option');o.value=i;
 o.text=`#${c.rank}  ${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}  score ${c.score.toFixed(3)}`;sel.appendChild(o);});
let S={},logC=[];
function ratioAt(cur,tau){const u=(tau-cur.t0_s)/cur.step_s,i=Math.floor(u);if(i<0||i+1>=cur.ratio.length)return null;
 const a=cur.ratio[i],b=cur.ratio[i+1];if(a===null||b===null)return null;return a+(b-a)*(u-i);}
function liveScore(c){let tot=0,n=0;const per=[];c.curves.forEach((cur,e)=>{const R=D.events[e].ridge,sh=S.dt-c.dt_s;let ce=0,ss=0,m=0,miss=0;
 if(!cur){per.push({cost:NaN,rms:NaN,miss:0,unmatched:true});return;}
 R.t.forEach((t,i)=>{const r=ratioAt(cur,t+sh);if(r===null){ce+=D.miss_cost;miss++;return;}const res=R.f[i]-S.f0[e]*r;ce+=huber(res/R.sigma);ss+=res*res;m++;});
 tot+=ce;n+=R.t.length;per.push({cost:ce/Math.max(R.t.length,1),rms:Math.sqrt(ss/Math.max(m,1)),miss:miss});});return {score:tot/Math.max(n,1),per:per};}
function slider(box,label,min,max,step,val,unit,fn,fmt){const r=document.createElement('div');r.className='row';
 const l=document.createElement('span');l.textContent=label;const s=document.createElement('input');s.type='range';s.min=min;s.max=max;s.step=step;s.value=val;
 const v=document.createElement('span');const show=x=>v.textContent=(fmt?fmt(x):(+x).toFixed(2))+' '+unit;show(val);
 s.oninput=()=>{show(+s.value);fn(+s.value);};r.append(l,s,v);box.appendChild(r);}
function buildC(){const c=D.candidates[+sel.value];S={dt:c.dt_s,f0:c.curves.map(k=>k?k.f0_hz:null)};
 const box=document.getElementById('csl');box.innerHTML='';
 slider(box,'Δt (shared, all events)',c.dt_lo_s-1,c.dt_hi_s+1,0.01,c.dt_s,'s',x=>{S.dt=x;drawC();});
 c.curves.forEach((k,e)=>{if(k)slider(box,`f0 event ${e}`,(k.f0_hz*0.97).toFixed(2),(k.f0_hz*1.03).toFixed(2),0.01,k.f0_hz,'Hz',x=>{S.f0[e]=x;drawC();});});
 const P=document.getElementById('cpanels');P.innerHTML='';
 D.events.forEach(e=>{const card=document.createElement('div');card.className='card';
  card.innerHTML=`<div id="cp${e.idx}" style="height:420px"></div><div id="ct${e.idx}"></div>`;P.appendChild(card);});
 document.getElementById('cinfo').textContent=`cell ${c.cell_id} · rank ${c.rank} (interim ${c.interim_rank}) · `+
  `tracks ${c.curves.map(k=>k?k.icao24+(k.callsign?' '+k.callsign:''):'(unmatched)').join(', ')} · feasible Δt [${c.dt_lo_s.toFixed(2)}, ${c.dt_hi_s.toFixed(2)}] s`;
 document.getElementById('opt').textContent=`scorer optimum: ${c.score.toFixed(3)} at Δt ${c.dt_s.toFixed(2)} s`;
 drawC();renderLog('clog',logC.filter(s=>s.cell_id===c.cell_id),['dt',...c.curves.map((k,e)=>k?'f0_e'+e:null).filter(Boolean)]);}
function fmtpm(v,s,d){return s===undefined||!isFinite(s)?v.toFixed(d):`${v.toFixed(d)} ± ${s.toFixed(d)}`;}
function drawC(){const c=D.candidates[+sel.value],L=liveScore(c),sh=S.dt-c.dt_s,showfit=document.getElementById('showfit').checked,
 others=document.getElementById('showothers').checked;
 document.getElementById('live').textContent=`live score ${L.score.toFixed(3)}`;
 D.events.forEach(e=>{const cur=c.curves[e.idx],tr=[heat(e),{type:'scattergl',mode:'markers',x:e.ridge.t,y:e.ridge.f,marker:{size:3,color:'#ffe600'},name:'recording ridge'}];
  const tt=e.spec.t;
  if(showfit){const F=e.fit;tr.push({type:'scatter',mode:'lines',x:tt,y:tt.map(t=>straight(t,F.f0_hz,F.v_mps,F.d_m,F.t0_s)),line:{color:'#fff',dash:'dot',width:1.4},name:'recording fit'});}
  if(cur){const xs=[],ys=[];tt.forEach(t=>{const r=ratioAt(cur,t+sh);xs.push(t);ys.push(r===null?null:r*S.f0[e.idx]);});
  tr.push({type:'scatter',mode:'lines',x:xs,y:ys,line:{color:'#e3170a',width:2},name:`candidate #${c.rank} (${cur.icao24})`});}
  if(others){[1,2].forEach(o=>{const oc=D.candidates[(+sel.value)+o];if(!oc)return;const k=oc.curves[e.idx];if(!k)return;
   tr.push({type:'scatter',mode:'lines',x:tt,y:tt.map(t=>{const r=ratioAt(k,t);return r===null?null:r*k.f0_hz;}),line:{color:'#999',dash:'dash',width:1},name:`#${oc.rank} (${k.icao24})`});});}
  Plotly.react('cp'+e.idx,tr,layout(e));
  if(!cur){document.getElementById('ct'+e.idx).innerHTML='<b>event left unmatched by this candidate</b> (ranked after candidates that explain every event)';return;}
  const F=e.fit,s=F.sigma,G=cur.geom,f0=S.f0[e.idx],v=G.v_mps,d=G.d_m,fp=f0*C/(C-v),fq=f0*C/(C+v),roc=f0*v*v/(C*d),pe=L.per[e.idx];
  document.getElementById('ct'+e.idx).innerHTML=`<table><tr><th></th><th>recording fit</th><th>candidate (track geometry)</th></tr>
   <tr><td>f0 (Hz)</td><td>${fmtpm(F.f0_hz,s.f0_hz,2)}</td><td>${f0.toFixed(2)} (slider)</td></tr>
   <tr><td>v (m/s)</td><td>${fmtpm(F.v_mps,s.v_mps,1)}</td><td>${v.toFixed(1)}</td></tr>
   <tr><td>d (m)</td><td>${fmtpm(F.d_m,s.d_m,0)}</td><td>${d.toFixed(0)}</td></tr>
   <tr><td>t0 CPA emission</td><td>${hms(F.t0_s)} ± ${s.t0_s.toFixed(2)} s</td><td>${hms(G.t0_rel_s-sh)}</td></tr>
   <tr><td>CPA received</td><td>${hms(F.t_rx_s)}</td><td>${hms(G.t_rx_rel_s-sh)}</td></tr>
   <tr><td>f_pre / f_post (Hz)</td><td>${F.f_pre_hz.toFixed(2)} / ${F.f_post_hz.toFixed(2)}</td><td>${fp.toFixed(2)} / ${fq.toFixed(2)}</td></tr>
   <tr><td>max RoC (Hz/s)</td><td>${F.max_roc_hz_s.toFixed(3)}</td><td>${roc.toFixed(3)}</td></tr>
   <tr><td>aircraft altitude (m, ellipsoidal)</td><td></td><td>${G.alt_m.toFixed(0)}</td></tr>
   <tr><td>event cost · RMS (Hz) · unmodeled pts</td><td colspan=2>${pe.cost.toFixed(3)} · ${pe.rms.toFixed(3)} · <span class="${pe.miss?'warn':''}">${pe.miss}</span></td></tr></table>`;});}
sel.onchange=buildC;['showfit','showothers'].forEach(id=>document.getElementById(id).onchange=drawC);
function renderLog(id,rows,keys){const el=document.getElementById(id);if(!rows.length){el.textContent='no snapshots yet';return;}
 let h='<table><tr><th>#</th>'+keys.map(k=>`<th>${k}</th>`).join('')+'<th>score</th></tr>';
 rows.forEach((r,i)=>h+=`<tr><td>${i+1}</td>`+keys.map(k=>`<td>${(+r[k]).toFixed(3)}</td>`).join('')+`<td>${r.score.toFixed(3)}</td></tr>`);
 ['MIN','MAX','BEST'].forEach(n=>{h+=`<tr><td><b>${n}</b></td>`+keys.map(k=>{const v=rows.map(r=>+r[k]),mn=Math.min(...v),mx=Math.max(...v);
  return `<td><b>${(n==='MIN'?mn:n==='MAX'?mx:(mn+mx)/2).toFixed(3)}</b></td>`;}).join('')+'<td></td></tr>';});el.innerHTML=h+'</table>';}
function dl(name,obj){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(obj,null,1)],{type:'application/json'}));a.download=name;a.click();}
document.getElementById('csnap').onclick=()=>{const c=D.candidates[+sel.value],L=liveScore(c),r={cell_id:c.cell_id,rank:c.rank,dt:S.dt,score:L.score};
 S.f0.forEach((v,e)=>{if(v!==null)r['f0_e'+e]=v;});logC.push(r);buildLogC();};
function buildLogC(){const c=D.candidates[+sel.value];renderLog('clog',logC.filter(s=>s.cell_id===c.cell_id),['dt',...c.curves.map((k,e)=>k?'f0_e'+e:null).filter(Boolean)]);}
document.getElementById('cclear').onclick=()=>{logC=[];buildLogC();};
document.getElementById('cdl').onclick=()=>dl(`${D.run_id}_compare_snapshots.json`,logC);
// ---------- event fit ----------
const fev=document.getElementById('fev');D.events.forEach(e=>{const o=document.createElement('option');o.value=e.idx;o.text=`event ${e.idx} (${e.cls}) at ${hms(e.fit.t_rx_s)}`;fev.appendChild(o);});
let FS={},logF=[];
function buildF(){const e=D.events[+fev.value],F=e.fit,s=F.sigma;FS={f0:F.f0_hz,v:F.v_mps,d:F.d_m,t0:F.t0_s};
 const box=document.getElementById('fsl');box.innerHTML='';const w=(sg,min)=>Math.max(5*(isFinite(sg)?sg:0),min);
 slider(box,'f0 (fundamental freq)',F.f0_hz-w(s.f0_hz,0.1*F.f0_hz),F.f0_hz+w(s.f0_hz,0.1*F.f0_hz),0.01,F.f0_hz,'Hz',x=>{FS.f0=x;drawF();});
 slider(box,'v (plane speed)',Math.max(5,F.v_mps-w(s.v_mps,30)),F.v_mps+w(s.v_mps,30),0.1,F.v_mps,'m/s',x=>{FS.v=x;drawF();});
 slider(box,'d (slant distance)',Math.max(10,F.d_m-w(s.d_m,0.6*F.d_m)),F.d_m+w(s.d_m,0.6*F.d_m),1,F.d_m,'m',x=>{FS.d=x;drawF();});
 slider(box,'t0 (CPA time, emission)',F.t0_s-w(s.t0_s,20),F.t0_s+w(s.t0_s,20),0.01,F.t0_s,'',x=>{FS.t0=x;drawF();},x=>hms(x));
 drawF();renderLog('flog',logF.filter(r=>r.event===e.idx),['f0','v','d','t0']);}
function drawF(){const e=D.events[+fev.value],tt=e.spec.t,R=e.ridge;let ss=0;
 R.t.forEach((t,i)=>{const r=R.f[i]-straight(t,FS.f0,FS.v,FS.d,FS.t0);ss+=r*r;});FS.rms=Math.sqrt(ss/Math.max(R.t.length,1));
 const fp=FS.f0*C/(C-FS.v),fq=FS.f0*C/(C+FS.v),roc=FS.f0*FS.v*FS.v/(C*FS.d);
 document.getElementById('fread').innerHTML=`<table><tr><td>f_pre (approach)</td><td>${fp.toFixed(2)} Hz</td><td>f_post (recede)</td><td>${fq.toFixed(2)} Hz</td>
  <td>max RoC</td><td>${roc.toFixed(3)} Hz/s</td><td>RMS vs ridge</td><td>${Math.sqrt(ss/Math.max(R.t.length,1)).toFixed(3)} Hz</td></tr></table>`;
 Plotly.react('fplot',[heat(e),{type:'scattergl',mode:'markers',x:R.t,y:R.f,marker:{size:3,color:'#ffe600'},name:'recording ridge'},
  {type:'scatter',mode:'lines',x:tt,y:tt.map(t=>straight(t,FS.f0,FS.v,FS.d,FS.t0)),line:{color:'#e3170a',width:2},name:'straight-line model'}],layout(e));}
fev.onchange=buildF;
document.getElementById('fsnap').onclick=()=>{const e=+fev.value;logF.push({event:e,f0:FS.f0,v:FS.v,d:FS.d,t0:FS.t0,score:FS.rms});renderLogF();};
function renderLogF(){renderLog('flog',logF.filter(r=>r.event===+fev.value),['f0','v','d','t0']);}
document.getElementById('fclear').onclick=()=>{logF=[];renderLogF();};
document.getElementById('fdl').onclick=()=>dl(`${D.run_id}_eventfit_snapshots.json`,logF);
// ---------- tabs ----------
function tab(m){document.getElementById('C').style.display=m==='C'?'':'none';document.getElementById('F').style.display=m==='F'?'':'none';
 document.getElementById('tabC').classList.toggle('on',m==='C');document.getElementById('tabF').classList.toggle('on',m==='F');
 if(m==='F')buildF();else if(D.candidates.length)buildC();}
document.getElementById('tabC').onclick=()=>tab('C');document.getElementById('tabF').onclick=()=>tab('F');
if(D.candidates.length)buildC();else{document.getElementById('cinfo').textContent='no candidates survived the cascade';tab('F');}
</script></body></html>
"""
