# Tutorial: Electric vehicle range on the WLTP cycle

| | |
|---|---|
| **You will learn** | the energy chain of an electric car, drivetrain efficiency, regenerative braking, the winter heater, consumption in kWh/100 km and range |
| **Before this** | [A car on a drive cycle](tutorial_drive_cycle.md) |
| **Level and time** | beginner, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/ev_range.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Vehicle dynamics and energy ▸ Longitudinal dynamics and energy ([all tutorials](tutorials.md)) |

How far does an electric car get on one charge, and why so much less in winter? `examples/ev_range.py` drives an
electric version of the car from [the drive-cycle tutorial](tutorial_drive_cycle.md) over the official **WLTC** (the cycle of
the WLTP type approval) and follows the energy from the battery to the wheels and back.

```bash
uv run python examples/ev_range.py
```

## The model

The vehicle, driver and road loads are those of the drive-cycle model, with the WLTC class 3b from the **Drive Cycle**
block: 30 minutes, 23.3 km, up to 131 km/h. The car weighs 1800 kg and has 150 kW. The new part is the **battery
power** (a Python Function on the wheel power):

$$ P_{bat} = \begin{cases} P_{wheel} / \eta_{drive} & P_{wheel} \ge 0 \ \text{(driving)} \\
P_{wheel} \cdot \eta_{regen} \cdot s_{regen} & P_{wheel} < 0 \ \text{(braking)} \end{cases} \; + P_{aux} + P_{heater} $$

with the drivetrain efficiency $\eta_{drive}$ = 0.88 (inverter, motor, gearbox), the recovery efficiency
$\eta_{regen}$ = 0.75, the share of the braking done by the motor $s_{regen}$ = 1, and 0.3 kW for the
auxiliaries. Integrated over the cycle, it gives the energy taken from the battery.

![](figures/ev_range.png)

## Where the energy goes

Over the 23.3 km of the WLTC:

| | kWh |
|---|---|
| energy delivered to the wheels (rolling resistance, air drag, accelerating the mass) | 3.72 |
| + drivetrain losses (12 %) | 0.51 |
| − braking energy recovered (75 % of 1.05 kWh) | −0.78 |
| + auxiliaries | 0.15 |
| **= from the battery** | **3.60** |

That is **15.5 kWh/100 km**, and with 60 kWh usable a WLTP range of **387 km**, typical for a mid-size electric car.
The negative spikes in the battery power are the decelerations, where the motor charges the battery.

## Winter

In winter the cabin must be heated, and an electric car has no waste heat from an engine. A resistive heater takes
about 3 kW, the whole time: on the 30-minute cycle that is 1.5 kWh, more than a third of the driving energy.

| Situation | kWh/100 km | Range |
|---|---|---|
| summer | 15.5 | 387 km |
| no regenerative braking | 18.8 | 318 km |
| winter, heat pump (1.2 kW) | 18.1 | 332 km |
| winter, resistive heater (3 kW) | 21.9 | 274 km |

The heater costs more range than the whole regenerative braking gains; a **heat pump** gets most of it back. And the
slower the driving, the larger the heater's share, because it takes power per hour, not per kilometre: in a traffic
jam the heater can use more energy than the driving.

## Experiments

| Try | What you will see |
|---|---|
| `regen_share` = 0.5 | half the braking with the friction brakes: 17.2 kWh/100 km |
| `m` = 2200 (four passengers and luggage) | 17.4 kWh/100 km: the mass costs on every acceleration and on the rolling resistance |
| `cd_A` = 0.55 instead of 0.66 (a sleeker car) | 14.5 kWh/100 km and 415 km: air drag dominates the WLTC's fast extra-high phase |
| `P_aux` = 1000 | a hot day with the air conditioning: 17.0 kWh/100 km, 353 km instead of 387 |

## Next

Next in this topic: [Tyre slip and traction control](tutorial_traction.md). Or pick another one from the [tutorial overview](tutorials.md).
