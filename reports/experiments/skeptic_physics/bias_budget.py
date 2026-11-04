"""Linearised/nonlinear bias budget for locked 2-event scoring (L2 surrogate of the pooled Huber cost).

Truth: mic at P0, two straight tracks. 'Observed' ridges are generated with a perturbed physics/ADS-B
(one effect at a time), then fitted with the pipeline's nominal model (still air, fixed c, ADS-B track as given,
constant f0 per event, shared dt). Reports location bias and the cost penalty of the TRUE point relative to the
minimum, in units of chi^2 (sigma = ridge scatter per decimated point).
"""
import numpy as np
from scipy.optimize import least_squares, brentq
from doppler import physics as ph

C = 343.0
SIG = 0.15        # Hz, ridge scatter per 0.5 s decimated point (typical noise_hz)
DEC = 0.5
P0 = np.array([0.0, 0.0, 1.5])


def track(heading_deg, offset_m, alt_m, v, t_cpa, t, climb=0.0):
    psi = np.deg2rad(heading_deg)
    u = np.array([np.sin(psi), np.cos(psi), 0.0])
    n = np.array([np.cos(psi), -np.sin(psi), 0.0])
    A = (offset_m * n)[None, :] + (t - t_cpa)[:, None] * v * u[None, :]
    A[:, 2] = alt_m + climb * (t - t_cpa)
    V = np.tile(v * u, (len(t), 1)); V[:, 2] = climb
    return A, V


def exact_wind_rx(A, te, P, c, w):
    """Exact uniform-wind propagation: |P - A - w tau| = c tau. Returns t_r."""
    rel = P[None, :] - A
    w = np.asarray(w, float)
    # (rel - w tau)^2 = c^2 tau^2 -> (w.w - c^2) tau^2 - 2 rel.w tau + rel.rel = 0
    a = w @ w - c * c
    b = -2 * rel @ w
    cc = np.einsum('ij,ij->i', rel, rel)
    tau = (-b - np.sqrt(b * b - 4 * a * cc)) / (2 * a)
    return te + tau


def observe(spec, t_obs, f0, c=C, wind=(0, 0, 0), P=P0, ramp=None, tlat=0.0, alt_err=0.0, vq=0.0):
    """Synthesise ridge (t_obs received times) with true physics; returns f_obs."""
    te = np.arange(spec['t_cpa'] - 400, spec['t_cpa'] + 400, 0.05)
    A, V = track(spec['hdg'], spec['off'], spec['alt'], spec['v'], spec['t_cpa'], te)
    tr = exact_wind_rx(A, te, P, c, np.asarray(wind, float))
    # frequency ratio = dte/dtr
    ratio = np.gradient(te, tr)
    fs = f0 * np.ones_like(te)
    if ramp is not None:
        frac, tc, dur = ramp
        fs = f0 * (1 + frac * 0.5 * (1 + np.tanh((te - tc) / (0.25 * dur))))
    return np.interp(t_obs, tr, fs * ratio)


def model_tracks(spec, tlat=0.0, alt_err=0.0, dv=0.0):
    """What ADS-B gives the pipeline: track times shifted by tlat (reported time = true + tlat), altitude error."""
    t = np.arange(spec['t_cpa'] - 400, spec['t_cpa'] + 400, 0.25)
    A, V = track(spec['hdg'], spec['off'], spec['alt'] + alt_err, spec['v'], spec['t_cpa'], t)
    V = V * (spec['v'] + dv) / spec['v']
    return t + tlat, A, V


def model_f(theta, specs, tobs, mts, c=C, zmic=1.5):
    E, N, dt = theta[:3]
    P = np.array([E, N, zmic])
    out = []
    for i, (s, t, (tt, A, V)) in enumerate(zip(specs, tobs, mts)):
        r, _, _ = ph.predicted_ratio_at(t + dt, tt, A, P, c, V)
        out.append(theta[3 + i] * r)
    return out


def fit(specs, tobs, fobs, mts, c=C, zmic=1.5, x0=None):
    f0s = [s['f0'] for s in specs]
    x0 = np.r_[0.0, 0.0, 0.0, f0s] if x0 is None else x0
    def res(th):
        return np.concatenate([(m - f) / SIG for m, f in zip(model_f(th, specs, tobs, mts, c, zmic), fobs)])
    best = None
    for e0 in (-300, 0, 300):
        for n0 in (-300, 0, 300):
            r = least_squares(res, np.r_[e0, n0, 0.0, f0s], x_scale=[100, 100, 1, 1, 1])
            if best is None or r.cost < best.cost:
                best = r
    chi_true = float(np.sum(res(np.r_[0, 0, 0, best.x[3:]]) ** 2))
    # true point with profiled f0 and dt
    def res_t(z):
        return res(np.r_[0, 0, z[0], z[1:]])
    rt = least_squares(res_t, np.r_[0.0, best.x[3:]], x_scale=[1, 1, 1])
    return best, 2 * rt.cost, 2 * best.cost, res


