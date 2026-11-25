# Getting started

## Install

Nervus needs Python 3.11 or newer. With pip:

```bash
pip install "nervus[gui]"                    # the engine and the desktop editor
nervus                                       # start the editor
nervus --copy-examples ~/nervus-examples     # the example models and scripts, to run and change
```

In the editor, **File ▸ Open Example** has the model of every tutorial. The example *scripts* (`.py`) write files
next to themselves, so run them from the copy: `cd ~/nervus-examples` and `python cruise_control.py`. The tutorials
write paths like `examples/cruise_control.nervus`: in the copy that is `~/nervus-examples/cruise_control.nervus`.

From the source, with [uv](https://docs.astral.sh/uv/):

```bash
git clone git@github.com:ikjbi/nervus.git
cd nervus
uv sync --extra gui           # the engine and the desktop editor (PySide6, matplotlib)
uv run nervus                 # start the editor
```

`uv sync` alone installs only the engine, for models in Python scripts. Other extras: `can` (CAN and DBC files),
`obd` (OBD-II over a serial adapter), `mdf` (MDF4 measurement files), `fmi` (FMUs); `uv sync --all-extras` installs
all of them.

### Troubleshooting

| You see | Do this |
|---|---|
| `ModuleNotFoundError: No module named 'PySide6'`, or *the desktop editor needs PySide6* | the editor's packages are not installed: `uv sync --extra gui` (or `uv run --extra gui nervus`) |
| *VIRTUAL_ENV … does not match the project environment path .venv and will be ignored* | harmless: another virtual environment is active, and uv uses the project's `.venv` anyway. `deactivate` removes the message. Do not use `uv run --active`: that runs Nervus in the other environment |
| Linux: *Could not load the Qt platform plugin "xcb"* | Qt needs a system library: `sudo apt install libxcb-cursor0` (Debian/Ubuntu) or `sudo dnf install xcb-util-cursor` (Fedora/RHEL) |
| Linux over SSH or without a display: *could not connect to display* | the editor needs a desktop session (or X forwarding: `ssh -X`); scripts and tests run without one, with `QT_QPA_PLATFORM=offscreen` |

## The editor

`uv run nervus` starts the desktop editor. It opens with a **welcome window**: build your first model with the
tutorial beside it, take a one-minute **tour** of the editor, open a beginner example together with its
tutorial, or pick a recent model. **Help ▸ Welcome** brings the window back, and **Help ▸ Editor Tour** repeats
the tour; untick *Show this window at startup* to start on an empty model instead.

To learn the editor, follow the tutorial [Your first model](tutorials/first-model.md), which builds a cruise
control step by step; the [editor guide](editor.md) lists everything it can do. The rest of this page builds a
model in Python, and [Scripting and testing models from Python](tutorials/python-scripting.md) goes further.

## Your first model

A model is a **diagram** of **blocks** connected by **signal lines**. This builds the first-order system
$\tau \dot y = u - y$ with $\tau = 0.5$ s:

```python
from nervus import Diagram, simulate
from nervus.blocks import Gain, Integrator, Scope, Step, Sum

d = Diagram("first_order")
u = d.add(Step(time=0.0, final=1.0), name="u")
err = d.add(Sum(signs="+-"), name="err")  # err = u - y
k = d.add(Gain(gain=1 / 0.5), name="1/tau")
y = d.add(Integrator(), name="y")
scope = d.add(Scope(num_inputs=2), name="scope")

d.connect(u, err.in_port(0))
d.connect(y, err.in_port(1))  # feedback
d.chain(err, k, y)  # err -> 1/tau -> y
d.connect(u, scope.in_port(0))
d.connect(y, scope.in_port(1))

res = simulate(d, t_final=3.0)
print(res.t[-1], res["y"][-1])
```

- `d.add(block, name=...)` adds a block. Names must be unique; one is generated if you leave it out.
- `d.connect(src, dst)` draws a line. A block on its own means port 0; use `block.in_port(i)` /
  `block.out_port(i)` (or a `(block, i)` tuple) for other ports.
- `d.chain(a, b, c)` connects `a → b → c` through port 0.

## Results

`simulate` returns a `SimResult`:

```python
res.t  # time points
res["y"]  # output of block "y"  (shape (N,) for scalars, (N, width) for vectors)
res["demux:1"]  # output port 1 of block "demux"
res.inputs("scope")  # the signals going into the scope
res.to_csv("out.csv")
res.to_dataframe()  # needs pandas
```

## Saving and loading

```python
from nervus import load, save

save(d, "first_order.nervus")
d2 = load("first_order.nervus")
```

Model files are plain JSON. See [Model files](model-files.md).

## Simulation settings

Each diagram stores its own settings, which `simulate` uses unless you override them:

```python
d.settings.t_final = 5.0
d.settings.solver = "rk4"  # fixed step
d.settings.step = 1e-3
res = simulate(d)
res = simulate(d, t_final=1.0, solver="LSODA")  # one-off override
```

See [Concepts](concepts.md#solvers) for how to choose a solver.

## Examples

The [`examples/`](https://github.com/ikjbi/nervus/tree/main/examples) folder has complete models:

- `cruise_control.py`: the cruise control of the tutorial [Your first model](tutorials/first-model.md), with P, PI and
  anti-windup versions.
- `dc_motor_lab.py`: the DC motor servo lab: identification, PI speed and PD position control
  (`dc_motor_speed.nervus`, `dc_motor_position.nervus`). See the [tutorial](tutorials/dc-motor.md).
- `coupled_tanks.py`: level control of two coupled water tanks, a nonlinear process. See the
  [tutorial](tutorials/coupled-tanks.md).
- `inverted_pendulum.py`: balancing a pendulum on a cart with LQR state feedback. See the
  [tutorial](tutorials/inverted-pendulum.md).
- `rc_rlc.py`: an RC low-pass and a series RLC circuit (`rc_filter.nervus`, `rlc_circuit.nervus`). See the
  [tutorial](tutorials/rc-rlc.md).
- `mass_spring_damper_pid.py`: continuous PID position control with actuator limits.
- `throttle_pi.py`: electronic throttle control with a 10 ms discrete PI controller, as on an ECU, with its
  gains as workspace variables and a second calibration file.
- `idle_speed_control.py`: idle speed control on a mean-value engine model, with AC feed-forward,
  requirement tests and C code. See the [tutorial](tutorials/idle-speed-control.md).
- `throttle_ecu.py`: the throttle controller as an ECU subsystem, with generated C and A2L.
- `coolant_fan.py`: radiator fan on/off control with a filtered sensor, a hysteresis relay and a lookup table.
- `fixed_point_sensor.py`: a sensor signal path in fixed-point data types, bit-true in simulation and C.
- `engine_library.py`: a mean-value engine and a throttle actuator as masked components in a library, used
  twice in `engine_comparison.nervus`. See [Component libraries](libraries.md).
- `lambda_control.py`: closed-loop lambda control with short- and long-term fuel trims. See the
  [tutorial](tutorials/lambda-control.md).
- `drive_cycle.py`: a car with a driver model on the ECE-15 urban drive cycle. See the
  [tutorial](tutorials/drive-cycle.md).
- `engine_modes.py`: the operating modes of an engine ECU as a State Chart (crank, idle, run, overrun fuel
  cut-off, stall), with calibratable thresholds and scenario test cases. See [State machines](statecharts.md).
- `gear_shift.py`: the shift logic of an automatic gearbox (shift maps, a gear chart, kickdown) on a car with
  engine, gearbox and road grade. See the [tutorial](tutorials/gear-shift.md).
- `circuits.py`: four electronic circuits as in LTspice: a rectifier read from a SPICE netlist, a relay driver
  with and without its freewheeling diode, a buck converter and an active filter (`rectifier.nervus`,
  `relay_driver.nervus`, `buck_converter.nervus`, `active_filter.nervus`). See the
  [tutorial](tutorials/circuits.md).
- `starter_motor.py`: cranking an engine: battery, starter relay, starter motor, compression. See the
  [tutorial](tutorials/physical-models.md).
- `electronic_throttle.py`: drive-by-wire: H-bridge, DC motor, gear and return spring, with a tuned PID in a
  1 ms ECU task. See the [tutorial](tutorials/physical-models.md).
- `traction_control.py`: tyre slip with Pacejka's magic formula and a traction control ECU on dry, wet and
  snowy roads. See the [tutorial](tutorials/traction-control.md).
- `ignition_knock.py`: spark advance from a speed/load map and knock control on low-octane fuel. See the
  [tutorial](tutorials/ignition-knock.md).
- `fuel_film.py`: wall wetting (x-tau) and its compensation, a switching lambda sensor with jump and ramp, and
  long-term fuel trims learned per load cell (Adaptive Map). See the [tutorial](tutorials/fuel-film.md).
- `truck.py`: a 40 t truck with a heavy-duty diesel, a 12-speed AMT and an exhaust brake on a climb and a
  descent. See the [tutorial](tutorials/truck.md).
- `can_network.py`: two CAN buses, a gateway, a rest-bus simulation, E2E protection and fault injection.
  See the [tutorial](tutorials/can-network.md).
- `xcp_calibration.py`, `xcp_tool.py`: live calibration over XCP on CAN: the cruise control as an ECU and a
  command-line calibration tool. See the [tutorial](tutorials/xcp-calibration.md).
- `ntc_sensor.py`: a coolant temperature sensor from the NTC circuit to the ECU software, with open and short
  circuit diagnosis. See the tutorials [NTC sensor interface](tutorials/ntc-sensor.md) and
  [sensor fault diagnosis](tutorials/sensor-diagnostics.md).
- `abs_braking.py`: an emergency stop from 100 km/h with and without ABS. See the [tutorial](tutorials/abs.md).
- `idle_requirements.py`: the idle speed controller with its requirements, traceability and coverage. See the
  [tutorial](tutorials/requirements-testing.md).
- `cruise_tolerance.py`: a Monte Carlo study of the cruise control over 200 cars. See the
  [tutorial](tutorials/robustness-monte-carlo.md).
- `charging_system.py`: the 12 V charging system with electrical energy management. See the
  [tutorial](tutorials/charging-system.md).
- `ev_range.py`: the range of an electric car on the WLTP cycle, summer and winter. See the
  [tutorial](tutorials/ev-range.md).
- `injector_driver.py`: the injector power stage on the ECU board. See the [tutorial](tutorials/injector-driver.md).
- `fmi_exchange.py`: exports the throttle ECU as an FMI 2.0 FMU, runs it in fmpy, and puts it back into the plant
  model (`fmi_plant.nervus`). See the [tutorial](tutorials/fmi.md).
- `obd_ecu.py`: an engine ECU with diagnostic monitors (misfire, lean) that answers real OBD-II tools while it
  runs. See [OBD-II](obd.md).
- `scripting.py`: the cruise control driven from Python: parameters, sweeps, test cases, a model built in code.
  See the [tutorial](tutorials/python-scripting.md).
- `discrete_control.py`: one PI loop run continuously and as a sampled ECU task, and a signal that aliases
  (`discrete_control.nervus`, `aliasing.nervus`). See the [tutorial](tutorials/discrete-control.md).
