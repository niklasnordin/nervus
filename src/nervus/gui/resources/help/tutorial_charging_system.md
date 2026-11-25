# Tutorial: The 12 V charging system and energy management

| | |
|---|---|
| **You will learn** | the power balance of battery, alternator and loads, alternator output versus engine speed, an electrical energy management function (idle boost and load shedding) as a State Chart |
| **Before this** | [Starter motor and electronic throttle](tutorial_physical.md) |
| **Level and time** | intermediate, about 35 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/charging_system.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Electrics and electronics ▸ Vehicle electrical system ([all tutorials](tutorials.md)) |

Every car's electrical system is a balance between the **alternator**, the **battery** and the **loads**. On a cold,
dark winter evening the loads are high and the alternator, turning slowly at idle, cannot keep up; the battery makes
up the difference, and a long traffic jam drains it. `examples/charging_system.py` models this balance and the
ECU function that manages it.

```bash
uv run python examples/charging_system.py
```

## The model

| Part | Block | Values |
|---|---|---|
| battery | Battery | 60 Ah, 75 % charged, 12 mΩ internal resistance |
| alternator | Alternator | 120 A at full speed (6000 rpm), nothing below 1000 rpm, 2.5:1 belt, regulator 14.2 V |
| loads | Variable Resistor | 45 A always on (ECUs, ignition, fuel pump, headlights, blower) + 25 A comfort (heated rear window, seat heaters) |
| engine | controlled Speed Source | idle in the traffic jam, 2000 rpm when driving |
| battery sensor | Current Sensor | the battery current (positive: charging), like an intelligent battery sensor |

The alternator's maximum current grows with its speed. At 800 rpm idle the alternator turns at 2000 rpm and delivers
at most **24 A**, while the loads draw about 70 A: the battery has to deliver almost 40 A.

## Energy management

The engine ECU has an **energy management** function, a State Chart running every 200 ms on the battery current:

| State | When | Action |
|---|---|---|
| normal | | idle at 800 rpm |
| boost | discharging by more than 10 A for 3 s | idle at 1100 rpm: the alternator delivers about 42 A |
| shed_loads | still discharging after 5 s in *boost* | also switch off the heated rear window and seat heaters (25 A) |
| back to normal | charging by more than 10 A | |

Its outputs go through a Unit Delay, so they act one task later, like the outputs of a real ECU (and the loop from the
battery current back to the circuit is not instantaneous).

![](figures/charging_system.png)

## Results

Without energy management the battery delivers **38 A** for the whole traffic jam: 2.5 Ah, the state of charge falls
from 0.75 to 0.71 and the voltage to 11.95 V. Repeat this on every trip in winter and the car will not start one
morning.

With energy management the chart raises the idle speed after 3.0 s and sheds the comfort loads at 8.0 s; from then on
the alternator covers the remaining 45 A and the battery is in balance (0.03 Ah in the whole jam). When the car
drives off, the alternator can deliver 96 A, the battery charges with 30 A, the loads come back on at once, and the
battery ends the trip fuller than it started (0.78).

Note that the voltage stays near 12.8 V even while charging at 2000 rpm: a partly discharged battery takes all the
current the alternator has left, and the voltage only rises to the regulator's 14.2 V when the battery is nearly
full.

## Experiments

| Try | What you will see |
|---|---|
| `energy_mgmt` = 0 | the traffic jam costs the battery 2.5 Ah |
| `idle_boost` = 1300 | the higher idle covers most of the load by itself: the comfort loads stay on, and the battery gives 0.7 Ah in the jam; the trade-off is fuel |
| `I_comfort` = 40 (more comfort loads) | still balanced after shedding: 0.06 Ah |
| `soc0` = 0.4 (a weak battery) | the same balance, but the voltage drops to 11.7 V before the management acts |

## Next

Next in this topic: [An NTC temperature sensor interface](tutorial_ntc_sensor.md). Or pick another one from the [tutorial overview](tutorials.md).
