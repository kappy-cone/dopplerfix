"""U19 D1 + E1 diagnostics (reporting only, no knob): where truth events are lost, and an oracle ladder.

Per unit (scenario rebuilt exactly as `cli.py ablate` does, see screen.py) and per detection arm (base, b):
- detection, then a traced copy of `fit.characterize_audio` that records every peel attempt in every window
  (Hough seed, guided ridge, fit, descent check) and every post-fit removal (jet duplicate / harmonic of a
  prop fit, too slow, `dedupe_same_aircraft`). The traced fits are checked against `fit.characterize_audio`
  on the same detection, every field compared (`trace_ok`).
- each truth event is classified: detected, no window, or (window but no matched fit) where it was lost.

The oracle ladder (E1) runs once per unit with the base configuration; it is a diagnostic, never a run claim:
- R1 oracle windows: one window per truth event, t_rx ± oracle_half_s (clipped to the clip), passed through
  the same automatic peel as detected windows (manual=False, so the descent check and the prop band apply
  as for a detected window; `detect.manual_windows` would bypass both).
- R2 oracle seed: the R1 window, the guided ridge around the truth received-frequency curve (the harmonic or
  half-order line k of FAMILY with the most columns >= ridge.weak_db), then the same fit and guided passes.
  The truth curve leaves out the source-tone jitter / RPM walk, so R2 can miss a line that wanders more than
  guide_hz from it: R2 below R1 on an event means the seed curve, not the ridge stage.
- R3 truth ridge: the truth curve itself (t_rx ± 60 s, 0.1 s step, 0.1 Hz noise, as
  tests/test_cascade.ridge_events), straight into fit_ridge.
A truth event counts as recovered on a rung if a fit lies within evaluate.match_tol_s of its received CPA time.

    .venv/bin/python reports/experiments/u19/d1.py --out <scratch>/d1.jsonl --jobs 14
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np  # noqa: E402

from screen import _seeds, arm_cfg, scenario_audio  # noqa: E402

from cli import event_params, section, speed_of_sound_from  # noqa: E402
from doppler import physics as ph  # noqa: E402
from doppler import synth  # noqa: E402
from doppler.audio import background_db, compute_spectrogram  # noqa: E402
from doppler.classify import ClassFeatures, broadband_rise_db, decide, harmonic_count  # noqa: E402
from doppler.detect import EventWindow, detect_events_audio  # noqa: E402
from doppler.fit import (_descent, characterize_audio, dedupe_same_aircraft, fit_ridge,  # noqa: E402
                         is_harmonic_of)
from doppler.ridge import (Ridge, _dp_path, _finish_ridge, doppler_hough, guided_ridge,  # noqa: E402
                           mask_family)

ORACLE_HALF_S = 30.0       # scenario C: events 30 s apart in a 60 s clip; each window reaches both clip edges
FAMILY = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0)   # harmonic and half-order ratios a ridge may lock onto
ON_CURVE_FRAC = 0.5        # a ridge "follows" a truth curve if half its points lie within guide_hz·k of k·f


# ---------------------------------------------------------------------------
# Traced copy of fit.characterize_window / characterize_audio (logic unchanged; records each step)
# ---------------------------------------------------------------------------
def handoff_diag(spec, Zw, Zw0, fi, ti, f_seed, rp, window, full_Z, n_prior_fits):
    """Why a guided ridge came back short: strong columns (corridor max >= weak_db) in the masked and the unmasked
    excess, and whether the unmasked excess would have given a ridge (diagnostic only)."""
    g = max(1, int(round(rp.guide_hz / spec.df_hz)))
    cidx = np.arange(len(ti))[None, :]

    def strong(Z, m=1.0):
        centre = np.rint((m * f_seed - spec.f_hz[fi[0]]) / spec.df_hz).astype(int)
        rows = centre[None, :] + np.arange(-g, g + 1)[:, None]
        valid = (rows >= 0) & (rows < len(fi))
        v = np.where(valid, Z[np.clip(rows, 0, len(fi) - 1), cidx], -np.inf).max(axis=0)
        return int((v >= rp.weak_db).sum())

    r0 = guided_ridge(spec, Zw0, fi, ti, f_seed, rp, window, full_Z)
    return {"strong_masked": strong(Zw), "strong_unmasked": strong(Zw0), "n_prior_fits": n_prior_fits,
            "unmasked_ridge_points": None if r0 is None else len(r0), "min_points": rp.min_points,
            "strong_masked_by_harmonic": {str(m): strong(Zw, m) for m in (0.5, 1.5, 2.0, 3.0)},
            "f_seed_median_hz": float(np.median(f_seed)), "band_hz": [float(spec.f_hz[fi[0]]), float(spec.f_hz[fi[-1]])]}


def traced_window(spec, full_Z, window, rp, fp, c, band_name, wi, log):
    ti = np.flatnonzero((spec.t_s >= window.start_s) & (spec.t_s <= window.end_s))
    fi = np.flatnonzero((spec.f_hz >= window.band_hz[0]) & (spec.f_hz <= window.band_hz[1]))
    stop = None
    if len(ti) < 3 or len(fi) < 3:
        log["windows"].append({"win": wi, "band": band_name, "stop": "empty"})
        return []
    assert rp.seed_mode == "hough"
    Zw = np.clip(full_Z[np.ix_(fi, ti)], -10.0, 60.0)
    Zw0 = Zw.copy()                       # unmasked, for the hand-off diagnostic only
    hop = spec.hop_s
    cols = np.arange(len(ti))
    t_win = spec.t_s[ti]
    f_sub = spec.f_hz[fi]
    fits = []
    for _ in range(rp.max_ridges + 2):
        if len(fits) >= rp.max_ridges:
            stop = "max_ridges"
            break
        n_strong = int((Zw.max(axis=0) >= rp.weak_db).sum())
        if n_strong < rp.min_points:
            stop = "nothing_strong_left"
            break
        seeds = doppler_hough(spec, Zw, fi, ti, rp, c, n_best=1)
        if not seeds:
            stop = "no_seed"
            break
        h = seeds[0]
        f_seed = ph.straight_freq_received(t_win, h.f0_hz, h.v_mps, h.d_m, h.t0_s, c)
        g = guided_ridge(spec, Zw, fi, ti, f_seed, rp, window, full_Z)
        if g is None:
            stop = "guided_short"
            log["attempts"].append({"win": wi, "band": band_name, "kind": "guided_short",
                                    "seed_t_cpa": h.t0_s + h.d_m / c, "seed_curve": (t_win, f_seed),
                                    "seed": (h.f0_hz, h.v_mps, h.d_m, h.t0_s),
                                    "handoff": handoff_diag(spec, Zw, Zw0, fi, ti, f_seed, rp, window, full_Z,
                                                            len(fits))})
            break
        est = fit_ridge(g, window, fp, c, hop, spec.window_s)
        for _pass in range(rp.guided_passes):
            g2 = guided_ridge(spec, Zw, fi, ti, est.model(t_win), rp, window, full_Z)
            if g2 is None:
                break
            est = fit_ridge(g2, window, fp, c, hop, spec.window_s)
        ok = _descent(est.ridge) >= fp.min_descent_frac or window.manual
        log["attempts"].append({"win": wi, "band": band_name, "kind": "fit" if ok else "no_descent", "fit": est,
                                "seed_t_cpa": h.t0_s + h.d_m / c, "seed_curve": (t_win, f_seed),
                                "seed": (h.f0_hz, h.v_mps, h.d_m, h.t0_s)})
        if ok:
            fits.append(est)
        subs = (1, est.ridge.subharmonic) if est.ridge.subharmonic > 1 else (1,)
        mask_family(Zw, f_sub, cols, est.model(t_win), rp.mask_halfwidth_hz, subharmonics=subs)
        mask_family(Zw, f_sub, cols, f_seed, rp.mask_halfwidth_hz, subharmonics=subs)
    log["windows"].append({"win": wi, "band": band_name, "stop": stop or "loop_cap"})
    return fits


def traced_characterize(x, fs_hz, detection, p, c):
    """fit.characterize_audio with a trace. Returns (fits, log); log['fate'][id(fit)] says what removed a fit."""
    sp_p, rp, jrp, fp, cp, sj_p = p["spec"], p["ridge"], p["ridge_jet"], p["fit"], p["classify"], p["spec_jet"]
    quiet = float(np.percentile(detection.broadband_db, cp.broadband_percentile))
    log = {"windows": [], "attempts": [], "fate": {}}
    out = []
    for wi, w in enumerate(detection.windows):
        pad = sp_p.window_s
        sp = compute_spectrogram(x, fs_hz, sp_p, (max(0.0, w.start_s - pad), w.end_s + pad))
        full_Z = sp.power_db - background_db(sp, rp.floor_mode, rp.floor_halfwidth_hz)
        band = w.band_hz if w.manual else cp.prop_band_hz
        prop = traced_window(sp, full_Z, replace(w, band_hz=tuple(band)), rp, fp, c, "prop", wi, log)
        jet = []
        if sj_p is not None:
            sj = compute_spectrogram(x, fs_hz, sj_p, (max(0.0, w.start_s - pad), w.end_s + pad))
            zj = sj.power_db - background_db(sj, jrp.floor_mode, jrp.floor_halfwidth_hz)
            jb = (cp.jet_band_hz[0], min(cp.jet_band_hz[1], sj.f_hz[-1]))
            jet_all = traced_window(sj, zj, replace(w, band_hz=jb), jrp, fp, c, "jet", wi, log)
            jet = []
            for j in jet_all:
                if any((abs(j.t_cpa_received_s - q.t_cpa_received_s) < cp.dedupe_dt_s
                        and abs(j.v_mps / q.v_mps - 1) < cp.dedupe_v_frac) or is_harmonic_of(j, q, cp) for q in prop):
                    log["fate"][id(j)] = "jet_dup_of_prop"
                else:
                    jet.append(j)
        for band_name, fits in (("prop", prop), ("jet", jet)):
            for e in fits:
                zz, ss = (full_Z, sp) if band_name == "prop" else (zj, sj)
                nh = harmonic_count(zz, ss.f_hz, ss.t_s, e.ridge.t_s, e.ridge.f_hz, cp.harmonic_weak_db,
                                    cp.max_harmonic)
                half = max(5.0, e.d_m / max(e.v_mps, 1.0))
                bb = broadband_rise_db(detection.t_s, detection.broadband_db, quiet, e.t_cpa_received_s, half)
                e.cls = decide(band_name, nh, bb, cp)
                e.class_features = ClassFeatures(band_name, nh, bb, e.cls)
                out.append(e)
    for e in out:
        if e.v_mps < cp.min_aircraft_speed_mps:
            log["fate"][id(e)] = "too_slow"
    out = [e for e in out if e.v_mps >= cp.min_aircraft_speed_mps]
    kept = dedupe_same_aircraft(out, cp)
    ids = {id(e) for e in kept}
    for e in out:
        if id(e) not in ids:
            log["fate"][id(e)] = "dedupe_same_aircraft"
    return sorted(kept, key=lambda e: e.t_cpa_received_s), log


def same_fits(a, b) -> bool:
    key = lambda e: (e.t_cpa_received_s, e.f0_hz, e.v_mps, e.d_m, e.t0_s, e.cls, e.n_points)  # noqa: E731
    return len(a) == len(b) and all(key(x) == key(y) for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Truth curves and attribution
# ---------------------------------------------------------------------------
def truth_curves(sc):
    """Per truth event (spec order): f(t_received) of the fundamental, from the synthesised geometry, the
    synthesis speed of sound and wind (source-tone jitter / RPM walk not included)."""
    t_track = sc.track_time_rel()
    c_true = sc.c_true if sc.c_true is not None else sc.c
    ev_enu = [e for e, s in zip(sc.track_enu, sc.specs) if s.role == "event"]
    out = []
    for enu, tru in zip(ev_enu, sc.truth):
        t_r, _, _ = synth.received_times(enu, sc.mic_enu, t_track, c_true, sc.wind_enu)
        ratio = 1.0 / np.gradient(t_r, t_track)
        out.append((t_r, tru.f0_hz * ratio))
    return out


def f_truth_at(curve, t):
    t_r, f = curve
    return np.where((t >= t_r[0]) & (t <= t_r[-1]), np.interp(t, t_r, f), np.nan)


def follows(t, f, curve, guide_hz) -> tuple[float, float]:
    """(best fraction of points within guide_hz·max(k,1) of k·f_truth, that k)."""
    ft = f_truth_at(curve, np.asarray(t))
    best = (0.0, float("nan"))
    ok = np.isfinite(ft)
    if not ok.any():
        return best
    for k in FAMILY:
        frac = float(np.mean(np.abs(np.asarray(f)[ok] - k * ft[ok]) <= guide_hz * max(k, 1.0)))
        if frac > best[0]:
            best = (frac, k)
    return best


def attribute(t_true, curve, windows, log, tol, guide_hz):
    """Where a window-covered, unmatched truth event was lost (D1 classes)."""
    cover = {i for i, w in enumerate(windows) if w.start_s <= t_true <= w.end_s}
    fits = [a for a in log["attempts"] if a["kind"] in ("fit", "no_descent")]
    info = {"n_windows_cover": len(cover)}
    # 4/5: a fit near the truth existed but a post-fit step removed it
    near = [a for a in fits if a["kind"] == "fit" and abs(a["fit"].t_cpa_received_s - t_true) < tol]
    if near:
        info["fates"] = sorted({log["fate"].get(id(a["fit"]), "kept") for a in near})
        return "removed_after_fit", info
    # 2: a fit near the truth (or on its curve) failed the descent check
    rej = [a for a in fits if a["kind"] == "no_descent" and (abs(a["fit"].t_cpa_received_s - t_true) < tol or
           follows(a["fit"].ridge.t_s, a["fit"].ridge.f_hz, curve, guide_hz)[0] >= ON_CURVE_FRAC)]
    if rej:
        info["dt_s"] = [round(a["fit"].t_cpa_received_s - t_true, 1) for a in rej]
        return "fit_rejected_no_descent", info
    # 3: an accepted fit follows this event's curve but its t_cpa is > tol away
    mis = []
    for a in fits:
        if a["kind"] != "fit":
            continue
        frac, k = follows(a["fit"].ridge.t_s, a["fit"].ridge.f_hz, curve, guide_hz)
        if frac >= ON_CURVE_FRAC:
            e = a["fit"]
            mis.append({"dt_s": round(e.t_cpa_received_s - t_true, 1), "k": k, "frac": round(frac, 2),
                        "fate": log["fate"].get(id(e), "kept"), "band": a["band"],
                        "coverage": [round(float(x), 2) for x in e.coverage], "v_mps": round(e.v_mps, 1),
                        "d_m": round(e.d_m, 0)})
    if mis:
        info["fits"] = mis
        return "fit_mistimed", info
    # 1: no ridge for this event; why the peel stopped in its window(s)
    stops = sorted({w["stop"] for w in log["windows"] if w["win"] in cover and w["band"] == "prop"})
    short = [a for a in log["attempts"] if a["kind"] == "guided_short" and a["win"] in cover and a["band"] == "prop"]
    info["prop_stops"] = stops
    info["guided_short_seed_on_event"] = [follows(*a["seed_curve"], curve, guide_hz)[0] >= ON_CURVE_FRAC
                                          for a in short]
    info["n_fits_in_window"] = sum(1 for a in fits if a["win"] in cover and a["kind"] == "fit")
    return "no_ridge", info


def classify_events(truth, curves, det_windows, fits, log, tol, guide_hz):
    t_fit = np.array([f.t_cpa_received_s for f in fits], float)
    out, matched = [], set()
    for te, curve in zip(truth["events"], curves):
        t = te["t_cpa_received_rel_s"]
        k = int(np.argmin(np.abs(t_fit - t))) if len(t_fit) else None
        ok = k is not None and abs(t_fit[k] - t) < tol
        if ok:
            matched.add(k)
            out.append({"cause": "detected"})
        elif not any(w.start_s <= t <= w.end_s for w in det_windows):
            out.append({"cause": "no_window"})
        else:
            cause, info = attribute(t, curve, det_windows, log, tol, guide_hz)
            out.append({"cause": cause, **info})
    return out, len(fits) - len(matched)


# ---------------------------------------------------------------------------
# Oracle rungs
# ---------------------------------------------------------------------------
def column_counts(spec, full_Z, window, band, curve, rp):
    """Columns along k·f_truth (best k of FAMILY, the harmonic and half-order lines) with excess >= weak_db, and >= 7.6 dB (the local 3σ
    bar for exponential noise, Luo & Shen 2019 eq. 15, reported only), from the guided ridge's own excess."""
    ti = np.flatnonzero((spec.t_s >= window.start_s) & (spec.t_s <= window.end_s))
    fi = np.flatnonzero((spec.f_hz >= band[0]) & (spec.f_hz <= band[1]))
    Zw = np.clip(full_Z[np.ix_(fi, ti)], -10.0, 60.0)
    t_win = spec.t_s[ti]
    ft = f_truth_at(curve, t_win)
    best = None
    for k in FAMILY:
        fm = k * ft
        okm = np.isfinite(fm) & (fm >= band[0]) & (fm <= band[1])
        if okm.sum() < 3:
            continue
        df = spec.df_hz
        g = max(1, int(round(rp.guide_hz / df)))
        centre = np.rint((np.where(okm, fm, 0.0) - spec.f_hz[fi[0]]) / df).astype(int)
        rows = centre[None, :] + np.arange(-g, g + 1)[:, None]
        valid = (rows >= 0) & (rows < len(fi)) & okm[None, :]
        vals = np.where(valid, Zw[np.clip(rows, 0, len(fi) - 1), np.arange(len(ti))[None, :]], -np.inf)
        exc = vals.max(axis=0)
        n12, n76 = int((exc >= rp.weak_db).sum()), int((exc >= 7.6).sum())
        if best is None or n12 > best["n_weak"]:
            best = {"k": k, "n_weak": n12, "n_7p6": n76, "n_cols": int(okm.sum()), "f_model": fm, "okm": okm,
                    "Zw": Zw, "fi": fi, "ti": ti, "t_win": t_win}
    return best


