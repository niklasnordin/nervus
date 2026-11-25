"""Integer-only generated code: no floating point, bit-true with the simulation."""

import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from nervus import Diagram
from nervus.blocks import (
    CompareToConstant,
    Constant,
    DataTypeConversion,
    Gain,
    Inport,
    LogicalOperator,
    Outport,
    Product,
    Saturation,
    Sum,
    Switch,
    UnitDelay,
    ZeroOrderHold,
)
from nervus.codegen import generate_c
from nervus.codegen.generator import CodegenError
from nervus.codegen.sil import back_to_back, find_compiler

pytestmark = pytest.mark.skipif(find_compiler() is None, reason="no C compiler")
FIX = "fixdt(1, 16, 7)"  # -256 ... 255.99, steps of 1/128


def _sensor_chain() -> Diagram:
    """A coolant temperature sensor: raw ADC counts → °C, plausibility, fallback, limits, a delayed copy."""
    d = Diagram("temp")
    adc = d.add(Inport(port=1, output_type="int16"), name="adc")
    ok_in = d.add(Inport(port=2, output_type="boolean"), name="sensor_ok")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    zo = d.add(ZeroOrderHold(sample_time=0.01), name="zo")
    scale = d.add(Gain(gain=0.0390625, output_type=FIX), name="scale")  # 5/128 °C per count
    offset = d.add(Constant(value=-40.0, output_type=FIX, sample_time=0.01), name="offset")
    temp = d.add(Sum(signs="++", output_type=FIX), name="temp")
    plausible = d.add(CompareToConstant(operator="<", constant=150.25), name="plausible")
    valid = d.add(LogicalOperator(operator="AND", num_inputs=2), name="valid")
    fallback = d.add(Constant(value=90.0, output_type=FIX, sample_time=0.01), name="fallback")
    choose = d.add(Switch(criterion="u1 != 0", threshold=0.0), name="choose")
    limit = d.add(Saturation(upper=130.3, lower=-30.7), name="limit")
    prev = d.add(UnitDelay(initial=20.0, sample_time=0.01), name="prev")
    rise = d.add(Sum(signs="+-", output_type=FIX), name="rise")
    small = d.add(DataTypeConversion(output_type="int8", rounding="floor", overflow="saturate"), name="to_int8")
    twice = d.add(Product(ops="**", output_type="fixdt(1, 32, 10)"), name="squared")
    d.chain(adc, z, scale)
    d.connect(scale, (temp, 0))
    d.connect(offset, (temp, 1))
    d.chain(temp, plausible)
    d.chain(ok_in, zo)
    d.connect(plausible, (valid, 0))
    d.connect(zo, (valid, 1))
    d.connect(temp, (choose, 0))
    d.connect(valid, (choose, 1))
    d.connect(fallback, (choose, 2))
    d.chain(choose, limit, prev)
    d.connect(limit, (rise, 0))
    d.connect(prev, (rise, 1))
    d.chain(rise, small)
    d.connect(limit, (twice, 0))
    d.connect(limit, (twice, 1))
    for k, (blk, name) in enumerate(((limit, "temp_c"), (small, "rise_i8"), (valid, "valid_out"), (twice, "sq"))):
        d.connect(blk, d.add(Outport(port=k + 1), name=name))
    return d


def test_no_floating_point_and_bit_true(tmp_path: Path) -> None:
    d = _sensor_chain()
    code = generate_c(d, integer_only=True)
    c = code.files["temp.c"]
    body = c[c.index("void temp_initialize") :]
    assert not re.search(r"\b(real_T|double|float|real32_T)\b", body), "floating point in the integer code"
    assert "#include <math.h>" not in c or "round(" not in body
    rng = np.random.default_rng(11)
    steps = 400
    adc = np.concatenate([rng.integers(-200, 5000, steps - 6), [-32768, 32767, 0, 4095, 4864, 4865]]).astype(float)
    ok = (rng.random(steps) > 0.2).astype(float)
    res = back_to_back(d, {"adc": adc, "sensor_ok": ok}, steps, tolerance=0.0, folder=tmp_path, integer_only=True)
    assert res.passed, res.summary()
    assert np.max(res.c["temp_c"]) == pytest.approx(130.296875)  # the limit, in steps of 1/128 (rounded)


