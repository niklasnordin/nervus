"""Integer-only code: no floating point between the inputs and the outputs, for processors without an FPU.

``generate_c(model, integer_only=True)``. Every signal is kept as its stored integer (``int16_T`` for
``fixdt(1, 16, 7)``, ``uint8_T`` for a boolean), and the blocks compute in an ``int64`` accumulator. Changing the
binary point is an integer multiplication, or a division rounded exactly as the simulation rounds (``nearest``: half
away from zero, ``floor``, ``ceil``, ``zero``), followed by the block's overflow handling (``saturate`` or
``wrap``). The result is the same as the simulation's, bit for bit.

The rules that make that possible:

* every signal has an integer or fixed-point type with **binary-point scaling** (a slope of 2^-n, no bias) or is
  boolean: no ``double`` signals;
* a gain is an exact binary fraction (``0.75``, ``3``, ``0.0078125``) or a variable with a fixed-point type (then
  it is calibratable as a stored integer); ``0.0123`` is neither, and the simulation would use a value the integer
  code cannot;
* an operation is refused if the simulation's double arithmetic could round its exact result (more than 52 bits);
* the blocks are the ones of integer ECU code: In/Out, Constant, Sum, Gain, Product (``*``), Bias, Abs, MinMax,
  comparisons, logic, Switch, Saturation, Unit Delay, Integer Delay, Zero-Order Hold, Data Type Conversion and
  subsystem ports. Others are refused with their names.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from nervus.core.datatypes import DataType, parse_datatype

if TYPE_CHECKING:
    from nervus.codegen.generator import BlockCode, Emitter

HELPERS = {
    "nearest": """/* x / d (d a power of two) rounded to the nearest, half away from zero: as the simulation rounds */
