# Coming from Simulink

Nervus uses the same ideas as Simulink, and most defaults match, so moving between the two is easy. This page
maps Simulink names to Nervus names.

## Models and simulation

| Simulink | Nervus |
|---|---|
| model (`.slx`) | `Diagram`, saved as a `.nervus` JSON file ([Model files](model-files.md)) |
| Model Configuration Parameters | Simulation → Settings, `diagram.settings` (`SimulationSettings`) |
| `sim('model')` | `simulate(diagram)`, or ▶ Run in the editor |
| `out.logsout`, To Workspace | `SimResult`: `res["block"]`; every block output is logged. `ToWorkspace(variable=...)` |
| Simulation Data Inspector | Data Inspector (Ctrl+I): runs, signals, comparisons |
| sample time `-1`, `0`, `inf`, `[T, o]` | the same |
| ode45 / ode23 / ode15s | `RK45` / `RK23` / `BDF` or `LSODA` |
| ode4 / ode1 (fixed step) | `rk4` / `euler` |
| zero-crossing detection | the same, per block, with a chattering (Zeno) guard |
| Model Workspace, `Simulink.Parameter` | Model Workspace variables with unit, min, max, description ([Workspace](workspace.md)) |
| parameter sets / data dictionary `.sldd` | parameter sets (`.nervcal` files) |
| `.mat` results | `res.to_mat("run.mat")`; results also save as `.nervres` |

## Structure

| Simulink | Nervus |
|---|---|
| Subsystem | Subsystem ([Subsystems](subsystems.md)) |
| Mask, mask parameters, mask icon | Edit Mask…: mask parameters, icon text, description |
| Enabled Subsystem (held / reset) | Subsystem with *enable_port* and *when_enabled* |
| Triggered Subsystem (rising / falling / either) | Subsystem with *trigger* |
| Function-Call Subsystem, called from Stateflow | Subsystem with *trigger* = `function_call`, called by incrementing a chart output |
| Atomic Subsystem with a sample time | Subsystem with *sample_time* (one ECU task) |
| Rate Transition, "single-tasking" / "multitasking" code | Rate Transition block; `generate_c(..., tasks=True)` for one function per task |
| Variant Subsystem, variant conditions | Variants…: conditions such as `ENGINE == 2` |
| Library (`.slx` library), library links | Component libraries (`.nervlib`), linked subsystems ([Libraries](libraries.md)) |
| Flip block (Ctrl+I), rotate block (Ctrl+R) | Flip Block (Ctrl+Shift+F), Rotate Block (Ctrl+Shift+R) |
| Stateflow chart | State Chart block with a text definition ([State machines](statecharts.md)) |
| Simscape (Electrical, rotational mechanics, thermal) | physical blocks joined by links ([Physical modelling](physical.md)) |
| LTspice / SPICE netlists | File → Import SPICE Netlist…, `nervus.spice` |
| MATLAB Function block | Python Function block (with a *parameters* vector) |
| S-Function | a Python `Block` subclass ([Writing blocks](developer/custom-blocks.md)) |

## Code generation and verification

| Simulink | Nervus |
|---|---|
| Embedded Coder / Simulink Coder | Code → Generate C (Ctrl+B): `model.c/.h`, report, A2L ([C code generation](codegen.md)) |
| fixed-point designer, `fixdt` | data types `int8` … `uint32`, `fixdt(1,16,8)`, bit-true simulation ([Data types](datatypes.md)) |
| SIL testing, back-to-back | `back_to_back()`: compiles the C and compares it with the simulation |
| Simulink Test, Test Manager | test cases stored in the model; Test Manager panel ([Test cases](testing.md)) |
| Simulink Coverage | model coverage of decisions ([Verification](verification.md)) |
| Model Advisor | Model Advisor (Ctrl+Shift+A) |
| Linear Analysis Tool, Bode, margins | Analysis → Linear Analysis (Ctrl+L) ([Linear analysis](analysis.md)) |
| Vehicle Network Toolbox, CAN Pack / Unpack | CAN Pack / CAN Unpack with DBC files ([CAN](can.md)) |

## Blocks

