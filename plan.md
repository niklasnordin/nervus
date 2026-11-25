# Nervus — Project Plan

> **Nervus** — Latin for "nerve". GitHub: `ikjbi/nervus`, Python package: `nervus`.

An open-source, Python-based block-diagram modeling and simulation tool (a Simulink alternative). The goal is
model-based design of control software for cars and trucks: model it, simulate it, test it, then generate C code
for an ECU.

Check items off (`- [x]`) as they are done. Add new items and phases whenever we find something we need.

---


The releases these items are grouped into are on the [roadmap to 1.0](docs/roadmap.md).

## Guiding principles

- **Headless engine first.** The simulation engine is pure Python with no Qt imports, so it can be used from
  scripts, tests, Jupyter and CI. The GUI sits on top of it.
- **MVC in the GUI.**
  - **Model:** the diagram domain model (blocks, ports, connections, parameters) plus the simulation results.
    It emits change notifications and knows nothing about Qt widgets.
  - **View:** PySide6 widgets and the `QGraphicsScene` canvas. It renders the model and forwards user input.
    It holds no business logic.
  - **Controller:** turns user intent into model changes as undoable commands (`QUndoStack`), and runs
    simulations.
- **Correctness is verified.** Every block and solver is tested against analytical solutions or reference data.
- **Built with codegen in mind.** Blocks have a clear `outputs` / `update` / `derivatives` split, explicit sample
  times, explicit data types and no hidden state. These are the same properties C code generation needs later.
- **Readable, git-friendly model files.** Models are JSON with a schema version, so diffs and code review work.
- **Documented as we go.** Every user-visible feature ships with docs and in-app help. Every change is committed
  and pushed.

## Proposed layout

```
src/nervus/
  core/        # Block base class, ports, signals, diagram graph, sample times, data types
  solvers/     # Fixed-step (Euler, RK4) and variable-step (scipy) solvers, zero-crossing detection
  sim/         # Simulator: sorting, algebraic loop detection, execution loop, logging/results
  blocks/      # Block library (sources, sinks, math, continuous, discrete, lookup, logic, ...)
  io/          # Model file (JSON) load/save, schema versioning and migration
  codegen/     # (later) C code generation
  gui/
    models/       # Qt-facing model adapters / signals around core diagram
    views/        # Main window, canvas, palette, property editor, scope windows
    controllers/  # Commands (undo/redo), simulation controller, file controller
    resources/    # Icons, stylesheets (light/dark theme)
docs/          # User guide, block reference, developer docs (MkDocs)
examples/      # Example models (.json) and scripts
tests/
```

---

## Phase 0 – Project setup
- [x] Choose the project name (**Nervus**), then rename the folder and create the private GitHub repo `ikjbi/nervus`
- [x] `git init`, `.gitignore`, MIT `LICENSE`, first commit and push
- [x] `pyproject.toml` (src layout, Python ≥ 3.11) with `uv` for environments
- [x] Tooling: `ruff` (lint and format), `mypy`, `pytest`, `pre-commit`
- [x] GitHub Actions CI: lint, type-check and tests on every push and PR
- [x] `README.md` (what it is, install, quick start), `CONTRIBUTING.md`, `CHANGELOG.md`
- [x] Docs skeleton with MkDocs Material
- [x] Publish the docs to GitHub Pages: https://ikjbi.github.io/nervus/ (workflow docs.yml, on every docs change)

## Phase 1 – Core simulation engine (headless)
- [x] `Block` base class: input and output ports, parameters, states, `outputs()`, `update()` (discrete),
      `derivatives()` (continuous), direct-feedthrough flag, sample time
- [x] Signals: scalar and vector (NumPy), with basic data types (`double` first)
- [x] `Diagram`: blocks plus connections, with validation (unconnected ports, fan-in, type and size mismatch)
- [x] Execution ordering: topological sort over direct-feedthrough dependencies
- [x] Algebraic loop detection, with clear error messages
- [x] Fixed-step solvers: forward Euler, RK4
- [x] Variable-step solver via `scipy.integrate.solve_ivp` (RK45 / LSODA)
- [x] Sample times: continuous, discrete (period/offset), inherited, constant; multi-rate scheduling
- [x] Simulation results: logged signals, time vector, export to NumPy / pandas / CSV
- [x] Python API: build and run a diagram entirely in code

