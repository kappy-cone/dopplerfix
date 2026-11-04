"""E6: robust mode (max_unmatched_events=1) vs tile pruning. Event 2 is labelled jet (e.g. a mislabelled
spurious tone) and no jet/unknown track exists in its window -> EventIndex.tree is None."""
import numpy as np
from common import *

DUR = 120.0
t_rel = np.arange(-200, DUR + 100, 1.0)
mic = np.array([0.0, 0.0, 1.5])
A, eA, _ = straight("A", t_rel, np.array([500.0, 0.0]), 0.0, 70.0, 600.0, 30.0 - 781 / C)
Cc, eC, _ = straight("C", t_rel, np.array([400.0, 0.0]), 180.0, 65.0, 700.0, 90.0 - 806 / C)
ev_ok = [truth_event(0, eA, t_rel, mic), truth_event(1, eC, t_rel, mic)]
spur = cs.ObservedEvent(**{**ev_ok[1].__dict__, "cls": at.JET, "idx": 2})
for label, evs, trs in (("2 prop events, both tracks", ev_ok, [A, Cc]),
                        ("prop event A + spurious jet event, no jet tracks", [ev_ok[0], cs.ObservedEvent(**{**spur.__dict__, "idx": 1})], [A, Cc]),
                        ("2 prop events, event-2 aircraft not in ADS-B, nothing else in window", ev_ok, [A])):
    prep = prepare(trs)
    res, ts, spec, prior = run_cascade(evs, prep, params(), DUR, radius_m=3000.0)
    tot = {k: sum(int(getattr(r, k).sum()) for r in res) for k in ("cpa", "offset", "silence")}
    print(f"{label}: tiles run={len(res)} survivors={tot}")
