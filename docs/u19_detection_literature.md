# U19 detection: what the literature and the step-2 screen say to try next

Input for the U19 session (plan §8.2). It is tailored to the step-2 screen in `reports/u19.md` (main, 49cbd20)
and to the detector and ridge code at that commit. It proposes arms and diagnostics. It
changes no code, threshold or decision; each arm below would be registered and screened under U19's own
rules.

## 1. Where detection stands (from `reports/u19.md`)
**All-on, seeds 0–49:**
- Base recall is 75 / 100, with 25 events missed.
  - **11 never got a window.** Arm b (3 s window) recovers 7 of these, with no rise in spurious events, so 4
    remain.
  - **14 got a window but no fitted event.** No arm recovers any of them. They are the largest remaining loss.
- Arm a (harmonic sum) does nothing. Arm c (low band) helps only `loo:path`.

**What drives the misses:** `loo:X` and `sub:X` both mean all-on with X switched off (`realism.without`).
- Removing the level model (`loo:levels`, 94 / 100) recovers most misses, and so does removing scene noise
  (`loo:scene`, 92 / 100).
- So misses are driven mainly by events placed near the detection level, and by transient noise (wind
  gusts, cars, birds, hum). No single propagation effect dominates.
- Removing ground reflection *lowers* recall (`sub:reflection`, 52 / 100), because the reflection adds
  coherent low-frequency energy (plan §8.0).

## 2. The gaps, traced to the code
**G1. Lost after detection (14 events): the ridge stage thresholds per column.**
- `ridge` runs a Viterbi-style DP over the *unthresholded* excess, which is right. Then `_finish_ridge` drops
  every column below `ridge.weak_db` = 12 dB.
- It returns no ridge unless at least `min_points` = 50 columns (5 s at the 0.1 s hop) and `min_ridge_s` = 5 s
  remain.
- An event that triggers near its loud CPA but has weaker approach and recede segments loses most of its
  path here. So does one whose columns fade intermittently (gusts, fringes).
- The fit then never sees it. A fit whose t_cpa lands more than 10 s from the truth also counts as a miss.
- **The breakdown is not measured yet.** The first step is to measure it (§4, D1).

**G2. Detection works on single frames.**
- The window statistic is, per frame, the maximum excess over the band (about 861 one-hertz bins for
  40–900 Hz), median-smoothed over 3 s.
- In pure noise that maximum already sits about 10.25 dB above the per-bin median
  (`theoretical_baseline_db`). A window then needs a further `trigger_db` = 10 dB.
- So a tone is detected only when its strongest bin, frame by frame, clears roughly 20 dB above the local
  median. This is an estimate from the code; the percentile baseline can lower it on some clips.
- Nothing integrates evidence along the Doppler curve over the 20–60 s a pass lasts.
- Arm b works because a 3 s window integrates coherently on the near-constant approach and recede
  plateaus, about 4.8 dB for a steady tone. Near CPA it smears: the slope reaches f0·v²/(c·d) ≈ 7 Hz/s in the
  reference case, about 21 Hz over 3 s. So it helps detection, not the CPA-region ridge that sets t0 and d.

