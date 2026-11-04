import sys, json, numpy as np
sys.path.insert(0, ".")
from multiprocessing import Pool
import cli
from doppler import synth, realism as rl

CFG = cli.load_config("config.yaml")
_, REGION = cli.load_region(CFG, None)
_, RADIUS = cli.region_frame(REGION)

def one(args):
    conf, seed = args
    sp = dict(CFG["synth"]); sp.update(seed=seed, duration_s=60.0, event_rx_times_s=[15.0, 45.0], event_classes=None,
        audible_slant_m=CFG["cascade"]["audible_slant_m"], region_radius_m=RADIUS, mic_height_m=1.5)
    p = synth.SynthParams.from_dict(sp)
    rp = cli.realism_for(CFG, conf)
    sc, _, _ = cli.build_synth_scenario(CFG, REGION, p, rp, 0)
    c = cli.speed_of_sound_from(CFG)
    _, fits = cli.run_events(sc.audio.astype(np.float64), sc.fs_hz, CFG, c)
    t_true = [t.t_cpa_received_rel_s for t in sc.truth]
    det = sum(any(abs(f.t_cpa_received_s - t) < 10 for f in fits) for t in t_true)
    snr = [sc.aircraft_info[s.icao24]["peak_band_snr_db"] for s in sc.event_specs]
    lvl = [sc.aircraft_info[s.icao24]["level_db_at_ref"] for s in sc.event_specs]
    d = [t.d_m for t in sc.truth]
    peak = float(np.max(np.abs(sc.audio)))
    return conf, seed, len(fits), det, [round(x, 1) for x in snr], [round(x, 1) for x in lvl], [round(x) for x in d]

if __name__ == "__main__":
    jobs = [(c, s) for s in range(10) for c in ("all", "loo:path", "sub:reflection", "sub:cwind")]
    with Pool(6) as pool:
        for r in pool.imap(one, jobs):
            print(*r, flush=True)
