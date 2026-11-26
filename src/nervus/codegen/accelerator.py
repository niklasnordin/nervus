"""Accelerated simulation: the whole model as compiled C, run by a fixed-step solver loop in C.

    simulate(model, mode="accelerated")    # or SimulationSettings(mode="accelerated")

Nervus generates C for every block (the ECU templates, plus the continuous blocks, the time sources and Python
Function blocks translated by :mod:`nervus.codegen.pyfunc`), compiles it once (cached by the code) and runs the
simulation loop in C, doing exactly what the Python fixed-step loop does: outputs at every step (discrete
blocks at their sample hits), integrator resets, logging, discrete updates, then one Euler or RK4 step of the
continuous states. Stop Simulation and Assertion blocks are checked on the logged signals afterwards.

A model with a variable-step solver runs with fixed-step RK4 (the step is *max_step*, else a thousandth of the
time span on the grid of the discrete rates). Models with blocks that have no C (physical networks, FMUs, CAN
buses, ...), algebraic loops, or enabled subsystems with continuous blocks cannot be accelerated:
:func:`accelerate_problems` names them, and :func:`nervus.simulate` then simulates normally and says why.
"""

from __future__ import annotations

import ctypes
import hashlib
import math
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from nervus.blocks.ports import Inport, Outport, PortProxy
from nervus.codegen.generator import (
    TEMPLATES,
    BlockCode,
    CGenerator,
    CodegenError,
    Emitter,
    Template,
    _pass_through,
    c_float,
    c_identifier,
)
from nervus.core.types import Array
from nervus.sim.compiler import CompiledModel, Kind

T = "acc_t"
"""The C expression of the simulation time in accelerated code."""


@dataclass
class AccCode(BlockCode):
    derivatives: list[str] = field(default_factory=list)
    """Statements writing the block's continuous state derivatives (``acc_DX``)."""
    major: list[str] = field(default_factory=list)
    """Statements run at a major step that may change the continuous state (an integrator reset); they set
    ``acc_jumped`` when they do."""


ACC_TEMPLATES: dict[str, Template] = {}


def acc_template(*type_ids: str) -> Callable[[Template], Template]:
    def register(fn: Template) -> Template:
        for t in type_ids:
            ACC_TEMPLATES[t] = fn
        return fn

    return register


def _x(e: Emitter, k: int | str) -> str:
    """Element k of the block's continuous state."""
    return f"acc_X[{e.gen.model.xc_slices[e.i].start}u + {k}]" if isinstance(k, str) else (
        f"acc_X[{e.gen.model.xc_slices[e.i].start + k}u]")  # fmt: skip


def _dx(e: Emitter, k: int | str) -> str:
    return _x(e, k).replace("acc_X", "acc_DX", 1)


def _literal(values: Any) -> list[str]:
    return [c_float(float(v)) for v in np.asarray(values, dtype=float).ravel()]


def _table(e: Emitter, name: str, values: Any) -> str:
    """A static constant table (time points and data) in the generated code."""
    var = f"acc_{e.cname}_{name}"
    vals = _literal(values)
    e.gen.extra_globals.append(f"static const real_T {var}[{max(1, len(vals))}] = {{{', '.join(vals) or '0.0'}}};")
    return var


# ---------------------------------------------------------------------------------------------------- sources


@acc_template("ports.inport")
def _inport(e: Emitter) -> BlockCode:
    if e.gen.model.origin[e.i].diagram is not e.gen.model.diagram:
        return TEMPLATES["ports.inport"](e)  # inside a subsystem: never reached (ports are proxies)
    sig = getattr(e.block, "signal", None)
    if sig is None:
        return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = {e.p('value', ix)};"]))
    return _lookup_table(e, np.asarray(sig.time), np.asarray(sig.values), sig.interpolation == "hold")


def _lookup_table(e: Emitter, time: Array, data: Array, hold: bool) -> BlockCode:
    n, cols = data.shape
    tt, dd = _table(e, "time", time), _table(e, "data", data)
    lines = [f"{e.y(0, str(j))} = acc_interp({tt}, &{dd}[{j}], {n}u, {cols}u, {T}, {int(hold)});" for j in range(cols)]
    return BlockCode(lines)


@acc_template("sources.from_workspace")
def _from_workspace(e: Emitter) -> BlockCode:
    time, data = e.block._table()  # type: ignore[attr-defined]
    return _lookup_table(e, time, data, e.block.params["interpolation"] == "hold")