def oracle(x, fs, truth, curves, cfg, c, tol, dur):
    p = event_params(cfg)
    sp_p, rp, fp, cp = p["spec"], p["ridge"], p["fit"], p["classify"]
    wins = [EventWindow(max(0.0, te["t_cpa_received_rel_s"] - ORACLE_HALF_S),
                        min(dur, te["t_cpa_received_rel_s"] + ORACLE_HALF_S), tuple(cp.prop_band_hz))
            for te in truth["events"]]
    det = detect_events_audio(x, fs, sp_p, p["detect"], cp.broadband_band_hz, p["spec_jet"])
    det_or = replace(det, windows=wins)
    fits1, log1 = traced_characterize(x, fs, det_or, p, c)
    r1, _ = classify_events(truth, curves, wins, fits1, log1, tol, rp.guide_hz)
    rows = []
    rng = np.random.default_rng(0)
    for i, (te, curve, w) in enumerate(zip(truth["events"], curves, wins)):
        t_true = te["t_cpa_received_rel_s"]
        row = {"r1": r1[i]["cause"], "r1_info": {k: v for k, v in r1[i].items() if k != "cause"}}
        # R2: guided ridge on the truth curve (prop spectrogram, prop band), then the same fit + guided passes
        pad = sp_p.window_s
        sp = compute_spectrogram(x, fs, sp_p, (max(0.0, w.start_s - pad), w.end_s + pad))
        full_Z = sp.power_db - background_db(sp, rp.floor_mode, rp.floor_halfwidth_hz)
        cc = column_counts(sp, full_Z, w, cp.prop_band_hz, curve, rp)
        r2 = "no_curve_in_band"
        if cc is not None:
            row.update(r2_k=cc["k"], r2_cols_weak=cc["n_weak"], r2_cols_7p6=cc["n_7p6"], r2_cols=cc["n_cols"])
            okm = cc["okm"]
            fm = np.where(okm, cc["f_model"], np.nanmedian(cc["f_model"][okm]))
            g = guided_ridge(sp, cc["Zw"], cc["fi"], cc["ti"], fm, rp, w, full_Z)
            if g is None:
                r2 = "guided_short"
            else:
                est = fit_ridge(g, w, fp, c, sp.hop_s, sp.window_s)
                for _pass in range(rp.guided_passes):
                    g2 = guided_ridge(sp, cc["Zw"], cc["fi"], cc["ti"], est.model(cc["t_win"]), rp, w, full_Z)
                    if g2 is None:
                        break
                    est = fit_ridge(g2, w, fp, c, sp.hop_s, sp.window_s)
                row["r2_dt_s"] = round(est.t_cpa_received_s - t_true, 2)
                if _descent(est.ridge) < fp.min_descent_frac:
                    r2 = "no_descent"
                elif est.v_mps < cp.min_aircraft_speed_mps:
                    r2 = "too_slow"
                else:
                    r2 = "detected" if abs(est.t_cpa_received_s - t_true) < tol else "fit_mistimed"
        row["r2"] = r2
        # R3: the truth curve itself as the ridge
        tr = np.arange(max(0.0, t_true - 60), min(dur, t_true + 60), 0.1)
        ft = f_truth_at(curve, tr)
        m = np.isfinite(ft)
        tr, ft = tr[m], ft[m] + rng.normal(0, 0.1, m.sum())
        rdg = Ridge(t_s=tr, f_hz=ft, excess_db=np.full(len(tr), 20.0), noise_hz=0.1, band_hz=tuple(cp.prop_band_hz))
        e3 = fit_ridge(rdg, w, fp, c, sp.hop_s, sp.window_s)
        row["r3_dt_s"] = round(e3.t_cpa_received_s - t_true, 2)
        row["r3"] = "detected" if abs(e3.t_cpa_received_s - t_true) < tol else "fit_mistimed"
        rows.append(row)
    return rows, len(fits1)


