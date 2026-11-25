# Tutorial: Level control of coupled water tanks

| | |
|---|---|
| **You will learn** | a nonlinear process, linearization at an operating point, PI design for a second-order process, gain scheduling, actuator limits |
| **Before this** | *DC motor speed and position control* |
| **Level and time** | intermediate, about 40 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/coupled_tanks.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Control engineering ▸ Classic lab experiments (*all tutorials*) |

`examples/coupled_tanks.py` is the process-control experiment of many lab courses: a pump fills an upper tank,
the water runs through an orifice into a lower tank and out through a second orifice, and a PI controller holds
the level of the lower tank. Unlike the motor, this process is **nonlinear**: a controller designed at one
level behaves differently at another. The dimensions are those of a common lab rig.

```bash
uv run python examples/coupled_tanks.py
```

## The plant

Water flows out of a tank through an orifice at Torricelli's speed $\sqrt{2 g h}$, so the outflow grows with
the square root of the level:

$$ A \frac{dh_1}{dt} = K_{pump} V - a_1 \sqrt{2 g h_1}, \qquad
   A \frac{dh_2}{dt} = a_1 \sqrt{2 g h_1} - a_2 \sqrt{2 g h_2} $$

with the tank cross-section $A = 15.5$ cm², the orifices $a_1 = 0.32$ cm² and $a_2 = 0.18$ cm², and a pump that
delivers 3.3 cm³/s per volt (0 to 22 V). In the model each tank is an Integrator limited to 0-30 cm (the tank
height) with a square-root block for the outflow.

**Operating point**: for the lower tank at 15 cm, the outflow is $a_2\sqrt{2g \cdot 15} = 30.5$ cm³/s; the pump
needs 9.26 V for that, and the upper tank settles at 4.74 cm. Linearized around this point, each tank is a
first-order system with the time constant $\tau_i = \frac{A}{a_i}\sqrt{\frac{2 h_i}{g}}$: 4.8 s for the upper
tank and 15.2 s for the lower one. The steady-state gain from pump voltage to the lower level is
$\frac{dh_2}{dV} = \frac{K_{pump}\, q}{g\, a_2^2} = 3.2$ cm/V.

## Step 1: the open-loop step

Set `closed_loop` = 0 and `V_open` = 10.25 (1 V more than the equilibrium) and run 400 s. The lower level rises
from 15 to 18.4 cm: 3.4 cm/V, a bit more than the linear 3.2 cm/V, because the process is nonlinear. It takes
23 s to reach 63 % of the change: the two time constants in series (15.2 + 4.8 s) make an S-shaped response.

## Step 2: the PI controller

With `closed_loop` = 1 the PI controller drives the pump. The design cancels the slow lower tank: the integral
time $T_i = 15.2$ s equals its time constant, so the loop is
$L(s) = \frac{K_c \cdot 3.2}{T_i s (4.8 s + 1)}$. For a crossover of 0.1 rad/s (a closed loop about ten times
faster than the tank) $K_c = 0.1 \cdot 15.2 / 3.2 = 0.47$ V/cm, with a phase margin of about 65°.

![](../images/blocks/lab_coupled_tanks.png)

The reference steps to 17 cm at 50 s: the level follows with 0.12 cm overshoot and settles within 2 % in 44 s;
the step down to 13 cm takes 38 s.

## Step 3: the nonlinearity

At 250 s the reference drops to 5 cm, far from the design point. The controller still works, but it takes
62 s to settle, half as fast. The process gain $dh_2/dq$ is proportional to $\sqrt{h_2}$: at 5 cm it is only
58 % of the gain at 15 cm, so the loop crosses over at a lower frequency. Industrial controllers solve this
with **gain scheduling**: the gain is made a function of the operating point. Try `Kc` = 0.94 (twice the
gain): the step to 5 cm then settles in 30 s, at the price of more overshoot around 15 cm.

## Experiments

| Try | What you will see |
|---|---|
| `Kc` = 0.94 | twice the gain: 2.2 % overshoot at 17 cm, and the step to 5 cm settles in 30 s instead of 62 s |
| `Kc` = 0.235 | half the gain: no overshoot, but 52 s near 15 cm and 121 s at 5 cm |
| `Ti` = 5 | too much integral action: the level shoots to 18 cm and drops to 3.4 cm after the step to 5 cm |
| `Kc` = 5 | the pump runs into its limits (0 V on the way down); with *anti_windup* on `none` (block **pi** in Properties) the level undershoots to 3.2 cm instead of reaching 5 cm cleanly |

## Next

Next in this topic: *Balancing an inverted pendulum with state feedback*. Or pick another one from the *tutorial overview*.