@acc_template("sources.step")
def _step(e: Emitter) -> BlockCode:
    at = e.p("time", "0")
    return BlockCode(e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = ({T} >= {at}) ? {e.p('final', ix)} : "
                                                f"{e.p('initial', ix)};"]))  # fmt: skip


@acc_template("sources.ramp")
def _ramp(e: Emitter) -> BlockCode:
    a, s, t0 = e.p("initial", "0"), e.p("slope", "0"), e.p("start_time", "0")
    return BlockCode([f"{e.y(0, '0')} = {a} + ({s} * fmax(0.0, {T} - {t0}));"])


@acc_template("sources.sine")
def _sine(e: Emitter) -> BlockCode:
    a, f, ph, b = (e.p(n, "0") for n in ("amplitude", "frequency", "phase", "bias"))
    return BlockCode([f"{e.y(0, '0')} = ({a} * sin(({f} * {T}) + {ph})) + {b};"])


@acc_template("sources.pulse")
def _pulse(e: Emitter) -> BlockCode:
    a, per, duty, d = (e.p(n, "0") for n in ("amplitude", "period", "duty", "delay"))
    y = e.y(0, "0")
    return BlockCode([
        f"if ({T} < {d}) {{ {y} = 0.0; }} else {{",
        f"    real_T k = floor((({T} - {d}) / {per}) + 1e-12);",
        f"    real_T phase = fmax(0.0, ({T} - {d}) - (k * {per}));",
        f"    {y} = (phase < (({duty} / 100.0) * {per})) ? {a} : 0.0;",
        "}",
    ])  # fmt: skip


@acc_template("sources.clock")
def _clock(e: Emitter) -> BlockCode:
    return BlockCode([f"{e.y(0, '0')} = {T};"])


@acc_template("sources.drive_cycle")
def _drive_cycle(e: Emitter) -> BlockCode:
    times, v = e.block._profile()  # type: ignore[attr-defined]
    n = times.size
    tt, vv = _table(e, "time", times), _table(e, "speed", v)
    scale = "3.6" if e.block.params["unit"] == "km/h" else "1.0"
    return BlockCode([
        "{",
        f"    real_T speed = ({T} > {tt}[{n - 1}u]) ? 0.0 : acc_interp({tt}, {vv}, {n}u, 1u, {T}, 0);",
        f"    uint32_T k = acc_index({tt}, {n}u, {T});",
        f"    if (k > {n - 2}u) {{ k = {n - 2}u; }}",
        f"    {e.y(0, '0')} = (speed / 3.6) * {scale};",
        f"    {e.y(1, '0')} = ({T} <= {tt}[{n - 1}u]) ? ((({vv}[k + 1u] - {vv}[k]) / 3.6) / ({tt}[k + 1u] - {tt}[k])) "
        ": 0.0;",
        "}",
    ])  # fmt: skip


# ---------------------------------------------------------------------------------------------------- continuous


