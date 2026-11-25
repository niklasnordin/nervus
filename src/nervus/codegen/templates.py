"""C code templates for the standard blocks.

Each template receives an :class:`~nervus.codegen.generator.Emitter` and returns the C statements for the
block's output equation and (for blocks with state) its state update. Signals are ``real_T`` arrays; ``ix``
is the element index (``"0"`` for scalars, ``"i"`` inside a loop).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

import numpy as np

from nervus.codegen.generator import BlockCode, CodegenError, Emitter, c_float, c_index, template
from nervus.core.types import as_signal

# ---------------------------------------------------------------------------- ports, sources, sinks


@template("ports.inport")
def _inport(e: Emitter) -> BlockCode:
    from nervus.codegen.generator import c_identifier

    field = c_identifier(e.path)
    dtype = e.gen.model.out_types[e.i][0]
    scale = f" * {c_float(dtype.slope)} + {c_float(dtype.bias)}" if dtype.is_fixed and not dtype.is_integer else ""
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = (real_T){e.gen.ref('U')}.{field}[{ix}]{scale};"]))


@template("ports.outport")
def _outport(e: Emitter) -> BlockCode:
    from nervus.codegen.generator import c_identifier

    field = c_identifier(e.path)
    s, p = e.gen.model.inputs[e.i][0]
    dtype = e.gen.model.out_types[s][p]
    if dtype.is_double:
        return BlockCode(e.each(e.in_widths[0], lambda ix: [f"{e.gen.ref('Y')}.{field}[{ix}] = {e.u(0, ix)};"]))
    if dtype.is_fixed and not dtype.is_integer:

        def conv(ix: str) -> str:
            return f"({dtype.c_type})round(({e.u(0, ix)} - {c_float(dtype.bias)}) / {c_float(dtype.slope)})"
    else:

        def conv(ix: str) -> str:
            return f"({dtype.c_type}){e.u(0, ix)}"

    return BlockCode(e.each(e.in_widths[0], lambda ix: [f"{e.gen.ref('Y')}.{field}[{ix}] = {conv(ix)};"]))


@template("routing.datatype_conversion")
def _dtc(e: Emitter) -> BlockCode:
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.u(0, ix)};"]))  # quantized afterwards


@template("sinks.scope", "sinks.to_workspace", "sinks.terminator", "sinks.stop", "sinks.assertion")
def _sink(e: Emitter) -> BlockCode:
    return BlockCode()  # simulation-only blocks: nothing on the target


@template("sources.constant")
def _constant(e: Emitter) -> BlockCode:
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.p('value', ix)};"]))


@template("sources.ground")
def _ground(e: Emitter) -> BlockCode:
    return BlockCode([f"{e.y(0, '0')} = 0.0;"])


# ---------------------------------------------------------------------------- math


@template("math.gain")
def _gain(e: Emitter) -> BlockCode:
    if e.block.params["multiplication"] == "matrix":
        k = np.asarray(e.block.params["gain"], dtype=float)
        rows, cols = k.shape
        kp = e.param("gain", k.ravel())  # row-major
        u = e.u_var(0)
        return BlockCode(
            [
                f"for (i = 0u; i < {rows}u; i++) {{",
                "    uint32_T j;",
                f"    {e.y(0, 'i')} = 0.0;",
                f"    for (j = 0u; j < {cols}u; j++) {{ {e.y(0, 'i')} += {kp}[(i * {cols}u) + j] * {u}[j]; }}",
                "}",
            ]
        )
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.p('gain', ix)} * {e.u(0, ix)};"]))


@template("math.sum")
def _sum(e: Emitter) -> BlockCode:
    signs = e.block._signs()  # type: ignore[attr-defined]

    def body(ix: str) -> list[str]:
        terms = "".join(f" {'+' if s == '+' else '-'} {e.u(k, ix)}" for k, s in enumerate(signs))
        terms = terms.strip()
        if terms.startswith("+ "):
            terms = terms[2:]
        elif terms.startswith("- "):
            terms = "-" + terms[2:]
        return [f"{e.y(0, ix)} = {terms};"]

    return BlockCode(e.each(e.w(), body))


@template("math.product")
def _product(e: Emitter) -> BlockCode:
    ops = e.block.params["ops"]

    def body(ix: str) -> list[str]:
        expr = "1.0"
        for k, op in enumerate(ops):
            expr = f"{expr} {op} {e.u(k, ix)}"
        return [f"{e.y(0, ix)} = {expr};"]

    return BlockCode(e.each(e.w(), body))


@template("math.abs")
def _abs(e: Emitter) -> BlockCode:
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = fabs({e.u(0, ix)});"]))


@template("math.bias")
def _bias(e: Emitter) -> BlockCode:
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.u(0, ix)} + {e.p('bias', ix)};"]))


@template("math.saturation")
def _saturation(e: Emitter) -> BlockCode:
    def body(ix: str) -> list[str]:
        lo, hi, u, y = e.p("lower", "0"), e.p("upper", "0"), e.u(0, ix), e.y(0, ix)
        return [f"{y} = ({u} > {hi}) ? {hi} : (({u} < {lo}) ? {lo} : {u});"]

    return BlockCode(e.each(e.w(), body))


_C_FUNCTIONS = {
    "exp": "exp({u})",
    "log": "log({u})",
    "log10": "log10({u})",
    "sqrt": "sqrt({u})",
    "square": "({u}) * ({u})",
    "reciprocal": "1.0 / ({u})",
    "sin": "sin({u})",
    "cos": "cos({u})",
    "tan": "tan({u})",
    "asin": "asin({u})",
    "acos": "acos({u})",
    "atan": "atan({u})",
    "sinh": "sinh({u})",
    "cosh": "cosh({u})",
    "tanh": "tanh({u})",
    "sign": "(({u}) > 0.0 ? 1.0 : (({u}) < 0.0 ? -1.0 : 0.0))",
    "floor": "floor({u})",
    "ceil": "ceil({u})",
    "round": "nearbyint({u})",
}


@template("math.function")
def _math_function(e: Emitter) -> BlockCode:
    fmt = _C_FUNCTIONS[e.block.params["function"]]
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {fmt.format(u=e.u(0, ix))};"]))


@template("math.minmax")
def _minmax(e: Emitter) -> BlockCode:
    fn = "fmin" if e.block.params["function"] == "min" else "fmax"
    if e.block.num_inputs() == 1:
        w = e.in_widths[0]
        u = e.u_var(0)
        return BlockCode(
            [
                f"{e.y(0, '0')} = {u}[0];",
                f"for (i = 1; i < {w}; i++) {{ {e.y(0, '0')} = {fn}({e.y(0, '0')}, {u}[i]); }}",
            ]
        )

    def body(ix: str) -> list[str]:
        expr = e.u(0, ix)
        for k in range(1, e.block.num_inputs()):
            expr = f"{fn}({expr}, {e.u(k, ix)})"
        return [f"{e.y(0, ix)} = {expr};"]

    return BlockCode(e.each(e.w(), body))


# ---------------------------------------------------------------------------- discontinuities, logic


@template("discontinuities.dead_zone")
def _dead_zone(e: Emitter) -> BlockCode:
    def body(ix: str) -> list[str]:
        lo, hi, u, y = e.p("start", "0"), e.p("end", "0"), e.u(0, ix), e.y(0, ix)
        return [f"{y} = ({u} > {hi}) ? ({u} - {hi}) : (({u} < {lo}) ? ({u} - {lo}) : 0.0);"]

    return BlockCode(e.each(e.w(), body))


@template("discontinuities.quantizer")
def _quantizer(e: Emitter) -> BlockCode:
    q = e.p("interval", "0")
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {q} * nearbyint({e.u(0, ix)} / {q});"]))


@template("discontinuities.relay")
def _relay(e: Emitter) -> BlockCode:
    w = e.w()
    s = e.state("state", e.block.initial_discrete_state(), "relay on/off state")
    on, off = e.p("on_point", "0"), e.p("off_point", "0")
    von, voff = e.p("on_value", "0"), e.p("off_value", "0")

    def nxt(ix: str) -> str:
        u = e.u(0, ix)
        return f"(({u} >= {on}) ? 1.0 : (({u} <= {off}) ? 0.0 : {s}[{ix}]))"

    out = e.each(w, lambda ix: [f"{e.y(0, ix)} = ({nxt(ix)} > 0.5) ? {von} : {voff};"])
    upd = e.each(w, lambda ix: [f"{s}[{ix}] = {nxt(ix)};"])
    return BlockCode(out, upd)


@template("discontinuities.rate_limiter")
def _rate_limiter(e: Emitter) -> BlockCode:
    s = e.state("prev", e.block.initial_discrete_state(), "previous output")
    ts = c_float(e.period)
    rise, fall = e.p("rising_rate", "0"), e.p("falling_rate", "0")

    def body(ix: str) -> list[str]:
        u, y, prev = e.u(0, ix), e.y(0, ix), f"{s}[{ix}]"
        return [f"{y} = fmin(fmax({u}, {prev} + ({fall} * {ts})), {prev} + ({rise} * {ts}));"]

    return BlockCode(e.each(e.w(), body), e.each(e.w(), lambda ix: [f"{s}[{ix}] = {e.y(0, ix)};"]))


_REL = {"==": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


@template("logic.relational")
def _relational(e: Emitter) -> BlockCode:
    op = _REL[e.block.params["operator"]]
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = ({e.u(0, ix)} {op} {e.u(1, ix)}) ? 1.0 : 0.0;"]))


@template("logic.compare_to_constant")
def _compare_const(e: Emitter) -> BlockCode:
    op = _REL[e.block.params["operator"]]
    c = e.p("constant", "0")
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = ({e.u(0, ix)} {op} {c}) ? 1.0 : 0.0;"]))


@template("logic.logical")
def _logical(e: Emitter) -> BlockCode:
    op = e.block.params["operator"]

    def body(ix: str) -> list[str]:
        terms = [f"({e.u(k, ix)} != 0.0)" for k in range(e.block.num_inputs())]
        if op == "NOT":
            expr = f"!{terms[0]}"
        elif op in ("AND", "NAND"):
            expr = " && ".join(terms)
        elif op in ("OR", "NOR"):
            expr = " || ".join(terms)
        else:  # XOR: odd number of true inputs
            expr = "((" + " + ".join(f"(int){t}" for t in terms) + ") % 2 == 1)"
        if op in ("NAND", "NOR"):
            expr = f"!({expr})"
        return [f"{e.y(0, ix)} = ({expr}) ? 1.0 : 0.0;"]

    return BlockCode(e.each(e.w(), body))


# ---------------------------------------------------------------------------- routing


@template("routing.mux")
def _mux(e: Emitter) -> BlockCode:
    lines, offset = [], 0
    for k, w in enumerate(e.in_widths):
        u = e.u_var(k)
        lines.append(f"for (i = 0u; i < {w}u; i++) {{ {e.y(0, c_index(offset, 'i'))} = {u}[i]; }}")
        offset += w
    return BlockCode(lines)


@template("routing.demux")
def _demux(e: Emitter) -> BlockCode:
    lines, offset = [], 0
    u = e.u_var(0)
    for p, w in enumerate(e.out_widths):
        lines.append(f"for (i = 0u; i < {w}u; i++) {{ {e.y(p, 'i')} = {u}[{c_index(offset, 'i')}]; }}")
        offset += w
    return BlockCode(lines)


@template("routing.switch")
def _switch(e: Emitter) -> BlockCode:
    crit = e.block.params["criterion"]
    thr = e.p("threshold", "0")
    c = e.u(1, "0")
    cond = (
        f"{c} >= {thr}" if crit == "u1 >= threshold" else f"{c} > {thr}" if crit == "u1 > threshold" else f"{c} != 0.0"
    )
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = ({cond}) ? {e.u(0, ix)} : {e.u(2, ix)};"]))


# ---------------------------------------------------------------------------- discrete


@template("discrete.zero_order_hold")
def _zoh(e: Emitter) -> BlockCode:
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.u(0, ix)};"]))


@template("discrete.unit_delay")
def _unit_delay(e: Emitter) -> BlockCode:
    s = e.state("x", e.block.initial_discrete_state(), "previous input")
    out = e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {s}[{ix}];"])
    upd = e.each(e.w(), lambda ix: [f"{s}[{ix}] = {e.u(0, ix)};"])
    return BlockCode(out, upd)


@template("routing.initial_condition")
def _initial_condition(e: Emitter) -> BlockCode:
    value = np.broadcast_to(np.asarray(e.block.params["value"], dtype=float).ravel(), (e.w(),))
    first = e.state("first", [1.0], "1 until the first step has run")
    vals = [c_float(float(v)) for v in value]
    out = [
        f"if ({first}[0] != 0.0) {{",
        *[f"    {e.y(0, str(k))} = {v};" for k, v in enumerate(vals)],
        "} else {",
        *[f"    {x}" for x in e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.u(0, ix)};"])],
        "}",
    ]
    return BlockCode(out, [f"{first}[0] = 0.0;"])


@template("discrete.rate_transition")
def _rate_transition(e: Emitter) -> BlockCode:
    if e.block.params["direction"] == "fast_to_slow":
        return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.u(0, ix)};"]))
    s = e.state("x", e.block.initial_discrete_state(), "value from the slow task, one slow period ago")
    out = e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {s}[{ix}];"])
    upd = e.each(e.w(), lambda ix: [f"{s}[{ix}] = {e.u(0, ix)};"])
    return BlockCode(out, upd)


@template("discrete.integer_delay")
def _integer_delay(e: Emitter) -> BlockCode:
    w, n = e.w(), int(e.block.params["delay"])
    s = e.state("buf", e.block.initial_discrete_state(), f"last {n} inputs, oldest first")
    out = e.each(w, lambda ix: [f"{e.y(0, ix)} = {s}[{ix}];"])
    upd = [f"for (i = 0u; i < {(n - 1) * w}u; i++) {{ {s}[i] = {s}[i + {w}u]; }}"] if n > 1 else []
    upd += e.each(w, lambda ix: [f"{s}[{c_index((n - 1) * w, ix)}] = {e.u(0, ix)};"])
    return BlockCode(out, upd)


@template("discrete.integrator")
def _discrete_integrator(e: Emitter) -> BlockCode:
    b = e.block
    w = e.w()
    reset = b.params.get("external_reset", "none")
    s = e.state(
        "x", b.initial_discrete_state(), "accumulator" + (f", [{w}] previous reset input" if reset != "none" else "")
    )
    k, lo, hi = e.p("gain", "0"), e.p("lower", "0"), e.p("upper", "0")
    ts = c_float(e.period)

    def step(ix: str) -> str:
        return f"fmin(fmax({s}[{ix}] + ({k} * {ts} * {e.u(0, ix)}), {lo}), {hi})"

    pre: list[str] = []
    post: list[str] = []
    if reset != "none":
        # The reset is decided when the outputs are computed: the state goes back to its initial value first, so
        # the output and the update of this step both use it (as in the simulation).
        r, prev = e.u(1, "0"), f"{s}[{w}]"
        rising, falling = f"(({prev} <= 0.0) && ({r} > 0.0))", f"(({prev} > 0.0) && ({r} <= 0.0))"
        cond = {"rising": rising, "falling": falling, "either": f"({rising} || {falling})", "level": f"({r} != 0.0)"}
        x0 = b._x0()  # type: ignore[attr-defined]
        pre = [f"if {cond[reset]} {{ /* external reset ({reset}) */"]
        pre += [f"    {s}[{ix}] = {c_float(float(x0[ix]))};" for ix in range(w)]
        pre += ["}", f"{prev} = {r};"]
    if b.params["method"] == "backward_euler":
        out = pre + e.each(w, lambda ix: [f"{e.y(0, ix)} = {step(ix)};"])
        upd = e.each(w, lambda ix: [f"{s}[{ix}] = {e.y(0, ix)};"])
    else:
        out = pre + e.each(w, lambda ix: [f"{e.y(0, ix)} = {s}[{ix}];"])
        upd = e.each(w, lambda ix: [f"{s}[{ix}] = {step(ix)};"])
    return BlockCode(out, upd + post)


@template("discrete.transfer_function")
def _discrete_tf(e: Emitter) -> BlockCode:
    a, b, c, d = e.block.matrices()  # type: ignore[attr-defined]
    n = a.shape[0]
    if n == 0:
        dp = e.param("D", d.ravel())
        return BlockCode([f"{e.y(0, '0')} = {dp}[0] * {e.u(0, '0')};"])
    s = e.state("x", e.block.initial_discrete_state(), "state vector")
    ap, bp, cp, dp = (e.param(nm, m.ravel()) for nm, m in (("A", a), ("B", b), ("C", c), ("D", d)))
    u = e.u(0, "0")
    out = [f"{e.y(0, '0')} = {dp}[0] * {u};", f"for (i = 0u; i < {n}u; i++) {{ {e.y(0, '0')} += {cp}[i] * {s}[i]; }}"]
    upd = [
        "{",
        f"    real_T x_new[{n}];",
        "    uint32_T j;",
        f"    for (i = 0u; i < {n}u; i++) {{",
        f"        x_new[i] = {bp}[i] * {u};",
        f"        for (j = 0u; j < {n}u; j++) {{ x_new[i] += {ap}[(i * {n}u) + j] * {s}[j]; }}",
        "    }",
        f"    for (i = 0u; i < {n}u; i++) {{ {s}[i] = x_new[i]; }}",
        "}",
    ]
    return BlockCode(out, upd)


@template("discrete.counter")
def _counter(e: Emitter) -> BlockCode:
    s = e.state("count", e.block.initial_discrete_state(), "count")
    lim = e.p("limit", "0")
    wrap = e.block.params["at_limit"] == "wrap"

    def upd(ix: str) -> list[str]:
        nxt = f"{s}[{ix}] + (({e.u(0, ix)} != 0.0) ? 1.0 : 0.0)"
        if wrap:
            return [f"{s}[{ix}] = ({nxt} > {lim}) ? 0.0 : ({nxt});"]
        return [f"{s}[{ix}] = fmin({nxt}, {lim});"]

    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {s}[{ix}];"]), e.each(e.w(), upd))


@template("discrete.edge_detect")
def _edge_detect(e: Emitter) -> BlockCode:
    s = e.state("prev", e.block.initial_discrete_state(), "previous input (boolean)")
    d = e.block.params["direction"]

    def out(ix: str) -> list[str]:
        now, before = f"({e.u(0, ix)} != 0.0)", f"({s}[{ix}] != 0.0)"
        cond = (
            f"{now} && !{before}"
            if d == "rising"
            else f"!{now} && {before}"
            if d == "falling"
            else f"{now} != {before}"
        )
        return [f"{e.y(0, ix)} = ({cond}) ? 1.0 : 0.0;"]

    upd = e.each(e.w(), lambda ix: [f"{s}[{ix}] = ({e.u(0, ix)} != 0.0) ? 1.0 : 0.0;"])
    return BlockCode(e.each(e.w(), out), upd)


@template("discrete.debounce")
def _debounce(e: Emitter) -> BlockCode:
    s = e.state("dw", [0.0, 0.0], "[0] output state, [1] samples disagreeing with it")
    on_n, off_n = int(e.block.params["on_samples"]), int(e.block.params["off_samples"])
    u = e.u(0, "0")
    common = [
        f"real_T now = ({u} != 0.0) ? 1.0 : 0.0;",
        f"real_T st = {s}[0];",
        f"real_T cnt = {s}[1];",
        "if (now == st) { cnt = 0.0; }",
        f"else {{ cnt += 1.0; if (cnt >= ((now != 0.0) ? {on_n}.0 : {off_n}.0)) {{ st = now; cnt = 0.0; }} }}",
    ]
    out = ["{", *[f"    {x}" for x in common], f"    {e.y(0, '0')} = st;", "}"]
    upd = ["{", *[f"    {x}" for x in common], f"    {s}[0] = st;", f"    {s}[1] = cnt;", "}"]
    return BlockCode(out, upd)


@template("discrete.filter")
def _pt1(e: Emitter) -> BlockCode:
    s = e.state("y", e.block.initial_discrete_state(), "previous output")
    ts = c_float(e.period)
    tau = e.p("time_constant", "0")

    def out(ix: str) -> list[str]:
        return [f"{e.y(0, ix)} = {s}[{ix}] + ({ts} / ({tau} + {ts})) * ({e.u(0, ix)} - {s}[{ix}]);"]

    return BlockCode(e.each(e.w(), out), e.each(e.w(), lambda ix: [f"{s}[{ix}] = {e.y(0, ix)};"]))


@template("discrete.moving_average")
def _moving_average(e: Emitter) -> BlockCode:
    w, n = e.w(), int(e.block.params["length"])
    if n == 1:
        return BlockCode(e.each(w, lambda ix: [f"{e.y(0, ix)} = {e.u(0, ix)};"]))
    s = e.state("buf", e.block.initial_discrete_state(), f"last {n - 1} inputs")

    def out(ix: str) -> list[str]:
        return [
            f"{e.y(0, ix)} = {e.u(0, ix)};",
            "{",
            "    uint32_T k;",
            f"    for (k = 0u; k < {n - 1}u; k++) {{ {e.y(0, ix)} += {s}[{c_index(f'k * {w}u' if w != 1 else 'k', ix)}]; }}",
            "}",
            f"{e.y(0, ix)} /= {n}.0;",
        ]

    upd = [f"for (i = 0u; i < {(n - 2) * w}u; i++) {{ {s}[i] = {s}[i + {w}u]; }}"] if n > 2 else []
    upd += e.each(w, lambda ix: [f"{s}[{c_index((n - 2) * w, ix)}] = {e.u(0, ix)};"])
    return BlockCode(e.each(w, out), upd)


@template("control.discrete_pid")
def _discrete_pid(e: Emitter) -> BlockCode:
    if e.w() != 1:
        raise CodegenError(f"{e.path}: generated code supports scalar PID controllers only", (e.path,))
    s = e.state("x", e.block.initial_discrete_state(), "[0] integral, [1] derivative filter")
    kp, ki, kd, kn = (e.p(n, "0") for n in ("P", "I", "D", "N"))
    lo, hi, kb = e.p("lower", "0"), e.p("upper", "0"), e.p("Kb", "0")
    ts = c_float(e.period)
    aw = e.block.params["anti_windup"]
    u = e.u(0, "0")
    law = [
        f"real_T err = {u};",
        f"real_T d_term = {kd} * {kn} * (err - {s}[1]);",
        f"real_T unsat = ({kp} * err) + {s}[0] + d_term;",
        f"real_T y = fmin(fmax(unsat, {lo}), {hi});",
    ]
    di = [f"real_T di = {ki} * err;"]
    if aw == "none":
        di.append("(void)y; /* no anti-windup */")
    elif aw == "clamping":
        di.append("if ((unsat != y) && (((unsat - y) > 0.0) == (di > 0.0)) && (di != 0.0)) { di = 0.0; }")
    elif aw == "back_calculation":
        di.append(f"di += {kb} * (y - unsat);")
    out = ["{", *[f"    {x}" for x in law], f"    {e.y(0, '0')} = y;", "}"]
    upd = [
        "{",
        *[f"    {x}" for x in law + di],
        f"    {s}[0] += {ts} * di;",
        f"    {s}[1] += {ts} * {kn} * (err - {s}[1]);",
        "}",
    ]
    return BlockCode(out, upd)


# ---------------------------------------------------------------------------- lookup tables


def _locate_c(x: str, bp: Callable[[str], str], n: int, interp: str, extrap: str, i_var: str, f_var: str) -> list[str]:
    """C statements computing interval index ``i_var`` and fraction ``f_var``, matching lookup.locate().
    ``bp(ix)`` is the C expression of breakpoint ``ix`` (scaled if the breakpoints are stored as integers)."""
    lines = [f"{i_var} = 0u;"]
    if n > 2:  # with two breakpoints there is only one interval
        lines.append(f"while (({i_var} < {n - 2}u) && ({x} >= {bp(f'{i_var} + 1u')})) {{ {i_var}++; }}")
    lines += [
        f"{f_var} = ({x} - {bp(i_var)}) / ({bp(f'{i_var} + 1u')} - {bp(i_var)});",
    ]
    if extrap == "clip":
        lines.append(f"{f_var} = fmin(fmax({f_var}, 0.0), 1.0);")
    if interp == "nearest":
        lines.append(f"{f_var} = (fmin(fmax({f_var}, 0.0), 1.0) >= 0.5) ? 1.0 : 0.0;")
    elif interp == "flat":
        lines.append(f"{f_var} = ({x} >= {bp(str(n - 1))}) ? 1.0 : ((({f_var}) >= 1.0) ? 1.0 : 0.0);")
    return lines


@template("lookup.table_1d")
def _lookup1d(e: Emitter) -> BlockCode:
    bp_arr, tab_arr = e.block._data()  # type: ignore[attr-defined]
    n = bp_arr.size
    bp, tab = e.param("breakpoints", bp_arr, typed=True), e.param("table", tab_arr, typed=True)
    interp, extrap = e.block.params["interpolation"], e.block.params["extrapolation"]

    def t(ix: str) -> str:
        return e.at(tab, ix)

    def body(ix: str) -> list[str]:
        return [
            "{",
            "    uint32_T k;",
            "    real_T f;",
            *[f"    {x}" for x in _locate_c(e.u(0, ix), lambda j: e.at(bp, j), n, interp, extrap, "k", "f")],
            f"    {e.y(0, ix)} = {t('k')} + (f * ({t('k + 1u')} - {t('k')}));",
            "}",
        ]

    return BlockCode(e.each(e.w(), body))


@template("lookup.prelookup")
def _prelookup(e: Emitter) -> BlockCode:
    bp_arr = e.block._bp()  # type: ignore[attr-defined]
    bp = e.param("breakpoints", bp_arr, typed=True)
    extrap = e.block.params["extrapolation"]

    def body(ix: str) -> list[str]:
        return [
            "{",
            "    uint32_T k;",
            "    real_T f;",
            *[
                f"    {x}"
                for x in _locate_c(e.u(0, ix), lambda j: e.at(bp, j), bp_arr.size, "linear", extrap, "k", "f")
            ],
            f"    {e.y(0, ix)} = (real_T)k;",
            f"    {e.y(1, ix)} = f;",
            "}",
        ]

    return BlockCode(e.each(e.w(), body))


@template("lookup.interpolation")
def _interpolation(e: Emitter) -> BlockCode:
    tab_arr = e.block._table()  # type: ignore[attr-defined]
    tab = e.param("table", tab_arr.ravel(), typed=True)  # row-major
    shape = tab_arr.shape
    n = len(shape)
    strides = [int(np.prod(shape[d + 1 :])) for d in range(n)]

    def index(port: int, ix: str, size: int) -> str:
        # The index comes from a Prelookup (an integer stored as real_T); clamp it like the simulation does.
        return f"(uint32_T)fmin(fmax({e.u(port, ix)}, 0.0), {float(size - 2)})"

    if n == 1:

        def body1(ix: str) -> list[str]:
            return [
                "{",
                f"    uint32_T k = {index(0, ix, shape[0])};",
                f"    real_T f = {e.u(1, ix)};",
                f"    {e.y(0, ix)} = {e.at(tab, 'k')} + (f * ({e.at(tab, 'k + 1u')} - {e.at(tab, 'k')}));",
                "}",
            ]

        return BlockCode(e.each(e.w(), body1))

    def body(ix: str) -> list[str]:
        lines = ["{"]
        for d in range(n):
            lines.append(f"    uint32_T k{d + 1} = {index(2 * d, ix, shape[d])};")
            lines.append(f"    real_T f{d + 1} = {e.u(2 * d + 1, ix)};")
        lines.append(f"    uint32_T base = {' + '.join(f'k{d + 1} * {strides[d]}u' for d in range(n))};")
        terms = []
        for corner in range(1 << n):  # the same corners, weights and order as the simulation
            ups = [(corner >> (n - 1 - d)) & 1 for d in range(n)]
            weight = " * ".join(f"f{d + 1}" if up else f"(1.0 - f{d + 1})" for d, up in enumerate(ups))
            offset = sum(strides[d] * up for d, up in enumerate(ups))
            terms.append(f"{weight} * {e.at(tab, f'base + {offset}u' if offset else 'base')}")
        lines.append(f"    {e.y(0, ix)} = {terms[0]}")
        lines += [f"        + {t}" for t in terms[1:]]
        lines[-1] += ";"
        return [*lines, "}"]

    return BlockCode(e.each(e.w(), body))


@template("lookup.table_2d")
def _lookup2d(e: Emitter) -> BlockCode:
    r_arr, c_arr, t_arr = e.block._data()  # type: ignore[attr-defined]
    nr, nc = r_arr.size, c_arr.size
    rb = e.param("row_breakpoints", r_arr, typed=True)
    cb = e.param("column_breakpoints", c_arr, typed=True)
    tab = e.param("table", t_arr.ravel(), typed=True)  # row-major
    interp, extrap = e.block.params["interpolation"], e.block.params["extrapolation"]

    def t(i: str, j: str) -> str:
        return e.at(tab, f"(({i}) * {nc}u) + ({j})")

    def body(ix: str) -> list[str]:
        return [
            "{",
            "    uint32_T r;",
            "    uint32_T c;",
            "    real_T fr;",
            "    real_T fc;",
            *[f"    {x}" for x in _locate_c(e.u(0, ix), lambda j: e.at(rb, j), nr, interp, extrap, "r", "fr")],
            *[f"    {x}" for x in _locate_c(e.u(1, ix), lambda j: e.at(cb, j), nc, interp, extrap, "c", "fc")],
            f"    {e.y(0, ix)} = ((1.0 - fr) * (1.0 - fc) * {t('r', 'c')}) + ((1.0 - fr) * fc * {t('r', 'c + 1u')})",
            f"        + (fr * (1.0 - fc) * {t('r + 1u', 'c')}) + (fr * fc * {t('r + 1u', 'c + 1u')});",
            "}",
        ]

    return BlockCode(e.each(e.w(), body))


@template("lookup.adaptive_map")
def _adaptive_map(e: Emitter) -> BlockCode:
    r_arr, c_arr = e.block._axes()  # type: ignore[attr-defined]
    nr, nc = r_arr.size, c_arr.size
    rb = e.param("row_breakpoints", r_arr, typed=True)
    cb = e.param("column_breakpoints", c_arr, typed=True)
    rate, lim = e.p("rate", "0"), e.p("limit", "0")
    tab = e.state("table", e.block.initial_discrete_state(), f"{e.path}: the learned map")

    def locate() -> list[str]:
        return [
            "    uint32_T r;",
            "    uint32_T c;",
            "    real_T fr;",
            "    real_T fc;",
            *[f"    {x}" for x in _locate_c(e.u(0, "0"), lambda j: e.at(rb, j), nr, "linear", "clip", "r", "fr")],
            *[f"    {x}" for x in _locate_c(e.u(1, "0"), lambda j: e.at(cb, j), nc, "linear", "clip", "c", "fc")],
        ]

    def t(i: str, j: str) -> str:
        return f"{tab}[(({i}) * {nc}u) + ({j})]"

    out = [
        "{",
        *locate(),
        f"    {e.y(0, '0')} = ((1.0 - fr) * (1.0 - fc) * {t('r', 'c')}) + ((1.0 - fr) * fc * {t('r', 'c + 1u')})",
        f"        + (fr * (1.0 - fc) * {t('r + 1u', 'c')}) + (fr * fc * {t('r + 1u', 'c + 1u')});",
        "}",
    ]
    upd = [
        f"if ({e.u(3, '0')} >= 0.5) {{",
        *locate(),
        f"    real_T step = {rate} * {c_float(e.period)} * {e.u(2, '0')};",
        "    real_T w[4];",
        "    uint32_T k[4];",
        "    uint32_T q;",
        f"    w[0] = (1.0 - fr) * (1.0 - fc); k[0] = (r * {nc}u) + c;",
        f"    w[1] = (1.0 - fr) * fc; k[1] = (r * {nc}u) + c + 1u;",
        f"    w[2] = fr * (1.0 - fc); k[2] = ((r + 1u) * {nc}u) + c;",
        f"    w[3] = fr * fc; k[3] = ((r + 1u) * {nc}u) + c + 1u;",
        "    for (q = 0u; q < 4u; q++) {",
        f"        {tab}[k[q]] = fmin(fmax({tab}[k[q]] + (step * w[q]), -{lim}), {lim});",
        "    }",
        "}",
    ]
    return BlockCode(out, upd)


# ---------------------------------------------------------------------------- state charts

_C_CALLS = {
    "abs": "fabs",
    "min": "fmin",
    "max": "fmax",
    "sqrt": "sqrt",
    "exp": "exp",
    "log": "log",
    "sin": "sin",
    "cos": "cos",
    "floor": "floor",
    "ceil": "ceil",
}
_C_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
_C_CMPS = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


class _ChartC:
    """Translates the (whitelisted) chart AST into C expressions and statements, with the semantics of Python
    (as the simulation runs them): ``%`` is a floored modulo, ``and``/``or`` give one of their operands."""

    def __init__(self, names: dict[str, str], elapsed: str, period: str, events: list[str] | None = None,
                 max_events: int = 8) -> None:  # fmt: skip
        self.names, self.elapsed, self.period = names, elapsed, period
        """``elapsed``: C expression of the sample periods the state has been active (``after``, ``t_in_state``)."""
        self.events, self.max_events = list(events or []), max_events

    def at(self, elapsed: str) -> _ChartC:
        """The same translator with another state's clock (``after`` and ``t_in_state``)."""
        return _ChartC(self.names, elapsed, self.period, self.events, self.max_events)

    def expr(self, n: ast.AST) -> str:
        if isinstance(n, ast.Constant):
            if not isinstance(n.value, int | float):
                raise CodegenError(f"chart constant {n.value!r} is not a number")
            return c_float(float(n.value))
        if isinstance(n, ast.Name):
            if n.id == "t_in_state":
                return f"({self.elapsed} * {self.period})"
            consts = {
                "pi": "3.141592653589793",
                "e": "2.718281828459045",
                "true": "1.0",
                "True": "1.0",
                "false": "0.0",
                "False": "0.0",
            }
            return self.names.get(n.id, consts.get(n.id, n.id))
        if isinstance(n, ast.BinOp):
            a, b = self.expr(n.left), self.expr(n.right)
            if isinstance(n.op, ast.Pow):
                return f"pow({a}, {b})"
            if isinstance(n.op, ast.Mod):  # floored like Python's: the sign of the divisor
                r = f"fmod({a}, {b})"
                return f"((({r} != 0.0) && ((({b}) < 0.0) != ({r} < 0.0))) ? ({r} + {b}) : {r})"
            return f"({a} {_C_BINOPS[type(n.op)]} {b})"
        if isinstance(n, ast.UnaryOp):
            v = self.expr(n.operand)
            return f"(!{v})" if isinstance(n.op, ast.Not) else f"(-{v})" if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BoolOp):  # Python's: the first operand that decides, as a value (2 or 3 is 2)
            values = [self.expr(v) for v in n.values]
            out = values[-1]
            for v in reversed(values[:-1]):
                out = (
                    f"((({v}) != 0.0) ? {out} : {v})"
                    if isinstance(n.op, ast.And)
                    else f"((({v}) != 0.0) ? {v} : {out})"
                )
            return out
        if isinstance(n, ast.Compare):
            parts, left = [], n.left
            for cmp_op, right in zip(n.ops, n.comparators, strict=True):
                parts.append(f"({self.expr(left)} {_C_CMPS[type(cmp_op)]} {self.expr(right)})")
                left = right
            return "(" + " && ".join(parts) + ")"
        if isinstance(n, ast.IfExp):
            return f"(({self.expr(n.test)}) ? {self.expr(n.body)} : {self.expr(n.orelse)})"
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id == "after":
                return f"({self.elapsed} >= {self.expr(n.args[0])})"
            fn = _C_CALLS[n.func.id]
            args = [self.expr(a) for a in n.args]
            if fn in ("fmin", "fmax") and len(args) > 2:
                out = args[0]
                for a in args[1:]:
                    out = f"{fn}({out}, {a})"
                return out
            return f"{fn}({', '.join(args)})"
        raise CodegenError(f"chart construct not supported in C: {ast.dump(n)}")

    def stmts(self, body: list[ast.stmt]) -> list[str]:
        lines = []
        for st in body:
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):  # send(event)
                arg = st.value.args[0]
                assert isinstance(arg, ast.Name)
                k = self.events.index(arg.id)
                lines.append(f"if (nq < {self.max_events}) {{ q[nq] = {k}; nq++; }} /* send({arg.id}) */")
            elif isinstance(st, ast.Assign):
                target = st.targets[0]
                assert isinstance(target, ast.Name)
                lines.append(f"{self.names[target.id]} = {self.cond_value(st.value)};")
            elif isinstance(st, ast.AugAssign):
                assert isinstance(st.target, ast.Name)
                var = self.names[st.target.id]
                rhs = ast.BinOp(left=ast.Name(st.target.id, ast.Load()), op=st.op, right=st.value)
                lines.append(f"{var} = {self.expr(rhs)};")
        return lines

    def cond_value(self, n: ast.AST) -> str:
        """A boolean expression used as a value becomes 1.0/0.0."""
        return f"(({self.expr(n)}) ? 1.0 : 0.0)" if _is_bool(n) else self.expr(n)


