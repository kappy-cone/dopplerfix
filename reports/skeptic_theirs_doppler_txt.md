# Skeptical review: the methodology described in `doppler.txt`

Scope: this reviews only the narration in `<repo>/doppler.txt`, an 839-line auto-caption transcript of a video. Anything that appeared only on screen (thresholds, equations, code, axis labels, tolerance values) is missing. Below, **"not described"** means the narration doesn't mention something. It does not mean they didn't do it. No other repo file was consulted.
Numbers marked *(calc)* come from `<scratch>/skeptic_theirs/calc.py`.

Their headline claim: from audio alone (date known, Sept 8; region = CONUS), they located a security camera "within a couple hundred feet" (L719–722), at about 40.87, −73.5 (L688–690). That is Long Island's North Shore.

---

## 1. Reconstructed methodology

Labels: **SHOWN** = a count, plot, overlay or result is presented. **CLAIMED** = asserted without a demonstrated result. **NOT DESC.** = the narration doesn't say.

| # | Stage | What the narration says | Lines | Status |
|---|---|---|---|---|
| 1 | Event detection | Done by hand in a spectrogram viewer: "zoom into the right frequency… increase the resolution." Three curves are noticed ("I think we have one more here"); two are chosen ("these two are what we'll be using"). The selection criteria aren't given. One curve is called inaudible. | 40–49, 67–74 | SHOWN (screenshots); selection criteria NOT DESC. |
| 2 | Prop vs. jet event label | By ear: "you can hear pretty intuitively that it's a propeller plane." Frequencies were boosted to listen. | 104–110 | CLAIMED (subjective) |
| 3 | Curve measurement / per-event fit | Model is straight, constant-velocity flight ("assuming linear constant velocity"). Speed sets the size of the drop; CPA distance sets how fast it happens. Fitting is manual with sliders: "best match 151.15. I'll move to 151.13, slide that fundamental frequency up… that could work." Speed is fitted to the right half only ("decent fit on the right side at 100 m/s… not 70"), because the left half doesn't fit an ideal curve. The output is a "pretty generous range" for each parameter, not an estimate with an uncertainty. | 245–298 | SHOWN (overlay); ranges NOT DESC. |
| 4 | Per-flyover parameters | Speed (≈100 m/s for one event), CPA slant distance ("pretty precise"), CPA time ("couple seconds of potential measurement error"), and f0 (slider). The ranges for event 2 aren't stated. | 283–298, 312–314 | Partly SHOWN |
| 5 | Inter-event offset | 920 s, measured from the audio. A ±"5-second variance" comes from where on the curve you measure, so there is "wiggle room." | 84, 205–218 | CLAIMED value; tolerance NOT DESC. |
| 6 | Flight data | A Python scraper run for "a few hours" collected all CONUS flights on Sept 8, with full flight paths. A paid API exists. "50 GB parquet files." Fields used: position, altitude, timestamp, speed, type code. Source, sample rate, and altitude field (baro or geometric) are not described. | 78–100, 57–58, 124–127 | SHOWN (map) |
| 7 | Prop/jet track filter | Type code → engine type via a found lookup table. Known jet types are removed, so props **and unknowns** are kept. | 111–120 | SHOWN (map) |
| 8 | Grid + delayed CPA | 200 m grid, initially within 10 km of each track ("pretty conservative"). For each point and flight, take the time of closest approach plus slant range / c (the "delayed CPA"). Example: 22:46:34 → 22:46:46, a 12 s delay ≈ 4.1 km *(calc)*. Each point stores a list of (flight ID, delayed CPA time). | 121–197 | SHOWN (one example) |
| 9 | Slant distance / altitude datum | "Altitude is the height above sea level"; ground elevation comes from a DEM; slant distance is 3D. Which ADS-B altitude field, and any geoid or pressure correction, are not described. | 167–179 | CLAIMED / NOT DESC. |
| 10 | Time-offset filter (alone) | Keep points with two delayed-CPA times 920 s ± tolerance apart. Result: about 35,000 points in a small test area (<0.05% of CONUS). | 197–224 | SHOWN (count) |
| 11 | Speed + distance filter | Keep track segments whose speed is inside each event's range. Lay candidate points at the fitted slant-distance range from those segments, then apply the offset filter. Result: "about 73,000" points (L316), later "roughly 70,000" (L359), clustered at airports. | 299–326, 348–359 | SHOWN (counts, map) |
| 12 | Silence filter | Disqualify any point that "would have heard a flight between our two events." The thresholds were shown on screen only, called "pretty conservative." Result: 70,000 → 15,000. The window covers only the interval between the events, and only the prop/unknown set (jets were already dropped). | 326–365 | SHOWN (count); thresholds NOT DESC. |
| 13 | Curve modeling | Doppler shift from the radial velocity in real ADS-B geometry, using emission time (retarded time), not reception time. Shown as a "wrong vs right" curve comparison on an actual spectrogram. The worked example (f0 = 130, v_r = 62.7 → 109.9 Hz) implies a fixed c = 343 m/s *(calc)*. | 399–510 | SHOWN (diagram, overlay) |
| 14 | Scoring | "Sum the brightness values that each curve covers." For each candidate, sweep event 1's time offset and both f0 values over "a few seconds" and "a few Hz," and take the best combination. Event 2's time is **locked** to event 1 by flight geometry ("event two falls wherever it does"). Normalization, harmonics, curve width, and the spectrogram parameters aren't described. | 515–575 | SHOWN (method) |
| 15 | Refinement | A 200 m grid is "too coarse." The correct cell "could be up to 141 m off" (half-diagonal, correct *(calc)*), so the grid is refined to 100 m (~30–40k points). After the winning pair is known: "much finer resolution with just that flight." Heat maps are shown for event 1, event 2, and combined. | 575–595, 641–648 | SHOWN (heat maps) |
| 16 | Ranking | Each point gets an (event 1, event 2) score pair, shown as a scatter plot. Points are grouped by flight pair and the best point per pair is kept. Top score 20.48; second ≈ 15 (event 1 score 9.5 vs. 10.6 for the winner, event 2 "badly off"). Overlays are shown for the top three. | 596–639 | SHOWN |
| 17 | Final location | The heat-map center is "honestly in water, so it'll be like right on this bank." Then Street View is used to match a tree line and find the camera on a pole. | 728–751 | SHOWN (visual match) |
| 18 | Single-event demo | "Say I told you this was 8:37 p.m. UTC." Use the one-minute faint curve, the velocity bounds, and only the segments at that time. The curve is **hand-traced** and scored by distance to the traced line. Top result is "spot on" and matches 40.87, −73.5. "Entirely different flights." | 652–702 | SHOWN (histogram, overlays); source of the time NOT DESC. |
| 19 | Countermeasure: high-pass | Applied a high-pass filter, exported MP3, the curve looked gone; "boost all frequencies, and the curve is back." Also argues the band overlaps the human voice. | 752–771 | SHOWN (one demo) |
| 20 | Countermeasure: drowning out | Speech was recorded for 15 s while a JBL speaker in the room played a Doppler curve, and the curve was visible. | 772–780 | SHOWN (one demo) |
| 21 | Countermeasure: phone call | A JBL playing the curve was heard through a phone call. Muting created a gap, and the curve came back when talking resumed. The conclusion: it "can survive phone compression." Recommended defenses: cuts, more noise cancellation. | 788–814 | SHOWN (one demo); "cuts work" CLAIMED |

