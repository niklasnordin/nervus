"""Linear analysis: linearize a model, find steady states, and compute frequency responses and margins.

The model's top-level **In** blocks are the inputs and its **Out** blocks the outputs. Linearization works on
the continuous-time part of the model (blocks with continuous states); discrete blocks are held at their
current output.

    from nervus import load
    from nervus.analysis import linearize, trim

    plant = load("motor.nervus")
    op = trim(plant, {"voltage": 6.0})            # steady state for a 6 V input
    lin = linearize(plant, x0=op.x, u0={"voltage": 6.0})
    print(lin.poles(), lin.dc_gain())
    w, mag_db, phase = lin.bode()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy import optimize
from scipy import signal as sps

from nervus.blocks.ports import Inport, Outport
from nervus.core.diagram import Diagram
from nervus.core.errors import ModelError
from nervus.core.signals import Signal
from nervus.core.types import Array
from nervus.sim.compiler import CompiledModel, compile_diagram


@dataclass
class Margins:
    gain_margin_db: float
    """How much the loop gain can increase before instability (inf if the phase never reaches -180°)."""
    phase_margin_deg: float
    """How much phase lag can be added at the gain crossover before instability (inf if |L| never crosses 1)."""
    gain_crossover: float
    """Frequency [rad/s] where |L| = 1 (0 dB)."""
    phase_crossover: float
    """Frequency [rad/s] where the phase is -180°."""


@dataclass
class LinearModel:
    """x' = A x + B u, y = C x + D u around an operating point."""

    A: Array
    B: Array
    C: Array
    D: Array
    inputs: list[str]
    outputs: list[str]
    states: list[str] = field(default_factory=list)

    def poles(self) -> npt.NDArray[np.complex128]:
        return np.asarray(np.linalg.eigvals(self.A) if self.A.size else np.zeros(0), dtype=np.complex128)

    def is_stable(self) -> bool:
        return bool(np.all(self.poles().real < 0))

    def _siso(self, i: int, o: int) -> tuple[Array, Array, Array, Array]:
        return self.A, self.B[:, i : i + 1], self.C[o : o + 1, :], self.D[o : o + 1, i : i + 1]

    def transfer_function(self, input: int = 0, output: int = 0) -> tuple[Array, Array]:
        """Numerator and denominator (descending powers of s) from one input to one output."""
        a, b, c, d = self._siso(input, output)
        if a.size == 0:
            return np.atleast_1d(d.ravel()), np.array([1.0])
        num, den = sps.ss2tf(a, b, c, d)
        num = np.trim_zeros(np.where(np.abs(num[0]) < 1e-12 * np.max(np.abs(num[0]) + 1e-300), 0.0, num[0]), "f")
        return minreal(num if num.size else np.array([0.0]), den)

    def zeros(self, input: int = 0, output: int = 0) -> npt.NDArray[np.complex128]:
        num, _ = self.transfer_function(input, output)
        return np.asarray(np.roots(num) if num.size > 1 else np.zeros(0), dtype=np.complex128)

    def dc_gain(self, input: int = 0, output: int = 0) -> float:
        num, den = self.transfer_function(input, output)
        return float(np.polyval(num, 0.0) / np.polyval(den, 0.0)) if np.polyval(den, 0.0) != 0 else float("inf")

    def frequency_response(self, w: Array, input: int = 0, output: int = 0) -> Array:
        num, den = self.transfer_function(input, output)
        s = 1j * np.asarray(w, dtype=float)
        return np.polyval(num, s) / np.polyval(den, s)

    def bode(self, w: Array | None = None, input: int = 0, output: int = 0) -> tuple[Array, Array, Array]:
        """(w [rad/s], magnitude [dB], phase [°]) with the phase unwrapped."""
        if w is None:
            w = self.default_frequencies()
        num, den = self.transfer_function(input, output)
        h = np.polyval(num, 1j * np.asarray(w, dtype=float)) / np.polyval(den, 1j * np.asarray(w, dtype=float))
        mag = 20 * np.log10(np.maximum(np.abs(h), 1e-300))
        return np.asarray(w), mag, phase_deg(num, den, w)

    def default_frequencies(self) -> Array:
        p = np.abs(self.poles())
        z = np.abs(self.zeros())
        corners = np.concatenate([p[p > 0], z[z > 0]])
        lo = np.min(corners) / 100 if corners.size else 1e-2
        hi = np.max(corners) * 100 if corners.size else 1e2
        return np.logspace(np.log10(lo), np.log10(hi), 600)

    def margins(self, input: int = 0, output: int = 0) -> Margins:
        """Stability margins of this model used as an **open loop** L(s) in a unity negative feedback loop."""
        return tf_margins(*self.transfer_function(input, output))

    def nyquist(self, w: Array | None = None, input: int = 0, output: int = 0) -> tuple[Array, Array]:
        """Real and imaginary part of G(jω) for ω from -∞ to ∞ (the negative frequencies mirror the positive)."""
        if w is None:
            w = self.default_frequencies()
        h = self.frequency_response(w, input, output)
        h = np.concatenate([np.conj(h[::-1]), h])
        return h.real, h.imag

    def root_locus(
        self, gains: Array | None = None, input: int = 0, output: int = 0
    ) -> tuple[Array, npt.NDArray[np.complex128]]:
        """Closed-loop poles of the loop k·G(s) in negative feedback for each gain k: ``(gains, poles)`` with
        one row of poles per gain."""
        return root_locus(*self.transfer_function(input, output), gains)

    def closed_loop(self, input: int = 0, output: int = 0) -> tuple[Array, Array]:
        """T(s) = G / (1 + G): this model as the open loop of a unity negative feedback loop."""
        return feedback(*self.transfer_function(input, output))

    def step_response(self, t: Array | None = None, input: int = 0, output: int = 0) -> tuple[Array, Array]:
        num, den = self.transfer_function(input, output)
        tt, y = sps.step((num, den), T=t)
        return np.asarray(tt), np.asarray(y)


