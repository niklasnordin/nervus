"""Generate embedded C code from a discrete-time model.

The generated code is plain C99 with static memory only (no malloc, no recursion), in the style of
Simulink Embedded Coder:

* ``<model>.h`` declares the structs and the API.
* ``<model>.c`` contains ``<model>_initialize()`` and ``<model>_step()``.

Call ``<model>_initialize()`` once, then ``<model>_step()`` every base period (e.g. from a 10 ms task). Before
each step, write the inputs to ``<model>_U``; after it, read the outputs from ``<model>_Y``. Tunable
parameters are in ``<model>_P``. A parameter given as a bare workspace variable (``Kp``) becomes one shared
field named after the variable, which a calibration tool can change at run time.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from nervus import __version__
from nervus.blocks.ports import Inport, Outport, PortProxy
from nervus.codegen.a2l import generate_a2l
from nervus.core.block import Block
from nervus.core.datatypes import DOUBLE, DataType, parse_datatype
from nervus.core.diagram import Diagram
from nervus.core.errors import ModelError
from nervus.sim.compiler import CompiledModel, Kind, compile_diagram


class CodegenError(ModelError):
    """The model (or a block in it) cannot be turned into C code."""


_C_RESERVED = frozenset({
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum", "extern",
    "float", "for", "goto", "if", "inline", "int", "long", "register", "restrict", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "_Bool",
    "_Complex", "_Imaginary", "bool", "true", "false", "NULL", "INFINITY", "NAN", "HUGE_VAL", "HUGE_VALF",
    "HUGE_VALL", "FP_NAN", "FP_INFINITE", "FP_ZERO", "FP_SUBNORMAL", "FP_NORMAL", "FP_FAST_FMA", "FP_ILOGB0",
    "FP_ILOGBNAN", "MATH_ERRNO", "MATH_ERREXCEPT", "math_errhandling", "real_T", "real32_T", "boolean_T", "int8_T",
    "uint8_T", "int16_T", "uint16_T", "int32_T", "uint32_T", "int64_T", "uint64_T",
})  # fmt: skip
"""C keywords, and names that the generated code's headers (``stdint.h``, ``math.h``, the type aliases) define."""
_STDINT_MACRO = re.compile(r"U?INT(?:_LEAST|_FAST)?(?:8|16|32|64|MAX|PTR)_(?:MIN|MAX|C)|SIZE_MAX|PTRDIFF_(?:MIN|MAX)")


def c_identifier(name: str) -> str:
    """Turn a block path like ``ECU/throttle pi`` into a valid C identifier ``ECU_throttle_pi``. A name that C
    keeps for itself (``switch``, ``double``, ``NAN``, ``_Reserved``) gets a trailing or leading mark."""
    s = re.sub(r"[^0-9A-Za-z_]", "_", name)
    if not s or s[0].isdigit() or (s[0] == "_" and (len(s) == 1 or s[1] == "_" or s[1].isupper())):
        s = "b_" + s
    if s in _C_RESERVED or _STDINT_MACRO.fullmatch(s):
        s += "_"
    return s


def c_float(v: float) -> str:
    if math.isinf(v):
        return "INFINITY" if v > 0 else "(-INFINITY)"
    if math.isnan(v):
        return "NAN"
    r = repr(float(v))
    return r if ("." in r or "e" in r) else r + ".0"


@dataclass
class _Field:
    name: str
    size: int
    init: list[float]
    comment: str = ""
    dtype: DataType = DOUBLE
    """Storage type of a parameter (scaled to physical values where it is read)."""
    const: bool = False
    """A constant in ROM (not calibratable)."""

    @property
    def nbytes(self) -> int:
        return _C_SIZES.get(self.dtype.c_type, 8)

    def stored(self) -> list[str]:
        """The C initialiser values: stored integers for fixed point, physical values otherwise."""
        d = self.dtype
        if d.kind == "fixed":
            return [str(round((v - d.bias) / d.slope)) for v in self.init]
        if d.kind == "boolean":
            return ["1" if v else "0" for v in self.init]
        return [c_float(v) for v in self.init]


_C_SIZES = {"real_T": 8, "real32_T": 4, "boolean_T": 1, "int8_T": 1, "uint8_T": 1, "int16_T": 2, "uint16_T": 2,
            "int32_T": 4, "uint32_T": 4}  # fmt: skip


@dataclass
class BlockCode:
    outputs: list[str] = field(default_factory=list)
    update: list[str] = field(default_factory=list)


class Emitter:
    """Helper handed to each block's code template."""

    def __init__(self, gen: CGenerator, index: int) -> None:
        self.gen = gen
        self.i = index
        self.block = gen.model.blocks[index]
        self.path = gen.model.names[index]
        self.cname = gen.cnames[index]
        self.in_widths = gen.model.in_widths[index]
        self.out_widths = gen.model.out_widths[index]

    # signals --------------------------------------------------------------

    def u(self, port: int, ix: str = "i") -> str:
        s, p = self.gen.model.inputs[self.i][port]
        var = self.gen.signal_var(s, p)
        return f"{var}[0]" if self.gen.model.out_widths[s][p] == 1 else f"{var}[{ix}]"

    def u_var(self, port: int) -> str:
        s, p = self.gen.model.inputs[self.i][port]
        return self.gen.signal_var(s, p)

    def y(self, port: int = 0, ix: str = "i") -> str:
        return f"{self.gen.signal_var(self.i, port)}[{ix}]"

    def w(self, port: int = 0) -> int:
        return self.out_widths[port]

    # parameters and states --------------------------------------------------

    def param(self, pname: str, value: Any = None, typed: bool = False) -> str:
        """A parameter field; returns the C expression of the array. Read its elements with :meth:`at`.

        Parameters that are bare workspace variables become shared fields named after the variable, with the
        variable's data type and calibratable flag. ``typed=False``: the template reads the array directly, so
        the variable must be ``double``.
        """
        value = self.block.params[pname] if value is None else value
        arr = np.atleast_1d(np.asarray(value, dtype=float)).ravel()
        expr = self.block.expressions.get(pname, "").strip()
        dtype, const = DOUBLE, False
        if expr.isidentifier() and expr in self.gen.namespace and not self._masked(expr):
            fname = c_identifier(expr)  # shared calibration parameter named after the variable
            comment = f"workspace variable {expr}"
            var = self.gen.model.diagram.root.workspace.variable(expr)
            dtype, const = parse_datatype(var.data_type) or DOUBLE, not var.calibratable
            if not dtype.is_double and not typed:
                raise CodegenError(
                    f"{self.path}: parameter {pname!r} uses {expr} stored as {var.data_type}; this block needs a "
                    "double parameter here (use data type double for this variable)",
                    (self.path,),
                )
        else:
            fname = self.gen.block_field(f"{self.cname}_{pname}", (self.i, pname))
            comment = f"{self.path}: {pname}" + (f" = {expr}" if expr else "")
        if fname in self.gen.global_names():
            fname += "_prm"  # e.g. parameter P of a block named like the model: not the same name as <model>_P
        self.gen.add_param(fname, arr, comment, dtype, const)
        return f"{self.gen.ref('C' if const else 'P')}.{fname}"

    def named_param(self, name: str, value: float) -> str:
        """A workspace (or mask) variable used by name inside a block (e.g. a chart ``param``): a shared
        calibration field named after the variable, or a per-instance field for a mask parameter. Returns the
        C expression of its (physical) value."""
        if self._masked(name) or name not in self.gen.namespace:
            fname = self.gen.block_field(f"{self.cname}_{name}", (self.i, name))
            comment, dtype, const = f"{self.path}: {name}", DOUBLE, False
        else:
            var = self.gen.model.diagram.root.workspace.variable(name)
            fname, comment = c_identifier(name), f"workspace variable {name}"
            dtype, const = parse_datatype(var.data_type) or DOUBLE, not var.calibratable
        self.gen.add_param(fname, np.array([float(value)]), comment, dtype, const)
        return self.at(f"{self.gen.ref('C' if const else 'P')}.{fname}", "0")

    def at(self, field_expr: str, ix: str) -> str:
        """Element ``ix`` of a parameter array as a physical real_T value (scaled for fixed-point storage)."""
        f = self.gen.params[field_expr.split(".", 1)[1]]
        raw = f"{field_expr}[{ix}]"
        d = f.dtype
        if d.is_double:
            return raw
        if d.kind != "fixed" or d.is_integer:
            return f"((real_T){raw})"
        return f"((real_T){raw} * {c_float(d.slope)} + {c_float(d.bias)})"

    def _masked(self, name: str) -> bool:
        """Whether ``name`` is a mask parameter of a subsystem around this block (then it is not shared)."""
        d = self.gen.model.origin[self.i].diagram
        while d is not None and d.parent is not None:
            mask = getattr(d.parent, "mask", None)
            if mask is not None and name in mask:
                return True
            d = d.parent.diagram
        return False

    def p(self, pname: str, ix: str = "i", value: Any = None) -> str:
        """Element ``ix`` of a parameter (or the scalar), with scalar expansion."""
        field_expr = self.param(pname, value, typed=True)
        size = self.gen.params[field_expr.split(".", 1)[1]].size
        return self.at(field_expr, "0" if size == 1 else ix)

    def state(self, sname: str, init: Iterable[float], comment: str = "", dtype: DataType = DOUBLE) -> str:
        values = [float(v) for v in np.atleast_1d(np.asarray(list(init), dtype=float))]
        fname = self.gen.block_field(f"{self.cname}_{sname}", (self.i, sname))
        self.gen.states[fname] = _Field(fname, max(1, len(values)), values or [0.0], comment or self.path, dtype)
        return f"{self.gen.ref('DW')}.{fname}"

    @property
    def period(self) -> float:
        return self.block.period()

    # loops --------------------------------------------------------------------

    def each(self, width: int, body: Callable[[str], list[str]]) -> list[str]:
        if width == 1:
            return body("0")
        lines = body("i")
        return [f"for (i = 0u; i < {width}u; i++) {{", *[f"    {line}" for line in lines], "}"]