The full pipeline is never shown rerunning on the processed audio in any countermeasure demo.

---

## 2. Ranked findings (most severe first)

### F1. Validation is one case, with the answer apparently known and settings chosen by hand. No false-positive rate.
- **Claim:** "Like any video you take could have that in there" (L724–726); the method "really works anywhere on that spectrum" (L337–338).
- **Evidence:** One recording (n = 1). The events were picked by eye (L74). Speed and distance ranges came from slider fits (L279–298). The silence thresholds were set by hand and called "conservative" (L341–347). The development loop is described as "look at some of the points, seeing why they don't work, and then encoding that logic as another filter" (L369–373). The footage is from "some random security camera" (L820–821) with a known date, so the presenter very likely knew or could look up the location. No clip is reported where the method failed, and none was run blind.
- **Failure scenario:** Tolerances, the choice of events, and the sweep widths each nudged toward settings that keep the known answer alive and on top. On a new clip with fresh settings, the true cell is silently filtered out, and the top-ranked cell is wrong but looks just as confident.
- **Severity:** Critical for any claim about reliability. **Confidence:** High that the evidence can't support a reliability claim. Unknown whether they actually overfit.

### F2. The single-event "independent confirmation" depends on the answer and agrees only to about 1 km.
- **Claim:** "The same location scoring first by a significant margin across two different methodologies with entirely different flights" (L691–694).
- **Evidence:** The time is introduced as "say I told you this was 8:37 p.m. UTC" (L661). The clip's only given metadata is the date (L13–16), so the most plausible source of that time is the two-event solution, because matching tracks gives absolute UTC. The faint curve was hand-traced (L675–681) by someone who already knew the answer. The two coordinates agree at "40.87, −73.5," which is two decimals: 0.01° is 1.11 km in latitude and 0.84 km in longitude at that latitude *(calc)*.
- **Physics:** With known time and one straight, level pass, the problem has two mirror solutions on either side of the track. Curve shape can't tell them apart; only turns, climbs, or altitude changes can. How far the time pins the point along the track is speed × time error. At 100 m/s, a time known only to the minute (±30 s) gives ±3 km; ±2 s gives ±200 m *(calc)*. Precision comparable to the two-event fix therefore needs the time to the second, which again comes from the solution.
- **What it really is:** A posterior-predictive check of a held-out third event, given the solution's time. That is worth something, but it isn't a second, independent localization, and the hand trace makes it non-blind.
- **Severity:** High (it is presented as corroboration). **Confidence:** Medium-high. The time source isn't stated, but no other source is mentioned.