def _is_bool(n: ast.AST) -> bool:
    """A C expression of type int (0/1). ``and``/``or`` are not: they give one of their operands."""
    return isinstance(n, ast.Compare) or (isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not))


@template("charts.state_chart")
def _state_chart(e: Emitter) -> BlockCode:
    """The chart as C: the same algorithm as the simulation (``nervus.blocks.chart._Interpreter``), unrolled over
    the state tree. The state array holds the active flags, the ticks and the history of every state, then the
    variables."""
    from nervus.blocks.chart import MAX_EVENT_PASSES, transition_scope

    blk = e.block
    spec = blk.spec  # type: ignore[attr-defined]
    n = len(spec.states)
    s = e.state("chart", blk.initial_discrete_state(),
                f"[0..{n - 1}] active flags, [{n}..{2 * n - 1}] samples in state, [{2 * n}..{3 * n - 1}] history, "
                "then the variables")  # fmt: skip

    def act(k: int) -> str:
        return f"{s}[{k}]"

    def tck(k: int) -> str:
        return f"{s}[{n + k}]"

    def hst(k: int) -> str:
        return f"{s}[{2 * n + k}]"

    names = {v: e.u(k, "0") for k, v in enumerate(spec.inputs)}
    for v in spec.params:  # calibration parameters
        names[v] = e.named_param(v, blk.param_values[v])  # type: ignore[attr-defined]
    for k, v in enumerate(spec.variables):
        names[v] = f"{s}[{3 * n + k}]"
    for k, v in enumerate(spec.events):
        names[v] = f"((ev == {k}) ? 1.0 : 0.0)"
    cg = _ChartC(names, "0.0", c_float(e.period), spec.events, MAX_EVENT_PASSES)
    states = spec.states

    def clock(k: int) -> _ChartC:
        """State k's clock: no period has passed in the sample that entered it (as ``_Interpreter._clock``)."""
        return cg.at(f"({tck(k)} + (entered[{k}] ? 0.0 : 1.0))")

    def enter_one(k: int) -> list[str]:
        return [f"{act(k)} = 1.0; {tck(k)} = 0.0; entered[{k}] = 1; /* enter {states[k].name} */",
                *cg.at("0.0").stmts(states[k].entry)]  # fmt: skip

    def enter_default(k: int) -> list[str]:
        out = enter_one(k)
        st = states[k]
        if st.parallel:
            for c in st.children:
                out += enter_default(c)
        elif st.composite:
            if st.history:
                branches = [c for c in st.children if c != st.initial]
                out.append(f"{{ int h = (int){hst(k)};")
                for c in branches:
                    out += [f"    if (h == {c}) {{", *[f"        {x}" for x in enter_default(c)], "    } else"]
                out += ["    {", *[f"        {x}" for x in enter_default(st.initial)], "    }", "}"]
            else:
                out += enter_default(st.initial)
        return out

    def exit_state(k: int) -> list[str]:
        st, out = states[k], []
        for c in reversed(st.children):
            body = ([] if st.parallel else [f"{hst(k)} = {c}.0;"]) + exit_state(c)
            out += [f"if ({act(c)} != 0.0) {{", *[f"    {x}" for x in body], "}"]
        return [*out, *clock(k).stmts(st.exit), f"{act(k)} = 0.0; /* exit {st.name} */"]

    def fire(tk: int) -> list[str]:
        t = spec.transitions[tk]
        anc_s, anc_d = spec.ancestors(t.source), spec.ancestors(t.target)
        lca = transition_scope(spec, t)
        leave = anc_s[anc_s.index(lca) - 1] if lca is not None else anc_s[-1]
        out = exit_state(leave) + clock(t.source).stmts(t.action)
        path = anc_d[: anc_d.index(lca)] if lca is not None else anc_d
        for x in reversed(path[1:]):
            out += [f"if ({act(x)} == 0.0) {{", *[f"    {y}" for y in enter_one(x)], "}"]
            if states[x].parallel:
                for c in states[x].children:
                    if c not in path:
                        out += [f"if ({act(c)} == 0.0) {{", *[f"    {y}" for y in enter_default(c)], "}"]
        return out + enter_default(t.target)

    def process(k: int) -> list[str]:
        here = clock(k)
        body: list[str] = []
        outgoing = [tk for tk, t in enumerate(spec.transitions) if t.source == k]
        for m, tk in enumerate(outgoing):
            t = spec.transitions[tk]
            kw = "if" if m == 0 else "} else if"
            body += [f"{kw} ({here.expr(t.guard)}) {{ /* -> {states[t.target].name} */",
                     *[f"    {x}" for x in fire(tk)]]  # fmt: skip
        rest = here.stmts(states[k].during)
        for c in states[k].children:
            rest += process(c)
        if outgoing:
            body += ["} else {", *([f"    {x}" for x in rest] or ["    /* no transition */"]), "}"]
        else:
            body += rest
        return [f"if ((act0[{k}] != 0.0) && ({act(k)} != 0.0)) {{ /* {states[k].name} */",
                *[f"    {x}" for x in body], "}"]  # fmt: skip

    body = [f"for (i = 0; i < {n}; i++) {{ act0[i] = {s}[i]; }}"]
    for k in spec.top:
        body += process(k)
    # The first sample (no state active yet) enters the initial states, with its inputs, before processing.
    none_active = " && ".join(f"({act(k)} == 0.0)" for k in spec.top)
    if len(spec.top) == 1:  # clang: "equality comparison with extraneous parentheses"
        none_active = none_active[1:-1]
    start = [f"if ({none_active}) {{", *[f"    {x}" for x in enter_default(spec.initial)], "}"]
    lines = ["{", f"    real_T act0[{n}];", f"    int entered[{n}];", "    int i;"]
    if spec.events:
        lines += [f"    int q[{MAX_EVENT_PASSES}];", "    int nq = 0;", "    int ev = -1;", "    int pass;"]
    lines += [f"    for (i = 0; i < {n}; i++) {{ entered[i] = 0; }}", *[f"    {x}" for x in start]]
    if spec.events:  # one pass, then one more for each broadcast event
        lines += [
            f"    for (pass = 0; pass <= {MAX_EVENT_PASSES}; pass++) {{",
            *[f"        {x}" for x in body],
            "        if (nq == 0) { break; }",
            "        ev = q[0];",
            "        for (i = 1; i < nq; i++) { q[i - 1] = q[i]; }",
            "        nq--;",
            "    }",
        ]
    else:
        lines += [f"    {x}" for x in body]
    lines += [
        f"    for (i = 0; i < {n}; i++) {{",
        f"        if (({s}[i] != 0.0) && (entered[i] == 0)) {{ {s}[{n} + i] += 1.0; }}",
        "    }",
    ]
    for k, (name, _init) in enumerate(spec.outputs):
        lines.append(f"    {e.y(k, '0')} = {names[name]};")
    if blk.params["state_output"]:
        leaf = _leaf_expr(spec, act)
        lines.append(f"    {e.y(len(spec.outputs), '0')} = {leaf};")
    lines.append("}")
    return BlockCode(lines)