def minreal(num: Array, den: Array, tol: float = 1e-6) -> tuple[Array, Array]:
    """Cancel poles and zeros that coincide (within ``tol``, relative): states that the input cannot move
    or the output cannot see (a battery's charge in a motor's speed loop), which would otherwise leave
    numerical poles near zero."""
    num, den = np.asarray(num, dtype=float), np.asarray(den, dtype=float)
    if num.size < 2 or den.size < 2 or not np.any(num):
        return num, den
    zeros, poles = list(np.roots(num)), list(np.roots(den))
    kept_z = []
    for z in zeros:
        scale = max(1.0, abs(z))
        k = next((i for i, p in enumerate(poles) if abs(p - z) <= tol * scale + 1e-9), None)
        if k is None:
            kept_z.append(z)
        else:
            poles.pop(k)
    if len(kept_z) == len(zeros):
        return num, den
    gain = num[0] / den[0]
    new_num = gain * np.real(np.poly(kept_z)) if kept_z else np.array([gain])
    return np.asarray(new_num, dtype=float), np.asarray(np.real(np.poly(poles)), dtype=float)


def _pad(a: Array, n: int) -> Array:
    return np.concatenate([np.zeros(n - a.size), a])


def feedback(num: Array, den: Array) -> tuple[Array, Array]:
    """Closed loop L / (1 + L) of an open loop L = num/den in unity negative feedback."""
    n = max(num.size, den.size)
    return np.asarray(num, dtype=float), _pad(np.asarray(den, dtype=float), n) + _pad(np.asarray(num, dtype=float), n)


