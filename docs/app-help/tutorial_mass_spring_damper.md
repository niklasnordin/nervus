# Tutorial: Position control of a mass-spring-damper

| | |
|---|---|
| **You will learn** | what the P, I and D terms do, natural frequency and damping ratio, steady-state error, actuator limits and anti-windup |
| **Before this** | *Your first model: a cruise control* |
| **Level and time** | beginner, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/mass_spring_damper_pid.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Control engineering ▸ Classic lab experiments (*all tutorials*) |

`examples/mass_spring_damper_pid.py` is the first experiment of many control lab courses: a mass on a spring,
pushed by a force actuator, and a PID controller that moves it to a reference position. You will design the
controller in the classic order, P, then PD, then PID, and see what each term does.

```bash
uv run python examples/mass_spring_damper_pid.py
```

## The plant

A mass $m$ on a spring $k$ with viscous damping $c$, pushed by a force $F$:

$$ m \ddot x + c \dot x + k x = F $$

With $m = 1$ kg, $c = 0.5$ N·s/m and $k = 2$ N/m the open plant has the natural frequency
$\omega_n = \sqrt{k/m} = 1.41$ rad/s and the damping ratio $\zeta = c / (2\sqrt{km}) = 0.18$: left alone it
oscillates for a long time. In the model the equation is two Integrators (acceleration → velocity → position)
with the spring and damper as feedback Gains, the way it is drawn in every textbook. The actuator is limited to
±50 N (the PID block's *upper* and *lower*).

## Step 1: P control

In the **Model Workspace** set `Ki` = 0 and `Kd` = 0, then press **▶** and open the scope. A P controller
acts like an extra spring: the closed loop has the stiffness $k + K_p$, so

$$ x_\infty = \frac{K_p}{k + K_p} = \frac{40}{42} = 0.952 \text{ m}, \qquad
   \omega_n = \sqrt{\frac{k + K_p}{m}} = 6.5 \text{ rad/s}, \qquad \zeta = \frac{c}{2\sqrt{m(k+K_p)}} = 0.04 $$

The mass shoots to 1.8 m and rings for many seconds around 0.952 m. A stiffer controller makes the error
smaller but the oscillation worse: P alone cannot do both.

## Step 2: PD control

Set `Kd` = 8. The derivative term acts like an extra damper: $\zeta = (c + K_d) / (2\sqrt{m(k+K_p)}) = 0.66$.
The overshoot drops to 5.5 % and the mass is settled within 1.4 s, but still at 0.952 m: the D term does
nothing at standstill, so the steady-state error stays. The step makes the D term kick: the force jumps to the
50 N limit for a moment.

## Step 3: PID control

Set `Ki` = 30. The integral term keeps pushing while there is any error, so the mass ends exactly at 1 m.
The price is a little more overshoot (12 %); the response settles within 2 % in 2.3 s.

![](../images/blocks/lab_mass_spring_damper.png)

| Controller | Highest position | Final position | Settled (2 %) |
|---|---|---|---|
| P (Kp = 40) | 1.80 m | 0.952 m (still ringing at 10 s) | > 10 s |
| PD (Kd = 8) | 1.00 m | 0.952 m | 1.4 s |
| PID (Ki = 30) | 1.12 m | 1.000 m | 2.3 s |

The controller uses **clamping anti-windup**: while the force is at its limit, the integrator stops. With
*back-calculation* anti-windup and `Ki` = 0, the integrator would still collect the saturation correction and
leave a permanent offset (Simulink behaves the same way), so clamping is the right choice for a lab where the I
term is switched off and on.

## Check it by hand, then with the tuner

The PD loop's characteristic equation is $m s^2 + (c + K_d) s + (k + K_p) = 0$, that is
$s^2 + 8.5 s + 42 = 0$, with the poles $-4.25 \pm 4.9j$: $\omega_n = 6.5$ rad/s and $\zeta = 0.66$, as in step 2.
For a systematic design open **Analysis ▸ Linear Analysis ▸ PID Tuning**, choose the block `pid` and a phase
margin, and press **Tune**: it designs the gains from the plant the controller sees (see
*Linear analysis*). Copy its gains into the workspace and compare its step response with
yours in the Data Inspector: a large phase margin gives a calm but slower response.

## Experiments

| Try | What you will see |
|---|---|
| `Kp` = 100 (PID) | faster (settled in 1.3 s) but 20 % overshoot |
| `Ki` = 100 | the error disappears faster, but the overshoot grows to 33 % |
| `m` = 3 (a heavier mass, same gains) | 42 % overshoot: gains are tuned for one plant |
| `Kd` = 20 | more damping in the PD part, but the I term still overshoots (13 %) and settling takes longer (4.5 s) |

## Next

Next in this topic: *DC motor speed and position control*. Or pick another one from the *tutorial overview*.
