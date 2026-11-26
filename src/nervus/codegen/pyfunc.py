"""Python Function blocks as C: a translator for the numeric subset of Python the blocks are written in.

Supported: scalar inputs and outputs; numbers, ``t``, the inputs, ``p[k]`` and local variables; ``+ - * / ** %
//``, comparisons (also chained), ``and``/``or``/``not``, ``x if c else y``; ``if``/``elif``/``else``,
assignments (also ``a, b = p[0], p[1]``), ``+=`` and the like, ``return`` (a tuple for several outputs);
``abs``, ``min``, ``max``, ``float``, ``int``; ``math``/``np`` functions (``sin``, ``atan``, ``exp``, ``sqrt``,
``hypot``, ``clip``, ``sign``, ``minimum``, ...) and constants (``pi``, ``e``, ``inf``); ``np.interp(x, [..],
[..])`` with lists of numbers. Anything else (loops, arrays, other functions) raises :class:`PyFuncError` with
the line, and the block stays in Python.
"""

from __future__ import annotations

import ast
import itertools
from typing import NoReturn

from nervus.codegen.generator import Emitter, c_float


class PyFuncError(Exception):
    pass


_FUNCTIONS = {
    "sin": "sin", "cos": "cos", "tan": "tan", "asin": "asin", "acos": "acos", "atan": "atan", "atan2": "atan2",
    "arcsin": "asin", "arccos": "acos", "arctan": "atan", "arctan2": "atan2", "sinh": "sinh", "cosh": "cosh",
    "tanh": "tanh", "exp": "exp", "log10": "log10", "log2": "log2", "sqrt": "sqrt", "floor": "floor",
    "ceil": "ceil", "fabs": "fabs", "abs": "fabs", "absolute": "fabs", "hypot": "hypot", "copysign": "copysign",
    "pow": "pow", "power": "pow", "fmod": "fmod", "trunc": "trunc", "minimum": "fmin", "maximum": "fmax",
    "fmin": "fmin", "fmax": "fmax", "expm1": "expm1", "log1p": "log1p", "cbrt": "cbrt",
}  # fmt: skip
_CONSTANTS = {
    "pi": "3.141592653589793",
    "e": "2.718281828459045",
    "inf": "INFINITY",
    "nan": "NAN",
    "tau": "6.283185307179586",
}
_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
_CMPS = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}

HELPERS = """\
static real_T acc_pymod(real_T a, real_T b) /* Python's a % b: the sign of b */
{
    real_T r = fmod(a, b);
    if ((r != 0.0) && ((r < 0.0) != (b < 0.0))) { r += b; }
    return r;
}

static real_T acc_sign(real_T x) { return (x > 0.0) ? 1.0 : ((x < 0.0) ? -1.0 : x); }
"""


