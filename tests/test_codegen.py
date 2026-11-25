"""C code generation: compile the generated code and compare it with the Python model (back-to-back)."""

from collections.abc import Callable

import numpy as np
import pytest

from nervus import Diagram
from nervus.blocks import (
    Abs,
    Bias,
    CompareToConstant,
    Constant,
    Counter,
    DeadZone,
    Debounce,
    Demux,
    DiscreteFilter,
    DiscreteIntegrator,
    DiscretePID,
    DiscreteTransferFunction,
    EdgeDetect,
    Gain,
    Inport,
    IntegerDelay,
    Integrator,
    LogicalOperator,
    Lookup1D,
    Lookup2D,
    MathFunction,
    MinMax,
    MovingAverage,
    Mux,
    Outport,
    Product,
    Quantizer,
    RateLimiter,
    RelationalOperator,
    Relay,
    Saturation,
    Scope,
    Subsystem,
    Sum,
    Switch,
    UnitDelay,
    ZeroOrderHold,
)
from nervus.codegen import CodegenError, generate_c
from nervus.codegen.sil import back_to_back, find_compiler
from nervus.core import Block

pytestmark = pytest.mark.skipif(find_compiler() is None, reason="no C compiler available")

STEPS = 60
RNG = np.random.default_rng(42)


def harness(block: Block, n_inputs: int, ts: float = 0.01) -> Diagram:
    """In -> ZOH -> block -> Out, so the block runs at a discrete rate."""
    d = Diagram("dut")
    b = d.add(block, name="dut")
    for k in range(n_inputs):
        u = d.add(Inport(port=k + 1), name=f"u{k}")
        z = d.add(ZeroOrderHold(sample_time=ts), name=f"zoh{k}")
        d.chain(u, z)
        d.connect(z, (b, k))
    for p in range(b.num_outputs()):
        d.connect((b, p), d.add(Outport(port=p + 1), name=f"y{p}"))
    return d


def random_inputs(n: int, kind: str = "real") -> dict[str, np.ndarray]:
    out = {}
    for k in range(n):
        if kind == "bool":
            out[f"u{k}"] = (RNG.random(STEPS) > 0.5).astype(float)
        elif kind == "positive":
            out[f"u{k}"] = RNG.uniform(0.1, 5.0, STEPS)
        else:
            out[f"u{k}"] = RNG.uniform(-3.0, 3.0, STEPS)
    return out


