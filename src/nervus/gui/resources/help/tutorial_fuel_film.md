# Tutorial: Wall wetting and learned fuel trims

| | |
|---|---|
| **You will learn** | fuel film dynamics (x-tau) and transient compensation, a switching lambda sensor, long-term trims learned per load cell (Adaptive Map) |
| **Before this** | [Lambda control and fuel trims](tutorial_lambda.md) |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/fuel_film.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Powertrain and engine control ▸ Engine management ([all tutorials](tutorials.md)) |

This tutorial follows `examples/fuel_film.py` and continues [lambda control](tutorial_lambda.md) with three
things every production fuel system has.

```bash
uv run python examples/fuel_film.py
```

![](figures/fuel_film.png)

## Wall wetting (x-tau)

Part of the fuel injected into the intake port lands on its wall and evaporates later. With the share *X* that
deposits and the evaporation time constant *τ*:

$$ \frac{dm_{film}}{dt} = X\,q_{inj} - \frac{m_{film}}{\tau}, \qquad q_{cyl} = (1 - X)\,q_{inj} + \frac{m_{film}}{\tau} $$

On a tip-in (air flow 5 → 20 g/s) the film first has to grow, so the cylinders get too little fuel and the
engine runs lean, then rich on the tip-out. Without compensation lambda jumps by 28 %. The ECU runs the same
model with its own film estimate and injects

$$ q_{inj} = \frac{q_{cyl,desired} - m_{film,est}/\tau}{1 - X} $$

which cuts the excursion to 10 % here (the rest comes from the manifold filling and the 10 ms task).

## A switching lambda sensor

A narrow-band (zirconia) sensor only says *rich* (about 0.8 V) or *lean* (about 0.1 V). The short-term trim
therefore **jumps** by 2 % when the sensor switches and **ramps** by 8 %/s in between: lambda oscillates in a
small limit cycle around 1, which is also what the catalyst needs to store and release oxygen.

## Long-term trims per load cell

The injectors in this model deliver 8 % too little at low flow (an injector non-linearity at short pulses).
The long-term trim is an **Adaptive Map** over the air flow: it learns the average short-term trim in the cells
around the current operating point. At idle it learns +8.8 %; at 20 g/s it stays near 0 %, and when the engine
comes back to idle, the idle correction is still there, so the short-term trim does not have to start over. A
single long-term trim would be pulled back and forth between the two regions.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

| Try | What you will see |
|---|---|
| `xtau_comp` = 0 | 28 % lean and rich spikes on every tip-in and tip-out |
| `X_film` = 0.5 and `xtau_comp` = 0 (a cold engine, no compensation) | 46 % spikes; a real ECU makes X and τ depend on the coolant temperature |
| `ltft_rate` = 0.1 | the map learns slowly: after 20 s it has 6.7 % and the short-term trim still carries the rest |

## Next

Next in this topic: [Gear-shift logic](tutorial_gear_shift.md). Or pick another one from the [tutorial overview](tutorials.md).
