"""Cost surface of the locked scorer around the true mic with the TRUE assignment, under counterfactuals:
  base         : pipeline inputs (degraded ADS-B, config c, mic 1.5 m)
  clean_adsb   : ADS-B replaced by the synth's clean tracks (same seed rebuild)
  +c_true      : and the synth's c
  +h_true      : and the true mic height
Δt profiled on a fine grid. Prints the minimum's offset from truth, Δt, cost at truth vs minimum, per-event rms."""
import sys, json
import numpy as np, soundfile as sf
import cli
from doppler import audio, score, tracks as trk, synth, realism as rl, cascade
from doppler.geo import LocalFrame, geodetic_to_ecef

cfg_path, scen, seed, dur, evt = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
cfg = cli.load_config(cfg_path)
truth = json.load(open(f"{scen}/truth.json"))
c_cfg = cli.speed_of_sound_from(cfg)
x, fs = sf.read(f"{scen}/audio.flac", always_2d=False); x = audio.mix_to_mono(x)
tprior = audio.parse_time_prior(json.load(open(f"{scen}/audio.json")))
duration = len(x) / fs
_, fits = cli.run_events(x, fs, cfg, c_cfg)
_, region = cli.load_region(cfg, None)
win = (tprior.start_s - 130, tprior.start_s + 10 + duration + 60)
prep_deg = cli.load_tracks(cfg, f"{scen}/tracks.parquet", f"{scen}/aircraft_db.csv", win)

# clean tracks: rebuild the scenario's first make_scenario (tracks are pre-degradation)
frame0, radius = cli.region_frame(region)
class A: pass
a = A(); a.seed = seed; a.snr_db = None; a.n_tracks = None; a.duration_s = dur; a.event_times = evt; a.event_classes = None
params = cli.synth_params_from(cfg, a, radius)
tp0 = audio.parse_time_prior(cfg.get("time_prior") or {})
ground = cli.section(cfg, "region").get("ground_elev_m", 0.0)
frame = LocalFrame(frame0.lat0, frame0.lon0, ground)
rp = rl.RealismParams.from_dict(cli.section(cfg, "realism"))
sc = synth.make_scenario(params, frame, tp0.start_s, ground, c_cfg, realism=rp)
tpar = trk.TrackParams.from_dict(cli.section(cfg, "tracks"))
prep_clean = trk.prepare_tracks(trk.tracks_from_frame(trk.tracks_to_frame(sc.tracks, frame), tpar), tpar)
cli.classify_tracks(cfg, prep_clean, f"{scen}/aircraft_db.csv")

ev_icao = [e["icao24"] for e in truth["events"]]
true_rx = [e["t_cpa_received_rel_s"] for e in truth["events"]]
c_true = truth["realism"]["c_true_mps"]; h_true = truth["realism"]["mic_height_true_m"]
dt_true = -truth["realism"]["clock_error_s"]
m = truth["mic"]; f = LocalFrame(**truth["enu_origin"])
sp = score.ScoreParams.from_dict(cli.section(cfg, "score"))

def match_events(events):
    """observed event index -> true event index by received CPA time (within 8 s)."""
    out = {}
    for k, e in enumerate(events):
        j = int(np.argmin([abs(e.t_rx_s - t) for t in true_rx]))
        if abs(events[k].t_rx_s - true_rx[j]) < 8: out[k] = j
    return out

def surface(prep, c, h_mic, label):
    events, prior, ts, spec, cp = cli.cascade_inputs(cfg, fits, prep, tprior, duration, c, region)
    mt = match_events(events)
    ridges = score.prepare_ridges(events, sp)
    assign = []
    for k, e in enumerate(events):
        if k not in mt: assign.append(-1); continue
        ic = ev_icao[mt[k]]
        t_abs = prior.start_s + e.t_rx_s + dt_true
        cands = [i for i in range(ts.n_tracks) if ts.icao24[i] == ic]
        best = max(cands, key=lambda i: np.isfinite(ts.sample(np.array([i]), np.array([t_abs]))[0][0, 0]))
        assign.append(best)
    assign = tuple(assign)
    E0, N0 = m["enu_m"][0], m["enu_m"][1]
    def xyz(de, dn):
        la, lo, _ = f.to_geodetic(np.array([E0 + de, N0 + dn, 0.0]))
        return geodetic_to_ecef(la, lo, m["ground_elev_m"] + h_mic)
    dts = np.arange(dt_true - 4, dt_true + 4, 0.05)
    tot = sum(len(r.t_s) for k, r in enumerate(ridges) if assign[k] >= 0)
    def cost(de, dn):
        cst, f0, rms, miss = score._hyp_cost(dts, ridges, assign, xyz(de, dn), ts, prior.start_s, c, sp.huber_k)
        s = cst.sum(0); j = int(np.argmin(s))
        return s[j] / tot, dts[j], rms[:, j], miss[:, j]
    g = np.arange(-600, 601, 40.0)
    C = np.array([[cost(de, dn)[0] for de in g] for dn in g])
    jn, je = np.unravel_index(np.argmin(C), C.shape)
    # refine around the coarse min
    g2 = np.arange(-40, 41, 8.0)
    C2 = np.array([[cost(g[je] + a, g[jn] + b)[0] for a in g2] for b in g2])
    bn, be = np.unravel_index(np.argmin(C2), C2.shape)
    de, dn = g[je] + g2[be], g[jn] + g2[bn]
    cm, dtm, rmsm, missm = cost(de, dn)
    ct, dtt, rmst, misst = cost(0, 0)
    # sublevel set area: grid points whose cost <= cost(truth)
    area_cells = (C <= ct).sum() * 40 * 40 / 200 ** 2
    print(f"{label:28s} min at dE={de:+5.0f} dN={dn:+5.0f} (|{np.hypot(de, dn):4.0f}| m) dt={dtm:+.2f} (true {dt_true:+.2f}) "
          f"cost min {cm:.3f} truth {ct:.3f} (dt {dtt:+.2f}); rms@min {np.round(rmsm, 2)} rms@truth {np.round(rmst, 2)} "
          f"miss@truth {misst}; sublevel(truth) ~{area_cells:.0f} cells; assign {assign}")

print("events:", [(round(e.t_rx_s, 1), round(e.v_mps, 1), round(e.d_m)) for e in cascade.observed_events(fits, c_cfg)])
surface(prep_deg, c_cfg, 1.5, "base")
surface(prep_clean, c_cfg, 1.5, "clean_adsb")
surface(prep_clean, c_true, 1.5, "clean_adsb+c_true")
surface(prep_clean, c_true, h_true, "clean_adsb+c_true+h_true")
surface(prep_deg, c_true, h_true, "deg_adsb+c_true+h_true")
