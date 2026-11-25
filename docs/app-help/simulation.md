# Running simulations

Press **▶ Run** (⌘R / Ctrl+R / F5). While a simulation runs, the diagram is locked and **■ Stop** ends the
run early; the data up to that point is kept.

## Stop time and solver

The stop time is in the toolbar. **Simulation → Simulation Settings…** (⌘E) has the rest:

| Solver | Use for |
|---|---|
| **auto / RK45** | The default. Picks its own step size. |
| **RK23**, **DOP853** | Lower / higher accuracy than RK45. |
| **Radau**, **BDF**, **LSODA** | *Stiff* models, with very fast and very slow dynamics together. |
| **rk4** (fixed step) | Models that will become ECU code. Every step has the same size. |
| **euler** (fixed step) | The simplest method. |

Variable-step solvers stop exactly at discontinuities that blocks announce (the time of a Step, the edges
of a Pulse). Fixed-step solvers need a step that divides every discrete sample time; leave it empty and
Nervus chooses one.

## Discontinuities (zero crossings)

When a Saturation hits its limit, a Switch changes input or a comparison flips, variable-step solvers stop
**exactly** at that moment instead of stepping over it. The status bar shows how many such *zero crossings*
were found. If a block chatters back and forth along a limit, detection is turned off for it and a warning
appears in Diagnostics. You can switch the feature off in Simulation Settings.

## Pacing

A simple model simulates much faster than real time, so a live scope only shows the end. Set **Pacing** in
Simulation Settings to run no faster than that multiple of real time: 1 = real time, 0.5 = half speed, 10 =
ten times faster. Leave it empty to run as fast as possible (the default). In Python:
`d.settings.pacing = 1.0`.

Use **Stop Simulation** to end a run when a condition becomes true, and **Assertion** to fail the run when a
condition that must always hold becomes false.

## Sample times

Discrete blocks (Unit Delay, Zero-Order Hold, Discrete PID, ...) have a **sample_time** in seconds: they
compute a new output every *T* seconds and hold it in between, just like a task on an ECU. `-1` means
*inherit from the input*, `0` means continuous.

## Scopes

Every Scope opens when the first run starts and **plots live** while the simulation runs. Each input gets its own colour, always in the same order. Hover over
the plot to read values at that time. Drag to pan, scroll to zoom, and press *Autoscale* to reset.
*Show samples* marks the computed points, which is useful for discrete signals.

## When something is wrong

Problems appear in **Diagnostics** and the blocks involved get a red outline. Common ones:

- **"input 0 of 'X' is not connected"**: every input needs a line. Use a *Constant* with value 0 if you
  really want zero.
- **Algebraic loop**: a feedback loop made only of blocks whose output depends directly on their input
  (Gain, Sum, ...). Put an *Integrator* (continuous) or *Unit Delay* (discrete) in the loop, or, if the loop
  is intended, set **Simulation ▸ Settings ▸ Algebraic loops** to *Solve iteratively* (see [Concepts](concepts.md)).
- **"needs a discrete sample time"**: a Unit Delay that inherits from a continuous signal. Give it a
  sample time, or put a Zero-Order Hold in front.
- **"fixed step does not divide the sample time"**: choose a step such as 0.001 for a 0.01 s controller.