def root_locus(num: Array, den: Array, gains: Array | None = None) -> tuple[Array, npt.NDArray[np.complex128]]:
    """Roots of den(s) + k·num(s) for each gain k (sorted so each pole follows a continuous branch)."""
    if gains is None:  # around the gain that makes |k·G(0)| = 1 (or k = 1 for integrating plants)
        g0 = abs(np.polyval(num, 0.0)) / abs(np.polyval(den, 0.0)) if np.polyval(den, 0.0) != 0 else np.inf
        scale = 1.0 / g0 if 0 < g0 < np.inf else 1.0
        gains = np.concatenate([[0.0], scale * np.logspace(-3, 3, 400)])
    n = max(num.size, den.size)
    pn, pd = _pad(np.asarray(num, float), n), _pad(np.asarray(den, float), n)
    order = np.trim_zeros(pd, "f").size - 1
    rows = []
    prev: npt.NDArray[np.complex128] | None = None
    for k in gains:
        r = np.roots(np.trim_zeros(pd + k * pn, "f"))
        r = np.concatenate([r, np.full(max(0, order - r.size), np.nan)])[:order].astype(np.complex128)
        if prev is not None and r.size == prev.size:  # match each root to the nearest of the previous gain
            left = list(range(r.size))
            matched = np.empty_like(r)
            for j, p in enumerate(prev):
                if not left:
                    break
                d = [abs(r[q] - p) if np.isfinite(r[q]) and np.isfinite(p) else np.inf for q in left]
                q = left.pop(int(np.argmin(d)))
                matched[j] = r[q]
            r = matched
        rows.append(r)
        prev = r
    return np.asarray(gains, dtype=float), np.asarray(rows, dtype=np.complex128)


def _low_frequency(num: Array, den: Array) -> tuple[int, float]:
    """(type, w_ref): the number of poles minus zeros at s = 0, and a frequency well below every other pole and
    zero, where the phase is close to its low-frequency asymptote -90°·type (or that ±180° for a negative gain)."""
    zr = np.roots(num) if np.asarray(num).size > 1 else np.zeros(0)
    pr = np.roots(den) if np.asarray(den).size > 1 else np.zeros(0)
    roots = np.abs(np.concatenate([zr, pr]))
    tol = 1e-9 * max(1.0, float(np.max(roots)) if roots.size else 1.0)
    n_type = int(np.sum(np.abs(pr) <= tol)) - int(np.sum(np.abs(zr) <= tol))
    others = roots[roots > tol]
    return n_type, (1e-3 * float(np.min(others)) if others.size else 1.0)


def phase_deg(num: Array, den: Array, w: Array) -> Array:
    """Unwrapped phase [°] of num/den at the frequencies ``w`` (ascending, > 0), on the branch that starts at
    the low-frequency asymptote -90°·(poles - zeros at s = 0): an integrator is -90°, a double integrator -180°
    (not +180°, which is where ``np.angle`` would put it)."""
    w = np.asarray(w, dtype=float)
    num, den = np.asarray(num, dtype=float), np.asarray(den, dtype=float)

    def angle(x: Array) -> Array:
        return np.asarray(np.angle(np.polyval(num, 1j * x) / np.polyval(den, 1j * x)))

    if w.size == 0 or np.any(w <= 0) or np.any(np.diff(w) <= 0):
        return np.asarray(np.degrees(np.unwrap(angle(w))))
    n_type, w_ref = _low_frequency(num, den)
    lead = np.zeros(0)
    if w_ref < w[0]:  # unwrap from far below the first frequency, where the phase is known
        decades = np.log10(w[0] / w_ref)
        lead = np.logspace(np.log10(w_ref), np.log10(w[0]), max(2, int(100 * decades) + 1))[:-1]
    grid = np.concatenate([lead, w])
    phase = np.degrees(np.unwrap(angle(grid)))
    target = -90.0 * n_type
    phase += 360.0 * np.floor((target + 180.0 - phase[0]) / 360.0)  # phase[0] in (target - 180, target + 180]
    return phase[lead.size :]


