"""Two-stage refinement (arm V1k, score.refine_lbfgs_top_k = 50): the decision against the Stage A2 V1 rows.

Registered in reports/apriori_registry.md ("Two-stage refinement") before the run, with this script. Paired by
unit (scenario, config, seed). V1k is accepted only if D1, D2 and D3 all hold:
- D1: rank-1 accuracy (evaluate.rank1_ok, ignoring status) over the units >= A2 V1's.
- D2: confident_wrong outcomes <= A2 V1's.
- D3: on units with more than K candidates in A2 that finish in both runs, the median per-unit ratio
  A2 score_s / V1k score_s >= 2.

    .venv/bin/python reports/experiments/two_stage_decision.py --v1k outputs/ablation/two_stage
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli import _fill_detection, apply_variant, load_config, section  # noqa: E402
from doppler import evaluate as ev  # noqa: E402

D3_MIN_RATIO = 2.0


def load(path):
    op = gzip.open if str(path).endswith(".gz") else open
    return [json.loads(line) for line in op(path, "rt")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--v1k", required=True, help="ablation folder of the V1k run (rows.jsonl, keep/)")
    ap.add_argument("--a2", default="reports/data/stageA2_rows.jsonl.gz")
    ap.add_argument("--a2-detection", default="reports/data/stageA2_detection.json")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    K = int(section(apply_variant(cfg, "V1k"), "score")["refine_lbfgs_top_k"])
    ep = ev.EvalParams.from_dict(section(cfg, "evaluate"))
    out = Path(a.v1k)
    H = [r for r in load(out / "rows.jsonl") if r["variant"] == "V1k"]
    _fill_detection(H, out, ep, cfg)
    det = {(d["config"], d["seed"]): d for d in json.load(open(a.a2_detection))}
    B = {(r["scenario"], r["config"], r["seed"]): r for r in load(a.a2) if r["variant"] == "V1"}
    for (_, c_, s_), r in B.items():
        d = det.get((c_, s_))
        if d and r.get("truth_events_detected") is None:
            r.update(n_truth_events=d["n_truth_events"], truth_events_detected=d["truth_events_detected"])
    h = {(r["scenario"], r["config"], r["seed"]): r for r in H}
    keys = sorted(k for k in h if k in B)
    rh, rb = [h[k] for k in keys], [B[k] for k in keys]
    print(f"K = {K}; units {len(keys)} (V1k rows {len(H)}, A2 V1 rows {len(B)}); "
          f"V1k commits {sorted({str(r['git']) for r in H})}")
    if len(keys) != len(B):
        print(f"WARNING: {len(B) - len(keys)} A2 units have no V1k row; the verdict covers the paired units only")

    acc_h = [ev.rank1_ok(r, ep) for r in rh]
    acc_b = [ev.rank1_ok(r, ep) for r in rb]
    s = ev.paired_sign(acc_h, acc_b)
    n = lambda rows, o: sum(r.get("outcome") == o for r in rows)  # noqa: E731
    tot = lambda rows, f: sum(int(r.get(f) or 0) for r in rows)  # noqa: E731
    big = [i for i, r in enumerate(rb) if (r.get("n_candidates") or 0) > K]
    both = [i for i in big if (rb[i].get("timings_s") or {}).get("score_s") and (rh[i].get("timings_s") or {}).get("score_s")]
    ratio = np.array([rb[i]["timings_s"]["score_s"] / rh[i]["timings_s"]["score_s"] for i in both])
    med = float(np.median(ratio)) if len(ratio) else float("nan")

    d1 = sum(acc_h) >= sum(acc_b)
    d2 = n(rh, "confident_wrong") <= n(rb, "confident_wrong")
    d3 = len(ratio) > 0 and med >= D3_MIN_RATIO
    print("\n| criterion | A2 V1 | V1k | bar | result |\n|---|---|---|---|---|")
    print(f"| D1 rank-1 accuracy | {sum(acc_b)} / {len(keys)} | {sum(acc_h)} / {len(keys)} | ≥ {sum(acc_b)} | "
          f"{'pass' if d1 else 'FAIL'}; paired gained {s['a_only']} / lost {s['b_only']} (p = {s['p']:.3g}) |")
    print(f"| D2 confident-wrong | {n(rb, 'confident_wrong')} | {n(rh, 'confident_wrong')} | "
          f"≤ {n(rb, 'confident_wrong')} | {'pass' if d2 else 'FAIL'} |")
    print(f"| D3 median score_s ratio (units > {K} candidates, both finished) | | {med:.2f} (n {len(ratio)}; "
          f"IQR {np.percentile(ratio, 25):.2f}–{np.percentile(ratio, 75):.2f}) | ≥ {D3_MIN_RATIO:g} | "
          f"{'pass' if d3 else 'FAIL'} |")
    print("\n| reported | A2 V1 | V1k |\n|---|---|---|")
    print(f"| correct | {n(rb, 'correct')} | {n(rh, 'correct')} |")
    print(f"| timeouts | {n(rb, 'timeout')} | {n(rh, 'timeout')} |")
    print(f"| recall | {tot(rb, 'truth_events_detected')} / {tot(rb, 'n_truth_events')} | "
          f"{tot(rh, 'truth_events_detected')} / {tot(rh, 'n_truth_events')} |")
    print(f"| total wall s (rows) | {sum(r['seconds'] for r in rb):.0f} | {sum(r['seconds'] for r in rh):.0f} |")
    print(f"| total score_s | {sum((r.get('timings_s') or {}).get('score_s') or 0 for r in rb):.0f} | "
          f"{sum((r.get('timings_s') or {}).get('score_s') or 0 for r in rh):.0f} |")
    for f in ("rank1_error_m", "true_cell_rank"):
        w = ev.paired_wilcoxon([r.get(f) for r in rh], [r.get(f) for r in rb])
        print(f"| {f} (Wilcoxon V1k − A2) | n {w['n']}, non-zero {w['n_nonzero']} | median {w['median_diff']:.3g}, "
              f"p = {w['p']:.3g} |")
    print("\noutcome transitions (A2 → V1k):", dict(Counter((x.get("outcome"), y.get("outcome"))
                                                          for x, y in zip(rb, rh) if x.get("outcome") != y.get("outcome"))))
    print("status transitions (A2 → V1k):", dict(Counter((x.get("status"), y.get("status"))
                                                         for x, y in zip(rb, rh) if x.get("status") != y.get("status"))))
    print("rank-1 changed units:", [(k[1], k[2], int(x), int(y)) for k, x, y in zip(keys, acc_b, acc_h) if x != y])
    small = [i for i, r in enumerate(rb) if (r.get("n_candidates") or 0) <= K]
    same = sum(rh[i].get("rank1_score") == rb[i].get("rank1_score") for i in small)
    print(f"units with ≤ {K} candidates (cannot change): {len(small)}, rank-1 score identical in {same}")
    print(f"\nVERDICT: {'ACCEPT' if d1 and d2 and d3 else 'REJECT'} (D1 {d1}, D2 {d2}, D3 {d3})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
