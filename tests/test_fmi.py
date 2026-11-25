"""FMI 2.0: export a model as an FMU, and run FMUs as blocks."""

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fmpy")
from fmpy import read_model_description, simulate_fmu
from fmpy.validation import validate_fmu

from nervus import simulate
from nervus.blocks import FMU
from nervus.codegen.sil import find_compiler

EXAMPLES = Path(__file__).parent.parent / "examples"
pytestmark = pytest.mark.skipif(find_compiler() is None, reason="no C compiler")


def _throttle():  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location("throttle_ecu", EXAMPLES / "throttle_ecu.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_export_is_a_valid_fmu(tmp_path: Path) -> None:
    from nervus.codegen.fmu import export_fmu

    d = _throttle().build()
    fmu = export_fmu(d.block("ECU"), tmp_path / "ECU")
    assert fmu.suffix == ".fmu" and validate_fmu(str(fmu)) == []
    md = read_model_description(str(fmu))
    kinds = {v.name: v.causality for v in md.modelVariables}
    assert kinds["target_deg"] == "input" and kinds["duty_pct"] == "output" and kinds["Kp"] == "parameter"
    assert float(md.defaultExperiment.stepSize) == 0.01
    # fmpy runs it: a step of the target saturates the duty cycle at 100 %.
    t = np.arange(0, 1.0, 0.01)
    sig = np.array([(x, 60.0 if x >= 0.2 else 5.0, 5.0) for x in t],
                   dtype=[("time", float), ("target_deg", float), ("tps_deg", float)])  # fmt: skip
    r = simulate_fmu(str(fmu), stop_time=0.99, output_interval=0.01, input=sig, start_values={"Kp": 3.0})
    assert np.max(r["duty_pct"]) == 100.0 and r["duty_pct"][0] == 0.0


def test_fmu_block_replaces_the_subsystem(tmp_path: Path) -> None:
    """The ECU exported as an FMU and put back into the plant model: the same controller, but its output comes one
    communication step (10 ms) later, as in every co-simulation. During the fast tip-in the angle lags by up to
    13°; the peak and the steady state are the same."""
    from nervus.codegen.fmu import export_fmu

    m = _throttle()
    original = m.build()
    ref = simulate(original)
    fmu_path = export_fmu(original.block("ECU"), tmp_path / "ECU.fmu")
    d = m.build()
    ecu = d.block("ECU")
    block = d.add(FMU(fmu_file=str(fmu_path)), name="ecu_fmu")
    assert block.input_names() == ["target_deg", "tps_deg"] and block.period() == pytest.approx(0.01)
    for c in list(d.connections):
        if c.dst is ecu:
            d.disconnect(c)
            d.connect((c.src, c.src_port), (block, c.dst_port))
        elif c.src is ecu:
            d.disconnect(c)
            d.connect((block, c.src_port), (c.dst, c.dst_port))
    d.remove(ecu)
    res = simulate(d)
    a, b = np.ravel(ref["plate_angle"]), np.interp(ref.t, res.t, np.ravel(res["plate_angle"]))
    assert np.max(np.abs(a - b)) < 15.0 and np.max(b) == pytest.approx(np.max(a), abs=0.5)
    assert b[-1] == pytest.approx(a[-1], abs=0.05)  # the same steady state
    # Parameters set from the block: a higher gain gives a faster rise.
    block.set_param("parameters", "Kp = 3.0")
    fast = simulate(d)
    k = np.searchsorted(fast.t, 0.25)
    assert np.ravel(fast["plate_angle"])[k] > np.ravel(res["plate_angle"])[np.searchsorted(res.t, 0.25)]
    with pytest.raises(Exception, match="no parameter"):
        block.set_param("parameters", "nonsense = 1")
        block.validate_params()


def test_fmu_with_integer_and_fixed_point_interface(tmp_path: Path) -> None:
    """Regression: the FMU read and wrote int16/fixed-point fields as doubles (wrong values, corrupted memory)."""
    from nervus import Diagram
    from nervus.blocks import Gain, Inport, Outport, ZeroOrderHold
    from nervus.codegen.fmu import export_fmu

    d = Diagram("tf")
    u = d.add(Inport(port=1, output_type="int16"), name="u")
    t = d.add(Inport(port=2, output_type="fixdt(0, 8, 0.5, -40)"), name="temp")
    g = d.add(Gain(gain=2.0, output_type="int16"), name="g")
    d.chain(u, d.add(ZeroOrderHold(sample_time=0.01), name="z"), g)
    d.connect(g, d.add(Outport(port=1), name="y"))
    d.chain(t, d.add(ZeroOrderHold(sample_time=0.01), name="zt"), d.add(Outport(port=2), name="temp_out"))
    fmu = export_fmu(d, tmp_path / "tf.fmu")
    assert validate_fmu(str(fmu)) == []
    sig = np.array([(0.0, 100.0, 21.3), (0.01, 40000.0, -50.0), (0.02, -7.0, 300.0)],
                   dtype=[("time", float), ("u", float), ("temp", float)])  # fmt: skip
    r = simulate_fmu(str(fmu), stop_time=0.03, output_interval=0.01, input=sig)
    # Each output comes after the step with the input before it: saturated at the int16 input and output, the
    # temperature rounded to 0.5 and saturated to [-40, 87.5].
    assert list(r["y"][1:4]) == [200.0, 32767.0, -14.0]
    assert list(r["temp_out"][1:4]) == [21.5, -40.0, 87.5]


def test_fmu_with_reusable_functions_loads(tmp_path: Path) -> None:
    """Regression: the FMU's library left out the reusable subsystem functions (undefined symbols)."""
    from nervus import Diagram
    from nervus.blocks import Gain, Inport, Outport, Subsystem, UnitDelay, ZeroOrderHold
    from nervus.codegen.fmu import export_fmu

    def trim() -> Subsystem:
        inner = Diagram()
        inner.chain(inner.add(Inport(port=1), name="u"), inner.add(Gain(gain=3.0), name="g"),
                    inner.add(UnitDelay(), name="ud"), inner.add(Outport(port=1), name="y"))  # fmt: skip
        return Subsystem(system=inner, sample_time=0.01, function_name="trim")

    d = Diagram("rf")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="z")
    d.chain(d.add(Inport(port=1), name="u"), z)
    for k in (1, 2):
        s = d.add(trim(), name=f"s{k}")
        d.chain(z, s, d.add(Outport(port=k), name=f"y{k}"))
    fmu = export_fmu(d, tmp_path / "rf.fmu")
    sig = np.array([(0.0, 1.0), (0.01, 2.0), (0.02, 3.0)], dtype=[("time", float), ("u", float)])
    r = simulate_fmu(str(fmu), stop_time=0.03, output_interval=0.01, input=sig)
    assert list(r["y1"]) == list(r["y2"]) == [0.0, 0.0, 3.0, 6.0]  # 3 u, one step late
