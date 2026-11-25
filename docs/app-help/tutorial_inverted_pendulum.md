# Tutorial: Balancing an inverted pendulum with state feedback

| | |
|---|---|
| **You will learn** | an unstable plant, state-space models, LQR state feedback, non-minimum-phase behaviour, redesigning a gain in the Console |
| **Before this** | *DC motor speed and position control* |
| **Level and time** | advanced, about 45 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/inverted_pendulum.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Control engineering ▸ Classic lab experiments (*all tutorials*) |

`examples/inverted_pendulum.py` is the classic advanced control lab: a pendulum hinged on a cart, balanced
upright by moving the cart. Upright is unstable, a single PID loop cannot hold the pendulum and the cart at the
same time, so the controller feeds back all four states with a gain designed by **LQR** (linear quadratic
regulator). The dimensions are those of a common lab rig.

```bash
uv run python examples/inverted_pendulum.py
```

## The plant

A cart of 0.94 kg carries a pendulum rod (0.23 kg, 0.64 m) on a hinge; the motor pushes the cart with up to
±10 N. The state is $x = [\,x_{cart},\ \theta,\ \dot x_{cart},\ \dot\theta\,]$ with $\theta = 0$ upright. The
model uses the full nonlinear equations of motion (the **cart_pole** Python Function block; its integrator
holds the four states):

$$ (M + m)\ddot x + m l (\ddot\theta\cos\theta - \dot\theta^2 \sin\theta) = F - b\dot x, \qquad
   (J + m l^2)\ddot\theta + m l \ddot x \cos\theta = m g l \sin\theta - B\dot\theta $$

Linearized at upright, $\dot x = A x + B F$ has the poles 0, −3.8, −6.7 and **+4.9 rad/s**: the pendulum falls
with a time constant of 0.2 s. Set `K_lqr` to `[0, 0, 0, 0]` and run: it falls past 0.5 rad (29°) within 0.6 s.

## The controller: LQR state feedback

State feedback $F = -K(x - x_{ref})$ places all four poles at once. LQR chooses the $K$ that minimizes

$$ J = \int_0^\infty \left( x^T Q x + r F^2 \right) dt, \qquad Q = \mathrm{diag}(q_x, q_\theta, 0, 0) $$

so $q_x$ and $q_\theta$ say how much cart and angle errors cost, and $r$ how much force costs.
`lqr_gain()` in the example solves the Riccati equation (with SciPy) for the linear model; with $q_x = 50$,
$q_\theta = 100$, $r = 1$ it gives $K = [-7.1,\ -57,\ -15,\ -11]$, stored in the workspace as `K_lqr` and used by
the **K** gain block (matrix multiplication). The sensors are sampled every 2 ms.

![](../images/blocks/lab_inverted_pendulum.png)

Run the model: the controller catches the 3° start, rejects the tap at 3 s (5 N for 0.1 s) and moves the cart to
0.3 m at 5 s, settling within 2 % in 3.6 s, with the pendulum never more than 3° from upright and at most 4.3 N.

Look at the cart at 5 s, when the reference jumps to +0.3 m: it first moves **backwards** (from −17 to
−26 mm, it was still recovering from the tap) and the pendulum leans 2° towards +x before the cart follows. To move right, the pendulum must first lean
right, and the only way to tip it is to pull the cart left from under it. This **non-minimum-phase** behaviour
(a zero in the right half plane) is why balancing systems react "backwards", and why they cannot be made
arbitrarily fast.

## Redesign the gain in the Console

`K_lqr` is a workspace variable, so a new design is one line in the **Console** tab:

```python
import sys

sys.path.insert(0, "examples")
from inverted_pendulum import lqr_gain

model.workspace["K_lqr"] = lqr_gain(q_x=200)
```

Then press **▶** and compare the runs in the Data Inspector.

## Experiments

| Try | What you will see |
|---|---|
| `lqr_gain(q_x=200)` | the cart is more important: it reaches 0.3 m in 2.1 s instead of 3.6 s, the pendulum leans up to 3.8° |
| `lqr_gain(r=10)` | force is expensive: gentle and slow, the cart has only reached 0.25 m after 5 s |
| `F_max` = 3 | a weaker motor still balances (it saturates for a moment during the tap) |
| `F_max` = 1.5 | too weak for the tap: the pendulum falls at 3.7 s |
| `m_pend` = 0.35 | the plant is heavier than the design model, and the LQR gain is robust enough to balance it anyway |

## Next

That was the last tutorial on control engineering. Continue with powertrain and engine control: *Engine operating modes*, or pick another one from the *tutorial overview*.
