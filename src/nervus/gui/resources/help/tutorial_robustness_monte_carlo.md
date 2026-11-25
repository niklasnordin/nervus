# Tutorial: Robustness and tolerances with Monte Carlo

| | |
|---|---|
| **You will learn** | parameter spread in a vehicle range, Monte Carlo studies in the editor and in Python, custom metrics, why the average hides the failures |
| **Before this** | [Your first model: a cruise control](tutorial_first_model.md) |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/cruise_control.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Testing and verification ([all tutorials](tutorials.md)) |

A controller is tuned on one car, but it is built into thousands, and every one is different: the mass changes with
passengers and luggage, the drag with tyres and roof boxes. A **Monte Carlo** study runs the model many times with
random parameters to see the spread, and checks the requirement for every one. This tutorial does it for the cruise
control of [the first tutorial](tutorial_first_model.md).

```bash
uv run python examples/cruise_tolerance.py
```

## The question

The cruise control was tuned for 1500 kg. The model range weighs **1200 to 2000 kg** (a uniform distribution), and
the drag coefficient varies by about ±10 % (a **normal** distribution, mean 8 N/(km/h), standard deviation 0.8). The
requirement:

> After the step to 100 km/h, the speed is within ±1 km/h of the set speed within **20 s**, for every car.

## Step 1: in the editor

Open `examples/cruise_control.nervus` and choose **Analysis ▸ Parameter Sweep / Monte Carlo…** (Ctrl+Shift+W).
Choose **Monte Carlo**, add the parameters `m` = `uniform(1200, 2000)` and `c_drag` = `normal(8, 0.8)`, 200 runs,
signal `speed`, statistic `max`, and press **Run**. The *Signal* tab draws all 200 responses on top of each other:
a band instead of a line. The *Metric* tab shows the histogram of the highest speed: never more than 0.3 km/h above
the set speed. The table can be exported as CSV.

## Step 2: the settling time in Python

The settling time needs a little logic (the last time the speed was more than 1 km/h off), so the example computes it
as a **metric** with `nervus.sweep.monte_carlo`:

```python
from nervus.sweep import monte_carlo


def settling_time(res):
    v = res["speed"]
    off = np.where(np.abs(v - 100) > 1)[0]
    return res.t[off[-1]] - 1.0


table = monte_carlo(
    model,
    {"m": ("uniform", 1200, 2000), "c_drag": ("normal", 8, 0.8)},
    runs=200,
    seed=1,
    metrics={"settling_s": settling_time},
)
```

With the original `Ki` = 30 every car settles between 12.1 and 18.4 s: the requirement is met with margin. The
settling time follows the mass closely (correlation 0.94); the drag hardly matters.

![](figures/cruise_tolerance.png)

## Step 3: the trap of the average

Someone proposes `Ki` = 60 to make the cruise control faster. On the nominal car it is faster, and the **average**
settling time drops from 15.2 to 14.5 s. But the Monte Carlo study shows the spread getting wider: the heavy cars
now overshoot and need up to 22.5 s, and only **83 %** of the cars meet the requirement. A test on one nominal car
would have passed.

| `Ki` | settling time (range) | mean | cars meeting 20 s |
|---|---|---|---|
| 15 | 15.4-29.2 s | 22.0 s | 17 % |
| 30 (original) | 12.1-18.4 s | 15.2 s | 100 % |
| 45 | 11.1-17.3 s | 14.1 s | 100 % |
| 60 | 10.5-22.5 s | 14.5 s | 83 % |

`Ki` = 45 is faster for every car and still meets the requirement everywhere: that is the robust choice.

## Experiments

| Try | What you will see |
|---|---|
| `m` = `uniform(1200, 2600)` (a van version) | the heaviest vans need longer; check whether 20 s still holds |
| `runs` = 20 | the statistics change from seed to seed: too few runs miss the rare cases |
| a third parameter, `Kp` = `normal(300, 30)` (a controller calibrated with a tolerance) | the spread widens a little |

See [Test cases and results](testing.md#parameter-sweeps-and-monte-carlo-runs) for sweeps over a grid and the
dialog's options.

## Next

Pick another topic from the [tutorial overview](tutorials.md), or build your own model.
