"""Circuit analyses in the spirit of SPICE: DC operating point and DC sweep.

Transient analysis is an ordinary simulation (``simulate``); AC small-signal analysis is the Linear Analysis
(put an In block on a controlled source and an Out block on a sensor, then look at the Bode plot).

    from nervus.circuit import dc_sweep, operating_point

    op = operating_point(model)             # like .op: every node voltage, at the steady state
    op["Q1.c"]                              # node names are "block.port" of one port on the node
    sweep = dc_sweep(model, "Vin", "V", np.linspace(0, 5, 51), ["vout"])   # like .dc
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from nervus.core.diagram import Diagram
from nervus.core.errors import ModelError
from nervus.core.types import Array


@dataclass
class OperatingPointResult:
    """Node voltages (and other across values) of every physical network at the steady state."""

    nodes: dict[str, float] = field(default_factory=dict)
    """Across value at every physical port, by ``"block.port"`` (``"R1.n"``); the reference is 0."""
    signals: dict[str, float] = field(default_factory=dict)
    """Every block output at the steady state (sensors included)."""
    residual: float = 0.0

    def __getitem__(self, name: str) -> float:
        if name in self.nodes:
            return self.nodes[name]
        if name in self.signals:
            return self.signals[name]
        raise KeyError(f"no node or signal {name!r}; nodes: {', '.join(self.nodes)}")

    def table(self) -> str:
        width = max((len(n) for n in self.nodes), default=4)
        return "\n".join(f"{n:{width}s}  {v: .6g}" for n, v in self.nodes.items())


def operating_point(diagram: Diagram, x_guess: Array | None = None) -> OperatingPointResult:
    """The DC operating point: the steady state where no state changes (capacitors carry no current,
    inductors have no voltage). Like SPICE's ``.op``."""
    from nervus.analysis import trim
    from nervus.physical.network import NetworkCore
    from nervus.sim import compile_diagram

    op = trim(diagram, x_guess=x_guess)
    model = compile_diagram(diagram)
    model.reset()
    x = np.asarray(op.x, dtype=float)
    model.eval_outputs(0.0, x if x.size else model.xc0, frozenset(range(len(model.rates))), init=True)
    result = OperatingPointResult(residual=op.residual)
    for i, b in enumerate(model.blocks):
        for p, y in enumerate(model.y[i]):
            if y.size == 1:
                result.signals[model.names[i] if p == 0 else f"{model.names[i]}:{p}"] = float(y[0])
        if isinstance(b, NetworkCore):
            net = b.network
            u = np.array([float(np.ravel(model.y[s][p])[0]) for s, p in model.inputs[i]])
            sol = net.solve(x[model.xc_slices[i]], u)
            for name, node in net.port_nodes.items():
                result.nodes[name] = sol.across(node)
    return result


@dataclass
class DcSweep:
    values: Array
    """The swept parameter values."""
    signals: dict[str, Array]
    """Each measured signal (or node) at every value."""

    def __getitem__(self, name: str) -> Array:
        return self.signals[name]


def dc_sweep(diagram: Diagram, block: str, param: str, values: Sequence[float], measure: Sequence[str]) -> DcSweep:
    """Operating points while a parameter of one block (e.g. the voltage *V* of a source) steps through
    ``values``; ``measure`` names sensor signals or nodes. Like SPICE's ``.dc``. Each point is an independent
    operating point (the model is compiled again, so the Newton iteration does not start from the previous point):
    a circuit with several stable states (a latch, a Schmitt trigger) shows one of them, not a hysteresis loop."""
    target = dict(diagram.walk()).get(block)
    if target is None:
        raise ModelError(f"no block {block!r}")
    original = target.param_source(param)
    out: dict[str, list[float]] = {m: [] for m in measure}
    try:
        for v in values:
            target._assign(target.param_spec(param), float(v))
            target.validate_params()
            op = operating_point(diagram)
            for m in measure:
                out[m].append(op[m])
    finally:
        target._assign(target.param_spec(param), original)
    return DcSweep(np.asarray(values, dtype=float), {k: np.asarray(v) for k, v in out.items()})
