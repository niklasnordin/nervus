"""Hierarchical charts: nested and parallel states, history, events, cross-level transitions; and their C code."""

import numpy as np
import pytest
from test_chart import run_chart

from nervus import Diagram, ParameterError
from nervus.blocks import Inport, Outport, StateChart, ZeroOrderHold
from nervus.blocks.chart import parse_chart
from nervus.codegen.sil import back_to_back, find_compiler

POWER = """
input key, rpm, fault
output mode = 0, mil = 0, stalls = 0
event stalled

state off
  entry: mode = 0
state on parallel
  state engine
    state cranking
      entry: mode = 1
    state running
      entry: mode = 2
  state diagnosis
    state ok
      entry: mil = 0
    state failed
      entry: mil = 1

transition off -> on: key > 0.5
transition on -> off: key < 0.5
transition cranking -> running: rpm > 600
transition running -> cranking: rpm < 300 / stalls += 1; send(stalled)
transition ok -> failed: fault > 0.5 or stalled
transition failed -> ok: fault < 0.5 and after(3)
"""

HISTORY = """
input go, pause
output y = 0, order = 0

state idle
  entry: y = 0
state active
  history
  exit: order = (order * 10 + 2) % 1000
  state a
    entry: y = 1
    exit: order = (order * 10 + 1) % 1000
  state b
    entry: y = 2
    exit: order = (order * 10 + 1) % 1000

transition idle -> active: go > 0.5
transition active -> idle: pause > 0.5
transition a -> b: after(3)
transition b -> idle: go > 1.5
"""


def test_parse_tree() -> None:
    spec = parse_chart(POWER)
    names = [s.name for s in spec.states]
    on = spec.states[names.index("on")]
    assert on.parallel and [names[c] for c in on.children] == ["engine", "diagnosis"]
    assert spec.states[names.index("engine")].initial == names.index("cranking")
    assert spec.events == ["stalled"] and spec.hierarchical
    assert parse_chart(HISTORY).states[1].history


def test_parallel_regions_and_events() -> None:
    #            key: off, on ..................................., off
    key = [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0]
    rpm = [0, 0, 0, 700, 700, 200, 200, 700, 700, 700, 700, 700, 700]
    fault = [0] * 13
    out = run_chart(POWER, {"key": key, "rpm": rpm, "fault": fault}, ts=1.0)
    assert list(out["mode"]) == [0, 1, 1, 2, 2, 1, 1, 2, 2, 2, 2, 2, 0]
    # The stall in the engine region sends 'stalled': the diagnosis region fails in the same sample.
    assert list(out["mil"]) == [0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0]  # back to ok after(3)
    assert out["stalls"][-1] == 1
    # The state output is the innermost active state of the first region.
    names = [s.name for s in parse_chart(POWER).states]
    assert [names[int(k)] for k in out["state"][[0, 2, 4, 12]]] == ["off", "cranking", "running", "off"]


def test_history_and_exit_order() -> None:
    go = [0, 1, 0, 0, 0, 0, 0, 1, 0, 0]
    pause = [0, 0, 0, 0, 0, 1, 0, 0, 0, 0]
    out = run_chart(HISTORY, {"go": go, "pause": pause}, ts=1.0)
    # idle → active.a (y=1) → after 3 samples b (y=2) → pause: idle → go: back to b by history.
    assert list(out["y"]) == [0, 1, 1, 1, 2, 0, 0, 2, 2, 2]
    # Leaving 'active' exits the inner state first (1), then 'active' (2); a -> b exits a (1) before.
    assert out["order"][5] == 112


def test_cross_level_transition_and_errors() -> None:
    out = run_chart(HISTORY, {"go": [0, 1, 0, 0, 0, 2, 0], "pause": [0] * 7}, ts=1.0)
    assert list(out["y"]) == [0, 1, 1, 1, 2, 0, 0]  # b -> idle leaves 'active' from inside
    with pytest.raises(ParameterError, match="between parallel regions"):
        parse_chart(POWER + "transition cranking -> failed: 1\n")
    with pytest.raises(ParameterError, match="send\\(\\) takes the name of a declared event"):
        parse_chart("output y\nstate a\n  entry: send(nothing)\n")
    with pytest.raises(ParameterError, match="history needs child states"):
        parse_chart("state a\n  history\n")
    with pytest.raises(ParameterError, match="must be directly inside"):
        parse_chart("state a\n  state b\nstate c\ninitial b\n")


# A transition to an enclosing state, Python operators whose C needs care (floored %, and/or as values), and
# entry actions of the initial states that read inputs.
TO_PARENT = """
input u, go
output y = 0, m = 0, a = 0, o = 0, n_entry = 0, n_exit = 0, tis = 0
state p
  entry: n_entry += 1; y = u
  exit: n_exit += 1
  state c1
    during: tis = t_in_state; m = u % 3; a = u and go; o = go or u
  state c2
    entry: m = -u % 2.5
transition c1 -> c2: go > 0.5 and after(2)
transition c2 -> p: go < 0.5
"""


def test_transition_to_an_enclosing_state_exits_and_enters_it() -> None:
    out = run_chart(TO_PARENT, {"u": [-1, -1, -1, -1, 2, 2], "go": [1, 1, 1, 0, 0, 0]}, ts=1.0)
    names = [s.name for s in parse_chart(TO_PARENT).states]
    assert [names[int(k)] for k in out["state"]] == ["c1", "c1", "c2", "c1", "c1", "c1"]
    np.testing.assert_array_equal(out["n_entry"], [1, 1, 1, 2, 2, 2])  # c2 -> p leaves p and enters it again
    np.testing.assert_array_equal(out["n_exit"], [0, 0, 0, 1, 1, 1])
    assert out["y"][0] == -1 and out["m"][0] == 2  # Python's floored modulo: -1 % 3 == 2
    assert out["a"][0] == 1 and out["o"][0] == 1 and out["a"][4] == 0 and out["o"][4] == 2


@pytest.mark.skipif(find_compiler() is None, reason="no C compiler")
@pytest.mark.parametrize("chart", [POWER, HISTORY, TO_PARENT])
def test_hierarchical_chart_c_code(chart: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = StateChart(definition=chart, sample_time=0.01)
    d = Diagram("hc")
    blk = d.add(c, name="chart")
    rng = np.random.default_rng(3)
    steps = 400
    inputs = {}
    for k, name in enumerate(c.spec.inputs):
        src = d.add(Inport(port=k + 1), name=name)
        d.chain(src, d.add(ZeroOrderHold(sample_time=0.01)))
        d.connect(d.blocks[-1], (blk, k))
        levels = {"rpm": [0.0, 200.0, 700.0], "go": [0.0, 1.0, 2.0], "u": [-2.5, -1.0, 0.0, 1.7, 3.0]}.get(
            name, [0.0, 1.0]
        )
        inputs[name] = np.repeat(rng.choice(levels, steps // 8 + 1), 8)[:steps]
    for k in range(c.num_outputs()):
        d.connect((blk, k), d.add(Outport(port=k + 1), name=f"y{k}"))
    res = back_to_back(d, inputs, steps, tolerance=0.0, folder=tmp_path)
    assert res.passed, res.summary()