def tf_margins(num: Array, den: Array) -> Margins:
    """Gain and phase margins of the open loop num/den in unity negative feedback. The phase margin is wrapped
    to (-180°, 180°] (for a stable open loop, negative means an unstable closed loop); the gain margin is taken where the phase first
    crosses -180° (± a multiple of 360°)."""
    w = np.logspace(-4, 5, 20000)
    h = np.polyval(num, 1j * w) / np.polyval(den, 1j * w)
    mag = 20 * np.log10(np.maximum(np.abs(h), 1e-300))
    phase = phase_deg(num, den, w)
    gm, pm, wgc, wpc = np.inf, np.inf, np.nan, np.nan
    cross = np.nonzero(np.diff(np.sign(mag)) != 0)[0]
    if cross.size:
        k = cross[0]
        wgc = float(np.interp(0.0, [mag[k + 1], mag[k]], [w[k + 1], w[k]]))
        ph = float(np.interp(wgc, w, phase))
        pm = 180.0 - float(np.mod(-ph, 360.0))  # 180 + ph, wrapped to (-180, 180]
    band = np.floor((phase + 180.0) / 360.0)  # changes where the phase crosses -180° + 360°·k
    pc = np.nonzero(np.diff(band) != 0)[0]
    if pc.size:
        k = pc[0]
        level = 360.0 * max(band[k], band[k + 1]) - 180.0
        lo, hi = sorted([(phase[k], w[k]), (phase[k + 1], w[k + 1])])
        wpc = float(np.interp(level, [lo[0], hi[0]], [lo[1], hi[1]]))
        gm = -float(np.interp(wpc, w, mag))
    return Margins(gm, pm, wgc, wpc)


@dataclass
class OperatingPoint:
    x: Array
    """Continuous states."""
    u: dict[str, float]
    residual: float
    """Largest |dx/dt| left (0 for an exact steady state)."""


class _Evaluator:
    def __init__(self, diagram: Diagram, t: float) -> None:
        self.diagram = diagram
        self.t = t
        self.ins = sorted((b for b in diagram.blocks if isinstance(b, Inport)), key=lambda b: b.params["port"])
        self.outs = sorted((b for b in diagram.blocks if isinstance(b, Outport)), key=lambda b: b.params["port"])
        for b in self.ins:
            b.signal = Signal.constant(np.atleast_1d(np.asarray(b.params["value"], dtype=float)).tolist())
        self.model: CompiledModel = compile_diagram(diagram)
        if any(w != 1 for b in self.ins for w in [b.signal.width]):
            raise ModelError("linearization supports scalar In blocks only")
        m = self.model
        m.reset()
        m.eval_outputs(t, m.xc0, frozenset(range(len(m.rates))), init=True)
        self.out_nodes = [m.index_of(b) for b in self.outs]

    def close(self) -> None:
        for b in self.ins:
            b.signal = None

    def set_u(self, u: Array) -> None:
        for b, v in zip(self.ins, u, strict=True):
            b.signal = Signal.constant(float(v))

    def f(self, x: Array, u: Array) -> Array:
        self.set_u(u)
        return self.model.derivatives(self.t, x)

    def y(self, x: Array, u: Array) -> Array:
        self.set_u(u)
        self.model.eval_outputs(self.t, x, None)
        vals = []
        for i in self.out_nodes:
            s, p = self.model.inputs[i][0]
            vals.append(float(self.model.y[s][p][0]))
        return np.array(vals)


def _u_vector(ev: _Evaluator, u0: dict[str, Any] | None) -> Array:
    u0 = u0 or {}
    unknown = set(u0) - {b.name for b in ev.ins}
    if unknown:
        raise ModelError(f"no top-level In block named {', '.join(sorted(unknown))}")
    return np.array([float(u0.get(b.name, np.ravel(b.params["value"])[0])) for b in ev.ins])


