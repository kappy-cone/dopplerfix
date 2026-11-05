# Field recordings for milestone 4.5: recording protocol

Milestone 4.5 calibrates the provisional values against real audio from known locations and times
(`reports/combined_plan.md` §8.3 step 4 and P5.4). The values are:
- detection and classification thresholds (`detect.*`, `classify.*`);
- audibility slant distances and the level scatter σ_L;
- `classify.min_aircraft_speed_mps`, the plan's `fit_model_sys_*` and the tolerance `sys` allowances;
- the swing distribution and p_u.

The plan's minimum useful set is a few clips near a small airport, each with a GPS location, an accurate
clock and at least two audible prop flyovers. This protocol aims a little higher, so that some clips can be
held out (§6).

Recordings, metadata files and photos stay in `data/field/` (gitignored). They contain precise locations and
may contain voices, so they are never committed. Commit only derived, anonymised summaries, and only if the
user agrees.

## 1. Site choice
- **Airport.** A small, busy GA airport with prop traffic and good ADS-B coverage. The plan names KBJC (Rocky
  Mountain Metropolitan) and KBDU (Boulder Municipal). Weekend mornings in fair weather usually have the most
  pattern traffic.
- **Geometry.** Aim for props passing at about 300–1500 m slant distance, well inside the 3 km audibility
  threshold, with passes on more than one track:
  - **Under the extended centreline,** about 2–5 km from the runway end. You get departures and arrivals at
    different heights.
  - **Abeam the downwind leg,** about 1–2 km from the pattern. You get passes in both directions.
  - **One site well away from the pattern,** 5–8 km. It gives fewer, higher passes and exercises the silence
    constraint.
- **Ground.** Open, flat, short grass or bare ground. Keep at least 50 m from buildings, walls and parked
  vehicles (strong reflections) and record the distance to the nearest one.
- **Quiet.** Stay at least 300 m from busy roads (car Doppler curves look like slow aircraft), and away from
  HVAC units, generators and power lines (hum), water, and dense trees (birds, wind noise).
- **Legal and polite.** Use public land or land you have permission for. Stay off airport property and away
  from people (no recognisable conversations in the audio).
- **Repeatability.** Mark the exact spot (photo plus GNSS fix) so that a later session can return to it.

## 2. Recorder settings
The pipeline needs the raw pressure signal, roughly flat from 30 Hz to 8 kHz. The prop bands start at 40 Hz
and the jet bands reach 3.8 kHz (`config.yaml`). Any adaptive processing changes level and spectral shape
over time, which is exactly what calibration measures. So:

| Setting | Value |
|---|---|
| Format | WAV or FLAC, 48 kHz (44.1 kHz is fine), 24-bit (16-bit only with conservative gain) |
| Channels | mono is enough; stereo is fine (record which channel is used) |
| Gain | **manual and fixed** for the whole session; write the setting down |
| AGC / auto level | **off** |
| Noise suppression / reduction, voice processing | **off** |
| Low-cut / high-pass filter | **off** (prop fundamentals can sit near 70–140 Hz) |
| Limiter / compressor | **off** |
| Wind-noise DSP | **off**; use a foam plus furry windscreen instead |
| Level | loudest expected pass peaks around −12 to −20 dBFS; never clip. If one clips, note it |

