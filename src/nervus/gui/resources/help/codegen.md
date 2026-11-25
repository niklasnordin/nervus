# C code generation

Nervus turns the **discrete-time** part of a model into plain C code for an ECU.

## Steps

1. Put the ECU software in a **subsystem**. Its In/Out blocks are the interface: sensors in, actuators out.
2. Give the controller blocks a **sample time** (e.g. the workspace variable `Ts = 0.01`).
3. Select the subsystem and choose **Code → Generate C Code…** (⌘B / Ctrl+B). With nothing selected, the
   whole model is generated.
4. Press **Run SIL check**. The code is compiled and run side by side with the model on the same inputs,
   and the largest difference is shown. It should be about 1e-15.
5. **Save to folder…** writes the files; **Open report** shows which lines belong to which block.

## Using the code

Call `<name>_initialize()` once, then every `<NAME>_BASE_PERIOD` seconds:

1. write the inputs into `<name>_U`,
2. call `<name>_step()`,
3. read the outputs from `<name>_Y`.

`main_example.c` shows the pattern. Only `<name>.h` and `<name>.c` go into the ECU project.

## Calibration

Parameters are in the struct `<name>_P`. A parameter written as a workspace variable (`Kp`) becomes one
shared field `<name>_P.Kp` that a calibration tool can change while the code runs.

## Typed parameters and constants

Workspace variables can be stored as integer or fixed-point types and as constants in ROM (the **Type** and
**Cal.** columns of the Model Workspace, see [Workspace and calibrations](workspace.md)). Calibratable parameters are in
`<name>_P`, ordered by size (8, 4, 2, 1 bytes) so the struct has no padding and the A2L offsets are exact.
Constants are in `const <name>_C`, which the linker puts in flash, and they are not in the A2L file.

## Calibration file (A2L)

The code generator also writes `<name>.a2l`, the file calibration tools such as INCA or CANape read. It
lists the tunable parameters, with units and limits from the model workspace, and the block signals for
measurement.

## One function per task (RTOS)

On a real ECU an operating system (an RTOS, e.g. AUTOSAR OS or FreeRTOS) runs the software in periodic
tasks, and a faster task interrupts a slower one. Tick **One function per task (for an RTOS)** in the
Generate C dialog (or `generate_c(model, tasks=True)`) to get one function per rate:

```c
void ecu_task_10ms(void);   /* call every 10 ms, highest priority */
void ecu_task_100ms(void);  /* call every 100 ms */
```

`main_example.c` then shows one OS task per rate, with the priorities to use. `ecu_step()` still exists:
called every base period, it runs the due tasks fastest first, which is what the SIL check compares with the
simulation.

**Signals between tasks need a Rate Transition block.** If the 10 ms task read the 100 ms task's output
directly, it would see the old or the new value depending on whether the slow task had finished, so the
result would depend on timing. A Rate Transition makes the transfer deterministic, in the simulation and on
the target:

![Rate Transition between a 10 ms and a 100 ms task](figures/rate_transition.png)

| Direction | Set | Behaviour |
|---|---|---|
| `fast_to_slow` | *sample_time* = the slow rate | the slow task gets the fast signal as it was at the start of its period |
| `slow_to_fast` | *sample_time* = the slow rate, *output_sample_time* = the fast rate | the fast task gets the slow signal one slow period later |

The code generator checks the model: a signal that goes from one task to another without a Rate Transition
is reported with both block names. Blocks that get the model's In ports directly must have a rate of their
own: sample the inputs with a Zero-Order Hold, or put each task in an
atomic subsystem (see [Subsystems](subsystems.md)) with its own *sample_time*.

## What can be generated

Everything that is discrete-time or has no state: gains, sums, lookup tables, logic, relays, filters,
counters, delays, discrete PID, and more. Not generated: blocks with continuous states (Integrator,
Transfer Fcn, PID Controller, ...) and time-based sources (Step, Sine, ...). Keep those in the plant, outside
the subsystem. If something cannot be generated, Diagnostics lists the blocks.