## Phase 2 – Core block library (~15 blocks)
- [x] Sources: Constant, Step, Ramp, Sine, Pulse, Clock, From Workspace (array or CSV)
- [x] Math: Gain, Sum (configurable signs), Product, Abs, Saturation, Math Function
- [x] Continuous: Integrator (with limits and reset), Derivative, Transfer Function, State-Space
- [x] Discrete: Unit Delay, Zero-Order Hold, Discrete Integrator
- [x] Control: PID (continuous and discrete; anti-windup by clamping or back-calculation)
- [x] Sinks: Scope (logging), To Workspace, Terminator
- [x] Routing: Mux, Demux, Switch
- [x] Python Function block (user-defined code, the equivalent of MATLAB Function)

## Phase 3 – Model file format
- [x] JSON schema: blocks, parameters, positions, connections, solver settings, schema version
- [x] Save and load round-trip tests
- [x] Schema migration hook for future versions
- [x] Block registry so files reference blocks by type id

## Phase 4 – Verification and first example
- [x] Tests against analytical solutions (first and second order systems, integrator, step responses)
- [x] Solver accuracy and convergence tests
- [x] Example: PID-controlled mass-spring-damper (script and `.json` model)
- [x] Example: first-order throttle actuator with a discrete PI controller (10 ms ECU task)

## Phase 5 – GUI MVP (PySide6, MVC)
- [x] App shell: main window, menus, toolbar, status bar, modern light and dark theme (follows the system)
- [x] Canvas (`QGraphicsView`): place, move, select and delete blocks; zoom and pan (wheel, pinch, trackpad,
      Space+drag); grid snapping; automatic layout for models without positions
