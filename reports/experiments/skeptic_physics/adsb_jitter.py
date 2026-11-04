"""Modeled-curve error from ADS-B position noise as the pipeline uses it: PCHIP through noisy 1 s fixes
(no smoothing), reported velocity interpolated linearly, rdot = rel.V/r. Compare against the clean curve."""
import numpy as np
from scipy.interpolate import PchipInterpolator
from doppler import physics as ph
rng = np.random.default_rng(0)
C, f0, v = 343.0, 150.0, 60.0
P = np.array([0, 0, 1.5])
def run(sig, mlat_frac=0.0, off=500, alt=600, vel_from='adsb'):
    tf = np.cumsum(np.clip(rng.normal(1.0, 0.3, 400), 0.2, None)) - 200
    A = np.c_[tf * v, np.full_like(tf, off), np.full_like(tf, alt)]
    s = np.where(rng.uniform(size=tf.size) < mlat_frac, 100.0, sig)
    An = A.copy(); An[:, :2] += rng.normal(0, 1, (tf.size, 2)) * s[:, None]
    tg = np.arange(-150, 150, 0.25)
    Ag = PchipInterpolator(tf, An, axis=0)(tg)
    if vel_from == 'adsb':
        Vg = np.tile([v, 0, 0], (tg.size, 1))
    else:
        Vg = PchipInterpolator(tf, An, axis=0).derivative()(tg)
    Ac = np.c_[tg * v, np.full_like(tg, off), np.full_like(tg, alt)]
    tr = np.arange(-50, 50, 0.5) + np.hypot(off, alt) / C
    rc, _, _ = ph.predicted_ratio_at(tr, tg, Ac, P, C, np.tile([v, 0, 0], (tg.size, 1)))
    rn, _, _ = ph.predicted_ratio_at(tr, tg, Ag, P, C, Vg)
    err = f0 * (rn - rc)
    near = np.abs(tr - tr.mean()) < 15
    return np.sqrt(np.mean(err ** 2)), np.sqrt(np.mean(err[near] ** 2)), np.abs(err).max()
for sig, ml, vf in ((7, 0, 'adsb'), (7, 0.03, 'adsb'), (7, 0, 'derivative'), (7, 0.03, 'derivative')):
    o = np.array([run(sig, ml, vel_from=vf) for _ in range(30)])
    print(f"pos sigma {sig} m, MLAT frac {ml}, velocity={vf:10s}: freq error RMS all={o[:,0].mean():.3f} Hz, "
          f"|t-tcpa|<15s={o[:,1].mean():.3f} Hz, max={o[:,2].max():.2f} Hz  (f0={f0} Hz, slant 781 m)")