## Integer-only code (no floating point)

Small ECUs often have no floating-point unit. Tick **Integer arithmetic only** in the code generation dialog (or
`generate_c(model, integer_only=True)`) and the generated code has no `double` or `float` at all: every signal stays
its stored integer (`int16_T` for `fixdt(1, 16, 7)`, `uint8_T` for a boolean), and every block computes in an
`int64` accumulator. Moving the binary point is an integer multiplication, or a division rounded exactly as the
simulation rounds (`nv_div_nearest`, half away from zero; `floor`, `ceil`, `zero`), then the block's overflow
handling (`nv_sat` or `nv_wrap`). The results are the simulation's, **bit for bit**; the back-to-back test checks it
at zero tolerance.

```c
/* scale (Gain): 5/128 °C per ADC count, into fixdt(1, 16, 7) */
temp_B.scale[0] = (int16_T)nv_sat(((int64_T)5 * (int64_T)temp_B.z[0]), (int64_T)-32768, (int64_T)32767);
```

What it needs:

| Rule | Why |
|---|---|
| every signal has an integer, boolean or fixed-point type with **binary-point scaling** (slope 2^-n, no bias): set the types from the In blocks on | other scalings need real multiplications |
| a gain is an exact binary fraction (`0.75`, `0.0390625`), or a workspace variable with a fixed-point type (then it is calibratable as a stored integer) | `0.0123` is neither: the simulation would use a value the integer code cannot |
| blocks: In/Out, Constant, Sum, Gain, Product (`*`), Bias, Abs, MinMax, Relational Operator, Compare To Constant, Logical Operator, Switch, Saturation, Unit Delay, Integer Delay, Zero-Order Hold, Data Type Conversion, subsystem ports | the blocks of integer ECU code; others (a PID, a filter, a chart) need floating-point code |

A model that breaks a rule gets a message that names the block and the rule. Results that the simulation's double
arithmetic could round (more than 52 bits) are refused too, so the two always agree.

## Reusable functions for subsystems

Normally every block of a subsystem is written into the model's code (inline). When the same subsystem appears
several times, for example one injector or cylinder controller per cylinder, give the subsystems a **function
name** (the *function_name* parameter in the Properties panel). Subsystems with the same function name then share
**one C function**, called once per subsystem:

```c
/* cyl_trim.h: generated once */
void cyl_trim_initialize(cyl_trim_B_t *B, cyl_trim_DW_t *DW);
void cyl_trim_output(const cyl_trim_U_t *U, cyl_trim_Y_t *Y, cyl_trim_B_t *B, cyl_trim_DW_t *DW,
                     const cyl_trim_P_t *P);
void cyl_trim_update(cyl_trim_B_t *B, cyl_trim_DW_t *DW, const cyl_trim_P_t *P);

/* engine.c: each cylinder has its own signals, states and calibration */
cyl_trim_output(&in, &out, &engine_cyl0_B, &engine_cyl0_DW, &engine_cyl0_P);
```

Each subsystem keeps its own data: signals (`_B`), states (`_DW`) and calibration parameters (`<model>_<subsystem>_P`,
for example different trims per cylinder from a mask). The model calls `_output` where the subsystem's blocks would
compute their outputs and `_update` where they would update their states, so the code does exactly what the inline
code does; the back-to-back test compares it with the simulation.

The subsystems must have the same contents (blocks and wiring; their parameter *values* may differ), run at one rate
(their own *sample_time*) and have no enable or trigger port. The functions are in their own files
(`cyl_trim.c`, `cyl_trim.h`), which the ECU project builds with the model's code. Each instance's calibration
parameters are in the A2L file as `<model>_<subsystem>_P.<name>`.

## Coverage of the generated code

`nervus.codegen.c_coverage.coverage_of_tests(model, model.block("ECU"))` runs every test case of the model, records
the signals that enter the code at each of its steps, replays them into the generated C compiled with `--coverage`,
and reads gcov's counts: which lines and branches of the ECU code the tests execute (with each test's calibration).

