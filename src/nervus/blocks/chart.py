"""State machine (chart) block: modes and sequences, like a small Stateflow chart.

A chart is written in a short text language::

    input rpm, key
    output mode = 0, fuel = 0
    local tries = 0

    state off
      entry: mode = 0; fuel = 0

    state cranking
      entry: mode = 1; tries = tries + 1
      during: fuel = 1

    state running
      entry: mode = 2; fuel = 1

    transition off -> cranking: key > 0.5
    transition cranking -> running: rpm > 600
    transition cranking -> off: after(300) / fuel = 0
    transition running -> off: key < 0.5 or rpm < 300

``param NAME, ...`` declares read-only calibration parameters whose values come from the model workspace
(or a mask). The first state is the initial one (or write ``initial NAME``). It is entered in the first sample,
so its ``entry`` actions see the inputs of that sample. Every sample period (the first one too) the chart:

1. checks the transitions leaving the active state, **in the order they are written**. The first whose guard
   is true fires: the source's ``exit`` actions run, then the transition's action (after ``/``), then the
   target's ``entry`` actions;
2. if no transition fires, runs the active state's ``during`` actions.

Guards and actions use Python-like expressions: ``+ - * / **``, comparisons, ``and or not``, ``abs min max
sqrt exp log sin cos`` and the constants ``pi`` and ``e``. ``after(n)`` is true when the state has been active for at
least *n* sample periods, and ``t_in_state`` is the time in seconds since the state was entered.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Sequence, Set
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from nervus.blocks.registry import register
from nervus.core.block import Block, Width
from nervus.core.errors import ParameterError
from nervus.core.expressions import parse_value
from nervus.core.params import Param, ParamKind
from nervus.core.types import Array

_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "sin": math.sin,
    "cos": math.cos,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e, "true": 1.0, "false": 0.0, "True": 1.0, "False": 0.0}
_ALLOWED = (
    ast.Expression,
    ast.Module,
    ast.Assign,
    ast.AugAssign,
    ast.Expr,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.IfExp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)

DEFAULT_CHART = """# A two-state example: switch on above 1, off below 0 (hysteresis)
input u
output y = 0

state low
  entry: y = 0

state high
  entry: y = 1

