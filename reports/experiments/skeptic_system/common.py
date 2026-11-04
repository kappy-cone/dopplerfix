"""Shared helpers for the skeptic experiments: straight-line traffic, truth events, cascade runs."""
import sys
sys.path.insert(0, "/Users/williambarr/qgis")
import numpy as np
import cli
from doppler import aircraft_types as at
from doppler import cascade as cs
from doppler import geo, synth, tracks, physics as ph, score

FRAME = geo.LocalFrame(40.0394, -105.2258, 1600.0)
START = 1781532000.0
C = 343.4
CFG = cli.load_config("/Users/williambarr/qgis/config.yaml")
STRICT = dict(max_unmatched_events=0, silence_audibility="hard")


def params(tile=32, **kw):
    d = {**CFG["cascade"], "tile_cells": tile,
         **{k: v for k, v in CFG["score"].items() if k in ("allow_shared_tracks", "max_hypotheses_per_cell")}}
    d.update(kw)
    return cs.CascadeParams.from_dict(d)


def straight(icao, t_rel, p0_enu, heading_deg, speed, alt_agl, t_pass, climb=0.0, gap=None, stationary=False):
    """Straight track through p0 (horizontal ENU) at emission time t_pass; ground at FRAME.h0."""
    psi = np.deg2rad(heading_deg)
    u = np.array([np.sin(psi), np.cos(psi)])
    v = 0.0 if stationary else speed
    xy = p0_enu[None, :] + (t_rel - t_pass)[:, None] * v * u[None, :]
    z = alt_agl + climb * (t_rel - t_pass)
    enu = np.c_[xy, z]
    vel = np.c_[np.tile(v * u, (len(t_rel), 1)), np.full(len(t_rel), 0.0 if stationary else climb)]
    m = np.ones(len(t_rel), bool)
    if gap is not None:
        m &= ~((t_rel >= gap[0]) & (t_rel < gap[1]))
    tr = tracks.Track.from_enu(icao, icao.upper(), START + t_rel[m], enu[m], FRAME, vel_enu=vel[m])
    return tr, enu, t_rel


def prepare(trs, cls=at.PROP):
    tp = tracks.TrackParams.from_dict(CFG["tracks"])
    prep = tracks.prepare_tracks(trs, tp)
    for t in prep:
        t.cls = cls if not isinstance(cls, dict) else cls.get(t.icao24, at.UNKNOWN)
    return prep


def truth_event(k, enu, t_rel, mic_enu, cls=at.PROP, sig=(1.5, 30.0, 0.15), t_near=None, ridge=False, dur=None):
    tru = synth.event_truth("x", 100.0, t_rel, enu, mic_enu, C, t_near_s=t_near)
    rt = rf = rw = np.zeros(0)
    if ridge:
        lo = max(0.0, tru.t_cpa_received_rel_s - 40)
        hi = tru.t_cpa_received_rel_s + 40 if dur is None else min(dur, tru.t_cpa_received_rel_s + 40)
        rt = np.arange(lo, hi, 0.1)
        ratio, _, _ = ph.predicted_ratio_at(rt, t_rel, enu, mic_enu, C)
        rf = 100.0 * ratio + np.random.default_rng(k).normal(0, 0.2, len(rt))
        rw = np.full(len(rt), 1 / 0.2 ** 2)
    return cs.ObservedEvent(k, cls, tru.t_cpa_received_rel_s, tru.v_mps, tru.d_m, 100.0,
                            np.diag(np.square(sig)), (5.0, 5.0), rt, rf, rw)


def run_cascade(events, prep, p, dur, radius_m=10000.0, spacing=200.0, t_pad=(150, 60), clock=10.0):
    prior = cs.dt_prior_from("known", START, None, clock, dur)
    ts = tracks.build_trackset(prep, START - t_pad[0], START + dur + t_pad[1], 0.25)
    region = cli.circle_region(FRAME.lat0, FRAME.lon0, radius_m)
    spec = geo.make_grid_spec(region, spacing, p.tile_cells)
    out = []
    for r in cs.iter_cascade(spec, events, ts, prior, geo.FlatDEM(1600.0), 1.5, C, p):
        out.append(r)
    return out, ts, spec, prior


def enu_of(xyz):
    return FRAME.ecef_to_enu(xyz)
