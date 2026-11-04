"""Residual statistics of real (synthetic-audio) ridges as the scorer sees them: raw vs 0.5 s median-decimated,
lag autocorrelation, and the scorer's sigma (fit noise_hz) vs the actual scatter of decimated points."""
import sys, json
import numpy as np, soundfile as sf
import cli
from doppler import audio, score, cascade
cfg = cli.load_config(sys.argv[1]); scen = sys.argv[2]
c = cli.speed_of_sound_from(cfg)
x, fs = sf.read(f"{scen}/audio.flac", always_2d=False)
_, fits = cli.run_events(audio.mix_to_mono(x), fs, cfg, c)
sp = score.ScoreParams.from_dict(cli.section(cfg, "score"))
evs = cascade.observed_events(fits, c)
rd = score.prepare_ridges(evs, sp)
for e, f_, r in zip(evs, fits, rd):
    res_raw = f_.ridge.f_hz - f_.model(f_.ridge.t_s)
    res_dec = r.f_hz - f_.model(r.t_s)
    def ac(z, k): z = z - z.mean(); return float(np.sum(z[:-k] * z[k:]) / np.sum(z * z))
    print(f"event t_rx {e.t_rx_s:6.1f}: n_raw {len(res_raw)} n_dec {len(res_dec)}  scorer sigma {r.sigma_hz:.3f} Hz  "
          f"ridge.noise_hz(SG) {f_.ridge.noise_hz:.3f}  std raw {res_raw.std():.3f} std dec {res_dec.std():.3f}  "
          f"raw ac lag1..5 {[round(ac(res_raw, k), 2) for k in (1, 2, 5)]} dec ac lag1,2,4 {[round(ac(res_dec, k), 2) for k in (1, 2, 4)]}")
