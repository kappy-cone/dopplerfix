import sys, json
import numpy as np
import cli
from doppler import synth, realism as rl, audio
from doppler.geo import LocalFrame
cfg_path, seed, dur, ev = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
cfg = cli.load_config(cfg_path)
_, region = cli.load_region(cfg, None)
frame0, radius = cli.region_frame(region)
class A: pass
a = A(); a.seed = seed; a.snr_db = None; a.n_tracks = None; a.duration_s = dur; a.event_times = ev; a.event_classes = None
params = cli.synth_params_from(cfg, a, radius)
tp = audio.parse_time_prior(cfg.get("time_prior") or {})
c = cli.speed_of_sound_from(cfg)
ground = cli.section(cfg, "region").get("ground_elev_m", 0.0)
frame = LocalFrame(frame0.lat0, frame0.lon0, ground)
rp = rl.RealismParams.from_dict(cli.section(cfg, "realism"))
log = []
orig = rl.draw_source
def ds(rng, cls, p, base, d):
    s = orig(rng, cls, p, base, d); log.append(s); return s
rl.draw_source = ds
orig_fa = synth.flyover_audio
sc = synth.make_scenario(params, frame, tp.start_s, ground, c, realism=rp)
for s, src in zip(sc.specs, log):
    if s.role == 'event':
        print(s.icao24, 'hdg', round(s.heading_deg, 1), 'v', round(s.speed_mps, 1), 'off', round(s.offset_m), 'alt_agl', round(s.alt_agl_m),
              'turn', round(s.turn_rate_deg_s, 2), 'turn_half', round(getattr(s, 'turn_half_s', 0), 1), 'climb', round(s.climb_mps, 2),
              'accel', round(s.accel_frac, 3), '| ramp', round(src.ramp_frac, 3), 'at', round(src.ramp_t_rel_s, 1), 'dur', round(src.ramp_dur_s, 1),
              'twin', round(src.twin_detune, 4), 'dir', round(src.directivity_db, 1), 'R', round(src.reflection, 2),
              'harm_db', np.round(20*np.log10(src.harmonics[:4]), 1))
