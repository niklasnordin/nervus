"""The :class:`Diagram`: a set of named blocks and the connections between their ports."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any, TypeVar

from nervus.core.block import Block, InPort, OutPort
from nervus.core.errors import ModelError, ParameterError
from nervus.core.settings import SimulationSettings
from nervus.core.workspace import Workspace

B = TypeVar("B", bound=Block)

SourceLike = Block | OutPort | tuple[Block, int]
DestLike = Block | InPort | tuple[Block, int]


@dataclass(frozen=True, eq=False)
class Connection:
    """A signal line from an output port to an input port."""

    src: Block
    src_port: int
    dst: Block
    dst_port: int
    meta: dict[str, Any] = field(default_factory=dict)
    """Drawing data, e.g. ``points``: manually placed corners of the wire."""

    def __repr__(self) -> str:
        return f"<Connection {self.src.name}[{self.src_port}] -> {self.dst.name}[{self.dst_port}]>"


@dataclass(frozen=True, eq=False)
class Link:
    """A physical connection between two ports of physical blocks (an electrical wire, a shaft, a heat path).

    Unlike a signal connection it has no direction: the ports share one node of the physical network."""

    a: Block
    a_port: str
    b: Block
    b_port: str
    meta: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"<Link {self.a.name}.{self.a_port} — {self.b.name}.{self.b_port}>"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """A problem found by :meth:`Diagram.check`."""

    severity: Severity
    message: str
    blocks: tuple[str, ...] = ()


class Diagram:
    """A block diagram.

    Blocks have unique names inside a diagram. Each input port is driven by at most one connection;
    an output port can drive any number of inputs (fan-out).
    """

    def __init__(self, name: str = "untitled") -> None:
        self.name = name
        self.meta: dict[str, Any] = {}
        """Free-form metadata (e.g. GUI view state). Saved in model files."""
        self.settings = SimulationSettings()
        """Default time span and solver used by :func:`nervus.simulate`."""
        self.workspace = Workspace()
        """Named parameters that block parameters can refer to (``Gain(gain="Kp")``). Only the top-level
        diagram's workspace is used; subsystems see their model's workspace."""
        from nervus.core.dictionary import DataDictionary
        from nervus.core.requirements import Requirements

        self.requirements = Requirements()
        """Requirements that blocks (``meta["requirements"]``) and test cases (``requirements``) are linked to."""
        self.signals = DataDictionary()
        """Signal objects (data type, unit, range) that named block outputs are bound to; the top-level
        diagram's is used."""
        self.source_path: str | None = None
        self.link_warnings: list[str] = []
        """Problems updating linked library subsystems when the model was loaded."""
        """The file this model was loaded from or saved to (used to resolve relative file paths)."""
        self.tests: list[Any] = []
        """Test cases (:class:`nervus.core.testcase.TestCase`) stored with the model."""
        self.parent: Block | None = None
        """The Subsystem block that contains this diagram (``None`` for a model)."""
        self._blocks: dict[str, Block] = {}
        self._connections: list[Connection] = []
        self._links: list[Link] = []

    # ------------------------------------------------------------------ hierarchy

    @property
    def root(self) -> Diagram:
        """The top-level diagram (the model) this diagram belongs to."""
        d = self
        while d.parent is not None and d.parent.diagram is not None:
            d = d.parent.diagram
        return d

    @property
    def path(self) -> str:
        """``""`` for the model, ``"sub/inner"`` for nested subsystems."""
        parts: list[str] = []
        d = self
        while d.parent is not None:
            parts.append(d.parent.name)
            if d.parent.diagram is None:
                break
            d = d.parent.diagram
        return "/".join(reversed(parts))

    def subsystems(self) -> list[Block]:
        return [b for b in self._blocks.values() if hasattr(b, "system")]

    def walk(self) -> Iterator[tuple[str, Block]]:
        """All blocks in this diagram and in nested subsystems, with path names (``"sub/gain"``)."""
        for b in self._blocks.values():
            yield b.name, b
            system = getattr(b, "system", None)
            if isinstance(system, Diagram):
                for name, inner in system.walk():
                    yield f"{b.name}/{name}", inner

    def usages(self, name: str) -> list[tuple[str, str]]:
        """Where the workspace variable ``name`` is used, as ``(block path, what)`` pairs: block parameters,
        chart ``param`` declarations, mask variables, variant conditions and other workspace variables
        (path ``""`` for this diagram's workspace). Subsystems whose mask defines ``name`` themselves are skipped
        inside (they use their own variable)."""
        from nervus.core.expressions import names_in

        out: list[tuple[str, str]] = []
        for var in self.workspace.variables:
            if var.name != name and isinstance(var.value, str) and name in names_in(var.value):
                out.append(("", f"workspace variable {var.name} = {var.value}"))
        self._usages(name, "", out)
        return out

    def _usages(self, name: str, prefix: str, out: list[tuple[str, str]]) -> None:
        from nervus.core.expressions import names_in

        for b in self._blocks.values():
            path = prefix + b.name
            for pname, expr in b.expressions.items():
                if name in names_in(expr):
                    out.append((path, f"{pname} = {expr}"))
            spec = getattr(b, "spec", None)
            if name in getattr(spec, "params", ()):
                out.append((path, f"chart param {name}"))
            mask = getattr(b, "mask", None)
            if isinstance(mask, Workspace):
                for var in mask.variables:
                    if var.name != name and isinstance(var.value, str) and name in names_in(var.value):
                        out.append((path, f"mask variable {var.name} = {var.value}"))
                if name in mask:
                    continue  # inside, the name means the mask's own variable
            variants = getattr(b, "variants", None) or {}
            for vname, variant in variants.items():
                if variant.condition and name in names_in(variant.condition):
                    out.append((path, f"variant {vname}: {variant.condition}"))
            systems = [v.system for v in variants.values()] or [getattr(b, "system", None)]
            for system in systems:
                if isinstance(system, Diagram):
                    system._usages(name, path + "/", out)

    # ------------------------------------------------------------------ blocks

    @property
    def blocks(self) -> list[Block]:
        return list(self._blocks.values())

    def __iter__(self) -> Iterator[Block]:
        return iter(list(self._blocks.values()))

    def __len__(self) -> int:
        return len(self._blocks)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return item in self._blocks
        return isinstance(item, Block) and self._blocks.get(item.name) is item

    def block(self, name: str) -> Block:
        try:
            return self._blocks[name]
        except KeyError:
            raise KeyError(f"no block named {name!r} in diagram {self.name!r}") from None

    def add(self, block: B, name: str | None = None) -> B:
        """Add ``block`` to the diagram and return it. A unique name is generated if none is given."""
        if block.diagram is not None:
            raise ModelError(f"block {block.name!r} already belongs to a diagram", (block.name,))
        if name is not None:
            block.name = name
        if not block.name:
            block.name = self.unique_name(block.label or type(block).__name__)
        if block.name in self._blocks:
            raise ModelError(f"a block named {block.name!r} already exists", (block.name,))
        self._blocks[block.name] = block
        block.diagram = self
        if block.expressions:
            with contextlib.suppress(Exception):  # unresolved variables are reported by check()
                block.resolve(self.namespace())
        return block

    def namespace(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Variables visible to blocks in this diagram: the model workspace, plus the mask parameters of every
        enclosing masked subsystem (inner masks shadow outer names)."""
        if self.parent is None or self.parent.diagram is None:
            return self.root.workspace.namespace(overrides)
        outer = self.parent.diagram.namespace(overrides)
        mask = getattr(self.parent, "mask", None)
        if mask is None or len(mask) == 0:
            return outer
        try:
            return mask.namespace(base=outer)
        except ModelError as exc:
            raise ModelError(f"mask of {self.parent.name!r}: {exc}", (self.parent.name,)) from exc

    def name_signal(self, block: Block | str, port: int, name: str | None) -> None:
        """Give output ``port`` of ``block`` a signal name (bound to the data dictionary object of that name, if
        there is one); ``None`` or ``""`` removes the name."""
        from nervus.core.dictionary import META_KEY
        from nervus.core.errors import ParameterError

        b = self.block(block) if isinstance(block, str) else block
        if not 0 <= port < b.num_outputs():
            raise ParameterError(f"{b.name} has no output {port}")
        names = dict(b.meta.get(META_KEY, {}))
        if name:
            if not name.isidentifier():
                raise ParameterError(f"invalid signal name {name!r}: use letters, digits and _")
            from nervus.core.dictionary import named_signals

            taken = named_signals(self.root).get(name)
            path = next((q for q, x in self.root.walk() if x is b), b.name)
            if taken is not None and taken != (path, port):
                raise ParameterError(f"signal name {name!r} is already used by {taken[0]}:{taken[1]}")
            names[str(port)] = name
        else:
            names.pop(str(port), None)
        if names:
            b.meta[META_KEY] = names
        else:
            b.meta.pop(META_KEY, None)

    def walk_namespaces(
        self, overrides: dict[str, Any] | None = None, prefix: str = ""
    ) -> Iterator[tuple[str, Block, dict[str, Any]]]:
        """Every block (with path) together with the namespace its parameters are evaluated in."""
        ns = self.namespace(overrides)
        for b in self._blocks.values():
            yield prefix + b.name, b, ns
            system = getattr(b, "system", None)
            if isinstance(system, Diagram):
                yield from system.walk_namespaces(overrides, prefix + b.name + "/")

    def resolve_parameters(self, overrides: dict[str, Any] | None = None) -> None:
        """Evaluate every block's parameter expressions with the workspace and masks (plus ``overrides``).
        Raises :class:`ModelError` naming the block on failure."""
        for name, b, ns in self.walk_namespaces(overrides):
            try:
                b.resolve(ns)
            except ParameterError as exc:
                raise ModelError(f"{name}: {exc}", (name,)) from exc

    def unique_name(self, base: str) -> str:
        """Return ``base`` or ``base1``, ``base2``, ... whichever is not taken yet."""
        if base not in self._blocks:
            return base
        stem = re.sub(r"\d+$", "", base) or base
        i = 1
        while f"{stem}{i}" in self._blocks:
            i += 1
        return f"{stem}{i}"

    def remove(self, block: Block | str) -> list[Connection]:
        """Remove a block and every connection touching it. Returns the removed connections."""
        b = self._resolve(block)
        removed = [c for c in self._connections if c.src is b or c.dst is b]
        self._connections = [c for c in self._connections if c.src is not b and c.dst is not b]
        self._links = [k for k in self._links if k.a is not b and k.b is not b]
        del self._blocks[b.name]
        b.diagram = None
        return removed

    def rename(self, block: Block | str, new_name: str) -> None:
        b = self._resolve(block)
        if not new_name:
            raise ModelError("block name cannot be empty", (b.name,))
        if new_name == b.name:
            return
        if new_name in self._blocks:
            raise ModelError(f"a block named {new_name!r} already exists", (b.name,))
        # Rebuild the dict to keep insertion order.
        self._blocks = {(new_name if k == b.name else k): v for k, v in self._blocks.items()}
        b.name = new_name

    def _resolve(self, block: Block | str) -> Block:
        b = self.block(block) if isinstance(block, str) else block
        if b not in self:
            raise ModelError(f"block {b.name!r} is not part of diagram {self.name!r}", (b.name,))
        return b

    # ------------------------------------------------------------------ connections

    @property
    def connections(self) -> list[Connection]:
        return list(self._connections)

    def connect(self, src: SourceLike, dst: DestLike) -> Connection:
        """Connect an output port to an input port.

        ``src`` may be a block (meaning output port 0), an :class:`OutPort` or a ``(block, index)`` tuple;
        likewise ``dst`` for input ports.
        """
        if isinstance(src, Block):
            src = src.out_port(0)
        elif isinstance(src, tuple):
            src = src[0].out_port(src[1])
        if isinstance(dst, Block):
            dst = dst.in_port(0)
        elif isinstance(dst, tuple):
            dst = dst[0].in_port(dst[1])
        for b in (src.block, dst.block):
            if b not in self:
                raise ModelError(f"block {b.name!r} is not part of diagram {self.name!r}", (b.name,))
        existing = self.driver(dst.block, dst.index)
        if existing is not None:
            raise ModelError(
                f"input {dst.index} of {dst.block.name!r} is already driven by {existing.src.name!r}",
                (dst.block.name,),
            )
        conn = Connection(src.block, src.index, dst.block, dst.index)
        self._connections.append(conn)
        return conn

    def chain(self, *blocks: Block) -> list[Connection]:
        """Connect output 0 of each block to input 0 of the next."""
        return [self.connect(a, b) for a, b in pairwise(blocks)]

    def disconnect(self, conn: Connection) -> None:
        self._connections.remove(conn)

    def reconnect(self, conn: Connection) -> Connection:
        """Put back a connection that was removed, as the same object (with its drawing data), so references
        to it stay valid, e.g. in an undo history. It is checked like a new connection."""
        self.connect((conn.src, conn.src_port), (conn.dst, conn.dst_port))
        self._connections[-1] = conn
        return conn

    # ------------------------------------------------------------------ physical links

    @property
    def links(self) -> list[Link]:
        return list(self._links)

    def link(self, a: tuple[Block, str], b: tuple[Block, str]) -> Link:
        """Join two physical ports, e.g. ``d.link((battery, "p"), (resistor, "p"))``. A port may be linked to
        several others: they all share one node."""
        for block, port in (a, b):
            if block not in self:
                raise ModelError(f"block {block.name!r} is not part of diagram {self.name!r}", (block.name,))
            names = [n for n, _dom in getattr(block, "physical_ports", lambda: [])()]
            if port not in names:
                raise ModelError(
                    f"{block.name!r} has no physical port {port!r}"
                    + (f" (ports: {', '.join(names)})" if names else ""),
                    (block.name,),
                )
        doms = [dict(blk.physical_ports())[port] for blk, port in (a, b)]
        if doms[0] != doms[1]:
            raise ModelError(
                f"cannot link a {doms[0]} port ({a[0].name}.{a[1]}) to a {doms[1]} port ({b[0].name}.{b[1]})",
                (a[0].name, b[0].name),
            )
        link = Link(a[0], a[1], b[0], b[1])
        self._links.append(link)
        return link

    def unlink(self, link: Link) -> None:
        self._links.remove(link)

    def relink(self, link: Link) -> Link:
        """Put back a link that was removed, as the same object (see :meth:`reconnect`)."""
        self.link((link.a, link.a_port), (link.b, link.b_port))
        self._links[-1] = link
        return link

    def driver(self, block: Block, in_port: int) -> Connection | None:
        """The connection driving ``block``'s input ``in_port``, if any."""
        for c in self._connections:
            if c.dst is block and c.dst_port == in_port:
                return c
        return None

    def fanout(self, block: Block, out_port: int) -> list[Connection]:
        return [c for c in self._connections if c.src is block and c.src_port == out_port]

    # ------------------------------------------------------------------ checks

    def check(self) -> list[Issue]:
        """Find structural problems without compiling the model (including inside subsystems)."""
        issues: list[Issue] = []
        try:
            ns = self.root.workspace.namespace()
        except ModelError as exc:
            issues.append(Issue(Severity.ERROR, str(exc)))
            ns = None
        if ns is not None and self.parent is None:
            try:
                for name, b, block_ns in self.walk_namespaces():
                    try:
                        b.resolve(block_ns)
                    except ParameterError as exc:
                        issues.append(Issue(Severity.ERROR, f"{name}: {exc}", (name,)))
            except ModelError as exc:
                issues.append(Issue(Severity.ERROR, str(exc), exc.blocks))
            for msg in self.workspace.check_ranges(ns):
                issues.append(Issue(Severity.WARNING, f"workspace: {msg}"))
        issues.extend(self._check_structure(""))
        return issues

    def _check_structure(self, prefix: str) -> list[Issue]:
        """Connection and port problems in this diagram and its subsystems; names are prefixed paths."""
        issues: list[Issue] = []
        for sub in self.subsystems():
            path = prefix + sub.name
            try:
                sub.validate_ports()  # type: ignore[attr-defined]
            except ParameterError as exc:
                issues.append(Issue(Severity.ERROR, f"{path}: {exc}", (path,)))
            link = getattr(sub, "library_link", None)
            if link is not None:
                base = Path(self.root.source_path).parent if self.root.source_path else None
                if not link.resolve(base).exists():
                    issues.append(
                        Issue(
                            Severity.WARNING,
                            f"{path}: library {link.library} not found; using the copy saved in the model",
                            (path,),
                        )
                    )
            issues.extend(sub.system._check_structure(path + "/"))  # type: ignore[attr-defined]
        for c in self._connections:
            src, dst = prefix + c.src.name, prefix + c.dst.name
            if c.src_port >= c.src.num_outputs():
                issues.append(
                    Issue(
                        Severity.ERROR, f"{src!r} has no output port {c.src_port} (connection is dangling)", (src, dst)
                    )
                )
            if c.dst_port >= c.dst.num_inputs():
                issues.append(
                    Issue(
                        Severity.ERROR, f"{dst!r} has no input port {c.dst_port} (connection is dangling)", (src, dst)
                    )
                )
        for b in self._blocks.values():
            path = prefix + b.name
            for i in range(b.num_inputs()):
                if self.driver(b, i) is None:
                    issues.append(Issue(Severity.ERROR, f"input {i} of {path!r} is not connected", (path,)))
            for o in range(b.num_outputs()):
                if not self.fanout(b, o):
                    issues.append(Issue(Severity.WARNING, f"output {o} of {path!r} is not connected", (path,)))
        return issues

    def __repr__(self) -> str:
        return f"<Diagram {self.name!r}: {len(self._blocks)} blocks, {len(self._connections)} connections>"