- [x] Wiring: drag from port to port (either direction), orthogonal routing, branch dots on fan-out
- [x] Block palette (library browser) with search, drag and drop onto the canvas
- [x] Property editor panel for block parameters, with validation and expressions (`2*pi`, `[1 2; 3 4]`)
- [x] Undo and redo for every edit (`QUndoStack` commands in controllers); copy / cut / paste
- [x] Run, stop and solver settings dialog; simulation runs off the UI thread
- [x] Scope windows with post-run plots (pyqtgraph), hover readout
- [x] Live scope updates while the simulation runs (and simulation pacing: real time or a multiple of it)
- [x] File: new, open, save, save as, recent files
- [x] Error and diagnostics panel (algebraic loops, unconnected ports), with click to highlight the block
- [x] Manual wire routing (drag segments, waypoints) and draggable branch points
- [x] Obstacle-avoiding automatic wire routing (A* around the blocks when the direct route is blocked)
- [x] Auto Layout command (left-to-right by signal flow), undoable
- [x] Flip blocks (Ctrl+Shift+F) for feedback paths; the wire router handles flipped ports
- [x] Rotate blocks (vertical signal flow), resize blocks, nudge with arrow keys
- [x] Show data types on the canvas (View → Show Data Types)
- [x] Show signal widths and sample-time colours on the canvas (like Simulink's "port data types" / "sample time colors")
- [x] Right-click context menus on blocks, wires and the canvas

## Phase 6 – Documentation and in-app help
- [x] User guide (Python API): getting started, concepts, coming from Simulink, model files
- [x] User guide (GUI): the editor, with screenshots generated by `scripts/screenshots.py`
- [x] Block reference, generated from block docstrings and parameter metadata
- [x] In-app help: F1 or the "?" button on a block opens its reference page; tooltips on parameters
- [x] Typeset LaTeX equations in the in-app help (matplotlib mathtext) and in the docs (MathJax)
- [x] Explanatory block figures (`scripts/gen_figures.py`), shown in the app and in the docs
- [x] Developer docs: architecture (engine and MVC), how to write a custom block
- [x] Tutorials that follow the examples (idle speed, lambda, drive cycle, gear shift, circuits, starter motor and throttle)

## Phase 7 – Parameters, model workspace and calibration data
*ECU software is "code + calibration". Parameters must live outside the blocks, as named, tunable values.*
- [x] Model workspace: named variables (`Kp = 1.5`, `fuel_map = [...]`) stored in the model file
- [x] Block parameters can be expressions using workspace variables (`Kp`, `2*Kp`, `1/tau`)
- [x] Variables carry metadata: description, unit, min/max (for calibration and A2L later)
- [x] Parameter sets ("calibrations"): save/load named sets of variable values as separate files and switch
      between them (e.g. `petrol.nervcal.json` vs `diesel.nervcal.json`)
- [x] GUI: Model Workspace panel to add/edit/delete variables; parameter fields accept and show expressions
- [x] Autocomplete variable names in parameter fields; "find usages" of a variable
- [x] Python API: `diagram.workspace["Kp"] = 2.0`, `simulate(d, params={...})` overrides

## Phase 8 – ECU block library
*The blocks that real engine/vehicle control software is built from.*
- [x] Lookup tables: 1-D and 2-D (n-D later) with linear / nearest / hold interpolation and clip/linear
      extrapolation. Calibration maps (fuel, ignition, boost) are 2-D tables
- [x] Relational Operator (`<`, `<=`, `==`, `!=`, `>=`, `>`), Logical Operator (AND, OR, NOT, XOR, NAND, NOR),
      Compare To Constant
- [x] Relay (hysteresis), Dead Zone, Rate Limiter (discrete), Quantizer, Sign, MinMax, Bias
- [x] Integer Delay (z⁻ⁿ), Memory, Counter (free-running / limited), Edge Detect (rising/falling/either),
      Debounce (signal must hold for N samples), Discrete Filter (first-order low-pass), Moving Average
- [x] Ground, MinMax, Bias
- [x] Data Type Conversion; Prelookup + Interpolation using Prelookup (1-D and 2-D, with C code)
- [x] Initial Condition block; lookup tables with more than 2 dimensions (Interpolation using Prelookup, up to 4-D)
- [x] Figures, equations and help for every new block

## Phase 9 – Hierarchy: subsystems, masks, conditional execution, variants
- [x] Subsystem block with Inport/Outport blocks; flattened at compile time
- [x] GUI: double-click to open a subsystem, breadcrumb navigation, "Create Subsystem from Selection"
- [x] Atomic subsystems with their own sample time (one ECU task = one atomic subsystem)
- [x] Masks: parameter dialog and icon text for a subsystem; mask parameters as local workspace (nested, C code)
- [x] Enabled subsystems (held or reset states; C code)
- [x] Triggered subsystems (rising/falling/either edge, combined with enable; C code)
- [x] Function-call subsystems (trigger = function_call: one run per change of a call counter, e.g. from a chart)
- [x] Variant subsystems (conditions on workspace variables, default variant, Variants dialog, C code of the active variant)
- [x] Reusable component libraries (`.nervlib` files, linked subsystems updated on load, per-instance mask values)

## Phase 10 – Test cases, scenarios and results
*"Save/load cases": reproducible simulations you can rerun and compare.*
- [x] Test case = name + input scenario + parameter set + stop time + checks, saved with the model
- [x] Export/import test suites as separate files (`.nervtest`)
- [x] Scenario inputs: piecewise signals (hold/linear), CSV replay
- [x] Graphical signal editor (Signal Builder-like): a Signal Builder block with groups (scenarios), edited by
      dragging points, in a table or as text
- [x] Save/load simulation results (`.nervres`, compressed NPZ) and export CSV / MAT
- [x] Import/export ASAM MDF4 (`.mf4`, via `asammdf`), the format of INCA/CANape vehicle logs
- [x] Run several cases in batch
- [x] Parameter sweeps and Monte Carlo runs (`nervus.sweep`); HTML test report
- [x] Sweep and Monte Carlo dialog in the editor, with result plots
- [x] Simulation Data Inspector: overlay and compare runs, with differences and tolerance checks
- [x] GUI: Test Manager panel (create, run, pass/fail), compare-runs window
- [x] Python API for all of the above (usable from pytest for regression tests)

## Phase 11 – C code generation
- [x] Restrict to fixed-step, discrete-time models (clear errors otherwise; continuous plants stay in
      simulation)
- [x] Generate `model.h` / `model.c` with `model_initialize()` and `model_step()`, static memory only, no malloc
- [x] Structs for inputs, outputs, states and parameters; parameters as `const` or as calibratable globals
- [x] Per-block C templates for all discrete/stateless blocks, including lookup tables
- [x] Multi-rate: a base-rate scheduler with rate counters
- [x] Multi-rate: one step function per rate (for RTOS tasks), with rate transition blocks
- [x] Software-in-the-loop (SIL): compile the generated C, run it via ctypes, and compare it with the Python
      simulation (back-to-back test)
- [x] Code generation report (HTML): block-to-code traceability, memory use
- [x] Generated code follows MISRA C guidelines (checked with cppcheck's MISRA addon; three documented
      deviations); compiles with `-Wall -Wextra -Werror -pedantic`
- [x] Generate code for blocks with offsets exactly like the simulation's first step
- [x] Optional `const` parameters (ROM) instead of calibratable RAM (variable not calibratable)
- [x] Reusable functions for subsystems instead of inlining (function_name: one function, one call per instance
      with its own data and its own A2L characteristics)
- [x] GUI: Code → Generate C Code, with a report and "run SIL comparison"
- [x] Example: an ECU-style project folder with a `main.c` that calls `model_step()` every 10 ms

## Phase 12 – Zero crossings, events and solver robustness
- [x] Zero-crossing detection with frozen modes (Saturation, Switch, Abs, Dead Zone, comparisons, Integrator
      limits, PID limits/clamping), direction-aware events and a chattering (Zeno) guard
- [x] Discrete-Time Integrator external reset (rising/falling/either/level), with C code
- [x] Continuous Integrator external reset (rising/falling/either/level, exact with zero crossings, solver restart)
- [x] Integrator saturation output port and external initial condition
- [x] Algebraic loop solver (optional), with warnings
- [x] Clamped continuous PID at its limit: sliding mode with a boundary layer (it chattered, or stayed clamped too
      long); blocks are told when the Zeno guard switches them to `sliding`; no derivative-filter dynamics with D = 0
- [x] Simulation stop block and assertions
- [x] First performance pass: precomputed execution tables, scalar fast paths (~25 % faster)
- [x] Second performance pass (10-25 % faster): physical networks assemble in plain lists and solve once per
      time point, cheaper sources and integrators; `scripts/benchmark.py` times the examples
- [ ] Performance: avoid per-step Python overhead (vectorised execution, Numba, or simulate the generated C:
      "accelerator mode")

## Phase 13 – Data types and fixed-point (required for ECU code)
- [x] Integer types (int8/16/32, uint8/16/32), boolean, single
- [x] Fixed-point with slope and bias scaling, overflow and saturation modes, rounding
- [x] Data type propagation and checking across the diagram
- [x] Simulation that matches the integer behaviour of the generated code bit for bit
- [x] Data dictionary: signal and parameter objects with type, unit, range, description
- [x] Integer-only arithmetic in generated code (no floating point; binary-point scaling; the blocks of integer
      ECU code; bit-true back-to-back)
- [x] Typed parameters (e.g. int16 calibration maps) and typed A2L characteristics

## Phase 14 – State machines
- [x] State chart block (a Stateflow-like subset): states, transitions, guards, entry/during/exit actions,
      locals, `after(n)`, `t_in_state`, defined in a small text language
- [x] Hierarchical (nested) and parallel states, events, history junctions
- [x] Graphical state-chart editor (boxes and arrows, property panel, synchronised text view, undoable; the
      layout is saved in the model)
- [x] C code generation for charts (back-to-back tested with zero tolerance)
- [x] Example: engine operating modes (crank, idle, run, fuel cut-off, stall) with chart `param`s
- [x] Example: gear-shift logic (`examples/gear_shift.py`, with a tutorial)

## Phase 15 – Automotive and ECU learning library
- [x] Mean-value engine model: throttle and intake manifold, air mass flow, torque production, crank dynamics
      (example `idle_speed_control.py`)
- [x] Turn the engine model into reusable library subsystems (`examples/library/engine_parts.nervlib`)
- [x] Fuel control: injector model, lambda (AFR) closed-loop control, short and long term fuel trim
      (`examples/lambda_control.py`, tutorial)
- [x] Fuel control, more: wall wetting (x-tau), narrow-band switching sensor, LTFT maps per load/speed cell
      (`examples/fuel_film.py`, Adaptive Map block)
- [x] Ignition timing from speed/load maps, knock retard (`examples/ignition_knock.py`, tutorial)
- [x] Idle speed control (PI + base opening + AC feed-forward), with tutorial
- [x] Electronic throttle control (DC motor, return spring, PID): `examples/electronic_throttle.py`
- [x] Vehicle longitudinal dynamics: aero and rolling resistance, force and power limits, brakes
      (`examples/drive_cycle.py`)
- [x] Vehicle dynamics, more: driveline with gearbox and shift logic, tire slip, road grade, engine efficiency map
      (`gear_shift.py` with a BSFC map, `traction_control.py`)
- [x] Driver model and the ECE-15 drive cycle (tutorial); other cycles from CSV files
- [x] Bundled WLTP / FTP-75 tables (from the official sources) and a drive-cycle block (`nervus.cycles`, Drive Cycle)
- [x] Sensor and actuator models: quantisation, noise, delay, faults (Sensor Model, Actuator Model blocks)
- [x] Truck-specific models: heavy-duty diesel, exhaust brake, AMT gearbox (`examples/truck.py`)
- [x] Tutorial series: "Learn engine control with Nervus": idle speed, lambda, ignition and knock, throttle,
      gear shift, traction control

## Phase 16 – Control design and analysis tools
- [x] Linearise a model around an operating point (state-space / transfer function)
- [x] Trim: find steady states
- [x] Bode and step plots; poles, DC gain, stability margins
- [x] Nyquist and pole-zero plots, root locus, closed-loop analysis from the model
- [x] PID auto-tuning against a linearised plant (the loop opened at the PID block)
- [x] Scripting console in the GUI (Python, with the model and results in scope)

## Phase 17 – ECU integration and calibration
- [x] CAN: read DBC files (`cantools`), CAN Pack / CAN Unpack blocks, Intel and Motorola, bit-exact C code
- [x] CAN FD, multiplexed signals, value tables, DBC signals as workspace variables
- [x] A2L file generation for calibration tools (measurements and characteristics), addresses from `nm`
- [x] XCP-on-CAN basics for live measurement and calibration (`nervus.xcp`, XCP Slave block)
- [ ] Target: a development board (e.g. STM32 or ESP32 with CAN), as a scheduler template and HAL stubs
- [ ] Processor-in-the-loop (PIL) testing
- [ ] Hardware-in-the-loop (HIL): run the plant model in real time against a real ECU

## Phase 18 – Interoperability
- [x] FMI 2.0/3.0: import FMUs as blocks (`fmpy`), export models as FMUs (FMI 2.0 co-simulation)
- [x] Import a subset of Simulink `.slx` (about 45 block types, masks, subsystems; the rest as empty subsystems in
      a report)
- [x] `.mdl` (text format), read into the same import as `.slx`
- [ ] Stateflow charts in the Simulink import (as State Chart blocks)
- [x] Export results to MAT files (`scipy.io`) for MATLAB users (`SimResult.to_mat`)

## Phase 19 – Quality, testing and release
- [x] Model coverage: decision outcomes of switches, comparisons, limits, lookups and charts; HTML report;
      shown in the Test Manager
- [x] Condition and MC/DC coverage for logic (Logical Operator decisions; the report names the missing
      evaluations)
- [x] Coverage of the generated C by the test cases (gcov: lines and branches; `coverage_of_tests`)
- [x] C coverage button in the code generation dialog; MC/DC for chart guards with and/or/not
- [x] Requirements traceability (link requirements to blocks and tests)
- [x] Model Advisor: 10 rules (float equality, division, anti-windup, implicit sampling, chart reachability, ...)
- [x] Packaging for PyPI: wheel with the examples (`nervus --copy-examples`, File ▸ Open Example), clean source
      package, README links for PyPI, a release workflow with trusted publishing (the first release needs the
      one-time PyPI setup in CONTRIBUTING.md)
- [x] Standalone applications (PyInstaller) for macOS (.dmg), Windows and Linux (.zip), built for every release
- [ ] Signed and notarised applications (an Apple developer account, a Windows code-signing certificate)
- [x] Release notes: each release gets a GitHub Release with its CHANGELOG section; docs on GitHub Pages
- [x] Versioned docs with mike: `dev` from main, one site per minor release (the newest is `latest`)

## Phase 20 – Electrical and physical modelling
*Acausal (equation-based) components connected by physical wires, like Simscape Electrical.*
- [x] Electrical network solver: components with pins, Kirchhoff's laws assembled automatically (modified nodal
      analysis), solved together with the block diagram (DAE)