@acc_template("continuous.integrator")
def _integrator(e: Emitter) -> AccCode:
    b = e.block
    w = e.w()
    lo, hi = float(b.params["lower"]), float(b.params["upper"])
    limited = math.isfinite(lo) or math.isfinite(hi)
    reset = str(b.params.get("external_reset", "none"))
    ext = b.params.get("initial_source", "internal") == "external"
    n_in = b.num_inputs()
    clo, chi = c_float(lo), c_float(hi)
    x0_internal = _literal(np.clip(np.broadcast_to(np.asarray(b.params["initial"], float).ravel(), (w,)), lo, hi))
    start = f"({T} <= (acc_t0 + (1e-12 * fmax(1.0, fabs(acc_t0)))))"

    def x0(ix: str) -> str:
        if ext:
            return f"fmin(fmax({e.u(n_in - 1, ix)}, {clo}), {chi})"
        return x0_internal[int(ix)] if ix != "i" else f"acc_{e.cname}_x0[i]"

    if not ext and w > 1:
        e.gen.extra_globals.append(f"static const real_T acc_{e.cname}_x0[{w}] = {{{', '.join(x0_internal)}}};")

    def out(ix: str) -> list[str]:
        x = f"({start} ? {x0(ix)} : {_x(e, ix)})" if ext else _x(e, ix)
        y = f"fmin(fmax({x}, {clo}), {chi})" if limited else x
        lines = [f"{e.y(0, ix)} = {y};"]
        if b.params.get("saturation_port"):
            lines.append(f"{e.y(1, ix)} = ({e.y(0, ix)} >= {chi}) ? 1.0 : (({e.y(0, ix)} <= {clo}) ? -1.0 : 0.0);")
        return lines

    def der(ix: str) -> list[str]:
        lines = [f"real_T du = {e.u(0, ix)};"]
        if reset == "level":
            lines.append(f"if ({e.u(1, '0')} != 0.0) {{ du = 0.0; }}")
        if limited:
            lines += [f"if (({_x(e, ix)} >= {chi}) && (du > 0.0)) {{ du = 0.0; }}",
                      f"if (({_x(e, ix)} <= {clo}) && (du < 0.0)) {{ du = 0.0; }}"]  # fmt: skip
        return ["{", *[f"    {x}" for x in lines], f"    {_dx(e, ix)} = du;", "}"]

    derivs = e.each(w, der)
    if reset != "none":
        derivs.append(f"{_dx(e, w)} = 0.0;")

    def jump(ix: str) -> list[str]:
        return [f"{{ real_T nv = {x0(ix)}; if (nv != {_x(e, ix)}) {{ {_x(e, ix)} = nv; acc_jumped = 1u; }} }}"]

    major: list[str] = []
    if reset != "none":
        r = e.u(1, "0")
        fire = {"rising": "rising", "falling": "falling", "either": "(rising || falling)", "level": f"({r} != 0.0)"}
        major += [
            "{",
            f"    real_T r = {r};",
            f"    real_T prev = {_x(e, w)};",
            "    int rising = (prev <= 0.0) && (0.0 < r);",
            "    int falling = (prev > 0.0) && (0.0 >= r);",
            f"    int fire = {fire[reset]};",
            "    (void)rising; (void)falling;",
            "    if (fire || (r != prev)) {",
            f"        if (r != prev) {{ {_x(e, w)} = r; acc_jumped = 1u; }}",
            "        if (fire) {",
            *[f"            {x}" for x in e.each(w, jump)],
            "        }",
            "    }",
            "}",
        ]
    if ext:  # start from the x0 input (this replaces the reset logic at the start, as in Python)
        first = [*e.each(w, jump)]
        if reset != "none":
            first.append(f"{{ real_T r = {e.u(1, '0')}; if (r != {_x(e, w)}) {{ {_x(e, w)} = r; acc_jumped = 1u; }} }}")
        major = [f"if ({start}) {{", *[f"    {x}" for x in first], "} else {", *[f"    {x}" for x in major], "}"]
    return AccCode(e.each(w, out), [], derivs, major)


@acc_template("continuous.derivative")
def _derivative(e: Emitter) -> AccCode:
    tau = e.p("time_constant", "0")
    out = e.each(e.w(), lambda ix: [f"{e.y(0, ix)} = ({e.u(0, ix)} - {_x(e, ix)}) / {tau};"])
    der = e.each(e.w(), lambda ix: [f"{_dx(e, ix)} = ({e.u(0, ix)} - {_x(e, ix)}) / {tau};"])
    return AccCode(out, [], der)


@acc_template("continuous.transfer_function", "continuous.state_space")
def _state_space(e: Emitter) -> AccCode:
    a, bm, c, d = e.block._abcd  # type: ignore[attr-defined]
    n, m, p = a.shape[0], bm.shape[1], c.shape[0]

    def u(k: int) -> str:
        return e.u(0, str(k)) if e.in_widths[0] > 1 else e.u(0, "0")

    out = []
    for r in range(p):
        terms = [f"({c_float(c[r, j])} * {_x(e, j)})" for j in range(n)] + [f"({c_float(d[r, k])} * {u(k)})"
                                                                          for k in range(m)]  # fmt: skip
        out.append(f"{e.y(0, str(r))} = {' + '.join(terms)};")
    der = []
    for i in range(n):
        terms = [f"({c_float(a[i, j])} * {_x(e, j)})" for j in range(n)] + [f"({c_float(bm[i, k])} * {u(k)})"
                                                                           for k in range(m)]  # fmt: skip
        der.append(f"{_dx(e, i)} = {' + '.join(terms)};")
    return AccCode(out, [], der)


