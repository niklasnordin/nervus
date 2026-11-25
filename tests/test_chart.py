import numpy as np
import pytest

from nervus import Diagram, ParameterError, simulate
from nervus.blocks import FromWorkspace, Inport, Outport, StateChart, Terminator, ZeroOrderHold
from nervus.blocks.chart import parse_chart
from nervus.codegen.sil import back_to_back, find_compiler

ENGINE = """
# Engine operating modes
input rpm, key
output mode = 0, fuel = 0
local starts = 0

state off
  entry: mode = 0; fuel = 0

state cranking
  entry: mode = 1; starts += 1
  during: fuel = 1

state running
  entry: mode = 2; fuel = 1

transition off -> cranking: key > 0.5
transition cranking -> running: rpm > 600
transition cranking -> off: after(5) / fuel = 0
transition running -> off: key < 0.5 or rpm < 300
"""


def run_chart(
    definition: str, inputs: dict[str, list[float]], ts: float = 0.1, **params: object
) -> dict[str, np.ndarray]:
    chart = StateChart(definition=definition, sample_time=ts, **params)
    d = Diagram()
    c = d.add(chart, name="chart")
    n = len(next(iter(inputs.values())))
    k = np.arange(n) * ts
    for p, name in enumerate(chart.spec.inputs):
        src = d.add(FromWorkspace(time=k, data=inputs[name], interpolation="hold"), name=name)
        d.connect(src, (c, p))
    for p in range(chart.num_outputs()):
        d.connect((c, p), d.add(Terminator()))
    res = simulate(d, float(k[-1]), solver="rk4", step=ts)
    out = {name: res.signal("chart", p) for p, (name, _) in enumerate(chart.spec.outputs)}
    if chart.params["state_output"]:
        out["state"] = res.signal("chart", len(chart.spec.outputs))
    return out


def test_parse() -> None:
    spec = parse_chart(ENGINE)
    assert spec.inputs == ["rpm", "key"]
    assert [n for n, _ in spec.outputs] == ["mode", "fuel"]
    assert [s.name for s in spec.states] == ["off", "cranking", "running"]
    assert len(spec.transitions) == 4


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("state a\ntransition a -> b: 1", "unknown state 'b'"),
        ("input u\nstate a\n  entry: u = 1", "not an output or local"),
        ("output y\nstate a\n  entry: y = __import__('os')", "can be called"),
        ("output y\nstate a\n  entry: y = z", "unknown name 'z'"),
        ("state a\nstate a", "defined twice"),
        ("nonsense", "expected input"),
        ("", "at least one state"),
    ],
)
def test_parse_errors(text: str, message: str) -> None:
    with pytest.raises(ParameterError, match=message):
        parse_chart(text)


