# Tutorial: A sensor signal chain in fixed point

| | |
|---|---|
| **You will learn** | fixed-point data types (word length, fraction bits, resolution, range), an ADC model, quantization error, and the silent failure of a badly chosen range |
| **Before this** | [From model to ECU C code](tutorial_model_to_code.md); read [Data types and fixed point](datatypes.md) first |
| **Level and time** | intermediate, about 30 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/fixed_point_sensor.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | ECU software, communication and diagnostics ▸ From model to ECU code ([all tutorials](tutorials.md)) |

Many ECU microcontrollers have no floating-point unit, or the code must be fast and deterministic, so signals are
stored as **integers with a scale**. `examples/fixed_point_sensor.py` runs a manifold pressure sensor chain
twice, in `double` and in fixed point, so you can see what the integers cost in accuracy and what happens when
the scaling is chosen badly.

```bash
uv run python examples/fixed_point_sensor.py
```

## The signal chain

| Stage | Block | Data type |
|---|---|---|
| pressure sensor and ADC: 20 kPa reads 0 counts, 0.0763 kPa per count | Bias, Gain | double (physics) |
| 12-bit ADC: truncate to whole counts, sample every 10 ms | Data Type Conversion (*floor*), Zero-Order Hold | `uint16` counts |
| counts → kPa | Gain (0.0763 kPa/count), Bias (+20 kPa) | `double` **or** `fixdt(0, 16, 6)` |
| smoothing | Discrete Filter (PT1, 50 ms) | inherited |
| over-pressure check | Compare to Constant (> 150 kPa) | boolean |

`fixdt(0, 16, 6)` means **unsigned, 16 bits, 6 fraction bits**: the stored integer $q$ represents
$q / 2^6$, so the resolution is 1/64 = 0.0156 kPa and the range 0 to 1024 kPa. The scaling Gain sets this type
with its *output_type*; the Bias and the filter after it inherit it, exactly as in generated C code, where the
blocks become integer arithmetic with shifts.

![](figures/fixed_point_sensor.png)

## What it costs

The true pressure swings between 40 and 160 kPa. The fixed-point chain follows the `double` chain within
**0.010 kPa** (10 Pa), less than one resolution step: every value it produces is a multiple of 1/64 kPa (turn on
**View ▸ Data Types** to see the types on the wires). That is far below the sensor's own accuracy, so the
16-bit format is a good choice.

## What can go wrong: the range

More fraction bits mean finer resolution but a smaller range. `fixdt(0, 16, 10)` has a resolution of 1/1024 kPa
but can only hold values up to 64 kPa. Select **scale_fix**, set *output_type* to `fixdt(0, 16, 10)` and run:
the pressure **saturates at 64 kPa**, and the over-pressure check never fires, although the real pressure
reaches 160 kPa. Nothing crashes and no error is reported: this is why fixed-point formats are chosen from the
signal's physical range, and why production code is tested with the extreme values.

## Experiments

Select the **scale_fix** block and change its *output_type* in Properties:

| Try | What you will see |
|---|---|
| `fixdt(0, 16, 2)` | resolution 0.25 kPa: the error grows to 0.15 kPa, still small for a pressure sensor |
| `uint8` | whole kPa only (and at most 255): the error is up to 0.7 kPa, visible as steps |
| `fixdt(0, 16, 9)` | the range ends at 128 kPa: the signal is cut off there and the check never fires |
| `fixdt(0, 16, 10)` | saturates at 64 kPa, as above |

See [Data types and fixed point](datatypes.md) for the rules (rounding, overflow, how types are inherited)
and [the code-generation tutorial](tutorial_model_to_code.md) for turning such a chain into C.

## Next

Next in this topic: [Exchanging models with FMI](tutorial_fmi.md). Or pick another one from the [tutorial overview](tutorials.md).
