# Tutorial: Radiator fan control

| | |
|---|---|
| **You will learn** | on/off control with hysteresis, a thermal model with a lookup table, sensor filtering and its delay, switching rate versus temperature swing |
| **Before this** | *Your first model: a cruise control* |
| **Level and time** | beginner, about 25 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/coolant_fan.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Powertrain and engine control ▸ Engine management (*all tutorials*) |

`examples/coolant_fan.py` is one of the simplest and most common ECU functions: switch the electric radiator fan
on when the coolant gets hot. It shows why on/off control needs **hysteresis**, and what a sensor filter does to a
control loop.

```bash
uv run python examples/coolant_fan.py
```

## The model

The **cooling circuit** is one thermal capacity $C_{th} = 40$ kJ/K (coolant, engine block, radiator):

$$ C_{th} \frac{dT}{dt} = P_{engine}(load) - P_{fan} \cdot fan - 0.2 \tfrac{\text{kW}}{\text{K}} (T - 25\,°C) $$

The engine heat comes from a 1-D lookup table over the engine load (5 kW at idle, 40 kW at full load); natural
cooling grows with the temperature above ambient; the fan removes 25 kW when it runs. The load ramps up to 80 %,
stays there, and drops back to 30 %.

The **ECU** reads the temperature sensor every 10 ms, smooths it with a first-order filter (2 s), and switches
the fan with a **Relay** block: on above 98 °C, off below 92 °C.

![](../images/blocks/coolant_fan.png)

## Why hysteresis

With one switching point the fan would chatter: it switches on at 98 °C, the temperature drops a few tenths,
and it switches off again, many times a second at the edge. The relay's two thresholds give a **dead band**: once
on, the fan runs until the coolant is 6 °C cooler. Over the 10 minutes the fan switches 12 times, runs 30 % of
the time, and the coolant peaks at 98.8 °C. Each switching costs relay and motor life and makes a noise the
driver hears, so the band is a trade-off between temperature swing and switching rate.

The sensor filter removes noise but adds delay: the ECU sees the temperature about 2 s late, which is why the
coolant goes a little above 98 °C before the fan starts.

## Experiments

| Try | What you will see |
|---|---|
| `T_off` = 97 (1 °C hysteresis) | 36 switchings instead of 12; the fan cycles every 14 s |
| `T_on` = 95, `T_off` = 93 | a cooler engine (95.8 °C peak), but 26 switchings |
| `tau_sensor` = 10 | the ECU reacts late: the coolant overshoots to 101.7 °C and the test case fails |
| `P_fan` = 15 (a weaker fan) | at 80 % load the fan cannot keep up: 103 °C, and the test case fails |

Run the test case in the **Test Manager** after each change: it checks that the coolant stays below 100 °C.

## Next

Next in this topic: *Idle speed control*. Or pick another one from the *tutorial overview*.