def _leaf_expr(spec: Any, act: Callable[[int], str]) -> str:
    """C expression for the innermost active state (first parallel region), like ``StateChart.active_state``."""

    def leaf(k: int) -> str:
        st = spec.states[k]
        if not st.composite:
            return f"{k}.0"
        expr = f"{k}.0"
        for c in reversed(st.children):
            expr = f"(({act(c)} != 0.0) ? {leaf(c)} : {expr})"
        return expr

    expr = f"{spec.initial}.0"
    for k in reversed(spec.top):
        expr = f"(({act(k)} != 0.0) ? {leaf(k)} : {expr})"
    return expr


# ---------------------------------------------------------------------------- CAN


def _can_raw(sig: Any, byte: Callable[[str], str], var: str = "raw") -> list[str]:
    """C statements assembling the raw bit field of ``sig`` from the frame bytes, run by run (see
    ``CanSignal.segments``); works for both byte orders and any frame length."""
    lines = [f"uint64_T {var} = 0u;"]
    for b, bit, n, pos in sig.segments():
        lines.append(f"{var} |= ((uint64_T)(((uint8_T){byte(str(b))} >> {bit}) & 0x{(1 << n) - 1:X}u)) << {pos};")
    return lines


def _mux_condition(sig: Any, mux: str) -> str:
    return " || ".join(f"{mux} == {k}u" for k in sig.mux_ids)


