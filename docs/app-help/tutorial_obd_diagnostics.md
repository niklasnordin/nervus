# Tutorial: On-board diagnostics and trouble codes

| | |
|---|---|
| **You will learn** | diagnostic monitors: debouncing, enable conditions, pending and confirmed codes, two-trip detection, the MIL, readiness; reading the codes with an OBD tool |
| **Before this** | *Engine operating modes*; read *OBD-II live data* first |
| **Level and time** | intermediate, about 35 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/obd_ecu.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | ECU software, communication and diagnostics ▸ Diagnostics (*all tutorials*) |

Every engine ECU watches itself: **monitors** test the misfire rate, the fuel trims, the catalyst, the sensors, and
store a **diagnostic trouble code** (DTC) and switch on the check-engine lamp (**MIL**) when a test fails. A
workshop reads the codes over the OBD-II port. `examples/obd_ecu.py` is such an ECU with two monitors, and it can
answer a real OBD tool.

```bash
uv run python examples/obd_ecu.py
```

## The monitors

| Code | Monitor | Raw test | Enable condition |
|---|---|---|---|
| P0302 | cylinder 2 misfire | misfires in a counting window ≥ `misfire_limit` | coolant above 40 °C (a cold engine misfires more) |
| P0171 | system too lean | long-term fuel trim ≥ `ltft_limit` (+20 %) | always |

From 20 s, cylinder 2 misfires. The fuel trim stays at +4 %, so P0171 passes. A **key cycle** (key off and on)
happens every 30 s.

Each monitor is a **Diagnostic Monitor** block, which implements what the OBD rules require:

- **debouncing**: a counter goes up while the raw test fails and down while it passes; only at the threshold
  (10 samples of 0.1 s) is the test *failed*, so a glitch does not set a code;
- **pending**: the first failure stores the code as pending at once;
- **confirmed and MIL**: failing in **two key cycles in a row** ("two-trip detection") confirms the code and
  switches on the MIL; three cycles without a failure switch it off again (the code stays stored);
- **readiness**: the monitor reports *complete* once it has run, passed or failed. An inspection station checks
  that the monitors are ready before it trusts "no codes".

![](../images/blocks/obd_ecu.png)

## Follow the scenario

Open `examples/obd_ecu.nervus` and run it (90 s). The engine is warm (40 °C) at 8.5 s; the misfire monitor then
runs and is *ready* at 9.5 s (it has completed without a failure). The misfire starts at 20 s: after 10 failed
samples the code is **pending** at 21.6 s. At the key cycle at 30 s the code fails again in the new cycle: it is
**confirmed** and the **MIL** comes on at 31.1 s. The **Test Manager** has the same story as two test cases; run
them with Ctrl+T.

## Read the codes with an OBD tool

The **OBD Responder** block publishes four PIDs (rpm, speed, coolant temperature, load) and both codes. Run the
model in real time as a simulated car with an ELM327 adapter on a serial port (macOS and Linux):

```bash
uv run python -m nervus.obd simulate examples/obd_ecu.nervus
#   ECU 'ecu': ELM327 on /dev/ttys007
uv run python -m nervus.obd dtc --port /dev/ttys007        # in another terminal, after 31 s
```

The `dtc` command shows *P0302 (Cylinder 2 misfire detected)* as stored and pending, and the MIL. Any OBD
program that talks to an ELM327 can connect to that port, also one that clears the codes (mode 04): the
responder's *clear* output then resets the monitors, and their readiness drops back to *not complete*, exactly
as after clearing codes in a real car. See *OBD-II live data*.

## Experiments

| Try | What you will see |
|---|---|
| `coolant_tau` = 100 (slow warm-up) | the monitor is enabled only at 35 s: the code is pending at 35.1 s and the MIL comes on in the third cycle (60.9 s) |
| `misfire_limit` = 1.5 | the raw test can never fail: no code, but the monitor reports **ready**. A wrong threshold hides the fault and still passes the inspection: why monitors are calibrated and tested against faults |
| *threshold* = 30 on **P0302_monitor** | slower debouncing (the raw test fails in only 75 % of the windows, so the counter also goes down): pending at 25.0 s instead of 21.6 s |

## Next

Next in this topic: *Sensor fault diagnosis and substitute values*. Or pick another one from the *tutorial overview*.