def crlb(specs, tobs, mts):
    th = np.r_[0, 0, 0, [s['f0'] for s in specs]]
    def res(t):
        return np.concatenate(model_f(t, specs, tobs, mts)) / SIG
    J = np.zeros((len(res(th)), len(th)))
    h = np.r_[0.5, 0.5, 0.005, 0.01, 0.01]
    for k in range(len(th)):
        e = np.zeros(len(th)); e[k] = h[k]
        J[:, k] = (res(th + e) - res(th - e)) / (2 * h[k])
    Cov = np.linalg.inv(J.T @ J)
    w, U = np.linalg.eigh(Cov[:2, :2])
    return np.sqrt(np.diag(Cov)), np.sqrt(w), U


def run(geom_name, specs, half_s=60.0):
    tobs = [np.arange(s['t_rx'] - half_s, s['t_rx'] + half_s, DEC) for s in specs]
    # set t_cpa (emission) so CPA sound arrives at t_rx (no wind)
    nominal = [model_tracks(s) for s in specs]
    sd, axes, U = crlb(specs, tobs, nominal)
    print(f"\n=== {geom_name}: CRLB sigma E,N={sd[0]:.1f},{sd[1]:.1f} m  dt={sd[2]*1e3:.0f} ms; "
          f"error-ellipse axes {axes[0]:.1f} x {axes[1]:.1f} m (sigma={SIG} Hz/pt)")
    cases = {
        'clean': dict(),
        'c +3 m/s': dict(c=C + 3),
        'wind 10 m/s E': dict(wind=(10, 0, 0)),
        'wind 10 m/s N': dict(wind=(0, 10, 0)),
        'mic 10 m (model 1.5)': dict(P=np.array([0, 0, 10.0])),
        'ramp +5%/25s @CPA+5 ev0': dict(ramp0=(0.05, 5.0, 25.0)),
        'ramp +5%/25s @CPA-20 ev0': dict(ramp0=(0.05, -20.0, 25.0)),
        'latency +0.6/-0.6 s': dict(tlat=(0.6, -0.6)),
        'baro +60 m ev1': dict(alt=(0, 60)),
        'speed quant +0.25 m/s both': dict(dv=(0.25, 0.25)),
    }
    for name, kw in cases.items():
        fobs = []
        for i, (s, t) in enumerate(zip(specs, tobs)):
            ramp = None
            if i == 0 and 'ramp0' in kw:
                fr, off, dur = kw['ramp0']
                ramp = (fr, s['t_cpa'] + off, dur)
            fobs.append(observe(s, t, s['f0'], c=kw.get('c', C), wind=kw.get('wind', (0, 0, 0)),
                                P=kw.get('P', P0), ramp=ramp))
        mts = [model_tracks(s, tlat=kw.get('tlat', (0, 0))[i], alt_err=kw.get('alt', (0, 0))[i],
                            dv=kw.get('dv', (0, 0))[i]) for i, s in enumerate(specs)]
        best, chi_t, chi_min, _ = fit(specs, tobs, fobs, mts)
        e, n, dt = best.x[:3]
        print(f"{name:28s} bias E={e:7.1f} N={n:7.1f} |{np.hypot(e, n):6.1f}| m  dt={dt:+.3f}s  "
              f"chi2(min)={chi_min:9.1f}  chi2(true)-chi2(min)={chi_t - chi_min:9.1f}  npts={sum(map(len, tobs))}")


def mk(hdg, off, alt, v, t_rx, f0):
    s = dict(hdg=hdg, off=off, alt=alt, v=v, f0=f0, t_rx=t_rx)
    d = np.hypot(off, alt - 1.5)
    s['t_cpa'] = t_rx - d / C
    return s


if __name__ == '__main__':
    import sys
    ev0 = mk(90, 600, 600, 60, 150, 120)
    run('crossing 90 deg', [ev0, mk(0, 400, 500, 70, 420, 150)])
    run('corridor same dir (+10 deg)', [ev0, mk(100, 400, 500, 70, 420, 150)])
    run('corridor same dir (+10 deg), equal speeds', [ev0, mk(100, 400, 500, 60, 420, 150)])
    run('reciprocal (270+10 deg)', [ev0, mk(280, 400, 500, 70, 420, 150)])
    run('near-overhead ev1 (off 150)', [ev0, mk(0, 150, 500, 70, 420, 150)])