def linearize(
    diagram: Diagram,
    x0: Array | None = None,
    u0: dict[str, Any] | None = None,
    t: float = 0.0,
    eps: float = 1e-6,
) -> LinearModel:
    """Linearize around the states ``x0`` (default: the initial states) and inputs ``u0`` (by In-block name;
    default: each In block's value), using central differences."""
    ev = _Evaluator(diagram, t)
    try:
        m = ev.model
        x = np.asarray(m.xc0 if x0 is None else x0, dtype=float).copy()
        u = _u_vector(ev, u0)
        n, k, p = x.size, u.size, len(ev.outs)
        a, b = np.zeros((n, n)), np.zeros((n, k))
        c, d = np.zeros((p, n)), np.zeros((p, k))
        for j in range(n):
            h = eps * max(1.0, abs(x[j]))
            xp, xm = x.copy(), x.copy()
            xp[j] += h
            xm[j] -= h
            a[:, j] = (ev.f(xp, u) - ev.f(xm, u)) / (2 * h)
            c[:, j] = (ev.y(xp, u) - ev.y(xm, u)) / (2 * h)
        for j in range(k):
            h = eps * max(1.0, abs(u[j]))
            up, um = u.copy(), u.copy()
            up[j] += h
            um[j] -= h
            b[:, j] = (ev.f(x, up) - ev.f(x, um)) / (2 * h)
            d[:, j] = (ev.y(x, up) - ev.y(x, um)) / (2 * h)
        states = []
        for i, sl in enumerate(m.xc_slices):
            states += [m.names[i] + (f"[{q}]" if sl.stop - sl.start > 1 else "") for q in range(sl.stop - sl.start)]
        return LinearModel(a, b, c, d, [bl.name for bl in ev.ins], [bl.name for bl in ev.outs], states)
    finally:
        ev.close()


def trim(
    diagram: Diagram, u0: dict[str, Any] | None = None, x_guess: Array | None = None, t: float = 0.0
) -> OperatingPoint:
    """Find continuous states where all derivatives are zero (a steady state) for constant inputs ``u0``."""
    ev = _Evaluator(diagram, t)
    try:
        u = _u_vector(ev, u0)
        x0 = np.asarray(ev.model.xc0 if x_guess is None else x_guess, dtype=float)
        if x0.size == 0:
            return OperatingPoint(x0, {b.name: float(v) for b, v in zip(ev.ins, u, strict=True)}, 0.0)
        sol = optimize.least_squares(lambda x: ev.f(x, u), x0, xtol=1e-12, ftol=1e-12, gtol=1e-12)
        res = float(np.max(np.abs(ev.f(sol.x, u))))
        return OperatingPoint(sol.x, {b.name: float(v) for b, v in zip(ev.ins, u, strict=True)}, res)
    finally:
        ev.close()


class _LoopEvaluator:
    """Evaluates a model with the loop opened at one block: its output is replaced by an injected value."""

    def __init__(self, diagram: Diagram, path: str, t: float) -> None:
        self.ins = sorted((b for b in diagram.blocks if isinstance(b, Inport)), key=lambda b: b.params["port"])
        for b in self.ins:
            b.signal = Signal.constant(np.atleast_1d(np.asarray(b.params["value"], dtype=float)).tolist())
        try:
            self.model: CompiledModel = compile_diagram(diagram)
        except Exception:
            self.close()
            raise
        m = self.model
        if path not in m.names:
            self.close()
            raise ModelError(f"no block {path!r} in the model")
        self.i = m.names.index(path)
        self.block = m.blocks[self.i]
        if m.out_widths[self.i] != [1] or len(m.inputs[self.i]) < 1:
            self.close()
            raise ModelError(f"{path}: the loop can only be opened at a block with one scalar output and an input")
        self.t = t
        self.all_hits = frozenset(range(len(m.rates)))
        self.u = 0.0
        m.reset()
        m.eval_outputs(t, m.xc0, self.all_hits, init=True)
        self.u = float(m.y[self.i][0][0])  # the operating point: the controller output at the start
        self.block.outputs = lambda t, u, xc, xd: [np.array([self.u])]  # type: ignore[method-assign]
        own = m.xc_slices[self.i]
        self.keep = np.array([k for k in range(m.xc0.size) if not own.start <= k < own.stop], dtype=int)
        self.x_full = m.xc0.copy()

    def close(self) -> None:
        for b in self.ins:
            b.signal = None
        block = getattr(self, "block", None)
        if block is not None and "outputs" in vars(block):
            del block.outputs

    def _eval(self, x: Array, u: float) -> None:
        self.u = u
        full = self.x_full.copy()
        full[self.keep] = x
        self.x_full_now = full
        # Discrete blocks without state (sampling, gains) pass signals on, so a sampled sensor does not cut the loop.
        self.model.eval_outputs(self.t, full, self.all_hits)

    def f(self, x: Array, u: float) -> Array:
        self._eval(x, u)
        m = self.model
        dx = np.zeros(m.xc0.size)
        for i, b, sl, inputs in m._cont_states:
            if i != self.i:
                dx[sl] = b.derivatives(self.t, [m.y[s][p] for s, p in inputs], self.x_full_now[sl], m.xd[i])
        return dx[self.keep]

    def y(self, x: Array, u: float) -> float:
        self._eval(x, u)
        s, p = self.model.inputs[self.i][0]
        return float(self.model.y[s][p][0])