- [x] Basic components: resistor, capacitor, inductor, voltage and current sources (controlled by signals),
      ground, ideal switch, diode
- [x] Sensors: voltage and current measurement as signals
- [x] Automotive electrics: battery (open-circuit voltage map + internal resistance), DC motor, H-bridge with PWM
      (averaged), solenoid / injector, relay coil, glow plug, alternator
- [x] Throttle body and electronic throttle with a real DC motor and H-bridge instead of a first-order lag
- [x] Other physical domains on the same solver: rotational mechanics (inertia, spring, damper, gear), thermal
      (heat capacity, conduction, convection), hydraulic/fluid basics
- [x] Editor: physical ports and wires drawn differently from signal lines; component library; examples and
      tutorials (battery + starter motor, electronic throttle)

## Phase 20b – Circuit simulation like LTspice
*Bigger electronic circuits with real semiconductor models, the way LTspice is used.*
- [x] Nonlinear devices solved with Newton iteration: Shockley diode (with Zener and LED variants), BJT
      (Ebers-Moll), MOSFET (square law), ideal and macro-model op-amps, comparators
- [x] Analyses: DC operating point, DC sweep, AC small-signal (Bode of a circuit), transient with switching
- [x] Sparse matrices for circuits with hundreds of nodes; time-step control at switching events
- [x] SPICE netlist import/export (`.cir`/`.net`: R, C, L, V, I, D, Q, M, E, G, .model, .tran, .ac, .dc)
- [x] Examples: rectifier with smoothing capacitor, transistor relay driver, buck converter, op-amp filter,
      battery charging; comparison with LTspice results