CASES: list[tuple[str, Callable[[], Block], int, str]] = [
    ("gain", lambda: Gain(gain=2.5), 1, "real"),
    ("sum", lambda: Sum(signs="+-+"), 3, "real"),
    ("product", lambda: Product(ops="*/"), 2, "positive"),
    ("abs", lambda: Abs(), 1, "real"),
    ("bias", lambda: Bias(bias=1.5), 1, "real"),
    ("saturation", lambda: Saturation(upper=1.0, lower=-0.5), 1, "real"),
    ("sqrt", lambda: MathFunction(function="sqrt"), 1, "positive"),
    ("tanh", lambda: MathFunction(function="tanh"), 1, "real"),
    ("sign", lambda: MathFunction(function="sign"), 1, "real"),
    ("minmax", lambda: MinMax(function="max", num_inputs=3), 3, "real"),
    ("dead_zone", lambda: DeadZone(start=-1.0, end=0.5), 1, "real"),
    ("quantizer", lambda: Quantizer(interval=0.25), 1, "real"),
    ("relay", lambda: Relay(on_point=1.0, off_point=-1.0, on_value=5.0, off_value=-2.0), 1, "real"),
    ("rate_limiter", lambda: RateLimiter(rising_rate=50.0, falling_rate=-20.0), 1, "real"),
    ("relational", lambda: RelationalOperator(operator=">="), 2, "real"),
    ("compare", lambda: CompareToConstant(operator="<", constant=0.3), 1, "real"),
    ("and", lambda: LogicalOperator(operator="AND", num_inputs=3), 3, "bool"),
    ("xor", lambda: LogicalOperator(operator="XOR", num_inputs=3), 3, "bool"),
    ("nor", lambda: LogicalOperator(operator="NOR"), 2, "bool"),
    ("not", lambda: LogicalOperator(operator="NOT"), 1, "bool"),
    ("switch", lambda: Switch(threshold=0.2), 3, "real"),
    ("unit_delay", lambda: UnitDelay(initial=0.7), 1, "real"),
    ("integer_delay", lambda: IntegerDelay(delay=4, initial=-1.0), 1, "real"),
    ("dint_fwd", lambda: DiscreteIntegrator(gain=2.0, upper=1.0, lower=-1.0), 1, "real"),
    ("dint_bwd", lambda: DiscreteIntegrator(method="backward_euler"), 1, "real"),
    ("dtf", lambda: DiscreteTransferFunction(numerator=[0.2, 0.1], denominator=[1.0, -1.2, 0.5]), 1, "real"),
    ("dtf_static", lambda: DiscreteTransferFunction(numerator=[2.0], denominator=[1.0]), 1, "real"),
    ("counter_wrap", lambda: Counter(limit=7), 1, "bool"),
    ("counter_sat", lambda: Counter(limit=7, at_limit="saturate"), 1, "bool"),
    ("edge_rising", lambda: EdgeDetect(direction="rising"), 1, "bool"),
    ("edge_either", lambda: EdgeDetect(direction="either"), 1, "bool"),
    ("debounce", lambda: Debounce(on_samples=3, off_samples=2), 1, "bool"),
    ("pt1", lambda: DiscreteFilter(time_constant=0.05), 1, "real"),
    ("moving_avg", lambda: MovingAverage(length=5), 1, "real"),
    (
        "pid",
        lambda: DiscretePID(P=1.2, I=3.0, D=0.05, N=50.0, upper=2.0, lower=-2.0, anti_windup="clamping"),
        1,
        "real",
    ),
    ("pid_none", lambda: DiscretePID(P=0.8, I=2.0, D=0.1, N=20.0), 1, "real"),
    ("pid_bc", lambda: DiscretePID(P=1.0, I=4.0, upper=1.0, lower=-1.0, anti_windup="back_calculation"), 1, "real"),
    ("lookup1d", lambda: Lookup1D(breakpoints=[-2, 0, 1, 2], table=[4, 0, 1, 3]), 1, "real"),
    (
        "lookup1d_lin",
        lambda: Lookup1D(breakpoints=[-2, 0, 1, 2], table=[4, 0, 1, 3], extrapolation="linear"),
        1,
        "real",
    ),
    ("lookup1d_flat", lambda: Lookup1D(breakpoints=[-2, 0, 1, 2], table=[4, 0, 1, 3], interpolation="flat"), 1, "real"),
    (
        "lookup1d_near",
        lambda: Lookup1D(breakpoints=[-2, 0, 1, 2], table=[4, 0, 1, 3], interpolation="nearest"),
        1,
        "real",
    ),
    (
        "lookup2d",
        lambda: Lookup2D(row_breakpoints=[-2, 0, 2], column_breakpoints=[-1, 1], table=[[1, 2], [3, 5], [0, -1]]),
        2,
        "real",
    ),
]