def test_engine_modes_sequence() -> None:
    key = [0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    rpm = [0, 200, 300, 700, 900, 900, 900, 900, 900, 900, 0, 0, 100, 150, 200, 200, 200, 200, 200, 200]
    out = run_chart(ENGINE, {"rpm": rpm, "key": key})
    #                 off  crank  crank  run ...                  off   off  crank ... after(5) -> off -> crank
    np.testing.assert_array_equal(out["state"][:11], [0, 1, 1, 2, 2, 2, 2, 2, 2, 0, 0])
    np.testing.assert_array_equal(out["mode"][:4], [0, 1, 1, 2])
    # Second start attempt: cranking at k=11, gives up after 5 samples in the state, then retries.
    assert out["state"][11] == 1
    assert out["state"][16] == 0 and out["fuel"][16] == 0  # after(5): 5 periods after entering at k=11
    assert out["state"][17] == 1


def test_t_in_state_and_locals() -> None:
    chart = """
    input go
    output y = 0, n = 0
    state idle
    state busy
      entry: n = n + 1
      during: y = t_in_state
    transition idle -> busy: go > 0
    transition busy -> idle: t_in_state >= 0.3
    """
    out = run_chart(chart, {"go": [0, 1, 1, 1, 1, 1, 1, 1]}, ts=0.1, state_output=False)
    assert "state" not in out
    # busy is entered at k=1; at k=2 one period has passed, so t_in_state = 0.1; leaves at k=4 (0.3 s)
    np.testing.assert_allclose(out["y"][:5], [0, 0, 0.1, 0.2, 0.2])
    assert out["n"][-1] == 2


def test_initial_state_is_entered_in_the_first_sample_with_its_inputs() -> None:
    chart = """
    input u
    output y = 0, tis = -1
    state a
      entry: y = u
      during: tis = t_in_state
    state b
      entry: y = 10 * u
    transition a -> b: after(3)
    """
    out = run_chart(chart, {"u": [5, 5, 6, 6, 6, 6]}, ts=1.0)
    # The entry action sees u at t = 0; the clock starts at the entry, like for a state entered later.
    np.testing.assert_array_equal(out["y"], [5, 5, 5, 60, 60, 60])
    np.testing.assert_array_equal(out["tis"][:3], [0, 1, 2])
    np.testing.assert_array_equal(out["state"], [0, 0, 0, 1, 1, 1])


def test_chart_ports_follow_definition() -> None:
    c = StateChart(definition=ENGINE)
    assert c.num_inputs() == 2 and c.num_outputs() == 3
    c.set_param("state_output", False)
    assert c.num_outputs() == 2


@pytest.mark.skipif(find_compiler() is None, reason="no C compiler")
def test_chart_codegen_back_to_back(tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = Diagram("modes")
    rpm = d.add(Inport(port=1), name="rpm")
    key = d.add(Inport(port=2), name="key")
    zr, zk = d.add(ZeroOrderHold(sample_time=0.01)), d.add(ZeroOrderHold(sample_time=0.01))
    c = d.add(StateChart(definition=ENGINE), name="chart")
    d.chain(rpm, zr)
    d.chain(key, zk)
    d.connect(zr, (c, 0))
    d.connect(zk, (c, 1))
    for p, name in enumerate(["mode", "fuel", "state"]):
        d.connect((c, p), d.add(Outport(port=p + 1), name=name))
    rng = np.random.default_rng(1)
    steps = 300
    key_sig = (np.cumsum(rng.random(steps) > 0.9) % 2).astype(float)
    rpm_sig = np.clip(np.cumsum(rng.normal(0, 80, steps)), 0, 1500)
    res = back_to_back(d, {"rpm": rpm_sig, "key": key_sig}, steps, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
    assert len(set(res.c["state"][:, 0])) >= 2  # the test actually visits several states


def test_chart_params_from_workspace_and_c(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import numpy as np

    from nervus import Diagram, simulate
    from nervus.blocks import Inport, Outport, StateChart, ZeroOrderHold
    from nervus.codegen import generate_c
    from nervus.core import Variable

    text = "input u\noutput y = 0\nparam thr_on, thr_off\nstate a\nstate b\n  entry: y = 1\ntransition a -> b: u > thr_on\ntransition b -> a: u < thr_off\n"
    d = Diagram("cp")
    d.workspace.add(Variable("thr_on", 0.8, data_type="fixdt(1,16,8)"))
    d.workspace["thr_off"] = 0.2
    u = d.add(Inport(port=1), name="u")
    c = d.add(StateChart(definition=text, state_output=False, sample_time=0.01), name="c")
    d.chain(u, d.add(ZeroOrderHold(sample_time=0.01)), c, d.add(Outport(port=1), name="y"))
    assert simulate(d, 0.05, inputs={"u": 0.9})["y"][-1] == 1.0
    assert simulate(d, 0.05, inputs={"u": 0.9}, params={"thr_on": 0.95})["y"][-1] == 0.0  # calibratable
    code = generate_c(d)
    assert "int16_T thr_on[1];" in code.files["cp.h"] and "real_T thr_off[1];" in code.files["cp.h"]
    from nervus.codegen.sil import back_to_back, find_compiler

    if find_compiler() is not None:
        k = np.arange(100)
        res = back_to_back(d, {"u": np.sin(0.1 * k)}, 100, tolerance=0.0, folder=tmp_path)
        assert res.passed, res.summary()
    bad = Diagram()
    bad.add(StateChart(definition=text.replace("thr_off", "missing_thr")), name="c2")
    with pytest.raises(Exception, match="not a workspace or mask variable"):
        simulate(bad, 0.1)