def _mask(length: int) -> str:
    return "0xFFFFFFFFFFFFFFFFu" if length >= 64 else f"0x{(1 << length) - 1:X}u"


@template("can.unpack")
def _can_unpack(e: Emitter) -> BlockCode:
    b = e.block
    sigs, msg = b._signals, b._msg  # type: ignore[attr-defined]
    held = e.state("held", b.initial_discrete_state(), f"{e.path}: last values of multiplexed signals") if any(
        s.mux_ids is not None for s in sigs
    ) else None  # fmt: skip
    lines = ["{"]
    if held is not None:
        lines += [f"    {x}" for x in _can_raw(msg.multiplexer, lambda ix: e.u(0, ix), "mux")]
    for p, s in enumerate(sigs):
        body = [f"/* {s.name} */", *_can_raw(s, lambda ix: e.u(0, ix))]
        if s.signed:
            body.append(
                f"real_T v = (raw & ((uint64_T)1u << {s.length - 1})) ? "
                f"(real_T)((int64_T)raw - ((int64_T)1 << {s.length})) : (real_T)raw;"
            )
        else:
            body.append("real_T v = (real_T)raw;")
        body.append(f"{e.y(p, '0')} = v * {c_float(s.factor)} + {c_float(s.offset)};")
        if s.mux_ids is not None:
            lines += [f"    if ({_mux_condition(s, 'mux')}) {{", *[f"        {x}" for x in body], "    } else {",
                      f"        {e.y(p, '0')} = {held}[{p}];", "    }"]  # fmt: skip
        else:
            lines += ["    {", *[f"        {x}" for x in body], "    }"]
    lines.append("}")
    upd = [f"{held}[{p}] = {e.y(p, '0')};" for p in range(len(sigs))] if held is not None else []
    return BlockCode(lines, upd)


