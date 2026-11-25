"""The simulation loop.

At every *major* time step the simulator

1. computes all block outputs (discrete blocks only at their sample hits; held otherwise),
2. logs them,
3. updates the discrete states of blocks with a sample hit,
4. integrates the continuous states to the next major time.

With a fixed-step solver the major steps are a uniform grid. With a variable-step solver the major steps are
the discrete sample hits and the block breakpoints, and the solver chooses its own steps in between (those
points are logged too).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from typing import Any

import numpy as np

from nervus.core.diagram import Diagram
from nervus.core.errors import ModelError, SimulationError
from nervus.core.sample_time import SampleTime
from nervus.core.settings import SimulationSettings
from nervus.core.types import EMPTY, Array
from nervus.sim.compiler import CompiledModel, Kind, compile_diagram
from nervus.sim.result import SimResult, SimStats
from nervus.solvers import FIXED_STEP_METHODS, integrate_variable


def _tol(t: float) -> float:
    return 1e-9 * max(1.0, abs(t))


class _RateScheduler:
    """Tracks the next sample hit of each discrete rate using integer counters (no drift)."""

    def __init__(self, rates: list[SampleTime], t_start: float) -> None:
        self.rates = rates
        # The first hit at or after t_start (k may be negative when the run starts before t = 0).
        self.counts = [math.ceil((t_start - r.offset) / r.period - 1e-9) for r in rates]

    def next_hit(self, k: int) -> float:
        r = self.rates[k]
        return r.offset + self.counts[k] * r.period

    def next_time(self) -> float:
        return min((self.next_hit(k) for k in range(len(self.rates))), default=math.inf)

    def hits(self, t: float) -> frozenset[int]:
        return frozenset(k for k in range(len(self.rates)) if abs(self.next_hit(k) - t) <= _tol(t))

    def advance(self, hits: frozenset[int]) -> None:
        for k in hits:
            self.counts[k] += 1


LIVE_INTERVAL = 0.2
"""Seconds (wall clock) between live snapshots of the Scope signals."""


class _Logger:
    def __init__(
        self, model: CompiledModel, live: Callable[[SimResult], None] | None = None, live_interval: float | None = None
    ) -> None:
        self.model = model
        self.t: list[float] = []
        self.data: list[list[list[Array]]] = [[[] for _ in ws] for ws in model.out_widths]
        self.live = live
        self.live_interval = LIVE_INTERVAL if live_interval is None else live_interval
        self._last_live = time.monotonic()
        self.pacing = 0.0  # simulated seconds per wall-clock second (0: no pacing)
        self._t0 = 0.0
        self._wall0 = time.monotonic()

    def start_pacing(self, pacing: float, t0: float) -> None:
        self.pacing, self._t0, self._wall0 = pacing, t0, time.monotonic()

    def record(self, t: float) -> None:
        if self.pacing > 0:  # wait until the wall clock catches up with the simulation
            ahead = (t - self._t0) / self.pacing - (time.monotonic() - self._wall0)
            if ahead > 0:
                time.sleep(ahead)
        self.t.append(t)
        for i, outs in enumerate(self.model.y):
            for p, y in enumerate(outs):
                self.data[i][p].append(y.copy())
        if self.live is not None and time.monotonic() - self._last_live >= self.live_interval:
            self._last_live = time.monotonic()
            self.live(self.scope_snapshot())

    def scope_snapshot(self) -> SimResult:
        """The signals logged so far that feed Scope blocks (for live plots while the simulation runs)."""
        m = self.model
        input_keys: dict[str, list[str]] = {}
        signals: dict[str, Array] = {}
        for i, (name, b) in enumerate(zip(m.names, m.blocks, strict=True)):
            if b.type_id != "sinks.scope":
                continue
            keys = [f"{m.names[s]}:{p}" for s, p in m.inputs[i]]
            input_keys[name] = keys
            for (s, p), key in zip(m.inputs[i], keys, strict=True):
                rows = self.data[s][p]
                signals[key] = np.vstack(rows) if rows else np.zeros((0, m.out_widths[s][p]))
        return SimResult(np.asarray(self.t), signals, input_keys, {}, None)

    def result(self, stats: SimStats) -> SimResult:
        m = self.model
        signals: dict[str, Array] = {}
        for i, name in enumerate(m.names):
            for p, w in enumerate(m.out_widths[i]):
                rows = self.data[i][p]
                signals[f"{name}:{p}"] = np.vstack(rows) if rows else np.zeros((0, w))
        input_keys = {name: [f"{m.names[s]}:{p}" for s, p in m.inputs[i]] for i, name in enumerate(m.names)}
        aliases: dict[str, str] = {}
        for name, b in zip(m.names, m.blocks, strict=True):
            for alias, port in b.result_aliases().items():
                aliases[alias] = input_keys[name][port]
        aliases.update(_subsystem_aliases(m.diagram, signals))
        aliases.update(_signal_aliases(m.diagram, signals, input_keys))
        t = np.asarray(self.t)
        dictionary = m.diagram.root.signals
        problems = [msg for sig, key in aliases.items() if sig in dictionary
                    for msg in dictionary[sig].range_problems(t, signals[key])]  # fmt: skip
        if problems:
            stats.warnings = [*(stats.warnings or []), *problems]
        return SimResult(t, signals, input_keys, aliases, stats)


def _on_empty_state(ev: Callable[[float, Array], float]) -> Callable[[float, Array], float]:
    def wrapped(t: float, x: Array) -> float:
        return ev(t, x[:0])

    for attr in ("direction", "index"):
        if hasattr(ev, attr):
            setattr(wrapped, attr, getattr(ev, attr))
    return wrapped


class _ZeroCrossings:
    """Zero-crossing events for solve_ivp, plus the frozen discontinuity *modes* of blocks.

    At every major step the branch (mode) of each discontinuous block is frozen, so the equations are smooth
    during integration. Each zero-crossing signal becomes an event; the integration stops exactly where one
    changes sign, and the modes are re-evaluated there.

    A signal that is exactly zero at the start of a step (just crossed) is only allowed to trigger in the
    opposite direction to the one it is leaving in; a tiny probe step tells which way that is. A signal that
    keeps crossing very often (chattering along a discontinuity, a "Zeno" situation) is switched off with a
    warning, and its block reverts to choosing its branch itself.
    """

    MAX_REPEATS = 50

    def __init__(self, model: CompiledModel, span: float, info: dict[str, Any], t0: float = 0.0) -> None:
        self.model = model
        self.span = max(span, 1e-12)
        self.info = info
        self.disabled: set[int] = set()
        self.history: dict[int, list[float]] = {}
        self._key: tuple[float, bytes] | None = None
        self._values = EMPTY
        self._probe = EMPTY
        self.mode_blocks = list(model._zc_blocks)
        for _i, b, _sl, _inp in self.mode_blocks:
            b.sliding = False
        self.unfrozen: set[int] = set()
        self.signal_block: list[int] = []
        x0 = model.xc0
        model.eval_outputs(t0, x0, None, init=True)
        for i, b, sl, inputs in model._zc_blocks:
            z = b.zero_crossings(t0, [model.y[s][p] for s, p in inputs], x0[sl], model.xd[i])
            self.signal_block.extend([i] * (0 if z is None else np.asarray(z).size))
        # Enable/trigger signals: owner -2 - (index of the block driving the signal).
        self.signal_block.extend(-2 - s for s, _p in model._gate_signals)

    def _values_at(self, t: float, x: Array) -> Array:
        key = (t, x.tobytes())
        if key != self._key:
            self._key = key
            self._values = self.model.zero_crossing_values(t, x)
        return self._values

    # ------------------------------------------------------------------ modes

    def unfreeze(self) -> None:
        for _i, b, _sl, _inp in self.mode_blocks:
            b.mode = None

    def freeze(self, t: float, x: Array, probe: bool = True) -> None:
        """Freeze every block's branch for the next integration step, choosing it from a tiny probe step into
        the future (right after a crossing the inputs sit exactly on the boundary)."""
        m = self.model
        self._key = None
        self.unfreeze()
        h = 1e-8 * self.span
        dx = m.derivatives(t, x) if x.size else x
        tp, xp = t + h, x + h * dx
        m.eval_outputs(tp, xp, None)
        modes = []
        for i, b, sl, inputs in self.mode_blocks:
            modes.append(
                None if i in self.unfrozen else b.compute_mode(tp, [m.y[s][p] for s, p in inputs], xp[sl], m.xd[i])
            )
        for (_i, b, _sl, _inp), mode in zip(self.mode_blocks, modes, strict=True):
            b.mode = None if mode is None else np.asarray(mode, dtype=float)
        self._probe = m.zero_crossing_values(tp, xp)
        m.eval_outputs(t, x, None)  # outputs at t with the frozen modes
        self._key = None

    # ------------------------------------------------------------------ events

    def events(self, t0: float, x0: Array, _cap: float) -> list[Callable[[float, Array], float]]:
        g0 = self._values_at(t0, x0)
        evs: list[Callable[[float, Array], float]] = []
        for k in range(g0.size):
            if k in self.disabled:
                continue

            def ev(t: float, x: Array, k: int = k) -> float:
                return float(self._values_at(min(t, _cap), x)[k])

            scale = 1e-9 * max(1.0, abs(float(self._probe[k])) if k < self._probe.size else 1.0)
            if abs(g0[k]) <= scale and k < self._probe.size and self._probe[k] != 0:
                ev.direction = -float(np.sign(self._probe[k]))  # type: ignore[attr-defined]
            ev.index = k  # type: ignore[attr-defined]
            evs.append(ev)
        self._active = evs
        return evs

    def fired(self, event_position: int, t: float, x: Array) -> None:
        k = self._active[event_position].index  # type: ignore[attr-defined]
        self.info["count"] += 1
        hist = self.history.setdefault(k, [])
        hist.append(t)
        if len(hist) >= self.MAX_REPEATS and hist[-1] - hist[-self.MAX_REPEATS] < 0.01 * self.span:
            block = self.signal_block[k] if k < len(self.signal_block) else -1
            for j, owner in enumerate(self.signal_block):
                if owner == block:
                    self.disabled.add(j)
            self.unfrozen.add(block)
            if block >= 0:
                self.model.blocks[block].sliding = True  # blocks may switch to a smooth version of the switch
            if block >= 0:
                who = self.model.names[block]
            elif block <= -2:
                who = f"the enable/trigger signal from {self.model.names[-2 - block]}"
            else:
                who = "a block"
            self.info["warnings"].append(
                f"{who}: zero crossings keep repeating near t = {t:.6g} (the system chatters along a "
                "discontinuity); zero-crossing detection was turned off for this block"
            )


def default_fixed_step(model: CompiledModel, t_start: float, t_final: float) -> float:
    span = t_final - t_start
    h0 = span / 1000 if span > 0 else 1.0
    if not model.rates:
        return h0
    # Fundamental rate: the largest step that divides every period and offset.
    values = [Fraction(v).limit_denominator(10**9) for r in model.rates for v in (r.period, r.offset) if v]
    g = values[0]
    for v in values[1:]:
        g = Fraction(
            math.gcd(g.numerator * v.denominator, v.numerator * g.denominator),
            g.denominator * v.denominator,
        )
    fundamental = float(g)
    if model.n_continuous_states == 0:
        return fundamental
    return fundamental / max(1, math.ceil(fundamental / h0 - 1e-9))


def simulate(
    diagram: Diagram | CompiledModel,
    t_final: float | None = None,
    *,
    t_start: float | None = None,
    solver: str | None = None,
    step: float | None = None,
    max_step: float | None = None,
    rtol: float | None = None,
    atol: float | None = None,
    settings: SimulationSettings | None = None,
    params: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    coverage: Any = None,
    progress: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    live: Callable[[SimResult], None] | None = None,
) -> SimResult:
    """Simulate ``diagram`` and return the logged signals.

    Settings come from ``settings`` if given, otherwise from ``diagram.settings``; keyword arguments override
    individual values. ``params`` overrides workspace variables for this run, e.g. ``params={"Kp": 2.0}``.
    ``coverage`` (a :class:`nervus.coverage.Coverage`) records which decision outcomes occur.
    ``inputs`` drives the model's top-level In blocks, by name: a :class:`~nervus.core.signals.Signal`, a
    number/vector (constant) or a ``(time, values)`` pair.
    ``progress(t)`` is called after each major step; if ``should_stop()`` returns true the run ends early (used
    by the GUI's Stop button). ``live(partial)`` is called a few times per second with the Scope signals logged so
    far (for live plots).
    """
    if isinstance(diagram, CompiledModel):
        if params or inputs:
            raise ValueError("params/inputs cannot be used with an already compiled model; pass the Diagram")
        return _simulate_compiled(
            diagram,
            t_final,
            t_start,
            solver,
            step,
            max_step,
            rtol,
            atol,
            settings,
            progress,
            should_stop,
            coverage,
            live,
        )
    attached = _attach_inputs(diagram, inputs or {})
    try:
        model = compile_diagram(diagram, params, algebraic_loops=(settings or diagram.settings).algebraic_loops)
        return _simulate_compiled(
            model, t_final, t_start, solver, step, max_step, rtol, atol, settings, progress, should_stop, coverage, live
        )
    finally:
        for block in attached:
            block.signal = None


def _attach_inputs(diagram: Diagram, inputs: dict[str, Any]) -> list[Any]:
    from nervus.blocks.ports import Inport
    from nervus.core.signals import Signal

    ports = {b.name: b for b in diagram.blocks if isinstance(b, Inport)}
    unknown = set(inputs) - set(ports)
    if unknown:
        raise ModelError(
            f"no top-level In block named {', '.join(sorted(unknown))}; inputs: {', '.join(ports) or 'none'}"
        )
    attached = []
    for name, value in inputs.items():
        ports[name].signal = Signal.coerce(value)
        attached.append(ports[name])
    return attached


def _simulate_compiled(
    model: CompiledModel,
    t_final: float | None,
    t_start: float | None,
    solver: str | None,
    step: float | None,
    max_step: float | None,
    rtol: float | None,
    atol: float | None,
    settings: SimulationSettings | None,
    progress: Callable[[float], None] | None,
    should_stop: Callable[[], bool] | None,
    coverage: Any = None,
    live: Callable[[SimResult], None] | None = None,
) -> SimResult:
    base = settings or model.diagram.settings
    overrides: dict[str, Any] = {
        "t_final": t_final,
        "t_start": t_start,
        "solver": solver,
        "step": step,
        "max_step": max_step,
        "rtol": rtol,
        "atol": atol,
    }
    opts = replace(base, **{k: v for k, v in overrides.items() if v is not None})
    opts.validate()
    model.reset()
    for b in model.blocks:
        b.t_start = opts.t_start
    model._coverage = None
    if coverage is not None:
        model.start_coverage(coverage)
        coverage.runs += 1
    wall0 = time.perf_counter()
    log = _Logger(model, live)
    log.start_pacing(opts.pacing, opts.t_start)
    t0, tf = opts.t_start, opts.t_final
    zc_info: dict[str, Any] = {"count": 0, "warnings": []}
    try:
        if opts.is_fixed_step:
            h, n_major, stopped = _run_fixed(model, opts, t0, tf, log, progress, should_stop)
        else:
            h = None
            n_major, stopped = _run_variable(model, opts, t0, tf, log, progress, should_stop, zc_info)
    finally:
        for b in model.blocks:  # never leave a frozen branch behind (e.g. when a block raised an error)
            b.mode = None
    loop_notes = [
        "algebraic loop solved by iteration: "
        + " -> ".join(model.names[m] for m in members)
        + f" (tear: {', '.join(model.names[k] for k in tears)}; {model.loop_iterations} loop evaluations)"
        for members, tears in model.loops
    ]
    stats = SimStats(
        opts.method,
        h,
        len(log.t),
        n_major,
        time.perf_counter() - wall0,
        stopped,
        zc_info["count"],
        [*zc_info["warnings"], *loop_notes] or None,
    )
    return log.result(stats)


def _run_fixed(
    model: CompiledModel,
    opts: SimulationSettings,
    t0: float,
    tf: float,
    log: _Logger,
    progress: Callable[[float], None] | None,
    should_stop: Callable[[], bool] | None,
) -> tuple[float, int, bool]:
    h = opts.step or default_fixed_step(model, t0, tf)
    for r in model.rates:
        for name, v in (("period", r.period), ("offset", r.offset)):
            n = v / h
            if abs(n - round(n)) > 1e-6:
                raise SimulationError(
                    f"fixed step {h:g} does not divide the sample time {name} {v:g}; "
                    "choose a step that is an integer fraction of every discrete sample time"
                )
        n = (t0 - r.offset) / h
        if abs(n - round(n)) > 1e-6:  # the grid t0 + k*h would never meet the hits offset + j*period
            raise SimulationError(
                f"the start time {t0:g} is not on the fixed-step grid of the sample time {r}: with step {h:g} the "
                "discrete blocks would never run; choose a start time that is a multiple of the step (plus the "
                "sample time offset)"
            )
    step_fn = FIXED_STEP_METHODS[opts.method]
    sched = _RateScheduler(model.rates, t0)
    n_steps = math.ceil((tf - t0) / h - 1e-9)
    x = model.xc0.copy()
    stopped = False
    for k in range(n_steps + 1):
        t = min(t0 + k * h, tf)
        hits = sched.hits(t)
        model.eval_outputs(t, x, hits, init=(k == 0))
        x, jumped = model.apply_major_step(t, x)
        if jumped:  # e.g. an integrator reset: recompute the continuous outputs from the new state
            model.eval_outputs(t, x, None)
        log.record(t)
        model.collect_coverage(t, hits)
        if progress:
            progress(t)
        if model.check_monitors(t):
            stopped = True
            break
        if k == n_steps:
            break
        if should_stop and should_stop():
            stopped = True
            break
        model.update(t, x, hits)
        sched.advance(hits)
        t_next = min(t0 + (k + 1) * h, tf)
        if x.size:
            x = step_fn(model.derivatives, t, x, t_next - t)
    return h, len(log.t), stopped


def _run_variable(
    model: CompiledModel,
    opts: SimulationSettings,
    t0: float,
    tf: float,
    log: _Logger,
    progress: Callable[[float], None] | None,
    should_stop: Callable[[], bool] | None,
    zc_info: dict[str, Any],
) -> tuple[int, bool]:
    span = tf - t0
    max_step = opts.max_step or (span / 50 if span > 0 else 1.0)
    sched = _RateScheduler(model.rates, t0)
    breakpoints = model.breakpoints(t0, tf)
    bp_iter = 0
    # A model where everything is discrete or constant only needs to run at its sample hits.
    purely_discrete = bool(model.rates) and all(k is not Kind.CONTINUOUS for k in model.kinds)
    zc = _ZeroCrossings(model, span, zc_info, t0) if (opts.zero_crossing and model.has_zero_crossings) else None
    # Continuous enable/trigger signals are located as zero crossings; without zero-crossing detection the major
    # steps are at most max_step apart, so the gates are decided at least that often.
    cap_gates = zc is None and model.has_gate_signals
    x = model.xc0.copy()
    t = t0
    first = True
    after_event = False
    n_major = 0
    while True:
        hits = sched.hits(t)
        if zc is not None:
            zc.unfreeze()
            if first or after_event:  # decide the gates by the side their signals move to
                model.probe_gates(t, x, 1e-8 * max(span, 1e-12))
        model.eval_outputs(t, x, hits, init=first)
        model._gate_probe = None
        first = False
        x, jumped = model.apply_major_step(t, x, probe=1e-8 * span if after_event else None)
        if jumped:  # e.g. an integrator reset: the solver restarts from the new state below
            model.eval_outputs(t, x, None)
        log.record(t)
        model.collect_coverage(t, hits)
        n_major += 1
        if progress:
            progress(t)
        if model.check_monitors(t):
            if zc is not None:
                zc.unfreeze()
            return n_major, True
        if t >= tf - _tol(tf):
            if zc is not None:
                zc.unfreeze()
            return n_major, False
        if should_stop and should_stop():
            if zc is not None:
                zc.unfreeze()
            return n_major, True
        model.update(t, x, hits)
        sched.advance(hits)
        if zc is not None:
            zc.freeze(t, x)

        while bp_iter < len(breakpoints) and breakpoints[bp_iter] <= t + _tol(t):
            bp_iter += 1
        next_bp = breakpoints[bp_iter] if bp_iter < len(breakpoints) else math.inf
        t_next = min(sched.next_time(), next_bp, tf)
        if cap_gates:
            t_next = min(t_next, t + max_step)
        if x.size == 0 and zc is None:
            t = t_next if purely_discrete else min(t_next, t + max_step)
            continue

        # Evaluate just left of a breakpoint so the discontinuity happens at the major step, not inside
        # the last solver stage.
        t_eval_max = t_next - _tol(t_next) if t_next == next_bp else t_next

        def f(tt: float, xx: Array, _cap: float = t_eval_max) -> Array:
            return model.derivatives(min(tt, _cap), xx)

        events = zc.events(t, x, _cap=t_eval_max) if zc is not None else None
        stateless = x.size == 0
        if stateless:
            # No continuous states, but zero crossings to locate: integrate a dummy state so events still work.
            t_next = t_next if purely_discrete else min(t_next, t + max_step)
            events = [_on_empty_state(ev) for ev in events or []]

            def f(tt: float, xx: Array, _cap: float = t_eval_max) -> Array:
                return np.zeros(1)

        x_run = np.zeros(1) if stateless else x
        ts, ys, fired = integrate_variable(f, t, t_next, x_run, opts.method, opts.rtol, opts.atol, max_step, events)
        if stateless:
            ys = ys[:0]
        for j in range(1, len(ts) - 1):
            model.eval_outputs(float(ts[j]), ys[:, j], None)
            log.record(float(ts[j]))
        x = ys[:, -1].copy()
        after_event = fired is not None
        if fired is not None and zc is not None:
            t = float(ts[-1])
            zc.fired(fired, t, x)
        else:
            t = t_next


def _signal_aliases(diagram: Diagram, signals: dict[str, Array], input_keys: dict[str, list[str]]) -> dict[str, str]:
    """Signal names (data dictionary) → result keys. An output that is not logged under its own path (e.g. a
    subsystem's) is found through a block it drives."""
    from nervus.core.dictionary import named_signals

    out: dict[str, str] = {}
    try:
        named = named_signals(diagram)
    except Exception:  # a name used twice: reported by the editor
        return out
    for name, (path, port) in named.items():
        key = f"{path}:{port}"
        if key in signals:
            out[name] = key
            continue
        parent, _, block_name = path.rpartition("/")
        system = diagram
        for part in [x for x in parent.split("/") if x]:
            system = getattr(system.block(part), "system", system)
        prefix = f"{parent}/" if parent else ""
        for c in system.connections:
            if c.src.name == block_name and c.src_port == port:
                consumer = input_keys.get(prefix + c.dst.name)
                if consumer is not None and c.dst_port < len(consumer):
                    out[name] = consumer[c.dst_port]
                    break
    return out


def _subsystem_aliases(diagram: Diagram, signals: dict[str, Array]) -> dict[str, str]:
    """``"sub:k"`` → the signal of the subsystem's Out block number k+1, so results can be read by the subsystem's
    name like any other block (``res["ECU"]``, ``res["ECU:1"]``)."""
    out: dict[str, str] = {}
    for path, b in diagram.walk():
        system = getattr(b, "system", None)
        if not isinstance(system, Diagram):
            continue
        ports = sorted((int(x.params["port"]), x.name) for x in system.blocks if x.type_id == "ports.outport")
        for k, (_num, name) in enumerate(ports):
            key = f"{path}/{name}:0"
            if key in signals:
                out[f"{path}:{k}"] = key
    return out
