import sys, json
import numpy as np, soundfile as sf
import cli
from doppler import audio, score
from doppler.geo import LocalFrame, geodetic_to_ecef
cfg = cli.load_config(sys.argv[1]); scen = sys.argv[2]
truth = json.load(open(f"{scen}/truth.json"))
c = cli.speed_of_sound_from(cfg)
x, fs = sf.read(f"{scen}/audio.flac", always_2d=False); x = audio.mix_to_mono(x)
tprior = audio.parse_time_prior(json.load(open(f"{scen}/audio.json")))
_, fits = cli.run_events(x, fs, cfg, c)
_, region = cli.load_region(cfg, None)
prep = cli.load_tracks(cfg, f"{scen}/tracks.parquet", f"{scen}/aircraft_db.csv", (tprior.start_s - 130, tprior.start_s + 130))
events, prior, ts, spec, cp = cli.cascade_inputs(cfg, fits, prep, tprior, len(x) / fs, c, region)
sp = score.ScoreParams.from_dict(cli.section(cfg, "score"))
rd = score.prepare_ridges(events, sp)[0]
m = truth["mic"]; f = LocalFrame(**truth["enu_origin"])
la, lo, _ = f.to_geodetic(np.array(m["enu_m"][:2] + [0.0]))
P = geodetic_to_ecef(la, lo, m["ground_elev_m"] + 1.5)
dt_true = -truth["realism"]["clock_error_s"]
segs = [i for i in range(ts.n_tracks) if ts.icao24[i] == truth["events"][0]["icao24"]]
for k in segs:
    v = ts.valid()[k]; tt = ts.t_s[v]
    print("segment", k, "coverage rel to stated start:", round(tt[0] - prior.start_s, 2), round(tt[-1] - prior.start_s, 2))
k = 34 if 34 in segs else segs[0]
tr = prior.start_s + dt_true + rd.t_s
ratio, te, r, ok = score.modeled_ratio(P, k, ts, tr, c)
bad = ~ok
print("missing ridge points (rx rel stated start):", np.round(rd.t_s[bad] + dt_true, 2))
ex = __import__('doppler.physics', fromlist=['x'])
v = ts.valid()[k]
te_exact = ex.emission_times(tr[bad], ts.t_s[v], ts.xyz[k][v], P, c, ts.vel[k][v])
print("their exact emission times rel stated start:", np.round(te_exact - prior.start_s, 2), " ridge t range", rd.t_s.min(), rd.t_s.max())
import mrfix
r2, te2, _, ok2 = mrfix.mr_fixed(P, k, ts, tr, c)
print("fixed seed: missing", int((~ok2).sum()), "te of formerly missing", np.round(te2[bad] - prior.start_s, 2))
import doppler.score as S_
S_.modeled_ratio = mrfix.mr_fixed
cst, f0, rms, miss = score._hyp_cost(np.array([dt_true]), [rd], (k,), P, ts, prior.start_s, c, sp.huber_k)
print("_hyp_cost with patched module: miss", miss.ravel())
