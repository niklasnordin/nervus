# Data types and fixed point

Most ECU software does not use `double`. Sensor values arrive as integers, memory is scarce, and many
microcontrollers have no floating-point unit, so signals are **integers** or **fixed point**. Nervus
simulates these types **bit-true**: the simulation gives exactly the values the generated C code computes.

## Types

| Type | Meaning |
|---|---|
| `double`, `single` | 64- and 32-bit floating point |
| `boolean` | 0 or 1 |
| `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32` | integers |
| `fixdt(s, w, f)` | fixed point: signed (`s`=1) or unsigned, `w` bits, `f` fraction bits (resolution 2⁻ᶠ) |
| `fixdt(s, w, slope, bias)` | fixed point with arbitrary scaling |
| `inherit` | take the type from the inputs |

A fixed-point signal stores an integer **Q** and represents the real value

$$ V = \mathrm{slope} \cdot Q + \mathrm{bias} $$

For example `fixdt(1, 16, 8)` covers −128 … 127.996 in steps of 1/256, and `fixdt(0, 8, 0.5, -40)` is a
temperature from −40 °C to 87.5 °C in 0.5 °C steps, in one byte. On wires the editor uses the short names
`sfix16_En8` and `ufix8_S0.5_B-40`.

## Setting types

- **Data Type Conversion** (*Signal Attributes*) converts a signal to a type, with a choice of *rounding*
  (`nearest`, `floor`, `ceil`, `zero`) and *overflow* (`saturate` or `wrap`):

  ![Overflow and resolution](../images/blocks/datatype_conversion.png)

- **In**, **Constant**, **Gain**, **Sum**, **Product**, **Bias** and the **lookup tables** have an
  *output_type* parameter.
- Everything else **inherits**. A block outputs the common type of its inputs (or `double` if they differ).
  Logic and comparison blocks output `boolean`. Continuous-time blocks, PID controllers and charts always
  use `double`.

Turn on **View → Show Data Types** to label every wire that carries a non-double type (**Show Signal
Widths** and **Sample Time Colours** are next to it).

## How it is simulated

Each block computes in double precision and its output is then converted to its type, with the block's
rounding and overflow mode (default: `nearest` and `saturate`). Doubles represent every 32-bit integer and
fixed-point value exactly, so this is bit-true.

## Generated code

- The **input and output structs** use real C types (`int16_T`, `uint8_T`, `boolean_T`, ...). Fixed-point
  ports carry the stored integer, and the header comment states the scaling.
- Inside the step function, signals are converted with small generated helper functions
  (`nv_q_int16_nearest_saturate`, ...) that use exactly the same rounding and overflow as the simulation. The
  test suite checks this with a back-to-back test at **zero tolerance**.
- The floating-point code computes in `double` between the conversions. For targets without an FPU,
  *integer-only code* (C code generation) keeps every signal a stored integer and computes with integers only, bit-true too.

## Example

`examples/fixed_point_sensor.py` runs a pressure sensor chain twice: a 12-bit ADC (`uint16` counts), scaling
to kPa, a PT1 filter and a limit check, once in `double` and once in `fixdt(0, 16, 6)`. The largest
difference is 0.010 kPa, within the fixed-point resolution of 1/64 kPa (see the tutorial on fixed point).
