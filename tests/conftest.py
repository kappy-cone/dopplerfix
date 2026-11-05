import zlib

import numpy as np
import pytest

from doppler import audio, synth
from doppler.geo import LocalFrame


# --- CI sharding (CONTRIBUTING §7): `--shard i/n` keeps only the tests whose id hashes to shard i. ---
def shard_of(nodeid: str, n: int) -> int:
    """Deterministic shard 0..n-1 of a test id (same on every machine and every xdist worker)."""
    return zlib.crc32(nodeid.encode()) % n


def pytest_addoption(parser):
    parser.addoption("--shard", default=None, metavar="I/N",
                     help="run only shard I (0-based) of N, split by a hash of the test id (CI gate)")


def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--shard")
    if not spec:
        return
    i, n = (int(x) for x in spec.split("/"))
    if not 0 <= i < n:
        raise pytest.UsageError(f"--shard {spec}: need 0 <= I < N")
    keep = [it for it in items if shard_of(it.nodeid, n) == i]
    drop = [it for it in items if shard_of(it.nodeid, n) != i]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep

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
