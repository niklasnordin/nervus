"""Condition coverage and MC/DC (modified condition/decision coverage)."""

import pytest

from nervus import Diagram
from nervus.blocks import FromWorkspace, LogicalOperator, Scope
from nervus.coverage import ConditionCoverage, Coverage
from nervus.sim.simulator import simulate


def _seen(op: str, n: int, vectors: list[str]) -> ConditionCoverage:
    c = ConditionCoverage("d", op, n)
    for v in vectors:
        c.record(tuple(ch == "1" for ch in v))
    return c


def test_and_needs_n_plus_one_evaluations() -> None:
    """The classic minimal MC/DC set for a 3-input AND: all true, and each condition false alone."""
    c = _seen("AND", 3, ["111", "011", "101", "110"])
    assert c.mcdc() == (3, 3) and c.condition_outcomes() == (6, 6)


def test_condition_coverage_is_not_mcdc() -> None:
    """000 and 111 make every condition true and false (100 % condition coverage), but show none independently."""
    c = _seen("AND", 3, ["000", "111"])
    assert c.condition_outcomes() == (6, 6) and c.mcdc() == (0, 3)
    need = c.needed(1)
    assert "(1, 0, 1) → 0" in need  # with 111 seen, one more evaluation shows condition 2


@pytest.mark.parametrize(("op", "vectors", "done"), [
    ("OR", ["000", "100", "010", "001"], 3),
    ("OR", ["100", "010"], 0),
    ("XOR", ["00", "10"], 1),
    ("NAND", ["11", "01", "10"], 2),
    ("NOR", ["00", "10"], 1),
])  # fmt: skip
def test_other_operators(op: str, vectors: list[str], done: int) -> None:
    assert _seen(op, len(vectors[0]), vectors).mcdc()[0] == done


def _model(a: list[float], b: list[float], c: list[float]) -> Diagram:
    d = Diagram("logic")
    t = [float(k) for k in range(len(a))]
    srcs = [d.add(FromWorkspace(time=t, data=[[v] for v in vals], interpolation="hold"), name=n)
            for n, vals in (("enable", a), ("speed_ok", b), ("no_fault", c))]  # fmt: skip
    gate = d.add(LogicalOperator(operator="AND", num_inputs=3), name="permit")
    for k, s in enumerate(srcs):
        d.connect(s, (gate, k))
    d.connect(gate, d.add(Scope(), name="scope"))
    return d


def test_measured_in_a_simulation() -> None:
    cov = Coverage()
    simulate(_model([1, 0, 1, 1], [1, 1, 1, 1], [1, 1, 1, 0]), 3.5, coverage=cov)
    c = cov.conditions["permit"]
    assert [c.independent(i) for i in range(3)] == [True, False, True]
    assert cov.mcdc_percent == pytest.approx(200 / 3)
    missing = cov.mcdc_missing()["permit"]
    assert missing == ["condition 2: needs (1, 0, 1) → 0 (the pair (1, 1, 1) → 1 / (1, 0, 1) → 0)"]
    text = cov.summary()
    assert "MC/DC 67 %" in text and "permit: condition 2" in text
    assert "Condition and MC/DC coverage" in cov.to_html()


CHART = """
input a, b, c
output y = 0
state off
  entry: y = 0
state on
  entry: y = 1
transition off -> on: a > 0.5 and (b > 0.5 or not c > 0.5)
transition on -> off: a < 0.5
"""


def test_chart_guards() -> None:
    """A transition guard with several conditions is a decision too; the nested and/or/not is evaluated exactly."""
    from nervus.blocks import StateChart

    d = Diagram("chart")
    t = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    a = [[1], [0], [1], [0], [0], [0]]  # (a, b) = (1,1), then (1,0) with c = 0, then a = 0
    b = [[1], [1], [0], [0], [0], [0]]
    c = [[0], [0], [0], [0], [0], [0]]
    chart = d.add(StateChart(definition=CHART, sample_time=0.1), name="ctl")
    for k, (n, vals) in enumerate((("a", a), ("b", b), ("c", c))):
        d.connect(d.add(FromWorkspace(time=t, data=vals, interpolation="hold"), name=n), (chart, k))
    d.connect(chart, d.add(Scope(), name="scope"))
    cov = Coverage()
    simulate(d, 5.0, coverage=cov)
    guard = cov.conditions["ctl: off -> on"]
    assert guard.n == 3 and guard.operator == "a > 0.5 and (b > 0.5 or not c > 0.5)"
    assert [guard.independent(i) for i in range(3)] == [True, False, False]
    # b is masked while c is false (not c is true): to show b, a test needs c = 1
    assert "(1, 0, 1) → 0" in guard.needed(1)
    assert "ctl: off -> on" in cov.mcdc_missing()