def test_compiles_strictly_and_misra(tmp_path: Path) -> None:
    from nervus.codegen.misra import cppcheck_path, misra_check

    code = generate_c(_sensor_chain(), integer_only=True)
    code.write(tmp_path)
    run = subprocess.run([find_compiler(), "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", "-c", "temp.c",
                          "main_example.c"], cwd=tmp_path, capture_output=True, text=True)  # fmt: skip
    assert run.returncode == 0, run.stderr
    if cppcheck_path() is not None:
        assert [str(f) for f in misra_check(code)] == []


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.block("scale").set_param("gain", 0.0123), "not an exact binary fraction"),
    (lambda d: d.block("adc").set_param("output_type", "double"), "integer or fixed-point types"),
    (lambda d: d.block("temp").set_param("output_type", "fixdt(1, 16, 0.1, 0)"), "not a power of two"),
])  # fmt: skip
def test_refusals_say_why(change, message: str) -> None:  # type: ignore[no-untyped-def]
    d = _sensor_chain()
    change(d)
    with pytest.raises(CodegenError, match=message):
        generate_c(d, integer_only=True)


def _one_block(block, output_type: str = "int16") -> Diagram:  # type: ignore[no-untyped-def]
    d = Diagram("one")
    u = d.add(Inport(port=1, output_type=output_type), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    b = d.add(block, name="dut")
    d.chain(u, z, b)
    d.connect(b, d.add(Outport(port=1), name="y"))
    return d


@pytest.mark.parametrize("block", [
    CompareToConstant(operator="==", constant=0.5),  # no int16 equals 0.5: always false
    CompareToConstant(operator="!=", constant=0.5),  # always true
    CompareToConstant(operator=">", constant=float("inf")),
    CompareToConstant(operator="<=", constant=float("inf")),
    CompareToConstant(operator=">=", constant=float("-inf")),
    Saturation(upper=float("inf"), lower=0.0),  # only a lower limit
    Saturation(upper=100.0, lower=float("-inf")),
], ids=["eq_half", "ne_half", "gt_inf", "le_inf", "ge_minus_inf", "sat_upper_inf", "sat_lower_inf"])  # fmt: skip
def test_constants_outside_the_stored_integers(block, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Regression: a constant that no stored integer can equal, or an infinite limit, is bit-true too (it used to
    compare against 0, or fail with an OverflowError)."""
    u = np.array([-32768.0, -3.0, -1.0, 0.0, 1.0, 2.0, 500.0, 32767.0])
    res = back_to_back(_one_block(block), {"u": u}, len(u), tolerance=0.0, folder=tmp_path, integer_only=True)
    assert res.passed, res.summary()


def test_switch_with_an_infinite_threshold(tmp_path: Path) -> None:
    d = Diagram("sw")
    for k in range(3):
        u = d.add(Inport(port=k + 1, output_type="int16"), name=f"u{k}")
        z = d.add(ZeroOrderHold(sample_time=0.01), name=f"z{k}")
        d.chain(u, z)
    sw = d.add(Switch(threshold=float("-inf")), name="sw")
    for k in range(3):
        d.connect(d.block(f"z{k}"), (sw, k))
    d.connect(sw, d.add(Outport(port=1), name="y"))
    rng = np.random.default_rng(3)
    inputs = {f"u{k}": np.round(rng.uniform(-100, 100, 20)) for k in range(3)}
    res = back_to_back(d, inputs, 20, tolerance=0.0, folder=tmp_path, integer_only=True)
    assert res.passed, res.summary()
    assert np.array_equal(res.c["y"][:, 0], inputs["u0"])