static int64_T nv_div_nearest(int64_T x, int64_T d)
{
    int64_T q = x / d;
    int64_T r = x % d;
    if ((2 * ((r < 0) ? -r : r)) >= d) {
        q += (x < 0) ? -1 : 1;
    } else {
        /* closer to q */
    }
    return q;
}""",
    "floor": """static int64_T nv_div_floor(int64_T x, int64_T d)
{
    int64_T q = x / d;
    if (((x % d) != 0) && (x < 0)) {
        q -= 1;
    } else {
        /* exact, or already rounded down */
    }
    return q;
}""",
    "ceil": """static int64_T nv_div_ceil(int64_T x, int64_T d)
{
    int64_T q = x / d;
    if (((x % d) != 0) && (x > 0)) {
        q += 1;
    } else {
        /* exact, or already rounded up */
    }
    return q;
}""",
    "zero": """static int64_T nv_div_zero(int64_T x, int64_T d)
{
    return x / d;
}""",
    "saturate": """static int64_T nv_sat(int64_T x, int64_T lo, int64_T hi)
{
    int64_T y = x;
    if (x > hi) {
        y = hi;
    } else if (x < lo) {
        y = lo;
    } else {
        /* in range */
    }
    return y;
}""",
    "wrap": """static int64_T nv_wrap(int64_T x, int64_T lo, int64_T span)
{
    int64_T r = (x - lo) % span;
    if (r < 0) {
        r += span;
    } else {
        /* in range */
    }
    return r + lo;
}""",
}


class IntegerCodeError(Exception):
    pass


def scale_of(dt: DataType, where: str) -> int:
    """n for a type whose value is stored · 2^-n (0 for integers and booleans)."""
    if dt.kind == "boolean":
        return 0
    if dt.kind != "fixed":
        raise IntegerCodeError(f"{where}: a {dt.name} signal; integer code needs integer or fixed-point types "
                               "(set the output types, e.g. int16 or fixdt(1, 16, 7), starting at the In blocks)")  # fmt: skip
    if dt.bias != 0:
        raise IntegerCodeError(f"{where}: {dt.name} has a bias; integer code needs binary-point scaling (no bias)")
    n = -math.log2(dt.slope)
    if abs(n - round(n)) > 1e-12 or abs(n) > 60:
        raise IntegerCodeError(f"{where}: {dt.name} has the slope {dt.slope:g}, not a power of two; integer code "
                               "needs binary-point scaling, e.g. fixdt(1, 16, 7)")  # fmt: skip
    return round(n)


def _bits(dt: DataType) -> int:
    return 1 if dt.kind == "boolean" else int(dt.bits)


def dyadic(value: float, where: str) -> tuple[int, int]:
    """(integer, n) with value = integer · 2^-n exactly, or an error if the value is not a binary fraction."""
    for n in range(0, 31):
        v = value * 2.0**n
        if v == math.floor(v) and abs(v) < 2**31:
            return int(v), n
    raise IntegerCodeError(f"{where}: {value:g} is not an exact binary fraction, so the integer code cannot use the "
                           "value the simulation uses; give the parameter a fixed-point type (a workspace variable "
                           "with e.g. data type fixdt(1, 16, 14)) or a value such as 0.75")  # fmt: skip


def threshold(value: float, n: int, op: str) -> tuple[str, int] | bool:
    """The integer comparison equal to ``stored·2^-n op value`` for integer ``stored``: (operator, integer), or
    ``True``/``False`` when the result is the same for every stored integer (e.g. ``== 0.5`` on an integer, or a
    comparison with ±inf)."""
    t = value * 2.0**n
    if math.isnan(t):
        return op == "!="  # NaN compares unequal to everything
    if abs(t) > 2.0**62:  # beyond every stored integer (at most 32 bits, scaled into int64)
        above = t > 0  # every stored value is below t
        return {">": not above, ">=": not above, "<": above, "<=": above, "==": False, "!=": True}[op]
    if t == math.floor(t):
        return op, int(t)
    if op in (">", "<="):
        return op, math.floor(t)
    if op in (">=", "<"):
        return op, math.ceil(t)
    return op == "!="  # not representable: never equal, always different


class Int:
    """Helpers for one block's integer code (wraps the template's :class:`Emitter`)."""

    def __init__(self, e: Emitter) -> None:
        self.e = e
        m = e.gen.model
        self.out_types = m.out_types[e.i]
        self.in_types = [m.out_types[s][p] for s, p in m.inputs[e.i]]

    def k_in(self, port: int) -> int:
        return scale_of(self.in_types[port], f"{self.e.path} input {port + 1}")

    def k_out(self, port: int = 0) -> int:
        return scale_of(self.out_types[port], f"{self.e.path} output {port + 1}")

    def u(self, port: int, ix: str) -> str:
        return f"(int64_T){self.e.u(port, ix)}"

    def nonzero(self, port: int, ix: str) -> str:
        """``input != 0``, with a zero of the input's signedness (MISRA C:2012 rule 10.4)."""
        unsigned = self.in_types[port].kind == "boolean" or self.in_types[port].c_type.startswith("u")
        return f"({self.e.u(port, ix)} != {'0u' if unsigned else '0'})"

    def helper(self, name: str) -> str:
        self.e.gen.quantizers.setdefault(f"nv_{name}", HELPERS[name])
        return name

    def exact(self, bits: int, what: str) -> None:
        if bits > 52:
            raise IntegerCodeError(f"{self.e.path}: {what} needs {bits} bits; the simulation computes in double "
                                   "(52 bits) and could round it, so the integer code would not match; use narrower "
                                   "types or fewer fraction bits")  # fmt: skip

    def store(self, acc: str, n_acc: int, port: int = 0) -> str:
        """``acc`` (int64, value = acc · 2^-n_acc) converted to output ``port``'s type with the block's rounding and
        overflow: a C expression of the output's stored type."""
        dt = self.out_types[port]
        if dt.kind == "boolean":
            return f"(({acc}) != 0) ? 1u : 0u"
        rounding, overflow = self.e.block.quantization()
        d = n_acc - self.k_out(port)
        x = acc
        if d > 0:
            x = f"nv_div_{self.helper(rounding)}({acc}, (int64_T){2**d})"
        elif d < 0:
            x = f"(({acc}) * (int64_T){2 ** (-d)})"
        lo, hi = int(dt.stored_min), int(dt.stored_max)
        if overflow == "wrap":
            self.helper("wrap")
            x = f"nv_wrap({x}, (int64_T){lo}, (int64_T){hi - lo + 1})"
        else:
            self.helper("saturate")
            x = f"nv_sat({x}, (int64_T){lo}, (int64_T){hi})"
        return f"({dt.c_type}){x}"


INT_TEMPLATES: dict[str, Callable[[Emitter], BlockCode]] = {}


def int_template(*type_ids: str) -> Callable[[Callable[[Emitter], BlockCode]], Callable[[Emitter], BlockCode]]:
    def deco(fn: Callable[[Emitter], BlockCode]) -> Callable[[Emitter], BlockCode]:
        for t in type_ids:
            INT_TEMPLATES[t] = fn
        return fn

    return deco


def _bc(outputs: list[str], update: list[str] | None = None) -> BlockCode:
    from nervus.codegen.generator import BlockCode

    return BlockCode(outputs, update or [])


