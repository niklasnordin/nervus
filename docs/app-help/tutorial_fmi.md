# Tutorial: Exchanging models with FMI

| | |
|---|---|
| **You will learn** | FMI 2.0 co-simulation FMUs: exporting a subsystem, running it in another tool (fmpy, Simulink), importing an FMU as a block, and the communication-step delay of co-simulation |
| **Before this** | *From model to ECU C code* |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/fmi_plant.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | ECU software, communication and diagnostics ▸ From model to ECU code (*all tutorials*) |

**FMI** (Functional Mock-up Interface) is the standard for exchanging simulation models between tools. A model is
packed as an **FMU**, a zip file with a description of its variables and compiled code, and almost every
simulation tool can run one: Simulink (the FMU block), Dymola, OpenModelica, SimulationX, CANoe, AMESim, and Python
with fmpy. This is how a Nervus model reaches a colleague's Simulink model, and how a supplier's plant model gets
into Nervus.

```bash
uv sync --extra fmi
uv run python examples/fmi_exchange.py
```

## Step 1: export an FMU

Open `examples/throttle_ecu.nervus`, select the **ECU** subsystem and choose **Code ▸ Export FMU…**. Nervus generates
the C code (as in *the code-generation tutorial*), adds the FMI 2.0 functions around it,
compiles it for this computer and writes `ECU.fmu`:

| In the FMU | From the model |
|---|---|
| inputs `target_deg`, `tps_deg` | the subsystem's In blocks |
| output `duty_pct` | the Out block |
| tunable parameters `Kp`, `Ki`, ... | the calibratable workspace variables and block parameters |
| default communication step 0.01 s | the ECU's task period |
| `binaries/darwin64` (or `linux64`, `win64`) | the compiled library for this computer |
| `sources/` | the C code, so the FMU can be built on other platforms |

It is a **co-simulation** FMU: the importing tool calls it every communication step (a whole number of 10 ms
periods), and the FMU runs the generated step function. The generated code keeps its state in global variables, so
one program can run one instance of it at a time.

## Step 2: run it without Nervus

The example runs the FMU in **fmpy**, without any Nervus code: a step of the target angle from 5° to 60° gives a duty
cycle of 0 % before the step and 82 % after it, the same numbers as the model. In Simulink, drag an **FMU** block into
a model and pick `ECU.fmu`; in Python:

```python
from fmpy import simulate_fmu

result = simulate_fmu("ECU.fmu", stop_time=1.0, input=signals, start_values={"Kp": 3.0})
```

## Step 3: import an FMU

The **FMU** block (Ports & Subsystems library) runs any FMI 2.0 co-simulation FMU in a Nervus model. Its ports are the
FMU's inputs and outputs; *parameters* sets the FMU's parameters, for example `Kp = 3.0`; *sample_time* is the
communication step (0: the FMU's default). `examples/fmi_plant.nervus` is the throttle model with the ECU subsystem
replaced by the FMU:

![](../images/blocks/fmi_exchange.png)

The responses differ during the fast tip-in by up to 13°, and peak and steady state are the same. The reason is the
co-simulation itself: the FMU's output is available at the **end** of a communication step, so the controller acts
10 ms later than the inline subsystem. That is true for every co-simulation FMU, also in Simulink; a shorter
communication step (when the FMU allows it) makes the difference smaller.

## Experiments

| Try | What you will see |
|---|---|
| **ECU_fmu** *parameters* = `Kp = 3.0` | a stiffer controller inside the FMU: a faster rise |
| *parameters* = `Kq = 1` | an error: the FMU has no such parameter |
| **ECU_fmu** *sample_time* = 0.02 | the FMU runs two internal steps per communication step: a 20 ms lag, and the difference grows to 25° |
| `uv run python -c "from fmpy import dump; dump('examples/generated/ECU.fmu')"` | the FMU's description: variables, value references, platforms |

See *C code generation* for the details of the export.

## Next

That was the last tutorial on ecu software, communication and diagnostics. Continue with testing and verification: *Requirements-based testing and coverage*, or pick another one from the *tutorial overview*.
