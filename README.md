# dopplerfix


---

**Acoustic Doppler Geolocation**

Estimates where an audio recording was made from the aircraft heard in its background. Each flyover leaves a Doppler curve in the spectrogram. Matched against ADS-B flight tracks for the region and time, the curves pin down the microphone's location.

**How it works**
- **Event detection and characterization:** finds flyover tones in the spectrogram, extracts each ridge, and fits speed, closest-approach distance and time, with covariance-based uncertainties.
- **Track handling:** cleans and interpolates ADS-B tracks (OpenSky-style), with prop/jet classification from aircraft type codes.
- **Candidate cascade:** a 200 m grid with DEM and geoid-corrected heights. Cells are filtered by closest-approach consistency, by inter-event timing under one shared clock offset, and by a silence constraint: aircraft that should have been heard but weren't. All geometry is computed in ECEF.
- **Locked N-event scoring:** models each event's Doppler curve from real track geometry, including the travel time of the sound. All events share one time offset; each has its own source frequency. Cells are refined within their footprint.
- **Outputs:** ranked candidates, a score GeoTIFF and GeoPackage layers for QGIS, diagnostic plots, and an offline comparison viewer.

**Validation tooling**

A synthetic scenario generator with a realism layer covering ADS-B degradation, engine RPM changes, harmonics, absorption, ground reflection, wind, scene noise and traffic geometry. A paired leave-one-group-out ablation driver compares two design variants: a strict one (every event matched, hard silence) and a robust one (an unmatched event allowed, probabilistic silence).

**Status**

Research code, Python 3.11+. Clean synthetic scenarios locate correctly; realistic ones are still being worked on. Real-recording calibration and a US-wide benchmark are planned but not yet done. Runs are fully automated, and the pipeline is designed so that a human-in-the-loop mode can be added later.

The repo also includes an analysis of which audio processing (high-pass filtering, masking, phone codecs) does and doesn't defeat the method. That analysis is planned but not built yet.