@template("can.pack")
def _can_pack(e: Emitter) -> BlockCode:
    from nervus.can import raw_value

    b = e.block
    sigs, msg = b._signals, b._msg  # type: ignore[attr-defined]
    inputs = {s.name: p for p, s in enumerate(sigs)}
    n = msg.length
    lines = ["{", f"    uint8_T bytes[{n}];", "    int k;", f"    for (k = 0; k < {n}; k++) {{ bytes[k] = 0u; }}"]

    def raw_of(s: Any) -> list[str]:
        if s.name not in inputs:  # not an input: its initial value
            return [f"uint64_T raw = 0x{raw_value(s, s.initial) & s.mask:X}u;"]
        lo, hi = (-(1 << (s.length - 1)), (1 << (s.length - 1)) - 1) if s.signed else (0, (1 << s.length) - 1)
        return [
            f"real_T r = round(({e.u(inputs[s.name], '0')} - {c_float(s.offset)}) / {c_float(s.factor)});",
            f"if (r > {c_float(float(hi))}) {{ r = {c_float(float(hi))}; }} else if (r < {c_float(float(lo))}) {{ r = {c_float(float(lo))}; }}",
            f"uint64_T raw = ((uint64_T)(int64_T)r) & {_mask(s.length)};",
        ]

    mux = msg.multiplexer
    if mux is not None:
        lines += ["    uint64_T mux;", "    {", *[f"        {x}" for x in raw_of(mux)], "        mux = raw;", "    }"]
    for s in msg.signals:
        body = [f"/* {s.name} */", *raw_of(s)]
        for byte, bit, nb, pos in s.segments():
            body.append(f"bytes[{byte}] |= (uint8_T)(((raw >> {pos}) & 0x{(1 << nb) - 1:X}u) << {bit});")
        cond = _mux_condition(s, "mux") if s.mux_ids is not None else "1"
        lines += [f"    if ({cond}) {{", *[f"        {x}" for x in body], "    }"]
    lines += [f"    for (k = 0; k < {n}; k++) {{", f"        {e.y(0, 'k')} = (real_T)bytes[k];", "    }", "}"]
    return BlockCode(lines)