def plant_at(diagram: Diagram, block: str, t: float = 0.0, eps: float = 1e-6, u0: float | None = 0.0) -> LinearModel:
    """The plant a controller block sees: the loop is opened at the block's output, and the result is the
    transfer function G(s) from the controller output to the *negated* controller input, so that the loop is
    C(s)·G(s) in negative feedback (the input of a controller is usually the error r - y).

    ``block`` is the block's path, e.g. ``"ECU/idle_pi"``. Discrete blocks without state (sampling, gains) pass the
    signal on; discrete blocks with state (delays, filters) are held at their initial output.

    ``u0`` is the controller output at the operating point (default 0: small signals around zero, away from
    actuator limits); ``None`` uses the block's output at the start.
    """
    ev = _LoopEvaluator(diagram, block, t)
    try:
        m = ev.model
        x = m.xc0[ev.keep].copy()
        if u0 is None:
            u0 = ev.u
        n = x.size
        a, b = np.zeros((n, n)), np.zeros((n, 1))
        c, d = np.zeros((1, n)), np.zeros((1, 1))
        for j in range(n):
            h = eps * max(1.0, abs(x[j]))
            xp, xm = x.copy(), x.copy()
            xp[j] += h
            xm[j] -= h
            a[:, j] = (ev.f(xp, u0) - ev.f(xm, u0)) / (2 * h)
            c[0, j] = -(ev.y(xp, u0) - ev.y(xm, u0)) / (2 * h)
        h = eps * max(1.0, abs(u0))
        b[:, 0] = (ev.f(x, u0 + h) - ev.f(x, u0 - h)) / (2 * h)
        d[0, 0] = -(ev.y(x, u0 + h) - ev.y(x, u0 - h)) / (2 * h)
        states = []
        for i, sl in enumerate(m.xc_slices):
            if i != ev.i:
                states += [m.names[i] + (f"[{q}]" if sl.stop - sl.start > 1 else "") for q in range(sl.stop - sl.start)]
        return LinearModel(a, b, c, d, [f"{block} (output)"], [f"-{block} (input)"], states)
    finally:
        ev.close()


@dataclass
class PIDGains:
    """Gains for a PID Controller block (parallel form), and what they achieve with the plant."""

    P: float
    I: float  # noqa: E741
    D: float
    N: float
    crossover: float
    """Gain crossover frequency [rad/s]: roughly the closed-loop bandwidth."""
    phase_margin: float
    gain_margin_db: float

    def as_params(self) -> dict[str, float]:
        return {"P": self.P, "I": self.I, "D": self.D, "N": self.N}


def pid_transfer(kp: float, ki: float, kd: float = 0.0, n: float = 100.0) -> tuple[Array, Array]:
    """C(s) = P + I/s + D·N·s/(s + N) (``kp``, ``ki``, ``kd``, ``n``), as numerator and denominator."""
    if kd == 0:
        return np.array([kp, ki], dtype=float), np.array([1.0, 0.0])
    num = np.polyadd(np.polyadd(np.polymul([kp], [1.0, n, 0.0]), np.polymul([ki], [1.0, n])), [kd * n, 0.0, 0.0])
    return np.asarray(num, dtype=float), np.array([1.0, n, 0.0])


