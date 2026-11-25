# Tutorial: Sensor fault diagnosis and substitute values

| | |
|---|---|
| **You will learn** | range checks on a sensor signal, open circuit and short to ground (P0117/P0118), debouncing, substitute values for limp-home, fault injection in a test |
| **Before this** | [An NTC temperature sensor interface](tutorial_ntc_sensor.md) |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/ntc_sensor.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | ECU software, communication and diagnostics ▸ Diagnostics ([all tutorials](tutorials.md)) |

Sensors and their wiring fail: a connector corrodes open, a chafed wire touches the engine block. The ECU must
notice, store a trouble code, and keep the engine running with a sensible **substitute value**. This tutorial uses
the coolant temperature sensor of [the NTC tutorial](tutorial_ntc_sensor.md) (`examples/ntc_sensor.py`) and breaks it.

```bash
uv run python examples/ntc_sensor.py
```

## Range checks

A healthy sensor never produces every voltage: the divider keeps the signal between 0.11 V (150 °C) and
4.79 V (−40 °C). The ECU uses the rest as a diagnostic window:

| Signal | Meaning | Code |
|---|---|---|
| below 0.1 V | the signal wire is shorted to ground | **P0117** engine coolant temperature circuit low |
| above 4.9 V | open circuit: broken wire or connector, the pull-up pulls the signal to 5 V | **P0118** engine coolant temperature circuit high |

Each check is a **Compare to Constant** on the measured voltage, followed by a **Diagnostic Monitor** that debounces
it (5 samples of 100 ms) and stores the code. While either code is pending, a **Switch** gives the rest of the ECU
the substitute value `T_substitute` = 80 °C instead of the measurement: the fan runs, and the engine is fuelled as
if warm, which is safe for driving to a workshop.

## Break the wire

The **fault_injection** block (a Python Function) sets the harness resistance to 100 MΩ (open circuit) or the
signal wire's resistance to ground to 0.05 Ω (short) at `fault_time` = 200 s. Set `fault` = 1 and run:

![](figures/ntc_fault.png)

The voltage jumps to 5.0 V; the raw measurement reads −40 °C (the end of the table), which would make the engine
run very rich if the ECU believed it. After 5 samples the code P0118 is pending (200.6 s) and the ECU uses 80 °C.
The MIL comes on in the same drive cycle (`confirm_cycles` = 1: electrical faults are usually confirmed at once).
With `fault` = 2 the voltage drops to 0 V, the raw reading is 150 °C, and P0117 is pending at 200.6 s.

The model's three test cases check the normal warm-up and both faults; run them in the **Test Manager**.

## Experiments

| Try | What you will see |
|---|---|
| `fault` = 1, `fault_time` = 50 | the open circuit during the warm-up: P0118 at 50.6 s, and from then on the ECU uses 80 °C |
| *threshold* = 50 on **P0118** | 5 s of debouncing: the wrong −40 °C reading is used for 5 s |
| `V_high` = 4.5 | the window is too narrow: below about −25 °C a healthy sensor already gives more than 4.5 V, so a cold start sets a false P0118 |
| `T_substitute` = 20 | a cold substitute: the ECU would run the engine rich, as if it were cold; why the substitute is chosen per function |

## Next

Next in this topic: [From continuous to discrete: sample times](tutorial_discrete_control.md). Or pick another one from the [tutorial overview](tutorials.md).
