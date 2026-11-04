"""Same as bias_budget but with the pipeline's Huber (k=3 sigma) loss, dt bounded to the ±10 s prior,
and the position bounded to ±1.5 km: ramps, RPM walk, latency, baro, combined."""
import numpy as np
from scipy.optimize import least_squares
from scipy.ndimage import gaussian_filter1d
import bias_budget as bb

K = 3.0

def fit_h(specs, tobs, fobs, mts):
    f0s = [s['f0'] for s in specs]
    def res(th):
        return np.concatenate([(m - f) / bb.SIG for m, f in zip(bb.model_f(th, specs, tobs, mts), fobs)])
    lb = [-1500, -1500, -10, 0, 0]; ub = [1500, 1500, 10, 1e4, 1e4]
    best = None
    for e0 in (-400, 0, 400):
        for n0 in (-400, 0, 400):
            for d0 in (-5, 0, 5):
                r = least_squares(res, np.r_[e0, n0, d0, f0s], bounds=(lb, ub), loss='huber', f_scale=K,
                                  x_scale=[100, 100, 1, 1, 1])
                if best is None or r.cost < best.cost:
                    best = r
    rt = least_squares(lambda z: res(np.r_[0, 0, z[0], z[1:]]), np.r_[0.0, best.x[3:]], loss='huber', f_scale=K,
                       bounds=([-10, 0, 0], [10, 1e4, 1e4]))
    n = sum(map(len, tobs))
    # scipy huber cost = 0.5*sum rho(z^2) with rho scaled by f_scale^2 -> convert to pipeline mean Huber per point
    return best, best.cost / n, rt.cost / n

def walk(rng, te, frac, dt=0.05):
    w = np.cumsum(rng.standard_normal(te.size))
    w = gaussian_filter1d(w - w.mean(), sigma=2.0 / dt)
    return frac * w / np.std(w)

def run(name, specs, cases, half_s=60.0, seed=0):
    tobs = [np.arange(s['t_rx'] - half_s, s['t_rx'] + half_s, bb.DEC) for s in specs]
    print(f"\n=== {name}")
    for cname, kw in cases.items():
        rng = np.random.default_rng(seed)
        fobs = []
        for i, (s, t) in enumerate(zip(specs, tobs)):
            ramp = None
            if i == 0 and 'ramp0' in kw:
                fr, off, dur = kw['ramp0']; ramp = (fr, s['t_cpa'] + off, dur)
            f = bb.observe(s, t, s['f0'], c=kw.get('c', bb.C), wind=kw.get('wind', (0, 0, 0)), P=kw.get('P', bb.P0), ramp=ramp)
            if kw.get('walk'):
                te = np.arange(s['t_cpa'] - 420, s['t_cpa'] + 420, 0.05)
                wv = walk(rng, te, kw['walk'])
                f = f * (1 + np.interp(t, te + 0.0, wv))
            fobs.append(f)
        mts = [bb.model_tracks(s, tlat=kw.get('tlat', (0, 0))[i], alt_err=kw.get('alt', (0, 0))[i],
                               dv=kw.get('dv', (0, 0))[i]) for i, s in enumerate(specs)]
        best, cmin, ctrue = fit_h(specs, tobs, fobs, mts)
        e, n, dt = best.x[:3]
        print(f"{cname:34s} bias |{np.hypot(e, n):6.1f}| m (E={e:7.1f},N={n:7.1f}) dt={dt:+.2f}s  "
              f"meanHuber(min)={cmin:7.3f} (true)={ctrue:7.3f}")

ev0 = bb.mk(90, 600, 600, 60, 150, 120)
geoms = {'crossing 90': [ev0, bb.mk(0, 400, 500, 70, 420, 150)],
         'corridor same dir +10deg': [ev0, bb.mk(100, 400, 500, 70, 420, 150)],
         'reciprocal +10deg': [ev0, bb.mk(280, 400, 500, 70, 420, 150)]}
cases = {
  'ramp 3%/20s @CPA+5': dict(ramp0=(0.03, 5.0, 20.0)),
  'ramp 10%/20s @CPA+5': dict(ramp0=(0.10, 5.0, 20.0)),
  'ramp 3%/40s @CPA-30 (pre side)': dict(ramp0=(0.03, -30.0, 40.0)),
  'rpm walk 0.3% (seed0)': dict(walk=0.003),
  'latency +-0.6 + baro 60 ev1': dict(tlat=(0.6, -0.6), alt=(0, 60)),
  'combo: walk+lat+baro+c+wind+mic': dict(walk=0.003, tlat=(0.6, -0.6), alt=(0, 60), c=346, wind=(7, 7, 0), P=np.array([0, 0, 6.0])),
}
for g, sp in geoms.items():
    run(g, sp, cases)
