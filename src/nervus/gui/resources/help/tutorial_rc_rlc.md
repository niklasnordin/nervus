# Tutorial: RC and RLC circuits

| | |
|---|---|
| **You will learn** | time constant and step response, cut-off frequency and the Bode plot, resonance, damping and the quality factor, physical circuit blocks and linear analysis |
| **Before this** | [Your first model: a cruise control](tutorial_first_model.md) |
| **Level and time** | beginner, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/rc_filter.nervus`, `examples/rlc_circuit.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Electrics and electronics ▸ Circuit fundamentals ([all tutorials](tutorials.md)) |

The first lab of every electronics course: charge a capacitor through a resistor, then add an inductor and watch the
circuit ring. `examples/rc_rlc.py` builds both circuits from physical blocks, and you measure their **step
response** in the time domain and their **frequency response** with Linear Analysis, and compare with the formulas.

```bash
uv run python examples/rc_rlc.py      # builds rc_filter.nervus and rlc_circuit.nervus
```

## The RC low-pass

Open `examples/rc_filter.nervus`: an **In** block (5 V) drives a controlled voltage source, through
$R = 1$ kΩ into $C = 1$ µF; a voltage sensor measures the capacitor and feeds the **Out** block. The capacitor starts
empty, so pressing **▶** shows the classic charging curve:

$$ v_C(t) = V \left(1 - e^{-t/\tau}\right), \qquad \tau = RC = 1 \text{ ms} $$

After one time constant the capacitor is at 63 % (3.16 V), after five at 99 %. The model's test case checks both.

Now **Analysis ▸ Linear Analysis** (Ctrl+L), from `v_in` to `v_out`: the transfer function is
$G(s) = 1 / (\tau s + 1)$, a first-order low-pass. The Bode plot is flat at low frequencies and falls by
20 dB per decade above the **cut-off frequency**

$$ f_c = \frac{1}{2\pi RC} = 159 \text{ Hz}, $$

where the gain is −3.01 dB (half the power) and the phase −45°.

## The RLC circuit

Open `examples/rlc_circuit.nervus`: the same source, 1 V, through $R = 10$ Ω, $L = 10$ mH and $C = 10$ µF in series.
The capacitor voltage is now a second-order system:

$$ G(s) = \frac{1}{LC s^2 + RC s + 1}, \qquad f_0 = \frac{1}{2\pi\sqrt{LC}} = 503 \text{ Hz}, \qquad
\zeta = \frac{R}{2}\sqrt{\frac{C}{L}} = 0.16 $$

With so little damping the step response **overshoots by 60 %** and rings at about 500 Hz before settling, and the
Bode plot has a **resonance peak** of +10 dB at $f_0$: the quality factor $Q = 1/(2\zeta) = 3.2$, the voltage across
the capacitor at resonance is 3.2 times the input.

![](figures/rc_rlc.png)

The resistance decides the damping. **Critical damping** ($\zeta = 1$) needs $R = 2\sqrt{L/C} = 63$ Ω: the fastest
response without overshoot. More resistance makes it **overdamped**: no overshoot, but slower.

## Experiments

Change the values in the Model Workspace, run, and compare in the Data Inspector or Linear Analysis:

| Try | What you will see |
|---|---|
| RC: `C` = 10e-6 | τ = 10 ms: ten times slower, and the cut-off moves to 15.9 Hz |
| RLC: `R` = 63.2 | critical damping: no overshoot, settled in about 2 ms |
| RLC: `R` = 200 | overdamped: slow, like a first-order circuit |
| RLC: `R` = 1 | ζ = 0.016: Q = 32, a 30 dB resonance peak and a ringing that lasts many milliseconds |
| RLC: `L` = 2.5e-3 | f₀ doubles to 1007 Hz, ζ doubles to 0.32 |

From here, [the circuits tutorial](tutorial_circuits.md) goes on to diodes, transistors and switching converters.

## Next

Next in this topic: [Rectifier, relay driver, buck converter and active filter](tutorial_circuits.md). Or pick another one from the [tutorial overview](tutorials.md).
