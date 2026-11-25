# Tutorial: Scripting and testing models from Python

| | |
|---|---|
| **You will learn** | loading and running a model from a script, changing parameters for one run or for good, parameter sweeps, running a model's test cases in pytest, building a model in code |
| **Before this** | *Your first model: a cruise control* |
| **Level and time** | beginner, about 25 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/cruise_control.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Getting started (*all tutorials*) |

Everything the editor does, a Python script can do too: load a model, change its parameters, run it, sweep a
gain, check the results. Scripts make studies repeatable, and they put models into CI, where every change is
tested. We take the cruise control of the *first tutorial* and drive it from Python.

The whole script is `examples/scripting.py`; it prints every number quoted here:

```bash
uv run python examples/scripting.py          # add --plot to see the speed curves
```

You can type the same lines into the editor's **Console** tab, where the open model is already loaded as
`model`.

## Step 1: load and run a model

```python
from nervus import load, simulate

model = load("examples/cruise_control.nervus")
print({v.name: v.value for v in model.workspace.variables})
# {'m': 1500.0, 'c_drag': 8.0, 'Kp': 300.0, 'Ki': 30.0}

res = simulate(model)
print(res["speed"][-1], res["speed"].max())  # 99.97 99.97
```

`simulate` returns a `SimResult`. `res.t` is the time vector; `res["speed"]` is the output of the block named
`speed`, as a NumPy array with one value per time step. A block with several outputs is `res["block:1"]`, and a
named wire is reached by its signal name. `res.names()` lists them all.

The stop time, the solver and the step come from the model's settings, as in the editor. Override them per run:
`simulate(model, t_final=60.0, solver="rk4", step=0.01)`.

## Step 2: change parameters

There are two ways, and the difference matters.

**For one run only**, pass workspace values with `params`. The model is not changed:

```python
p_only = simulate(model, params={"Ki": 0.0})
print(p_only["speed"][-1])  # 97.40: P control alone leaves a steady error
print(model.workspace["Ki"])  # still 30
```

**In the model itself**, set the workspace value or a block parameter. This is what the Properties panel does:

```python
model.block("cruise_control").set_param("anti_windup", "none")
print(simulate(model)["speed"].max())  # 138.57 km/h: integrator windup
model.workspace["Kp"] = 400.0
```

Block parameters accept the same expressions as in the editor, e.g. `set_param("gain", "3.6 / m")`. Save the
changed model with `save(model, "my_cruise.nervus")`.

> **Prefer `params` in studies:** A study that changes the model in place has to remember to change it back. With `params`, every run starts from the saved model, so runs cannot leak into each other.

## Step 3: sweep a gain

`nervus.sweep` runs a model once per parameter combination and reduces each run to a few numbers, the
**metrics**:

```python
from nervus.sweep import metric, sweep


def overshoot(res):
    return max(0.0, res["speed"].max() - 100.0)


table = sweep(
    model, {"Kp": [100.0, 300.0, 1000.0]}, metrics={"overshoot": overshoot, "max_speed": metric("speed", "max")}
)
for row in table.rows:
    print(row["Kp"], row["overshoot"])
```

| Kp | Overshoot |
|---|---|
| 100 | 9.73 km/h |
| 300 | 0 |
| 1000 | 0 |

A *smaller* proportional gain gives *more* overshoot here. With `Ki` fixed, a small `Kp` leaves the
integral term in charge, and the integral overshoots. Sweeps like this one find such surprises quickly.

`table.to_csv("sweep.csv")` saves the table. Pass several variables to get every combination, and use
`monte_carlo` for random tolerances (see *Robustness and tolerances with Monte Carlo*).
Pass `keep_results=True` to keep the full results for plotting:

```python
import matplotlib.pyplot as plt

for row in sweep(model, {"Kp": [100.0, 300.0, 1000.0]}, keep_results=True).rows:
    plt.plot(row["result"].t, row["result"]["speed"], label=f"Kp = {row['Kp']:g}")
plt.legend()
plt.show()
```

## Step 4: run the model's test cases

The test cases you see in the editor's **Test Manager** are saved with the model. Run them from Python:

```python
from nervus.testing import run_tests

for r in run_tests(model):
    print(r.summary())
```

```text
PASS  reaches the set speed without overshoot
    ✓ -inf ≤ speed ≤ 101.0: min 0, max 99.9738
    ✓ 99.0 ≤ speed ≤ 101.0 for 20 ≤ t ≤ end: min 99.7148, max 99.9738
```

That makes a model a **regression test**. Put this in `tests/test_cruise.py` and pytest runs it in CI on every
push:

```python
from pathlib import Path

import pytest

from nervus import load
from nervus.testing import run_tests

MODEL = load(Path(__file__).parent.parent / "examples" / "cruise_control.nervus")


@pytest.mark.parametrize("case", MODEL.tests, ids=lambda c: c.name)
def test_model(case):
    (result,) = run_tests(MODEL, [case])
    assert result.passed, result.summary()
```

Each test case becomes one pytest test, and a failure prints which check failed and by how much. Plain asserts
on a result work too: `assert simulate(model)["speed"].max() < 101`.

## Step 5: build a model in code

Models can also be built without the editor. That suits generated models, such as a filter bank with one block
per frequency, and small models inside unit tests. A first-order lag, $\dot y = (u - y)/\tau$:

```python
from nervus import Diagram, simulate
from nervus.blocks import Gain, Integrator, Scope, Step, Sum

d = Diagram("first_order")
u = d.add(Step(time=0.0, final=1.0), name="u")
err = d.add(Sum(signs="+-"), name="err")
k = d.add(Gain(gain=1 / 0.5), name="1/tau")
y = d.add(Integrator(), name="y")
d.connect(u, err.in_port(0))
d.connect(y, err.in_port(1))
d.chain(err, k, y)  # err -> 1/tau -> y

res = simulate(d, t_final=3.0)
print(res["y"][-1])  # 0.9975 = 1 - exp(-6)
```

`d.add` places a block, `d.connect` wires an output to an input, and `d.chain` wires a row of single-port blocks.
`save(d, "first_order.nervus")` writes a file the editor opens. Blocks get no position in code, and the editor
lays them out when it opens the file. Every example in `examples/` is built this way, so they are a good
reference: `examples/cruise_control.py` builds the model of the first tutorial, test case included.

The block classes and their parameters are listed in the *block reference*. In the
Console, `help(Gain)` shows the same text.

## Experiments

| Try | What you will see |
|---|---|
| sweep `Ki` over `[0, 10, 30, 100]` with `Kp` = 300 | 0 leaves a steady error of 2.6 km/h; 10 is still 0.8 km/h short after 40 s; 100 overshoots to 101.6 km/h |
| `model.workspace["m"] = 2500.0`, then `run_tests(model)` | a heavier car is still at 95.7 km/h after 20 s: the test case fails, and the summary says where |
| `res.to_csv("run.csv")`, then open the file | one column per signal, ready for a spreadsheet |
| in pytest, change the check to `upper: 99.5` | the test fails, and the message shows the maximum speed that broke it |

## Next

That was the last tutorial on getting started. Continue with control engineering: *Position control of a mass-spring-damper*, or pick another one from the *tutorial overview*.
