# Tutorial: Ignition timing and knock control

| | |
|---|---|
| **You will learn** | spark advance from a speed/load map (2-D lookup table), knock and the knock limit, a knock controller that retards and recovers, noisy sensors |
| **Before this** | [Idle speed control](tutorial_idle.md), [Lambda control and fuel trims](tutorial_lambda.md) |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/ignition_knock.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Powertrain and engine control ▸ Engine management ([all tutorials](tutorials.md)) |

This tutorial follows `examples/ignition_knock.py`. It is the third part of the engine-control series, after
[idle speed control](tutorial_idle.md) and [lambda control](tutorial_lambda.md).

```bash
uv run python examples/ignition_knock.py
```

## Spark advance

The flame needs time to burn through the charge, so the spark fires before top dead centre: the **advance**,
in degrees of crank angle. The best torque comes at **MBT** (minimum advance for best torque); with less
advance the pressure peaks too late. The torque efficiency in this model falls off quadratically:

$$ \eta_{spark} = 1 - 0.0011\,(\alpha - \alpha_{MBT})^2 \qquad (1\ \%\ \text{at}\ 3°,\ 11\ \%\ \text{at}\ 10°) $$

The ECU reads the base advance from a **map over speed and load** (`advance_map`, calibration data): more
advance at high speed (less time per degree), less at high load.

## Knock

At high load, the last part of the charge can ignite by itself before the flame reaches it: **knock**, a sharp
pressure wave that damages pistons and head gaskets. It starts at the **knock limit**, which is lower with
low-octane fuel, hot intake air, a hot engine and high load. At full load the knock limit is below MBT, so the
engine is *knock limited*: the calibration keeps 2-3° below the knock limit of the reference fuel.

A **knock sensor** (an accelerometer on the engine block) picks up the vibration. In the model, the knock
intensity grows with the square of the advance beyond the limit, with cycle-to-cycle scatter (a Sensor Model
with noise).

## Knock control

The ECU (10 ms task) counts a knock event when the sensor exceeds a threshold, and for each event **retards**
the spark by 3°; the retard then recovers by 1°/s:

$$ r_{k+1} = \mathrm{clip}(r_k + 3°\cdot\text{knock}_k - 1°/\text{s}\cdot T_s,\ 0,\ 12°), \qquad \alpha = \alpha_{base}(n, \text{load}) - r $$

![](figures/ignition_knock.png)

At 4 s the car gets low-octane fuel, and the knock limit drops by 6° to 12°. The first combustions knock hard;
the ECU retards, and from then on the advance makes a **sawtooth** just around the new limit. It creeps up at
1°/s until a knock occurs, then steps back 3°. On average the spark is retarded by 5°, and the engine gives up
12 % torque (from 93 % to 81 %): the price of the cheaper fuel. Real ECUs do this per cylinder, and learn the retard into a map so the engine starts
at the right timing.

The ECU subsystem generates C code; the test compares it back to back with the simulation.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

| Try | What you will see |
|---|---|
| `knock_recover` = 3 | the advance creeps up faster: twice as many knock events, but less time far below the limit (82 % torque) |
| `knock_step` = 1 | smaller steps: the advance stays closer to the limit (3° average retard, 86 % torque) |
| `knock_threshold` = 0.2 | noise starts to count as knock: the ECU retards almost 12° and the torque falls to 57 % |

## Next

Next in this topic: [Wall wetting and learned fuel trims](tutorial_fuel_film.md). Or pick another one from the [tutorial overview](tutorials.md).
