"""Summarise a U19 detection screen (screen.py output): per config x arm, paired against the base arm.

    .venv/bin/python reports/experiments/u19/summarize.py <scratch>/screen.jsonl
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from cli import ABLATION_SCENARIOS  # noqa: E402
from doppler import evaluate as ev  # noqa: E402

DURATION_S = {k: v[0] for k, v in ABLATION_SCENARIOS.items()}

rows = [json.loads(line) for line in open(sys.argv[1])]
rows = [r for r in rows if r["arm"] != "oracle"]       # d1.py rows: the oracle ladder is summarised by d1_summary.py
for r in rows:                                         # d1.py rows carry a cause instead of the detected flag
    for e in r["events"]:
        e.setdefault("detected", e.get("cause") == "detected")
        e.setdefault("window", e.get("cause") not in ("detected", "no_window"))
by = {(r["config"], r["seed"], r["arm"]): r for r in rows}
configs = [c for c in ("clean", "all", "loo:path", "loo:levels", "loo:scene", "sub:reflection")
           if any(r["config"] == c for r in rows)]
arms = [a for a in ("base", "a", "b", "c", "abc") if any(r["arm"] == a for r in rows)]


def cause(e):
    if e["detected"]:
        return "detected"
    if e["window"]:
        return "window, no fit"
    return "no window, stat >= trigger" if (e["stat_peak_db"] or -99) >= 10 else "no window, below trigger"


print("| config | arm | n | recall | runs with a miss | spurious / run | events gained / lost vs base (p) | "
      "runs: spurious up / down vs base (Wilcoxon p, median diff) |")
print("|---|---|---|---|---|---|---|---|")
gains = collections.Counter()
for c in configs:
    seeds = sorted({r["seed"] for r in rows if r["config"] == c})
    for a in arms:
        rs = [by[(c, s, a)] for s in seeds if (c, s, a) in by]
        n_t = sum(r["n_truth"] for r in rs)
        rec = sum(r["detected"] for r in rs)
        miss = sum(r["detected"] < r["n_truth"] for r in rs)
        sp = sum(r["spurious"] for r in rs) / len(rs)
        if a == "base":
            pair, spur = "", ""
        else:
            ga = gl = 0
            sa, sb = [], []
            for s in seeds:
                ra, rb = by.get((c, s, a)), by.get((c, s, "base"))
                if ra is None or rb is None:
                    continue
                for ea, eb in zip(ra["events"], rb["events"]):
                    ga += ea["detected"] and not eb["detected"]
                    gl += eb["detected"] and not ea["detected"]
                    if ea["detected"] and not eb["detected"]:
                        gains[(c, a, cause(eb))] += 1
                sa.append(ra["spurious"])
                sb.append(rb["spurious"])
            t = ev.paired_sign([True] * ga + [False] * gl, [False] * ga + [True] * gl)
            w = ev.paired_wilcoxon(sa, sb)
            up = sum(x > y for x, y in zip(sa, sb))
            dn = sum(x < y for x, y in zip(sa, sb))
            pair = f"{ga} / {gl} ({t['p']:.3g})"
            spur = f"{up} / {dn} ({w['p']:.3g}, {w['median_diff']:+.1f})"
        print(f"| {c} | {a} | {len(rs)} | {rec}/{n_t} | {miss}/{len(rs)} | {sp:.2f} | {pair} | {spur} |")
print("\nGained events by their baseline failure class:")
for (c, a, k), n in sorted(gains.items()):
    print(f"  {c:15s} {a:4s} {k:28s} {n}")

# E3: recall against the event's peak band SNR (truth.json aircraft peak_band_snr_db), and spurious per minute.
SNR_EDGES = (-float("inf"), 1, 2, 3, 4, 6, 8, float("inf"))
SNR_LABELS = ("< 1", "1-2", "2-3", "3-4", "4-6", "6-8", ">= 8")


def snr_bin(x):
    for i in range(len(SNR_LABELS)):
        if SNR_EDGES[i] <= x < SNR_EDGES[i + 1]:
            return i
    return None


print("\nRecall against peak band SNR (dB): detected / events")
print("| config | arm | " + " | ".join(SNR_LABELS) + " | spurious / clip-minute |")
print("|---|---|" + "---|" * (len(SNR_LABELS) + 1))
for c in configs + ["pooled"]:
    for a in arms:
        rs = [r for r in rows if r["arm"] == a and (c == "pooled" or r["config"] == c)]
        if not rs:
            continue
        num, den = [0] * len(SNR_LABELS), [0] * len(SNR_LABELS)
        for r in rs:
            for e in r["events"]:
                if e.get("peak_band_snr_db") is None:
                    continue
                b = snr_bin(e["peak_band_snr_db"])
                den[b] += 1
                num[b] += e["detected"]
        minutes = sum(DURATION_S.get(r.get("scenario", "C"), 60.0) for r in rs) / 60.0
        cells = [f"{n}/{d}" if d else "" for n, d in zip(num, den)]
        print(f"| {c} | {a} | " + " | ".join(cells) + f" | {sum(r['spurious'] for r in rs) / minutes:.2f} |")
