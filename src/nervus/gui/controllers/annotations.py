"""What View ▸ Show Data Types / Show Signal Widths / Sample Time Colours show, worked out from the compiled model.

The view only decides how it looks (the colour of each sample time)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nervus.gui.models.diagram_model import DiagramModel


@dataclass
class SignalAnnotations:
    labels: dict[tuple[str, int], str] = field(default_factory=dict)
    """(block path, output port) -> the text on its wire, e.g. ``int16 [3]``."""
    wide: set[tuple[str, int]] = field(default_factory=set)
    """Outputs that carry vectors (drawn as wide wires)."""
    rate_of: dict[str, Any] = field(default_factory=dict)
    """Block path -> its sample time: a discrete rate of the compiled model, or ``"constant"``. Continuous blocks
    are left out (they keep the normal colour)."""
    rates: list[Any] = field(default_factory=list)
    """The discrete rates of the model, fastest first."""
    legend: list[tuple[str, Any]] = field(default_factory=list)
    """(text, key) for the legend; the key is a rate, ``"continuous"`` or ``"constant"``."""


def signal_annotations(model: DiagramModel, types: bool, widths: bool, rates: bool) -> SignalAnnotations:
    """Compile the model and collect the annotations that are switched on. Raises
    :class:`~nervus.core.NervusError` if the model does not compile."""
    from nervus.sim import compile_diagram
    from nervus.sim.compiler import Kind

    m = compile_diagram(model.root)
    out = SignalAnnotations(rates=list(m.rates))
    for i, ts in enumerate(m.out_types):
        paths = {m.names[i], model.path_of(m.origin[i])}  # In blocks in subsystems: both
        for p, t in enumerate(ts):
            parts = []
            if types and not t.is_double:
                parts.append(t.short_name)
            width = m.out_widths[i][p]
            if widths and width > 1:
                parts.append(f"[{width}]")
                out.wide.update((path, p) for path in paths)
            if parts:
                out.labels.update({(path, p): " ".join(parts) for path in paths})
        if rates:
            kind = m.kinds[i]
            key: Any = (
                "constant" if kind is Kind.CONSTANT else m.rates[m.rate_index[i]] if kind is Kind.DISCRETE else None
            )
            if key is not None:
                out.rate_of.update({path: key for path in paths})
    for r in m.rates:
        name = f"{r.period * 1000:g} ms" if r.period < 1 else f"{r.period:g} s"
        out.legend.append((name + (f" (offset {r.offset:g} s)" if r.offset else ""), r))
    if any(k is Kind.CONTINUOUS for k in m.kinds):
        out.legend.append(("continuous", "continuous"))
    if any(k is Kind.CONSTANT for k in m.kinds):
        out.legend.append(("constant", "constant"))
    return out