### F3. Brightness-sum scoring has no noise model, no normalization stated, and no null distribution. The 20.48 vs. ~15 margin is uncalibrated.
- **Evidence:** "We just sum the brightness values that each curve covers" (L520–523). Each candidate keeps the best of a sweep over f0 and time (L548–554). Points are ranked across ~30–40k candidates and many flight pairs (L586–589). Combined score ≈ event 1 + event 2 (9.5 + ~5.5 ≈ 15; ~10 + ~10 ≈ 20.48).
- **Problems:**
  1. **Loudness and length dominate.** A raw sum rewards whichever event is louder (event 2 is "a lot louder," L69–70) and whichever modeled curve stays longer inside the analysis window or crosses more bins. Unless each event is normalized (not described), the louder event decides the ranking. The narration's scores do look comparable across events, so some normalization may exist; it isn't described.
  2. **Other tones in the background.** A security camera on a pole near a house is the classic setting for mains hum and HVAC tones. 120 and 180 Hz (60 Hz harmonics) sit right in the 100–160 Hz prop band (f0 ≈ 130–151 Hz per L290, L463). A nearly flat modeled curve (distant CPA, slow drop) gains brightness by running along a hum line. Brightness sums don't penalize this, and they don't penalize leaving observed ridge energy unexplained.
  3. **The sweeps inflate every score**, more for broad or flat curves, which fit "somewhere" in a ±few-s × ±few-Hz box.
  4. **Multiple comparisons.** The expected maximum of N independent standard-normal scores grows from 1.5σ (N = 10) to 3.3σ (N = 1,000) to 4.1σ (N = 30,000) *(calc)*. Taking the best point per flight pair also favors pairs whose clusters are larger. The real question is whether the gap from 20.48 to ~15 is large compared with how much scores vary across candidates and noise. The narration offers no null test (time-shifted or scrambled audio, wrong date, or scoring a clip from somewhere else).
  5. **Bin quantization and chirp smear.** At the maximum frequency slope R, a window that balances smear and bin width is T = 1/√R. For R ≈ 4–7 Hz/s, which their example geometry implies (6.6 Hz/s), that is 0.4–0.5 s and about 2 Hz bins *(calc)*. "Increase the resolution" (L48) for faint curves means longer windows, which smear exactly the steep CPA section that carries the timing information.
- **Failure scenario:** A wrong flight pair whose modeled curves run along a hum line and a strong ridge outscores the true pair, whose ridge is partly missing because of a reflection notch (F9) or a misfitted harmonic.
- **Severity:** High. **Confidence:** Medium (normalization may exist off-narration).

