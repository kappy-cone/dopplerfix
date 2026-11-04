"""Exact uniform-wind propagation (|P - A(te) - w tau| = c tau) for a straight level pass: what does the
closed-form Doppler inversion return, and how big is the travel-time effect? Also compare the synth's
first-order model (c_eff = c + w.k along the straight ray)."""
import numpy as np
import bias_budget as bb
from doppler import physics as ph
c, v = 343.0, 60.0
P = np.array([0.0, 0.0, 0.0])
te = np.arange(-2000, 2000, 0.01)
A = np.c_[te * v, np.full_like(te, 500.0), np.full_like(te, 600.0)]
for name, w in (('none', (0, 0, 0)), ('tail 10', (10, 0, 0)), ('head 10', (-10, 0, 0)), ('cross 10', (0, 10, 0))):
    tr = bb.exact_wind_rx(A, te, P, c, np.array(w, float))
    ratio = np.gradient(te, tr)
    fpre, fpost = ratio[50], ratio[-50]
    vinv = ph.invert_speed(fpre, fpost, c)
    # first-order synth model
    rel = P - A; r = np.linalg.norm(rel, axis=1); k = rel / r[:, None]
    tr1 = te + r / (c + k @ np.array(w, float))
    r1 = np.gradient(te, tr1)
    i0 = np.argmin(r)
    dtau = (tr - te)[i0] - r[i0] / c
    print(f"{name:9s} v_inv={vinv:7.3f} m/s (ground 60)  CPA travel-time change {dtau*1e3:+6.1f} ms at r={r[i0]:.0f} m "
          f"({dtau/r[i0]*1e6:+.1f} ms/km); synth first-order ratio error max {np.abs(r1 - ratio)[100:-100].max():.2e}")
print("r*w/c^2 per km for w=10:", 1000 * 10 / c**2, "s")