class _Translator:
    def __init__(self, e: Emitter, fn: ast.FunctionDef, out_vars: list[str], time: str) -> None:
        self.e = e
        self.out_vars = out_vars
        self.locals: set[str] = set()
        self.tables = 0
        args = [a.arg for a in fn.args.args]
        if fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.defaults:
            self.fail(fn, "only plain arguments f(t, u0, u1, ...)")
        n_in = e.block.num_inputs()
        if len(args) != n_in + 1:
            self.fail(fn, f"f must take t and {n_in} input(s)")
        self.names = {args[0]: time, **{a: e.u(k, "0") for k, a in enumerate(args[1:])}}
        assigned = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        self.arg_copies = {a: self.names[a] for a in args if a in assigned}  # arguments the code changes
        self.locals |= set(self.arg_copies)
        self.n_p = len(_flat(e.block.params.get("parameters", [])))
        self.p = e.param("parameters") if self.n_p else ""

    def fail(self, node: ast.AST, what: str) -> NoReturn:
        line = getattr(node, "lineno", 0)
        raise PyFuncError(f"{self.e.path}: line {line}: {what} cannot become C code")

    # statements -----------------------------------------------------------------------------------------------

    def body(self, stmts: list[ast.stmt]) -> list[str]:
        out: list[str] = []
        for st in stmts:
            out += self.stmt(st)
        return out

    def stmt(self, st: ast.stmt) -> list[str]:
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
            return []  # docstring
        if isinstance(st, ast.Pass):
            return []
        if isinstance(st, ast.Return):
            if st.value is None:
                self.fail(st, "return without a value")
            assert st.value is not None
            values = st.value.elts if isinstance(st.value, ast.Tuple) else [st.value]
            if len(values) != len(self.out_vars):
                self.fail(st, f"returning {len(values)} value(s) for {len(self.out_vars)} output(s)")
            return [*(f"{v} = {self.expr(x)};" for v, x in zip(self.out_vars, values, strict=True)), "break;"]
        if isinstance(st, ast.Assign):
            if len(st.targets) != 1:
                self.fail(st, "chained assignment")
            return self.assign(st.targets[0], st.value, st)
        if isinstance(st, ast.AugAssign):
            if not isinstance(st.target, ast.Name):
                self.fail(st, "this assignment")
            assert isinstance(st.target, ast.Name)
            rhs = ast.BinOp(left=ast.Name(st.target.id, ast.Load()), op=st.op, right=st.value)
            ast.copy_location(rhs, st)
            return self.assign(st.target, rhs, st)
        if isinstance(st, ast.AnnAssign) and st.value is not None:
            return self.assign(st.target, st.value, st)
        if isinstance(st, ast.If):
            lines = [f"if ({self.cond(st.test)}) {{", *_indent(self.body(st.body)), "}"]
            if st.orelse:
                lines[-1] = "} else {"
                lines += [*_indent(self.body(st.orelse)), "}"]
            return lines
        self.fail(st, f"the statement {type(st).__name__}")

    def assign(self, target: ast.expr, value: ast.expr, st: ast.stmt) -> list[str]:
        if isinstance(target, ast.Name):
            return [f"{self.local(target.id, st)} = {self.expr(value)};"]
        if isinstance(target, ast.Tuple) and all(isinstance(x, ast.Name) for x in target.elts):
            if not isinstance(value, ast.Tuple) or len(value.elts) != len(target.elts):
                self.fail(st, "unpacking anything but a tuple of the same length")
            assert isinstance(value, ast.Tuple)
            temps = [f"tmp{k}" for k in range(len(value.elts))]
            lines = ["{", *(f"    real_T {tmp} = {self.expr(x)};" for tmp, x in zip(temps, value.elts, strict=True))]
            lines += [f"    {self.local(n.id, st)} = {tmp};"  # type: ignore[attr-defined]
                      for n, tmp in zip(target.elts, temps, strict=True)]  # fmt: skip
            return [*lines, "}"]
        self.fail(st, "this assignment target")

    def local(self, name: str, node: ast.AST) -> str:
        self.locals.add(name)
        return f"py_{name}"

    # expressions ----------------------------------------------------------------------------------------------

    def cond(self, n: ast.expr) -> str:
        if isinstance(n, ast.Compare):
            parts, left = [], n.left
            for op, right in zip(n.ops, n.comparators, strict=True):
                if type(op) not in _CMPS:
                    self.fail(n, "this comparison")
                parts.append(f"({self.expr(left)} {_CMPS[type(op)]} {self.expr(right)})")
                left = right
            return parts[0] if len(parts) == 1 else "(" + " && ".join(parts) + ")"
        if isinstance(n, ast.BoolOp):
            joiner = " && " if isinstance(n.op, ast.And) else " || "
            return "(" + joiner.join(self.cond(v) for v in n.values) + ")"
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return f"(!{self.cond(n.operand)})"
        if isinstance(n, ast.Constant) and isinstance(n.value, bool):
            return "1" if n.value else "0"
        return f"({self.expr(n)} != 0.0)"

    def expr(self, n: ast.expr) -> str:
        if isinstance(n, ast.Constant) and isinstance(n.value, bool | int | float) and not isinstance(n.value, complex):
            return c_float(float(n.value))
        if isinstance(n, ast.Name):
            if n.id in self.locals:
                return f"py_{n.id}"
            if n.id in self.names:
                return self.names[n.id]
            if n.id in ("True", "False"):
                return "1.0" if n.id == "True" else "0.0"
            self.fail(n, f"the name {n.id!r}")
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("math", "np", "numpy"):
            if n.attr in _CONSTANTS:
                return _CONSTANTS[n.attr]
            self.fail(n, f"{n.value.id}.{n.attr}")
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == "p":
            k = _constant_int(n.slice)
            if k is None or not -self.n_p <= k < self.n_p:
                self.fail(n, "p[...] with anything but a constant index within the parameters")
            assert k is not None
            return self.e.at(self.p, str(k % self.n_p))
        if isinstance(n, ast.BinOp):
            a, b = self.expr(n.left), self.expr(n.right)
            if type(n.op) in _BINOPS:
                return f"({a} {_BINOPS[type(n.op)]} {b})"
            if isinstance(n.op, ast.Pow):
                return f"pow({a}, {b})"
            if isinstance(n.op, ast.Mod):
                return f"acc_pymod({a}, {b})"
            if isinstance(n.op, ast.FloorDiv):
                return f"floor({a} / {b})"
            self.fail(n, "this operator")
        if isinstance(n, ast.UnaryOp):
            if isinstance(n.op, ast.USub):
                return f"(-{self.expr(n.operand)})"
            if isinstance(n.op, ast.UAdd):
                return self.expr(n.operand)
            if isinstance(n.op, ast.Not):
                return f"({self.cond(n.operand)} ? 0.0 : 1.0)"
        if isinstance(n, ast.Compare | ast.BoolOp):
            return f"({self.cond(n)} ? 1.0 : 0.0)"
        if isinstance(n, ast.IfExp):
            return f"({self.cond(n.test)} ? {self.expr(n.body)} : {self.expr(n.orelse)})"
        if isinstance(n, ast.Call):
            return self.call(n)
        self.fail(n, f"the expression {type(n).__name__}")

    def call(self, n: ast.Call) -> str:
        if n.keywords:
            self.fail(n, "keyword arguments")
        f = n.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        module = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
        if isinstance(f, ast.Attribute) and module not in ("math", "np", "numpy"):
            self.fail(n, f"calling {module}.{name}")
        if name == "interp" and module:
            return self.interp(n)
        args = [self.expr(a) for a in n.args]
        if not module:
            if name in ("min", "max") and len(args) >= 2:
                out = args[0]
                for a in args[1:]:
                    out = f"{'fmin' if name == 'min' else 'fmax'}({out}, {a})"
                return out
            if name == "abs" and len(args) == 1:
                return f"fabs({args[0]})"
            if name == "float" and len(args) == 1:
                return args[0]
            if name == "int" and len(args) == 1:
                return f"trunc({args[0]})"
            if name == "bool" and len(args) == 1:
                return f"({self.cond(n.args[0])} ? 1.0 : 0.0)"
            self.fail(n, f"calling {name}()")
        if name == "clip" and len(args) == 3:
            return f"fmin(fmax({args[0]}, {args[1]}), {args[2]})"
        if name == "sign" and len(args) == 1:
            return f"acc_sign({args[0]})"
        if name == "square" and len(args) == 1:
            return f"({args[0]} * {args[0]})"
        if name == "log":
            if len(args) == 1:
                return f"log({args[0]})"
            if len(args) == 2:
                return f"(log({args[0]}) / log({args[1]}))"
        if name == "degrees" and len(args) == 1:
            return f"({args[0]} * (180.0 / 3.141592653589793))"
        if name == "radians" and len(args) == 1:
            return f"({args[0]} * (3.141592653589793 / 180.0))"
        if name in _FUNCTIONS:
            return f"{_FUNCTIONS[name]}({', '.join(args)})"
        self.fail(n, f"{module}.{name}()")

    def interp(self, n: ast.Call) -> str:
        if len(n.args) != 3:
            self.fail(n, "np.interp with more than 3 arguments")
        xp, fp = (_numbers(a) for a in n.args[1:])
        if xp is None or fp is None or len(xp) != len(fp) or len(xp) < 1:
            self.fail(n, "np.interp with anything but two lists of numbers of the same length")
        assert xp is not None and fp is not None
        if any(b < a for a, b in itertools.pairwise(xp)):
            self.fail(n, "np.interp with decreasing points")
        name = f"acc_tab_{self.e.cname}_{self.tables}"
        self.tables += 1
        self.e.gen.extra_globals.append(
            f"static const real_T {name}[{2 * len(xp)}] = {{{', '.join(c_float(v) for v in [*xp, *fp])}}};"
        )
        return f"acc_interp({name}, &{name}[{len(xp)}], {len(xp)}u, 1u, {self.expr(n.args[0])}, 0)"