### F4. The silence filter and ADS-B completeness: rejecting the true cell, and events with no track at all.
- **Evidence:** "Disqualified any point that would have heard a flight between our two events" (L344–346), using prop/unknown tracks only. The 10 km radius is called "conservative" (L147–150) for building candidates.
- **Problems:**
  - "Conservative" flips meaning across stages. A large audibility radius is conservative when generating candidates but **aggressive** in a silence test. A track that "should" be audible but wasn't detected rejects the true cell. Causes include upwind refraction shadow zones, building or terrain shielding, masking by local noise, an aircraft gliding at low power, or a camera mic that rolls off low frequencies. The thresholds and whether they're probabilistic aren't described.
  - **ADS-B coverage in the US.** Many GA props transmit on UAT (978 MHz) rather than 1090ES. Many community feeds and 1090-only networks under-capture UAT, and low-altitude GA in rural areas has coverage gaps. Missing tracks have two effects:
    - They weaken the silence filter.
    - When the observed event came from an untracked aircraft, there is **no correct track to match**, and the method returns some other location with no warning.
  - Non-aircraft sources that also sweep in frequency: boats (the solution is on a harbor bank, L730), trucks, motorcycles, trains, mowers. These are handled only by the manual event choice.
  - The silence test ignores the time before event 1 and after event 2 (see §3 for why that is partly sensible).
- **Severity:** High in sparse coverage or noisy scenes. **Confidence:** High that the risk exists; its size is unknown.

### F5. Altitude datum: "altitude is the height above sea level" is wrong for either ADS-B altitude field.
- **Evidence:** L172–179. The DEM is presumably orthometric (≈ MSL).
- **Facts:**
  - ADS-B barometric altitude is **pressure altitude** referenced to 1013.25 hPa, with no altimeter-setting correction. The error is about 8.3 m per hPa of departure from standard, which is ±40–85 m for ±5–10 hPa and more in strong weather systems.
  - ADS-B geometric altitude is height above the **WGS84 ellipsoid**. The geoid undulation near 40.87, −73.5 is about −32 to −33 m (EGM96 / GEOID18; this is from memory, not computed here). Treating ellipsoid height as MSL overstates height above ground by about 32 m.
  - Quantization is 25 ft (7.6 m) for most ADS-B and 100 ft for Gillham-coded sources.
- **Effect on horizontal distance** (horizontal = √(s² − h²)) *(calc)*:
  - h = 500, s = 1000: errors of 32/85/140 m in height shift horizontal distance by −19/−55/−98 m.
  - h = 600, s = 800: −39/−116/−225 m.
  - Near-overhead passes (h = 600, s = 650): −98 m at 32 m error, and complete failure (no real solution) at 85 m.
- **Severity:** Medium. It exceeds the claimed "couple hundred feet" (~60 m) precision for steep geometries and can quietly push the true cell out of a tight distance gate. **Confidence:** High on the physics; low on what they actually did.

### F6. Fixed speed of sound; no temperature, wind, or vertical gradient.
- **Evidence:** The worked example reproduces only with c = 343 m/s (130 · 343/405.7 = 109.9) *(calc)*. Temperature and wind are never mentioned.
- **Magnitudes** *(calc)*:
  - c changes by −1.6% at 10 °C and +1.9% at 30 °C. An early-September afternoon on Long Island (≈ 25 °C) gives about +1.0%.
  - Fitted v and d scale roughly with c, so about 1 m/s and 1% of d.
  - Frequency sensitivity is d ln f / d ln c = 0.15–0.23 at v_r = 60–100 m/s, so about 0.3 Hz at 150 Hz and about 1 Hz at the third harmonic.
  - A 10 m/s wind changes the effective c along the path by ~3%: about 0.7% curve-shape error, a travel-time difference of ~0.09 s/km, and refraction that changes audibility (feeding F4).
  - Differential propagation delay between the two events changes by (r₂ − r₁)·Δc/c², which is about 0.1 s per 4 km at 1%.
- **Severity:** Low-medium for ranking; it matters at the claimed sub-100 m level. **Confidence:** High.

### F7. Per-event characterization is a hand fit with ad-hoc ranges; tolerances aren't principled.
- **Evidence:** Slider fitting (L289–295). "Pretty generous range… we can be pretty sure it wasn't going 70" (L280–286). Speed was fitted only on the right half of the curve (L275–283).
- **Problems:**
  - No covariance is computed, so the parameter ranges can't be reproduced.
  - Since d = f0·v²/(c·max slope), a generous v range (say ±15%) implies about ±30% in d, unless the joint v–d correlation is used. A box of independent ranges loses that constraint (conservative) but inflates the candidate count.
  - Fitting one half of an asymmetric curve biases v whenever the aircraft was accelerating or turning.
  - The ±5 s tolerance on the 920 s offset is intuition, not a measurement.
