"""score.modeled_ratio seeds Newton with the track position at the RECEIVED time. Within r/c of a segment's
end (or a >max_gap split) that sample is NaN, so the point is declared unmodelable (penalty 22.5) although its
emission time is inside the track."""
import numpy as np
from doppler.tracks import TrackSet
from doppler import score, physics as ph
dt = 0.25
t = np.arange(0, 100 + 1e-9, dt)
K, T = 1, len(t)
v = 60.0
xyz = np.zeros((1, T, 3)); xyz[0, :, 0] = (t - 50) * v; xyz[0, :, 1] = 1000.0; xyz[0, :, 2] = 800.0
vel = np.zeros((1, T, 3)); vel[0, :, 0] = v
ts = TrackSet(np.array(['a'], object), np.array([''], object), np.array([0], np.int8), np.array(['geometric'], object),
              0.0, dt, xyz, vel, xyz[..., 2].copy(), np.full((1, T), v))
P = np.array([0.0, 0.0, 0.0])
tr = np.arange(90, 106, 0.5)
ratio, te, r, ok = score.modeled_ratio(P, 0, ts, tr, 343.0)
for a, b, c_, d in zip(tr, te, r, ok):
    exact = ph.emission_times(np.array([a]), t, xyz[0], P, 343.0, vel[0])[0]
    print(f"t_rx {a:6.1f}  exact t_emit {exact:7.2f} (inside track: {exact <= 100})  scorer valid={d}")