| Simulink | Nervus | Notes |
|---|---|---|
| Constant, Step, Ramp, Sine Wave, Pulse Generator, Clock | same names | Sine frequency in rad/s, as in Simulink |
| From Workspace | From Workspace | arrays or a CSV file |
| Gain, Sum, Product, Abs, Saturation, Math Function | same names | Sum signs as `"+-"`, Product ops as `"*/"` |
| Integrator, Derivative, Transfer Fcn, State-Space | same names | Integrator: limits, external reset, x0 input, saturation port; Derivative is filtered (see its help) |
| Unit Delay, Zero-Order Hold, Discrete-Time Integrator, Discrete Transfer Fcn | same names | Discrete-Time Integrator has an external reset |
| PID Controller (continuous/discrete) | PID Controller, Discrete PID Controller | parallel form, filter coefficient N, anti-windup |
| Scope, To Workspace, Terminator, Stop Simulation, Assertion | same names | |
| Mux, Demux, Switch | same names | Switch control input is the middle port |
| 1-D / 2-D Lookup Table | 1-D Lookup Table, 2-D Lookup Table | interpolation `linear`/`nearest`/`flat`; extrapolation `clip`/`linear` |
| Prelookup, Interpolation Using Prelookup | Prelookup, Interpolation using Prelookup | 1-D to 4-D tables over shared axes (n-D lookup) |
| Relational Operator, Logical Operator, Compare To Constant | same names | booleans are 1.0 / 0.0 |
| Relay, Dead Zone, Rate Limiter, Quantizer | same names | Relay and Rate Limiter run at a discrete rate |
| Delay / Integer Delay, Counter Limited, Detect Rise/Fall | Integer Delay, Counter, Edge Detect | |
| Discrete Filter (first order), Moving Average, debouncing | Discrete Filter (PT1), Moving Average, Debounce | |
| MinMax, Bias, Ground, Data Type Conversion, IC | same names (IC: Initial Condition) | |

## Importing a Simulink model

**File ▸ Import Simulink Model…** opens an `.slx` file, or an `.mdl` file (Simulink's older text format), as a new
Nervus model (save it as `.nervus`). From Python: `model = nervus.simulink.read_model("controller.slx")`.

- The common blocks are translated with their parameters and positions: sources (Constant, Step, Sine Wave, Ramp,
  Clock), math (Gain, Sum, Product, Bias, Abs, MinMax), continuous and discrete blocks (Integrator, Transfer Fcn,
  State-Space, Unit Delay, Memory, Zero-Order Hold, Discrete-Time Integrator, Discrete Transfer Fcn), discontinuities
  (Saturation, Dead Zone, Rate Limiter, Relay, Quantizer), logic (Relational Operator, Logical Operator, Compare To
  Constant), routing (Switch, Mux, Demux, Data Type Conversion), lookup tables (1-D, 2-D), PID controllers, sinks,
  ports and subsystems with their masks.
- Parameters stay expressions with their workspace variables (`Kp`, `2*pi`, `[1 2 3]`); MATLAB's `^` becomes `**` and
  `0:0.5:2` becomes a list. The variables the model uses are added to the Model Workspace with the value 1: give them
  their values (they live in a MATLAB script or data dictionary that the `.slx` does not contain).
- A block that is not translated (an S-Function, a Stateflow chart, a block from another toolbox) becomes an empty
  subsystem with the same ports, named like the Simulink block, so the wiring stays and you can rebuild it there.
- The import report lists the blocks that were not translated and the workspace variables.

Compare the results: run the same scenario in both tools, export Simulink's results to a CSV or MAT file, and load
them in the **Data Inspector** next to Nervus's.

## Differences

- **Unconnected inputs are errors**, not warnings. Simulink silently treats them as zero; Nervus makes you
  decide (use a Constant 0 or Ground).
- **Everything is logged.** Every block output is available in the result; Scopes only mark what the editor
  plots.
- **Models are text.** `.nervus` files are JSON and diff well in git.
- **Charts are also text.** State charts are drawn in the chart editor and stored in a small text language, which
  diffs well and can be edited directly.
- **Stateflow charts are not imported.** `.slx` and `.mdl` models are (see above); charts become empty subsystems
  and have to be rebuilt as State Chart blocks.
