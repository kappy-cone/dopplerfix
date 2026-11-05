import yaml, sys
S = "<scratch>/skeptic_system"
cfg = yaml.safe_load(open("./config.yaml"))
cfg["outputs"]["root"] = S + "/runs"
yaml.safe_dump(cfg, open(S + "/cfg_robust.yaml", "w"), sort_keys=False)
cfg["cascade"]["max_unmatched_events"] = 0
cfg["cascade"]["silence_audibility"] = "hard"
yaml.safe_dump(cfg, open(S + "/cfg_strict.yaml", "w"), sort_keys=False)