Template = Callable[[Emitter], BlockCode]
TEMPLATES: dict[str, Template] = {}


def template(*type_ids: str) -> Callable[[Template], Template]:
    """Register a C code template for one or more block types (also usable for custom blocks)."""

    def deco(fn: Template) -> Template:
        for t in type_ids:
            TEMPLATES[t] = fn
        return fn

    return deco


def _drop_unused_types(header: str, others: list[str]) -> str:
    """Remove the ``typedef`` lines of the type aliases that no generated file uses (MISRA C:2012 rule 2.3)."""
    lines = header.split("\n")
    text = "\n".join([*(x for x in lines if not x.startswith("typedef ")), *others])
    keep = []
    for line in lines:
        m = re.match(r"typedef \w+ (\w+);$", line)
        if m is None or re.search(rf"\b{m.group(1)}\b", text):
            keep.append(line)
    return "\n".join(keep)


def _guard_types(header: str) -> str:
    """Guard each basic type alias, so several generated headers (a model's and its reusable functions') can define
    the same one in one program."""
    out = []
    for line in header.split("\n"):
        m = re.match(r"typedef \w+ (\w+);$", line)
        if m is None:
            out.append(line)
        else:
            guard = f"NERVUS_TYPE_{m.group(1).upper()}"
            out += [f"#ifndef {guard}", f"#define {guard}", line, "#endif"]
    return "\n".join(out)


def c_index(*terms: int | str) -> str:
    """A C array index as the sum of ``terms``, with the constant terms folded into one unsigned literal: no
    ``+ 0`` (MISRA C:2012 rule 2.2) and no signed constants next to unsigned loop counters (rule 10.4)."""
    const, rest = 0, []
    for term in terms:
        if isinstance(term, int) or str(term).isdigit():
            const += int(term)
        else:
            rest.append(str(term))
    if not rest:
        return str(const)
    return " + ".join(rest + ([f"{const}u"] if const else []))


@dataclass
class GeneratedCode:
    name: str
    files: dict[str, str]
    base_period: float
    rates: list[float]
    inputs: list[tuple[str, int]]
    """(C field name, width) of each input, in port order."""
    outputs: list[tuple[str, int]]
    params: dict[str, list[float]]
    model: CompiledModel
    tasks: list[str] = field(default_factory=list)
    """Names of the task functions (one per rate) when generated with ``tasks=True``."""
    tunables: list[tuple[str, int, list[float], str, str]] = field(default_factory=list)
    """Calibratable parameters in the ``<name>_P`` struct: (field, size, initial values, C type, comment)."""
    integer_only: bool = False
    """Generated with integer arithmetic only (see :mod:`nervus.codegen.integer`)."""
    extra_sources: list[str] = field(default_factory=list)
    """Further C files of the ECU code: the reusable functions of subsystems (``<function>.c`` and ``.h``)."""
    io_types: dict[str, DataType] = field(default_factory=dict)
    """Data type of each input and output field (missing: ``double``)."""

    @property
    def c_files(self) -> dict[str, str]:
        """The files that belong in the ECU project (no SIL accessors, examples or report)."""
        keep = {f"{self.name}.h", f"{self.name}.c", *self.extra_sources}
        return {k: v for k, v in self.files.items() if k in keep}

    def write(self, folder: str | Path) -> list[Path]:
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for fname, text in self.files.items():
            p = out / fname
            p.write_text(text, encoding="utf-8")
            paths.append(p)
        return paths