__all__ = ["as_signal"]


@template("diagnostics.monitor")
def _diagnostic_monitor(e: Emitter) -> BlockCode:
    b = e.block
    x = e.state("dem", b.initial_discrete_state(), f"{e.path}: DTC {b.params['dtc']} (counter, status, cycles)")
    threshold, fail_step, pass_step = (e.p(n, "0") for n in ("threshold", "fail_step", "pass_step"))
    confirm, heal = e.p("confirm_cycles", "0"), e.p("heal_cycles", "0")
    ports = b.input_names()

    def inp(name: str, default: str) -> str:
        return f"(({e.u(ports.index(name), '0')} >= 0.5) ? 1 : 0)" if name in ports else default

    status = f"((uint32_T){x}[1])"
    out = [
        f"{e.y(0, '0')} = ({status} & 0x80u) ? 1.0 : 0.0;  /* MIL */",
        f"{e.y(1, '0')} = ({status} & 0x04u) ? 1.0 : 0.0;  /* pending */",
        f"{e.y(2, '0')} = ({status} & 0x08u) ? 1.0 : 0.0;  /* confirmed */",
        f"{e.y(3, '0')} = ({status} & 0x10u) ? 0.0 : 1.0;  /* ready: completed since clear */",
        f"{e.y(4, '0')} = {x}[1];",
    ]
    upd = [
        "{",
        f"    real_T c = {x}[0];",
        f"    uint32_T st = (uint32_T){x}[1];",
        f"    real_T fc = {x}[2];",
        f"    real_T pc = {x}[3];",
        f"    int fault = {inp('fault', '0')};",
        f"    int enable = {inp('enable', '1')};",
        f"    int cycle = {inp('cycle', '0')};",
        f"    int clear = {inp('clear', '0')};",
        f"    if ((clear != 0) && ({x}[5] < 0.5)) {{  /* mode 04: clear the code and the readiness */",
        "        c = 0.0; st = 0x50u; fc = 0.0; pc = 0.0;",
        f"    }} else if ((cycle != 0) && ({x}[4] < 0.5)) {{  /* a new operation cycle */",
        "        if ((st & 0x02u) != 0u) {",
        "            pc = 0.0;",
        "        } else if ((st & 0x40u) == 0u) {  /* the last cycle completed and passed */",
        "            fc = 0.0;",
        "            pc += 1.0;",
        "            st &= ~0x04u;",
        f"            if (pc >= {heal}) {{ st &= ~0x80u; }}",
        "        } else {",
        "            /* the last cycle did not complete: no change */",
        "        }",
        "        st = (st & ~0x03u) | 0x40u;",
        "        c = 0.0;",
        "    } else {",
        "        /* the same operation cycle */",
        "    }",
        "    if (enable != 0) {",
        "        if (fault != 0) {",
        *(["            if (c < 0.0) { c = 0.0; }  /* jump down */"] if b.params["jump_down"] else []),
        f"            c = fmin(c + {fail_step}, {threshold});",
        "        } else {",
        f"            c = fmax(c - {pass_step}, -{threshold});",
        "        }",
        f"        if ((c >= {threshold}) && ((st & 0x02u) == 0u)) {{  /* failed, first time this cycle */",
        "            fc += 1.0;",
        "            st |= 0x27u;",
        "            st &= ~0x50u;",
        f"            if (fc >= {confirm}) {{",
        "                st |= 0x08u;",
        *(["                st |= 0x80u;", "                pc = 0.0;"] if b.params["mil"] else []),
        "            }",
        f"        }} else if (c >= {threshold}) {{",
        "            st |= 0x01u;",
        f"        }} else if (c <= -{threshold}) {{  /* passed */",
        "            st &= ~0x51u;",
        "        } else {",
        "            /* not decided yet */",
        "        }",
        "    }",
        f"    {x}[0] = c;",
        f"    {x}[1] = (real_T)st;",
        f"    {x}[2] = fc;",
        f"    {x}[3] = pc;",
        f"    {x}[4] = (cycle != 0) ? 1.0 : 0.0;",
        f"    {x}[5] = (clear != 0) ? 1.0 : 0.0;",
        "}",
    ]
    return BlockCode(out, upd)