**Phones** apply voice processing by default. Use a recording app that records the unprocessed input (iOS
"measurement" mode, Android's `UNPROCESSED` audio source) and record which app and version you used. A
dedicated field recorder is better if one is available.

**Mic.**
- Put it on a stand at about 1.5 m above ground (`mic.height_agl_m` in config.yaml) and measure the actual height.
- Keep it away from your body and from the car.
- Note its orientation.

**Optional, valuable:** record 30 s from an acoustic calibrator (e.g. 94 dB SPL) at the start of each
session, without changing the gain afterwards. It turns levels into absolute SPL, which the σ_L and
source-level calibration can use. Without it, levels are relative only. Say so in the metadata.

## 3. Location
- Use a GNSS fix (phone or handheld), averaged over at least 60 s at the mic position.
- Record WGS84 lat/lon to 6 decimals, the reported horizontal accuracy (target ≤ 5 m) and the averaging time.
- Take one photo of the setup that shows the horizon, and one of the GNSS screen.

## 4. Clock
Known-time mode needs the recording's UTC start to well under a second. The time prior's half-width is
`time_prior.clock_tol_s` = 10 s, but the calibration of timing allowances needs better. File timestamps
and the recorder clock are not trusted (CLAUDE.md, clock offset).
- **Slate each clip at start and end against a reference clock.**
  - Best: record audible UTC time ticks. Options are the NIST telephone time service (Boulder) on speaker,
    or a WWV shortwave broadcast (Fort Collins).
  - Otherwise: clap or beep on a full minute while watching an NTP- or GPS-synced clock. Use the phone's
    network time or a GPS time app, and video the clock and the clap together if you can.
- **Why both ends:** together they measure the recorder's sample-clock drift. Tens of ppm is about 0.1 s per
  hour.
- **Record:** the method, the clip time of each slate (in seconds) and its UTC, and your estimate of the
  error.

## 5. Clip length and count
- **Length:** record continuous clips of 10–20 min, so each one holds at least 2 audible prop flyovers.
  One-minute benchmark-style clips are cut from these later (cutting is a recorded, automated step, never a
  hand choice per clip).
- **Count (target):**
  - at least 12 clips;
  - from at least 4 sites;
  - over at least 3 sessions on different days;
  - at least 30 audible prop flyovers in total, with a few jets or helicopters if they occur.
- **Storage:** 48 kHz 24-bit mono FLAC of outdoor ambience is about 5–6 MB per minute, so a 2 h session is
  about 0.7 GB (disk is limited; CLAUDE.md Storage).

## 6. Calibration versus validation split
Choose it **before looking at any results**, and write it into each clip's metadata (`split`). The
recommendation:
- split by session, e.g. the first two sessions calibrate and the rest validate;
- or, if there are fewer sessions, split by site.

Thresholds are set only on calibration clips. Location accuracy is reported only on validation clips. This
keeps 4.5 from tuning to its own test set (the registry rule in `reports/apriori_registry.md`).

## 7. Conditions to record
- **Weather:**
  - the METAR for the nearest reporting airport covering the clip (wind, temperature, dew point, altimeter);
  - your own wind estimate at the mic.
- **When to record:** wind ≤ 5 m/s if possible. No precipitation.
- **Runway in use and the traffic level,** in a few words.
- **Ground cover and the noise sources heard** (road, birds, dogs, mowers, other people).
- **A field log of aircraft heard:**
  - approximate UTC, direction of travel, and type if visible;
  - keep it on paper or a second device. Never speak over the recording.

  The log is evaluation truth only. It never feeds a run: `detect.manual_windows` stays empty
  (CLAUDE.md Operating modes).
- **Local ADS-B receiver**, if one is run (1090 MHz, and 978 MHz UAT if possible): its log file. This is the
  P5.3 coverage check (`docs/uat_coverage.md`).

## 8. Metadata template
One YAML sidecar per clip, next to the audio: `data/field/<session_id>/<clip_id>.yaml`.

```yaml
clip_id: 2026-11-08_s1_c03          # <date>_<session>_<clip>
session_id: 2026-11-08_s1
split: calibration                  # calibration | validation, fixed before any analysis (§6)
audio_file: c03.flac
recorder:
  device: "<model>"
  app: "<app and version, if a phone>"
  sample_rate_hz: 48000
  bit_depth: 24
  channels: 1
  channel_used: 0
  gain_setting: "<as shown on the device>"
  agc: off
  noise_suppression: off
  low_cut: off
  limiter: off
  windscreen: "foam + fur"
  calibrator: none                  # or "94 dB SPL, file cal_s1.flac"
  clipped: false
mic:
  lat_deg: 0.000000                 # WGS84
  lon_deg: 0.000000
  gnss_accuracy_m: 0.0
  gnss_averaging_s: 60
  height_agl_m: 1.5
  ground: "short grass"
  nearest_reflector: "barn, 120 m NE"
  photos: [setup.jpg, gnss.jpg]
time:
  method: "NIST phone ticks"        # | WWV | clap on synced clock
  start_slate: {clip_s: 4.20, utc: "2026-11-08T15:02:00.000Z"}
  end_slate: {clip_s: 1206.85, utc: "2026-11-08T15:22:02.000Z"}
  estimated_error_s: 0.05
conditions:
  metar: "KBJC 081450Z ..."
  wind_mps: 3.0
  wind_from_deg: 270
  temperature_c: 12.0
  humidity_pct: 40
  precipitation: none
  runway_in_use: "30R"
  traffic: "busy pattern, 3-4 aircraft"
noise_sources: ["road 600 m E, light", "birds"]
field_log:                          # evaluation truth only; never an input to a run
  - {utc: "2026-11-08T15:05:30Z", what: "high-wing single", heading: "westbound", note: "low, north side"}
adsb_local:
  receiver: none                    # none | 1090 | 1090+978
  log_file: null
notes: ""
```