@int_template("ports.inport")
def _inport(e: Emitter) -> BlockCode:
    from nervus.codegen.generator import c_identifier

    Int(e).k_out()  # checks the type
    field = c_identifier(e.path)
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.gen.ref('U')}.{field}[{ix}];"]))


@int_template("ports.outport")
def _outport(e: Emitter) -> BlockCode:
    from nervus.codegen.generator import c_identifier

    Int(e).k_in(0)
    field = c_identifier(e.path)
    return _bc(e.each(e.in_widths[0], lambda ix: [f"{e.gen.ref('Y')}.{field}[{ix}] = {e.u(0, ix)};"]))


@int_template("sources.constant")
def _constant(e: Emitter) -> BlockCode:
    dt = e.gen.model.out_types[e.i][0]
    values = np.atleast_1d(np.asarray(e.block.params["value"], dtype=float)).ravel()
    stored = dt.to_stored(dt.quantize(values)) if dt.kind != "boolean" else (values != 0).astype(float)
    vals = np.broadcast_to(stored, (e.w(),))
    return _bc([f"{e.y(0, str(k))} = ({dt.c_type}){int(v)};" for k, v in enumerate(vals)])


@int_template("math.sum")
def _sum(e: Emitter) -> BlockCode:
    t = Int(e)
    signs = e.block._signs()  # type: ignore[attr-defined]
    ks = [t.k_in(p) for p in range(len(signs))]
    n_acc = max(ks)
    bits = max(_bits(t.in_types[p]) + n_acc - ks[p] for p in range(len(signs))) + math.ceil(math.log2(len(signs)))
    t.exact(bits, "the sum")

    def body(ix: str) -> list[str]:
        terms = []
        for p, s in enumerate(signs):
            term = t.u(p, ix) if ks[p] == n_acc else f"({t.u(p, ix)} * (int64_T){2 ** (n_acc - ks[p])})"
            terms.append(("+ " if s == "+" else "- ") + term)
        acc = " ".join(terms).lstrip("+ ")
        if acc.startswith("- "):
            acc = "-" + acc[2:]
        return [f"{e.y(0, ix)} = {t.store(f'({acc})', n_acc)};"]

    return _bc(e.each(e.w(), body))


@int_template("math.gain")
def _gain(e: Emitter) -> BlockCode:
    t = Int(e)
    if e.block.params["multiplication"] == "matrix":
        raise IntegerCodeError(f"{e.path}: a matrix gain is not supported in integer code")
    k_u = t.k_in(0)
    expr = e.block.expressions.get("gain", "").strip()
    var = None
    if expr.isidentifier() and expr in e.gen.namespace:
        var = e.gen.model.diagram.root.workspace.variable(expr)
    gdt = parse_datatype(var.data_type) if var is not None else None
    if gdt is not None and gdt.kind == "fixed":  # a calibratable fixed-point gain: its stored integers
        n_g = scale_of(gdt, f"{e.path} gain {expr}")
        field = e.param("gain", typed=True)

        def gain(ix: str) -> str:
            return f"(int64_T){field}[{ix if np.size(e.block.params['gain']) > 1 else '0'}]"

        gbits = _bits(gdt)
    else:
        values = np.atleast_1d(np.asarray(e.block.params["gain"], dtype=float)).ravel()
        pairs = [dyadic(float(v), f"{e.path}: gain") for v in values]
        n_g = max(n for _g, n in pairs)
        ints = [g * 2 ** (n_g - n) for g, n in pairs]
        gbits = max(abs(i).bit_length() + 1 for i in ints)

        def gain(ix: str) -> str:
            if len(ints) == 1:
                return f"(int64_T){ints[0]}"
            return f"(int64_T)nv_{e.cname}_gain[{ix}]"

        if len(ints) > 1:
            e.gen.quantizers[f"nv_{e.cname}_gain"] = (f"static const int64_T nv_{e.cname}_gain[{len(ints)}] = "
                                                      f"{{{', '.join(str(i) for i in ints)}}};")  # fmt: skip
    t.exact(_bits(t.in_types[0]) + gbits, "the product of the gain and the input")
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {t.store(f'({gain(ix)} * {t.u(0, ix)})', n_g + k_u)};"]))


