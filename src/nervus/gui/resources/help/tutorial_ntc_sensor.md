# Tutorial: An NTC temperature sensor interface

| | |
|---|---|
| **You will learn** | a thermistor in a voltage divider, the ECU's pull-up, input filter and ADC, a conversion table, resolution over the range, why software and hardware must match |
| **Before this** | [Your first model: a cruise control](tutorial_first_model.md) |
| **Level and time** | intermediate, about 35 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/ntc_sensor.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Electrics and electronics ▸ ECU hardware and PCB ([all tutorials](tutorials.md)) |

Coolant, intake air, oil, fuel and battery temperatures are all measured the same way: an **NTC thermistor** in a
voltage divider, read by the ECU's analog-to-digital converter. `examples/ntc_sensor.py` models the whole chain,
from the component on the engine to the number in the software, so you can see the design decisions an ECU
hardware engineer makes.

```bash
uv run python examples/ntc_sensor.py
```

## The circuit

| Part | Where | Value |
|---|---|---|
| NTC thermistor | in the engine | 2 kΩ at 25 °C, β = 3600 K (the **Thermistor** block) |
| harness | the wiring | 0.5 Ω (a **Variable Resistor**, used for fault injection) |
| sensor supply | ECU board | 5 V reference |
| pull-up resistor | ECU board | 2.49 kΩ |
| input filter | ECU board | 10 kΩ and 100 nF (τ = 1 ms): protection against noise and transients |
| ADC | microcontroller | 12 bits on 0-5 V (4096 counts), read every 100 ms |

The NTC's resistance falls steeply with temperature, $R = R_{25}\, e^{\beta (1/T - 1/298\,\mathrm{K})}$: 30.7 kΩ at
−30 °C, 2.0 kΩ at 25 °C, 230 Ω at 90 °C. With the pull-up it forms a divider:

$$ V_{sig} = 5\,\mathrm{V} \cdot \frac{R_{NTC}}{R_{NTC} + R_{pull}} $$

The **software** turns the ADC counts back into a temperature with a lookup table (23 points from −40 to 150 °C,
computed from the divider, the way a sensor supplier's data sheet gives it), every 100 ms.

## Accuracy depends on the temperature

![](figures/ntc_divider.png)

The divider is nonlinear, so one ADC count means a different temperature step at each temperature. With the
2.49 kΩ pull-up:

| Temperature | NTC | Signal | Resolution |
|---|---|---|---|
| −30 °C | 30.7 kΩ | 4.62 V | 0.06 °C per count |
| 20 °C | 2.46 kΩ | 2.48 V | 0.02 °C per count |
| 90 °C | 230 Ω | 0.42 V | 0.12 °C per count |
| 120 °C | 108 Ω | 0.21 V | 0.26 °C per count |

The pull-up is chosen so the sensor is most accurate where it matters (here 20-100 °C) and the signal stays
inside the diagnostic window of 0.1-4.9 V over the whole range. Run the model: over the warm-up from −10 to
95 °C the ECU reads the temperature within 0.3 °C. (At the very first sample, at power-up, the filter capacitor is
still empty and the ADC reads 0 V; real ECUs wait a few milliseconds before the first reading.)

## Experiments

| Try | What you will see |
|---|---|
| `R_pull` = 470 | the ECU reads **41.5 °C at 95 °C**: the table still describes the 2.49 kΩ circuit. Software and hardware must match |
| `R_pull` = 470 and the table for it: in the Console, `import sys; sys.path.insert(0, "examples"); from ntc_sensor import table`, then `model.workspace["adc_counts_axis"], model.workspace["adc_temp_table"] = table(470)` | accurate again (0.4 °C) and better at high temperatures, but −30 °C would give 4.92 V, above the 4.9 V open-circuit limit: a false code on a cold morning |
| `C_filter` = 100e-6 (τ = 1 s) | a slower filter: the reading lags by about a second and the error grows to 0.9 °C during the warm-up |

The same circuit with a short or an open wire is the subject of the next tutorial,
[Sensor fault diagnosis](tutorial_sensor_diagnostics.md).

## Next

Next in this topic: [The injector driver power stage](tutorial_injector_driver.md). Or pick another one from the [tutorial overview](tutorials.md).
