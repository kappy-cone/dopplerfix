import numpy as np
def mr_fixed(mic_xyz, track, ts, t_rx_s, c, n_iter=4):
    # seed Newton with the track sample clamped into its own coverage (the fix), then the original iteration
    P = np.asarray(mic_xyz, float).reshape(1, 3); tr = np.asarray(t_rx_s, float)
    k = np.full(tr.shape, int(track))
    v = ts.valid()[int(track)]; tt = ts.t_s[v]
    A, _ = ts.sample(k, np.clip(tr, tt[0], tt[-1] - 1e-6))
    te = tr - np.linalg.norm(A - P, axis=-1) / c
    for _ in range(n_iter + 2):
        A, V = ts.sample(k, np.clip(te, tt[0], tt[-1] - 1e-6))
        rel = A - P; r = np.linalg.norm(rel, axis=-1); rdot = np.einsum("...k,...k->...", rel, V) / r
        te = te - (te + r / c - tr) / (1.0 + rdot / c)
    A, V = ts.sample(k, te)
    rel = A - P; r = np.linalg.norm(rel, axis=-1); rdot = np.einsum("...k,...k->...", rel, V) / r
    ratio = c / (c + rdot)
    return ratio, te, r, np.isfinite(ratio)