@acc_template("control.pid")
def _pid(e: Emitter) -> AccCode:
    if e.w() != 1:
        raise CodegenError(f"{e.path}: accelerated simulation supports scalar PID controllers only", (e.path,))
    kp, ki, kd, kn = (e.p(n, "0") for n in ("P", "I", "D", "N"))
    lo, hi, kb = e.p("lower", "0"), e.p("upper", "0"), e.p("Kb", "0")
    aw = e.block.params["anti_windup"]
    law = [
        f"real_T err = {e.u(0, '0')};",
        f"real_T d_term = ({kd} * {kn}) * (err - {_x(e, 1)});",
        f"real_T unsat = (({kp} * err) + {_x(e, 0)}) + d_term;",
        f"real_T y = fmin(fmax(unsat, {lo}), {hi});",
    ]
    di = [f"real_T di = {ki} * err;"]
    if aw == "clamping":
        di.append("if ((unsat != y) && (((unsat - y) > 0.0) == (di > 0.0)) && (di != 0.0)) { di = 0.0; }")
    elif aw == "back_calculation":
        di.append(f"di += {kb} * (y - unsat);")
    else:
        di.append("(void)y;")
    out = ["{", *[f"    {x}" for x in law], f"    {e.y(0, '0')} = y;", "}"]
    der = [
        "{",
        *[f"    {x}" for x in law + di],
        f"    {_dx(e, 0)} = di;",
        f"    {_dx(e, 1)} = ({kd} != 0.0) ? ({kn} * (err - {_x(e, 1)})) : 0.0;",
        "}",
    ]
    return AccCode(out, [], der)


@acc_template("user.python_function")
def _python_function(e: Emitter) -> BlockCode:
    from nervus.codegen.pyfunc import PyFuncError, translate

    try:
        return BlockCode(translate(e, [e.y(k, "0") for k in range(e.block.num_outputs())], T))
    except PyFuncError as exc:
        raise CodegenError(str(exc), (e.path,)) from exc


# ---------------------------------------------------------------------------------------------------- generator

_RUNTIME = """\
static uint32_T acc_index(const real_T *x, uint32_T n, real_T t) /* the last k with x[k] <= t, at least 0 */
{
    uint32_T lo = 0u;
    uint32_T hi = n;
    while (lo < hi) {
        uint32_T mid = (lo + hi) / 2u;
        if (x[mid] <= t) { lo = mid + 1u; } else { hi = mid; }
    }
    return (lo == 0u) ? 0u : (lo - 1u);
}

/* np.interp (hold = 0) or the value of the last point at or before t (hold = 1); y has a stride */
static real_T acc_interp(const real_T *x, const real_T *y, uint32_T n, uint32_T stride, real_T t, int hold)
{
    uint32_T k;
    if (hold != 0) { return y[acc_index(x, n, t) * stride]; }
    if ((n == 1u) || (t <= x[0])) { return y[0]; }
    if (t >= x[n - 1u]) { return y[(n - 1u) * stride]; }
    k = acc_index(x, n, t);
    {
        real_T slope = (y[(k + 1u) * stride] - y[k * stride]) / (x[k + 1u] - x[k]);
        return (slope * (t - x[k])) + y[k * stride];
    }
}
"""


