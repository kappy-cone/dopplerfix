"""E1b: strict variant when event 2's aircraft has no ADS-B: what does the system report?"""
import sys
from e1_density import *
for K, s in ((30, 1), (60, 1), (60, 2)):
    trs, events, mic = scenario(K, 1000 * K + s)
    no2 = [t for t in trs if t.icao24 != "ev1"]
    print(f"K={K} seed={s}", analyse(no2, events, params(**STRICT), "strict/noadsb2", do_score=True), flush=True)