## Phase 21 – CAN bus networks
*Several ECUs exchanging messages over a simulated bus.*
- [x] CAN bus block: frames with IDs, periods and DLC from DBC files; arbitration by priority; bit timing and bus
      load; transmission delay
- [x] ECU nodes as subsystems with CAN Rx/Tx ports; periodic and event-triggered transmission
- [x] Rest-bus simulation: generate the messages of ECUs that are not modelled, from the DBC file
- [x] Gateways between buses (e.g. powertrain and body CAN)
- [x] Fault injection: lost frames, delays, wrong values, bus-off; checksums and alive counters (AUTOSAR E2E
      style) with C code
- [x] Bus trace view (frames over time) and export to standard log formats (ASC/BLF)
- [x] Connect to real hardware through `python-can` (PEAK, Kvaser, Vector, SocketCAN) for HIL-style tests

## Phase 22 – OBD-II: read data from a real car
*Record live data and trouble codes from a vehicle's OBD-II port and use them in models and tests.*
- [x] Decode standard SAE J1979 mode 01 PIDs (rpm, speed, temperatures, MAF, MAP, throttle, load, fuel trims, ...)
- [x] ELM327 adapter client (USB/Bluetooth serial): initialise, query supported PIDs, poll PIDs
- [x] Record a drive into a result file (`.nervres`) / CSV: replay as test inputs, compare with models in the
      Data Inspector