@int_template("math.product")
def _product(e: Emitter) -> BlockCode:
    t = Int(e)
    ops = e.block.params["ops"].replace("|", "")
    if "/" in ops:
        raise IntegerCodeError(f"{e.path}: division is not supported in integer code (only *)")
    ks = [t.k_in(p) for p in range(len(ops))]
    t.exact(sum(_bits(t.in_types[p]) for p in range(len(ops))), "the product")
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {t.store('(' + ' * '.join(t.u(p, ix) for p in range(len(ops))) + ')', sum(ks))};"]))  # fmt: skip


@int_template("math.bias")
def _bias(e: Emitter) -> BlockCode:
    t = Int(e)
    k = t.k_in(0)
    b, n_b = dyadic(float(np.ravel(e.block.params["bias"])[0]), f"{e.path}: bias")
    n = max(k, n_b)
    t.exact(_bits(t.in_types[0]) + n - k + 1, "the sum")

    def body(ix: str) -> str:
        return f"({t.u(0, ix)} * (int64_T){2 ** (n - k)}) + (int64_T){b * 2 ** (n - n_b)}"

    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {t.store(f'({body(ix)})', n)};"]))


@int_template("math.abs")
def _abs(e: Emitter) -> BlockCode:
    t = Int(e)
    k = t.k_in(0)
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {t.store(f'(({t.u(0, ix)} < 0) ? -{t.u(0, ix)} : {t.u(0, ix)})', k)};"]))  # fmt: skip


@int_template("math.minmax")
def _minmax(e: Emitter) -> BlockCode:
    t = Int(e)
    n_in = e.block.num_inputs()
    ks = [t.k_in(p) for p in range(n_in)]
    n = max(ks)
    cmp = "<" if e.block.params["function"] == "min" else ">"

    def body(ix: str) -> list[str]:
        vals = [t.u(p, ix) if ks[p] == n else f"({t.u(p, ix)} * (int64_T){2 ** (n - ks[p])})" for p in range(n_in)]
        lines = ["{", f"    int64_T m = {vals[0]};"]
        lines += [f"    if ({v} {cmp} m) {{ m = {v}; }} else {{ /* keep */ }}" for v in vals[1:]]
        return [*lines, f"    {e.y(0, ix)} = {t.store('m', n)};", "}"]

    return _bc(e.each(e.w(), body))


def _compare(t: Int, a: tuple[int, str], b: tuple[int, str], op: str) -> str:
    (ka, xa), (kb, xb) = a, b
    n = max(ka, kb)
    xa = xa if ka == n else f"({xa} * (int64_T){2 ** (n - ka)})"
    xb = xb if kb == n else f"({xb} * (int64_T){2 ** (n - kb)})"
    return f"({xa} {op} {xb})"


@int_template("logic.relational")
def _relational(e: Emitter) -> BlockCode:
    t = Int(e)
    op = e.block.params["operator"]
    k0, k1 = t.k_in(0), t.k_in(1)
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {_compare(t, (k0, t.u(0, ix)), (k1, t.u(1, ix)), op)} ? 1u : 0u;"]))  # fmt: skip


@int_template("logic.compare_to_constant")
def _compare_constant(e: Emitter) -> BlockCode:
    t = Int(e)
    k = t.k_in(0)
    cmp = threshold(float(np.ravel(e.block.params["constant"])[0]), k, e.block.params["operator"])
    if isinstance(cmp, bool):  # the same result for every input value
        return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {'1u' if cmp else '0u'};"]))
    op, c = cmp
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = ({t.u(0, ix)} {op} (int64_T){c}) ? 1u : 0u;"]))


@int_template("logic.logical")
def _logical(e: Emitter) -> BlockCode:
    t = Int(e)
    op = e.block.params["operator"]
    n = e.block.num_inputs()

    def body(ix: str) -> list[str]:
        conds = [t.nonzero(p, ix) for p in range(n)]
        for p in range(n):
            t.k_in(p)
        if op == "NOT":
            expr = f"!{conds[0]}"
        elif op in ("AND", "NAND"):
            expr = " && ".join(conds)
        elif op in ("OR", "NOR"):
            expr = " || ".join(conds)
        else:  # XOR: an odd number true
            expr = "((" + " + ".join(f"({c} ? 1 : 0)" for c in conds) + ") % 2) == 1"
        if op in ("NAND", "NOR"):
            expr = f"!({expr})"
        return [f"{e.y(0, ix)} = ({expr}) ? 1u : 0u;"]

    return _bc(e.each(e.w(), body))