class AccGenerator(CGenerator):
    """The C generator with continuous states allowed (for accelerated simulation, not for ECUs)."""

    def __init__(self, model: CompiledModel, name: str = "nervus_acc") -> None:
        super().__init__(model, name)
        self.extra_globals += [
            f"static real_T acc_X[{max(1, model.n_continuous_states)}];",
            f"static real_T acc_DX[{max(1, model.n_continuous_states)}];",
            "static real_T acc_t;",
            "static real_T acc_t0;",
            "static uint8_T acc_jumped;",
            _RUNTIME,
            __import__("nervus.codegen.pyfunc", fromlist=["HELPERS"]).HELPERS,
        ]

    def problems(self) -> list[str]:
        m = self.model
        out = []
        for i, b in enumerate(m.blocks):
            if isinstance(b, PortProxy):
                continue
            continuous = m.xc_slices[i].stop > m.xc_slices[i].start
            if b.type_id not in ACC_TEMPLATES and (continuous or b.type_id not in TEMPLATES):
                out.append(f"{m.names[i]}: {b.label or b.type_id or type(b).__name__} has no C code")
            elif (m.enable[i] or m.trigger[i]) and m.kinds[i] is Kind.CONTINUOUS:
                out.append(f"{m.names[i]}: continuous blocks in enabled or triggered subsystems")
        for members, _tears in m.loops:
            out.append(f"{m.names[members[0]]}: in an algebraic loop")
        return out

    def generate_acc(self, step: float) -> str:
        """The C source of the accelerated model: the model's code plus ``nervus_acc_run``."""
        problems = self.problems()
        if problems:
            raise CodegenError("cannot accelerate:\n  - " + "\n  - ".join(problems))
        m = self.model
        steps = [(max(1, round(r.period / step)), round(r.offset / step)) for r in m.rates] or [(1, 0)]
        codes: list[BlockCode] = []
        for i, b in enumerate(m.blocks):
            if isinstance(b, PortProxy):
                codes.append(_pass_through(Emitter(self, i)))
                continue
            before = set(self.states)
            fn = ACC_TEMPLATES.get(b.type_id) or TEMPLATES[b.type_id]
            codes.append(fn(Emitter(self, i)))
            code = codes[-1]
            if (m.enable[i] or m.trigger[i]) and m.kinds[i] is not Kind.CONSTANT:
                self._gate(i, code, [k for k in self.states if k not in before])
            if code.outputs:
                rounding, overflow = b.quantization()
                for p, dtype in enumerate(m.out_types[i]):
                    if not dtype.is_double:
                        fq = self.quantizer(dtype, rounding, overflow)
                        var, w = self.signal_var(i, p), m.out_widths[i][p]
                        code.outputs.append(f"for (i = 0u; i < {w}u; i++) {{ {var}[i] = {fq}({var}[i]); }}")
        ins = [i for i, b in enumerate(m.blocks) if isinstance(b, Inport)]
        outs = [i for i, b in enumerate(m.blocks) if isinstance(b, Outport)]
        in_fields = [(c_identifier(m.names[i]), m.out_widths[i][0]) for i in ins]
        out_fields = [(c_identifier(m.names[i]), m.in_widths[i][0]) for i in outs]
        self.io_types = {c_identifier(m.names[i]): m.out_types[i][0] for i in ins}
        for i in outs:
            s, p = m.inputs[i][0]
            self.io_types[c_identifier(m.names[i])] = m.out_types[s][p]
        self.task_plan = None
        header = self._header(in_fields, out_fields, step, steps)
        source = self._source(codes, steps, step).replace(f'#include "{self.prefix}.h"', header, 1)
        return source + "\n" + self._driver(codes)

    def _driver(self, codes: list[BlockCode]) -> str:
        m = self.model
        outs: list[str] = []
        derivs: list[str] = []
        major: list[str] = []
        upd: list[str] = []
        for i, code in enumerate(codes):
            kind, rate = m.kinds[i], m.rate_index[i]
            label = f"/* {m.names[i]} */"
            if code.outputs and kind is not Kind.CONSTANT:
                if kind is Kind.DISCRETE:
                    outs += [label, f"if ((init != 0) || ((major != 0) && (acc_hit[{rate}] != 0u))) {{",
                             *[f"    {x}" for x in code.outputs], "}"]  # fmt: skip
                else:
                    outs += [label, *code.outputs]
            if isinstance(code, AccCode):
                derivs += [label, *code.derivatives]
                major += [label, *code.major]
            if code.update:
                guard = f"acc_hit[{rate}] != 0u" if kind is Kind.DISCRETE else "1"
                upd += [label, f"if ({guard}) {{", *[f"    {x}" for x in code.update], "}"]
        widths = [(self.signal_var(i, p), w) for i, ws in enumerate(m.out_widths) for p, w in enumerate(ws)]
        n_log = sum(w for _v, w in widths)
        log = []
        offset = 0
        for var, w in widths:
            log.append(f"for (i = 0u; i < {w}u; i++) {{ row[{offset}u + i] = {var}[i]; }}")
            offset += w
        nx = m.n_continuous_states
        nr = max(1, len(m.rates))
        rates = [(r.period, r.offset) for r in m.rates] or [(1.0, 0.0)]

        def body(lines: list[str]) -> list[str]:
            return [f"    {x}" for x in lines]

        return "\n".join([
            f"static uint8_T acc_hit[{nr}];",
            f"static const real_T acc_period[{nr}] = {{{', '.join(c_float(p) for p, _o in rates)}}};",
            f"static const real_T acc_offset[{nr}] = {{{', '.join(c_float(o) for _p, o in rates)}}};",
            "",
            "static void acc_outputs(int major, int init)",
            "{",
            "    uint32_T i;",
            "    (void)i; (void)major; (void)init;",
            *body(outs),
            "}",
            "",
            "static void acc_derivatives(real_T t, const real_T *x, real_T *dx)",
            "{",
            "    uint32_T i;",
            f"    for (i = 0u; i < {nx}u; i++) {{ acc_X[i] = x[i]; }}",
            "    acc_t = t;",
            "    acc_outputs(0, 0);",
            *body(derivs),
            f"    for (i = 0u; i < {nx}u; i++) {{ dx[i] = acc_DX[i]; }}",
            "}",
            "",
            "static void acc_major(void)",
            "{",
            "    uint32_T i;",
            "    (void)i;",
            "    acc_jumped = 0u;",
            *body(major),
            "}",
            "",
            "static void acc_update(void)",
            "{",
            "    uint32_T i;",
            "    (void)i;",
            *body(upd),
            "}",
            "",
            "static void acc_log(real_T *row)",
            "{",
            "    uint32_T i;",
            "    (void)i;",
            *body(log),
            "}",
            "",
            f"int nervus_acc_width(void) {{ return {n_log}; }}",
            "",
            "/* The fixed-step simulation loop of nervus.sim.simulator._run_fixed. method: 0 Euler, 1 RK4. */",
            "int nervus_acc_run(double t0, double tf, double h, int n_steps, int method, const double *x0,",
            "                   double *t_log, double *log)",
            "{",
            "    int k;",
            "    uint32_T i, r;",
            f"    real_T x[{max(1, nx)}], k1[{max(1, nx)}], k2[{max(1, nx)}], k3[{max(1, nx)}], k4[{max(1, nx)}],"
            f" xs[{max(1, nx)}];",
            f"    long long count[{nr}];",
            f"    {self.prefix}_initialize();",
            f"    for (i = 0u; i < {nx}u; i++) {{ x[i] = x0[i]; acc_X[i] = x0[i]; }}",
            "    acc_t0 = t0;",
            f"    for (r = 0u; r < {nr}u; r++) {{ count[r] = (long long)ceil(((t0 - acc_offset[r]) / acc_period[r]) - 1e-9); }}",
            "    for (k = 0; k <= n_steps; k++) {",
            "        real_T t = fmin(t0 + ((real_T)k * h), tf);",
            "        real_T tn, hh;",
            "        acc_t = t;",
            f"        for (i = 0u; i < {nx}u; i++) {{ acc_X[i] = x[i]; }}",
            f"        for (r = 0u; r < {nr}u; r++) {{",
            "            real_T next = acc_offset[r] + ((real_T)count[r] * acc_period[r]);",
            "            acc_hit[r] = (fabs(next - t) <= (1e-9 * fmax(1.0, fabs(t)))) ? 1u : 0u;",
            "        }",
            *([] if m.rates else ["        acc_hit[0] = 0u;"]),
            "        acc_outputs(1, (k == 0) ? 1 : 0);",
            "        acc_major();",
            "        if (acc_jumped != 0u) {",
            f"            for (i = 0u; i < {nx}u; i++) {{ x[i] = acc_X[i]; }}",
            "            acc_outputs(0, 0);",
            "        }",
            "        t_log[k] = t;",
            f"        acc_log(&log[(long long)k * {n_log}]);",
            "        if (k == n_steps) { break; }",
            "        acc_update();",
            f"        for (r = 0u; r < {nr}u; r++) {{ if (acc_hit[r] != 0u) {{ count[r]++; }} }}",
            "        tn = fmin(t0 + ((real_T)(k + 1) * h), tf);",
            "        hh = tn - t;",
            f"        if ({nx} > 0) {{",
            "            if (method == 0) {",
            "                acc_derivatives(t, x, k1);",
            f"                for (i = 0u; i < {nx}u; i++) {{ x[i] = x[i] + (hh * k1[i]); }}",
            "            } else {",
            "                acc_derivatives(t, x, k1);",
            f"                for (i = 0u; i < {nx}u; i++) {{ xs[i] = x[i] + ((hh / 2.0) * k1[i]); }}",
            "                acc_derivatives(t + (hh / 2.0), xs, k2);",
            f"                for (i = 0u; i < {nx}u; i++) {{ xs[i] = x[i] + ((hh / 2.0) * k2[i]); }}",
            "                acc_derivatives(t + (hh / 2.0), xs, k3);",
            f"                for (i = 0u; i < {nx}u; i++) {{ xs[i] = x[i] + (hh * k3[i]); }}",
            "                acc_derivatives(t + hh, xs, k4);",
            f"                for (i = 0u; i < {nx}u; i++) {{",
            "                    x[i] = x[i] + ((hh / 6.0) * (((k1[i] + (2.0 * k2[i])) + (2.0 * k3[i])) + k4[i]));",
            "                }",
            "            }",
            "        }",
            "    }",
            "    return n_steps + 1;",
            "}",
            "",
        ])  # fmt: skip


