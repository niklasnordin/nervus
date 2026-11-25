import math

import numpy as np
import pytest

from nervus.core.expressions import format_value, parse_value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2", 2.0),
        ("-1.5e3", -1500.0),
        ("2*pi", 2 * math.pi),
        ("1/0.05", 20.0),
        ("inf", math.inf),
        ("-inf", -math.inf),
        ("sqrt(4)", 2.0),
        ("[1, 2, 3]", [1.0, 2.0, 3.0]),
        ("[1 2 3]", [1.0, 2.0, 3.0]),
        ("[1 2; 3 4]", [[1.0, 2.0], [3.0, 4.0]]),
        ("[[1, 0], [0, 1]]", [[1.0, 0.0], [0.0, 1.0]]),
        ("[-1 -2]", [-1.0, -2.0]),
        ("[0.1, 0.05]", [0.1, 0.05]),
    ],
)
def test_parse(text: str, expected: object) -> None:
    assert parse_value(text) == expected


@pytest.mark.parametrize("text", ["", "__import__('os')", "open('x')", "1 +", "x", "1/0", "lambda: 1"])
def test_reject(text: str) -> None:
    with pytest.raises(ValueError):
        parse_value(text)


@pytest.mark.parametrize("value", [1.0, 0.5, math.inf, -math.inf, [1.0, 2.5], [[1.0, 2.0], [3.0, 4.0]]])
def test_roundtrip(value: object) -> None:
    assert parse_value(format_value(value)) == value


def test_format_array() -> None:
    assert format_value(np.array([[1.0, 2.0], [3.0, 4.0]])) == "[1.0 2.0; 3.0 4.0]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[1, 2] * 2", [2.0, 4.0]),
        ("[1, 2] + [3, 4]", [4.0, 6.0]),  # element-wise, not list concatenation
        ("-[1, 2]", [-1.0, -2.0]),
        ("[0 0.5 1] * v_max", [0.0, 5.0, 10.0]),  # a MATLAB vector inside a larger expression
        ("v_max * [0, 0.5, 1]", [0.0, 5.0, 10.0]),
        ("2 * [1 2; 3 4]", [[2.0, 4.0], [6.0, 8.0]]),
        ("[1 - 2]", [-1.0]),  # as in MATLAB: spaces around a binary operator join
        ("[1 -2]", [1.0, -2.0]),
        ("[sqrt( 4 ) 2]", [2.0, 2.0]),
        ("v[k + 1]", 20.0),
    ],
)
def test_vector_arithmetic(text: str, expected: object) -> None:
    names = {"v_max": 10.0, "v": [10.0, 20.0], "k": 0.0}
    np.testing.assert_allclose(np.asarray(parse_value(text, names), dtype=float), np.asarray(expected))


@pytest.mark.parametrize("text", ["sqrt([1, 4])", "[1, 2][5]", "[1, 2] + [1, 2, 3]"])
def test_bad_vector_expressions_are_value_errors(text: str) -> None:
    with pytest.raises(ValueError):
        parse_value(text)


def test_vector_expression_in_a_block() -> None:
    from nervus import Diagram, simulate
    from nervus.blocks import Constant, Scope
    from nervus.core.errors import ModelError

    d = Diagram()
    d.workspace["v_max"] = 10.0
    c = d.add(Constant(value="[0 0.5 1] * v_max"), name="c")
    d.chain(c, d.add(Scope()))
    np.testing.assert_allclose(simulate(d, 0.1)["c"][0], [0.0, 5.0, 10.0])
    c.set_param("value", "[1, 2] + off")
    with pytest.raises(ModelError, match="off"):
        simulate(d, 0.1)  # an unknown name is reported as a model error
    d.workspace["off"] = [1.0, 2.0, 3.0]
    with pytest.raises(ModelError, match="value"):
        simulate(d, 0.1)  # shapes that do not match: a clean error naming the parameter
