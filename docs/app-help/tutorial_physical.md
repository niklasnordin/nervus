# Tutorial: Starter motor and electronic throttle

| | |
|---|---|
| **You will learn** | physical modelling (circuits, shafts, springs connected by links instead of signals): cranking an engine on a weak battery, a drive-by-wire throttle servo |
| **Before this** | [Your first model: a cruise control](tutorial_first_model.md); read [Physical modelling](physical.md) first |
| **Level and time** | intermediate, about 45 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/starter_motor.nervus`, `examples/electronic_throttle.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Electrics and electronics ▸ Vehicle electrical system ([all tutorials](tutorials.md)) |

Two physical models that mix circuits, mechanics and control: cranking an engine with the starter motor, and
the drive-by-wire throttle. Both are built from physical blocks (see [Physical modelling](physical.md)).

```bash
uv run python examples/starter_motor.py
uv run python examples/electronic_throttle.py
```

![](../images/blocks/physical_models.png)

## Cranking: why ECUs must survive 8 V

The key switches the **starter relay**; the relay connects the **starter motor** to the **battery**. At standstill
the motor has no back-EMF, so the current is limited only by the resistances of the battery (10 mΩ), the motor
(20 mΩ) and the contact:

$$ I_0 \approx \frac{12.6\ \text{V}}{0.010 + 0.020 + 0.002\ \Omega} \approx 390\ \text{A} $$

The simulation peaks at 378 A (the inductance and the first movement take a little off), and the battery voltage
falls to 9 V: $12.6 - 378 \cdot 0.010$. With a cold, weak battery it falls much further, which is why ECUs and
sensors are specified to keep working down to 6-8 V while cranking.

The motor drives the flywheel through the pinion (12:1). Each compression stroke resists, so speed, current and
voltage ripple twice per revolution (four cylinders). The engine settles at about 300 rpm, with 160 A from the
battery. The rotor's inertia is lumped with the crankshaft's ($J_{engine} + 12^2 J_{rotor}$): a stiff gear
between two separate inertias has no unique solution.

## Electronic throttle: a position servo

The ECU reads the throttle position sensor every millisecond and sets the H-bridge's duty cycle; the motor turns
the plate through a 20:1 gear against a return spring, whose preload closes the throttle if the motor is off
(the limp-home safety requirement). Signal blocks (the ECU) and physical blocks (battery, H-bridge, motor,
gear, spring) are linked through the H-bridge's duty input and the angle sensor's output.

The controller was designed with **Linear Analysis → PID Tuning**: it opens the loop at the PID block,
linearizes the plant from the duty cycle to the angle through the motor, gear and spring (third order, after
the battery's charge and the angle sensor's integrator are cancelled), and shapes the loop for 60° phase margin
at 100 rad/s:

| | P | I | D |
|---|---|---|---|
| tuned | 0.081 | 2.05 | 0.0008 |
| in the model | 0.08 | 2.0 | 0.0008 |

Small steps follow the target closely. A big step (5° → 60°) saturates the duty cycle at 100 % for the first
40 ms: the plate reaches 90 % in 45 ms and overshoots by 6.6° before the integral (with clamping anti-windup)
recovers. A feed-forward of the spring torque helps the integral at part throttle, where the error goes to zero
within 0.15 s. The ECU subsystem generates C code (`generate_c(d.block("ECU"))`), which the tests check back to
back against the simulation.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

| Try | What you will see |
|---|---|
| starter: battery `R_internal` = 0.03 | the battery collapses to 4.5 V while cranking: an ECU without enough reserve resets |
| throttle: `Kd_etc` = 0 | 10° of overshoot and 0.25 s to settle: the derivative damps the motor |
| throttle: `R_motor` = 3 | a weaker motor: 56 ms instead of 45 ms to 90 %, and more overshoot |

## Next

Next in this topic: [The 12 V charging system and energy management](tutorial_charging_system.md). Or pick another one from the [tutorial overview](tutorials.md).
