"""Scripting a model from Python: the script of the tutorial docs/tutorials/python-scripting.md.

It loads the cruise control of the first tutorial (``cruise_control.nervus``), runs it, changes its parameters,
sweeps a gain, runs the model's test cases, and builds a small model in code. Every step prints what the tutorial
quotes.

Run with ``uv run python examples/scripting.py`` (add ``--plot`` to see the speed curves).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

from nervus import Diagram, load, simulate
from nervus.blocks import Gain, Integrator, Scope, Step, Sum
from nervus.sweep import metric, sweep
from nervus.testing import run_tests

HERE = Path(__file__).parent


def overshoot(res: Any) -> float:
    """How far the speed goes above the set speed of 100 km/h."""
    return max(0.0, float(np.max(res["speed"])) - 100.0)


def build_first_order(tau: float = 0.5) -> Diagram:
    """A first-order lag, dy/dt = (u - y) / tau, built block by block."""
    d = Diagram("first_order")
    u = d.add(Step(time=0.0, final=1.0), name="u")
    err = d.add(Sum(signs="+-"), name="err")
    k = d.add(Gain(gain=1 / tau), name="1/tau")
    y = d.add(Integrator(), name="y")
    scope = d.add(Scope(num_inputs=2), name="scope")
    d.connect(u, err.in_port(0))
    d.connect(y, err.in_port(1))
    d.chain(err, k, y)
    d.connect(u, scope.in_port(0))
    d.connect(y, scope.in_port(1))
    return d


def main(show_plot: bool = False) -> dict[str, Any]:
    # 1. Load and run.
    model = load(HERE / "cruise_control.nervus")
    print("workspace:", {v.name: v.value for v in model.workspace.variables})
    print("blocks:", ", ".join(b.name for b in model.blocks))
    res = simulate(model)
    print(f"speed after {res.t[-1]:.0f} s: {res['speed'][-1]:.2f} km/h, max {res['speed'].max():.2f} km/h")

    # 2. Change a workspace value for one run only: the model keeps its own value.
    p_only = simulate(model, params={"Ki": 0.0})
    print(f"Ki = 0 for one run: speed after 40 s {p_only['speed'][-1]:.2f} km/h; "
          f"the model still has Ki = {model.workspace['Ki']:g}")  # fmt: skip

    # ... or change the model itself: a block parameter, then save it under a new name.
    model.block("cruise_control").set_param("anti_windup", "none")
    windup = simulate(model)
    print(f"without anti-windup: max {windup['speed'].max():.2f} km/h")
    model.block("cruise_control").set_param("anti_windup", "clamping")

    # 3. Sweep the proportional gain.
    table = sweep(model, {"Kp": [100.0, 300.0, 1000.0]},
                  metrics={"overshoot": overshoot, "max_speed": metric("speed", "max")})  # fmt: skip
    for row in table.rows:
        print(f"Kp = {row['Kp']:6.0f}: overshoot {row['overshoot']:.2f} km/h")

    # 4. The model's own test cases.
    results = run_tests(model)
    for r in results:
        print(r.summary())

    # 5. A model built in code.
    fo = simulate(build_first_order(), t_final=3.0)
    print(f"first-order lag: y(3 s) = {fo['y'][-1]:.4f} (exact {1 - np.exp(-6):.4f})")

    if show_plot:
        import matplotlib.pyplot as plt

        for row in sweep(model, {"Kp": [100.0, 300.0, 1000.0]}, keep_results=True).rows:
            plt.plot(row["result"].t, row["result"]["speed"], label=f"Kp = {row['Kp']:g}")
        plt.xlabel("time [s]")
        plt.ylabel("speed [km/h]")
        plt.legend()
        plt.show()
    return {"base": res, "p_only": p_only, "windup": windup, "sweep": table, "tests": results, "first_order": fo}


if __name__ == "__main__":
    main(show_plot="--plot" in sys.argv)
