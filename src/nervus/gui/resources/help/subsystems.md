# Subsystems

A **subsystem** is a block that contains its own diagram, the way a function contains code. Use one per
ECU function ("idle control", "fuel", "ignition") or to keep a plant model tidy.

## Creating a subsystem

- **From a selection**: select blocks and choose **Edit → Create Subsystem from Selection** (⌘G / Ctrl+G).
  Lines that cross the selection boundary become the subsystem's **In** and **Out** ports automatically.
  This is one undo step.
- **From the library**: drag **Ports & Subsystems → Subsystem** onto the canvas. It starts with one In
  connected to one Out.

## Opening and navigating

- **Double-click** a subsystem to open it.
- The **breadcrumb bar** above the canvas shows where you are (`model › ECU › ...`). Click a level to
  go back, or use **View → Up to Parent** (⌘↑ / Ctrl+Up).
- Undo and redo work across levels. Undoing a change inside a subsystem opens that subsystem.

## Ports

The **In** and **Out** blocks inside a subsystem are its ports, ordered by their *port* parameter
(1, 2, ...). Their names are shown inside the subsystem block on the canvas. Port numbers must be
1, 2, 3, ... without gaps; Diagnostics reports problems.

At the **top level** of a model, In and Out blocks are the model's external inputs and outputs. When
simulating, a top-level In block outputs its *value* parameter. In generated C code they become the input
and output structs.

## Names in results and diagnostics

Blocks inside subsystems are named by their path: `ECU/throttle_pi`, `ECU/fuel/map`. Use those names to
read results (`res["ECU/throttle_pi"]`). Clicking a diagnostic opens the subsystem and selects the block. A
subsystem that contains an error is outlined in red.

## Atomic subsystems: one ECU task

An ECU's operating system runs its software in **tasks** with fixed periods, such as a 10 ms task for idle
speed and throttle control and a 100 ms task for temperatures. In Nervus, a subsystem with a *sample_time*
is such a task:

- Every block inside that **inherits** its sample time (`-1`, e.g. Gain, Sum, Counter, Discrete PID with
  `sample_time = -1`) runs at the task period. Blocks with their own sample time keep it, and an inner task
  keeps its own period.
- The inputs are **sampled** at the start of each period and the outputs **held** until the next one, as if
  the task read its inputs from RAM when it starts.
- Blocks with continuous states (Integrator, Transfer Function) are not allowed inside. Use their discrete
  versions.
- The block shows its period (⏱ 10 ms). In generated C the multirate scheduler runs each task's blocks at
  its rate, or, with *one function per task*, each task becomes its own C function (see [C code generation](codegen.md)).

So you set the period in one place, and every block in the task follows it: change `sample_time` to
`"Ts_fast"` and the whole task moves with the workspace variable.

## Enabled subsystems

Tick *enable_port* in a subsystem's Properties to give it an extra last input, **⏻ enable**. While the
enable is ≤ 0 nothing inside runs: outputs and discrete states are held, and continuous states stop. Use it for
functions that only run in some operating mode, e.g. idle speed control only while the engine idles. With
*when_enabled* = `reset`, the discrete states (counters, delays, integrators) restart from their initial
values each time the subsystem is enabled again. The enable is checked once per time step, and the
generated C code wraps the subsystem's blocks in `if (enabled)`.

## Triggered subsystems

Set *trigger* to `rising`, `falling` or `either` to give the subsystem a last input, **trigger**. The
subsystem then runs only when that signal has an edge, and holds its outputs and states in between. Use it
for event-driven functions: counting crank teeth, latching a value when a button is pressed, or running a
task each time a CAN message arrives.

| *trigger* | Runs when the trigger signal goes |
|---|---|
| `rising` | from ≤ 0 to > 0 |
| `falling` | from > 0 to ≤ 0 |
| `either` | either way |

The trigger is checked each time the blocks inside would execute, so drive it with a discrete signal of the
same rate as those blocks. The first execution only records the trigger value, so nothing runs before the
first real edge. Blocks with continuous states (integrators, transfer functions) are not allowed inside; use
their discrete versions. A subsystem can have both an enable and a trigger input (data inputs first, then
enable, then trigger). It then runs on an edge only while it is enabled. The generated C checks the edge the
same way, and the tests compare it with the simulation with zero tolerance.

## Variant subsystems: several implementations of one component

A subsystem can have **variants**: alternative implementations with the same inputs and outputs. Typical
uses: a petrol and a diesel engine model, a simple and a detailed sensor model, or the controller for two
vehicle versions. Only the active variant is simulated and turned into C code.

Select a subsystem and press **Create Variants…** (or **Variants…**) in the Properties panel:

- **Add (copy of active)** creates a new variant as a copy of the active one; open the subsystem and change it.
- **Condition** chooses the variant from model workspace variables, e.g. `ENGINE == 2`. Comparisons
  (`==`, `!=`/`~=`, `<`, `<=`, `>`, `>=`) and `and`/`&&`, `or`/`||`, `not`/`~` are allowed. A variant with an
  empty condition is the **default**, used when no condition is true. It is an error if several conditions
  are true at once.
- Without any conditions, you pick the **active variant** by hand.

The block shows the active variant (◆ petrol). Opening the subsystem shows and edits the active variant;
switch variants to edit another. A parameter sweep can switch variants too:
`simulate(model, 10, params={"ENGINE": 2})`.

```python
engine = Subsystem(system=petrol_engine)
engine.active_variant = "petrol"
engine.add_variant("diesel", diesel_engine, condition="ENGINE == 2")
engine.variants["petrol"].condition = "ENGINE == 1"
```

## Function-call subsystems

Sometimes logic, not a clock, decides when a function runs: store a value each time the engine is switched
off, or run a learning routine once per drive cycle. Set *trigger* to `function_call`: the subsystem then runs
**once every time the trigger value changes**. The caller, usually a State Chart, increments a counter output
whenever it wants the function to run:

```text
input u
output calls = 0

state low
state high
  entry: calls = calls + 1      # call the subsystem once, every time we enter "high"

transition low -> high: u > 0.5
transition high -> low: u < -0.5
```

Connect `calls` to the subsystem's trigger input (marked ƒ()). The subsystem runs in the same time step as the
call, after the chart, and holds its outputs and states between calls. In generated C it becomes
`if (call counter changed) { ... }` around its blocks.

## Masks: reusable components with parameters

A **masked** subsystem has its own parameters, like a function's arguments. The blocks inside use them in
their expressions, and each copy of the subsystem can have different values. This is how you build
reusable components such as an engine model with a `displacement` parameter.

Select a subsystem and press **Create Mask…** (or **Edit Mask…**) in the Properties panel:



- **Mask parameters**: name, value, unit and description. Values can be numbers, vectors, or expressions of
  model workspace variables (`3 * base`).
- **Icon text**: shown on the block; `\n` starts a new line.
- **Description**: shown in the Properties panel.

The parameters then appear in the Properties panel of the subsystem, like the parameters of any block:



Names are looked up from the inside out: a block sees the parameters of the nearest mask first, then those
of outer masks, then the model workspace. In generated C, mask parameters are per-instance values. Only bare
**model workspace** variables become shared calibration parameters.

```python
from nervus.core import Variable

engine = Subsystem(system=engine_diagram)
engine.mask.add(Variable("displacement", 1.6e-3, "Swept volume", "m³"))
engine.mask_icon = "Engine\\n1.6 l"
```

## Parameters

Blocks inside subsystems can use the model workspace variables, just like blocks at the top level.
