"""Diagnostic figures."""
from __future__ import annotations

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
