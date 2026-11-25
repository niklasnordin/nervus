"""Safe parsing of parameter values typed by users, e.g. in the GUI property editor.

Accepts numbers, arithmetic (``2*pi``, ``1/0.05``), ``inf``, ``pi``, ``e``, Python-style lists
(``[1, 2, 3]``, ``[[1, 0], [0, 1]]``) and MATLAB-style matrices (``[1 2; 3 4]``). Nothing is executed:
expressions are evaluated from the syntax tree with a small whitelist.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

_NAMES = {"pi": math.pi, "e": math.e, "inf": math.inf, "Inf": math.inf, "nan": math.nan, "true": 1.0, "false": 0.0}
_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_FUNCS: dict[str, Callable[[Any], Any]] = {
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "sin": math.sin,
    "cos": math.cos,
    "abs": abs,
}


_GROUP = re.compile(r"\[([^\[\]]*)\]")
"""A bracketed group without brackets inside: a vector/matrix literal or a subscript."""
_OPERATORS = "+-*/^%"


def _elements(row: str) -> list[str]:
    """Split one row of a MATLAB matrix into its elements. Commas and spaces separate elements, but (as in MATLAB)
    a binary operator with spaces on both sides joins its neighbours (``[1 - 2]`` is ``-1``, ``[1 -2]`` two
    elements), and so does a token that ends with an operator or an open parenthesis."""
    items: list[str] = []
    for part in row.split(","):
        merged: list[str] = []
        join_next = False
        for tok in part.split():
            standalone = all(c in _OPERATORS for c in tok)
            if merged and (join_next or standalone or tok[0] in "*/^%"):
                merged[-1] += " " + tok
            else:
                merged.append(tok)
            last = merged[-1]
            join_next = last[-1] in _OPERATORS or last.count("(") > last.count(")")
        items.extend(merged)
    return items


def _convert_group(match: re.Match[str]) -> str:
    body = match.group(1).strip()
    if not body:
        return "[]"
    if ";" not in body and "," in body:  # already Python: a list or a subscript
        return match.group(0)
    rows = [r for r in (_elements(r) for r in body.split(";")) if r]
    if ";" in body:
        return "[" + ", ".join("[" + ", ".join(r) + "]" for r in rows) + "]"
    return "[" + ", ".join(rows[0]) + "]" if rows else "[]"


def _matlab_to_python(text: str) -> str:
    """MATLAB-style vectors and matrices, also inside a larger expression: ``[1 2; 3 4]`` -> ``[[1, 2], [3, 4]]``,
    ``[0 0.5 1] * v_max`` -> ``[0, 0.5, 1] * v_max``; MATLAB ``~=``, ``&&``, ``||`` and ``~`` -> Python ``!=``,
    ``and``, ``or``, ``not``."""
    text = text.replace("~=", "!=").replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"~(?!=)", " not ", text).strip()
    return _GROUP.sub(_convert_group, text)


class UnknownNameError(ValueError):
    """An expression refers to names that are not defined (e.g. workspace variables not available)."""

    def __init__(self, names: set[str]) -> None:
        super().__init__("unknown variable" + ("s " if len(names) > 1 else " ") + ", ".join(sorted(names)))
        self.names = names


def names_in(text: str) -> set[str]:
    """Variable names used by an expression (excluding built-in constants and functions)."""
    try:
        tree = ast.parse(_matlab_to_python(text.strip()), mode="eval")
    except SyntaxError:
        return set()
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in _NAMES and n.id not in called}


def _eval(node: ast.AST, names: Mapping[str, Any] | None = None) -> Any:
    names = names or {}
    if isinstance(node, ast.Expression):
        return _eval(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in names:
        return _as_operand(names[node.id])
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        v = _as_operand(_eval(node.operand, names))
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Compare) and all(type(op) in _COMPARE for op in node.ops):
        # Comparisons give 1.0 (true) or 0.0 (false), element-wise for arrays; chains like 0 < x < 3 work.
        left = _eval(node.left, names)
        result: Any = 1.0
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            right = _eval(right_node, names)
            result = np.logical_and(result, _COMPARE[type(op)](np.asarray(left), np.asarray(right)))
            left = right
        return float(result) if np.ndim(result) == 0 else np.asarray(result, dtype=float)
    if isinstance(node, ast.BoolOp):
        values = [np.asarray(_eval(v, names)) != 0 for v in node.values]
        combine = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
        result = values[0]
        for v in values[1:]:
            result = combine(result, v)
        return float(result) if np.ndim(result) == 0 else np.asarray(result, dtype=float)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        v = np.asarray(_eval(node.operand, names)) == 0
        return float(v) if np.ndim(v) == 0 else np.asarray(v, dtype=float)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        # Lists are vectors here: [1, 2] * 2 is [2, 4] (not list repetition), [1, 2] + [3, 4] is [4, 6].
        return _BINOPS[type(node.op)](_as_operand(_eval(node.left, names)), _as_operand(_eval(node.right, names)))
    if isinstance(node, ast.List | ast.Tuple):
        return [_eval(e, names) for e in node.elts]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and len(node.args) == 1
        and not node.keywords
    ):
        return _FUNCS[node.func.id](_eval(node.args[0], names))
    if isinstance(node, ast.Subscript):
        base = np.asarray(_eval(node.value, names), dtype=float)
        index = _eval(node.slice, names)
        idx = tuple(int(i) for i in index) if isinstance(index, list) else int(index)
        return base[idx].tolist() if isinstance(base[idx], np.ndarray) else float(base[idx])
    raise ValueError(f"unsupported expression: {ast.unparse(node)}")


def _as_operand(value: Any) -> Any:

    if isinstance(value, list | tuple):
        return np.asarray(value, dtype=float)
    if isinstance(value, np.ndarray) and value.ndim == 0:
        return float(value)
    return value


def parse_value(text: str, names: Mapping[str, Any] | None = None) -> Any:
    """Parse ``text`` into a float, a (nested) list of floats or an array.

    ``names`` maps variable names (e.g. workspace variables) to values. Raises :class:`UnknownNameError` if the
    expression uses names that are not defined, and :class:`ValueError` for any other problem.
    """
    text = text.strip()
    if not text:
        raise ValueError("value is empty")
    try:
        tree = ast.parse(_matlab_to_python(text), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"cannot parse {text!r}") from exc
    missing = names_in(text) - set(names or {})
    if missing:
        raise UnknownNameError(missing)
    try:
        return _eval(tree, names)
    except ZeroDivisionError as exc:
        raise ValueError("division by zero") from exc
    except (TypeError, IndexError, OverflowError) as exc:  # e.g. sqrt([1, 2]), v[5]: a clean error to report
        raise ValueError(f"cannot evaluate {text!r}: {exc}") from exc


def _nested(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_nested(x) for x in value) + "]"
    return format_value(value)


def format_value(value: Any) -> str:
    """Format a parameter value for display/editing, so that ``parse_value(format_value(v)) == v``."""

    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value) if not value.is_integer() else str(value)
    if isinstance(value, list):
        if value and all(isinstance(r, list) and r and isinstance(r[0], list) for r in value):  # 3-D and more
            return "[" + ", ".join(_nested(r) for r in value) + "]"
        if value and all(isinstance(r, list) for r in value):
            return "[" + "; ".join(" ".join(format_value(x) for x in r) for r in value) + "]"
        return "[" + ", ".join(format_value(x) for x in value) + "]"
    return str(value)
