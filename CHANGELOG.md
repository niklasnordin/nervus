# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Standalone applications for macOS, Windows and Linux (no Python needed), attached to every release.
- Simulink `.mdl` import (the text format), next to `.slx`: `nervus.simulink.read_model`.
- Versioned documentation: a version selector on https://ikjbi.github.io/nervus/ (`latest`, each release, `dev`).
- Help ▸ About Nervus: the version and the environment (copy for a bug report), links, the contributors with
  their contributions, the licence. Merged pull requests are credited automatically (CONTRIBUTORS.md).
- The [roadmap to 1.0](https://ikjbi.github.io/nervus/roadmap/): reading from and writing to real ECUs.
- MC/DC for state chart guards with several conditions; "C coverage (tests)" in the code generation dialog.
- Coverage of the generated C code by the model's test cases (gcov lines and branches, HTML report):
  `nervus.codegen.c_coverage.coverage_of_tests`.
- The documentation is online: https://ikjbi.github.io/nervus/. Releases get a GitHub Release with their notes.
- A welcome window at startup: build your first model with its tutorial beside it, take a tour, open a beginner
  example together with its tutorial, or reopen a recent model. **Help ▸ Welcome** brings it back; it can be
  switched off.
- An editor tour (**Help ▸ Editor Tour**): the library, canvas, properties, Run, the panels and the help, one at a
  time, with the rest of the window dimmed.
- Tutorial *Scripting and testing models from Python*: loading and running models, parameters for one run or for
  good, sweeps, the model's test cases in pytest, models built in code (`examples/scripting.py`).
- Tutorial *From continuous to discrete: sample times*: a PI loop run every T ms, from no difference to
  instability, discrete poles, and aliasing (`examples/discrete_control.nervus`, `examples/aliasing.nervus`).
- A validation paper in the style of the *Journal of Fluid Mechanics* (`paper/`): every module against exact
  solutions, tank draining through an orifice against measurements, and Newton's St Paul's Cathedral drops.

### Fixed
- File ▸ Import Simulink, Import SPICE, Export SPICE and Code ▸ Export FMU did nothing from the menu.
- Closing the window during a simulation hung for 5 s and left the simulation thread running.
- Undo after deleting and restoring a block could fail (*list.remove(x): x not in list*) and leave a wire behind;
  restored wires also lost their manually placed corners.
- File ▸ Refresh Links marked the model as saved, so unsaved changes could be lost on closing.
- Scope windows of earlier models stayed alive and redrew on every run.
- New, Open and the examples are disabled while a simulation runs (the old run's result ended up in the new
  model).
- Simulation: an enabled or triggered subsystem with a continuous enable signal was decided only at the start and
  end of a variable-step run; the enable is now located exactly with zero-crossing events.
- Simulation: a zero-crossing signal resting at exactly 0 fired 50 false events and switched zero-crossing
  detection off with a "chatters" warning.
- Simulation: a run that failed left saturation modes frozen, so the next run (even fixed-step) gave wrong results.
- Simulation: a negative start time skipped every discrete sample before t = 0; a fixed-step start time off the
  sample grid never ran the discrete blocks (it is now an error).
- Blocks inside an enabled subsystem whose inputs are constant ignored the enable; coverage reported outcomes for
  subsystems that never ran.
- Expressions: lists are vectors in arithmetic (`[1, 2] + [3, 4]` was concatenated), MATLAB brackets work inside
  larger expressions (`[0 0.5 1] * v_max`), and type errors are reported as parameter errors.
- State Chart: the initial state is entered in the first sample with the real inputs (its entry actions saw every
  input as 0), so `after(n)` and `t_in_state` count from its entry (they were one sample early); a transition to
  an enclosing state exits and re-enters it (its entry ran twice without its exit). The *charging system* example
  now raises the idle speed after 3.0 s and sheds loads at 8.0 s (15 samples of 0.2 s), not 2.8 s and 7.8 s.
- Generated chart code: `%` is a floored modulo and `and`/`or` return an operand, as in the simulation.
- Lookup tables: *nearest* interpolation rounds exact midpoints up in every extrapolation mode, as the C code does.
- Linear analysis: phase and margins of loops with poles at the origin or a negative gain were off by 360° (a phase
  margin of +340° for an unstable loop); PID tuning of integrating plants used the wrong margin.
- Integer-only code: Compare To Constant with `==`/`!=` and a constant the input type cannot hold gave wrong
  results; infinite saturation limits and thresholds raised an internal error.
- FMU export: integer and fixed-point inputs and outputs corrupted memory (read and written as doubles); reusable
  functions were not compiled into the FMU.
- A2L files for integer-only code declared every measurement as a 64-bit float at the wrong offset.
- C code: block names that are C keywords or collide after conversion to C names no longer break the build.
- The software-in-the-loop check rounds fixed-point inputs (truncation gave false failures).
- A damaged `.nervus` or `.slx` file gives a clear error instead of an internal one; Simulink ranges keep their
  digits; Simulink settings that are not imported are listed in the import report.
- OBD-II: trouble codes from multi-frame and multi-ECU replies, VINs from non-CAN cars, and the generic/
  manufacturer classification of B2/C2/U2 codes.
- MDF4: channel names that occur in several groups (e.g. CRC) are kept as `CRC@0`, `CRC@1` instead of dropped.
- XCP: the master follows the slave's byte order (Motorola ECUs got wrong addresses and values).
- In-app help: `\left(` and `\leq` in block equations were shown as `≤ft(` and `≤q`.
- MISRA findings with Windows paths were dropped; library links saved on Windows did not resolve elsewhere.

### Changed
- The editor follows its model–view–controller rules more strictly, checked by `tests/test_architecture.py`:
  Refresh Links is undoable, resizing a block changes the model only when the drag ends, and file import/export,
  test-suite handling and the wire annotations moved from the views to the controllers.

## [0.1.0] - 2026-11-24

The first release on PyPI: `pip install "nervus[gui]"`.

### Changed
- Simulation 10-25 % faster: physical networks are assembled in plain lists and solved once per time point;
  cheaper Step sources and integrators.

### Fixed
- A model that needs an optional package says which and how to install it (e.g. `pip install "nervus[can]"`),
  instead of an error about missing ports.
- Setting several block parameters at once (and undoing it) validates them together, so combinations that are
  only valid as a whole work; a failed change also restores the expressions.
- Wires between blocks can be drawn straight: every port sits on the grid (ports 20 px apart, centred), so a
  two-input Sum's output is level with the next block's input.
- Editor crash when a parameter was entered with Enter in the Properties panel (e.g. the PID's `I` in Tutorial
  1): the panel touched the field it had just rebuilt.
- `nervus` without the editor's packages explains how to install them (`uv sync --extra gui`) instead of a
  traceback; troubleshooting section in Getting started.
- Variable-step solvers: a zero crossing whose bracket is lost to rounding (platform dependent) is located at
  the step's end instead of stopping the simulation with "f(a) and f(b) must have different signs".
- Generated code: blocks with a sample-time offset output their initial values from the first step, as in
  the simulation (they were 0 until their first hit).
- `examples/fixed_point_sensor.py` subtracted the sensor offset in counts instead of kPa, so the ADC read 0;
  the test now checks that the pressure crosses the limit.
- XCP: frames from real hardware could be missed when another block polled the interface at the same instant;
  the XCP master discards late answers to earlier commands.
- Gain, Sum, Product and Bias inherited the boolean type of a comparison, so `3 · true` gave 1; arithmetic on
  booleans now gives double.
- A continuous PID with clamping anti-windup followed a wrong trajectory with variable-step solvers when it
  slid along its output limit (the integrator stayed clamped too long; only the final value was right), or
  chattered for tens of seconds. Clamping now uses a boundary layer during variable-step integration and
  matches a fine fixed-step reference. The derivative filter no longer integrates when D = 0.
- Blocks with a parameter given as a not-yet-resolved workspace expression were validated with the default
  value (e.g. a 2-D Interpolation with `table="ign_map"` failed); they are now validated once resolved.
- On Linux and Windows, Help and Help for Selected Block were both on F1, so F1 did nothing; Help is now Ctrl+?.
- Test checks failed to notice NaN/inf: a bounds check passed on a diverged signal. Checks now fail.
- `generate_c` for an atomic subsystem ignored its own sample time.
- Out blocks could be scheduled before the block driving them, so generated C output last step's value.
- Generated C for 2-point lookup axes compared an unsigned value with 0 (rejected by GCC with -Werror).
- A Transfer Fcn without dynamics (e.g. `3/2`) failed to build its state-space form.
- 1-D lookup with `nearest` interpolation and linear extrapolation rounded halves to even.
- An empty diagram passed to the editor model or a Subsystem was replaced by a default one.
- Discrete blocks with an inherited sample time used −1 as their period (Rate Limiter, Discrete Filter, ...).

### Added
- Integer-only code generation (no floating point, for processors without an FPU): "Integer arithmetic only" in
  the code generation dialog, `generate_c(..., integer_only=True)`; bit-true with the simulation.
- Simulink `.slx` import (File ▸ Import Simulink Model, `nervus.simulink.read_slx`): the common blocks with their
  parameters and positions, subsystems and masks; untranslated blocks become empty subsystems, listed in a report.
- Condition coverage and MC/DC for Logical Operator decisions: in the Test Manager, the coverage report (with
  the evaluations a test still needs) and `Coverage.mcdc_percent` / `mcdc_missing()`.
- Packaging for PyPI: `pip install "nervus[gui]"`; the examples are in the package (File ▸ Open Example,
  `nervus --copy-examples DIR`); `nervus --version`; a release workflow (tag `vX.Y.Z`) with trusted publishing.
- Code generation: subsystems with a *function_name* become one reusable C function, called once per subsystem
  with its own signals, states and calibration (in the A2L file; back-to-back tested, MISRA checked).
- Signal Builder block and signal editor: piecewise-linear test signals in groups (scenarios), drawn by dragging
  points (double-click the block), with a table and a text view.
- Properties panel: clicking in (or hovering over) a field shows what can be written there, with examples (e.g.
  Sum *signs*: `+-` subtracts the second input), the unit, the range and the default.
- Graphical chart editor: double-click a State Chart block to edit states, transitions, priorities and the
  chart's interface as a diagram (with a synchronised text view); the layout is saved in the model.
- MISRA C:2012: the generated code follows it (checked with cppcheck's MISRA addon for every block template);
  `nervus.codegen.misra.misra_check` and a MISRA check button in the code generation dialog.
- FMI 2.0: Code ▸ Export FMU (co-simulation, from the generated C code) and the FMU block (import, with fmpy:
  extra `fmi`). Example `fmi_exchange.py` and tutorial.
- Results can be read by a subsystem's name (`res["ECU"]`, `res["ECU:1"]`). Tutorial on reusable components
  (subsystems, masks and libraries).
- Blocks: Variable Resistor (resistance from a signal) and Thermistor (NTC, β model). Example `ntc_sensor.py` and
  tutorials: NTC sensor interface, sensor fault diagnosis (P0117/P0118), on-board diagnostics.
- Tutorials grouped by topic (a registry in `nervus.tutorials` generates the overview, navigation and help
  list). New tutorials: mass-spring-damper, DC motor, coupled tanks and inverted pendulum (control lab
  experiments), engine operating modes, radiator fan, from model to ECU C code, fixed point, and live calibration
  over XCP (`examples/xcp_tool.py`, a command-line calibration tool).
- CAN Bus block: the `udp_multicast` interface connects several programs on one computer (needs msgpack, now in
  the `can` extra).
- State Charts: nested and parallel states, history, events (`send`), transitions across levels; generated C
  matches the simulation.
- Requirements traceability: a Requirements tab (CSV import, HTML report), links from blocks (right-click ▸
  Requirements…) and test cases, status per requirement from the test results, and a Model Advisor rule.
- Algebraic loop solver (Simulation ▸ Settings ▸ Algebraic loops = solve): loops are torn and solved with
  Newton's method at every evaluation, reported as warnings; code generation refuses them.
- Tutorial 1, *your first model*: a cruise control built step by step in the editor, with screenshots
  (`examples/cruise_control.py`); a tutorial overview with learning paths; every tutorial now starts with what
  you will learn, what to do first and how to follow along, and ends with a link to the next one. The app's
  tutorial pages are generated from the docs.
- README and docs home list all features.
- Data dictionary: signal objects (type, unit, range, description) bound to named block outputs (*Name
  Signal…* on a wire); type checks, range warnings, results by signal name, named A2L measurements.
- Scripting console (View ▸ Console): Python with the model, the results and the block classes in scope;
  changes to the model are one undo step.
- MDF4 import and export (`nervus.mdf`, extra `mdf`): Data Inspector *Load…* reads `.mf4` measurements,
  *Export* writes them.
- XCP on CAN (`nervus.xcp`): the XCP Slave block makes a running model measurable and calibratable by INCA,
  CANape or `XcpMaster`; `XcpMaster` reads and writes a real ECU by A2L name; `read_a2l`.
- CAN FD frames (up to 64 bytes, bit-rate switching on the bus), multiplexed signals (held when not sent),
  value tables in the trace, and Model Workspace ▸ Import DBC (signals and value tables as variables); pack and
  unpack C code now extracts runs of bits, for any frame length. Example DBC `examples/data/chassis.dbc`.
- Simulated CAN buses (`nervus.canbus`): CAN Transmit / Receive / Bus / Rest Bus / Gateway blocks with
  arbitration, stuff-bit timing, bus load, fault injection (lost, delayed, corrupted frames, bus-off), E2E
  Protect / Check (CRC-8 and alive counter, with C code), Tools ▸ CAN Bus Trace with ASC/BLF export, and real
  interfaces through python-can. Example `can_network.py` and tutorial.
- Relative file paths (DBC files) inside subsystems now resolve while a model file is loading.
- Example `truck.py` and tutorial: 40 t truck with heavy-duty diesel, 12-speed AMT and exhaust brake.
- The official WLTC class 3b (UNECE GTR 15) and FTP-75 (US EPA) speed tables, `nervus.cycles`, and the Drive
  Cycle block (speed and acceleration); the drive-cycle example runs them with `build("wltc3b")`.
- Adaptive Map block (a learning 2-D table, with C code); example `fuel_film.py` and tutorial: wall wetting
  and x-tau compensation, jump-and-ramp lambda control, long-term trims per load cell.
- Example `ignition_knock.py` and tutorial: spark advance map, knock sensor and knock control.
- Sensor Model and Actuator Model blocks: gain/offset errors, reproducible noise, delay, quantization, range,
  rate limit, and injected faults (stuck, open circuit, short to supply, drift).
- Example `traction_control.py` (tyre slip, magic formula, TCS ECU) with a tutorial; a BSFC fuel map in
  `gear_shift.py` (fuel consumption per gear in the tutorial).
- Examples `starter_motor.py` and `electronic_throttle.py` with a tutorial; the PID tuner linearizes around
  zero controller output and cancels unobservable pole-zero pairs (`minreal`).
- Automotive electrics (solenoid, fuel injector, relay, glow plug, alternator) and hydraulics (tank, pump,
  pressure source, orifice, relief valve, volume, sensors) on the physical network solver.
- Example `circuits.py` and tutorial: rectifier (from a netlist), relay driver with freewheeling diode, buck
  converter, Sallen-Key filter.
- SPICE-like electronics: Shockley diode (Zener, LED), BJT, MOSFET and op-amp solved with Newton-Raphson
  (junction limiting), E/G controlled sources, sparse solving for big circuits, `nervus.circuit` operating
  point and DC sweep, SPICE netlist import/export (File menu, `nervus.spice`).
- Drawing circuits in the editor: physical ports in domain colours, links dragged between them (undo,
  delete, context menu), Connection Ports for physical ports on subsystems, symbols for physical blocks.
- Physical modelling: electrical, rotational and thermal components with physical ports joined by links
  (`Diagram.link`), solved by modified nodal analysis together with the block diagram: R, C, L, sources,
  switch, diode, battery, DC motor, H-bridge, inertia, spring, damper, gear, thermal mass and resistance,
  sensors.
- Tools → Record from OBD-II…: record PIDs from an ELM327, a CAN interface or a simulated ECU with live
  plots; save the drive or open it in the Data Inspector.
- A simulated OBD-II ECU (`nervus.obd_sim`) answering the ELM327 protocol (also on a pseudo-terminal) and
  CAN; the OBD Responder block feeds it from a model; `python -m nervus.obd simulate`; example `obd_ecu.py`.
- OBD over CAN without an ELM327 (`nervus.obd_can`, ISO 15765-4 with ISO-TP); `--can-interface` in the CLI.
- Diagnostic Monitor block: debounced trouble codes with pending/confirmed (two-trip) status, MIL and
  healing over operation cycles, readiness, clear, and the UDS status byte; generates C code.
- Linear Analysis: Nyquist and root-locus tabs (with poles and zeros), the closed-loop step response, and PID
  Tuning: the plant seen by a PID block (`plant_at`) and loop-shaping gains (`tune_pid`) with Apply to Block.
- Analysis → Parameter Sweep / Monte Carlo: vary workspace variables, overlay a signal of every run, plot a
  metric against the parameters or as a histogram, export the table; `nervus.sweep.metric()` and `run_points()`.
- Parameter fields complete workspace and mask variable names; Model Workspace → Find Usages lists where a
  variable is used (`Diagram.usages()`).
- Scopes open when a simulation starts and plot live while it runs; simulation pacing (Simulation Settings)
  runs a model no faster than a multiple of real time.
- Wires are routed around blocks; wire segments can be dragged (manual corners, saved with the model, moving
  a branch point moves all its wires), and right-click → Reset Route returns to automatic routing.
- Editor: rotate blocks (Ctrl+Shift+R, vertical signal flow), resize them with a corner grip, and move them
  with the arrow keys; wires route into ports on the top and bottom of rotated blocks.
- View → Show Signal Widths and Sample Time Colours: vector wires labelled and drawn thicker; blocks and
  wires coloured by sample time, with a legend in the status bar.
- Example `gear_shift.py` and tutorial: automatic-gearbox shift logic (shift maps, kickdown, hysteresis)
  on a car with engine, gearbox and road grade; the TCU generates C code.
- Initial Condition block (Signal Attributes): a defined output in the first step, then the input; in C too.
- Interpolation using Prelookup handles 3-D and 4-D tables (n-D maps), in simulation and C.
- Integrator: *initial_source* `external` adds an **x0** input (start and reset value from a signal);
  *saturation_port* adds an output that shows when the integrator is at a limit.
- Blocks can declare which inputs feed through (`feedthrough_ports()`), and that they are never constant.
- State charts: `param` declarations read calibration parameters from the workspace or a mask; in C they are
  calibration parameters (typed, in the A2L file).
- Example `engine_modes.py`: engine operating modes with scenario test cases and C code.
- Model outputs are available in results under the name of their Out block (`res["fuel"]`).
- Function-call subsystems: *trigger* = `function_call` runs the subsystem once per change of the trigger
  value, e.g. a call counter incremented by a State Chart; identical C code.
- Continuous Integrator: *external_reset* input (rising, falling, either, level). Variable-step solvers stop
  exactly at the reset edge and restart from the initial value. Blocks can make their state jump at a major
  step (`Block.major_step`).
- Workspace variables have a data type (`int16`, `fixdt(1,16,7)`, ...) and a calibratable flag (Type and Cal.
  columns). Typed parameters are stored as integers in C and scaled where they are read (scalars, lookup
  tables, breakpoints); the simulation uses the stored values (bit-true); the A2L file has the matching record
  layouts and linear conversions. Non-calibratable variables become constants in ROM (`const <name>_C`).
- C code with one function per task for an RTOS (`generate_c(..., tasks=True)`, a checkbox in the Generate C
  dialog), an RTOS `main_example.c`, and the Rate Transition block (fast_to_slow, slow_to_fast) that makes data
  transfer between tasks deterministic; missing transitions are reported. Blocks can declare the rate their
  outputs pass on (`output_sample_time`).
- Test suites as `.nervtest` files (Test Manager: Export…/Import…), an HTML test report (Report…), and
  parameter sweeps and Monte Carlo runs from Python (`nervus.sweep`).
- Prelookup and Interpolation using Prelookup blocks (1-D/2-D maps over shared axes), with C code.
- Documentation: a feature overview in the README, the docs home page and the in-app Getting started page;
  in-app pages *Concepts*, *Coming from Simulink* (rewritten: masks, variants, libraries, tasks, codegen,
  verification) and *Model files*; the GitHub editor page now has all sections of the in-app one; calibration
  maps over shared axes; all examples listed. Tests keep the Help window, the GitHub navigation and the
  example lists complete.
- Discrete-Time Integrator: *external_reset* input (rising, falling, either, level); identical C code.
- Port tooltips show input names (e.g. "input 2: reset (rising)").
- Flip blocks (Ctrl+Shift+F): inputs on the right and outputs on the left, for feedback paths; saved with the model.
  The lambda and drive-cycle examples use it.
- Example and tutorial: a car with a driver model on the ECE-15 urban drive cycle (driver and vehicle
  subsystems, road loads, wheel energy, effect of mass).
- Right-click menus on blocks, subsystems, wires and the canvas (rename, open, mask, variants, library, code,
  delete wire, layout, ...).
- Example and tutorial: lambda closed-loop control with short- and long-term fuel trims (vacuum leak, clogged
  injector), with the link to the OBD-II fuel trim PIDs.
- Atomic subsystems: a subsystem *sample_time* makes it a task; inherited blocks inside run at that period,
  inputs are sampled and outputs held; the period is shown on the block; multirate C code.
- OBD-II: read stored, pending and permanent trouble codes (with descriptions of common codes), freeze frame
  values and the VIN; `python -m nervus.obd dtc`. Clearing codes is deliberately not supported.
- Example `engine_library.py`: a mean-value engine and a throttle actuator as masked library components,
  used twice in `engine_comparison.nervus` to compare a 1.6 l and a 2.0 l engine.
- Python Function blocks have a *parameters* vector (`p[0]`, `p[1]`, ...) that can use workspace and mask
  variables.
- Component libraries: `.nervlib` files whose subsystems are reusable components; linked copies are updated
  from the library when a model opens, keep their own mask values, are read-only inside, and fall back to the
  saved copy when the library is missing. Library panel, Save Subsystem to Library, Break Link, Update
  Library Links.
- Variant subsystems: alternative implementations chosen by conditions on workspace variables (or by hand);
  Variants dialog, active variant in the Properties panel and on the block; saved in model files.
- Expressions support comparisons (`==`, `!=`, `<`, ...) and `and`/`or`/`not`, also in MATLAB spelling
  (`~=`, `&&`, `||`, `~`).
- Triggered subsystems: a trigger input (rising, falling or either edge) runs the subsystem only on edges;
  works together with the enable input; edge detection in generated C matches the simulation.
- OBD-II (read-only): decoding of 21 standard mode 01 PIDs, an ELM327 client and `python -m nervus.obd record`
  to record a drive into a `.nervres`/CSV file for the Data Inspector and test inputs (optional extra `obd`).
- Roadmap: OBD-II diagnostics (Phase 22).
- Enabled subsystems: an enable input gates every block inside (outputs and states held, optional reset when
  enabled again), decided at major steps; gated C code.
- Roadmap: electrical/physical modelling (Phase 20) and CAN bus networks (Phase 21).
- Masked subsystems: mask parameters (values or expressions of workspace variables, nested masks), icon text
  and description; Edit Mask dialog and mask parameters in the Properties panel; per-instance values in C.
- Model coverage (`nervus.coverage`): decision outcomes of switches, comparisons, saturations, limits, relays,
  rate limiters, lookups and charts; `simulate(coverage=...)`, `run_tests(coverage=...)`, HTML report; the Test
  Manager shows coverage and opens the report.
- Model Advisor (`nervus.advisor`, Analysis → Model Advisor) with 10 rules for ECU modelling practice.
- CAN: DBC files (optional `cantools`), CAN Pack and CAN Unpack blocks (Intel/Motorola, signed, factor/offset,
  rounding and saturation), bit-exact C code; file pickers and message suggestions in the editor.
- Data types: double, single, boolean, int8…uint32 and fixed point (`fixdt`), with rounding and overflow modes;
  type propagation; bit-true simulation; Data Type Conversion block; `output_type` on In, Constant, Gain, Sum,
  Product, Bias and lookup tables; View → Show Data Types.
- Generated C: typed input/output structs and quantization helpers matching the simulation bit for bit.
- Example: fixed-point pressure sensor chain compared with double.
- Linear analysis (`nervus.analysis`): linearize between In and Out blocks around initial or trimmed
  states, transfer function, poles, DC gain, Bode, gain/phase margins, step response; Analysis → Linear
  Analysis dialog (Ctrl+L).
- Example and tutorial: idle speed control on a mean-value engine model (throttle flow, manifold, cylinder
  flow, torque, crankshaft) with PI control, AC feed-forward, requirement tests and C code.
- A2L (ASAM MCD-2 MC) file with every C code generation: parameters as CHARACTERISTICs with units/limits from
  the workspace, signals as MEASUREMENTs; `fill_addresses()` resolves addresses from the built binary.
- State Chart block: state machines with entry/during/exit actions, prioritised transitions with guards and
  actions, local variables, `after(n)` and `t_in_state`; written in a small text language, evaluated safely,
  and generated as C.
- Zero-crossing detection for variable-step solvers: discontinuous blocks freeze their mode during
  integration and events are located exactly; a chattering guard reports a warning. A clamped continuous PID
  that took 33 s now takes 0.1 s. Toggle with `settings.zero_crossing`.
- Stop Simulation and Assertion blocks.
- Test cases saved with the model (schema version 4): input signals for top-level In blocks, parameter
  overrides or a parameter-set file, stop time, and checks (`bounds`, `final_value`, `reaches`, `max`, `min`,
  `baseline`). `nervus.testing.run_tests()` runs them.
- `simulate(..., inputs={...})` drives top-level In blocks with `Signal`s (steps, ramps, CSV).
- Results: `SimResult.save()/load()` (`.nervres`), `to_mat()`, and `compare_results()`.
- Editor: Test Manager panel (background test runs, pass/fail, undoable edits) and Data Inspector (overlay and
  compare runs, differences, save/load/export).
- The throttle_ecu example stores three requirement test cases.
- C code generation (`nervus.codegen.generate_c`) for models and subsystems: `<name>.h`/`<name>.c` with
  `_initialize()`/`_step()`, static memory, input/output/parameter/state structs, shared calibration
  parameters for workspace variables, base-rate multi-rate scheduling, a `main_example.c` template and an
  HTML report with block-to-code traceability. Templates for 38 block types.
- Software-in-the-loop back-to-back testing (`nervus.codegen.sil`): compiles the generated code and compares
  it with the Python model step by step; tested for every block template.
- Editor: Code → Generate C Code (Ctrl+B) with a code viewer, SIL check, save and report.
- Example `throttle_ecu.py` with its generated code committed in `examples/generated/ECU`.
- Subsystems: Subsystem, In and Out blocks; hierarchies are flattened at compile time; results and errors use
  path names (`ECU/throttle_pi`); model file schema version 3 stores nested systems.
- Editor: open subsystems by double-click, breadcrumb navigation, Up to Parent, Create Subsystem from
  Selection (Ctrl+G), Auto Layout (Ctrl+Shift+L); undo works across levels.
- ECU block library: 1-D and 2-D Lookup Tables (linear/nearest/flat, clip/linear extrapolation), Relational
  Operator, Compare To Constant, Logical Operator, Dead Zone, Quantizer, Relay (hysteresis), Rate Limiter,
  Integer Delay, Counter, Edge Detect, Debounce, Discrete Filter (PT1), Moving Average, MinMax, Bias, Ground,
  each with equations, figures and help.
- Example: radiator fan control with sensor filtering, hysteresis and a lookup table.
- Model workspace: named variables (value, unit, min/max, description) stored in the model; block
  parameters can be expressions of them (`Gain(gain="Kp")`, `"1/tau"`, `sample_time="Ts"`).
- Parameter sets (`.nervcal`): save, load and apply calibrations; `simulate(..., params={...})` overrides.
- Model Workspace panel in the editor, with undo and parameter-set load/save.
- Model file schema version 2 (adds `workspace`); version 1 files are migrated automatically.
- Desktop editor (`nervus` command), built with PySide6 in a Model–View–Controller structure: canvas with
  drag-and-drop blocks and wires, block library with search, property editor with expressions and MATLAB-style
  matrices, undo/redo for every edit, copy/paste, diagnostics panel, threaded simulation with progress and stop,
  scope windows with hover readout, simulation settings dialog, recent files, light and dark themes.
- In-app help: user guide pages and the full block reference (F1 on a selected block), with typeset LaTeX
  equations and explanatory figures.
- Block figures (`scripts/gen_figures.py`) and editor screenshots (`scripts/screenshots.py`) for the docs.
- LaTeX equations in every block's documentation.
- Automatic left-to-right layout for diagrams without block positions.
- Simulation engine: block base class, diagrams, width propagation, execution ordering, algebraic loop
  detection, sample-time inheritance, multi-rate discrete scheduling.
- Solvers: fixed-step Euler and RK4; variable-step RK45, RK23, DOP853, Radau, BDF and LSODA (via SciPy).
  Variable-step solvers stop exactly at discontinuities declared by blocks.
- Simulation results with access by block name, CSV and pandas export.
- Model files (`.nervus`, JSON) with schema versioning and migrations; simulation settings stored per diagram.
- Examples: PID control of a mass-spring-damper, electronic throttle control with a discrete PI.
- Documentation: getting started, concepts, coming from Simulink, model files, architecture, writing blocks,
  and a block reference generated from the code.
- Block library (30 blocks): Constant, Step, Ramp, Sine Wave, Pulse Generator, Clock, From Workspace, Gain,
  Sum, Product, Abs, Saturation, Math Function, Integrator, Derivative, Transfer Fcn, State-Space, Unit Delay,
  Zero-Order Hold, Discrete-Time Integrator, Discrete Transfer Fcn, PID Controller, Discrete PID Controller,
  Scope, To Workspace, Terminator, Mux, Demux, Switch, Python Function.
- Project setup: packaging (uv/hatchling), linting (ruff), type checking (mypy), tests (pytest), CI, docs skeleton.
