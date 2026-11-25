"""Regression tests for simulator bugs: sample hits, zero crossings, gated subsystems, state left between runs."""

import math
import os

import numpy as np
import pytest

from nervus import Diagram, simulate
from nervus.blocks import (
    Abs,
    Constant,
    DiscreteIntegrator,
    Gain,
    Inport,
    Integrator,
    Outport,
    Saturation,
    Scope,
    Sine,
    Step,
    Subsystem,
)
from nervus.blocks.user import PythonFunction
from nervus.core.errors import SimulationError
from nervus.coverage import Coverage


def _discrete_integrator() -> Diagram:
    d = Diagram()
    di = d.add(DiscreteIntegrator(sample_time=0.1), name="di")
    d.chain(d.add(Constant(value=1.0)), di, d.add(Scope()))
    return d


@pytest.mark.parametrize("solver", ["euler", "RK45"])
def test_negative_start_time_keeps_the_sample_hits_before_zero(solver: str) -> None:
    res = simulate(_discrete_integrator(), 1.0, t_start=-1.0, solver=solver)
    assert res["di"][-1] == pytest.approx(2.0)  # 20 hits in [-1, 1) of 0.1 each
    assert res["di"][int(np.argmin(abs(res.t)))] == pytest.approx(1.0)


def test_fixed_step_start_time_off_the_sample_grid_is_an_error() -> None:
    with pytest.raises(SimulationError, match="start time"):
        simulate(_discrete_integrator(), 1.0, t_start=0.05, solver="euler", step=0.1)
    res = simulate(_discrete_integrator(), 1.05, t_start=0.05, solver="euler", step=0.05)  # on the grid: fine
    assert res["di"][-1] == pytest.approx(0.9)  # hits at 0.1 ... 1.0; the output at 1.0 is before its update


@pytest.mark.parametrize("make", [lambda: Saturation(upper=1.0, lower=0.0), lambda: Abs()])
def test_signal_resting_on_zero_is_not_a_zero_crossing(make) -> None:  # type: ignore[no-untyped-def]
    d = Diagram()
    s = d.add(Step(time=1.0, initial=0.0, final=-1.0), name="u")  # exactly on the limit until t = 1
    d.chain(s, d.add(make(), name="b"), d.add(Integrator(), name="i"), d.add(Scope()))
    res = simulate(d, 3.0)
    assert res.stats is not None
    assert len(np.unique(res.t)) == len(res.t)  # no repeated time points
    assert not res.stats.warnings  # no "chatters" warning
    assert res.stats.zero_crossings <= 1


def test_frozen_modes_do_not_leak_into_the_next_run() -> None:
    d = Diagram()
    sat = d.add(Saturation(upper=1.0, lower=-1.0), name="sat")
    d.chain(d.add(Sine(amplitude=2.0)), sat, d.add(Integrator(), name="i"), d.add(Scope()))
    code = "import os\ndef f(t, u):\n    if t > 0.5 and os.environ.get('NERVUS_TEST_BOOM'):\n        raise RuntimeError('boom')\n    return u\n"
    d.chain(sat, d.add(PythonFunction(code=code), name="pf"), d.add(Scope()))
    reference = simulate(d, 3.0, solver="euler", step=0.01)
    os.environ["NERVUS_TEST_BOOM"] = "1"
    try:
        with pytest.raises(SimulationError, match="boom"):
            simulate(d, 3.0)
    finally:
        del os.environ["NERVUS_TEST_BOOM"]
    assert sat.mode is None
    again = simulate(d, 3.0, solver="euler", step=0.01)
    assert np.max(again["sat"]) == pytest.approx(1.0)
    assert again["i"][-1] == pytest.approx(reference["i"][-1])


def _enabled(source, enable) -> Diagram:  # type: ignore[no-untyped-def]
    inner = Diagram()
    u = inner.add(Inport(port=1), name="u")
    g = inner.add(Gain(gain=2.0), name="g")
    i = inner.add(Integrator(), name="i")
    inner.chain(u, g, inner.add(Outport(port=1), name="y"))
    inner.connect(u, i)
    inner.connect(i, inner.add(Outport(port=2), name="area"))
    d = Diagram()
    ss = d.add(Subsystem(system=inner, enable_port=True), name="task")
    d.connect(d.add(source, name="c"), (ss, 0))
    d.connect(d.add(enable, name="en"), (ss, 1))
    d.connect((ss, 0), d.add(Scope()))
    d.connect((ss, 1), d.add(Scope()))
    return d


@pytest.mark.parametrize("phase", [0.0, 0.1])
@pytest.mark.parametrize("zero_crossing", [True, False])
def test_continuous_enable_with_variable_step(phase: float, zero_crossing: bool) -> None:
    d = _enabled(Constant(value=1.0), Sine(frequency=1.0, phase=phase))  # enabled while sin(t + phase) > 0
    d.settings.zero_crossing = zero_crossing
    t_f = 4 * math.pi
    exact = 2 * math.pi  # half of the time
    res = simulate(d, t_f, solver="RK45")
    tol = 1e-6 if zero_crossing else 0.6  # without event location the gate is decided every max_step
    assert res["task/i"][-1] == pytest.approx(exact, abs=tol)


def test_constant_inputs_do_not_run_a_disabled_subsystem() -> None:
    enable = Step(time=0.5, initial=0.0, final=1.0)
    constant = simulate(_enabled(Constant(value=5.0), enable), 1.0, solver="euler", step=0.1)
    varying = simulate(
        _enabled(Step(time=0.0, initial=5.0, final=5.0), Step(time=0.5, initial=0.0, final=1.0)),
        1.0,
        solver="euler",
        step=0.1,
    )
    np.testing.assert_array_equal(constant["task/g"], varying["task/g"])
    assert constant["task/g"][0] == 0.0  # held at its initial output until enabled at t = 0.5
    assert constant["task/g"][-1] == 10.0


def test_coverage_ignores_blocks_of_a_disabled_subsystem() -> None:
    inner = Diagram()
    u = inner.add(Inport(port=1), name="u")
    inner.chain(u, inner.add(Saturation(upper=1.0, lower=-1.0), name="sat"), inner.add(Outport(port=1), name="y"))
    d = Diagram()
    ss = d.add(Subsystem(system=inner, enable_port=True), name="task")
    d.connect(d.add(Constant(value=5.0)), (ss, 0))
    d.connect(d.add(Constant(value=0.0)), (ss, 1))  # never enabled
    d.connect((ss, 0), d.add(Scope()))
    cov = Coverage()
    simulate(d, 1.0, solver="euler", step=0.1, coverage=cov)
    assert cov.covered == 0
