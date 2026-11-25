"""Coverage of the generated C code by the model's test cases (gcov)."""

from pathlib import Path

import pytest

from nervus import Diagram
from nervus.blocks import Constant, Inport, Outport, Saturation, Switch, ZeroOrderHold
from nervus.codegen.c_coverage import coverage_of_tests, gcov_path
from nervus.codegen.sil import find_compiler
from nervus.core import Check, TestCase

EXAMPLES = Path(__file__).parent.parent / "examples"
pytestmark = pytest.mark.skipif(find_compiler() is None or gcov_path() is None, reason="needs a C compiler and gcov")


def _limiter(cases: list[TestCase]) -> Diagram:
    """y = the input, or a fallback of 5 when the 'fault' input is set; limited to [0, 10]."""
    d = Diagram("lim")
    u = d.add(Inport(port=1), name="u")
    fault = d.add(Inport(port=2), name="fault")
    zu, zf = d.add(ZeroOrderHold(sample_time=0.1), name="zu"), d.add(ZeroOrderHold(sample_time=0.1), name="zf")
    fallback = d.add(Constant(value=5.0), name="fallback")
    sw = d.add(Switch(criterion="u1 != 0", threshold=0.0), name="choose")
    sat = d.add(Saturation(upper=10.0, lower=0.0), name="limit")
    d.chain(u, zu)
    d.chain(fault, zf)
    d.connect(fallback, (sw, 0))
    d.connect(zf, (sw, 1))
    d.connect(zu, (sw, 2))
    d.chain(sw, sat)
    d.connect(sat, d.add(Outport(port=1), name="y"))
    d.tests = cases
    return d


NORMAL = TestCase("normal", "", stop_time=1.0, inputs={"u": ([0, 1], [[2.0], [12.0]]), "fault": ([0, 1], [[0], [0]])},
                  checks=[Check("max", "y", {"value": 10.0})])  # fmt: skip
FAULT = TestCase("fault", "", stop_time=0.5, inputs={"u": ([0, 1], [[1.0], [1.0]]), "fault": ([0, 1], [[1], [1]])},
                 checks=[Check("final_value", "y", {"value": 5.0, "tolerance": 1e-9})])  # fmt: skip


NEGATIVE = TestCase("negative", "", stop_time=0.5, inputs={"u": ([0, 1], [[-3.0], [-3.0]]), "fault": ([0, 1], [[0], [0]])},
                    checks=[Check("min", "y", {"value": 0.0})])  # fmt: skip


def test_a_missing_test_shows_as_missed_branches() -> None:
    """The Switch and the Saturation are one-line conditionals: a missing test shows as branches never taken."""
    normal = coverage_of_tests(_limiter([NORMAL]))
    assert normal.runs == 1 and normal.line_percent == 100.0 and normal.branch_counts == (4, 6)
    missed = normal.missed_branches()
    assert any("fallback" in src for _f, _n, src, _k in missed)  # no test with a fault: the fallback never chosen
    assert "branch(es) never taken" in normal.summary()
    with_fault = coverage_of_tests(_limiter([NORMAL, FAULT]))
    assert with_fault.branch_counts == (5, 6)  # the lower limit is still never reached
    complete = coverage_of_tests(_limiter([NORMAL, FAULT, NEGATIVE]))
    assert complete.branch_percent == 100.0 and complete.missed_branches() == []


def test_report_and_example(tmp_path: Path) -> None:
    from nervus import load

    d = load(EXAMPLES / "throttle_ecu.nervus")  # its tests use a parameter set next to the model file
    cov = coverage_of_tests(d, d.block("ECU"))
    assert cov.runs == 3 and cov.line_percent == 100.0 and 0 < cov.branch_percent < 100.0
    page = cov.save_html(tmp_path / "c.html").read_text()
    assert "ECU.c" in page and "of the branches" in page


def test_needs_test_cases() -> None:
    from nervus.codegen.generator import CodegenError

    with pytest.raises(CodegenError, match="no test cases"):
        coverage_of_tests(_limiter([]))