- **Severity:** Medium. Reproducibility and the risk of tuning with the answer known (F1). **Confidence:** High.

### F8. ADS-B timing and sampling limit the "exact flight geometry" claim.
- **Evidence:** "Model what the exact Doppler curve would have been for the actual flight geometry" (L413–415). The sampling rate and interpolation method aren't described.
- **Problems:**
  - At 100 m/s, 1 s of timestamp error or latency is 100 m along track. That is the same size as the grid refinement and larger than "a couple hundred feet" divided across two events.
  - Many archives thin positions to about 5–10 s or longer, sometimes with timestamps rounded to 1 s. Turns and speed changes, which they say break the ideal curve (L391–396), are then under-resolved.
  - Taking radial velocity from linearly interpolated positions gives a stepwise velocity and kinks in the modeled curve.
- **Severity:** Medium. **Confidence:** Medium.

### F9. Ground and water reflection (Lloyd's mirror) for a pole-mounted mic.
- Not mentioned. The camera is on a pole (L749) on a harbor bank (L730–731).
- **Magnitudes** *(calc)*: a mic 5 m up at 30° elevation has a path difference of 5 m, which puts notches at about 34, 103, 172, 240 Hz. At 60° elevation: 20, 59, 99, 139 Hz. The notches sweep as the elevation angle changes during the pass. Over water the reflection coefficient is close to 1, so the notches are deep.
- **Effect:** Parts of the ridge drop out at specific time and frequency points, which hurts a brightness sum (F3) and a manual right-half-only fit (F7). It doesn't bias frequency, but it does decide which parts of the curve count in the score.
- **Severity:** Low-medium. **Confidence:** High on the physics.

### F10. Constant-f0 assumption and harmonics.
- f0 is a single swept constant per event (L552). Fixed-pitch props change RPM with power and airspeed (on climbs, descents, or power reductions near airports), which distorts the shape over a pass of about 2 minutes (L141–142). Constant-speed props and turboprops are more stable.
- Engine firing-order harmonics and blade-pass harmonics are separate tone families with the same Doppler ratio, so they're harmless *if* the model tracks the right line. Whether harmonics are scored isn't described.
- **Severity:** Low-medium. **Confidence:** Medium.

### F11. The countermeasure conclusions go beyond what the demos show.
- **High-pass (L752–771):**
  - The demo shows that their particular high-pass left the tone above the MP3 noise floor, since boosting brings it back. It says nothing about a steep high-pass (for example 48 dB/octave at 300 Hz), which would bury the fundamental.
  - The real reason a high-pass likely fails is different: prop **harmonics** above the cutoff carry the same Doppler ratio, and f0 is a free parameter.
  - The voice-overlap argument holds for male fundamentals (~85–180 Hz).
- **"You can't drown it out" (L772–780):**
  - One 15 s demo with a loudspeaker playing a synthetic curve at an unknown, probably high, level, in the same room, against intermittent speech.
  - No broadband or continuous masker (music, traffic) is tested, and the pipeline isn't rerun to see whether localization survives.
- **Phone call (L788–807):**
  - Narrowband telephony (AMR-NB, G.711) passes roughly 300–3,400 Hz, so a 100–160 Hz fundamental is attenuated. The demo's survival suggests a wideband or VoIP codec, or harmonics, and again a loud source.
  - The gap during mute is consistent with voice-activity detection and discontinuous transmission (DTX) gating. In a real call the curve would appear only during speech: fragmented, with lost inter-event timing.
  - "It can survive phone compression" is shown for one codec, one level, one loudspeaker source, with no localization run.
- **"Making cuts works" (L781–783):** Cuts remove the inter-event offset and silence information, but their own single-event demo argues that known time plus one curve can still be enough.
- **Severity:** Medium (public-facing advice). **Confidence:** High that the conclusions don't follow from the demos.

### F12. The claim that it works in dense traffic isn't demonstrated.
- After the offset and speed filters, the survivors cluster at airports (L317–326). Near a busy airport, the silence filter, which does most of the pruning (70k → 15k), has no silence to work with.
- "If you're near an airport… you get a lot of signal" (L334–336) is asserted, not shown. Dense traffic also means overlapping curves and a harder event-to-track assignment, done here by hand.
- The jet exclusion also discards jet flyovers as silence evidence. Near JFK/LGA arrival flows (Long Island), that information is lost, and loud jets could mask faint prop curves.
- **Severity:** Medium. **Confidence:** Medium.

