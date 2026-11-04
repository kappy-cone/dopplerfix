import numpy as np
import pytest

from doppler import audio, synth
from doppler.geo import LocalFrame

FRAME = LocalFrame(40.0394, -105.2258, 1600.0)
START = 1781532000.0  # 2026-06-15T14:00:00Z


def make(seed: int = 0, **kw) -> synth.Scenario:
    return synth.make_scenario(synth.SynthParams(seed=seed, **kw), FRAME, START, ground_elev_m=1600.0)


@pytest.fixture(scope="session")
def scenario0():
    return make(0)


@pytest.fixture(scope="session")
def spec0(scenario0):
    return audio.compute_spectrogram(scenario0.audio, scenario0.fs_hz)


def match(fits, truths):
    """Pair each truth with the fit whose received CPA time is nearest (None if no fits)."""
    out = []
    for tr in truths:
        if not fits:
            out.append((tr, None))
            continue
        e = min(fits, key=lambda e: abs(e.t_cpa_received_s - tr.t_cpa_received_rel_s))
        out.append((tr, e if abs(e.t_cpa_received_s - tr.t_cpa_received_rel_s) < 10 else None))
    return out
