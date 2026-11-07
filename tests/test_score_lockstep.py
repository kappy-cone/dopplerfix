"""Lockstep centre scoring (ADR-0006): the generator Brent equals scipy's bounded search, and score_cell_lockstep
equals scoring each hypothesis on its own (the frozen oracle) on every CellScore field, bit for bit."""
import dataclasses

import numpy as np
import pytest
from scipy.optimize import minimize_scalar

from doppler import cascade as cs
from doppler import score

from . import _scoring_oracle as oracle
from .test_score import START, _equal, _two_track_setup


def _problem(rng):
    """A random 1-D objective: smooth, multimodal, or with plateaus (ties exercise the comparisons)."""
    kind = rng.integers(3)
    c0, w, ph, x0 = rng.normal(0, 2), rng.uniform(0.5, 20), rng.uniform(0, 6.3), rng.normal(0, 3)
    if kind == 0:
        return lambda x: float((x - x0) ** 2 + c0)
    if kind == 1:
        return lambda x: float(np.sin(w * x + ph) + 0.1 * (x - x0) ** 2)
    step = rng.uniform(0.01, 0.5)
    return lambda x: float(np.round(((x - x0) ** 2) / step) * step)


def _run_generator(f, a, b, xatol):
    gen = score._bounded_brent(a, b, xatol=xatol)
    probes, x = [], next(gen)
    while True:
        probes.append(x)
        try:
            x = gen.send(f(x))
        except StopIteration as stop:
            return probes, stop.value


def test_bounded_brent_probes_and_result_equal_scipy_on_random_problems():
    rng = np.random.default_rng(6)
    for _ in range(200):
        f = _problem(rng)
        a = float(rng.normal(0, 5))
        b = a + float(rng.choice([1e-4, 0.01, 1.0, rng.uniform(0.1, 20)]))
        xatol = float(rng.choice([1e-5, 1e-3, 0.1]))
        asked = []
        res = minimize_scalar(lambda x: asked.append(x) or f(x), bounds=(a, b), method="bounded",
                              options={"xatol": xatol})
        probes, (x, fun) = _run_generator(f, a, b, xatol)
        assert probes == asked                               # exact: same sequence of probes
        assert x == res.x and fun == res.fun and type(x) is type(res.x) and type(fun) is type(res.fun)


def test_bounded_brent_stops_at_maxiter_like_scipy():
    f = lambda x: float(np.sin(40 * x))                      # noqa: E731
    asked = []
    res = minimize_scalar(lambda x: asked.append(x) or f(x), bounds=(0.0, 10.0), method="bounded",
                          options={"xatol": 1e-12, "maxiter": 7})
    gen = score._bounded_brent(0.0, 10.0, xatol=1e-12, maxiter=7)
    probes, x = [], next(gen)
    with pytest.raises(StopIteration) as stop:
        while True:
            probes.append(x)
            x = gen.send(f(x))
    assert probes == asked and len(asked) == 7 and stop.value.value == (res.x, res.fun)


def _same(a: score.CellScore, b: score.CellScore) -> bool:
    return all(_equal(getattr(a, f.name), getattr(b, f.name)) and type(getattr(a, f.name)) is type(getattr(b, f.name))
               for f in dataclasses.fields(score.CellScore))


@pytest.fixture(scope="module")
def three_events():
    """The two-track setup, plus a third event with an empty ridge (scored as contributing nothing)."""
    ts, mic, evs = _two_track_setup()
    e = evs[0]
    empty = cs.ObservedEvent(2, 0, 200.0, 70.0, 700.0, 100.0, np.eye(3), (5.0, 5.0), np.array([]), np.array([]),
                             np.array([]))
    return ts, mic, evs, e, empty


HYPS = [((0, 1), (-5.0, 5.0)), ((1, 0), (-5.0, 5.0)), ((0, -1), (-3.0, 2.0)), ((-1, 1), (-1.0, 1.0)),
        ((0, 0), (-4.0, 6.0)), ((1, 1), (0.0, 0.0)), ((-1, -1), (-2.0, 2.0)), ((0, 1), (1.6, 1.6)),
        ((0, 1), (8.0, 9.0)), ((0, 1), (-20.0, -15.0)), ((1, 0), (-0.3, 0.4)), ((0, 1), (2.0, 2.4))]


@pytest.mark.parametrize("prior", [(-10.0, 10.0), None])
def test_lockstep_cell_equals_one_hypothesis_at_a_time(three_events, prior):
    ts, mic, evs, _, empty = three_events
    p = score.ScoreParams()
    for events in (evs, evs + [empty]):
        ridges = score.prepare_ridges(events, p)
        n = len(events)
        hyps = [cs.Hypothesis(5, (a + (-1,) * (n - 2) if n > 2 else a), b) for a, b in HYPS]
        new = score.score_cell_lockstep(hyps, ridges, ts, mic, START, 343.0, p, prior)
        old = oracle.score_cell(hyps, ridges, ts, mic, START, 343.0, p, prior)
        assert len(new) == len(old) == len(hyps)
        for a, b in zip(new, old):
            assert _same(a, b), (a, b)


def test_lockstep_single_hypothesis_and_empty_cell(three_events):
    ts, mic, evs, _, _ = three_events
    p = score.ScoreParams()
    ridges = score.prepare_ridges(evs, p)
    h = [cs.Hypothesis(1, (0, 1), (-5.0, 5.0))]
    assert _same(score.score_cell_lockstep(h, ridges, ts, mic, START, 343.0, p)[0],
                 oracle.score_hypothesis(h[0], ridges, ts, mic, START, 343.0, p))
    assert score.score_cell_lockstep([], ridges, ts, mic, START, 343.0, p) == []


def test_calibrated_likelihood_keeps_the_one_at_a_time_path(three_events):
    ts, mic, evs, _, _ = three_events
    p = score.ScoreParams(likelihood="calibrated")
    ridges = score.prepare_ridges(evs, p)
    hyps = [cs.Hypothesis(5, a, b) for a, b in HYPS]
    new = score.score_cell_lockstep(hyps, ridges, ts, mic, START, 343.0, p, (-10.0, 10.0))
    old = oracle.score_cell(hyps, ridges, ts, mic, START, 343.0, p, (-10.0, 10.0))
    assert all(_same(a, b) for a, b in zip(new, old))