def _e2e_crc_lines(byte: Callable[[str], str], data_id: int, crc_byte: int, width: int = 8) -> list[str]:
    """C statements computing ``crc`` (E2E CRC-8 SAE J1850) of the frame bytes ``byte("k")`` (real_T 0..255)."""
    return [
        "uint8_T crc = 0xFFu;",
        "int k;",
        "int j;",
        f"uint8_T head[2] = {{0x{data_id & 0xFF:02X}u, 0x{(data_id >> 8) & 0xFF:02X}u}};",
        f"for (k = -2; k < {width}; k++) {{",
        f"    if (k == {crc_byte}) {{ continue; }}",
        f"    crc ^= (k < 0) ? head[k + 2] : (uint8_T){byte('k')};",
        "    for (j = 0; j < 8; j++) {",
        "        crc = (crc & 0x80u) ? (uint8_T)((crc << 1) ^ 0x1Du) : (uint8_T)(crc << 1);",
        "    }",
        "}",
        "crc ^= 0xFFu;",
    ]


@template("can.e2e_protect")
def _e2e_protect(e: Emitter) -> BlockCode:
    b = e.block
    cb, kb, did = int(b.params["counter_byte"]), int(b.params["crc_byte"]), int(b.params["data_id"])
    x = e.state("counter", [0.0], f"{e.path}: E2E alive counter 0..14")
    out = [
        "{",
        "    int i;",
        f"    for (i = 0; i < {e.out_widths[0]}; i++) {{",
        f"        {e.y(0, 'i')} = {e.u(0, 'i')};",
        "    }",
        f"    {e.y(0, str(cb))} = (real_T)(((uint8_T){e.u(0, str(cb))} & 0xF0u) | (uint8_T){x}[0]);",
        "    {",
        *[f"        {line}" for line in _e2e_crc_lines(lambda ix: e.y(0, ix), did, kb, e.out_widths[0])],
        f"        {e.y(0, str(kb))} = (real_T)crc;",
        "    }",
        "}",
    ]
    upd = [f"{x}[0] = ({x}[0] >= 14.0) ? 0.0 : {x}[0] + 1.0;"]
    return BlockCode(out, upd)


