"""Designer re-checks (physics skeptic F1, F2, F4):
 A. Newton-seed edge bug in score.modeled_ratio (own minimal case).
 B. Corridor amplification: analytic along-track bias = dt_rel / |1/v1 - 1/v2| vs nonlinear fit.
 C. Does a per-event linear f0-drift nuisance (prior sigma_beta) remove RPM-ramp bias, and what does it cost
    in clean precision (CRLB)?
 D. Linearised 'systematic-aware' covariance: propagate per-track timing (0.5 s) and altitude (60 m) priors
    through the estimator; does the predicted error ellipse cover the measured biases?
L2 loss, sigma 0.15 Hz per 0.5 s point, +-60 s ridges, true c, still air (uses skeptic bias_budget helpers)."""
import sys
sys.path.insert(0, "/private/tmp/claude-501/-Users-williambarr-qgis/c334e685-7499-4d45-9232-dd5dbcafcf8c/scratchpad/skeptic_physics")
import numpy as np
from scipy.optimize import least_squares
import bias_budget as bb
from doppler import score, physics as ph
from doppler.tracks import TrackSet

# ---------- A. edge bug
dt = 0.25; t = np.arange(0, 100 + 1e-9, dt); T = len(t); v = 60.0
xyz = np.zeros((1, T, 3)); xyz[0, :, 0] = (t - 50) * v; xyz[0, :, 1] = 1000.0; xyz[0, :, 2] = 800.0
vel = np.zeros((1, T, 3)); vel[0, :, 0] = v
ts = TrackSet(np.array(['a'], object), np.array([''], object), np.array([0], np.int8), np.array(['geometric'], object),
              0.0, dt, xyz, vel, xyz[..., 2].copy(), np.full((1, T), v))
P = np.zeros(3); tr = np.arange(96, 106, 1.0)
ratio, te, r, ok = score.modeled_ratio(P, 0, ts, tr, 343.0)
exact = ph.emission_times(tr, t, xyz[0], P, 343.0, vel[0])
n_false = int(np.sum((~ok) & (exact <= 100 - dt)))
print(f"A. edge bug: received 96..105 s, exact t_emit {exact.min():.1f}..{exact.max():.1f} s (track ends 100 s); "
      f"scorer invalid {int((~ok).sum())}/{len(tr)}, of which falsely invalid {n_false}")

# ---------- B/C/D
SIG = bb.SIG
def model(theta, specs, tobs, mts, beta_on):
    E, N, d = theta[:3]; Pm = np.array([E, N, 1.5]); out = []
    nE = len(specs)
    for i, (s, tt, (tk, A, V)) in enumerate(zip(specs, tobs, mts)):
        rr, _, _ = ph.predicted_ratio_at(tt + d, tk, A, Pm, bb.C, V)
        f0 = theta[3 + i]
        if beta_on:
            f0 = f0 * (1 + theta[3 + nE + i] * (tt - s['t_rx']))
        out.append(f0 * rr)
    return out

def fit(specs, tobs, fobs, mts, beta_on=False, sb=1e-3):
    nE = len(specs); f0s = [s['f0'] for s in specs]
    def res(th):
        r = np.concatenate([(m - f) / SIG for m, f in zip(model(th, specs, tobs, mts, beta_on), fobs)])
        return np.r_[r, th[3 + nE:] / sb] if beta_on else r
    best = None
    for e0 in (-300, 0, 300):
        for n0 in (-300, 0, 300):
            x0 = np.r_[e0, n0, 0.0, f0s, np.zeros(nE) if beta_on else []]
            lb = np.r_[-2000, -2000, -10, np.zeros(nE), -np.ones(nE) if beta_on else []]
            ub = np.r_[2000, 2000, 10, 1e4 * np.ones(nE), np.ones(nE) if beta_on else []]
            r_ = least_squares(res, x0, bounds=(lb, ub), x_scale=np.r_[100, 100, 1, np.ones(nE), sb * np.ones(nE) if beta_on else []])
            if best is None or r_.cost < best.cost:
                best = r_
    J = best.jac; cov = np.linalg.inv(J.T @ J)
    return best.x, cov, best

