# Concepts

## Signals

Every signal is a vector of floating-point numbers. A scalar signal has **width 1**. Widths are worked out
automatically when the model is compiled. Most math blocks work elementwise, and a scalar combines with a
vector by **scalar expansion** (a scalar gain of 2 doubles every element).

## Blocks

A block is a small dynamic system with inputs *u*, outputs *y* and optionally states *x*:

| Function | Meaning | Called |
|---|---|---|
| `outputs(t, u, xc, xd)` | output equation $y = g(t, u, x)$ | whenever outputs are needed |
| `derivatives(t, u, xc, xd)` | continuous states $\dot x_c = f(t, u, x_c)$ | by the solver |
| `update(t, u, xc, xd)` | discrete states $x_d[k+1] = f(t, u, x_d[k])$ | at the block's sample hits |

**Direct feedthrough** means that `outputs` uses *u* directly (Gain, Sum). Blocks whose output only
depends on their state (Integrator, Unit Delay) are *not* direct feedthrough.

## Execution order and algebraic loops

Before simulating, Nervus sorts the blocks so that each direct-feedthrough block runs after the blocks
that drive it. A feedback loop made **only** of direct-feedthrough blocks has no valid order. That is an
**algebraic loop**: $y = u - 2y$ defines *y* in terms of itself. Nervus reports these as errors and lists
the blocks involved:

```
AlgebraicLoopError: algebraic loop: s -> g -> s. Every feedback loop needs a block with state ...
```

Put an Integrator (continuous) or a Unit Delay (discrete) in the loop. In a real control system that
delay always exists anyway.

### Solving algebraic loops

Some models are naturally algebraic: a voltage divider with a load that depends on its own voltage, a
mass-flow balance, a controller with a direct feedthrough on purpose. For these, set **Simulation ▸ Settings ▸
Algebraic loops** to *Solve iteratively* (in Python: `model.settings.algebraic_loops = "solve"`). Nervus then

1. finds each loop (a group of blocks that depend on each other directly),
2. chooses **tear** blocks in it, whose outputs it guesses, so that the rest of the loop can be computed in
   order,
3. adjusts the guess with Newton's method until computing the loop from the guess gives the guess back:
   for $y = u - 2y$ it finds $y = u/3$, and for $x = \cos x$ it finds 0.739085.

Each evaluation of the model then solves the loop, which is slower; the simulation reports every solved loop
as a warning, with its tear blocks. A loop without a solution (e.g. $y = u + y$ with $u \ne 0$) stops the
simulation with an error naming the blocks. A loop must run at one rate and outside enabled or triggered
subsystems, and a model with a solved loop cannot become C code: an ECU cannot iterate in its tasks, so break
the loop with a Unit Delay before generating code.

## Sample times

Each block runs at a **sample time**, using the same values as Simulink:

| Value | Meaning |
|---|---|
| `0` | continuous: computed at every solver step |
| `T > 0` | discrete: computed every *T* seconds, held in between |
| `[T, offset]` | discrete with an offset |
| `-1` | inherited from the blocks that drive it |
| `inf` | constant: computed once |

Inherited blocks take the sample time of their inputs: a Gain after a 10 ms Zero-Order Hold also runs every
10 ms. If its inputs have mixed rates it becomes continuous.

Discrete outputs are computed **only at sample hits** and held between them. That is how code on an ECU
behaves: a 10 ms task computes a value and it stays put until the next run.

Use a **Zero-Order Hold** where a continuous signal (a sensor) enters a discrete controller.

## Solvers

| Solver | Type | Use for |
|---|---|---|
| `auto` / `RK45` | variable step | the default; smooth, non-stiff models |
| `RK23` | variable step | low accuracy needs, quick |
| `DOP853` | variable step | high accuracy |
| `Radau`, `BDF`, `LSODA` | variable step, implicit | **stiff** models (very fast and very slow dynamics together) |
| `rk4` | fixed step | models that will become code; deterministic step size |
| `euler` | fixed step | the simplest method; matches simple embedded code |

Variable-step solvers pick their own step size from `rtol`/`atol`, up to `max_step`. They also stop
**exactly** at discontinuities that blocks announce (the time of a Step, the edges of a Pulse), so these are
captured accurately.

Fixed-step solvers use `step` everywhere. The step must divide every discrete sample time. If you don't set
it, Nervus picks one.

## Zero crossings and discontinuities

Switching *inside* a model, such as a Saturation hitting its limit, a Switch changing input, an Integrator
reaching its limit or a comparison flipping, makes the equations discontinuous. Variable-step solvers cannot
step across a discontinuity accurately, so Nervus uses **zero-crossing detection** (like Simulink):

1. Each such block declares a *zero-crossing signal* that changes sign exactly at the discontinuity, e.g.
   `u − upper` for a saturation.
2. During integration the block's branch (its **mode**, e.g. "saturated at the upper limit") is frozen, so the
   equations stay smooth.
3. The solver stops exactly where a zero-crossing signal changes sign, and the modes are re-evaluated there.

The result is exact switching times and accurate integrals, e.g. ∫ clip(sin t) to 1e-8 with large steps.
If a signal keeps crossing back and forth very rapidly (the system *chatters* along a limit), detection is
switched off for that block and the run reports a warning in Diagnostics.

Turn it off in **Simulation Settings** (*Locate discontinuities exactly*) or with
`d.settings.zero_crossing = False`. Fixed-step solvers never use it: on an ECU the code also just steps.

Blocks with zero crossings: Saturation, Dead Zone, Abs, Sign (Math Function), Switch, Relational Operator,
Compare To Constant, Integrator (with limits), PID Controller (with limits). Stop Simulation stops a run at
the exact moment its input becomes true, and Assertion fails it when its input becomes false.

## Simulation loop

At each **major step** Nervus (1) computes all outputs, (2) logs them, (3) updates discrete states with a
sample hit, and (4) integrates the continuous states to the next major step. With a variable-step solver,
the major steps are the discrete sample hits and the breakpoints. The solver's own steps in between are
logged too.

## Accelerated simulation

**Simulation Settings → Simulation mode → Accelerated** runs the whole model as compiled C: Nervus generates C
code for every block, compiles it, and runs the simulation loop in C. Large models run **30 to 700 times
faster**. The first run of a model (and the first run after a parameter change) takes a moment longer to
compile; later runs use the compiled code again.

| | Normal | Accelerated |
|---|---|---|
| Blocks | all | blocks with C code: the ECU blocks, Integrator, Transfer Fcn, State-Space, Derivative, PID, the time sources (Step, Sine, Pulse, Ramp, From Workspace, Drive Cycle, Clock) and Python Function blocks written in simple Python |
| Solver | the one you chose | the fixed-step solver you chose; a variable-step solver is replaced by **rk4** (the step is the max step, else 1/1000 of the time span) |
| Needs | nothing | a C compiler (on macOS: `xcode-select --install`; on Linux: gcc; on Windows: MinGW or clang) |

The results are the same as a normal run with the same fixed-step solver (to about 1e-12). Models with blocks
that have no C code (physical networks, FMUs, CAN buses, OBD Responder), algebraic loops, or coverage
collection run normally, and **Diagnostics** says why. A Python Function is translated if it uses numbers,
`if`/`elif`/`else`, arithmetic, `math`/`np` functions, `min`/`max`/`abs`, `np.clip` and `np.interp` with lists
of numbers; loops and arrays keep the model in normal mode. In Python:

```python
d.settings.mode = "accelerated"
res = simulate(d)
print(res.stats.solver)  # "rk4 (accelerated, compiled C)"
```