class CGenerator:
    def __init__(self, model: CompiledModel, name: str, tasks: bool = False, reusable: bool = False,
                 integer_only: bool = False) -> None:  # fmt: skip
        self.model = model
        self.integer_only = integer_only
        """Integer arithmetic only (see :mod:`nervus.codegen.integer`): signals and states are stored integers."""
        self.prefix = c_identifier(name)
        self.tasks = tasks
        """One C function per rate (for an RTOS) instead of one step function with a built-in scheduler."""
        self.reusable = reusable
        """Generate a reusable function: its data comes in through pointers (one set per instance), see :meth:`ref`."""
        self.extra_includes: list[str] = []
        self.extra_globals: list[str] = []
        self.extra_header: list[str] = []
        self.extra_characteristics: list[tuple[str, list[_Field]]] = []
        """Further calibratable parameter structs for the A2L file: (C name, fields), one per function instance."""
        self.extra_init: list[str] = []
        self.extra_files: dict[str, str] = {}
        """Code that templates add to the model's files (reusable subsystem functions and their instances)."""
        self.namespace = model.diagram.root.workspace.namespace()  # shared calibration parameters
        self.cnames = self._unique_cnames(model.names, [len(ws) for ws in model.out_widths])
        self._field_owner: dict[str, tuple[int, str]] = {}
        """Owner (block index, name) of every per-block parameter and state field."""
        self.params: dict[str, _Field] = {}
        self.states: dict[str, _Field] = {}
        self.quantizers: dict[str, str] = {}  # C function name -> definition

    @staticmethod
    def _unique_cnames(names: list[str], n_outputs: list[int]) -> list[str]:
        """C names of the blocks, such that their signal fields (``<name>``, or ``<name>_o<port>`` for several
        outputs) are all different, also from the names other blocks have before they are made unique."""
        bases = [c_identifier(n) for n in names]
        taken = set(bases)  # a renamed block must not take another block's own name

        def fields(c: str, n: int) -> list[str]:
            return [c] if n <= 1 else [f"{c}_o{p}" for p in range(n)]

        used: set[str] = set()
        out = []
        for base, n in zip(bases, n_outputs, strict=True):
            c, k = base, 0
            while used & set(fields(c, n)) or (c != base and c in taken):
                k += 1
                c = f"{base}_{k}"
            used |= set(fields(c, n))
            out.append(c)
        return out

    def block_field(self, name: str, owner: tuple[int, str]) -> str:
        """A parameter or state field of one block (``owner`` = block index, parameter or state name): ``name``,
        unless another block's field or a workspace variable (a shared calibration field) has it already."""
        variables = {c_identifier(v) for v in self.namespace}
        fname, k = name, 0
        while (self._field_owner.get(fname, owner) != owner) or fname in variables:
            k += 1
            fname = f"{name}_{k}"
        self._field_owner[fname] = owner
        return fname

    def _gate(self, i: int, code: BlockCode, block_states: list[str]) -> None:
        """Wrap a block of an enabled and/or triggered subsystem: run only while the enable is > 0 and the
        trigger has an edge, optionally resetting its states when enabled again. The decision is stored, so the
        state update uses the same one (same logic as ``CompiledModel._gate``)."""
        m = self.model
        c = self.cnames[i]
        flag = f"{c}_enabled"
        self.states[flag] = _Field(flag, 1, [1.0], f"{m.names[i]}: ran at its last execution")
        dw = self.ref("DW")
        cond = " && ".join(f"({self.signal_var(s, p)}[0] > 0.0)" for s, p in m.enable[i]) or "1"
        head = ["{", f"    real_T en = ({cond}) ? 1.0 : 0.0;"]
        if m.reset_on_enable[i] and block_states:
            prev = f"{c}_en_prev"
            self.states[prev] = _Field(prev, 1, [1.0], f"{m.names[i]}: enable at its last execution")
            head.append(f"    if ((en > 0.0) && ({dw}.{prev}[0] == 0.0)) {{ /* enabled again: reset states */")
            for name in block_states:
                for k, v in enumerate(self.states[name].init):
                    head.append(f"        {dw}.{name}[{k}] = {c_float(v)};")
            head.append("    }")
            head.append(f"    {dw}.{prev}[0] = en;")
        for k, (s, p, edge) in enumerate(m.trigger[i]):
            tr = f"{c}_trig{k}"
            self.states[tr] = _Field(tr, 2, [0.0, 0.0], f"{m.names[i]}: [seen, last value] of its trigger")
            rising = f"(({dw}.{tr}[1] <= 0.0) && (tv > 0.0))"
            falling = f"(({dw}.{tr}[1] > 0.0) && (tv <= 0.0))"
            if edge == "function_call":
                fired = f"({dw}.{tr}[1] != tv)"  # every change of the call counter is one call
            else:
                fired = rising if edge == "rising" else falling if edge == "falling" else f"({rising} || {falling})"
            head += [
                "    {",
                f"        real_T tv = {self.signal_var(s, p)}[0];",
                f"        if (!(({dw}.{tr}[0] > 0.0) && {fired})) {{ en = 0.0; }} /* {edge} edge */",
                f"        {dw}.{tr}[0] = 1.0;",
                f"        {dw}.{tr}[1] = tv;",
                "    }",
            ]
        head.append(f"    {dw}.{flag}[0] = en;")
        head.append("}")
        if code.outputs:
            code.outputs[:] = [*head, f"if ({dw}.{flag}[0] > 0.0) {{", *[f"    {x}" for x in code.outputs], "}"]
        else:
            code.outputs[:] = head
        if code.update:
            code.update[:] = [f"if ({dw}.{flag}[0] > 0.0) {{", *[f"    {x}" for x in code.update], "}"]

    def quantizer(self, dtype: DataType, rounding: str = "nearest", overflow: str = "saturate") -> str:
        """Name of a generated C function that converts a real_T to ``dtype`` and back, exactly like
        :meth:`DataType.quantize` does in the simulation."""
        name = f"nv_q_{c_identifier(dtype.name).strip('_')}_{rounding}_{overflow}".replace("__", "_")
        if name in self.quantizers:
            return name
        if dtype.kind == "single":
            body = ["    return (real_T)(real32_T)x;"]
        elif dtype.kind == "boolean":
            body = ["    return (x != 0.0) ? 1.0 : 0.0;"]
        else:
            rnd = {"nearest": "round", "floor": "floor", "ceil": "ceil", "zero": "trunc"}[rounding]
            lo, hi = c_float(float(dtype.stored_min)), c_float(float(dtype.stored_max))
            body = [f"    real_T q = {rnd}((x - {c_float(dtype.bias)}) / {c_float(dtype.slope)});"]
            if overflow == "wrap":
                span = c_float(float(dtype.stored_max - dtype.stored_min + 1))
                body += [f"    q = fmod(q - {lo}, {span});", f"    if (q < 0.0) {{ q += {span}; }}", f"    q += {lo};"]
            else:
                body += [
                    f"    if (q > {hi}) {{ q = {hi}; }} else if (q < {lo}) {{ q = {lo}; }} else {{ /* in range */ }}"
                ]
            body += [f"    return (q * {c_float(dtype.slope)}) + {c_float(dtype.bias)};"]
        self.quantizers[name] = "\n".join(
            [
                f"/* {dtype.name}, rounding {rounding}, overflow {overflow} */",
                f"static real_T {name}(real_T x)",
                "{",
                *body,
                "}",
            ]
        )
        return name

    def signal_var(self, block: int, port: int) -> str:
        return f"{self.ref('B')}.{self.cnames[block]}" + (f"_o{port}" if len(self.model.out_widths[block]) > 1 else "")

    def add_param(
        self, fname: str, values: np.ndarray, comment: str, dtype: DataType = DOUBLE, const: bool = False
    ) -> None:
        existing = self.params.get(fname)
        vals = [float(v) for v in values]
        if existing is not None and existing.init != vals:
            raise CodegenError(f"parameter {fname} has conflicting values ({existing.init} vs {vals})")
        self.params[fname] = _Field(fname, len(vals), vals, comment, dtype, const)

    def ref(self, kind: str) -> str:
        """The C expression of a data struct: U, Y, P, C, B or DW. ``<model>_DW`` normally; ``(*DW)`` in a reusable
        function, which receives its instance's data through pointers (the constants C stay shared)."""
        return f"(*{kind})" if self.reusable and kind != "C" else f"{self.prefix}_{kind}"

    def global_names(self) -> set[str]:
        """The names with external linkage in the generated code (MISRA C:2012 rule 5.8: no other identifier may
        reuse them, not even a struct field)."""
        p = self.prefix
        return {f"{p}_{x}" for x in ("U", "Y", "P", "C", "B", "DW", "step", "initialize")}

    def param_fields(self, const: bool = False) -> list[_Field]:
        """Calibratable (or constant) parameters, largest types first so the struct has no padding."""
        return sorted((f for f in self.params.values() if f.const == const), key=lambda f: -f.nbytes)

    # ------------------------------------------------------------------ checks

    def _check(self) -> None:
        m = self.model
        problems = []
        for i, b in enumerate(m.blocks):
            if m.xc_slices[i].stop > m.xc_slices[i].start:
                problems.append(f"{m.names[i]}: continuous states (only discrete-time blocks can become C code)")
            elif self.integer_only and b.type_id not in _int_templates() and not isinstance(b, PortProxy):
                problems.append(f"{m.names[i]}: {b.label or b.type_id} is not supported in integer-only code")
            elif b.type_id not in TEMPLATES and not isinstance(b, PortProxy):
                problems.append(f"{m.names[i]}: no C code template for block type {b.type_id or type(b).__name__!r}")
        for members, _tears in m.loops:
            problems.append(f"{m.names[members[0]]}: in an algebraic loop ({' -> '.join(m.names[k] for k in members)}); "
                            "break it with a Unit Delay to generate code")  # fmt: skip
        if problems:
            advice = (
                "Integer-only code supports In/Out, Constant, Sum, Gain, Product (*), Bias, Abs, MinMax, comparisons, "
                "logic, Switch, Saturation, Unit Delay, Integer Delay, Zero-Order Hold and Data Type Conversion; "
                "generate floating-point code for the others."
                if self.integer_only
                else "Generate code for the controller subsystem only, with In/Out blocks at its boundary."
            )
            raise CodegenError(
                "cannot generate C code:\n  - " + "\n  - ".join(problems) + "\n" + advice,
                tuple(p.split(":", 1)[0] for p in problems),
            )
        if not m.rates:
            raise CodegenError("the model has no discrete sample time; set the sample time of at least one block")

    # ------------------------------------------------------------------ generation

    def generate(self) -> GeneratedCode:
        self._check()
        m = self.model
        periods = [Fraction(v).limit_denominator(10**9) for r in m.rates for v in (r.period, r.offset) if v]
        base = periods[0]
        for v in periods[1:]:
            base = Fraction(
                math.gcd(base.numerator * v.denominator, v.numerator * base.denominator),
                base.denominator * v.denominator,
            )
        base_period = float(base)
        steps = [(round(r.period / base_period), round(r.offset / base_period)) for r in m.rates]

        codes: list[BlockCode] = []
        for i, b in enumerate(m.blocks):
            if isinstance(b, PortProxy):
                codes.append(_pass_through(Emitter(self, i)))
            else:
                before = set(self.states)
                fn = _int_templates()[b.type_id] if self.integer_only else TEMPLATES[b.type_id]
                try:
                    codes.append(fn(Emitter(self, i)))
                except _integer_error() as exc:
                    raise CodegenError(str(exc), (m.names[i],)) from exc
                block_states = [k for k in self.states if k not in before]
            code = codes[-1]
            if (m.enable[i] or m.trigger[i]) and m.kinds[i] is not Kind.CONSTANT:
                self._gate(i, code, [] if isinstance(b, PortProxy) else block_states)
            if code.outputs and not self.integer_only:  # integer code: the templates convert exactly
                rounding, overflow = b.quantization()
                for p, dtype in enumerate(m.out_types[i]):
                    if dtype.is_double:
                        continue
                    fn = self.quantizer(dtype, rounding, overflow)
                    var, w = self.signal_var(i, p), m.out_widths[i][p]
                    code.outputs.append(
                        f"for (i = 0u; i < {w}u; i++) {{ {var}[i] = {fn}({var}[i]); }} /* {dtype.name} */"
                    )

        inputs = [(i, b) for i, b in enumerate(m.blocks) if isinstance(b, Inport)]
        outputs = [(i, b) for i, b in enumerate(m.blocks) if isinstance(b, Outport)]
        inputs.sort(key=lambda x: x[1].params["port"])
        outputs.sort(key=lambda x: x[1].params["port"])
        in_fields = [(c_identifier(m.names[i]), m.out_widths[i][0]) for i, _ in inputs]
        out_fields = [(c_identifier(m.names[i]), m.in_widths[i][0]) for i, _ in outputs]
        for kind, ports, fields in (("In", inputs, in_fields), ("Out", outputs, out_fields)):
            seen: dict[str, str] = {}
            for (i, _b), (fname, _w) in zip(ports, fields, strict=True):
                if fname in seen:
                    raise CodegenError(
                        f"the {kind} blocks {seen[fname]} and {m.names[i]} both become the C field {fname}; "
                        "rename one of them",
                        (seen[fname], m.names[i]),
                    )
                seen[fname] = m.names[i]
        self.io_types = {c_identifier(m.names[i]): m.out_types[i][0] for i, _ in inputs}
        for i, _ in outputs:
            s, p = m.inputs[i][0]
            self.io_types[c_identifier(m.names[i])] = m.out_types[s][p]

        self.task_plan = self._task_plan(steps) if self.tasks else None
        header = self._header(in_fields, out_fields, base_period, steps)
        source = self._source(codes, steps, base_period)
        if self.reusable:
            return self._function_code(header, source, steps, base_period, in_fields, out_fields)
        header = _drop_unused_types(header, [source, self._sil(in_fields, out_fields),
                                             self._main_example(in_fields, out_fields, base_period)])  # fmt: skip
        if self.extra_includes:  # reusable subsystem functions: their types come from their headers
            header = _guard_types(header).replace(
                "#include <stdint.h>\n",
                "#include <stdint.h>\n" + "".join(f'#include "{h}"\n' for h in self.extra_includes),
                1,
            )
        files = {
            f"{self.prefix}.h": header,
            f"{self.prefix}.c": source,
            f"{self.prefix}_sil.c": self._sil(in_fields, out_fields),
            "main_example.c": self._main_example(in_fields, out_fields, base_period),
            f"{self.prefix}_report.html": self._report(source, in_fields, out_fields, base_period, steps),
            f"{self.prefix}.a2l": generate_a2l(self),
            **self.extra_files,
        }
        return GeneratedCode(
            name=self.prefix,
            files=files,
            base_period=base_period,
            rates=[r.period for r in m.rates],
            inputs=in_fields,
            outputs=out_fields,
            params={k: v.init for k, v in self.params.items()},
            model=m,
            tasks=[self.task_name(k) for k in self.task_plan["order"]] if self.task_plan is not None else [],
            tunables=[(f.name, f.size, list(f.init), f.dtype.c_type, f.comment) for f in self.param_fields()],
            extra_sources=sorted(f for f in self.extra_files if f.endswith((".c", ".h"))),
            integer_only=self.integer_only,
            io_types=dict(self.io_types),
        )

    def param_initializer(self) -> str:
        """The initial values of the parameter struct (``{...}`` for each field, in struct order)."""
        return ",\n".join(f"    {{{', '.join(f.stored())}}} /* {f.name} */" for f in self.param_fields()) or "    {0.0}"

    def _function_code(self, header: str, source: str, steps: list[tuple[int, int]], base_period: float,
                       ins: list[tuple[str, int]], outs: list[tuple[str, int]]) -> GeneratedCode:  # fmt: skip
        """A reusable function: the model's code with its data passed in through pointers, split into an output
        and an update function (the model calls them at its output and update phases)."""
        p = self.prefix
        if steps != [(1, 0)]:
            raise CodegenError(f"{p}: a reusable function needs all its blocks at one rate, without an offset")
        if self.param_fields(const=True):
            raise CodegenError(f"{p}: a reusable function cannot have constant (ROM) parameters yet")
        args_out = f"const {p}_U_t *U, {p}_Y_t *Y, {p}_B_t *B, {p}_DW_t *DW, const {p}_P_t *P"
        args_upd = f"{p}_B_t *B, {p}_DW_t *DW, const {p}_P_t *P"
        args_init = f"{p}_B_t *B, {p}_DW_t *DW"
        dropped = {f"extern {p}_{k}_t {p}_{k};" for k in ("U", "Y", "P", "B", "DW")}
        dropped |= {f"void {p}_initialize(void);", f"void {p}_step(void);"}
        hlines = [x for x in header.split("\n") if x not in dropped and not x.startswith(f"#define {p.upper()}_BASE")]
        end = max(k for k, x in enumerate(hlines) if x.startswith("#endif"))
        hlines[end:end] = [
            f"/* One instance: call {p}_initialize once, then {p}_output and {p}_update every {base_period:g} s. */",
            f"void {p}_initialize({args_init});",
            f"void {p}_output({args_out});",
            f"void {p}_update({args_upd});",
            "",
        ]
        lines = source.split("\n")
        start = lines.index(f"{p}_U_t {p}_U;")
        stop = lines.index(f"{p}_DW_t {p}_DW;")
        del lines[start : stop + 1]  # the data lives in the instances
        k_init = lines.index(f"void {p}_initialize(void)")
        lines[k_init] = f"void {p}_initialize({args_init})"
        lines.insert(k_init + 4, "    (void)B;")  # after the declarations
        k_step = lines.index(f"void {p}_step(void)")
        k_upd = lines.index("    /* state updates */", k_step)
        lines[k_step] = f"void {p}_output({args_out})"
        lines[k_step + 4 : k_step + 4] = ["    (void)U;", "    (void)Y;", "    (void)DW;", "    (void)P;"]
        k_upd += 4
        update = [f"void {p}_update({args_upd})", "{", "    uint32_T i;", "    (void)i;", "    (void)B;",
                  "    (void)DW;", "    (void)P;", *lines[k_upd:]]  # fmt: skip
        lines[k_upd:] = ["}", "", *update]
        src = "\n".join(lines)
        fheader = _guard_types(_drop_unused_types("\n".join(hlines), [src]))
        return GeneratedCode(
            name=p,
            files={f"{p}.h": fheader, f"{p}.c": src},
            base_period=base_period,
            rates=[r.period for r in self.model.rates],
            inputs=ins,
            outputs=outs,
            params={k: v.init for k, v in self.params.items()},
            model=self.model,
            tunables=[(f.name, f.size, list(f.init), f.dtype.c_type, f.comment) for f in self.param_fields()],
        )

    def _banner(self, what: str) -> list[str]:
        return [
            "/*",
            f" * {what}",
            f" * Generated by Nervus {__version__} from model '{self.model.diagram.name}'. Do not edit by hand.",
            " */",
        ]

    def _signal_types(self) -> dict[str, DataType]:
        """The stored type of every block signal (integer code)."""
        m = self.model
        return {self.signal_var(i, p).split(".", 1)[1]: t for i, ts in enumerate(m.out_types) for p, t in enumerate(ts)}

    def _struct(
        self, name: str, fields: list[tuple[str, int, str]], types: dict[str, DataType] | None = None
    ) -> list[str]:
        lines = ["typedef struct {"]
        if not fields:
            lines.append("    real_T dummy[1]; /* (empty) */")
        for fname, size, comment in fields:
            dtype = getattr(self, "io_types", {}).get(fname) if name.endswith(("_U_t", "_Y_t")) else None
            if types is not None:
                dtype = types.get(fname)
            ctype = dtype.c_type if dtype is not None else "real_T"
            note = comment or (dtype.name if dtype is not None and not dtype.is_double else "")
            if dtype is not None and dtype.is_fixed and not dtype.is_integer:
                note = f"{dtype.name}: value = {dtype.slope:g} * stored + {dtype.bias:g}"
            lines.append(f"    {ctype} {fname}[{size}];" + (f" /* {note} */" if note else ""))
        lines.append(f"}} {name};")
        return lines

    def _header(
        self, ins: list[tuple[str, int]], outs: list[tuple[str, int]], base: float, steps: list[tuple[int, int]]
    ) -> str:
        pfx = self.prefix
        guard = f"{pfx.upper()}_H"
        m = self.model
        signals = []
        for i, ws in enumerate(m.out_widths):
            for p, w in enumerate(ws):
                signals.append((self.signal_var(i, p).split(".", 1)[1], w, m.names[i]))
        lines = [
            *self._banner(f"{pfx}.h"),
            f"#ifndef {guard}",
            f"#define {guard}",
            "",
            "#include <stdint.h>",
            "",
            "typedef double real_T;",
            "typedef float real32_T;",
            "typedef uint8_t boolean_T;",
            "typedef int8_t int8_T;",
            "typedef uint8_t uint8_T;",
            "typedef int16_t int16_T;",
            "typedef uint16_t uint16_T;",
            "typedef int32_t int32_T;",
            "typedef uint32_t uint32_T;",
            "typedef int64_t int64_T;",
            "typedef uint64_t uint64_T;",
            "",
            f"#define {pfx.upper()}_BASE_PERIOD ({c_float(base)}) /* seconds between calls to {pfx}_step() */",
            "",
            "/* Inputs: write before each step */",
            *self._struct(f"{pfx}_U_t", [(n, w, "") for n, w in ins]),
            "",
            "/* Outputs: read after each step */",
            *self._struct(f"{pfx}_Y_t", [(n, w, "") for n, w in outs]),
            "",
            "/* Tunable parameters (calibration) */",
            *self._struct(
                f"{pfx}_P_t",
                [(f.name, f.size, f.comment) for f in self.param_fields()],
                {f.name: f.dtype for f in self.params.values()},
            ),
            *(
                [
                    "",
                    "/* Constant parameters (in ROM, not calibratable) */",
                    *self._struct(
                        f"{pfx}_C_t",
                        [(f.name, f.size, f.comment) for f in self.param_fields(const=True)],
                        {f.name: f.dtype for f in self.params.values()},
                    ),
                ]
                if self.param_fields(const=True)
                else []
            ),
            "",
            "/* Block signals */",
            *self._struct(f"{pfx}_B_t", signals, self._signal_types() if self.integer_only else None),
            "",
            "/* States */",
            *self._struct(
                f"{pfx}_DW_t",
                [(f.name, f.size, f.comment) for f in self.states.values()],
                {f.name: f.dtype for f in self.states.values()} if self.integer_only else None,
            ),
            "",
            f"extern {pfx}_U_t {pfx}_U;",
            f"extern {pfx}_Y_t {pfx}_Y;",
            f"extern {pfx}_P_t {pfx}_P;",
            *([f"extern const {pfx}_C_t {pfx}_C;"] if self.param_fields(const=True) else []),
            f"extern {pfx}_B_t {pfx}_B;",
            f"extern {pfx}_DW_t {pfx}_DW;",
            "",
            *self.extra_header,
            f"void {pfx}_initialize(void);",
            f"void {pfx}_step(void);",
            *self._task_declarations(),
            "",
            f"#endif /* {guard} */",
            "",
        ]
        return "\n".join(lines)

    def _source(self, codes: list[BlockCode], steps: list[tuple[int, int]], base: float) -> str:
        pfx = self.prefix
        m = self.model

        def init_values(f: _Field) -> str:
            return "{" + ", ".join(f.stored()) + "}"

        param_init = ",\n".join(f"    {init_values(f)} /* {f.name} */" for f in self.param_fields()) or "    {0.0}"
        consts = self.param_fields(const=True)
        const_init = ",\n".join(f"    {init_values(f)} /* {f.name} */" for f in consts)
        multirate = len(steps) > 1 or any(n > 1 or off for n, off in steps)
        offset_rates = {k for k, (n, off) in enumerate(steps) if (n - off) % n != 0} if multirate else set()

        body_out: list[str] = []
        body_upd: list[str] = []
        init_out: list[str] = []
        for i, code in enumerate(codes):
            kind, rate = m.kinds[i], m.rate_index[i]
            label = f"/* {m.names[i]} ({m.blocks[i].label or 'port'}) */"
            if kind is Kind.CONSTANT:
                if code.outputs:
                    init_out += [label, *code.outputs]
                continue
            guard = f"rt_hit[{rate}] != 0u" if (kind is Kind.DISCRETE and multirate) else None
            if code.outputs:
                if guard:
                    # A rate with an offset first runs later, but its outputs are computed at the start, as in the
                    # simulation (the state updates wait for the first hit).
                    first = rate in offset_rates
                    out_guard = f"({guard}) || (rt_first != 0u)" if first else guard
                    body_out += [label, f"if ({out_guard}) {{", *[f"    {x}" for x in code.outputs], "}"]
                else:
                    body_out += [label, *code.outputs]
            if code.update:
                if guard:
                    body_upd += [label, f"if ({guard}) {{", *[f"    {x}" for x in code.update], "}"]
                else:
                    body_upd += [label, *code.update]

        lines = [
            *self._banner(f"{pfx}.c"),
            f'#include "{pfx}.h"',
            "#include <math.h>",
            "",
            f"{pfx}_U_t {pfx}_U;",
            f"{pfx}_Y_t {pfx}_Y;",
            f"{pfx}_P_t {pfx}_P = {{",
            param_init,
            "};",
            *([f"const {pfx}_C_t {pfx}_C = {{", const_init, "};"] if consts else []),
            f"{pfx}_B_t {pfx}_B;",
            f"{pfx}_DW_t {pfx}_DW;",
            "",
            *self.extra_globals,
            *[f"{q}\n" for q in self.quantizers.values()],
        ]
        if multirate:
            lines += [
                f"static uint32_T rt_count[{len(steps)}];",
                *(["static uint8_T rt_first; /* 1 until the end of the first step */"] if offset_rates else []),
                *(
                    [f"static uint8_T rt_hit[{len(steps)}]; /* shared by the task functions */"]
                    if self.task_plan is not None
                    else []
                ),
                "",
            ]
        lines += [f"void {pfx}_initialize(void)", "{", "    uint32_T i;", "    (void)i;"]
        for f in self.states.values():
            if all(v == 0 for v in f.init):
                zero = f"({f.dtype.c_type})0" if self.integer_only and not f.dtype.is_double else "0.0"
                lines.append(f"    for (i = 0u; i < {f.size}u; i++) {{ {self.ref('DW')}.{f.name}[i] = {zero}; }}")
            else:
                for k, v in enumerate(f.init):
                    value = f"({f.dtype.c_type}){int(v)}" if self.integer_only and not f.dtype.is_double else c_float(v)
                    lines.append(f"    {self.ref('DW')}.{f.name}[{k}] = {value};")
        if multirate:
            for k, (n, off) in enumerate(steps):
                lines.append(
                    f"    rt_count[{k}] = {(n - off) % n}u; /* rate {k}: every {n} base steps, offset {off} */"
                )
            if offset_rates:
                lines.append("    rt_first = 1u;")
        lines += [f"    {x}" for x in self.extra_init]
        lines += [f"    {x}" for x in init_out]
        if self.task_plan is not None:
            lines += ["}", "", *self._task_functions(codes, steps)]
            return "\n".join(lines)
        lines += ["}", "", f"void {pfx}_step(void)", "{", "    uint32_T i;"]
        if multirate:
            lines += [f"    {self._rt_period(steps)}", f"    uint8_T rt_hit[{len(steps)}];"]
        lines += ["    (void)i;"]
        if multirate:
            lines += [f"    for (i = 0u; i < {len(steps)}u; i++) {{ rt_hit[i] = (rt_count[i] == 0u) ? 1u : 0u; }}"]
        lines += ["", "    /* outputs */", *[f"    {x}" for x in body_out]]
        lines += ["", "    /* state updates */", *[f"    {x}" for x in body_upd]]
        if offset_rates:
            lines += ["    rt_first = 0u;"]
        if multirate:
            lines += [
                "",
                "    /* advance the rate counters */",
                f"    for (i = 0u; i < {len(steps)}u; i++) {{",
                "        rt_count[i] = ((rt_count[i] + 1u) == rt_period[i]) ? 0u : (rt_count[i] + 1u);",
                "    }",
            ]
        lines += ["}", ""]
        return "\n".join(lines)

    # ------------------------------------------------------------------ one function per task (RTOS)

    def task_name(self, k: int) -> str:
        period = self.model.rates[k].period
        ms = period * 1000
        tag = f"{round(ms)}ms" if abs(ms - round(ms)) < 1e-9 and ms >= 1 else f"{period:g}s".replace(".", "p")
        return f"{self.prefix}_task_{tag}"

    @staticmethod
    def _rt_period(steps: list[tuple[int, int]]) -> str:
        """The base steps between two runs of each rate (a constant local of the function that counts them)."""
        return f"static const uint32_T rt_period[{len(steps)}] = {{" + ", ".join(f"{n}u" for n, _ in steps) + "};"

    def _task_plan(self, steps: list[tuple[int, int]]) -> dict[str, Any]:
        """Which task runs the outputs and the update of every block, checked for deterministic data transfer."""
        m = self.model
        if any(off for _n, off in steps) or (steps and min(n for n, _ in steps) != 1):
            raise CodegenError("one function per task needs rates without offsets, all multiples of the fastest rate")
        fastest = min(range(len(m.rates)), key=lambda k: m.rates[k].period)
        n = len(m.blocks)
        out_task: list[int | None] = [None] * n
        upd_task: list[int | None] = [None] * n
        guard: list[int | None] = [None] * n
        consumers: list[list[int]] = [[] for _ in range(n)]
        for i in range(n):
            for s, _p in m.inputs[i]:
                consumers[s].append(i)
        for i, b in enumerate(m.blocks):
            kind = m.kinds[i]
            if kind is Kind.CONSTANT:
                continue
            if kind is not Kind.DISCRETE:
                if isinstance(b, Inport):
                    out_task[i] = fastest  # copies the inputs; read by every task
                    continue
                periods = sorted(
                    {m.rates[m.rate_index[s]].period for s, _p in m.inputs[i] if m.kinds[s] is Kind.DISCRETE}
                )
                if len(periods) > 1:
                    raise CodegenError(
                        f"{m.names[i]} gets signals from tasks with different rates ("
                        + " s and ".join(f"{p:g}" for p in periods)
                        + " s): insert a Rate Transition block on the inputs that come from the other task",
                        (m.names[i],),
                    )
                raise CodegenError(
                    f"{m.names[i]}: one function per task needs every block to have a discrete rate. Set its "
                    "sample time, sample the model inputs it uses with a Zero-Order Hold, or put it in an atomic "
                    "subsystem with a sample time",
                    (m.names[i],),
                )
            out_task[i] = upd_task[i] = m.rate_index[i]
        for i, b in enumerate(m.blocks):  # rate transitions run in the fast task, at the slow rate's ticks
            if b.type_id != "discrete.rate_transition":
                continue
            slow = m.rate_index[i]
            if b.params["direction"] == "fast_to_slow":
                s = m.inputs[i][0][0]
                src_task = out_task[s]
                fast = src_task if src_task is not None else slow
                if m.rates[fast].period > m.rates[slow].period:
                    raise CodegenError(
                        f"{m.names[i]}: its input is slower than its sample time; use slow_to_fast", (m.names[i],)
                    )
                out_task[i], upd_task[i], guard[i] = fast, None, slow if fast != slow else None
            else:
                rates = [t for c in consumers[i] if (t := out_task[c]) is not None]
                fast = min(rates, key=lambda k: m.rates[k].period) if rates else slow
                out_task[i], upd_task[i], guard[i] = fast, slow, slow if fast != slow else None
        for i, b in enumerate(m.blocks):  # every other signal must stay within one task
            if out_task[i] is None or (
                b.type_id == "discrete.rate_transition" and b.params["direction"] == "fast_to_slow"
            ):
                continue
            mine = {t for t in (out_task[i], upd_task[i]) if t is not None}
            if b.type_id == "discrete.rate_transition":
                mine = {m.rate_index[i]}  # its input is read by the slow task's update
            for s, _p in m.inputs[i]:
                src = m.blocks[s]
                src_task = out_task[s]
                if m.kinds[s] is Kind.CONSTANT or src_task is None or src.type_id == "discrete.rate_transition":
                    continue
                if isinstance(src, Inport) and m.kinds[s] is not Kind.DISCRETE:
                    continue
                if src_task not in mine or len(mine) > 1:
                    raise CodegenError(
                        f"{m.names[s]} ({m.rates[src_task].period:g} s) feeds {m.names[i]} "
                        f"({m.rates[next(iter(mine))].period:g} s): insert a Rate Transition block between the two "
                        "tasks (fast_to_slow or slow_to_fast)",
                        (m.names[s], m.names[i]),
                    )
        order = sorted(range(len(m.rates)), key=lambda k: m.rates[k].period)
        return {"out": out_task, "upd": upd_task, "guard": guard, "order": order, "fastest": fastest}

    def _task_declarations(self) -> list[str]:
        if getattr(self, "task_plan", None) is None:
            return []
        pfx, m = self.prefix, self.model
        lines = [
            "",
            "/* One function per task. Call each one every period; at the same tick, the faster task first (as a",
        ]
        lines.append(f" * rate-monotonic RTOS does). {pfx}_step() does that for you when called every base period. */")
        for k in self.task_plan["order"]:  # type: ignore[index]
            lines.append(f"void {self.task_name(k)}(void); /* every {m.rates[k].period:g} s */")
        return lines

    def _task_functions(self, codes: list[BlockCode], steps: list[tuple[int, int]]) -> list[str]:
        n_rates = len(steps)
        plan = self.task_plan
        assert plan is not None
        pfx, m = self.prefix, self.model
        fastest = plan["fastest"]
        lines = []
        for k in plan["order"]:
            out_lines: list[str] = []
            upd_lines: list[str] = []
            for i, code in enumerate(codes):
                label = f"/* {m.names[i]} ({m.blocks[i].label or 'port'}) */"
                g = plan["guard"][i]
                if plan["out"][i] == k and code.outputs:
                    body = (
                        [f"if (rt_hit[{g}] != 0u) {{", *[f"    {x}" for x in code.outputs], "}"]
                        if g is not None
                        else code.outputs
                    )
                    out_lines += [label, *body]
                if plan["upd"][i] == k and code.update:
                    upd_lines += [label, *code.update]
            lines += [f"void {self.task_name(k)}(void)", "{", "    uint32_T i;"]
            if k == fastest and n_rates > 1:
                lines += [f"    {self._rt_period(steps)}"]
            lines += ["    (void)i;"]
            if k == fastest and n_rates > 1:
                lines += [f"    for (i = 0u; i < {n_rates}u; i++) {{ rt_hit[i] = (rt_count[i] == 0u) ? 1u : 0u; }}"]
            lines += ["", "    /* outputs */", *[f"    {x}" for x in out_lines]]
            lines += ["", "    /* state updates */", *[f"    {x}" for x in upd_lines]]
            if k == fastest and n_rates > 1:
                lines += [
                    "",
                    "    /* this task counts the ticks of all tasks */",
                    f"    for (i = 0u; i < {n_rates}u; i++) {{",
                    "        rt_count[i] = ((rt_count[i] + 1u) == rt_period[i]) ? 0u : (rt_count[i] + 1u);",
                    "    }",
                ]
            lines += ["}", ""]
        lines += [f"void {pfx}_step(void)", "{", f"    {self.task_name(fastest)}();"]
        for k in plan["order"]:
            if k != fastest:
                lines.append(f"    if (rt_hit[{k}] != 0u) {{ {self.task_name(k)}(); }}")
        lines += ["}", ""]
        return lines

    def _main_example_rtos(self, read: list[str], write: list[str]) -> str:
        """Template for an RTOS: one OS task per rate, the fastest with the highest priority."""
        plan = self.task_plan
        assert plan is not None
        m = self.model
        lines = [
            *self._banner("main_example.c: how to run the task functions on an RTOS (a template)"),
            f'#include "{self.prefix}.h"',
            "",
            "/* Give every rate its own periodic OS task. Faster tasks need a higher priority (rate-monotonic), so",
            " * that at a common tick the faster task runs first. Rate Transition blocks in the model take care of",
            " * the data passed between the tasks. */",
            "",
        ]
        for rank, k in enumerate(plan["order"]):
            period = m.rates[k].period
            lines += [f"/* priority {len(plan['order']) - rank} (highest = fastest), every {period:g} s */"]
            lines += [f"void os_task_{self.task_name(k).split('_task_')[-1]}(void)", "{"]
            if k == plan["fastest"]:
                lines += read
            lines.append(f"    {self.task_name(k)}();")
            if k == plan["fastest"]:
                lines += write
            lines += ["}", ""]
        lines += [
            "int main(void)",
            "{",
            f"    {self.prefix}_initialize();",
            "    /* TODO: create the OS tasks above with their periods and priorities, then start the scheduler,",
            "     * e.g. FreeRTOS: xTaskCreate(...); vTaskStartScheduler(); */",
            "    for (;;) {",
            "    }",
            "}",
            "",
        ]
        return "\n".join(lines)

    def _main_example(self, ins: list[tuple[str, int]], outs: list[tuple[str, int]], base: float) -> str:
        """A template for the ECU side: how to call the generated code from a periodic task."""
        pfx = self.prefix
        read = [f"    {pfx}_U.{n}[{k}] = 0.0; /* TODO: read sensor */" for n, w in ins for k in range(w)]
        write = [f"    (void){pfx}_Y.{n}[{k}]; /* TODO: drive actuator */" for n, w in outs for k in range(w)]
        if getattr(self, "task_plan", None) is not None:
            return self._main_example_rtos(read, write)
        lines = [
            *self._banner("main_example.c: how to run the generated code on a target (a template)"),
            f'#include "{pfx}.h"',
            "",
            f"/* Call this every {pfx.upper()}_BASE_PERIOD = {base:g} s, e.g. from a timer interrupt or an RTOS task. */",
            f"void {pfx}_task(void)",
            "{",
            *read,
            f"    {pfx}_step();",
            *write,
            "}",
            "",
            "int main(void)",
            "{",
            f"    {pfx}_initialize();",
            "    for (;;) {",
            "        /* TODO: wait for the next period tick */",
            f"        {pfx}_task();",
            "    }",
            "}",
            "",
        ]
        return "\n".join(lines)

    def _report(
        self,
        source: str,
        ins: list[tuple[str, int]],
        outs: list[tuple[str, int]],
        base: float,
        steps: list[tuple[int, int]],
    ) -> str:
        """An HTML code generation report: interface, rates, parameters, and block-to-code traceability."""
        import html

        m = self.model
        rows = []
        lines = source.splitlines()
        for i, name in enumerate(m.names):
            marker = f"/* {name} ("
            found = [n + 1 for n, line in enumerate(lines) if line.strip().startswith(marker)]
            st = m.sample_times[i]
            links = ", ".join(f'<a href="#L{n}">{n}</a>' for n in found) or "—"
            rows.append(
                f"<tr><td>{html.escape(name)}</td><td>{html.escape(m.blocks[i].label or 'port')}</td>"
                f"<td>{html.escape(str(st))}</td><td>{links}</td></tr>"
            )
        code_html = "\n".join(
            f'<span id="L{n}" class="ln">{n:4d}</span>  {html.escape(line)}' for n, line in enumerate(lines, 1)
        )
        param_rows = []
        for f in self.params.values():
            values = ", ".join(f"{v:g}" for v in f.init[:8]) + (" …" if f.size > 8 else "")
            param_rows.append(
                f"<tr><td><code>{html.escape(f.name)}</code></td><td>{f.size}</td><td>{html.escape(values)}</td>"
                f"<td>{html.escape(f.comment)}</td></tr>"
            )
        params = "".join(param_rows)
        io = "".join(f"<tr><td>input</td><td><code>{n}</code></td><td>{w}</td></tr>" for n, w in ins) + "".join(
            f"<tr><td>output</td><td><code>{n}</code></td><td>{w}</td></tr>" for n, w in outs
        )
        rates = "".join(f"<li>rate {k}: every {n} base step(s), offset {off}</li>" for k, (n, off) in enumerate(steps))
        css = (
            "body{font-family:system-ui,sans-serif;margin:24px;color:#1d1d1b;background:#fcfcfb}"
            "table{border-collapse:collapse;margin:8px 0 20px}td,th{border:1px solid #dcdbd5;padding:4px 8px;text-align:left}"
            "pre{background:#f3f3f1;padding:12px;overflow:auto;font-size:12px}.ln{color:#86857f}"
            "@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#f2f2ef}pre{background:#262625}"
            "td,th{border-color:#353532}a{color:#3987e5}}"
        )
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>{self.prefix} code generation report</title>
<style>{css}</style></head><body>
<h1>Code generation report: {html.escape(self.prefix)}</h1>
<p>Model <b>{html.escape(m.diagram.name)}</b>, generated by Nervus {__version__}. Base period {base:g} s
(call <code>{self.prefix}_step()</code> every {base:g} s).</p>
<ul>{rates}</ul>
<h2>Interface</h2><table><tr><th>kind</th><th>field</th><th>width</th></tr>{io}</table>
<h2>Tunable parameters</h2><table><tr><th>field</th><th>size</th><th>value</th><th>source</th></tr>{params}</table>
<h2>Blocks and code</h2><table><tr><th>block</th><th>type</th><th>sample time</th><th>lines in {self.prefix}.c</th></tr>
{"".join(rows)}</table>
<h2>{self.prefix}.c</h2><pre>{code_html}</pre>
</body></html>
"""

    def _sil_copy_in(self, pfx: str, n: str, w: int) -> list[str]:
        """double values -> typed input (quantized exactly like the simulation's In block)."""
        dtype = self.io_types.get(n, DOUBLE)
        if dtype.is_double:
            return [f"        memcpy({pfx}_U.{n}, v, sizeof({pfx}_U.{n}));"]
        q = self.quantizer(dtype)
        to_stored = (
            f"({dtype.c_type})round(({q}(v[i]) - {c_float(dtype.bias)}) / {c_float(dtype.slope)})"
            if dtype.is_fixed
            else f"({dtype.c_type}){q}(v[i])"
        )
        return [f"        {{ int i; for (i = 0; i < {w}; i++) {{ {pfx}_U.{n}[i] = {to_stored}; }} }}"]

    def _sil_copy_out(self, pfx: str, n: str, w: int) -> list[str]:
        dtype = self.io_types.get(n, DOUBLE)
        if dtype.is_double:
            return [f"        memcpy(v, {pfx}_Y.{n}, sizeof({pfx}_Y.{n}));"]
        value = (
            f"(double){pfx}_Y.{n}[i] * {c_float(dtype.slope)} + {c_float(dtype.bias)}"
            if dtype.is_fixed
            else f"(double){pfx}_Y.{n}[i]"
        )
        return [f"        {{ int i; for (i = 0; i < {w}; i++) {{ v[i] = {value}; }} }}"]

    def _sil(self, ins: list[tuple[str, int]], outs: list[tuple[str, int]]) -> str:
        """Accessors used by the software-in-the-loop harness (not needed on the ECU)."""
        pfx = self.prefix
        lines = [
            *self._banner(f"{pfx}_sil.c: accessors for software-in-the-loop testing"),
            f'#include "{pfx}.h"',
            "#include <math.h>",
            "#include <string.h>",
            "",
            "@QUANTIZERS@",
        ]
        lines += ["void nervus_sil_set_input(int k, const double *v)", "{", "    switch (k) {"]
        for k, (n, _w) in enumerate(ins):
            lines += [f"    case {k}:", *self._sil_copy_in(pfx, n, _w), "        break;"]
        lines += ["    default: break;", "    }", "}", ""]
        lines += ["void nervus_sil_get_output(int k, double *v)", "{", "    switch (k) {"]
        for k, (n, _w) in enumerate(outs):
            lines += [f"    case {k}:", *self._sil_copy_out(pfx, n, _w), "        break;"]
        lines += ["    default: break;", "    }", "}", ""]
        defs = "\n\n".join(q for name, q in self.quantizers.items() if any(name in line for line in lines))
        lines = [defs if line == "@QUANTIZERS@" else line for line in lines]
        lines += [
            f"void nervus_sil_initialize(void) {{ {pfx}_initialize(); }}",
            f"void nervus_sil_step(void) {{ {pfx}_step(); }}",
            "",
        ]
        return "\n".join(lines)


def _pass_through(e: Emitter) -> BlockCode:
    """Subsystem port: copy the signal (the C compiler optimises this away)."""

    def body(ix: str) -> list[str]:
        return [f"{e.y(0, ix)} = {e.u(0, ix)};"]

    return BlockCode(outputs=e.each(e.w(), body))


def _int_templates() -> dict[str, Any]:
    from nervus.codegen.integer import INT_TEMPLATES

    return INT_TEMPLATES


def _integer_error() -> type[Exception]:
    from nervus.codegen.integer import IntegerCodeError

    return IntegerCodeError


def generate_c(target: Diagram | Block, name: str | None = None, tasks: bool = False,
               integer_only: bool = False) -> GeneratedCode:  # fmt: skip
    """Generate C code for a model, or for a subsystem of a model.

    For a subsystem, its In/Out blocks become the inputs and outputs of the generated code, and the rest of
    the model (e.g. the plant) is left out. Every block in the generated part must be discrete-time (or
    constant/stateless) and have a C template.
    """
    system = getattr(target, "system", None)
    diagram = system if isinstance(system, Diagram) else target
    if not isinstance(diagram, Diagram):
        raise TypeError("generate_c needs a Diagram or a Subsystem block")
    from nervus.codegen.functions import has_functions, replace_with_calls

    if has_functions(diagram):  # subsystems that become reusable functions: generate from a copy with calls
        label = name or (target.name if isinstance(target, Block) else diagram.name) or "model"
        work = replace_with_calls(diagram)
        if isinstance(target, Block) and diagram is system:
            work_target: Diagram | Block = copy.copy(target)
            setattr(work_target, "system", work)  # noqa: B010  (a Subsystem's system)
        else:
            work_target = work
        code = generate_c(work_target, label, tasks, integer_only)
        rate = None
        if isinstance(target, Block) and diagram is system and "sample_time" in target.params:
            st = target.sample_time()
            rate = None if st.is_inherited else st
        code.model = compile_diagram(diagram, default_rate=rate)  # the model itself: the reference for SIL tests
        return code
    rate = None
    if isinstance(target, Block) and diagram is system and "sample_time" in target.params:
        if target.diagram is not None:
            target.resolve(target.diagram.namespace())  # e.g. sample_time = "Ts"
        st = target.sample_time()
        rate = None if st.is_inherited else st  # an atomic subsystem: its own task period
    model = compile_diagram(diagram, default_rate=rate)
    label = name or (target.name if isinstance(target, Block) else diagram.name) or "model"
    return CGenerator(model, label, tasks=tasks, integer_only=integer_only).generate()


# Register the block templates.
from nervus.codegen import templates as _templates  # noqa: E402,F401
