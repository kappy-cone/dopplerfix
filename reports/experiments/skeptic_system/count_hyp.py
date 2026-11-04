"""Cascade-only replay of a scenario (no scoring): survivors and hypothesis counts per variant."""
import sys, json, time
from pathlib import Path
sys.path.insert(0, "/Users/williambarr/qgis")
import numpy as np, soundfile as sf
import cli
from doppler import audio, cascade, geo
sc = Path(sys.argv[1])
for v in sys.argv[2:]:
    cfg = cli.load_config(f"/private/tmp/claude-501/-Users-williambarr-qgis/c334e685-7499-4d45-9232-dd5dbcafcf8c/scratchpad/skeptic_system/cfg_{v}.yaml")
    c = cli.speed_of_sound_from(cfg)
    x, fs = sf.read(sc / "audio.flac"); x = audio.mix_to_mono(x)
    tp = audio.parse_time_prior(json.loads((sc / "audio.json").read_text()))
    _, fits = cli.run_events(x, fs, cfg, c)
    _, region = cli.load_region(cfg)
    dur = len(x) / fs
    win = (tp.start_s - 130, tp.start_s + 10 + dur + 60)
    prep = cli.load_tracks(cfg, str(sc / "tracks.parquet"), str(sc / "aircraft_db.csv"), win)
    events, prior, ts, spec, cp = cli.cascade_inputs(cfg, fits, prep, tp, dur, c, region)
    t0 = time.time()
    n = dict(cpa=0, offset=0, silence=0, hyps_at_silence=0, cells_at_cap=0)
    for r in cascade.iter_cascade(spec, events, ts, prior, cli.FlatDEM(1600.0), 1.5, c, cp):
        n["cpa"] += int(r.cpa.sum()); n["offset"] += int(r.offset.sum()); n["silence"] += int(r.silence.sum())
        for h in np.flatnonzero(r.silence):
            k = int((r.hypotheses.cell == h).sum()); n["hyps_at_silence"] += k; n["cells_at_cap"] += int(k >= cp.max_hypotheses_per_cell)
    print(v, f"events={len(events)} required_matched={cascade.required_matched(len(events), cp)}", n,
          f"cascade {time.time() - t0:.1f}s", flush=True)