- [x] Read diagnostic trouble codes (stored 03, pending 07, permanent 0A), freeze frame (02), VIN (09);
      clearing (04) deliberately left out: it resets the emissions readiness monitors
- [x] OBD over a CAN interface (`python-can`, ISO-TP / ISO 15765-4) without an ELM327
- [x] Editor: Tools → Record from OBD-II… with live plots
- [x] OBD responder in models: a simulated ECU that answers OBD requests (to test diagnostic tools): ELM327
      on a pseudo-terminal, or CAN
- [x] Diagnostic logic in models: DTC setting/debouncing, MIL lamp, readiness monitors (Diagnostic Monitor block, with C code)

## Future – Full ECU tooling (INCA-like): measure, calibrate, flash and diagnose a real ECU
To look into later: what it takes to handle an ECU completely, as ETAS INCA (or Vector CANape) does, either inside
Nervus or as a separate app that shares Nervus's XCP, A2L and CAN code. Nervus already has the pieces: an XCP slave
and a command-line XCP tool, A2L export, MDF4 files, CAN with DBC files and OBD-II.
- [ ] Decide: inside Nervus (a "Calibration" workspace) or a separate app sharing the libraries
- [ ] A2L import of any ECU's description (not only Nervus's own), including maps, curves, axes and conversion
      methods (COMPU_METHOD), record layouts and memory segments
