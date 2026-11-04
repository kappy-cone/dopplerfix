"""Reviewer check of D-2C: does the drift prior width matter at all, and what beta is fitted?"""
import sys, importlib.util
R="/private/tmp/claude-501/-Users-williambarr-qgis/c334e685-7499-4d45-9232-dd5dbcafcf8c/scratchpad"
sys.path.insert(0, R+"/skeptic_physics")
import numpy as np, io, contextlib
spec = importlib.util.spec_from_file_location("d2", R+"/designer/d2_bias_nuisance.py")
src = open(R+"/designer/d2_bias_nuisance.py").read().split("ev0 = bb.mk")[0]
ns = {}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(src, "d2", "exec"), ns)
bb = ns['bb']; fit = ns['fit']; obs = ns['obs']
ev0 = bb.mk(90, 600, 600, 60, 150, 120)
geoms = {'crossing': [ev0, bb.mk(0, 400, 500, 70, 420, 150)], 'reciprocal': [ev0, bb.mk(280, 400, 500, 70, 420, 150)]}
for g, specs in geoms.items():
    tobs = [np.arange(s['t_rx'] - 60, s['t_rx'] + 60, bb.DEC) for s in specs]
    nom = [bb.model_tracks(s) for s in specs]
    fo = obs(specs, tobs, ramp=(0.03, 5.0, 20.0))
    for sb in (1e-7, 1e-5, 3e-4, 1e-3, 1e-2):
        x, cov, b = fit(specs, tobs, fo, nom, True, sb)
        print(f"{g:10s} sb={sb:g}: bias {np.hypot(x[0],x[1]):6.0f} m dt {x[2]:+.2f} beta {x[5:7]} cost {b.cost:.1f}")