def translate(e: Emitter, out_vars: list[str], time: str) -> list[str]:
    """C statements computing a Python Function block's outputs into ``out_vars`` (one C lvalue per output)."""
    b = e.block
    if any(w != 1 for w in e.in_widths) or any(w != 1 for w in e.out_widths):
        raise PyFuncError(f"{e.path}: only scalar inputs and outputs can become C code")
    try:
        module = ast.parse(str(b.params["code"]))
    except SyntaxError as exc:
        raise PyFuncError(f"{e.path}: syntax error") from exc
    fn = None
    for st in module.body:
        if isinstance(st, ast.FunctionDef) and st.name == "f":
            fn = st
        elif not isinstance(st, ast.Import | ast.ImportFrom) and not (
            isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
        ):
            raise PyFuncError(f"{e.path}: line {st.lineno}: code outside f() cannot become C code")
    if fn is None:
        raise PyFuncError(f"{e.path}: no function f")
    if fn.decorator_list:
        raise PyFuncError(f"{e.path}: decorators cannot become C code")
    tr = _Translator(e, fn, out_vars, time)
    body = tr.body(fn.body)
    decls = [f"real_T py_{name} = {tr.arg_copies.get(name, '0.0')};" for name in sorted(tr.locals)]
    return ["do {", *_indent(decls + body), "} while (0);"]


def _indent(lines: list[str]) -> list[str]:
    return [f"    {x}" for x in lines]


def _constant_int(n: ast.expr) -> int | None:
    if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool):
        return n.value
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
        k = _constant_int(n.operand)
        return -k if k is not None else None
    return None


def _numbers(n: ast.expr) -> list[float] | None:
    if not isinstance(n, ast.List | ast.Tuple):
        return None
    out = []
    for x in n.elts:
        if (isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.USub) and isinstance(x.operand, ast.Constant)
                and isinstance(x.operand.value, int | float)):  # fmt: skip
            x = ast.Constant(-x.operand.value)
        if not isinstance(x, ast.Constant) or not isinstance(x.value, int | float) or isinstance(x.value, bool):
            return None
        out.append(float(x.value))
    return out


def _flat(v: object) -> list[float]:
    import numpy as np

    return [float(x) for x in np.asarray(v, dtype=float).ravel()]
