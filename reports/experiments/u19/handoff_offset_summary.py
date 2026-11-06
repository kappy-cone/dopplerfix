"""Summarise handoff_offset.py rows: seed-curve offset from its nearest truth line, by attempt outcome.

    .venv/bin/python reports/experiments/u19/handoff_offset_summary.py <offset.jsonl>
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import numpy as np

REL_EDGES = (0.0, 0.005, 0.01, 0.02, 0.05, 0.10, np.inf)
REL_LABELS = ("< 0.5 %", "0.5–1 %", "1–2 %", "2–5 %", "5–10 %", "≥ 10 %")


def rel_bin(x: float) -> str:
    return REL_LABELS[int(np.searchsorted(REL_EDGES, x, side="right")) - 1]


def main(path: str) -> int:
    rows = [json.loads(line) for line in open(path)]
    print(f"units {len(rows)}; trace_ok {sum(r['trace_ok'] for r in rows)}; commits {sorted({r.get('git') for r in rows})}")
    groups = {}
    for r in rows:
        for a in r["attempts"]:
            if a.get("line") is None and "event" not in a:
                continue
            if a["kind"] == "guided_short":
                g = "guided_short, event missed" if not a["event_detected"] else "guided_short, event detected anyway"
            else:
                g = a["kind"]
            groups.setdefault((r["config"], g), []).append(a)
            groups.setdefault(("pooled", g), []).append(a)
    print("\n### Seed-curve offset from the nearest truth line (median relative offset over the window), attempts")
    print("| config | attempt | n | " + " | ".join(REL_LABELS) + " |")
    print("|---|---|---|" + "---|" * len(REL_LABELS))
    order = ("fit", "no_descent", "guided_short, event detected anyway", "guided_short, event missed")
    for conf in ("clean", "all", "loo:path", "loo:levels", "loo:scene", "sub:reflection", "pooled"):
        for g in order:
            A = groups.get((conf, g))
            if not A:
                continue
            c = Counter(rel_bin(a["median_rel"]) for a in A)
            print(f"| {conf} | {g} | {len(A)} | " + " | ".join(str(c.get(lb, 0)) for lb in REL_LABELS) + " |")
    print("\n### On-line seeds (median relative offset < 5 %), pooled: offset in Hz and grid steps (medians [IQR])")
    print("| attempt | n | k = 1 / k ≠ 1 | median |Δf| Hz | p90 |Δf| Hz | frac within guide_hz | f0 steps | v steps "
          "| τ steps | t0 steps |")
    print("|---|---|---|---|---|---|---|---|---|---|")

    def q(x):
        x = np.asarray([v for v in x if v is not None and np.isfinite(v)], float)
        if not len(x):
            return "–"
        a, b, c = np.percentile(x, [25, 50, 75])
        return f"{b:.2f} [{a:.2f}, {c:.2f}]"

    for g in order:
        A = [a for a in groups.get(("pooled", g), []) if a["median_rel"] < 0.05]
        if not A:
            continue
        k1 = sum(a["k"] == 1.0 for a in A)
        print(f"| {g} | {len(A)} | {k1} / {len(A) - k1} | {q(a['median_df_hz'] for a in A)} | "
              f"{q(a['p90_df_hz'] for a in A)} | {q(a['frac_within_guide'] for a in A)} | "
              f"{q(abs(a.get('f0_steps', np.nan)) for a in A)} | {q(abs(a.get('v_steps', np.nan)) for a in A)} | "
              f"{q(abs(a.get('tau_steps', np.nan)) for a in A)} | {q(abs(a.get('t0_steps', np.nan)) for a in A)} |")
    # missed events: was any guided_short seed in its window on its line?
    print("\n### Missed truth events with a guided_short attempt, pooled")
    n_missed_short = n_on = 0
    for r in rows:
        for i, det in enumerate(r["detected"]):
            if det:
                continue
            sh = [a for a in r["attempts"] if a["kind"] == "guided_short" and a.get("event") == i]
            if not sh:
                continue
            n_missed_short += 1
            n_on += any(a["median_rel"] < 0.05 for a in sh)
    print(f"- missed events whose nearest-line guided_short seed(s) exist: {n_missed_short}; "
          f"with a seed on the event's line (< 5 %): {n_on}")
    return 0


def handoff_table(path: str) -> None:
    """Short guided ridges on missed events with an on-line seed: masked vs unmasked strong columns."""
    rows = [json.loads(line) for line in open(path)]
    A = [a for r in rows for a in r["attempts"] if a["kind"] == "guided_short" and "handoff" in a]
    for label, sel in (("missed event, seed on line (< 5 %)", lambda a: not a["event_detected"] and a["median_rel"] < 0.05),
                       ("missed event, seed off line", lambda a: not a["event_detected"] and a["median_rel"] >= 0.05),
                       ("event detected anyway", lambda a: a["event_detected"])):
        B = [a for a in A if sel(a)]
        if not B:
            continue
        h = [a["handoff"] for a in B]
        med = lambda k: float(np.median([x[k] for x in h]))  # noqa: E731
        unm = sum(x["unmasked_ridge_points"] is not None for x in h)
        prior = sum(x["n_prior_fits"] > 0 for x in h)
        print(f"- {label}: n {len(B)}; strong columns in corridor, masked median {med('strong_masked'):.0f}, "
              f"unmasked median {med('strong_unmasked'):.0f} (min_points {h[0]['min_points']}); unmasked excess "
              f"gives a ridge in {unm}; a fit already peeled from the window in {prior}")


if __name__ == "__main__":
    main(sys.argv[1])
    print("\n### Short guided ridges: masked vs unmasked excess in the seed's corridor")
    handoff_table(sys.argv[1])
