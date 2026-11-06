"""Stage A2 (post-U19 defaults) against Stage A (pre-U19 detector), paired by unit: NEXT_SESSION task B step 4.

Per config and variant (V0, V1): outcome tiers, rank-1 accuracy ignoring status (exact sign test on the
discordant units), detection recall, timeouts, and the failure taxonomy. Stage A detection counts come from
reports/data/stageA_detection.json; A2's are filled from its kept files (cli._fill_detection) and written to
--detection-out so they can be committed beside the rows.

    .venv/bin/python reports/experiments/stageA2_compare.py --a2 <repo>/outputs/ablation/stageA2 \
        [--detection-out reports/data/stageA2_detection.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli import _fill_detection, load_config, section  # noqa: E402
from doppler import evaluate as ev  # noqa: E402

GROUPS = [("all-on", ["all"]), ("clean", ["clean"])]


def load(path):
    op = gzip.open if str(path).endswith(".gz") else open
    return [json.loads(line) for line in op(path, "rt")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a2", required=True, help="Stage A2 ablation folder (rows.jsonl, keep/)")
    ap.add_argument("--a", default="reports/data/stageA_rows.jsonl.gz")
    ap.add_argument("--a-detection", default="reports/data/stageA_detection.json")
    ap.add_argument("--detection-out")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    ep = ev.EvalParams.from_dict(section(cfg, "evaluate"))
    out = Path(a.a2)
    A2 = [r for r in load(out / "rows.jsonl") if r["variant"] in ("V0", "V1")]
    _fill_detection(A2, out, ep, cfg)
    A = [r for r in load(a.a) if r["variant"] in ("V0", "V1")]
    det = {(d["scenario"], d["config"], d["seed"]): d for d in json.load(open(a.a_detection))}
    for r in A:
        d = det.get((r["scenario"], r["config"], r["seed"]))
        if d and r.get("n_truth_events") is None:
            r.update(n_truth_events=d["n_truth_events"], truth_events_detected=d["truth_events_detected"])
    if a.detection_out:
        seen = {}
        for r in A2:
            if r.get("n_truth_events") is not None:
                seen[(r["scenario"], r["config"], r["seed"])] = {
                    "scenario": r["scenario"], "config": r["config"], "seed": r["seed"],
                    "n_truth_events": r["n_truth_events"], "truth_events_detected": r["truth_events_detected"]}
        Path(a.detection_out).write_text(json.dumps(sorted(seen.values(), key=lambda d: (d["config"], d["seed"])),
                                                    indent=0) + "\n")
    ka = {(r["variant"], r["config"], r["seed"]): r for r in A}
    k2 = {(r["variant"], r["config"], r["seed"]): r for r in A2}
    configs = list(dict.fromkeys(r["config"] for r in A2))
    others = [c for c in configs if c not in ("all", "clean")]
    groups = GROUPS + [("every other config (pooled)", others)] + [(c, [c]) for c in others]
    print(f"Stage A rows {len(A)}, A2 rows {len(A2)}; A2 commits {sorted({str(r['git']) for r in A2})}")
    print("\n| configs | arm | units | correct A → A2 | rank-1 acc A → A2 | gained / lost (p) | recall A → A2 | "
          "confident-wrong A → A2 | timeouts A → A2 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label, cs in groups:
        for v in ("V0", "V1"):
            keys = [k for k in k2 if k[0] == v and k[1] in cs and k in ka]
            if not keys:
                continue
            ra, r2 = [ka[k] for k in keys], [k2[k] for k in keys]
            acc_a = [ev.rank1_ok(r, ep) for r in ra]
            acc_2 = [ev.rank1_ok(r, ep) for r in r2]
            s = ev.paired_sign(acc_2, acc_a)
            n = lambda rows, o: sum(r.get("outcome") == o for r in rows)  # noqa: E731

            def rec(rows):
                kn = [r for r in rows if r.get("n_truth_events") is not None]
                return f"{sum(min(r['truth_events_detected'], r['n_truth_events']) for r in kn)}/" \
                       f"{sum(r['n_truth_events'] for r in kn)}"
            p = "—" if s["a_only"] + s["b_only"] == 0 else f"{s['p']:.3g}"
            print(f"| {label} | {v} | {len(keys)} | {n(ra, 'correct')} → {n(r2, 'correct')} | {sum(acc_a)} → "
                  f"{sum(acc_2)} | {s['a_only']} / {s['b_only']} ({p}) | {rec(ra)} → {rec(r2)} | "
                  f"{n(ra, 'confident_wrong')} → {n(r2, 'confident_wrong')} | {n(ra, 'timeout')} → "
                  f"{n(r2, 'timeout')} |")
    print("\n### Failure taxonomy (rows with a failure class), A → A2")
    print("| configs | arm | survival:grid | survival:cpa | survival:none | survival:offset | discrimination | bias "
          "| timeout |")
    print("|---|---|---|---|---|---|---|---|---|")
    cls = ("survival:grid", "survival:cpa", "survival:none", "survival:offset", "discrimination", "bias", "timeout")
    for label, cs in groups[:3]:
        for v in ("V0", "V1"):
            keys = [k for k in k2 if k[0] == v and k[1] in cs and k in ka]
            ca = Counter(ka[k].get("failure") for k in keys)
            c2 = Counter(k2[k].get("failure") for k in keys)
            print(f"| {label} | {v} | " + " | ".join(f"{ca.get(c, 0)} → {c2.get(c, 0)}" for c in cls) + " |")
    print("\n### Timeouts, A2 (unit: Stage A outcome)")
    for k, r in sorted(k2.items()):
        if r.get("outcome") == "timeout" or ka.get(k, {}).get("outcome") == "timeout":
            print(f"- {k[0]} {k[1]} seed {k[2]}: A {ka.get(k, {}).get('outcome')} → A2 {r.get('outcome')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
