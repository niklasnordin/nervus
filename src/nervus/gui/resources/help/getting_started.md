# Getting started

Nervus models a system as a **block diagram**: blocks are small dynamic systems (a gain, an integrator, a
PID controller), and the lines between them carry signals.

New here? **Help ▸ Editor Tour** shows the parts of the editor in a minute, and **Help ▸ Welcome** offers the
first tutorial (a cruise control, built step by step) and the beginner examples, each with its tutorial.

## Build your first model in 5 steps

1. **Add blocks.** Drag a block from the **Library** (left) onto the canvas, or double-click it in the
   library. Add a *Step*, a *Transfer Fcn* and a *Scope*.
2. **Connect them.** Press on a block's **output port** (the small triangle on its right side) and drag to
   another block's **input port** (left side). Releasing on the body of a block connects to its first free
   input.
3. **Set parameters.** Select a block and edit its parameters in the **Properties** panel (right).
   Values accept expressions like `2*pi`, `1/0.05` and matrices like `[1 2; 3 4]`.
4. **Run.** Press **▶ Run** (⌘R / Ctrl+R or F5). The stop time is in the toolbar.
5. **Look at the result.** Scope windows open after the first run. Double-click a Scope block to reopen
   it. Hover over the plot to read values.

## What Nervus can do

Every feature has a page in this Help window (the list on the left):

| Area | Features | Help page |
|---|---|---|
| Editor | block library, wires, undo, copy/paste, right-click menus, flip blocks, auto layout, themes | Using the editor, Keyboard shortcuts |
| Simulation | continuous, discrete and multirate models, solvers, zero crossings, stop and assertion blocks | Running simulations, Concepts |
| Parameters | model workspace, expressions, parameter sets (`.nervcal`) | Workspace and calibrations |
| Structure | subsystems, masks, enabled/triggered subsystems, atomic tasks, variants, component libraries | Subsystems, Component libraries |
| Logic | State Chart block | State machines |
| ECU code | C code, A2L file, fixed-point data types, software-in-the-loop check | C code generation, Data types and fixed point |
| Testing | test cases, Test Manager, Data Inspector, coverage, Model Advisor | Test cases and the Data Inspector, Coverage and Model Advisor |
| Analysis | linearisation, trim, Bode, margins | Linear analysis |
| Vehicle networks | CAN with DBC files; OBD-II live data and trouble codes | CAN communication, OBD-II live data |
| Simulink users | how Simulink names map to Nervus | Coming from Simulink |
| Files | what is stored in a `.nervus` file | Model files |

## Where to go next

- **Using the editor**: selecting, moving, copying and undoing.
- **Running simulations**: solvers, sample times and what to do when something goes wrong.
- **Block reference**: every block, its equation and parameters (below the guides in the list on the left).
  Select a block and press **F1** to jump to its page.
- **Tutorials**: idle speed control, lambda control and fuel trims, and a car on a drive cycle.

Complete example models come with Nervus: **File → Open Example** lists the model of every tutorial by topic, and
*All examples…* opens the folder. To run and change the example *scripts*, copy them to a folder of your own with
`nervus --copy-examples ~/nervus-examples`. The examples:

| Example | What it shows |
|---|---|
| `cruise_control.nervus` | the cruise control built step by step in the tutorial *Your first model* |
| `throttle_pi.nervus` | an electronic throttle controller running as a 10 ms ECU task |
| `throttle_ecu.nervus` | the same controller as an ECU subsystem, with generated C and A2L |
| `dc_motor_speed.nervus`, `dc_motor_position.nervus` | DC motor lab: identification, PI speed and PD position control (tutorial) |
| `coupled_tanks.nervus` | level control of two coupled tanks: a nonlinear process (tutorial) |
| `inverted_pendulum.nervus` | balancing a pendulum on a cart with LQR state feedback (tutorial) |
| `rc_filter.nervus`, `rlc_circuit.nervus` | RC low-pass and series RLC: step and frequency response (tutorial) |
| `mass_spring_damper_pid.nervus` | continuous PID position control with actuator limits |
| `idle_speed_control.nervus` | idle speed control on a mean-value engine model (tutorial) |
| `coolant_fan.nervus` | radiator fan control with hysteresis and a lookup table |
| `fixed_point_sensor.nervus` | a sensor signal path in fixed-point data types |
| `engine_comparison.nervus` | two engines from one library component |
| `lambda_control.nervus` | lambda control with short- and long-term fuel trims (tutorial) |
| `drive_cycle.nervus` | a car with a driver model on the ECE-15 drive cycle (tutorial) |
| `engine_modes.nervus` | engine operating modes as a State Chart: crank, idle, run, fuel cut-off, stall |
| `gear_shift.nervus` | gear-shift logic of an automatic gearbox on a car with a hill (tutorial) |
| `rectifier.nervus` | half-wave rectifier with a smoothing capacitor, from a SPICE netlist (tutorial) |
| `relay_driver.nervus` | a transistor switching a relay coil, with its freewheeling diode (tutorial) |
| `buck_converter.nervus` | a 12 V → 5 V switching regulator (tutorial) |
| `active_filter.nervus` | an op-amp Sallen-Key low-pass filter, 1 kHz (tutorial) |
| `starter_motor.nervus` | cranking: battery, starter relay and motor, compression (tutorial) |
| `electronic_throttle.nervus` | drive-by-wire throttle: H-bridge, DC motor, gear, spring, PID (tutorial) |
| `traction_control.nervus` | tyre slip and a traction control ECU on a wet road (tutorial) |
| `ignition_knock.nervus` | spark advance map and knock control on low-octane fuel (tutorial) |
| `fuel_film.nervus` | wall wetting, switching lambda sensor, fuel trims learned per load cell (tutorial) |
| `truck.nervus` | a 40 t truck: diesel, 12-speed AMT, exhaust brake on a mountain road (tutorial) |
| `can_network.nervus` | two CAN buses, gateway, rest bus, E2E checks and fault injection (tutorial) |
| `xcp_calibration.nervus` | the cruise control with an XCP slave: calibrate it live from `xcp_tool.py` (tutorial) |
| `ntc_sensor.nervus` | a coolant temperature sensor: NTC circuit, ADC, conversion table, P0117/P0118 diagnosis (tutorials) |
| `abs_braking.nervus` | an emergency stop with and without ABS on dry, wet and icy roads (tutorial) |
| `idle_requirements.nervus` | the idle controller with requirements, traceability and coverage (tutorial) |
| `charging_system.nervus` | battery, alternator, loads and electrical energy management on a winter night (tutorial) |
| `ev_range.nervus` | an electric car on the WLTC: consumption, regenerative braking, winter range (tutorial) |
| `injector_driver.nervus` | an injector power stage: opening and closing delays, freewheel diode versus clamp (tutorial) |
| `fmi_plant.nervus` | the throttle plant with the ECU as an FMU (run `examples/fmi_exchange.py` first to build the FMU; tutorial) |
| `obd_ecu.nervus` | an engine ECU with trouble codes that answers OBD-II tools (`python -m nervus.obd simulate`) |
| `discrete_control.nervus` | one PI loop run continuously and as a sampled ECU task: overshoot and instability as the sample time grows (tutorial) |
| `aliasing.nervus` | a 45 Hz vibration sampled at 50 Hz looks like 5 Hz (tutorial) |