```python
cov = coverage_of_tests(model, model.block("ECU"))
print(cov.summary())  # C code coverage: lines 100 % (25/25), branches 75 % (6/8) in 3 run(s), and what never ran
cov.save_html("ecu_coverage.html")
```

Many blocks become one-line conditionals (a Switch, a Saturation), so a missing test shows as a branch never taken
rather than a line. Needs a C compiler and gcov. In the editor: **C coverage (tests)** in the code generation
dialog shows the percentages and opens the annotated report.

## MISRA C

**MISRA C:2012** is the coding standard for safety-related C in the automotive industry: ISO 26262 asks for a coding
standard, and most ECU projects choose MISRA. The generated code follows it, and **MISRA check** in the code
generation dialog checks it with the free checker **cppcheck** and its MISRA addon (install it with
`brew install cppcheck` or `apt install cppcheck`). From Python:

```python
from nervus.codegen.misra import misra_check

findings = misra_check(generate_c(model.block("ECU")))  # [] : no findings
```

What the generated code does for it:

| Rule | In the generated code |
|---|---|
| 10.4, 7.2 | unsigned loop counters and index constants (`for (i = 0u; i < 4u; i++)`) |
| 12.1 | explicit parentheses: `(Kp * err) + x`, `(a != 0.0) && (b != 0.0)` |
| 14.4 | conditions are comparisons: `if ((st & 0x02u) != 0u)`, never `if (st & 0x02u)` |
| 15.7 | every `if … else if` chain ends with an `else` |
| 2.2, 2.3 | no `+ 0` in index expressions; only the type aliases the model uses |
| 8.9 | data used by one function only is declared in it (e.g. the rate periods) |
| 12.3 | one declaration per line |

The check analyses `<name>.c` and `<name>.h` (with `main_example.c`, so the calls of the step function count, but
its own findings are not reported). Three rules are **deviations**, as a MISRA compliance report records them:

| Rule | Why |
|---|---|
| 2.5 (advisory) | `<MODEL>_BASE_PERIOD` is part of the interface, for the scheduler that calls the step function |
| 8.7 (advisory) | `<model>_P` has external linkage so the calibration tool (XCP, A2L) and other ECU code reach the parameters |
| 7.3 | a false positive of the addon: the last letter of an identifier like `pedal` taken for an `l` suffix |

The test suite checks the code of every block with a C template and several example ECUs this way, and compiles it
with `-std=c99 -Wall -Wextra -Werror -pedantic`. cppcheck checks the rules a tool can decide; a MISRA compliance
claim for a project also needs a review of the rules no tool can decide, and a compliance report. Many projects use
a commercial checker (PC-lint, Polyspace, QA-C) as well.

## FMI export

**Code ▸ Export FMU…** packs the generated code of the selected subsystem (or the whole model) as an **FMI 2.0
co-simulation FMU**, the standard format for exchanging models between tools (Simulink, Dymola, OpenModelica,
SimulationX, CANoe, fmpy). The FMU contains:

- `modelDescription.xml`: the inputs and outputs (In and Out blocks), the tunable parameters (the calibratable
  parameters of the `_P` struct, with their start values) and the default communication step (the base period);
- `binaries/<platform>/`: the library compiled for this computer (`darwin64`, `linux64` or `win64`), with the FMI
  functions (`fmi2Instantiate`, `fmi2DoStep`, `fmi2GetReal`, ...) around the generated step function;
- `sources/`: the generated C files and the FMI wrapper, so other platforms can build it.

Each `fmi2DoStep` runs the step function once per base period; the communication step must be a whole number of base
periods. The generated code keeps its state in global variables, so one process runs one instance. In Python:
`nervus.codegen.fmu.export_fmu(model.block("ECU"), "ECU.fmu")`.

The **FMU** block imports FMUs from other tools into a model (co-simulation, needs fmpy: `uv sync --extra fmi`). See
the tutorial [Exchanging models with FMI](tutorial_fmi.md).

