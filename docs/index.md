# Nervus

Nervus is an open-source block-diagram modeling and simulation tool written in Python, as an alternative to
Simulink. It is aimed at control engineering, and especially at engine and vehicle control units (ECUs).

!!! warning "Pre-alpha"
    Nervus is in early development. APIs may change.

## What Nervus can do

| Area | Features | Guide |
|---|---|---|
| Editor | block library with search, drag and drop, wires that route around blocks, undo, copy/paste, subsystems, flip/rotate/resize, auto layout, light and dark theme, F1 help with equations for every block, a Python **console** with the model in scope, **Simulink `.slx` import** | [Editor](editor.md) |
| Simulation | continuous, discrete and multirate models; fixed-step and variable-step solvers (also stiff: BDF); zero-crossing detection; an algebraic loop solver; real-time pacing; live scopes | [Concepts](concepts.md) |
| Parameters and data | model workspace with units, limits and data types; expressions; calibration sets (`.nervcal`); a **data dictionary** of signal objects (type, unit, range) bound to named wires | [Workspace](workspace.md) |
| Structure | subsystems, masks, enabled/triggered/function-call subsystems, atomic ECU tasks with their own period, variants, linked component libraries | [Subsystems](subsystems.md), [Libraries](libraries.md) |
| Logic | State Chart block with a **graphical chart editor**: nested and parallel states, history, events, guards, timers, calibration parameters | [State machines](statecharts.md) |
| Physical modelling | electrical, rotational, thermal and hydraulic networks connected like real parts; batteries, motors, solenoids, injectors, relays, alternator, glow plugs, NTC thermistors | [Physical modelling](physical.md) |
| Electronics | LTspice-style circuit simulation: diodes, BJTs, MOSFETs, op-amps, switches; operating point and DC sweeps; SPICE netlist import and export | [Physical modelling](physical.md), [Circuits tutorial](tutorials/circuits.md) |
| Automotive models | mean-value engine, vehicle and driveline, tyres (magic formula), automatic and automated gearboxes, sensor and actuator models, diagnostic monitors with DTCs, adaptive maps, the official **WLTC and FTP-75 drive cycles** | [Tutorials](tutorials/index.md) |
| ECU code | C code generation (multirate, calibratable parameters, A2L file, HTML report), fixed-point data types, integer-only code for processors without an FPU, software-in-the-loop check, reusable functions for subsystems, MISRA C:2012 check (cppcheck), **FMI 2.0 FMU export and import** | [C code](codegen.md), [Data types](datatypes.md) |
| Vehicle networks | CAN Pack/Unpack with DBC files (also **CAN FD**, multiplexed signals, value tables); **simulated CAN buses** with arbitration, bus load, gateways, rest-bus simulation, fault injection and E2E protection; bus trace with ASC/BLF export; real CAN adapters through python-can | [CAN](can.md) |
| Measurement and calibration | **XCP on CAN**: calibrate a running model from INCA/CANape or Python, or a real ECU from Python; **MDF4** (`.mf4`) import and export | [CAN: XCP](can.md#xcp-live-measurement-and-calibration), [Testing](testing.md) |
| OBD-II | live data and trouble codes from a real car (ELM327 or CAN), and a simulated ECU to practise with | [OBD-II](obd.md) |
| Testing | test cases stored with the model, **Signal Builder** (test signals drawn in an editor), Test Manager, Data Inspector, parameter sweeps and Monte Carlo runs, model coverage with MC/DC, **requirements traceability**, Model Advisor | [Testing](testing.md), [Verification](verification.md) |
| Analysis | linearisation, trim, Bode, Nyquist, root locus, stability margins, step response, PID tuning | [Analysis](analysis.md) |
| Learning | 35 tutorials in 7 topics, from your first model to ECU code, CAN and diagnostics, each with a finished example model | [Tutorials](tutorials/index.md) |

## Where to start

- [Your first model](tutorials/first-model.md): build a cruise control in the editor, step by step.
- [Tutorials](tutorials/index.md): twelve guided examples and learning paths.
- [Getting started](getting-started.md): install Nervus and build a model in Python.
- [Concepts](concepts.md): signals, sample times, solvers and algebraic loops.
- [Coming from Simulink](coming-from-simulink.md): how Simulink terms and blocks map to Nervus.
- [Block reference](blocks/index.md): every block, its equations and parameters.
