"""Rebuild a scenario's first make_scenario call (same seed/config) and dump per-event hidden draws
(kinematics + source realism) that truth.json does not record."""
import sys, types
import numpy as np
sys.argv = ['x']
import cli
from doppler import synth, realism as rl, audio
from doppler.geo import LocalFrame
cfg_path, seed, dur, ev = sys.argv_extra if hasattr(sys, 'argv_extra') else (None,)*4