def one(unit):
    conf, seed, arms, scenario = unit
    t0 = time.time()
    cfg, sc, truth, xm, fs = scenario_audio(conf, seed, scenario)
    tol = float(section(cfg, "evaluate").get("match_tol_s", 10.0))
    dur = len(xm) / fs
    curves = truth_curves(sc)
    info = truth.get("aircraft") or {}
    if isinstance(info, list):
        info = {a["icao24"]: a for a in info}
    out = []
    for arm in arms:
        acfg = arm_cfg(cfg, arm)
        c = speed_of_sound_from(acfg)
        p = event_params(acfg)
        det = detect_events_audio(xm, fs, p["spec"], p["detect"], p["classify"].broadband_band_hz, p["spec_jet"])
        fits, log = traced_characterize(xm, fs, det, p, c)
        ref = characterize_audio(xm, fs, det, p["spec"], p["ridge"], p["ridge_jet"], p["fit"], p["classify"], c,
                                 jet_spec_params=p["spec_jet"])
        ev, spurious = classify_events(truth, curves, det.windows, fits, log, tol, p["ridge"].guide_hz)
        for e, te in zip(ev, truth["events"]):
            t = te["t_cpa_received_rel_s"]
            near = np.abs(det.t_s - t) <= tol
            a = info.get(te["icao24"], {})
            e.update(icao24=te["icao24"], t_rx_s=t, cls=te.get("cls"),
                     stat_peak_db=float(det.stat[near].max()) if near.any() else None,
                     peak_band_snr_db=a.get("peak_band_snr_db"), d_m=te.get("d_m"), v_mps=te.get("v_mps"),
                     f0_hz=te.get("f0_hz"))
        out.append({"scenario": scenario, "config": conf, "seed": seed, "arm": arm, "trace_ok": same_fits(fits, ref),
                    "n_windows": len(det.windows), "n_fits": len(fits), "n_truth": len(truth["events"]),
                    "detected": sum(e["cause"] == "detected" for e in ev), "spurious": spurious, "events": ev})
    c = speed_of_sound_from(cfg)
    orc, n_fits_r1 = oracle(xm, fs, truth, curves, cfg, c, tol, dur)
    out.append({"scenario": scenario, "config": conf, "seed": seed, "arm": "oracle", "n_truth": len(truth["events"]),
                "n_fits_r1": n_fits_r1, "events": orc, "wall_s": round(time.time() - t0, 1)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--configs", default="clean,all,loo:path,loo:levels,loo:scene,sub:reflection")
    ap.add_argument("--seeds", default="0-49")
    ap.add_argument("--arms", default="base,b")
    ap.add_argument("--scenario", default="C")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv)
    import os
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(k, "1")
    arms = a.arms.split(",")
    units = [(c, s, arms, a.scenario) for c in a.configs.split(",") for s in _seeds(a.seeds)]
    with Pool(a.jobs) as pool, open(a.out, "w") as fh:
        for rows in pool.imap_unordered(one, units):
            for r in rows:
                fh.write(json.dumps(r, default=float) + "\n")
                fh.flush()
    print(f"{len(units)} units x {len(arms)} arms + oracle -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
