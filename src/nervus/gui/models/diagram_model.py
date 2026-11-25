"""Qt model wrapping a core :class:`~nervus.core.Diagram`.

All edits from the GUI go through this class, so it can tell the views what changed. The methods are
*primitive* operations without undo; controllers wrap them in undo commands.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPointF, Signal

from nervus.core import Block, Connection, Diagram, Link, SimulationSettings, Variable
from nervus.core.layout import auto_layout


def block_position(block: Block) -> QPointF:
    x, y = block.meta.get("position", (0.0, 0.0))
    return QPointF(float(x), float(y))


class DiagramModel(QObject):
    blockAdded = Signal(object)  # Block
    blockRemoved = Signal(object)  # Block
    blockMoved = Signal(object)  # Block
    blockRenamed = Signal(object, str)  # Block, old name
    paramsChanged = Signal(object)  # Block (parameters changed; ports may have changed too)
    connectionAdded = Signal(object)  # Connection
    connectionRemoved = Signal(object)  # Connection
    connectionChanged = Signal(object)  # Connection (its drawn route changed)
    linkAdded = Signal(object)  # Link (a physical connection)
    linkRemoved = Signal(object)  # Link
    settingsChanged = Signal()
    workspaceChanged = Signal()
    signalsChanged = Signal()  # the data dictionary (signal objects)
    requirementsChanged = Signal()
    testsChanged = Signal()
    diagramReset = Signal()  # a whole new diagram was loaded
    currentChanged = Signal(object)  # Diagram: the editor now shows this (sub)system
    filePathChanged = Signal(object)  # Path | None
    nameChanged = Signal(str)  # the model's name
    changed = Signal()  # any structural or parameter change

    def __init__(self, diagram: Diagram | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root = diagram if diagram is not None else Diagram()
        self._diagram = self._root  # the (sub)system shown in the editor
        self._path: Path | None = None
        for sig in (
            self.blockAdded,
            self.blockRemoved,
            self.blockMoved,
            self.blockRenamed,
            self.paramsChanged,
            self.connectionAdded,
            self.connectionRemoved,
            self.linkAdded,
            self.linkRemoved,
            self.settingsChanged,
            self.workspaceChanged,
            self.testsChanged,
            self.diagramReset,
        ):
            sig.connect(self.changed)

    # ------------------------------------------------------------------ access

    @property
    def diagram(self) -> Diagram:
        """The diagram currently shown in the editor (the model, or a subsystem inside it)."""
        return self._diagram

    @property
    def root(self) -> Diagram:
        """The model: owns the settings, the workspace, and what is saved and simulated."""
        return self._root

    def show(self, diagram: Diagram) -> None:
        """Show ``diagram`` (the model or one of its subsystems) in the editor."""
        if diagram is not self._diagram:
            self._diagram = diagram
            self.currentChanged.emit(diagram)

    def enter(self, subsystem: Block) -> None:
        system = getattr(subsystem, "system", None)
        if isinstance(system, Diagram):
            self.show(system)

    def up(self) -> None:
        parent = self._diagram.parent
        if parent is not None and parent.diagram is not None:
            self.show(parent.diagram)

    def breadcrumbs(self) -> list[tuple[str, Diagram]]:
        """(label, diagram) from the model down to the current subsystem."""
        crumbs: list[tuple[str, Diagram]] = []
        d: Diagram | None = self._diagram
        while d is not None:
            label = d.parent.name if d.parent is not None else (self._root.name or "model")
            crumbs.append((label, d))
            d = d.parent.diagram if d.parent is not None else None
        return list(reversed(crumbs))

    def path_of(self, block: Block) -> str:
        """Full path name of a block, as used in simulation results and diagnostics."""
        prefix = block.diagram.path if block.diagram is not None else ""
        return f"{prefix}/{block.name}" if prefix else block.name

    def _focus(self, diagram: Diagram | None) -> None:
        if diagram is not None and diagram is not self._diagram:
            self.show(diagram)

    @property
    def file_path(self) -> Path | None:
        return self._path

    @file_path.setter
    def file_path(self, path: Path | None) -> None:
        self._path = path
        self.filePathChanged.emit(path)

    def set_name(self, name: str) -> None:
        """Rename the model (not undoable: done when an untitled model is first saved, from its file name)."""
        if name != self._root.name:
            self._root.name = name
            self.nameChanged.emit(name)

    @property
    def title(self) -> str:
        return self._path.name if self._path else f"{self._root.name}"

    width_of: Callable[[Block], float] | None = None
    """The drawn width of a block, for placing blocks that have no position yet. The canvas (a view) sets it, so
    this model does not depend on how blocks are drawn; without a canvas, a default width is used."""

    keeping_history = False
    """True while :meth:`set_diagram` replaces the model as part of an undoable edit (the undo history stays)."""

    def set_diagram(self, diagram: Diagram, path: Path | None = None, keep_history: bool = False) -> None:
        auto_layout(diagram, width_of=self.width_of)  # diagrams built in Python have no positions yet
        for _name, b in diagram.walk():
            system = getattr(b, "system", None)
            if isinstance(system, Diagram):
                auto_layout(system, width_of=self.width_of)
        self._root = diagram
        self._diagram = diagram
        self.keeping_history = keep_history
        try:
            self.diagramReset.emit()
        finally:
            self.keeping_history = False
        self.file_path = path

    # ------------------------------------------------------------------ primitive edits

    def add_block(self, block: Block, position: QPointF | None = None, diagram: Diagram | None = None) -> Block:
        target = diagram if diagram is not None else self._diagram
        self._focus(target)
        if position is not None:
            block.meta["position"] = [round(position.x(), 1), round(position.y(), 1)]
        target.add(block)
        self.blockAdded.emit(block)
        return block

    def remove_block(self, block: Block) -> list[Connection | Link]:
        """Remove ``block`` with its connections and links. Returns what was removed with it (for undo)."""
        d = block.diagram
        assert d is not None
        self._focus(d)
        conns: list[Connection | Link] = [c for c in d.connections if c.src is block or c.dst is block]
        for c in conns:
            self.remove_connection(c)  # type: ignore[arg-type]
        links = [k for k in d.links if k.a is block or k.b is block]
        for k in links:
            self.remove_link(k)
        d.remove(block)
        self.blockRemoved.emit(block)
        return conns + links

    def add_link(self, a: Block, a_port: str, b: Block, b_port: str) -> Link:
        d = a.diagram
        assert d is not None
        self._focus(d)
        link = d.link((a, a_port), (b, b_port))
        self.linkAdded.emit(link)
        return link

    def restore_link(self, link: Link) -> Link:
        """Put back a removed link as the same object, so undo commands that hold it stay valid."""
        d = link.a.diagram
        assert d is not None
        self._focus(d)
        d.relink(link)
        self.linkAdded.emit(link)
        return link

    def remove_link(self, link: Link) -> None:
        d = link.a.diagram
        assert d is not None
        self._focus(d)
        d.unlink(link)
        self.linkRemoved.emit(link)

    def move_block(self, block: Block, position: QPointF) -> None:
        self._focus(block.diagram)
        block.meta["position"] = [round(position.x(), 1), round(position.y(), 1)]
        self.blockMoved.emit(block)

    def set_wire_points(self, conn: Connection, points: list[list[float]] | None) -> None:
        """Manually placed corners of a wire (``None``: route it automatically)."""
        self._focus(conn.dst.diagram)
        if points:
            conn.meta["points"] = [[float(x), float(y)] for x, y in points]
        else:
            conn.meta.pop("points", None)
        self.connectionChanged.emit(conn)

    def rename_block(self, block: Block, new_name: str) -> None:
        self._focus(block.diagram)
        old = block.name
        assert block.diagram is not None
        block.diagram.rename(block, new_name)
        self.blockRenamed.emit(block, old)

    def set_param(self, block: Block, name: str, value: Any) -> None:
        self._focus(block.diagram)
        block.set_param(name, value)
        self.paramsChanged.emit(block)

    def set_params(self, block: Block, params: dict[str, Any]) -> None:
        """Set several parameters at once; all-or-nothing."""
        self._focus(block.diagram)
        block.set_params(params)
        self.paramsChanged.emit(block)

    def add_connection(self, src: Block, src_port: int, dst: Block, dst_port: int) -> Connection:
        d = dst.diagram
        assert d is not None
        self._focus(d)
        conn = d.connect((src, src_port), (dst, dst_port))
        self.connectionAdded.emit(conn)
        return conn

    def restore_connection(self, conn: Connection) -> Connection:
        """Put back a removed connection as the same object, with its wire corners, so undo commands that hold
        it stay valid."""
        d = conn.dst.diagram
        assert d is not None
        self._focus(d)
        d.reconnect(conn)
        self.connectionAdded.emit(conn)
        return conn

    def remove_connection(self, conn: Connection) -> None:
        d = conn.dst.diagram
        assert d is not None
        self._focus(d)
        d.disconnect(conn)
        self.connectionRemoved.emit(conn)

    def find_connection(self, src: Block, src_port: int, dst: Block, dst_port: int) -> Connection | None:
        d = dst.diagram
        for c in d.connections if d is not None else []:
            if c.src is src and c.src_port == src_port and c.dst is dst and c.dst_port == dst_port:
                return c
        return None

    # ------------------------------------------------------------------ workspace

    def set_variable(self, old_name: str | None, var: Variable | None, index: int | None = None) -> None:
        """Add (``old_name`` None, optionally at ``index``), replace/rename, or delete (``var`` None) a
        workspace variable."""
        ws = self._root.workspace
        if var is None:
            if old_name is not None:
                del ws[old_name]
        elif old_name is not None:
            ws.replace(old_name, var)
        elif index is not None:
            ws.insert(index, var)
        else:
            ws.add(var)
        self._workspace_changed()

    def set_signal_object(self, old_name: str | None, obj: Any, index: int | None = None) -> None:
        """Add, replace/rename or delete (``obj`` None) a data-dictionary signal object."""
        dd = self._root.signals
        if obj is None:
            if old_name is not None and old_name in dd:
                dd.remove(old_name)
        else:
            dd.replace(old_name or obj.name, obj, index)
        self.signalsChanged.emit()
        self.changed.emit()

    def set_requirements(self, reqs: Any) -> None:
        self._root.requirements = reqs.copy()
        self.requirementsChanged.emit()
        self.changed.emit()

    def apply_values(self, values: dict[str, object]) -> None:
        self._root.workspace.apply(values)
        self._workspace_changed()

    def _workspace_changed(self) -> None:
        ns = None
        with contextlib.suppress(Exception):
            ns = self._root.workspace.namespace()
        for _name, b in self._root.walk():
            if b.expressions:
                if ns is not None:
                    with contextlib.suppress(Exception):
                        b.resolve(ns)
                self.paramsChanged.emit(b)
        self.workspaceChanged.emit()

    def set_meta(self, block: Block, changes: dict[str, Any]) -> None:
        """Change drawing-only entries of ``block.meta`` (orientation, size, flipped, signal names, ...); ``None``
        or ``False`` removes the key."""
        for k, v in changes.items():
            if v is None or v is False:
                block.meta.pop(k, None)
            else:
                block.meta[k] = v
        self.paramsChanged.emit(block)

    def set_mask(self, subsystem: Block, variables: list[dict[str, Any]], icon: str, description: str) -> None:
        """Replace a subsystem's mask (its parameters, icon text and description)."""
        from nervus.core.workspace import Workspace

        sub: Any = subsystem
        sub.mask = Workspace.from_list(list(variables))
        sub.mask_icon = str(icon)
        sub.mask_description = str(description)
        self.mask_changed(subsystem)

    def set_variants(self, subsystem: Block, variants: dict[str, Any], active: str, system: Diagram) -> None:
        """Replace a subsystem's variants and choose the active one (no variants: ``system`` is its diagram)."""
        sub: Any = subsystem
        sub.variants = dict(variants)
        if sub.variants:
            for v in sub.variants.values():
                v.system.parent = sub
            sub.activate(active)
        else:
            sub.active_variant = ""
            sub.system = system
            sub.system.parent = sub
        self.variants_changed(subsystem)

    def set_library_link(self, subsystem: Block, link: Any) -> None:
        """Link a subsystem to a library component (a :class:`nervus.library.LibraryLink`), or unlink it (None)."""
        subsystem.library_link = link  # type: ignore[attr-defined]
        self.paramsChanged.emit(subsystem)

    def mask_changed(self, subsystem: Block) -> None:
        """A subsystem's mask changed: re-resolve parameters inside it and refresh the views."""
        with contextlib.suppress(Exception):
            for _name, b, ns in subsystem.system.walk_namespaces():  # type: ignore[attr-defined]
                if b.expressions:
                    b.resolve(ns)
        self.paramsChanged.emit(subsystem)

    def variants_changed(self, subsystem: Block) -> None:
        """A subsystem's variants or active variant changed. If the editor shows a diagram that is no longer
        active (inside an inactive variant), go back up to the subsystem."""
        d: Diagram | None = self._diagram
        while d is not None and d.parent is not None:
            owner = d.parent
            if getattr(owner, "variants", None) and getattr(owner, "system", None) is not d:
                if owner.diagram is not None:
                    self.show(owner.diagram)
                break
            d = owner.diagram
        self.paramsChanged.emit(subsystem)

    def set_tests(self, tests: list[Any]) -> None:
        self._root.tests = list(tests)
        self.testsChanged.emit()

    def set_settings(self, settings: SimulationSettings) -> None:
        settings.validate()
        self._root.settings = settings
        self.settingsChanged.emit()
