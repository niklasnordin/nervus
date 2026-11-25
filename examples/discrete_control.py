"""From continuous to discrete: the models of the tutorial docs/tutorials/discrete-control.md.

``discrete_control.nervus``: the speed of a DC motor (a first-order lag, K / (tau·s + 1)) under PI control, twice
in one model: once with a continuous PID block, once as an ECU runs it, with the speed sampled by a Zero-Order
Hold and a Discrete PID every *T* seconds. The PI gains cancel the motor's pole, so the continuous loop is a clean
first-order response with the time constant ``tau_cl``; everything the discrete loop does differently comes from
sampling.

``aliasing.nervus``: a 45 Hz vibration sampled at 50 Hz looks like a slow 5 Hz wave.

Run with ``uv run python examples/discrete_control.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from nervus import Diagram, SimResult, save, simulate
from nervus.blocks import PID, DiscretePID, Scope, Sine, Step, Sum, TransferFunction, ZeroOrderHold
from nervus.core import Check, TestCase, Variable
from nervus.sweep import sweep

HERE = Path(__file__).parent

POSITIONS = {
    "set_speed": (0, 60),
    "error_c": (160, 0), "pi_continuous": (300, 0), "motor_c": (470, 0),
    "error_d": (160, 160), "pi_discrete": (300, 160), "motor_d": (470, 160), "sensor": (300, 270),
    "scope": (680, 60),
}  # fmt: skip

SAMPLE_TIMES = [0.001, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05]


def build(sample_time: float = 0.01) -> Diagram:
    d = Diagram("discrete_control")
    d.settings.t_final = 0.4
    d.settings.max_step = 1e-3  # smooth curves between the controller samples
    ws = d.workspace
    ws.add(Variable("K", 2.0, "Motor gain: speed per volt", "rad/s/V"))
    ws.add(Variable("tau", 0.1, "Motor time constant", "s"))
    ws.add(Variable("tau_cl", 0.02, "Wanted closed-loop time constant", "s"))
    ws.add(Variable("Kp", "tau / (K * tau_cl)", "Proportional gain", "V/(rad/s)"))
    ws.add(Variable("Ki", "Kp / tau", "Integral gain (cancels the motor pole)", "V/(rad/s·s)"))
    ws.add(Variable("T", sample_time, "Controller sample time", "s"))

    sp = d.add(Step(time=0.05, initial=0.0, final=100.0), name="set_speed")
    # Continuous loop.
    err_c = d.add(Sum(signs="+-"), name="error_c")
    pi_c = d.add(PID(P="Kp", I="Ki"), name="pi_continuous")
    motor_c = d.add(TransferFunction(numerator="[K]", denominator="[tau, 1]"), name="motor_c")
    d.connect(sp, (err_c, 0))
    d.connect(motor_c, (err_c, 1))
    d.chain(err_c, pi_c, motor_c)
    # Discrete loop: the ECU reads the speed sensor and runs the controller every T seconds.
    err_d = d.add(Sum(signs="+-"), name="error_d")
    pi_d = d.add(DiscretePID(P="Kp", I="Ki", sample_time="T"), name="pi_discrete")
    motor_d = d.add(TransferFunction(numerator="[K]", denominator="[tau, 1]"), name="motor_d")
    sensor = d.add(ZeroOrderHold(sample_time="T"), name="sensor")
    d.connect(sp, (err_d, 0))
    d.connect(sensor, (err_d, 1))
    d.chain(err_d, pi_d, motor_d, sensor)

    scope = d.add(Scope(num_inputs=3), name="scope")
    d.connect(sp, (scope, 0))
    d.connect(motor_c, (scope, 1))
    d.connect(motor_d, (scope, 2))
    for name, (x, y) in POSITIONS.items():
        d.block(name).meta["position"] = [float(x), float(y)]
    d.block("sensor").meta["flipped"] = True
    d.tests = [
        TestCase(
            "the 10 ms controller tracks like the continuous one",
            "At T = 10 ms the overshoot stays below 15 % and the speed settles within 1 % in 0.2 s",
            stop_time=0.4,
            checks=[
                Check("bounds", "motor_d", {"upper": 115.0}),
                Check("bounds", "motor_d", {"lower": 99.0, "upper": 101.0, "t_from": 0.25}),
            ],
        )
    ]
    return d


def build_aliasing() -> Diagram:
    """A 45 Hz vibration on a sensor, sampled every 20 ms (50 Hz)."""
    d = Diagram("aliasing")
    d.settings.t_final = 0.6
    d.settings.max_step = 1e-4
    d.workspace.add(Variable("f_signal", 45.0, "Frequency of the vibration", "Hz"))
    d.workspace.add(Variable("T", 0.02, "Sample time of the ECU input", "s"))
    sig = d.add(Sine(amplitude=1.0, frequency="2 * pi * f_signal"), name="vibration")
    zoh = d.add(ZeroOrderHold(sample_time="T"), name="sampled")
    scope = d.add(Scope(num_inputs=2), name="scope")
    d.chain(sig, zoh)
    d.connect(sig, (scope, 0))
    d.connect(zoh, (scope, 1))
    for name, (x, y) in {"vibration": (0, 0), "sampled": (170, 0), "scope": (340, 0)}.items():
        d.block(name).meta["position"] = [float(x), float(y)]
    return d


def overshoot(res: SimResult) -> float:
    """Overshoot of the discrete loop in percent of the 100 rad/s step (inf if it diverged)."""
    y = res["motor_d"]
    return float(np.max(y) - 100.0) if np.all(np.isfinite(y)) and np.max(np.abs(y)) < 1e4 else float("inf")


def dominant_frequency(t: Any, y: Any) -> float:
    """The frequency (Hz) of the largest peak in the spectrum of a uniformly resampled signal."""
    tu = np.linspace(t[0], t[-1], 4096)
    yu = np.interp(tu, t, y)
    spec = np.abs(np.fft.rfft(yu - yu.mean()))
    return float(np.fft.rfftfreq(tu.size, tu[1] - tu[0])[np.argmax(spec)])


def main() -> dict[str, Any]:
    from nervus.testing import run_tests

    d = build()
    save(d, HERE / "discrete_control.nervus")
    for r in run_tests(d):
        print(r.summary())
    table = sweep(d, {"T": SAMPLE_TIMES}, metrics={"overshoot": overshoot})
    for row in table.rows:
        print(f"T = {row['T'] * 1000:4.0f} ms: overshoot {row['overshoot']:7.1f} %")

    a = build_aliasing()
    save(a, HERE / "aliasing.nervus")
    res = simulate(a)
    print(f"aliasing: input {dominant_frequency(res.t, res['vibration']):.1f} Hz, "
          f"sampled {dominant_frequency(res.t, res['sampled']):.1f} Hz")  # fmt: skip
    return {"sweep": table, "aliasing": res}


if __name__ == "__main__":
    main()