@template("can.e2e_check")
def _e2e_check(e: Emitter) -> BlockCode:
    b = e.block
    cb, kb, did = int(b.params["counter_byte"]), int(b.params["crc_byte"]), int(b.params["data_id"])
    x = e.state("e2e", [-1.0, 0.0], f"{e.path}: last accepted counter (-1: none), counter of this sample")
    status_lines = [
        "{",
        "    real_T status;",
        f"    {x}[1] = -1.0;",
        f"    if ({e.u(1, '0')} < 0.5) {{",
        "        status = 1.0; /* no new data */",
        "    } else {",
        *[f"        {line}" for line in _e2e_crc_lines(lambda ix: e.u(0, ix), did, kb, e.in_widths[0])],
        f"        if ((uint8_T){e.u(0, str(kb))} != crc) {{",
        "            status = 2.0; /* wrong CRC */",
        "        } else {",
        f"            int counter = (int)((uint8_T){e.u(0, str(cb))} & 0x0Fu);",
        f"            {x}[1] = (real_T)counter;",
        f"            if ({x}[0] < 0.0) {{",
        "                status = 0.0;",
        "            } else {",
        f"                int delta = ((counter - (int){x}[0]) % 15 + 15) % 15;",
        f"                status = (delta == 0) ? 3.0 : ((delta <= {int(b.params['max_delta'])}) ? 0.0 : 4.0);",
        "            }",
        "        }",
        "    }",
        f"    {e.y(0, '0')} = status;",
        f"    {e.y(1, '0')} = (status == 0.0) ? 1.0 : 0.0;",
        "}",
    ]
    upd = [f"if ({x}[1] >= 0.0) {{ {x}[0] = {x}[1]; }}"]
    return BlockCode(status_lines, upd)


@template("codegen.function_call")
def _function_call(e: Emitter) -> BlockCode:
    """A reusable subsystem function (see :mod:`nervus.codegen.functions`): this instance's data, and the calls."""
    from nervus.codegen.functions import FunctionCall, instance_name

    b = e.block
    assert isinstance(b, FunctionCall)
    f, inst = b.function, instance_name(e.gen, b.instance)
    gen = e.gen
    if f"{f}.h" not in gen.extra_includes:
        gen.extra_includes.append(f"{f}.h")
        gen.extra_files.update(b.files)
    gen.extra_globals += [
        f"/* {e.path}: an instance of {f}() */",
        f"static {f}_B_t {inst}_B;",
        f"static {f}_DW_t {inst}_DW;",
        f"{f}_P_t {inst}_P = {{",
        b.initializer,
        "};",
        "",
    ]
    gen.extra_header.append(f"extern {f}_P_t {inst}_P; /* {e.path}: calibration of this instance of {f}() */")
    gen.extra_init.append(f"{f}_initialize(&{inst}_B, &{inst}_DW);")
    gen.extra_characteristics.append((f"{inst}_P", b.fields))  # this instance's calibration, in the A2L file
    out = ["{", f"    {f}_U_t in;", f"    {f}_Y_t out;"]
    for port, (field_name, width) in enumerate(b.code.inputs):
        out += [f"    in.{field_name}[{k}] = {e.u(port, str(k))};" for k in range(width)]
    out.append(f"    {f}_output(&in, &out, &{inst}_B, &{inst}_DW, &{inst}_P);")
    for port, (field_name, width) in enumerate(b.code.outputs):
        out += [f"    {e.y(port, str(k))} = (real_T)out.{field_name}[{k}];" for k in range(width)]
    out.append("}")
    return BlockCode(out, [f"{f}_update(&{inst}_B, &{inst}_DW, &{inst}_P);"])
