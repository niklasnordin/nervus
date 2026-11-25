# Tutorial: Anti-lock braking (ABS)

| | |
|---|---|
| **You will learn** | braking slip and the tyre's friction peak, a quarter-car model, hydraulic pressure modulation, a threshold ABS as a State Chart, stopping distances on dry, wet and icy roads |
| **Before this** | [Tyre slip and traction control](tutorial_traction.md) |
| **Level and time** | intermediate, about 35 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/abs_braking.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Vehicle dynamics and energy ▸ Chassis control ([all tutorials](tutorials.md)) |

`examples/abs_braking.py` brakes a car as hard as possible from 100 km/h, with and without **ABS**. Without it the
wheels lock: the car slides further and cannot be steered. With it, an ECU modulates the brake pressure dozens of
times a second to keep the tyres at the slip where they grip best.

```bash
uv run python examples/abs_braking.py
```

## The model

A **quarter car**: one wheel carrying a quarter of a 1600 kg car (400 kg), with a 0.31 m tyre and a wheel inertia
of 1.2 kg·m². The tyre force depends on the **slip** between the car and the wheel,

$$ s = \frac{v - \omega r}{v} \qquad (0: \text{rolling freely},\ 1: \text{locked}) $$

through Pacejka's magic formula, as in [the traction control tutorial](tutorial_traction.md): on this tyre the
friction peaks at 15 % slip and a locked wheel keeps only 71 % of the peak. The car decelerates by the tyre force;
the wheel by the brake torque minus the tyre's torque.

The **brake** is a torque that the hydraulic unit changes at a limited rate (8000 N·m/s up, 16000 N·m/s down).
The driver asks for 2500 N·m from 0.5 s: much more than the tyre can transmit (about 1200 N·m), so the wheel
would lock.

The **ABS ECU** is a State Chart running every 5 ms, the classic threshold algorithm:

| State | Valves | Leave when |
|---|---|---|
| build | inlet open: the pressure rises towards the driver's demand | slip > 10 % → hold, > 20 % → release |
| hold | both closed: the pressure stays | slip > 20 % → release, < 10 % → build |
| release | outlet open: the pressure falls | slip < 10 % → build |

Below 7 km/h the ABS stops controlling, as in real cars.

![](../images/blocks/abs_braking.png)

## Results

Without ABS the wheel locks 0.14 s after the driver brakes: it stops turning while the car slides on. With ABS the
wheel speed dips and recovers six times a second, the slip oscillates between 3 and 23 %, and the brake
torque saws up and down; that is the pulsing you feel in the pedal.

| Road | `mu_road` | Stopping distance with ABS | without ABS |
|---|---|---|---|
| dry | 1.0 | 45.7 m | 54.9 m |
| wet | 0.5 | 97.0 m | 110.2 m |
| ice | 0.15 | 276 m | 367 m |

The physical limit on the dry road is $v^2 / (2 \mu g) = 39$ m: the ABS reaches 86 % of the tyre's best grip. The
shorter distance is a bonus; the main purpose of ABS is that a rolling wheel can still produce side force, so the
driver can **steer around** the obstacle.

## Experiments

| Try | What you will see |
|---|---|
| `abs_on` = 0 | the wheel locks (slip 100 %) and the car slides 9 m further on a dry road |
| `slip_hold` = 0.05, `slip_release` = 0.10 | too careful: the tyre never reaches its peak, 49.3 m |
| `slip_hold` = 0.2, `slip_release` = 0.4 | too late: the slip reaches 61 %, past the peak, 54.2 m |
| `release_rate` = 4000 | slower release: 43.4 m, but the slip peaks at 29 %; a trade-off between distance and steerability |
| `T_driver` = 1500 | a gentler driver still locks the wheel without ABS: 1200 N·m is all the tyre can take |

## Next

That was the last tutorial on vehicle dynamics and energy. Continue with electrics and electronics: [RC and RLC circuits](tutorial_rc_rlc.md), or pick another one from the [tutorial overview](tutorials.md).