- [ ] XCP master over CAN, CAN FD and Ethernet (UDP/TCP), plus CCP for older ECUs; DAQ lists with
      time stamps, several rates, seed and key
- [ ] Measurement windows: oscilloscope, numeric, bar and state displays; recording to MDF4; triggers
- [ ] Calibration editors: values, curves and maps as tables and 3-D surfaces, with working and reference pages
      (switch between them live)
- [ ] Data sets: read and write HEX/S19 files and calibration files (DCM, CDF/CDFX); compare and merge two data sets;
      a history of changes
- [ ] Flashing over UDS (ISO 14229) and the ECU's bootloader: program, verify, checksums
- [ ] Diagnostics over UDS/KWP2000: read and clear trouble codes, read data by identifier, routines
- [ ] Experiments saved as files (the windows, the variables, the layout), and scripting (Python)
- [ ] Existing open-source pieces to evaluate: pyxcp, asammdf, python-can, cantools, canmatrix, udsoncan, pya2l
- [ ] Safety: write-protection, plausibility limits for calibration values, and only on a bench or a test vehicle

## Safety note

Nervus only *reads* from vehicles over OBD-II; it never writes to ECUs. Code generated by this tool is for learning, bench and off-road experiments. Do not run it on a vehicle on public
roads. Production ECU software needs ISO 26262 processes, qualified tools and certified code.

## Decisions log

| Date       | Decision                                                               |
|------------|------------------------------------------------------------------------|
| 2026-11-18 | Python engine + PySide6 desktop GUI, MVC architecture                  |
| 2026-11-18 | MIT license                                                            |
| 2026-11-18 | Headless engine before GUI; JSON model files                           |
| 2026-11-18 | Long-term goal: C code generation for car/truck ECUs                   |
| 2026-11-18 | Blocks are pure functions (outputs/derivatives/update); simulator owns all state |
| 2026-11-18 | Discrete block outputs are computed only at sample hits and held in between |
| 2026-11-18 | Variable-step solvers stop exactly at block breakpoints (Step, Pulse, ...) |
| 2026-11-18 | Frequencies in rad/s and most defaults follow Simulink, to ease moving between the two |
| 2026-11-18 | CI runs on Ubuntu only while the repo is private, to save Actions minutes |
| 2026-11-18 | Every feature ships with in-app help and GitHub docs, with figures/screenshots where useful |
| 2026-11-18 | Block docstrings are the single source for the in-app help and the docs reference |
| 2026-11-18 | Nervus clears the macOS "hidden" flag on Qt plugins at startup (uv/.venv issue) |
