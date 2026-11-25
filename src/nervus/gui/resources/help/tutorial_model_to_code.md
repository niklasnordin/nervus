# Tutorial: From model to ECU C code

| | |
|---|---|
| **You will learn** | an ECU software component with an interface, generated C code and its structure, calibration parameters and the A2L file, a back-to-back (SIL) test of the code, a MISRA C check |
| **Before this** | [Your first model: a cruise control](tutorial_first_model.md); read [C code generation](codegen.md) first |
| **Level and time** | intermediate, about 40 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/throttle_ecu.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | ECU software, communication and diagnostics ▸ From model to ECU code ([all tutorials](tutorials.md)) |

`examples/throttle_ecu.py` takes a throttle position controller from the model to C code that can run on an ECU:
the controller as a software component, the generated code, its calibration file, and a **back-to-back test**
that proves the C code computes the same as the model.

```bash
uv run python examples/throttle_ecu.py    # simulates, runs the tests, generates examples/generated/ECU/
```

## Step 1: the ECU as a software component

Open `throttle_ecu.nervus`. Everything that runs on the ECU is inside the subsystem **ECU**; the throttle body
(an actuator lag and the plate angle) is outside, because it is hardware. The ECU's **In** and **Out** blocks
are its software interface:

| Port | Signal | Unit |
|---|---|---|
| In 1 `target_deg` | requested throttle angle (from the pedal map) | ° |
| In 2 `tps_deg` | measured angle (throttle position sensor) | ° |
| Out 1 `duty_pct` | H-bridge duty cycle | % |

Inside, the sensor is sampled every `Ts` = 10 ms (Zero-Order Hold), and a **Discrete PID** (P and I) with clamping
anti-windup computes the duty cycle, limited to 0-100 %. The gains `Kp` and `Ki` are workspace variables. Run the
three test cases in the **Test Manager**: the throttle reaches 59° within 80 ms of the step to 60° and overshoots
to 62.7°.

## Step 2: generate the code

Select the **ECU** block and choose **Code ▸ Generate C Code** (Ctrl+B). Nervus writes:

| File | What it is |
|---|---|
| `ECU.h` | the interface: input, output, parameter and state structs, `ECU_initialize()`, `ECU_step()` |
| `ECU.c` | the step function: every block as a few lines of C, in execution order |
| `ECU.a2l` | the calibration description for INCA or CANape: parameters and measurements with their addresses |
| `main_example.c` | a template showing how to call the code from a 10 ms task |
| report | an HTML page linking each block to its code |

The interface in `ECU.h`:

```c
#define ECU_BASE_PERIOD (0.01) /* seconds between calls to ECU_step() */

typedef struct { real_T target_deg[1]; real_T tps_deg[1]; } ECU_U_t;   /* inputs  */
typedef struct { real_T duty_pct[1]; } ECU_Y_t;                         /* outputs */
typedef struct { real_T Kp[1]; real_T Ki[1]; /* ... */ } ECU_P_t;      /* calibration parameters */
```

and the PI controller in `ECU_step()`:

```c
/* error (Sum) */
ECU_B.error[0] = ECU_B.target_deg[0] - ECU_B.tps_sample[0];
/* throttle_pi (Discrete PID Controller) */
real_T unsat = ECU_P.Kp[0] * err + ECU_DW.throttle_pi_x[0] + d_term;
real_T y = fmin(fmax(unsat, ECU_P.throttle_pi_lower[0]), ECU_P.throttle_pi_upper[0]);
```

The code uses only static memory, no dynamic allocation and no operating system: a timer interrupt or an
RTOS task calls `ECU_step()` every 10 ms after writing the inputs, as `main_example.c` shows.

## Step 3: calibration

`Kp` and `Ki` stay **parameters in RAM** (`ECU_P`), so a calibration tool can change them on the running ECU
through the A2L file, the same way as in the [XCP tutorial](tutorial_xcp_calibration.md). In the Model Workspace,
untick **Cal.** for `Kp`: it becomes a constant in ROM (`ECU_C`) and disappears from the A2L file, which is what
production code does with values that must never change.

## Step 4: back-to-back test (software in the loop)

How do you know the C code does what the model does? The script compiles the generated code with the system C
compiler (gcc or clang), feeds it the same 300 input samples as the model, and compares every output:

```
Back-to-back test, 300 steps: PASSED
  duty_pct: max |C - Python| = 1.42e-14
```

The difference is in the last digits of a double: the C code computes the same numbers. In the editor, the
code generation dialog offers the same check (**Run SIL check**). Run it after every change to the model, and
after updating the compiler.

## Step 5: MISRA C

ECU code is written to a coding standard, in the automotive industry nearly always **MISRA C**. Click **MISRA check**
in the code generation dialog (it needs cppcheck: `brew install cppcheck` or `apt install cppcheck`): the throttle
controller's code has no findings. [C code generation](codegen.md#misra-c) lists what the generated code does for
the rules, and the three documented deviations.

## Experiments

| Try | What you will see |
|---|---|
| `Ts` = 0.02 (a 20 ms task) | the loop gets slower and overshoots to 66°; the *tip-in* tests fail: the gains were tuned for 10 ms |
| `Ts` = 0.05 | 79° overshoot: a controller tuned for one rate does not work at another |
| untick **Cal.** for `Kp`, generate again | `Kp` moves from `ECU_P` (RAM) to `ECU_C` (ROM) and leaves the A2L file |

## Next

Next in this topic: [A sensor signal chain in fixed point](tutorial_fixed_point.md). Or pick another one from the [tutorial overview](tutorials.md).
