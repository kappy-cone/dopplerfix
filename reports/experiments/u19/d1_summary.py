"""Summarise the U19 D1 + E1 diagnostics (d1.py output).

    .venv/bin/python reports/experiments/u19/d1_summary.py <scratch>/d1.jsonl
"""
import collections
import json
import sys

import numpy as np

rows = [json.loads(line) for line in open(sys.argv[1])]
by = {(r["config"], r["seed"], r["arm"]): r for r in rows}
configs = [c for c in ("clean", "all", "loo:path", "loo:levels", "loo:scene", "sub:reflection")
           if any(r["config"] == c for r in rows)]
arms = [a for a in ("base", "b") if any(r["arm"] == a for r in rows)]
bad = [(r["config"], r["seed"], r["arm"]) for r in rows if r["arm"] != "oracle" and not r["trace_ok"]]
print(f"{len(rows)} rows; traced fits differ from fit.characterize_audio on {len(bad)} units {bad[:5]}")


def label(e):
    c = e["cause"]
    if c == "removed_after_fit":
        return "removed: " + "+".join(e["fates"])
    if c == "no_ridge":
        return "no ridge: " + "+".join(e["prop_stops"])
    return c


print("\n## D1: where each missed truth event is lost (events)")
for c in configs:
    for a in arms:
        cnt = collections.Counter()
        for (cc, s, aa), r in by.items():
            if cc == c and aa == a:
                for e in r["events"]:
                    if e["cause"] != "detected":
                        cnt[label(e)] += 1
        print(f"{c:15s} {a:4s} missed {sum(cnt.values()):3d}: " + ", ".join(f"{k} {n}" for k, n in cnt.most_common()))

print("\n## Window-but-no-fit misses, detail (all configs)")
for a in arms:
    for (c, s, aa), r in sorted(by.items()):
        if aa != a:
            continue
        o = by.get((c, s, "oracle"))
        for i, e in enumerate(r["events"]):
            if e["cause"] in ("detected", "no_window"):
                continue
            oe = o["events"][i] if o else {}
            extra = {k: v for k, v in e.items() if k in ("fates", "dt_s", "fits", "prop_stops",
                                                         "guided_short_seed_on_event", "n_fits_in_window")}
            print(f"  {a:4s} {c:15s} seed {s:2d} ev {i} snr {e['peak_band_snr_db']:5.1f} {e['cause']:24s} "
                  f"R1 {oe.get('r1')} R2 {oe.get('r2')} (k {oe.get('r2_k')}, cols>=12 {oe.get('r2_cols_weak')}, "
                  f">=7.6 {oe.get('r2_cols_7p6')}) R3 {oe.get('r3')} {json.dumps(extra)}")

print("\n## Arm b: window-less misses")
for (c, s, a), r in sorted(by.items()):
    if a != "b":
        continue
    for i, e in enumerate(r["events"]):
        if e["cause"] == "no_window":
            o = by[(c, s, "oracle")]["events"][i]
            print(f"  {c:15s} seed {s:2d} ev {i} snr {e['peak_band_snr_db']:5.1f} stat peak "
                  f"{e['stat_peak_db'] if e['stat_peak_db'] is not None else float('nan'):5.1f} dB  "
                  f"R1 {o['r1']} R2 {o['r2']} cols>=12 {o.get('r2_cols_weak')} >=7.6 {o.get('r2_cols_7p6')}")

print("\n## E1 oracle ladder: recall (events recovered / events)")
print("| config | R0 base | R0 arm b | R1 oracle window | R2 oracle seed | R3 truth ridge | R1 misses by cause |")
print("|---|---|---|---|---|---|---|")
for c in configs:
    seeds = sorted({s for (cc, s, a) in by if cc == c and a == "oracle"})
    n = sum(by[(c, s, "oracle")]["n_truth"] for s in seeds)
    r0 = {a: sum(by[(c, s, a)]["detected"] for s in seeds if (c, s, a) in by) for a in arms}
    oe = [e for s in seeds for e in by[(c, s, "oracle")]["events"]]
    r1 = sum(e["r1"] == "detected" for e in oe)
    r2 = sum(e["r2"] == "detected" for e in oe)
    r3 = sum(e["r3"] == "detected" for e in oe)
    m1 = collections.Counter(label({"cause": e["r1"], **e["r1_info"]}) for e in oe if e["r1"] != "detected")
    print(f"| {c} | {r0.get('base')}/{n} | {r0.get('b')}/{n} | {r1}/{n} | {r2}/{n} | {r3}/{n} | "
          + ", ".join(f"{k} {v}" for k, v in m1.most_common()) + " |")

print("\n## Paired transitions, base -> R1 -> R2 (events)")
for c in configs:
    tr = collections.Counter()
    for (cc, s, a), r in by.items():
        if cc != c or a != "base":
            continue
        o = by[(c, s, "oracle")]
        for e, oe in zip(r["events"], o["events"]):
            tr[(e["cause"] == "detected", oe["r1"] == "detected", oe["r2"] == "detected")] += 1
    print(f"{c:15s} " + ", ".join(f"R0 {'+' if k[0] else '-'} R1 {'+' if k[1] else '-'} R2 {'+' if k[2] else '-'}: {v}"
                                  for k, v in sorted(tr.items(), reverse=True)))

print("\n## Columns >= 12 dB / >= 7.6 dB along the truth curve (R2), by base outcome, all configs")
grp = collections.defaultdict(list)
for (c, s, a), r in by.items():
    if a != "base":
        continue
    o = by[(c, s, "oracle")]
    for e, oe in zip(r["events"], o["events"]):
        if oe.get("r2_cols_weak") is not None:
            grp[e["cause"]].append((oe["r2_cols_weak"], oe["r2_cols_7p6"]))
for k, v in sorted(grp.items()):
    a = np.array(v)
    print(f"  {k:24s} n {len(a):3d}  >=12 dB median {np.median(a[:, 0]):5.0f} (< 50: {int((a[:, 0] < 50).sum())})"
          f"  >=7.6 dB median {np.median(a[:, 1]):5.0f} (< 50: {int((a[:, 1] < 50).sum())})")

print("\n## Wall time per unit (s): median %.0f, max %.0f" % (
    np.median([r["wall_s"] for r in rows if r["arm"] == "oracle"]),
    max(r["wall_s"] for r in rows if r["arm"] == "oracle")))
