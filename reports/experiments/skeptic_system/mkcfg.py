import yaml, sys
S = "/private/tmp/claude-501/-Users-williambarr-qgis/c334e685-7499-4d45-9232-dd5dbcafcf8c/scratchpad/skeptic_system"
cfg = yaml.safe_load(open("/Users/williambarr/qgis/config.yaml"))
cfg["outputs"]["root"] = S + "/runs"
yaml.safe_dump(cfg, open(S + "/cfg_robust.yaml", "w"), sort_keys=False)
cfg["cascade"]["max_unmatched_events"] = 0
cfg["cascade"]["silence_audibility"] = "hard"
yaml.safe_dump(cfg, open(S + "/cfg_strict.yaml", "w"), sort_keys=False)
