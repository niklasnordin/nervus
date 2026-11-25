# Tutorial: Tyre slip and traction control

| | |
|---|---|
| **You will learn** | tyre slip and Pacejka's magic formula, wheel and vehicle dynamics, a traction control ECU on dry, wet and snowy roads |
| **Before this** | *Your first model: a cruise control* |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/traction_control.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Vehicle dynamics and energy ▸ Chassis control (*all tutorials*) |

This tutorial follows `examples/traction_control.py`: a car launching at full throttle on dry, wet and snowy
roads, and a traction control (TCS) ECU that keeps the driven wheels from spinning.

```bash
uv run python examples/traction_control.py
```

## Slip and the magic formula

A tyre transmits force only through **slip**: the driven wheel turns a little faster than the car moves,

$$ \lambda = \frac{\omega r - v}{\max(\omega r,\ v)} $$

The force rises steeply with slip, peaks at about 10 %, then falls slowly; a spinning tyre also loses its side
grip, so the car can no longer be steered. Pacejka's **magic formula** fits this curve:

$$ F_x = \mu\,F_z\,\sin\!\big(C \arctan(B\lambda - E(B\lambda - \arctan B\lambda))\big) $$

with B = 10, C = 1.9, E = 0.97 here, and the road's friction μ (dry 1.0, wet 0.5, snow 0.2) scaling it.

The car: the engine torque at the wheels turns the driven wheels (their inertia includes the engine's, reflected
through 1st gear: 30 kg·m²); the tyre force pushes the car and brakes the wheels.

## Traction control

Every 10 ms the TCS ECU compares the driven wheels' speed with the non-driven wheels' (which turn with the car)
and computes the slip. Above the 10 % target, a PI controller reduces the engine torque (0 = none, 1 = all).

![](../images/blocks/traction_control.png)

| Road | TCS off | TCS on |
|---|---|---|
| dry (μ = 1.0) | 8 % slip, 0-50 km/h in 2.7 s | the same: the tyres grip, TCS does nothing |
| wet (μ = 0.5) | the wheels spin at 86 % slip, 0-50 in 5.4 s | 10 % slip, 0-50 in 5.2 s |
| snow (μ = 0.2) | 96 % slip | 10 % slip (13 % at the start) |

On the wet road the car is only a little faster with TCS (the magic formula's force falls slowly past its
peak), but its wheels keep their side grip: that is the main point of traction control. In the first 0.2 s
the controller oscillates: at walking pace the slip changes very fast compared with the 10 ms task, and real
systems use the brakes and an engine torque model for the start.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

| Try | What you will see |
|---|---|
| `slip_target` = 0.2 | 20 % slip and a little faster (60 instead of 58 km/h after 6 s): this tyre's peak is broad |
| `J_wheels` = 2.5 (the wheels alone) | the wheels spin up much faster than the 10 ms task: slip peaks at 67 % |
| `mu_road` = 0.2, `T_wheel_max` = 500, `tcs_enable` = 0 | below the 550 N·m the snowy tyres can carry: little slip even without TCS |

## Next

Next in this topic: *Anti-lock braking (ABS)*. Or pick another one from the *tutorial overview*.