@int_template("routing.switch")
def _switch(e: Emitter) -> BlockCode:
    t = Int(e)
    crit = e.block.params["criterion"]
    k_c = t.k_in(1)
    k0, k2 = t.k_in(0), t.k_in(2)
    if crit == "u1 != 0":

        def cond(ix: str) -> str:
            return t.nonzero(1, ix)
    else:
        cmp = threshold(
            float(np.ravel(e.block.params["threshold"])[0]), k_c, ">" if ">" in crit and ">=" not in crit else ">="
        )
        if isinstance(cmp, bool):  # e.g. a threshold of -inf: always the same input
            port, k_sel = (0, k0) if cmp else (2, k2)
            return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {t.store(t.u(port, ix), k_sel)};"]))
        op, c = cmp

        def cond(ix: str) -> str:
            return f"({t.u(1, ix)} {op} (int64_T){c})"

    def body(ix: str) -> list[str]:
        return [f"if {cond(ix)} {{", f"    {e.y(0, ix)} = {t.store(t.u(0, ix), k0)};", "} else {",
                f"    {e.y(0, ix)} = {t.store(t.u(2, ix), k2)};", "}"]  # fmt: skip

    return _bc(e.each(e.w(), body))


@int_template("math.saturation")
def _saturation(e: Emitter) -> BlockCode:
    t = Int(e)
    k = t.k_in(0)
    if t.in_types[0] != t.out_types[0]:
        raise IntegerCodeError(f"{e.path}: in integer code a Saturation's input and output need the same type")
    rounding = e.block.quantization()[0]
    from nervus.core.datatypes import round_values

    dt = t.in_types[0]
    smin, smax = (0, 1) if dt.kind == "boolean" else (int(dt.stored_min), int(dt.stored_max))

    def limit(pname: str) -> int:
        # Clamped to the stored range first (rounding is monotonic, so this gives the same result), which also
        # makes infinite limits finite.
        v = float(np.ravel(e.block.params[pname])[0]) * 2.0**k
        return int(round_values(np.array([min(max(v, float(smin)), float(smax))]), rounding)[0])

    lo, hi = limit("lower"), limit("upper")
    t.helper("saturate")
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = ({t.out_types[0].c_type})nv_sat({t.u(0, ix)}, (int64_T){lo}, (int64_T){hi});"]))  # fmt: skip


@int_template("routing.datatype_conversion")
def _conversion(e: Emitter) -> BlockCode:
    t = Int(e)
    k = t.k_in(0)
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {t.store(t.u(0, ix), k)};"]))


def _typed_state(e: Emitter, name: str, init: list[float], dt: DataType, comment: str) -> str:
    stored = [float(v) for v in (dt.to_stored(dt.quantize(np.asarray(init, dtype=float))) if dt.kind != "boolean"
                                 else (np.asarray(init) != 0).astype(float))]  # fmt: skip
    return e.state(name, stored, comment, dtype=dt)


@int_template("discrete.unit_delay")
def _unit_delay(e: Emitter) -> BlockCode:
    t = Int(e)
    t.k_in(0)
    dt = t.out_types[0]
    if t.in_types[0] != dt:
        raise IntegerCodeError(f"{e.path}: in integer code a Unit Delay's input and output need the same type")
    s = _typed_state(e, "x", list(np.ravel(e.block.initial_discrete_state())), dt, "previous input (stored)")
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {s}[{ix}];"]),
               e.each(e.w(), lambda ix: [f"{s}[{ix}] = {e.u(0, ix)};"]))  # fmt: skip


@int_template("discrete.zero_order_hold")
def _zoh(e: Emitter) -> BlockCode:
    t = Int(e)
    k = t.k_in(0)
    return _bc(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {t.store(t.u(0, ix), k)};"]))


@int_template("discrete.integer_delay")
def _integer_delay(e: Emitter) -> BlockCode:
    from nervus.codegen.generator import c_index

    t = Int(e)
    t.k_in(0)
    dt = t.out_types[0]
    if t.in_types[0] != dt:
        raise IntegerCodeError(f"{e.path}: in integer code an Integer Delay's input and output need the same type")
    w, n = e.w(), int(e.block.params["delay"])
    s = _typed_state(e, "buf", list(np.ravel(e.block.initial_discrete_state())), dt, f"last {n} inputs (stored)")
    out = e.each(w, lambda ix: [f"{e.y(0, ix)} = {s}[{ix}];"])
    upd = [f"for (i = 0u; i < {(n - 1) * w}u; i++) {{ {s}[i] = {s}[i + {w}u]; }}"] if n > 1 else []
    upd += e.each(w, lambda ix: [f"{s}[{c_index((n - 1) * w, ix)}] = {e.u(0, ix)};"])
    return _bc(out, upd)