@pytest.mark.parametrize(("label", "factory", "n_inputs", "kind"), CASES, ids=[c[0] for c in CASES])
def test_block_back_to_back(label: str, factory: Callable[[], Block], n_inputs: int, kind: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = harness(factory(), n_inputs)
    res = back_to_back(d, random_inputs(n_inputs, kind), STEPS, tolerance=1e-12, folder=tmp_path)
    assert res.passed, res.summary()


@pytest.mark.parametrize("extrapolation", ["clip", "linear"])
def test_lookup_nearest_ties_go_up_in_simulation_and_c(extrapolation: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Exactly half way between two breakpoints, nearest takes the upper point, whatever the extrapolation."""
    blk = Lookup1D(breakpoints=[0, 1, 2, 4], table=[10, 20, 30, 40], interpolation="nearest",
                   extrapolation=extrapolation)  # fmt: skip
    u = np.resize([0.5, 1.5, 3.0, 0.49, 2.99, -1.0, 5.0], STEPS)
    res = back_to_back(harness(blk, 1), {"u0": u}, STEPS, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
    np.testing.assert_array_equal(np.ravel(res.python["y0"])[:7], [20, 30, 40, 10, 30, 10, 40])


def test_vectors_mux_demux_matrix_gain(tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = Diagram("vec")
    a = d.add(Inport(port=1, value=[0.0, 0.0]), name="a")
    b = d.add(Inport(port=2), name="b")
    za, zb = d.add(ZeroOrderHold(sample_time=0.01)), d.add(ZeroOrderHold(sample_time=0.01))
    m = d.add(Mux(), name="mux")
    k = d.add(Gain(gain=[[1, 2, 3], [0, -1, 4]], multiplication="matrix"), name="K")
    dm = d.add(Demux(num_outputs=2), name="demux")
    d.chain(a, za)
    d.chain(b, zb)
    d.connect(za, (m, 0))
    d.connect(zb, (m, 1))
    d.chain(m, k, dm)
    d.connect((dm, 0), d.add(Outport(port=1), name="y0"))
    d.connect((dm, 1), d.add(Outport(port=2), name="y1"))
    inputs = {"a": RNG.uniform(-1, 1, (STEPS, 2)), "b": RNG.uniform(-1, 1, STEPS)}
    res = back_to_back(d, inputs, STEPS, tolerance=1e-12, folder=tmp_path)
    assert res.passed, res.summary()


def test_multirate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = Diagram("multirate")
    u = d.add(Inport(port=1), name="u")
    fast = d.add(ZeroOrderHold(sample_time=0.01), name="fast")
    slow = d.add(ZeroOrderHold(sample_time=0.05), name="slow")
    acc = d.add(DiscreteIntegrator(sample_time=0.05), name="acc")
    s = d.add(Sum(), name="sum")
    d.chain(u, fast)
    d.chain(u, slow, acc)
    d.connect(fast, (s, 0))
    d.connect(acc, (s, 1))
    d.connect(s, d.add(Outport(port=1), name="y"))
    code = generate_c(d)
    assert code.base_period == pytest.approx(0.01)
    assert "rt_hit[1]" in code.files["multirate.c"]
    res = back_to_back(d, {"u": np.sin(np.arange(STEPS) * 0.3)}, STEPS, tolerance=1e-12, folder=tmp_path)
    assert res.passed, res.summary()


def test_workspace_variables_become_shared_parameters() -> None:
    d = harness(Gain(gain="Kp"), 1)
    d.workspace["Kp"] = 2.0
    d2 = d.add(Gain(gain="Kp"), name="g2")
    d.connect(d.block("dut"), d2)
    d.connect(d2, d.add(Outport(port=2), name="y_2"))
    code = generate_c(d)
    assert code.params["Kp"] == [2.0]
    assert sum("ECU" in k or k == "Kp" for k in code.params) == 1
    assert "dut_P.Kp[0]" in code.files["dut.c"]


def test_subsystem_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    inner = harness(Gain(gain=3.0), 1)
    d = Diagram("model")
    sub = d.add(Subsystem(system=inner), name="controller")
    plant = d.add(Integrator(), name="plant")  # continuous: fine, it is outside the generated subsystem
    d.chain(d.add(Constant(value=1.0)), sub, plant, d.add(Scope()))
    code = generate_c(sub)
    assert set(code.c_files) == {"controller.h", "controller.c"}
    assert {"controller_sil.c", "main_example.c", "controller_report.html"} <= set(code.files)
    with pytest.raises(CodegenError, match="continuous states"):
        generate_c(d)


def test_unsupported_blocks_are_listed() -> None:
    d = Diagram("bad")
    i = d.add(Integrator(), name="int")
    d.chain(d.add(Constant()), i, d.add(Outport(port=1)))
    with pytest.raises(CodegenError) as exc:
        generate_c(d)
    assert "int" in exc.value.blocks


def test_generated_header_api() -> None:
    code = generate_c(harness(Gain(gain=2.0), 1))
    h = code.files["dut.h"]
    for needle in ("void dut_initialize(void);", "void dut_step(void);", "extern dut_U_t dut_U;", "dut_BASE_PERIOD"):
        assert needle in h or needle.upper() in h


def test_main_example_and_report_compile(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import subprocess

    code = generate_c(harness(DiscretePID(P=1.0, I=2.0), 1))
    code.write(tmp_path)
    cc = find_compiler()
    proc = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", "-c", "dut.c", "main_example.c"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    report = (tmp_path / "dut_report.html").read_text()
    assert "Code generation report" in report and "dut" in report and 'href="#L' in report
    assert set(code.c_files) == {"dut.h", "dut.c"}


def test_a2l_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from nervus.codegen.a2l import fill_addresses, symbol_addresses
    from nervus.codegen.sil import build_shared_library
    from nervus.core import Variable

    d = harness(DiscretePID(P="Kp", I=2.0), 1)
    d.workspace.add(Variable("Kp", 1.5, "Proportional gain", "%/deg", min=0.0, max=10.0))
    code = generate_c(d)
    a2l = code.files["dut.a2l"]
    assert a2l.count("/begin") == a2l.count("/end")
    assert '/begin CHARACTERISTIC dut_P.Kp "Proportional gain"' in a2l
    assert "RL_FLOAT64 0 CM___deg 0.0 10.0" in a2l
    assert "MEASUREMENT dut_B.dut" in a2l
    lib = build_shared_library(code, tmp_path)
    filled = fill_addresses(a2l, symbol_addresses(lib))
    assert "@dut_P" not in filled and "@dut_B" not in filled


def test_outport_added_first_is_ordered_after_its_driver(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: an Out block added before its driver must not output last step's value in C."""
    d = Diagram("order")
    y = d.add(Outport(port=1), name="y")  # added first
    u = d.add(Inport(port=1), name="u")
    g = d.add(Gain(gain=2.0), name="g")
    d.chain(u, g, y)
    d.connect(d.add(Constant(value=1.0)), d.add(Outport(port=2), name="dummy"))
    d.blocks  # noqa: B018
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    d.connect(d.add(Inport(port=2), name="v"), z)
    d.connect(z, d.add(Outport(port=3), name="w"))
    res = back_to_back(d, {"u": np.arange(10.0), "v": np.arange(10.0)}, 10, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()


def test_data_types_bit_true(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Integer and fixed-point signals: the generated C matches the simulation bit for bit."""
    from nervus.blocks import DataTypeConversion

    d = Diagram("typed")
    u = d.add(Inport(port=1, output_type="int16"), name="sensor_raw")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    scale = d.add(Gain(gain=0.0123, output_type="fixdt(1, 16, 7)"), name="scaled")
    acc = d.add(Sum(signs="++", output_type="int8"), name="acc")  # saturating int8 sum
    wrap = d.add(DataTypeConversion(output_type="uint8", overflow="wrap", rounding="floor"), name="wrapped")
    temp = d.add(DataTypeConversion(output_type="fixdt(0, 8, 0.5, -40)"), name="temp")
    cmp_ = d.add(CompareToConstant(operator=">", constant=10.0), name="hot")
    d.chain(u, z, scale)
    d.connect(scale, (acc, 0))
    d.connect(z, (acc, 1))
    d.chain(scale, wrap)
    d.chain(scale, temp)
    d.chain(temp, cmp_)
    for k, (blk, name) in enumerate(((acc, "y_acc"), (wrap, "y_wrap"), (temp, "y_temp"), (cmp_, "y_hot"))):
        d.connect(blk, d.add(Outport(port=k + 1), name=name))
    rng = np.random.default_rng(7)
    raw = np.round(rng.uniform(-40000, 40000, 200))  # outside int16 too: saturates at the input
    res = back_to_back(d, {"sensor_raw": raw}, 200, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
    code = generate_c(d)
    h = code.files["typed.h"]
    assert "int16_T sensor_raw[1];" in h and "int8_T y_acc[1];" in h and "uint8_T y_temp[1];" in h
    assert "boolean_T y_hot[1];" in h
    # Saturation really happened, and the fixed-point temperature has 0.5 resolution with -40 offset.
    assert res.python["y_acc"].max() == 127.0 and res.python["y_acc"].min() == -128.0
    assert np.all(np.mod(res.python["y_temp"] + 40, 0.5) == 0)


def test_masked_subsystems_codegen(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from nervus.core import Variable

    def component() -> Subsystem:
        inner = Diagram()
        u = inner.add(Inport(port=1), name="u")
        g = inner.add(Gain(gain="K"), name="gain")
        inner.chain(u, g, inner.add(Outport(port=1), name="y"))
        sub = Subsystem(system=inner)
        sub.mask.add(Variable("K", 2.0))
        return sub

    d = Diagram("masked")
    d.workspace["K"] = 100.0  # shadowed inside the masks; must not become the shared field for them
    d.workspace["Kg"] = 3.0
    u = d.add(Inport(port=1), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    a = d.add(component(), name="a")
    b = d.add(component(), name="b")
    b.mask["K"] = "Kg * 2"
    d.chain(u, z, a)
    d.connect(z, b)
    d.connect(a, d.add(Outport(port=1), name="ya"))
    d.connect(b, d.add(Outport(port=2), name="yb"))
    res = back_to_back(d, {"u": np.arange(20.0)}, 20, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
    assert res.c["yb"][5, 0] == 30.0 and res.c["ya"][5, 0] == 10.0
    assert "K" not in generate_c(d).params  # the masked K is per-block, not the workspace K


def test_sample_time_offsets_match_the_first_step(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Blocks with an offset run first at their offset, but their outputs exist from the start (a delay's initial
    value, a hold's first input), exactly as in the simulation."""
    from nervus.blocks import DiscreteIntegrator, UnitDelay

    d = Diagram("offsets")
    u = d.add(Inport(port=1), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    hold = d.add(ZeroOrderHold(sample_time=[0.05, 0.02]), name="hold")
    delay = d.add(UnitDelay(initial=5.0, sample_time=[0.04, 0.03]), name="delay")
    integ = d.add(DiscreteIntegrator(initial=3.0, sample_time=[0.05, 0.02]), name="integ")
    scaled = d.add(Gain(gain=2.0), name="scaled")  # inherits the offset rate of the hold
    d.chain(u, z, hold, scaled)
    d.chain(z, delay)
    d.chain(z, integ)
    for k, blk in enumerate((scaled, delay, integ, z)):
        d.connect(blk, d.add(Outport(port=k + 1), name=f"y{k}"))
    res = back_to_back(d, {"u": np.arange(60.0) + 1.0}, 60, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
    assert res.c["y0"][0, 0] == 2.0 and res.c["y1"][0, 0] == 5.0 and res.c["y2"][0, 0] == 3.0
    assert "rt_first" in generate_c(d).files["offsets.c"]


def test_sil_rounds_fixed_point_inputs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: the SIL harness truncated the stored value of a fixed-point input (-76.3 became -76.2)."""
    d = Diagram("fx")
    u = d.add(Inport(port=1, output_type="fixdt(1, 16, 0.1, 0)"), name="u")
    d.chain(u, d.add(ZeroOrderHold(sample_time=0.01), name="z"), d.add(Outport(port=1), name="y"))
    vals = np.array([-76.3, -75.8, 12.3, -0.7, 1.0])
    res = back_to_back(d, {"u": vals}, len(vals), tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()


def test_block_names_that_are_c_keywords(tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = Diagram("kw")
    u = d.add(Inport(port=1), name="int")
    d.chain(u, d.add(ZeroOrderHold(sample_time=0.01), name="switch"), d.add(Gain(gain=2.0), name="NAN"))
    d.connect(d.block("NAN"), d.add(Outport(port=1), name="double"))
    res = back_to_back(d, {"int": np.arange(5.0)}, 5, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
    assert "kw_B.switch_[0]" in generate_c(d).files["kw.c"]


def test_block_names_that_collide_in_c(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Different block names that become the same C name (also with the _<n> and _o<port> suffixes, and a block
    field named like a workspace variable) get different fields."""
    d = Diagram("names")
    d.workspace["g_x_gain"] = 7.0  # not used: a block field must still not take its name
    u = d.add(Inport(port=1, value=[0.0, 0.0]), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    dm = d.add(Demux(num_outputs=2), name="a")  # fields a_o0, a_o1
    g0 = d.add(Gain(gain=5.0), name="a_o0")
    g1 = d.add(Gain(gain=2.0), name="g x")
    g2 = d.add(Gain(gain=3.0), name="g_x")
    g3 = d.add(Gain(gain=4.0), name="g_x_1")
    d.chain(u, z, dm)
    d.chain((dm, 0), g0, g1, g2, g3)
    d.connect(g3, d.add(Outport(port=1), name="y"))
    d.connect((dm, 1), d.add(Outport(port=2), name="y2"))
    inputs = {"u": RNG.uniform(-1, 1, (10, 2))}
    res = back_to_back(d, inputs, 10, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
    assert np.allclose(res.c["y"][:, 0], 120.0 * inputs["u"][:, 0])
    params = generate_c(d).params
    assert "g_x_gain" not in params and params["g_x_gain_1"] == [2.0]


def test_in_blocks_with_the_same_c_name_are_refused() -> None:
    d = Diagram("io")
    s = d.add(Sum(), name="s")
    d.connect(d.add(Inport(port=1), name="a b"), (s, 0))
    d.connect(d.add(Inport(port=2), name="a_b"), (s, 1))
    d.chain(s, d.add(ZeroOrderHold(sample_time=0.01)), d.add(Outport(port=1), name="y"))
    with pytest.raises(CodegenError, match="both become the C field a_b"):
        generate_c(d)


def test_a2l_of_integer_code_matches_the_struct(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: the A2L file described every signal of integer-only code as an 8-byte double."""
    import re
    import subprocess

    d = Diagram("ia")
    u = d.add(Inport(port=1, output_type="int8"), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    g = d.add(Gain(gain=2.0, output_type="int32"), name="g")
    c = d.add(CompareToConstant(operator=">", constant=3.0), name="c")
    f = d.add(Gain(gain=0.5, output_type="fixdt(1, 16, 7)"), name="f")
    d.chain(u, z, g)
    d.chain(z, c)
    d.chain(z, f)
    for k, blk in enumerate((g, c, f)):
        d.connect(blk, d.add(Outport(port=k + 1), name=f"y{k}"))
    code = generate_c(d, integer_only=True)
    a2l = code.files["ia.a2l"]
    meas = re.findall(r"MEASUREMENT ia_B\.(\w+) .*?\n\s+(\w+) (\w+) .*?@ia_B\+(\d+)", a2l, re.S)
    assert {(n, t) for n, t, _m, _o in meas} == {("u", "SBYTE"), ("z", "SBYTE"), ("g", "SLONG"), ("c", "UBYTE"),
                                                 ("f", "SWORD")}  # fmt: skip
    assert "COEFFS_LINEAR 0.0078125 0.0" in a2l  # f is shown in physical units
    code.write(tmp_path)
    prog = ["#include <stdio.h>", "#include <stddef.h>", '#include "ia.h"', "int main(void) {"]
    prog += [f'printf("{n} %zu\\n", offsetof(ia_B_t, {n}));' for n, _t, _m, _o in meas]
    (tmp_path / "chk.c").write_text("\n".join([*prog, "return 0;", "}"]))
    exe = tmp_path / "chk"
    subprocess.run([find_compiler(), "-o", str(exe), "chk.c", "ia.c", "-lm"], cwd=tmp_path, check=True)
    offsets = dict(
        line.split() for line in subprocess.run([str(exe)], capture_output=True, text=True).stdout.split("\n") if line
    )
    assert {n: int(o) for n, _t, _m, o in meas} == {n: int(o) for n, o in offsets.items()}


def test_misra_findings_are_parsed_with_windows_paths() -> None:
    """Regression: cppcheck's lines start with the file path, which on Windows has a drive letter and a colon."""
    from nervus.codegen.misra import _LINE

    out = (r"C:\Users\me\AppData\Local\Temp\tmp1\dut.c:12:5: style: misra violation [misra-c2012-10.4]" "\n"
           "/tmp/tmp2/dut.c:7:1: style: misra violation [misra-c2012-2.5]\n")  # fmt: skip
    found = [(m.group("file"), m.group("line"), m.group("rule")) for m in _LINE.finditer(out)]
    assert found == [(r"C:\Users\me\AppData\Local\Temp\tmp1\dut.c", "12", "10.4"), ("/tmp/tmp2/dut.c", "7", "2.5")]
