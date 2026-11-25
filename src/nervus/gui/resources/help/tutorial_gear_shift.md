# Tutorial: Gear-shift logic

| | |
|---|---|
| **You will learn** | an automatic gearbox's shift schedule, hysteresis, a State Chart in its own ECU task, kickdown and hills, fuel consumption per gear |
| **Before this** | [Your first model: a cruise control](tutorial_first_model.md), [A car on a drive cycle](tutorial_drive_cycle.md); read [State machines](statecharts.md) first |
| **Level and time** | intermediate, about 60 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/gear_shift.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Powertrain and engine control ▸ Transmission and driveline ([all tutorials](tutorials.md)) |

This tutorial follows `examples/gear_shift.py`: the shift logic of an automatic gearbox, running in its own
ECU (the TCU, transmission control unit), and a car to try it on. You will see how a shift schedule works,
why it needs hysteresis, and how it reacts to the driver and to a hill.

```bash
uv run python examples/gear_shift.py      # simulates, saves gear_shift.nervus, runs the tests
uv run nervus examples/gear_shift.nervus  # open it in the editor
```

## The model

The pedal and the road grade come from **From Workspace** blocks. The **tcu** subsystem reads the vehicle
speed and the pedal and decides the gear; the **car** subsystem turns the gear and the pedal into speed and
engine speed.

## The shift schedule

A TCU decides the gear from two maps over (gear, pedal position):

| Upshift speed [km/h] | pedal 0 % | 50 % | 100 % |
|---|---|---|---|
| 1 → 2 | 15 | 25 | 40 |
| 2 → 3 | 30 | 45 | 70 |
| 3 → 4 | 45 | 65 | 100 |
| 4 → 5 | 60 | 85 | 130 |

| Downshift speed [km/h] | pedal 0 % | 50 % | 100 % |
|---|---|---|---|
| 2 → 1 | 10 | 15 | 30 |
| 3 → 2 | 22 | 32 | 55 |
| 4 → 3 | 35 | 50 | 85 |
| 5 → 4 | 50 | 68 | 110 |

With more pedal both thresholds rise. The engine revs further before each upshift, so there is more power
when the driver asks for it. With a light foot the gearbox shifts up early, and the engine runs at low speed,
where it uses less fuel. Pressing the pedal fully at speed raises the downshift speed above the current speed,
and the gearbox drops a gear or two: **kickdown**.

In the TCU, two **2-D Lookup Tables** (`up_map`, `down_map`) read the maps for the current gear and the pedal.
The current gear comes from the chart through a **Unit Delay**: the maps for this step use the gear of the
last step, which also breaks the loop between the chart and the maps. The maps are workspace variables
(`upshift_map`, `downshift_map`), so they are calibration data in the generated C code and in the A2L file.

## The chart

```
input speed, up_speed, down_speed
output gear = 1, shifting = 0
param min_gear_time, shift_time

state g1
  entry: gear = 1; shifting = 0
state g2
  entry: gear = 2; shifting = 1
  during: shifting = 1 if t_in_state < shift_time else 0
...
transition g1 -> g2: speed > up_speed and after(min_gear_time)
transition g2 -> g3: speed > up_speed and after(min_gear_time)
transition g2 -> g1: speed < down_speed and after(min_gear_time)
...
```

One state per gear. Two things keep the gearbox from **hunting** (shifting up and down again and again):

1. **Hysteresis in the maps.** Every downshift speed is below the upshift speed of the gear below. After an
   upshift from 2nd to 3rd at 45 km/h (50 % pedal), the speed would have to drop to 32 km/h before the
   gearbox goes back to 2nd.
2. **A minimum time in each gear**, `after(min_gear_time)`: 100 samples of 10 ms = 1 s.

While a shift is in progress (`shift_time` = 0.3 s) the output `shifting` is 1 and the engine torque is cut.
A real gearbox does this to synchronise the speeds of the shafts.

The TCU is an atomic subsystem with a 10 ms sample time, so it is one ECU task. Generate its C code with
**Code → Generate C** on the selected `tcu` block, or:

```python
from nervus.codegen import generate_c

code = generate_c(d.block("tcu"))
```

## The car

