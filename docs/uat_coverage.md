# P5.3 UAT-coverage check: design note

Plan P5.3 (and S-F8, U16): measure how much low-altitude GA traffic near KBJC/KBDU is **missing from, or
gappy in,** OpenSky, compared with a UAT-capable reference. Several provisional values rest on that number:
- p_u (`score.p_unmatched` if R3c is built);
- the realism layer's ADS-B drop and gap rates and the share of baro-only props;
- how strict the silence filter can afford to be (CLAUDE.md Coverage gaps pitfall).

This note is a design only. Nothing is built or downloaded.

## Question
For aircraft that a mic near these airports could hear, ask four things:
1. What fraction of passes does OpenSky miss entirely?
2. Of those, how many are UAT-only (978 MHz), and how many are 1090 aircraft in an OpenSky coverage hole?
3. How many have no ADS-B Out at all (seen only as TIS-B / radar targets, or only by ear)?
4. For the passes OpenSky does have, how do its gaps, cadence, position noise and timestamp bias compare
   with the reference?

## Reference feeds (user decision: which, and when)
- **A local receiver** at or near a recording site, with 1090 MHz plus 978 MHz (one receiver chain each,
  e.g. readsb and dump978). It gives UAT aircraft and ADS-R/TIS-B rebroadcasts, all logged every second.
  This is the best reference, because it is co-located with the field recordings. It needs hardware and a
  site.
- **A UAT-capable aggregator's historical data** for one day (U14 (iv)). Check its terms and licence first,
  and record its feeder coverage near the airports.
- **The field log of aircraft heard** (`docs/field_recordings.md` §7). It is the only reference for
  aircraft with no ADS-B Out and no TIS-B. Use it for evaluation only.

## Method
1. **Region and band:**
   - circles of 10 km around KBJC and KBDU;
   - fixes below 3000 m above ground (the largest audibility slant distance);
   - one full day, plus every field session.
2. **Ingest both feeds** into the canonical track schema through `doppler/tracks.py`. The local receiver's
   JSON uses ft, kt and ft/min (`alt_baro`, `alt_geom`, `gs`, `baro_rate`). It gets an explicit conversion
   in its reader, like the units guard in `normalize_frame`. Keep each fix's source type
   (`adsb_icao`, `adsr_icao`, `tisb_*`, `uat`).
3. **Passes:** split each aircraft's presence in the region into passes at gaps over 60 s.
4. **Matching:**
   - by `icao24` first;
   - by trajectory for anonymous or random addresses (UAT anonymous mode, privacy ICAO addresses): within
     200 m and 5 s for at least 30 s;
   - every unmatched pass is checked by hand on a map plot (read-only diagnostic).
5. **Classes per reference pass:** matched; UAT-only; 1090 but missing in OpenSky; TIS-B only. Prop, jet or
   heli comes from the type database (`aircraft_types`).
6. **Metrics:**
   - the share of reference passes missing in OpenSky, with a Wilson 95 % interval, per class and per
     airport. That is the p_u estimate;
   - for matched passes: OpenSky gap lengths, fix interval, horizontal and vertical offset from the
     reference after time alignment, the per-track timestamp bias, and the share of fixes with geoaltitude;
   - side by side with the realism layer's assumptions (Poisson gaps 5–60 s, σ 7 m, timestamp bias
     U(±0.6 s), 30 % baro-only props).

## Code and outputs (when built)
- **Pure functions** in a new `doppler/coverage.py`: pass splitting, matching, metrics.
- **Readers and a `cli.py coverage` subcommand** for I/O, once `cli.py` is free.
- **Tests:** tiny hand-made feeds with a known missing pass, a UAT-only pass and an anonymous address.
- **Outputs:**
  - `reports/p53_uat_coverage.md` (tables and the comparison with the realism assumptions);
  - per-pass rows as zstd Parquet in `outputs/` (gitignored).

  Expected size: a day of 1 s local fixes for two 10 km circles is tens of MB as JSON and a few MB as
  Parquet.
- **Measured values go into `reports/apriori_registry.md` as sources.** Moving any provisional config value
  to them is the user's decision. Do it only before the decision runs it would affect, never to hit a target.

## Open decisions for the user
1. Which reference: a local receiver (hardware and a site), an aggregator's day (which one, and its terms),
   or both.
2. When: alongside the first field session is the natural time.
3. Whether 10 km and below 3000 m AGL are the right region and band.
