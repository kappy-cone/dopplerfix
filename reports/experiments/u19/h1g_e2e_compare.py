"""U19 arm h1g, screen 2 (end to end): V1h1g against the Stage A2 V1 rows, all-on C seeds 0-49, paired by unit.

Registered in reports/apriori_registry.md ("Arm h1g"). Measures: rank-1 accuracy ignoring status (exact sign
test), recall, spurious events, rank-1 error and true-cell rank (Wilcoxon), timeouts. h1g is ruled out if
rank-1 runs lost exceed runs gained. A screen only rules out; not ruled out is not acceptance.

    .venv/bin/python reports/experiments/u19/h1g_e2e_compare.py --h1g <scratch>/screen2
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from cli import _fill_detection, load_config, section  # noqa: E402
from doppler import evaluate as ev  # noqa: E402


def load(path):
    op = gzip.open if str(path).endswith(".gz") else open
    return [json.loads(line) for line in op(path, "rt")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--h1g", required=True, help="ablation folder of the V1h1g run (rows.jsonl, keep/)")
    ap.add_argument("--a2", default="reports/data/stageA2_rows.jsonl.gz")
    ap.add_argument("--a2-detection", default="reports/data/stageA2_detection.json")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    ep = ev.EvalParams.from_dict(section(cfg, "evaluate"))
    out = Path(a.h1g)
    H = [r for r in load(out / "rows.jsonl") if r["variant"] == "V1h1g"]
    _fill_detection(H, out, ep, cfg)
    det = {(d["config"], d["seed"]): d for d in json.load(open(a.a2_detection))}
    B = {(r["config"], r["seed"]): r for r in load(a.a2) if r["variant"] == "V1"}
    for r in B.values():
        d = det.get((r["config"], r["seed"]))
        if d and r.get("truth_events_detected") is None:
            r.update(n_truth_events=d["n_truth_events"], truth_events_detected=d["truth_events_detected"])
    keys = sorted((r["config"], r["seed"]) for r in H if (r["config"], r["seed"]) in B)
    h = {(r["config"], r["seed"]): r for r in H}
    rh, rb = [h[k] for k in keys], [B[k] for k in keys]
    print(f"units {len(keys)} (h1g rows {len(H)}); h1g commits {sorted({str(r['git']) for r in H})}")
    print(f"h1g detection keys {sorted({r['detection_sha256'][:12] for r in rh})}; "
          f"A2 V1 {sorted({r['detection_sha256'][:12] for r in rb})}")

    acc_h = [ev.rank1_ok(r, ep) for r in rh]
    acc_b = [ev.rank1_ok(r, ep) for r in rb]
    s = ev.paired_sign(acc_h, acc_b)
    tot = lambda rows, f: sum(int(r.get(f) or 0) for r in rows)  # noqa: E731
    n = lambda rows, o: sum(r.get("outcome") == o for r in rows)  # noqa: E731
    print("\n| measure | A2 V1 | V1h1g | paired |\n|---|---|---|---|")
    print(f"| rank-1 accuracy | {sum(acc_b)} / {len(keys)} | {sum(acc_h)} / {len(keys)} | "
          f"gained {s['a_only']} / lost {s['b_only']} (p = {s['p']:.3g}) |")
    print(f"| correct (outcome) | {n(rb, 'correct')} | {n(rh, 'correct')} | |")
    print(f"| confident-wrong | {n(rb, 'confident_wrong')} | {n(rh, 'confident_wrong')} | |")
    print(f"| recall | {tot(rb, 'truth_events_detected')} / {tot(rb, 'n_truth_events')} | "
          f"{tot(rh, 'truth_events_detected')} / {tot(rh, 'n_truth_events')} | |")
    print(f"| spurious events (extra_events) | {tot(rb, 'extra_events')} | {tot(rh, 'extra_events')} | |")
    for f in ("rank1_error_m", "true_cell_rank"):
        w = ev.paired_wilcoxon([r.get(f) for r in rh], [r.get(f) for r in rb])
        print(f"| {f} (Wilcoxon h1g − A2) | | | n {w['n']} (dropped {w['n_dropped']}), non-zero {w['n_nonzero']}, "
              f"median {w['median_diff']:.3g}, p = {w['p']:.3g} |")
    print(f"| timeouts | {n(rb, 'timeout')} | {n(rh, 'timeout')} | |")
    print(f"| mean / median run s | {sum(r['seconds'] for r in rb) / len(rb):.0f} / "
          f"{sorted(r['seconds'] for r in rb)[len(rb) // 2]:.0f} | {sum(r['seconds'] for r in rh) / len(rh):.0f} / "
          f"{sorted(r['seconds'] for r in rh)[len(rh) // 2]:.0f} | |")
    print("\noutcome transitions (A2 → h1g):", dict(Counter((x.get("outcome"), y.get("outcome"))
                                                          for x, y in zip(rb, rh) if x.get("outcome") != y.get("outcome"))))
    print("failure classes A2:", dict(Counter(r.get("failure") for r in rb if r.get("failure"))))
    print("failure classes h1g:", dict(Counter(r.get("failure") for r in rh if r.get("failure"))))
    print("rank-1 changed units:", [(k, int(x), int(y)) for k, x, y in zip(keys, acc_b, acc_h) if x != y])
    unchanged = sum(r.get("detection_sha256") and x.get("n_events") == r.get("n_events") and
                    x.get("rank1_error_m") == r.get("rank1_error_m") for x, r in zip(rb, rh))
    print(f"units with identical n_events and rank-1 error: {unchanged} / {len(keys)}")
    verdict = "RULED OUT" if s["b_only"] > s["a_only"] else "not ruled out (not acceptance)"
    print(f"\nregistered rule (lost > gained rules h1g out): {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