**gearbox**: the ratio of the current gear times the final drive, $i = i_{gear}\,i_{final}$. The engine speed
follows from the wheel speed, $n = \frac{v}{r_{wheel}}\,i \cdot \frac{60}{2\pi}$ rpm. The clutch is locked,
except below idle speed, where it slips (`clutch_slip` takes the maximum of the two).

**engine**: the torque is the pedal fraction times the full-load torque at the current speed, minus the
friction torque. With the pedal released, the friction brakes the car (engine braking). While shifting, the
torque is cut to zero.

**body**: Newton's second law along the road, with aerodynamic drag, rolling resistance and the grade
$\alpha = \arctan(\text{grade}/100)$:

$$ m\,\frac{dv}{dt} = \frac{T\,i\,\eta}{r_{wheel}} - \tfrac{1}{2}\rho\,c_d A\,v^2 - m g\,c_{rr}\cos\alpha - m g \sin\alpha $$

## Fuel consumption: why light-pedal upshifts save fuel

The engine has a **brake-specific fuel consumption** map (BSFC, in g/kWh) over speed and torque. The fuel flow
is the map value times the power the engine delivers:

$$ \dot m_{fuel} = \frac{\mathrm{BSFC}(n, T)\cdot T\,\omega}{3.6\cdot 10^{6}} \quad [\text{g/s}] $$

The map is best (about 245 g/kWh, 33 % efficiency) at middle speed and high load, and poor at light load,
where the throttle losses dominate. Cruising at 80 km/h needs the same power in every gear, but in 5th the
engine runs at 2000 rpm with a high load, in 3rd at 3700 rpm with a light load. Over the cruise (12-22 s):

| | engine speed | fuel |
|---|---|---|
| 5th gear (the shift schedule) | 2040 rpm | 5.1 L/100 km |
| held in 3rd (`upshift_map` row 3 = 999) | 3720 rpm | 7.6 L/100 km |

Half as much fuel again, for the same speed. The model integrates the fuel (`fuel_g`) and the distance
(`distance`); the whole drive with its full-throttle launch, kickdown and hill uses about 14 L/100 km.

## Results

![](figures/gear_shift.png)

- **Full-throttle launch** (0–9 s): the upshifts come at 40 and 70 km/h, at almost 5000 rpm. Each upshift
  drops the engine speed, and the speed curve has a short flat step: the torque cut.
- **20 % pedal** (9–22 s): the thresholds fall below the current speed, so the gearbox shifts straight up to
  5th and the engine settles at about 2100 rpm.
- **Kickdown** (22 s): at 100 % pedal the downshift speeds of 5th (110 km/h) and 4th (85 km/h) are above the
  76 km/h of the car. The gearbox drops to 4th, and one second later (the minimum gear time) to 3rd. The
  engine jumps to 3700 rpm and the car accelerates.
- **A 10 % hill at 60 % pedal** (from 28 s): the car slows down and, below 76 km/h, the gearbox drops to 4th.

The four test cases in the **Test Manager** check each phase. The test in `tests/test_examples.py` also
checks that no gear is held for less than a second, and that the C code of the TCU gives exactly the same
gears as the simulation.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

| Try | What you will see |
|---|---|
| `min_gear_time = 0` | the kickdown goes from 5th to 3rd within 0.07 s, and the launch shifts 4 → 5 after only 0.2 s in 4th |
| `shift_time = 1.0` | longer torque cuts: the car reaches 71 instead of 80 km/h at 9 s |
| `upshift_map` 1 → 2 at full pedal = 60 (first row: 15, 25, 60) | 1st gear tops out at 55 km/h, where the engine reaches 6400 rpm and its full-load torque runs out: the car is stuck in 1st until the pedal is released at 9 s. Shift maps must fit the gear ratios |
| `mass = 2500` | the heavier car never reaches 5th: the kickdown drops one gear, and the hill pulls it down to 3rd |
| `gear_ratios = [4.0, 2.4, 1.6, 1.2, 1.0]` | shorter gears: 85 instead of 80 km/h at 9 s, but 2800 instead of 2100 rpm when cruising |

## Next

Next in this topic: [A truck on a mountain road](tutorial_truck.md). Or pick another one from the [tutorial overview](tutorials.md).
