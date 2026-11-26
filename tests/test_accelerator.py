"""Accelerated simulation: the model as compiled C gives what the Python simulation gives."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from nervus import Diagram, load, simulate
from nervus.codegen.sil import find_compiler
from nervus.core.errors import SimulationError
from nervus.core.settings import SimulationSettings

pytestmark = pytest.mark.skipif(find_compiler() is None, reason="needs a C compiler")
EXAMPLES = Path(__file__).parent.parent / "examples"


def both(d: Diagram, **kw: object):  # type: ignore[no-untyped-def]
    normal = simulate(d, **kw)  # type: ignore[arg-type]
    d.settings = replace(d.settings, mode="accelerated")
    try:
        fast = simulate(d, **kw)  # type: ignore[arg-type]
    finally:
        d.settings = replace(d.settings, mode="normal")
    assert "accelerated" in fast.stats.solver, fast.stats.warnings
    return normal, fast


def assert_same(a, b, rtol: float = 1e-9) -> None:  # type: ignore[no-untyped-def]
    assert np.array_equal(a.t, b.t)
    assert set(a.signals) == set(b.signals)
    for k in a.signals:
        x, y = a.signals[k], b.signals[k]
        assert np.allclose(x, y, rtol=rtol, atol=rtol, equal_nan=True), (k, np.max(np.abs(x - y)))


@pytest.mark.parametrize(
    "name", ["truck", "gear_shift", "abs_braking", "drive_cycle", "cruise_control", "fixed_point_sensor",
             "engine_comparison", "lambda_control", "traction_control", "fuel_film", "discrete_control"],
)  # fmt: skip
def test_examples_match(name: str) -> None:
    d = load(EXAMPLES / f"{name}.nervus")
    s = d.settings
    kw = {} if s.is_fixed_step else {"solver": "rk4"}
    if not s.is_fixed_step:
        span = s.t_final - s.t_start
        from nervus.codegen.accelerator import fundamental_step
        from nervus.sim.compiler import compile_diagram

        kw["step"] = fundamental_step(compile_diagram(d), s.max_step or span / 1000)
    a, b = both(d, **kw)
    assert_same(a, b)
    assert a.aliases == b.aliases


def test_continuous_blocks_and_sources() -> None:
    from nervus.blocks import (
        Clock,
        Constant,
        Derivative,
        FromWorkspace,
        Integrator,
        Pulse,
        Ramp,
        Scope,
        Sine,
        StateSpace,
        Step,
        TransferFunction,
    )  # fmt: skip
    from nervus.blocks.control import PID

    d = Diagram()
    srcs = [
        d.add(Sine(amplitude=2.0, frequency=3.0, phase=0.4, bias=0.1), name="sine"),
        d.add(Pulse(amplitude=1.5, period=0.3, duty=40.0, delay=0.05), name="pulse"),
        d.add(Ramp(slope=0.5, start_time=0.2, initial=-1.0), name="ramp"),
        d.add(Step(time=0.5, initial=[0.0, 1.0], final=[2.0, 3.0]), name="step"),
        d.add(FromWorkspace(time=[0, 0.4, 0.8], data=[[0, 1], [2, -1], [1, 0]], interpolation="hold"), name="hold"),
        d.add(FromWorkspace(time=[0, 0.4, 0.8], data=[0.0, 2.0, 1.0]), name="lin"),
        d.add(Clock(), name="clock"),
    ]
    sine = srcs[0]
    lim = d.add(Integrator(upper=0.3, lower=-0.2, saturation_port=True), name="limited")
    rst = d.add(Integrator(initial=0.5, external_reset="rising"), name="reset")
    lvl = d.add(Integrator(external_reset="level", initial_source="external"), name="level")
    tf = d.add(TransferFunction(numerator=[1.0, 2.0], denominator=[0.1, 1.0, 4.0]), name="tf")
    ss = d.add(StateSpace(A=[[-1.0, 2.0], [0.0, -3.0]], B=[[1.0], [1.0]], C=[[1.0, 0.0], [0.5, 1.0]],
                          D=[[0.0], [0.2]], initial=[0.1, -0.1]), name="ss")  # fmt: skip
    der = d.add(Derivative(time_constant=0.01), name="der")
    pid = d.add(PID(P=2.0, I=5.0, D=0.1, N=50.0, upper=1.0, lower=-1.0, anti_windup="clamping"), name="pid")
    d.connect(sine, (lim, 0))
    d.connect(sine, (rst, 0))
    d.connect(srcs[1], (rst, 1))
    d.connect(srcs[2], (lvl, 0))
    d.connect(srcs[1], (lvl, 1))
    d.connect(d.add(Constant(value=0.7), name="x0"), (lvl, 2))
    for blk in (tf, ss, der, pid):
        d.connect(sine, blk)
    scope = d.add(Scope(num_inputs=1))
    d.connect(srcs[4], scope)
    a, b = both(d, t_final=2.0, solver="rk4", step=0.001)
    assert_same(a, b)
    a, b = both(d, t_final=2.0, solver="euler", step=0.001)
    assert_same(a, b)


PYTHON_CODE = '''\
def f(t, a, b):
    """docstring"""
    m, k = p[0], p[-1]
    x = a * m + b ** 2 - (a % 0.7) + (b // 0.3)
    if a > 0.5 and not b < -0.2:
        y = math.atan2(a, b) + np.clip(x, -1, 1) + abs(b)
    elif 0.0 <= a <= 0.2 or b > 0.9:
        y = max(a, b, 0.1) - min(a, 0.3) + np.sign(b) * np.interp(a, [0, 0.5, 1], [1, -1, 2])
    else:
        y = math.exp(-a) if b > 0 else np.sqrt(abs(b)) + math.pi
    a += 1.0
    y *= k
    return y + a, float(int(x * 10))
'''


def test_python_function_translation() -> None:
    from nervus.blocks import FromWorkspace, Scope
    from nervus.blocks.user import PythonFunction

    rng = np.random.default_rng(3)
    t = np.linspace(0, 1, 201)
    d = Diagram()
    a = d.add(FromWorkspace(time=t, data=rng.uniform(-1, 1.2, t.size)), name="a")
    b = d.add(FromWorkspace(time=t, data=rng.uniform(-1, 1.2, t.size)), name="b")
    fn = d.add(PythonFunction(code=PYTHON_CODE, num_inputs=2, num_outputs=2, parameters=[1.5, 2.0]), name="fn")
    d.connect(a, (fn, 0))
    d.connect(b, (fn, 1))
    d.connect((fn, 0), d.add(Scope()))
    x, y = both(d, t_final=1.0, solver="rk4", step=0.001)
    assert_same(x, y, rtol=1e-12)


def test_input_signals_stop_and_assertion() -> None:
    from nervus.blocks import Assertion, CompareToConstant, Gain, Integrator, StopSimulation
    from nervus.blocks.ports import Inport, Outport
    from nervus.core.signals import Signal

    d = Diagram()
    u = d.add(Inport(), name="u")
    i = d.add(Integrator(), name="i")
    d.connect(u, i)
    g = d.add(Gain(gain=2.0), name="g")
    d.connect(g, d.add(Outport(), name="y"))
    d.connect(i, g)
    done = d.add(CompareToConstant(operator=">=", constant=1.0), name="done")
    d.connect(i, done)
    d.connect(done, d.add(StopSimulation(), name="stop"))
    sig = {"u": Signal.steps([(0, 1.0), (0.3, 3.0)])}
    a, b = both(d, t_final=5.0, solver="rk4", step=0.01, inputs=sig)
    assert_same(a, b)
    assert b.stats.stopped_early and b.t[-1] < 1.0
    d2 = Diagram()
    d2.connect(d2.add(Inport(), name="u"), d2.add(Assertion(), name="positive"))
    with pytest.raises(SimulationError, match="positive"):
        both(d2, t_final=1.0, solver="rk4", step=0.1, inputs={"u": Signal.steps([(0, 1.0), (0.5, 0.0)])})


def test_variable_step_models_run_with_rk4() -> None:
    d = load(EXAMPLES / "coolant_fan.nervus")
    a = simulate(d)
    d.settings = replace(d.settings, mode="accelerated")
    b = simulate(d)
    assert b.stats.solver.startswith("rk4 (accelerated")
    for key in ("coolant_temp:0", next(iter(a.signals))):
        if key in a.signals:  # the same course as the variable-step run
            assert np.allclose(np.interp(b.t, a.t, a.signals[key][:, 0]), b.signals[key][:, 0], rtol=0.02, atol=0.05)


def test_models_without_c_run_normally_and_say_why() -> None:
    d = load(EXAMPLES / "rc_filter.nervus")
    d.settings = replace(d.settings, mode="accelerated")
    res = simulate(d)
    assert "accelerated" not in res.stats.solver
    assert any("accelerated mode not used" in w and "no C code" in w for w in res.stats.warnings or [])


def test_python_code_that_cannot_be_translated() -> None:
    from nervus.blocks import Constant, Scope
    from nervus.blocks.user import PythonFunction

    d = Diagram()
    fn = d.add(PythonFunction(code="def f(t, u):\n    for k in range(3):\n        u += k\n    return u\n"), name="loop")
    d.connect(d.add(Constant(value=1.0)), fn)
    d.connect(fn, d.add(Scope()))
    d.settings = replace(d.settings, mode="accelerated")
    res = simulate(d, t_final=0.1, solver="rk4", step=0.01)
    assert res["loop"][-1] == 4.0
    assert any("line 2" in w and "For" in w for w in res.stats.warnings or [])


def test_mode_setting() -> None:
    assert "mode" not in SimulationSettings().to_dict()
    s = SimulationSettings.from_dict({"mode": "accelerated"})
    assert s.to_dict()["mode"] == "accelerated"
    with pytest.raises(ValueError, match="mode"):
        SimulationSettings(mode="fast").validate()
    assert math.isfinite(s.t_final)