# ---------------------------------------------------------------------------------------------------- running


def accelerate_problems(model: CompiledModel) -> list[str]:
    """Why a model cannot be accelerated (empty: it can)."""
    return AccGenerator(model).problems()


def cache_folder() -> Path:
    return Path(tempfile.gettempdir()) / "nervus-accelerated"


class AcceleratedModel:
    """A compiled model, run by :func:`nervus.simulate` in accelerated mode."""

    def __init__(self, model: CompiledModel, step: float) -> None:
        from nervus.codegen.sil import find_compiler

        self.model = model
        source = AccGenerator(model).generate_acc(step)
        cc = find_compiler()
        if cc is None:
            raise CodegenError("no C compiler found (install gcc or clang) for accelerated simulation")
        digest = hashlib.sha256((source + cc).encode()).hexdigest()[:20]
        folder = cache_folder()
        folder.mkdir(parents=True, exist_ok=True)
        lib = folder / f"acc_{digest}.so"
        self.compiled = not lib.exists()
        if self.compiled:
            src = folder / f"acc_{digest}.c"
            src.write_text(source, encoding="utf-8")
            tmp = folder / f"acc_{digest}.{np.random.default_rng().integers(1 << 30)}.so"
            proc = subprocess.run([cc, "-std=c99", "-O2", "-shared", "-fPIC", "-o", str(tmp), str(src), "-lm"],
                                  capture_output=True, text=True)  # fmt: skip
            if proc.returncode != 0:
                raise CodegenError(f"the accelerated model does not compile:\n{proc.stderr[:4000]}")
            tmp.replace(lib)  # atomic: another process may build the same model
        self.lib = ctypes.CDLL(str(lib))
        dbl = ctypes.POINTER(ctypes.c_double)
        self.lib.nervus_acc_run.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_int,
                                            ctypes.c_int, dbl, dbl, dbl]  # fmt: skip
        self.lib.nervus_acc_run.restype = ctypes.c_int
        self.width = int(self.lib.nervus_acc_width())

    def run(self, t0: float, tf: float, h: float, method: str) -> tuple[Array, Array]:
        """(time points, logged signals: one row per time point, all block outputs in model order)."""
        n_steps = math.ceil((tf - t0) / h - 1e-9)
        x0 = np.ascontiguousarray(self.model.xc0, dtype=float)
        if x0.size == 0:
            x0 = np.zeros(1)
        t = np.zeros(n_steps + 1)
        log = np.zeros((n_steps + 1, max(1, self.width)))
        dbl = ctypes.POINTER(ctypes.c_double)
        n = self.lib.nervus_acc_run(t0, tf, h, n_steps, 0 if method == "euler" else 1, x0.ctypes.data_as(dbl),
                                    t.ctypes.data_as(dbl), log.ctypes.data_as(dbl))  # fmt: skip
        return t[:n], log[:n, : self.width]


def fundamental_step(model: CompiledModel, h: float) -> float:
    """The largest step <= h on the grid of every discrete rate (period and offset)."""
    values = [Fraction(v).limit_denominator(10**9) for r in model.rates for v in (r.period, r.offset) if v]
    if not values:
        return h
    g = values[0]
    for v in values[1:]:
        g = Fraction(math.gcd(g.numerator * v.denominator, v.numerator * g.denominator), g.denominator * v.denominator)
    f = float(g)
    return f / max(1, math.ceil(f / h - 1e-9))