def _pid_candidate(
    gnum: Array, gden: Array, form: str, wc: float, ratio: float, phase_margin: float, min_gm: float
) -> PIDGains | None:
    """The controller with Ti = ratio/ωc (and Td = Ti/4 for a PID) and |C·G(jωc)| = 1, if it meets the targets."""
    g = np.polyval(gnum, 1j * wc) / np.polyval(gden, 1j * wc)
    if not np.isfinite(g) or abs(g) < 1e-14:
        return None
    ti = ratio / wc
    td = ti / 4.0 if form == "PID" else 0.0
    n = 10.0 * wc
    shape = pid_transfer(1.0, 1.0 / ti, td, n)  # the controller with Kp = 1
    c1 = np.polyval(shape[0], 1j * wc) / np.polyval(shape[1], 1j * wc)
    kp = float(1.0 / abs(c1 * g))
    cnum, cden = pid_transfer(kp, kp / ti, kp * td, n)
    lnum = np.asarray(np.polymul(cnum, gnum), dtype=float)
    lden = np.asarray(np.polymul(cden, gden), dtype=float)
    cl = np.roots(np.trim_zeros(feedback(lnum, lden)[1], "f"))
    if cl.size and np.max(cl.real) >= -1e-9:
        return None
    m = tf_margins(lnum, lden)
    if m.phase_margin_deg < phase_margin or m.gain_margin_db < min_gm:
        return None
    return PIDGains(kp, kp / ti, kp * td, n, wc, m.phase_margin_deg, m.gain_margin_db)


def tune_pid(
    plant: LinearModel,
    form: str = "PI",
    phase_margin: float = 60.0,
    max_crossover: float | None = None,
    min_gain_margin_db: float = 6.0,
) -> PIDGains:
    """Tune a PI or PID controller for ``plant`` (as from :func:`plant_at`) by loop shaping.

    The controller shape is set relative to the crossover frequency ωc: a PI has its zero at ωc/4
    (Ti = 4/ωc); a PID has Ti = 4·Td = 4/ωc and its derivative filter at 10·ωc. The proportional gain makes
    |C·G| = 1 at ωc. The result is the fastest ωc that still gives ``phase_margin`` degrees, at least
    ``min_gain_margin_db`` and a stable closed loop. If that leaves phase margin to spare (because ωc reached
    its limit), the integral action is made faster (smaller Ti) until the margin is used.

    ``max_crossover`` [rad/s] limits the speed (default: ten times the fastest pole or zero of the plant); for
    a discrete controller use about a twentieth of the sample rate, 2π/(20·Ts)."""
    if form not in ("PI", "PID"):
        raise ValueError("form must be 'PI' or 'PID'")
    gnum, gden = plant.transfer_function()
    corners = np.abs(np.concatenate([plant.poles(), plant.zeros()]))
    corners = corners[corners > 1e-9]
    cap = max_crossover if max_crossover is not None else (10.0 * float(np.max(corners)) if corners.size else 10.0)
    lo = float(np.min(corners)) / 100 if corners.size else 1e-3
    best: PIDGains | None = None
    for wc in (float(w) for w in np.logspace(np.log10(min(lo, cap / 1e4)), np.log10(cap), 300)):
        found = _pid_candidate(gnum, gden, form, wc, 4.0, phase_margin, min_gain_margin_db)
        if found is not None:
            best = found
    if best is None:
        raise ValueError(
            f"no {form} gains give {phase_margin:g}° phase margin and a stable loop; try a smaller phase margin, "
            "the other controller form, or check the plant (is the sign of the feedback right?)"
        )
    for ratio in np.linspace(3.5, 0.5, 13):  # use spare phase margin for faster integral action
        faster = _pid_candidate(gnum, gden, form, best.crossover, float(ratio), phase_margin, min_gain_margin_db)
        if faster is None:
            break
        best = faster
    return best