**G3. Why arm a found nothing (and why harmonics won't give SNR).**
- `_harmonic_rows` averages the dB excess at f, 2f, 3f. That is a geometric mean of power ratios, so one weak
  harmonic drags the row down: −3 dB per order, ±6 dB scatter, the occasional reflection fringe or
  directivity null.
- Even the ideal combination gains little at −3 dB per order:
  - an equal-weight linear power sum's deflection is (1 + 0.5 + 0.25) / √3 ≈ 1.01 times the fundamental's;
  - SNR-matched weights give √(1 + 0.25 + 0.0625) ≈ 1.15, i.e. +0.6 dB.
- **Conclusion:** harmonics are worth using for robustness (diversity when one harmonic fades, and the
  shared Doppler ratio as a constraint), not as a detection-SNR gain. U19 found the same.

**G4. Transient noise.**
- `loo:scene` recovers 17 events. The frequency-median background (±20 Hz per frame) removes broadband
  level but not stationary lines.
- Hum combs at 60, 120 and 180 Hz sit inside the prop band, appear as tonal excess, and can capture or cross
  the DP path.
- Gusts and car passes produce short frames of heavy-tailed excess, which the max-over-band statistic and
  the 12 dB column gate both treat as signal.

## 3. What the sources say
What was verified, and how, is noted per item. Quotes are kept to short phrases.
- **HMM / Viterbi frequency-line tracking.** Streit & Barrett (IEEE TASSP 1990) and Paris & Jauffret (IEEE TAES
  2003) are known from abstracts. Luo & Shen, *A Sensing and Tracking Algorithm for Multiple Frequency Line
  Components in Underwater Acoustic Signals*, Sensors 19(22):4866, 2019, was read in full (PDF).
  - **Method:**
    - Background equalisation first (an estimated noise floor is subtracted).
    - A coarse "high false alarm" pre-screen picks the sub-images worth processing.
    - Frequency bins are the hidden states, with a Gaussian transition model and a maximum offset R per frame.
      The observation likelihood is Rician (Bessel I0) when the SNR is known, otherwise normalised power.
    - Viterbi finds each line's path on the *unthresholded* spectrum.
    - Only then is each point on the path checked (their eq. 15), by a "local 3σ criterion": a point counts if
      its power exceeds the mean of its ±M neighbouring bins plus 3σ. That also sets the line's start and end.
      For exponential noise (σ = mean) the bar is 4× the local mean, about 6 dB above the mean or 7.6 dB above
      the median.
  - **Results** (simulation; Pd and Pf counted per frequency point; SNR is the wideband line-spectrum SNR after
    background equalisation, so not comparable with this repo's band or per-bin SNRs):
    - Viterbi on the unthresholded image reaches Pd = 0.5 about 3 dB lower than "pre-detection clustering"
      (detect per frame, then link). At −30 dB, Pd ≈ 0.85 against ≈ 0.25.
    - The price is a false-point rate of about 0.08–0.1, against about 0, over −30 to −20 dB.
    - Against a plain whole-image HMM: about 1–2 dB better, and 11.4 s against 44 s of compute.
  - **For us:**
    - Find the path before testing anything. The repo's DP already does that.
    - Test points against a *local, noise-referenced* bar, about 6 dB over the local mean, rather than a fixed
      12 dB above the median.
    - Expect, and measure, more false points.

    G1's fixed 12 dB gate sits about 4–6 dB above what this tracker uses.
- **Track-before-detect** (Bossér, Lundberg Nordenvaad, Hendeby, Skog, *Broadband Passive Sonar
  Track-Before-Detect Using Raw Acoustic Data*, arXiv 2412.15727, Dec 2024; full text read).
  - **Method:** a Bernoulli filter with SNR as a state. A track is confirmed when the existence probability
    exceeds 90 %.
  - **Result:** in simulation it first detects at about −9 dB, against about −5 dB for a CFAR-detector
    tracker. That's one comparison read off a figure, not a Pd/Pfa curve.
  - **Real data:** two things mattered, a heavy-tailed (multivariate-t) likelihood and noise whitening.
    Without temporal whitening the tracker produced tracks at wrong bearings.
  - **For us:** integrating before deciding is worth several dB. And a heavy-tailed noise model is how real
    transients are kept from becoming tracks (G4). The array and bearing machinery doesn't transfer.
- **Detect-on-track with the Hough transform.** The first two items are known only from search-result
  summaries of their abstracts.
  - Lee & Shyu, ASA 1995 abstract: it sums amplitudes along modelled delay curves over a grid of CPA, speed and
    heading, and uses nonlinear sums to cut sidelobes.
  - Goldsmith et al., US patent 10353060: each Hough peak over a movement model gives a vehicle's velocity
    and time of closest approach from a spectrogram.
  - Lo, Perry & Ferguson, *Aircraft flight parameter estimation using acoustical Lloyd's mirror effect*
    (index-page abstract): ground-reflection interference fringes in a near-ground microphone's
    time-frequency plane, a Cramér–Rao feasibility analysis, and both nonlinear least squares and a
    *generalized Hough transform* for the flight parameters.
  - **For us:** the repo already has the right template family. `ridge.doppler_hough` is a matched filter of
    exact straight-pass Doppler curves (log-frequency, over v and τ), used today only to seed the ridge
    inside a window.
- **Ferguson's single-microphone aircraft work** (DSTO). Ferguson, JASA 92(3):1403, 1992. Ferguson & Quinn,
  JASA 96(2):821, 1994. JASA abstract pages read; the full texts are pay-per-view.
  - **Method:** blade-rate frequency is estimated "at short time intervals" (STFT, and in 1994 also the
    Wigner–Ville distribution). A flight model is then fitted by least squares for speed, altitude and the
    rest frequency.
  - **Validation:** 18 instrumented transits at 250–1500 ft and 150–250 kn.
  - **For us:** prior art for the fit stage. Detection is not their subject.
- **Transit** (Guier & Weiffenbach, *Genesis of Satellite Navigation*, JHU APL Tech. Digest 18(2), 1997;
  full text read).
  - Closest-approach distance came from the maximum slope of the Doppler curve.
  - Accuracy needed "the whole Doppler curve".
  - The inverse (receiver) problem had 2 position parameters plus frequency offset. Harmonically related
    broadcast frequencies were used to remove a propagation error.
  - **For us:** harmonics' value is the shared ratio (one f0, one Doppler ratio), consistent with G3. Whole
    curves are worth integrating (G2).
- **Prior art, same goal:** [Espaye/aircraft-doppler](https://github.com/Espaye/aircraft-doppler) (MIT, last
  commit 2026-11-03; README and docs/method.md read).
  - **Detection:** two background subtractions, a per-frequency median over "~40 s blocks" (removes hums and
    fixed tones) and a per-frame median over "±20 Hz". Then peak linking, harmonic grouping by a constant
    frequency ratio, and a quality score.
  - **Physics:** propagation delay is modelled by solving for emission time.
  - **Real results (one site, blind):** 7 of 13 and 4 of 5 passes explained, with errors of 17 m and 114 m.
  - **For us:** the time-median background targets G4's stationary lines. Its recall on real piston and
    turboprop traffic, about 55–80 % of passes, is a useful reality check for milestone 4.5.
- **Aircraft Doppler on a buried fibre** (Gurevich, Isaenkov, Erbe, Gavrilov, Sidenko, Tertyshnikov, Vorobev,
  Pevzner, *Detection of aircraft noise using distributed acoustic sensing with a buried telecommunication
  cable*, npj Acoustics 1:2, 2025; read in full).
  - **Detection was by visual inspection** of spectrograms. The authors call it subjective judgement; no
    automatic detector.
  - **Noise removal** uses a median filter in two steps: first "vertical stripes" (broadband bursts), then
    "horizontal stripes" (persistent narrow-band noise), each by estimating and subtracting a low-frequency
    trend.
  - **The model includes emission time** (their eq. 3). Curved paths near an airport are fitted as two
    straight segments joined by an arc.
  - **Fit accuracy** for one Saab 340B: d = 600 m against a 541 m altitude, speed 365 against 364.8 km/h, and
    closest-approach time within 0.5 s of Flightradar24.
  - **Range:** turboprops were detected out to about 2.5 km on a single channel. Jets were not detected, a
    limitation of the instrument's 10 m gauge length.
  - **For us:** an independent use of the same two-step background as Espaye's tool, i.e. D4. The ~2.5 km
    turboprop range is a different sensor, so it is not a calibration for `audible_slant_m`.
- **The two PDFs already reviewed** (AMOS 2018 satellite Doppler; Aydın 2018 passive-radar thesis):
  - CA-CFAR per pixel, then greedy cell-following with a persistence count and quadratic gap bridging.
  - These are weaker than what the repo has (amplitude discarded, no physical curve model). They are not
    proposed.

## 4. Proposals, in order
None of these moves `detect.trigger_db`, `ridge.weak_db` or any existing threshold. Each arm is off by default
(V0 and V1 unchanged), registered in `reports/apriori_registry.md` before its first measurement, and screened
like arms a–c: seeds 0–49, the same six configs, paired by audio.

**D1. Breakdown of the 14 lost-after-detection misses (reporting only, no knob). Do this first.**
- For each truth event that has a window but no matched fit, record where it was lost:
  1. `extract_ridges` returned nothing (`_finish_ridge` columns < 50, or span < 5 s), with the number of
     columns above `weak_db`;
  2. the fit was rejected (bounds, `min_descent_frac`, outliers);
  3. the fit was accepted but t_cpa is more than 10 s from the truth (edge-clipped, or locked on a harmonic or
     half-order);
  4. it was removed by `dedupe_same_aircraft` / `is_harmonic_of`;
  5. it was classified as too slow (`classify.min_aircraft_speed_mps`).
- Also record arm b's remaining 4 window-less misses.
- **Why first:** it decides between D2 and the fit-stage fixes, at the cost of a screen re-run.

**D2. Path-level ridge acceptance (arm d). Targets G1, if D1 shows cause 1 dominates.**
- **Change:** keep the DP path, and in this arm replace the fixed per-column 12 dB gate with two tests:
  - **The whole path.** A ridge is accepted if its summed excess over its span exceeds a threshold set for a
    stated false-alarm rate per window. The threshold comes from the same statistic on exponential noise, by
    fixed-seed Monte Carlo, as arm a's baseline was made.
  - **Each point, against local noise.** Points are kept by Luo & Shen's local 3σ rule (mean of the ±M
    neighbouring bins plus 3σ, about 6 dB over the local mean for exponential noise) instead of 12 dB above
    the median. Kept weak points carry a per-point σ_f from their SNR, so the scorer can down-weight them.
- **Knobs:** the false-alarm rate (a requirement with an a-priori meaning) and the 3σ multiplier (source: Luo
  & Shen 2019, eq. 15). Neither is a tuned dB value. `ridge.weak_db` stays as it is for V0 and V1.
- **Risks to measure:** spurious ridges per run (Luo & Shen's false-point rate rose to ~0.1), harmonic and
  half-order lock, and t0 bias from long weak tails.

**D3. Doppler-Hough detection (arm e). Targets G2 and seeds G1.**
- **Change:** run `ridge.doppler_hough`, already bounded by `hough_v_range_mps`, `hough_tau_range_s` and
  `hough_harmonics`, as a detection statistic over the clip in chunks. A template's score is the summed excess
  along an exact Doppler curve across the whole pass. It covers both plateaus and the CPA slope, which neither
  a 1 s nor a 3 s window does.
- **Threshold:** from the statistic's noise distribution (Monte Carlo) at a stated false-alarm rate per
  clip-hour. A detection's best (v, τ, t0, f0) then guides `guided_ridge` directly, which should also help G1.
- **Expected gain (rough, to be measured):** incoherent integration over N roughly independent frames
  improves deflection by about 5·log10 N dB. That's 6.5 dB for N = 20 one-second frames, minus the
  look-elsewhere cost of the template bank, and minus what the 3 s smoothing already captures. The two
  measured anchors are about 3 dB (Luo & Shen: Viterbi against detect-then-link) and about 4 dB (Bossér et al.:
  track-before-detect against a CFAR tracker). A physical-curve template should do at least as well as those
  generic motion models; expect a few dB, not 10.
- **Cost:** the Hough over a whole clip is new compute; measure it against the 900 s cap.

**D4. Transient-robust background (arm f). Targets G4; lower priority.**
- **Change:**
  - (i) Also subtract a per-frequency median over a long block (`detect.time_floor_s`, e.g. 40–120 s), to
    remove stationary lines such as hum before the frequency median. Two independent systems do exactly this
    two-step stripe removal: Espaye's tool and Gurevich et al. 2025.
  - (ii) Treat frames whose broadband level jumps (gusts, cars) as heavy-tailed. For example, cap their
    excess, or weight them down in D2/D3's sums (cf. the multivariate-t likelihood in Bossér et al.).
- **Risk:** a long-block median eats a slowly drifting plateau. Keep the block longer than the plateau, and
  measure on `loo:scene` against all-on.

**D5. Harmonics for diversity only (no separate arm).**
- If D3 is built, its templates already sum `hough_harmonics` = 3 harmonics. Make that sum in *linear*
  power, or the maximum of the per-harmonic sums, never a mean of dB.
- Expected effect: small on average (G3). It helps when one harmonic sits in a reflection fringe; more fringes
  arise at the larger microphone heights in `mic_height_range_m`.
- Don't pursue arm a further.

**Not proposed:**
- **CA-CFAR pixels with greedy linking** (Aydın 2018): it discards amplitude and does worse than the DP.
- **A generic Bernoulli or particle-filter tracker:** its motion model is weaker than the known Doppler-curve
  family.
- **Quadratic gap bridging:** the physical curve model is better.
- **Exploiting Lloyd's-mirror fringes for altitude** (Lo, Perry & Ferguson): a later fit-stage feature, not
  detection.

## 5. Caveats
- **Abstract-only sources:** Ferguson 1992, Ferguson & Quinn 1994 and Lo, Perry & Ferguson are known from
  their abstracts. Their full texts weren't available.
- **The ~4 dB track-before-detect figure** is a single simulated comparison. Luo & Shen's ~3 dB is from
  simulation with a wideband SNR definition.
- **The integration-gain and ~20 dB trigger numbers** are back-of-envelope figures from the code and standard
  detection theory. They are not measured.
- **Synthetic level model:** the level model puts synthetic events on a detection cliff (plan §8.0). Detection
  thresholds and false-alarm rates are calibrated only on milestone-4.5 field recordings
  (`docs/field_recordings.md`). Screens here measure relative gains.

## Sources
- Luo & Shen, Sensors 19(22):4866 (2019): https://www.mdpi.com/1424-8220/19/22/4866
- Gurevich et al., npj Acoustics 1:2 (2025): https://doi.org/10.1038/s44384-025-00007-8
- Bossér et al., arXiv 2412.15727 (2024): https://arxiv.org/abs/2412.15727
- Ferguson, JASA 92, 1403 (1992): https://pubs.aip.org/asa/jasa/article/92/3/1403/697032/A-ground-based-narrow-band-passive-acoustic
- Ferguson & Quinn, JASA 96, 821 (1994): https://pubs.aip.org/asa/jasa/article/96/2/821/970519/Application-of-the-short-time-Fourier-transform
- Lo, Perry & Ferguson, Lloyd's mirror (index record): https://manuscript.isc.ac/Inventory/49/1267618.htm
- Lee & Shyu, ASA 1995 abstract: https://auditory.org/asamtgs/asa95wsh/2aUW/2aUW26.html
- Goldsmith et al., US 10353060: https://patents.justia.com/patent/10353060
- Guier & Weiffenbach, APL Tech. Digest 18(2) (1997): https://secwww.jhuapl.edu/techdigest/Content/techdigest/pdf/V18-N02/18-02-Guier.pdf
- Espaye/aircraft-doppler: https://github.com/Espaye/aircraft-doppler