### F13. The precision claim isn't measured.
- The acoustic best point is "honestly in water, so it'll be like right on this bank" (L730–731). The final pinpoint comes from Street View matching of a tree line (L735–751). The distance from the acoustic estimate to the camera is never stated.
- "Within a couple hundred feet" (≈60 m) is plausible for a fine refinement but unverified. It sits below the 100 m refinement grid, whose maximum error is 70.7 m *(calc)*.
- **Severity:** Medium. **Confidence:** High that it isn't quantified.

### F14. Smaller physics and terminology slips (low severity)
- **"Closest point of arrival"** (L133): the term is closest point of *approach*.
- **"CPA… when the plane was closest and loudest"** (L144–145): peak level depends on source directivity and path effects. It is close to CPA for props but not in general, and the zero-Doppler time and the loudest time differ.
- **"Entirely wrong curve"** if you use reception-time radial velocity (L496–506): true in principle, but overstated in size. After the best time shift, the two curves differ by 0.2–1.5 Hz RMS (0.7–2.4 Hz max) for their example geometries (v = 60–100 m/s, d = 0.3–3 km) *(calc)*. The bigger practical error is an unmodeled delay of roughly r/c (1–10 s), which matters for locked inter-event timing.
- **Count inconsistencies:** 73,000 (L316) vs. "roughly 70,000" (L359) for the same stage. The CPA time is given as 22:46:36 (L158) and 22:46:34 (L182).
- **Date boundary:** "Sept 8" in local time vs. UTC matters for an evening recording in the western US. Not described.

---

## 3. What survives scrutiny

**Physics and design (sound):**
- **Retarded-time Doppler modeling** (L476–490): the observed frequency at reception time uses the radial velocity at the emission time. This is correct and often missed.
- **Delayed CPA with a 3D slant range and a DEM** (L160–183): the right construct for comparing an unknown-clock recording with track timestamps.
- **Modeling the real ADS-B geometry instead of inverting a straight-line model** (L405–418): the right call. Non-ideal curves become information instead of noise.
- **Locked inter-event timing** (L555–575): the strongest design choice. One shared time correction absorbs clock error and CPA-time measurement error. The relative timing comes only from physics, so a wrong cell can't fix its own timing. It also explains why the score surface is sharp (100 m along track ≈ 1 s ≈ several Hz on a steep CPA slope), and so why 200 m is too coarse.
- **Cheap cascade before expensive scoring**, with counts at each stage (35k / 73k / 15k / ~30–40k): the right structure for a CONUS search, and the counts are reported.
- **Excluding known jets but keeping unknown types** (L115–119): gaps in the type database can't throw away the true track.

**Silence and ranking choices:**
- **Restricting silence to the interval between events**:
  - Partial passes at the clip edges and events at the edges are hard to judge.
  - The interval between two detected events is where "no detection" is most believable, because the recording demonstrably captured aircraft before and after it.
  - It's a defensible trade of power for safety, given F4.
- **Best point per flight pair** (L605–613):
  - Collapses each cluster of correlated points into a single hypothesis, an assignment of tracks to events.
  - The comparison then becomes the top assignment vs. the runner-up, the meaningful quantity, rather than near-duplicate points crowding the top N.
  - Showing overlays for the second and third pairs (L623–639) is honest diagnostic practice.

**Scoring and confirmation:**
- **Brightness-sum scoring** buys real things:
  - No ridge extraction, so it works on faint and fragmented ridges where peak-picking fails.
  - Every pixel along the model counts, and there are no detection thresholds to tune.
  - It's tolerant of gaps such as reflection notches and masking.
  - With per-event normalization, a local noise floor, and a penalty for unexplained ridge, it could stay as a robust secondary score.
- **Street-View confirmation** is a genuine independent check, provided the location wasn't already known.

**Manual steps: where an analyst adds value vs. what is simply not yet automated**

