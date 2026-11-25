# Tutorial: Rectifier, relay driver, buck converter and active filter

| | |
|---|---|
| **You will learn** | circuit simulation like LTspice: a rectifier, a relay driver and its freewheeling diode, a buck converter, an active filter; SPICE netlists |
| **Before this** | *RC and RLC circuits*, *Starter motor and electronic throttle* |
| **Level and time** | advanced, about 45 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/rectifier.nervus`, `examples/relay_driver.nervus`, `examples/buck_converter.nervus`, `examples/active_filter.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Electrics and electronics ▸ Circuit fundamentals (*all tutorials*) |

This tutorial follows `examples/circuits.py`: four circuits that every ECU engineer meets, simulated the way
LTspice does it. You will see a rectifier's ripple, the voltage spike of a relay coil, a switching regulator
and an active filter, and how to check each one by hand.

```bash
uv run python examples/circuits.py     # simulates the four circuits, saves them as models
uv run nervus examples/relay_driver.nervus
```

![](../images/blocks/circuits.png)

## 1. Rectifier with a smoothing capacitor (from a SPICE netlist)

The rectifier is read from a netlist, exactly as LTspice writes it:

```
V1 in 0 SIN(0 10 50)
D1 in out D1N4007
C1 out 0 100u
R1 out 0 1k
.model D1N4007 D(IS=7n N=1.8 BV=1000 IBV=5u)
.tran 50u 100m
```

```python
from nervus.spice import read_netlist

d = read_netlist("rectifier.cir")  # every node gets a probe named like SPICE's: V(out)
```

The diode conducts only near the peaks of the sine; in between, the capacitor feeds the 1 kΩ load and its
voltage sags. The ripple is about the load current times the time between peaks, divided by C:

$$ \Delta V \approx \frac{I}{f\,C} = \frac{9\ \text{mA}}{50\ \text{Hz} \cdot 100\ \mu\text{F}} = 1.8\ \text{V} $$

The simulation gives 9.3 V at the peaks (10 V minus about 0.7 V across the diode) and 1.5 V of ripple: a bit
less, because the capacitor also recharges for part of each period.

## 2. Relay driver: why every coil needs a freewheeling diode

An ECU output switches a relay coil (80 Ω, 0.1 H) with an NPN transistor. When the transistor turns off,
the coil wants to keep its current flowing, and

$$ v_L = L\,\frac{di}{dt} $$

gets as large as it needs to be. Without a diode, the collector voltage shoots up until something gives way:
here the transistor's collector-emitter breakdown at 60 V (modelled as a 60 V Zener). That repeated avalanche
destroys output stages. With the **freewheeling diode** from the collector to the supply, the current flows on
through the diode and decays with τ = L/R = 1.25 ms; the collector stays one diode drop (0.8 V) above 12 V.

While it is on, the transistor is **saturated**: 2.2 kΩ from 5 V gives about 2 mA of base current, β · 2 mA =
300 mA is more than the 150 mA the coil needs, so the collector sits at 0.13 V.

This circuit is **stiff**: with the transistor off, the coil sees a huge resistance, a time constant of a few
picoseconds. Explicit solvers then take tiny steps; the model uses **BDF**, an implicit method like LTspice's.

## 3. Buck converter: 12 V to 5 V

A switch opens and closes 20 000 times a second (duty cycle 42 %). While it is closed, the input drives
current into the inductor; while it is open, the inductor keeps the current flowing through the diode. On
average the inductor's voltage is zero, so

$$ V_{out} \approx D \cdot V_{in} = 0.42 \cdot 12\ \text{V} = 5.04\ \text{V} $$

The simulation settles at 4.8 V: the switch resistance and the 0.4 V of the Schottky diode take the rest. The
start shows the LC filter overshooting to 8 V before it settles; a real converter's controller would prevent
that with a soft start. The model uses BDF without zero-crossing search: the switching instants are known from
the PWM source.

## 4. Active filter: a Sallen-Key low-pass

An op-amp with two resistors and two capacitors makes a second-order low-pass filter. With equal resistors R
and C1 = 2 C2 it is a Butterworth filter (Q = 0.707) with the cut-off frequency

$$ f_c = \frac{1}{2\pi R \sqrt{C_1 C_2}} = 1\ \text{kHz} $$

The model has an In block on its input source and an Out block on the output sensor, so **Analysis → Linear
Analysis** gives its Bode plot: 0 dB at 100 Hz, −3.0 dB at 1 kHz and −40 dB at 10 kHz, the 40 dB per decade of
a second-order filter. This is SPICE's `.ac` analysis.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

| Try | What you will see |
|---|---|
| rectifier: `C1` = 470 µF | the ripple falls below 0.4 V |
| relay driver: remove `D_flyback` | the 60 V breakdown spike (or `relay_driver(flyback=False)`) |
| buck converter: `duty` of `pwm_20kHz` = 25 % | about 2.8 V out |
| filter: `R1` = `R2` = 20 kΩ | the cut-off moves to 500 Hz |

## Next

Next in this topic: *Starter motor and electronic throttle*. Or pick another one from the *tutorial overview*.