def obs(specs, tobs, ramp=None):
    out = []
    for i, (s, tt) in enumerate(zip(specs, tobs)):
        rp = None
        if ramp is not None and i == 0:
            fr, off, dur = ramp; rp = (fr, s['t_cpa'] + off, dur)
        out.append(bb.observe(s, tt, s['f0'], ramp=rp))
    return out

ev0 = bb.mk(90, 600, 600, 60, 150, 120)
geoms = {'crossing 90': [ev0, bb.mk(0, 400, 500, 70, 420, 150)],
         'same dir +10, 60/70 m/s': [ev0, bb.mk(100, 400, 500, 70, 420, 150)],
         'reciprocal +10': [ev0, bb.mk(280, 400, 500, 70, 420, 150)]}
for g, specs in geoms.items():
    tobs = [np.arange(s['t_rx'] - 60, s['t_rx'] + 60, bb.DEC) for s in specs]
    nom = [bb.model_tracks(s) for s in specs]
    print(f"\n=== {g}")
    # B: latency +0.6/-0.6 -> relative 1.2 s
    lat = [bb.model_tracks(s, tlat=l) for s, l in zip(specs, (0.6, -0.6))]
    x, cov, _ = fit(specs, tobs, obs(specs, tobs), lat)
    v1, v2 = specs[0]['v'], specs[1]['v']
    # analytic along-track bias for same-direction tracks only
    print(f"B. latency +-0.6 s: fitted bias {np.hypot(x[0], x[1]):6.0f} m, dt {x[2]:+.2f} s; "
          f"1.2 s/|1/v1-1/v2| = {1.2/abs(1/v1-1/v2):.0f} m (valid only for parallel tracks)")
    # C: ramp 3%/20 s @ CPA+5 on event 0; constant f0 vs drift nuisance
    fo = obs(specs, tobs, ramp=(0.03, 5.0, 20.0))
    for beta_on, sb in ((False, None), (True, 3e-4), (True, 1e-3)):
        x, cov, _ = fit(specs, tobs, fo, nom, beta_on, sb or 1e-3)
        xc, covc, _ = fit(specs, tobs, obs(specs, tobs), nom, beta_on, sb or 1e-3)
        w = np.sqrt(np.linalg.eigvalsh(covc[:2, :2]))
        lab = 'constant f0' if not beta_on else f'drift prior {sb:g}/s'
        print(f"C. ramp 3%/20s, {lab:18s}: bias {np.hypot(x[0], x[1]):6.0f} m dt {x[2]:+.2f}  | clean CRLB axes {w[1]:.1f} x {w[0]:.1f} m")
    # D: systematic-aware covariance: G = d(theta_hat)/d(nuisance) by finite differences; nuisance prior cov
    x0, cov0, _ = fit(specs, tobs, obs(specs, tobs), nom)
    nuis = {'lat0': (0.5, lambda h: [bb.model_tracks(specs[0], tlat=h), nom[1]]),
            'lat1': (0.5, lambda h: [nom[0], bb.model_tracks(specs[1], tlat=h)]),
            'alt0': (60.0, lambda h: [bb.model_tracks(specs[0], alt_err=h), nom[1]]),
            'alt1': (60.0, lambda h: [nom[0], bb.model_tracks(specs[1], alt_err=h)])}
    G = []; S = []
    fo0 = obs(specs, tobs)
    for k, (s_, mk) in nuis.items():
        h = 0.1 * s_
        xp, _, _ = fit(specs, tobs, fo0, mk(h)); xm, _, _ = fit(specs, tobs, fo0, mk(-h))
        G.append((xp[:2] - xm[:2]) / (2 * h)); S.append(s_)
    G = np.array(G).T; Sn = np.diag(np.square(S))
    Csys = cov0[:2, :2] + G @ Sn @ G.T
    w = np.sqrt(np.linalg.eigvalsh(Csys))
    print(f"D. systematic-aware 1-sigma ellipse (lat 0.5 s, alt 60 m per track): {w[1]:.0f} x {w[0]:.0f} m "
          f"(noise-only {np.sqrt(np.linalg.eigvalsh(cov0[:2,:2]))[1]:.1f} m)")