| Manual step | Verdict |
|---|---|
| Choosing which curves are aircraft events, and rejecting boats, trucks and hum (L67–74) | **Genuine analyst value**, especially at low SNR and in cluttered scenes. It must be done blind to any candidate location. |
| Hand-tracing a very faint curve (L675–681) | **Genuine value at the limit of detection**, but only blind to the answer. Otherwise it is the most bias-prone step in the video. |
| Final visual confirmation (Street View tree line, L735–751) | **Genuine analyst value** that can't be replaced. It turns a 100 m acoustic fix into the exact camera. |
| Slider fitting of v, d, t0, f0 and "generous ranges" (L279–298) | **Just unautomated.** Least squares with covariance does this better and reproducibly. |
| ±5 s offset tolerance, silence thresholds, 10 km radius | **Just unautomated.** They should come from measured uncertainty and calibration data, not intuition. |
| "Look at failing points, encode a new filter" (L369–373) | A legitimate development practice on a calibration set. With the test answer known, it is overfitting. |
| Picking which flight to refine at finer resolution (L642–644) | Mechanical. Refine the top-K pairs automatically. |

---

## 4. Claims that are wrong or unsupported

1. **"Altitude is the height above sea level"** (L174). Wrong for both ADS-B fields: barometric altitude is pressure altitude at 1013.25 hPa, and geometric altitude is ellipsoidal (geoid ≈ −32 m there).
2. **Two methods with entirely different flights agree on the location, so it's confirmed** (L691–694). Not independent if the 8:37 UTC time came from the solution. The curve was hand-traced with the answer known. The agreement shown is at two decimals (~1 km).
3. **"Within a couple hundred feet"** (L720–722). The acoustic error distance was never measured; the acoustic best point was in the water.
4. **"It really works anywhere on that spectrum"** (L337–338) and "any video you take could have that in there" (L724–726). Unsupported with n = 1, no failure analysis, and unaddressed UAT and low-altitude coverage gaps.
5. **"You can't drown it out"** (L780) and **"it can survive phone compression"** (L808–810). Each rests on one demo with a loud loudspeaker source, without rerunning localization. Narrowband telephony removes the fundamental, and DTX gating fragments the curve.
6. **"High pass doesn't work"** (L780). The demo shows that their gentle filter didn't work. The general conclusion may still be right, but because of harmonics, which aren't the reason given.
7. **Using reception-time radial velocity gives "an entirely wrong curve"** (L500–503). Overstated: after alignment the shape differs by about 1–2 Hz. The main error is timing.
8. **"10 km radius is conservative… beyond that you probably wouldn't hear a plane"** (L147–150). Spreading from 300 m to 10 km is only about −30 dB *(calc)*. A narrowband tone can be detected in a long-window spectrogram well below the broadband level, and they found an "inaudible" curve. 10 km isn't obviously conservative for detection. Used as an audibility threshold in a silence test, it is aggressive.
9. **"There's just not much more we can do"** before curve modeling (L374–381). That's rhetorical. Curve shape, harmonics and level all remained unused at that point.

---

## 5. Open questions the transcript can't answer

1. Where did "8:37 p.m. UTC" come from? Was the single-event run done before or after the two-event solution? Was the faint curve traced before the location was known?
2. Did the presenter know the camera's location before running the pipeline? Were any parameters changed after seeing intermediate results?
3. Which ADS-B source (OpenSky, ADS-B Exchange, adsb.lol, other) and which altitude field were used? Sample interval, timestamp precision, interpolation method? Was UAT data included?
4. What were the per-event v, d, t0 and f0 ranges, how were they set, and how wide were the sweeps ("a few seconds," "a few Hz")?
5. What were the silence-filter thresholds (slant distance, altitude, a probabilistic or hard cut)? Was it applied before event 1 or after event 2? Were helicopters and unknown types included in it?
6. Brightness score details: linear or dB values, noise-floor subtraction, per-event normalization, curve width in bins, harmonics modeled or not, spectrogram window and hop, frequency band? Is the combined score a sum?
7. What was the score variance or null? For example, the score of the true pair on time-shifted audio, or the distribution of best-per-pair scores.
8. How many flight pairs survived to scoring, and how many points did each cluster have?
9. What was the distance between the acoustic best point (in water) and the camera?
10. Was the third curve noticed at L73 the one used in the single-event demo? Were any other curves present and ignored, and were any of them between the two events (which would contradict the silence assumption)?
11. Speed of sound, temperature, wind: fixed c = 343 m/s throughout? (The worked example implies yes.)
12. Mic height and any vertical datum for the DEM (NAVD88?) relative to the altitude field used?
13. Have any other clips been tried, and with what success rate? Is there any blind test?
14. For the countermeasures: what were the source level, the high-pass slope and cutoff, the MP3 bitrate, the phone codec and network type? Was localization rerun on any processed clip?
