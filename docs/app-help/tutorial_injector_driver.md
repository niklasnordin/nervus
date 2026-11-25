# Tutorial: The injector driver power stage

| | |
|---|---|
| **You will learn** | a low-side switch driving a solenoid injector, opening delay versus battery voltage, freewheeling diode versus clamp, why injection needs a battery voltage correction |
| **Before this** | *RC and RLC circuits* |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/injector_driver.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Electrics and electronics ▸ ECU hardware and PCB (*all tutorials*) |

A fuel injector is a solenoid valve, switched by a transistor on the ECU's circuit board. How long it is *really* open,
and so how much fuel the engine gets, depends on that circuit and on the battery voltage, not only on the pulse the
software commands. `examples/injector_driver.py` models the power stage and shows the two effects every injection
software compensates.

```bash
uv run python examples/injector_driver.py
```

## The circuit

| Part | Value |
|---|---|
| battery | 9-15 V (`V_bat`), the injector's supply |
| injector | **Fuel Injector** block: 12 Ω, 10 mH, the needle lifts above 0.6 A and drops below 0.3 A, 3.5 g/s when open |
| low-side switch | the ECU's transistor between the injector and ground, 0.1 Ω when on |
| clamp | the transistor's avalanche clamp: limits its voltage to 60 V |
| freewheeling diode | optional (`freewheel` = 1): across the coil, from the transistor back to the battery |

The software commands a **3 ms** pulse. At 3.5 g/s that should be 10.5 mg of fuel.

![](../images/blocks/injector_driver.png)

## Opening: the battery voltage matters

When the transistor switches on, the coil current rises with the time constant $\tau = L/R = 0.83$ ms towards
$V_{bat}/R$:

$$ i(t) = \frac{V_{bat}}{R}\left(1 - e^{-t/\tau}\right) $$

The needle only starts to lift when the current passes the pull-in current (0.6 A), and then needs 0.5 ms to
travel. At 14 V the injector is open (half lift) **0.96 ms** after the command; at 9 V, where the current only just
reaches 0.6 A (9/12.1 = 0.74 A at the end), it takes **1.71 ms**.

## Closing: freewheeling diode or clamp

When the transistor switches off, the coil keeps driving its current:

- With a **freewheeling diode**, the current circulates through the diode and decays slowly with τ; the voltage at the
  transistor stays near the battery voltage (14.7 V at 14 V). The needle closes **1.34 ms** after the end of the pulse.
- Without it, the current has nowhere to go; the transistor's voltage jumps until the **clamp** conducts at 60 V, and
  the high voltage drives the current down fast. The needle closes **0.50 ms** after the end. That is why injector
  drivers use a clamp: a fast, repeatable closing. The price is a transistor rated for 60 V and the energy dissipated
  in it at every injection.

## What the engine gets

| Battery | Driver | Opens after | Closes after | Fuel (3 ms command) |
|---|---|---|---|---|
| 9 V | clamp | 1.71 ms | 0.41 ms | 6.0 mg |
| 12 V | clamp | 1.12 ms | 0.46 ms | 8.2 mg |
| 14 V | clamp | 0.96 ms | 0.50 ms | 8.9 mg |
| 15 V | clamp | 0.90 ms | 0.51 ms | 9.2 mg |
| 14 V | freewheel diode | 0.96 ms | 1.34 ms | 11.9 mg |

The same command delivers 50 % more fuel at 15 V than at 9 V. An engine ECU therefore adds a **battery voltage
correction** (the injector's "dead time", a table over the battery voltage) to every pulse, so a cold start on a weak
battery still gets the fuel it needs. The table comes from exactly this kind of measurement, on the bench with the real
injector and driver.

## Experiments

| Try | What you will see |
|---|---|
| `freewheel` = 1 | a slow, voltage-independent closing: more fuel, and a different dead-time table |
| `V_bat` = 9 with `freewheel` = 0 | the current barely reaches the pull-in level: a weak battery at a cold start |
| the **injector**'s `L` = 0.005 | a faster coil: it opens earlier, and the voltage dependence shrinks |
| the **injector**'s `I_pull` = 0.75 | at 9 V the current never reaches pull-in: the injector does not open at all |

## Next

That was the last tutorial on electrics and electronics. Continue with ecu software, communication and diagnostics: *A vehicle network (CAN)*, or pick another one from the *tutorial overview*.