transition low -> high: u > 1
transition high -> low: u < 0
"""


MAX_EVENT_PASSES = 8
"""How many broadcast events one sample may process (each runs the chart once more)."""


@dataclass
class ChartState:
    name: str
    entry: list[ast.stmt] = field(default_factory=list)
    during: list[ast.stmt] = field(default_factory=list)
    exit: list[ast.stmt] = field(default_factory=list)
    entry_src: str = ""
    during_src: str = ""
    exit_src: str = ""
    parent: int | None = None
    """Index of the enclosing state (``None`` at the top level)."""
    children: list[int] = field(default_factory=list)
    parallel: bool = False
    """All children are active at the same time (parallel regions); otherwise exactly one is."""
    initial: int | None = None
    """The child entered by default (the first one unless ``initial NAME`` says otherwise)."""
    history: bool = False
    """Re-entering the state goes back to the child that was active when it was left."""

    @property
    def composite(self) -> bool:
        return bool(self.children)


@dataclass
class ChartTransition:
    source: int
    target: int
    guard: ast.expr
    action: list[ast.stmt]
    guard_src: str
    action_src: str


@dataclass
class ChartSpec:
    inputs: list[str]
    outputs: list[tuple[str, float]]
    locals: list[tuple[str, float]]
    states: list[ChartState]
    transitions: list[ChartTransition]
    initial: int
    """The top-level state entered first."""
    params: list[str] = field(default_factory=list)
    """Read-only names whose values come from the model workspace or a mask (calibration parameters)."""
    events: list[str] = field(default_factory=list)
    """Local events: ``send(name)`` broadcasts one; a guard sees ``name`` as 1 while it is processed."""

    @property
    def variables(self) -> list[str]:
        return [n for n, _ in self.outputs] + [n for n, _ in self.locals]

    @property
    def top(self) -> list[int]:
        return [k for k, st in enumerate(self.states) if st.parent is None]

    @property
    def hierarchical(self) -> bool:
        return any(st.composite for st in self.states) or bool(self.events)

    def ancestors(self, k: int) -> list[int]:
        """``k`` and its enclosing states, innermost first."""
        out = [k]
        while self.states[out[-1]].parent is not None:
            out.append(self.states[out[-1]].parent)  # type: ignore[arg-type]
        return out

    def default_entered(self) -> set[int]:
        """States that are entered by default (initial states and parallel regions), from the top."""
        out = {self.initial}
        for st in self.states:
            if st.composite:
                out |= set(st.children) if st.parallel else {st.initial}  # type: ignore[arg-type]
        return {k for k in out if k is not None}

    def leaf_of(self, k: int, active: Sequence[float] | Array) -> int:
        """The innermost active state under ``k`` (first region of a parallel state)."""
        st = self.states[k]
        while st.composite:
            nxt = next((c for c in st.children if active[c]), None)
            if nxt is None:
                break
            k, st = nxt, self.states[nxt]
        return k


def _check_ast(
    node: ast.AST, names: set[str], assignable: set[str], where: str, events: Set[str] = frozenset()
) -> None:
    for sub in ast.walk(node):
        if not isinstance(sub, _ALLOWED):
            raise ParameterError(f"{where}: '{type(sub).__name__}' is not allowed in charts")
        if isinstance(sub, ast.Call):
            if not isinstance(sub.func, ast.Name) or (
                sub.func.id not in _FUNCS and sub.func.id not in ("after", "send")
            ):
                raise ParameterError(f"{where}: only {', '.join([*_FUNCS, 'after', 'send'])} can be called")
            if sub.keywords:
                raise ParameterError(f"{where}: keyword arguments are not supported")
            if sub.func.id == "send" and (len(sub.args) != 1 or not isinstance(sub.args[0], ast.Name)
                                          or sub.args[0].id not in events):  # fmt: skip
                raise ParameterError(f"{where}: send() takes the name of a declared event, e.g. send(go)")
        known = names | set(_FUNCS) | set(_CONSTS) | {"after", "send"} | set(events)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id not in known:
            raise ParameterError(f"{where}: unknown name '{sub.id}'")
        if isinstance(sub, ast.Assign):
            if len(sub.targets) != 1 or not isinstance(sub.targets[0], ast.Name):
                raise ParameterError(f"{where}: assign one variable at a time, e.g. 'y = 1'")
            if sub.targets[0].id not in assignable:
                raise ParameterError(f"{where}: '{sub.targets[0].id}' is not an output or local variable")
        if isinstance(sub, ast.AugAssign) and (not isinstance(sub.target, ast.Name) or sub.target.id not in assignable):
            raise ParameterError(f"{where}: can only update outputs and locals")


def _parse_actions(text: str, names: set[str], assignable: set[str], where: str,
                   events: Set[str] = frozenset()) -> list[ast.stmt]:  # fmt: skip
    text = text.strip()
    if not text:
        return []
    try:
        mod = ast.parse("\n".join(part.strip() for part in text.split(";")), mode="exec")
    except SyntaxError as exc:
        raise ParameterError(f"{where}: syntax error in '{text}'") from exc
    _check_ast(mod, names, assignable, where, events)
    for stmt in mod.body:
        is_send = (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                   and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "send")  # fmt: skip
        if not isinstance(stmt, ast.Assign | ast.AugAssign) and not is_send:
            raise ParameterError(f"{where}: actions must be assignments like 'y = 1' or send(event)")
    return mod.body


def _parse_vars(text: str, where: str, with_init: bool) -> list[tuple[str, float]]:
    out = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        name, _, init = part.partition("=")
        name = name.strip()
        if not name.isidentifier():
            raise ParameterError(f"{where}: invalid name '{name}'")
        try:
            value = float(parse_value(init)) if init.strip() else 0.0
        except (ValueError, TypeError) as exc:
            raise ParameterError(f"{where}: invalid initial value '{init.strip()}'") from exc
        if init.strip() and not with_init:
            raise ParameterError(f"{where}: inputs have no initial value")
        out.append((name, value))
    return out


def parse_chart(text: str) -> ChartSpec:
    """Parse the chart language (see the module docstring). Raises :class:`ParameterError` with the line."""
    inputs: list[str] = []
    params: list[str] = []
    events: list[str] = []
    outputs: list[tuple[str, float]] = []
    locals_: list[tuple[str, float]] = []
    states: list[ChartState] = []
    raw_transitions: list[tuple[int, str, str, str, str]] = []
    initial_names: list[tuple[int | None, str, int]] = []  # (owner state, name, line)
    pending: list[tuple[ChartState, str, str, int]] = []
    stack: list[tuple[int, int]] = []  # (indentation of a state line, state index), outermost first
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip().replace("\t", "    ")
        if not line.strip():
            continue
        where = f"line {lineno}"
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        keyword, _, rest = stripped.partition(" ")
        while stack and stack[-1][0] >= indent:
            stack.pop()  # this line is not inside those states
        owner = stack[-1][1] if stack else None
        if re.match(r"^(entry|during|exit)\s*:", stripped):
            if owner is None:
                raise ParameterError(f"{where}: entry/during/exit must be indented under a state")
            kind, _, actions = stripped.partition(":")
            pending.append((states[owner], kind.strip(), actions, lineno))
            continue
        if keyword == "state":
            words = rest.split()
            name = words[0] if words else ""
            if not name.isidentifier() or any(w != "parallel" for w in words[1:]):
                raise ParameterError(f"{where}: write 'state NAME' or 'state NAME parallel'")
            if any(st.name == name for st in states):
                raise ParameterError(f"{where}: state '{name}' is defined twice")
            states.append(ChartState(name, parent=owner, parallel="parallel" in words[1:]))
            if owner is not None:
                states[owner].children.append(len(states) - 1)
            stack.append((indent, len(states) - 1))
        elif keyword == "history" and not rest.strip():
            if owner is None:
                raise ParameterError(f"{where}: 'history' must be indented under a state with children")
            states[owner].history = True
        elif keyword == "initial":
            initial_names.append((owner, rest.strip(), lineno))
        elif keyword == "transition":
            m = re.match(r"^(\w+)\s*->\s*(\w+)\s*:(.*)$", rest.strip())
            if not m:
                raise ParameterError(f"{where}: write 'transition A -> B: guard' (optionally '/ action')")
            guard, _, action = m.group(3).partition("/")
            raw_transitions.append((lineno, m.group(1), m.group(2), guard.strip(), action.strip()))
        elif owner is not None:
            raise ParameterError(f"{where}: inside a state only entry, during, exit, state, initial and history")
        elif keyword == "input":
            inputs += [n for n, _ in _parse_vars(rest, where, with_init=False)]
        elif keyword == "output":
            outputs += _parse_vars(rest, where, with_init=True)
        elif keyword == "local":
            locals_ += _parse_vars(rest, where, with_init=True)
        elif keyword == "param":
            params += [n for n, _ in _parse_vars(rest, where, with_init=False)]
        elif keyword == "event":
            events += [n for n, _ in _parse_vars(rest, where, with_init=False)]
        else:
            raise ParameterError(f"{where}: expected input, output, local, param, event, state, initial or transition")
    if not states:
        raise ParameterError("a chart needs at least one state")
    names = set(inputs) | {n for n, _ in outputs} | {n for n, _ in locals_} | set(params) | {"t_in_state"}
    assignable = {n for n, _ in outputs} | {n for n, _ in locals_}
    declared = [*inputs, *(o for o, _ in outputs), *(v for v, _ in locals_), *params, *events]
    dupes = [n for n in declared if declared.count(n) > 1]
    if dupes:
        raise ParameterError(f"names used twice: {', '.join(sorted(set(dupes)))}")
    ev = set(events)
    for state, kind, actions, lineno in pending:
        setattr(state, kind, _parse_actions(actions, names, assignable, f"line {lineno}", ev))
        setattr(state, f"{kind}_src", actions.strip())
    index = {st.name: k for k, st in enumerate(states)}
    top = [k for k, st in enumerate(states) if st.parent is None]
    root_initial = top[0]
    for owner, name, lineno in initial_names:
        if name not in index:
            raise ParameterError(f"line {lineno}: initial state '{name}' does not exist")
        siblings = top if owner is None else states[owner].children
        if index[name] not in siblings:
            raise ParameterError(f"line {lineno}: initial state '{name}' must be directly inside "
                                 f"{'the chart' if owner is None else repr(states[owner].name)}")  # fmt: skip
        if owner is None:
            root_initial = index[name]
        else:
            states[owner].initial = index[name]
    for st in states:
        if st.composite and st.initial is None:
            st.initial = st.children[0]
        if st.history and (not st.composite or st.parallel):
            raise ParameterError(f"state '{st.name}': history needs child states (and not parallel ones)")
    transitions = []
    for lineno, a, b, guard, action in raw_transitions:
        for sname in (a, b):
            if sname not in index:
                raise ParameterError(f"line {lineno}: unknown state '{sname}'")
        src, dst = index[a], index[b]
        anc_s, anc_d = _ancestry(states, src), _ancestry(states, dst)
        lca = next((x for x in anc_s[1:] if x in anc_d), None)
        below = [x for x in anc_s if x != lca][-1:] + [x for x in anc_d if x != lca][-1:]
        if lca is not None and states[lca].parallel and len(set(below)) == 2 and below[0] != below[1]:
            raise ParameterError(f"line {lineno}: no transitions between parallel regions ({a} -> {b})")
        try:
            g = ast.parse(guard or "True", mode="eval")
        except SyntaxError as exc:
            raise ParameterError(f"line {lineno}: syntax error in guard '{guard}'") from exc
        _check_ast(g, names, assignable, f"line {lineno}", ev)
        transitions.append(ChartTransition(src, dst, g.body, _parse_actions(action, names, assignable,
                                                                            f"line {lineno}", ev),
                                           guard or "true", action))  # fmt: skip
    return ChartSpec(inputs, outputs, locals_, states, transitions, root_initial, params, events)


def _ancestry(states: list[ChartState], k: int) -> list[int]:
    out = [k]
    while states[out[-1]].parent is not None:
        out.append(states[out[-1]].parent)  # type: ignore[arg-type]
    return out


def transition_scope(spec: ChartSpec, t: ChartTransition) -> int | None:
    """The innermost state that contains both ends of ``t`` and is left neither way (``None``: the chart itself).

    Everything below it on the source side is exited, on the target side entered. A self-transition, and a
    transition to an enclosing state, leave that state and enter it again (its exit and entry actions run)."""
    anc_s, anc_d = spec.ancestors(t.source), spec.ancestors(t.target)
    return next((x for x in anc_s[1:] if x in anc_d[1:]), None)


def _send_to_index(stmts: list[ast.stmt], events: list[str]) -> list[ast.stmt]:
    """``send(go)`` → ``send(<index of go>)`` (an event name is otherwise a 0/1 value)."""
    out = []
    for st in stmts:
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and getattr(st.value.func, "id", "") == "send":
            arg = st.value.args[0]
            assert isinstance(arg, ast.Name)
            st = ast.Expr(ast.Call(ast.Name("send", ast.Load()), [ast.Constant(events.index(arg.id))], []))
        out.append(st)
    return out


class _Interpreter:
    """Runs a chart on a dictionary of variables (code compiled once). The configuration is kept in arrays:
    ``active`` (0/1 per state), ``ticks`` (samples since each state was entered) and ``hist`` (last active child
    of each state with history, -1 if none)."""

    def __init__(self, spec: ChartSpec) -> None:
        self.spec = spec

        def comp(stmts: list[ast.stmt]) -> Any:
            stmts = _send_to_index(stmts, spec.events)
            mod = ast.Module(body=stmts, type_ignores=[])
            return compile(ast.fix_missing_locations(mod), "<chart>", "exec") if stmts else None

        self.entry = [comp(st.entry) for st in spec.states]
        self.during = [comp(st.during) for st in spec.states]
        self.exit = [comp(st.exit) for st in spec.states]
        self.guards = [
            compile(ast.fix_missing_locations(ast.Expression(t.guard)), "<chart>", "eval") for t in spec.transitions
        ]
        self.actions = [comp(t.action) for t in spec.transitions]
        self.outgoing: list[list[int]] = [[] for _ in spec.states]
        for k, t in enumerate(spec.transitions):
            self.outgoing[t.source].append(k)
        self.fired: list[int] = []
        """Transitions that fired in the last step (for coverage)."""
        self.period = 0.0
        self.entered: set[int] = set()
        """States entered in the current step (their clocks stay at 0)."""

    @staticmethod
    def _run(code: Any, env: dict[str, Any]) -> None:
        if code is not None:
            exec(code, {"__builtins__": {}}, env)

    def _clock(self, k: int, env: dict[str, Any], period: float) -> None:
        # Periods since entry: one has passed at the first step after it, none in the step that entered it (the
        # initial states are entered in the first step, and then processed in it).
        elapsed = self.ticks[k] + (0 if k in self.entered else 1)
        env["t_in_state"] = elapsed * period
        env["after"] = lambda n: elapsed >= n

    # ------------------------------------------------------------------ configuration changes

    def _enter_one(self, k: int, env: dict[str, Any]) -> None:
        env["t_in_state"] = 0.0
        env["after"] = lambda n: n <= 0
        self.active[k], self.ticks[k] = 1.0, 0.0
        self.entered.add(k)
        self._run(self.entry[k], env)

    def _enter_default(self, k: int, env: dict[str, Any]) -> None:
        self._enter_one(k, env)
        st = self.spec.states[k]
        if st.parallel:
            for c in st.children:
                self._enter_default(c, env)
        elif st.composite:
            h = int(self.hist[k])
            self._enter_default(h if st.history and h >= 0 else st.initial, env)  # type: ignore[arg-type]

    def _exit(self, k: int, env: dict[str, Any]) -> None:
        st = self.spec.states[k]
        for c in reversed(st.children):
            if self.active[c]:
                if not st.parallel:
                    self.hist[k] = float(c)
                self._exit(c, env)
        self._clock(k, env, self.period)  # exit actions see the time in their own state (as in the C code)
        self._run(self.exit[k], env)
        self.active[k] = 0.0

    def _fire(self, k: int, env: dict[str, Any]) -> None:
        t = self.spec.transitions[k]
        anc_s, anc_d = self.spec.ancestors(t.source), self.spec.ancestors(t.target)
        lca = transition_scope(self.spec, t)
        leave = anc_s[anc_s.index(lca) - 1] if lca is not None else anc_s[-1]
        self._exit(leave, env)
        self._clock(t.source, env, self.period)  # the transition action sees the time in its source state
        self._run(self.actions[k], env)
        path = anc_d[: anc_d.index(lca)] if lca is not None else anc_d
        for x in reversed(path[1:]):  # down to the target: enter each level, and the other parallel regions
            if not self.active[x]:
                self._enter_one(x, env)
            st = self.spec.states[x]
            if st.parallel:
                for c in st.children:
                    if not self.active[c] and c not in path:
                        self._enter_default(c, env)
        self._enter_default(t.target, env)
        self.fired.append(k)

    # ------------------------------------------------------------------ one sample

    def _process(self, k: int, env: dict[str, Any], period: float, act0: Array) -> None:
        """Outer transitions first; if none fires, ``during`` and then the active children."""
        self._clock(k, env, period)
        for tk in self.outgoing[k]:
            if eval(self.guards[tk], {"__builtins__": {}}, env):
                self._fire(tk, env)
                return
        self._run(self.during[k], env)
        for c in self.spec.states[k].children:
            if act0[c] and self.active[c]:
                self._process(c, env, period, act0)

    def step(self, active: Array, ticks: Array, hist: Array, env: dict[str, Any], period: float) -> None:
        """One sample: process the chart, then every broadcast event (in order), then advance the clocks.
        Changes ``active``, ``ticks`` and ``hist`` in place.

        In the first sample (no state is active yet) the initial states are entered first, with the inputs of that
        sample, and then processed like the rest of the chart."""
        self.active, self.ticks, self.hist, self.period = active, ticks, hist, period
        self.entered = set()
        self.fired = []
        queue: list[int] = []
        env["send"] = lambda e: queue.append(int(e)) if len(queue) < MAX_EVENT_PASSES else None
        for name in self.spec.events:
            env[name] = 0.0
        if not any(active[k] for k in self.spec.top):
            self._enter_default(self.spec.initial, env)
        event: int | None = None
        for _ in range(MAX_EVENT_PASSES + 1):
            if event is not None:
                env[self.spec.events[event]] = 1.0
            act0 = active.copy()
            for k in self.spec.top:
                if act0[k] and active[k]:
                    self._process(k, env, period, act0)
            if event is not None:
                env[self.spec.events[event]] = 0.0
            if not queue:
                break
            event = queue.pop(0)
        for k in range(len(active)):
            if active[k] and k not in self.entered:
                ticks[k] += 1.0


@register
class StateChart(Block):
    """A state machine (chart) for operating modes, sequences and fault handling, like Simulink's Stateflow.

    ![An engine operating-mode chart](figures/chart_engine_modes.png)

    The chart is written in a short text language in *definition*:

    ```
    input rpm, key
    output mode = 0, fuel = 0

    state off
      entry: mode = 0; fuel = 0
    state cranking
      entry: mode = 1
      during: fuel = 1
    state running
      entry: mode = 2

    transition off -> cranking: key > 0.5
    transition cranking -> running: rpm > 600
    transition cranking -> off: after(300) / fuel = 0
    transition running -> off: key < 0.5 or rpm < 300
    ```

    Inputs and outputs become the block's ports, in the order they are declared; with *state_output* an extra
    last output gives the active state's number (0, 1, ... in the order the states are written). Every sample
    period the transitions leaving the active state are checked in the order written; the first true guard
    fires (exit → transition action → entry). Otherwise the state's *during* actions run. ``after(n)`` is true
    after *n* periods in the state, and ``t_in_state`` is the time in the state in seconds. The initial state is
    entered in the first sample (its entry actions see the inputs of that sample), and its clock starts there.

    The chart runs at a discrete rate, and can be generated as C code.
    """

    type_id = "charts.state_chart"
    label = "State Chart"
    category = "State Machines"
    TYPE_RULE = "double"
    PARAMS = (
        Param("definition", DEFAULT_CHART, ParamKind.CODE, "The chart: inputs, outputs, states and transitions"),
        Param("state_output", True, ParamKind.BOOL, "Add an output with the number of the active state"),
        Param("sample_time", -1.0, ParamKind.SAMPLE_TIME, "Sample period (-1 = inherit)", "s"),
    )

    def validate_params(self) -> None:
        self.spec = parse_chart(self.params["definition"])
        self._interp = _Interpreter(self.spec)
        self.param_values: dict[str, float] = getattr(self, "param_values", {})

    def resolve(self, namespace: dict[str, Any]) -> None:
        """Take the values of the chart's ``param`` names from the workspace (or an enclosing mask)."""
        super().resolve(namespace)
        values = {}
        for name in self.spec.params:
            if name not in namespace:
                raise ParameterError(f"chart parameter {name!r} is not a workspace or mask variable")
            try:
                values[name] = float(np.ravel(np.asarray(namespace[name], dtype=float))[0])
            except (TypeError, ValueError) as exc:
                raise ParameterError(f"chart parameter {name!r} must be a number") from exc
        self.param_values = values

    def num_inputs(self) -> int:
        return len(self.spec.inputs)

    def num_outputs(self) -> int:
        return len(self.spec.outputs) + (1 if self.params["state_output"] else 0)

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        return [1] * self.num_outputs()

    def configure(self, in_widths: list[int], out_widths: list[int]) -> None:
        super().configure(in_widths, out_widths)
        bad = [self.spec.inputs[k] for k, w in enumerate(in_widths) if w != 1]
        if bad:
            raise ValueError(f"chart inputs must be scalar: {', '.join(bad)}")

    # discrete state layout: [active (one 0/1 per state), ticks per state, history per state, outputs..., locals...]

    def _layout(self) -> tuple[int, int]:
        n = len(self.spec.states)
        return n, 3 * n

    def _env(self, u: Sequence[Array], xd: Array) -> dict[str, Any]:
        env: dict[str, Any] = {n: float(u[k][0]) for k, n in enumerate(self.spec.inputs)}
        env.update(_FUNCS)
        env.update(_CONSTS)
        env.update(self._params())
        _n, v0 = self._layout()
        for k, name in enumerate(self.spec.variables):
            env[name] = float(xd[v0 + k])
        return env

    def initial_discrete_state(self) -> Array:
        """No state is active yet: the first sample enters the initial states, with the inputs of that sample."""
        self._params()  # every chart parameter must be resolved
        n, _v0 = self._layout()
        values = [float(v) for _, v in self.spec.outputs + self.spec.locals]  # in the order of spec.variables
        return np.array([*np.zeros(n), *np.zeros(n), *np.full(n, -1.0), *values])

    def _params(self) -> dict[str, float]:
        missing = [n for n in self.spec.params if n not in self.param_values]
        if missing:
            raise ParameterError(f"chart parameter(s) {', '.join(missing)} not resolved (add them to the workspace)")
        return dict(self.param_values)

    def _step(self, u: Sequence[Array], xd: Array) -> Array:
        env = self._env(u, xd)
        n, v0 = self._layout()
        active, ticks, hist = xd[:n].copy(), xd[n : 2 * n].copy(), xd[2 * n : v0].copy()
        self._interp.step(active, ticks, hist, env, self.period())
        return np.array([*active, *ticks, *hist, *[float(env[v]) for v in self.spec.variables]])

    def active_state(self, xd: Array) -> int:
        """Number of the innermost active state (in the first parallel region), as the state output gives it."""
        top = next((k for k in self.spec.top if xd[k]), self.spec.initial)
        return self.spec.leaf_of(top, xd)

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        nxt = self._step(u, xd)
        _n, v0 = self._layout()
        outs = [np.array([nxt[v0 + k]]) for k in range(len(self.spec.outputs))]
        if self.params["state_output"]:
            outs.append(np.array([float(self.active_state(nxt))]))
        return outs

    def update(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> Array:
        return self._step(u, xd)

    def _transition_label(self, k: int) -> str:
        t = self.spec.transitions[k]
        return f"{self.spec.states[t.source].name} -> {self.spec.states[t.target].name} ({t.guard_src})"

    def coverage_outcomes(self) -> list[str]:
        return [f"state {st.name}" for st in self.spec.states] + [
            self._transition_label(k) for k in range(len(self.spec.transitions))
        ]

    def coverage_hits(self, t: float, u: Sequence[Array], y: Sequence[Array], xd: Array) -> list[str]:
        n, _v0 = self._layout()
        before = {k for k in range(n) if xd[k]}
        after = self._step(u, xd)
        hits = [f"state {self.spec.states[k].name}" for k in sorted(before | {k for k in range(n) if after[k]})]
        return hits + [self._transition_label(k) for k in self._interp.fired]
