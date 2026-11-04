"""fit_ridge covariance honesty: Monte Carlo coverage of sigma(v,d,t_rx), and the gof factor, on ridges with
(a) white noise, (b) correlated noise like a 1 s/0.1 s spectrogram, (c) a 3% RPM ramp, (d) edge clipping."""
import numpy as np
from scipy.ndimage import uniform_filter1d
from doppler import physics as ph
from doppler.ridge import Ridge, robust_std
from doppler.detect import EventWindow
from doppler.fit import fit_ridge, FitParams
from scipy.signal import savgol_filter

C = 343.0
f0, v, d, t0 = 120.0, 60.0, 700.0, 100.0
hop = 0.1
rng = np.random.default_rng(1)
W = EventWindow(0, 400, (40, 900))

def ridge(t, f):
    wl = 31
    noise = robust_std(f - savgol_filter(f, wl, 2))
    return Ridge(t, f, np.full(len(t), 20.0), noise, (40, 900))

def trial(kind, n=60):
    out = []
    t = np.arange(t0 + d / C - 60, t0 + d / C + 60, hop)
    if kind == 'clip':
        t = t[t > t0 + d / C - 8]
    for _ in range(n):
        f = ph.straight_freq_received(t, f0, v, d, t0, C)
        if kind == 'white':
            f = f + rng.normal(0, 0.2, len(t))
        else:
            e = rng.normal(0, 0.2 * np.sqrt(10), len(t))
            f = f + uniform_filter1d(e, 10)      # 1 s window / 0.1 s hop: ~10-frame correlation, std ~0.2
        if kind == 'ramp':
            f = f * (1 + 0.03 * 0.5 * (1 + np.tanh((t - (t0 + d / C + 5)) / 5.0)))
        r = fit_ridge(ridge(t, f), W, FitParams(), C, hop, 1.0)
        trx = r.t_cpa_received_s
        J = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 1 / C, 1]], float)
        cov3 = J @ r.cov[:4, :4] @ J.T
        s3 = np.sqrt(np.diag(cov3))
        err = np.array([r.v_mps - v, r.d_m - d, trx - (t0 + d / C)])
        out.append((err / s3, s3, r.rms_hz / r.noise_hz, r.noise_hz, r.ridge.noise_hz, err))
    z = np.array([o[0] for o in out]); s = np.array([o[1] for o in out]); e = np.array([o[5] for o in out])
    g = np.array([o[2] for o in out]); nh = np.array([o[3] for o in out]); rn = np.array([o[4] for o in out])
    print(f"{kind:6s} std(z) v,d,t = {z.std(0).round(2)}  mean(z)={z.mean(0).round(2)}  "
          f"median sigma v,d,t = {np.median(s,0).round(3)}  actual rms err = {np.sqrt((e**2).mean(0)).round(3)}  "
          f"gof(median)={np.median(g):.3f}  noise_hz={np.median(nh):.3f} ridge.noise_hz={np.median(rn):.3f}")

for k in ('white', 'corr', 'ramp', 'clip'):
    trial(k)
