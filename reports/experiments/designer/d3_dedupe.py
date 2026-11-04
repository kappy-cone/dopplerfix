"""System F12: how often does fit._simple_ratio(tol 0.03, max_den 4) accept the f0 ratio of two unrelated props?
Also: the same for tighter settings, to see what a physics-derived tolerance (f0 sigma) would give."""
import numpy as np
from doppler.fit import _simple_ratio
rng = np.random.default_rng(0)
a, b = rng.uniform(80, 180, 20000), rng.uniform(80, 180, 20000)
r = np.maximum(a, b) / np.minimum(a, b)
for tol, md in ((0.03, 4), (0.01, 4), (0.03, 2), (0.005, 2)):
    print(f"tol {tol:5.3f} max_den {md}: accept {np.mean([_simple_ratio(x, tol, md) for x in r]):.1%}")
