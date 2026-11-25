"""Undoable edit commands. Every change the user makes to a diagram is one of these."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand

from nervus.core import Block, Connection, Link, SimulationSettings, Variable
from nervus.gui.models.diagram_model import DiagramModel, block_position


class Command(QUndoCommand):
    """Base class. Subclasses implement ``do``/``revert``.

    Exceptions raised from Python inside ``redo()`` do not reach the caller of ``QUndoStack.push``, so they are
    caught here: the command stores the error and marks itself obsolete, which makes Qt drop it from the stack.
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.error: Exception | None = None

    def redo(self) -> None:
        self.error = None
        try:
            self.do()
        except Exception as exc:
            self.error = exc
            self.setObsolete(True)

    def undo(self) -> None:
        self.revert()

    def do(self) -> None:
        raise NotImplementedError

    def revert(self) -> None:
        raise NotImplementedError


class AddBlockCommand(Command):
    def __init__(self, model: DiagramModel, block: Block, position: QPointF) -> None:
        super().__init__(f"Add {block.label}")
        self.model, self.block, self.position = model, block, position
        self.target = model.diagram  # the (sub)system shown when the block was added

    def do(self) -> None:
        self.model.add_block(self.block, self.position, self.target)

    def revert(self) -> None:
        self.model.remove_block(self.block)


class RemoveCommand(Command):
    """Remove blocks and/or connections."""

    def __init__(self, model: DiagramModel, blocks: list[Block], connections: list[Connection | Link]) -> None:
        n = len(blocks) + len(connections)
        super().__init__(f"Delete {n} item{'s' if n != 1 else ''}")
        self.model = model
        self.blocks = list(blocks)
        self._homes = [b.diagram for b in blocks]
        block_set = {id(b) for b in blocks}

        def ends(c: Connection | Link) -> tuple[Block, Block]:
            return (c.a, c.b) if isinstance(c, Link) else (c.src, c.dst)

        # Connections removed together with their blocks are handled by remove_block.
        self.connections = [c for c in connections if not any(id(e) in block_set for e in ends(c))]
        self._removed: list[Connection | Link] = []
        self._positions: list[QPointF] = []

    def do(self) -> None:
        self._removed = []
        for c in self.connections:
            if isinstance(c, Link):
                self.model.remove_link(c)
            else:
                self.model.remove_connection(c)
            self._removed.append(c)
        self._positions = [block_position(b) for b in self.blocks]
        for b in self.blocks:
            self._removed.extend(self.model.remove_block(b))

    def revert(self) -> None:
        for b, pos, home in zip(self.blocks, self._positions, self._homes, strict=True):
            self.model.add_block(b, pos, home)
        restored: list[Connection | Link] = []
        for c in self._removed:
            restored.append(self.model.restore_link(c) if isinstance(c, Link) else self.model.restore_connection(c))
        # The diagram holds new Connection objects now; keep ours in sync for the next redo.
        mapping = dict(zip(map(id, self._removed), restored, strict=True))
        self.connections = [mapping.get(id(c), c) for c in self.connections]


class MoveBlocksCommand(Command):
    def __init__(self, model: DiagramModel, moves: list[tuple[Block, QPointF, QPointF]]) -> None:
        super().__init__(f"Move {len(moves)} block{'s' if len(moves) != 1 else ''}")
        self.model, self.moves = model, moves

    def do(self) -> None:
        for b, _old, new in self.moves:
            self.model.move_block(b, new)

    def revert(self) -> None:
        for b, old, _new in self.moves:
            self.model.move_block(b, old)


class SetParamsCommand(Command):
    """Change block parameters. Connections to ports that disappear (e.g. fewer Sum inputs) are removed."""

    def __init__(self, model: DiagramModel, block: Block, params: dict[str, Any]) -> None:
        super().__init__(f"Change {block.name}")
        self.model, self.block = model, block
        self.new = dict(params)
        self.old = {k: block.param_source(k) for k in params}
        self._dropped: list[Connection] = []

    def do(self) -> None:
        self.model.set_params(self.block, self.new)
        b = self.block
        self._dropped = [
            c
            for c in (b.diagram.connections if b.diagram is not None else [])
            if (c.dst is b and c.dst_port >= b.num_inputs()) or (c.src is b and c.src_port >= b.num_outputs())
        ]
        for c in self._dropped:
            self.model.remove_connection(c)

    def revert(self) -> None:
        self.model.set_params(self.block, self.old)
        self._dropped = [self.model.restore_connection(c) for c in self._dropped]


class RenameCommand(Command):
    def __init__(self, model: DiagramModel, block: Block, new_name: str) -> None:
        super().__init__(f"Rename {block.name} to {new_name}")
        self.model, self.block, self.old, self.new = model, block, block.name, new_name

    def do(self) -> None:
        self.model.rename_block(self.block, self.new)

    def revert(self) -> None:
        self.model.rename_block(self.block, self.old)


class ConnectCommand(Command):
    """Connect two ports. If the input is already driven, the old line is replaced."""

    def __init__(self, model: DiagramModel, src: Block, src_port: int, dst: Block, dst_port: int) -> None:
        super().__init__(f"Connect {src.name} to {dst.name}")
        self.model = model
        self.args = (src, src_port, dst, dst_port)
        self._conn: Connection | None = None
        self._replaced: Connection | None = None

    def do(self) -> None:
        src, sp, dst, dp = self.args
        assert dst.diagram is not None
        self._replaced = dst.diagram.driver(dst, dp)
        if self._replaced is not None:
            self.model.remove_connection(self._replaced)
        # On redo, put back the same connection: later commands (routing the wire) hold a reference to it.
        if self._conn is None:
            self._conn = self.model.add_connection(src, sp, dst, dp)
        else:
            self.model.restore_connection(self._conn)

    def revert(self) -> None:
        assert self._conn is not None
        self.model.remove_connection(self._conn)
        if self._replaced is not None:
            self._replaced = self.model.restore_connection(self._replaced)


class LinkCommand(Command):
    """Join two physical ports."""

    def __init__(self, model: DiagramModel, a: Block, a_port: str, b: Block, b_port: str) -> None:
        super().__init__(f"Link {a.name}.{a_port} to {b.name}.{b_port}")
        self.model, self.args = model, (a, a_port, b, b_port)
        self._link: Link | None = None

    def do(self) -> None:
        if self._link is None:
            self._link = self.model.add_link(*self.args)
        else:  # redo: the same link, see ConnectCommand
            self.model.restore_link(self._link)

    def revert(self) -> None:
        assert self._link is not None
        self.model.remove_link(self._link)


class SetSettingsCommand(Command):
    def __init__(self, model: DiagramModel, settings: SimulationSettings) -> None:
        super().__init__("Change simulation settings")
        self.model, self.new, self.old = model, settings, model.root.settings

    def do(self) -> None:
        self.model.set_settings(self.new)

    def revert(self) -> None:
        self.model.set_settings(self.old)


class SetVariableCommand(Command):
    """Add, edit, rename or delete one workspace variable."""

    def __init__(self, model: DiagramModel, old: Variable | None, new: Variable | None) -> None:
        verb = "Add" if old is None else "Delete" if new is None else "Change"
        super().__init__(f"{verb} variable {(new or old).name}")  # type: ignore[union-attr]
        self.model, self.old, self.new = model, old, new
        names = list(model.root.workspace)
        self._index = names.index(old.name) if old is not None else len(names)

    def do(self) -> None:
        self.model.set_variable(self.old.name if self.old else None, self.new)

    def revert(self) -> None:
        if self.new is None:  # undo a delete: put it back where it was
            self.model.set_variable(None, self.old, index=self._index)
        else:
            self.model.set_variable(self.new.name, self.old)


class ApplyValuesCommand(Command):
    """Apply a parameter set (several variable values at once)."""

    def __init__(self, model: DiagramModel, values: dict[str, object], label: str) -> None:
        super().__init__(f"Load parameter set {label}")
        self.model, self.new = model, dict(values)
        current = model.root.workspace.values()
        self.old = {k: current[k] for k in values if k in current}

    def do(self) -> None:
        self.model.apply_values(self.new)

    def revert(self) -> None:
        self.model.apply_values(self.old)


class CreateSubsystemCommand(Command):
    """Move the selected blocks into a new Subsystem; lines crossing the boundary become In/Out ports."""

    def __init__(self, model: DiagramModel, blocks: list[Block]) -> None:
        super().__init__(f"Create subsystem from {len(blocks)} blocks")
        self.model = model
        self.blocks = list(blocks)
        self.outer = blocks[0].diagram
        self.subsystem: Block | None = None
        self._saved: list[tuple[Block, int, Block, int]] = []
        self._positions: dict[int, list[float]] = {}

    def do(self) -> None:
        from nervus.blocks.ports import Inport, Outport, Subsystem
        from nervus.core import Diagram

        outer = self.outer
        assert outer is not None
        if any(b.diagram is not outer for b in self.blocks):
            raise ValueError("all selected blocks must be in the same diagram")
        sel = {id(b) for b in self.blocks}
        conns = [c for c in outer.connections if id(c.src) in sel or id(c.dst) in sel]
        self._saved = [(c.src, c.src_port, c.dst, c.dst_port) for c in conns]
        self._positions = {id(b): list(b.meta.get("position", [0.0, 0.0])) for b in self.blocks}
        xs = [p[0] for p in self._positions.values()]
        ys = [p[1] for p in self._positions.values()]
        x0, y0 = min(xs), min(ys)

        for b in self.blocks:
            self.model.remove_block(b)
        inner = Diagram("system")
        for b in self.blocks:
            p = self._positions[id(b)]
            b.meta["position"] = [p[0] - x0 + 160.0, p[1] - y0]
            inner.add(b)

        inputs: dict[tuple[int, int], int] = {}  # (id(src), port) -> subsystem input index
        outputs: dict[tuple[int, int], int] = {}
        outer_in: list[tuple[Block, int]] = []
        outer_out: list[tuple[int, Block, int]] = []
        for src, sp, dst, dp in self._saved:
            if id(src) in sel and id(dst) in sel:
                inner.connect((src, sp), (dst, dp))
            elif id(dst) in sel:  # crosses inward
                key = (id(src), sp)
                if key not in inputs:
                    inputs[key] = len(inputs)
                    in_block = inner.add(Inport(port=len(inputs)), name=inner.unique_name(f"In{len(inputs)}"))
                    in_block.meta["position"] = [0.0, 70.0 * (len(inputs) - 1)]
                    outer_in.append((src, sp))
                port_block = next(
                    b for b in inner.blocks if isinstance(b, Inport) and b.params["port"] == inputs[key] + 1
                )
                inner.connect(port_block, (dst, dp))
            else:  # crosses outward
                key = (id(src), sp)
                if key not in outputs:
                    outputs[key] = len(outputs)
                    out_block = inner.add(Outport(port=len(outputs)), name=inner.unique_name(f"Out{len(outputs)}"))
                    out_block.meta["position"] = [max(xs) - x0 + 340.0, 70.0 * (len(outputs) - 1)]
                    inner.connect((src, sp), out_block)
                outer_out.append((outputs[key], dst, dp))

        if self.subsystem is None:
            self.subsystem = Subsystem(outer.unique_name("Subsystem"), system=inner)
        else:  # redo: reuse the same block so later commands that refer to it keep working
            self.subsystem.system = inner  # type: ignore[attr-defined]
            inner.parent = self.subsystem
        sub = self.subsystem
        self.model.add_block(sub, QPointF(x0 + (max(xs) - x0) / 2, y0 + (max(ys) - y0) / 2), outer)
        for k, (src, sp) in enumerate(outer_in):
            self.model.add_connection(src, sp, sub, k)
        for k, dst, dp in outer_out:
            self.model.add_connection(sub, k, dst, dp)

    def revert(self) -> None:
        assert self.subsystem is not None and self.outer is not None
        self.model.remove_block(self.subsystem)
        inner = self.subsystem.system  # type: ignore[attr-defined]
        for b in self.blocks:
            inner.remove(b)
            b.meta["position"] = self._positions[id(b)]
            self.model.add_block(b, None, self.outer)
        for src, sp, dst, dp in self._saved:
            self.model.add_connection(src, sp, dst, dp)


class SetTestsCommand(Command):
    """Replace the model's list of test cases (add, edit, delete and reorder are all this command)."""

    def __init__(self, model: DiagramModel, tests: list[object], text: str) -> None:
        super().__init__(text)
        self.model = model
        self.new = [t.to_dict() for t in tests]  # type: ignore[attr-defined]
        self.old = [t.to_dict() for t in model.root.tests]

    def _apply(self, data: list[dict[str, object]]) -> None:
        from nervus.core.testcase import TestCase

        self.model.set_tests([TestCase.from_dict(d) for d in data])

    def do(self) -> None:
        self._apply(self.new)

    def revert(self) -> None:
        self._apply(self.old)


class SetMaskCommand(Command):
    """Change a subsystem's mask (parameters, icon text, description)."""

    def __init__(self, model: DiagramModel, subsystem: Block, new: dict[str, Any]) -> None:
        super().__init__(f"Edit mask of {subsystem.name}")
        self.model, self.subsystem = model, subsystem
        self.old = mask_state(subsystem)
        self.new = new

    def _apply(self, state: dict[str, Any]) -> None:
        self.model.set_mask(self.subsystem, list(state["variables"]), str(state["icon"]), str(state["description"]))

    def do(self) -> None:
        self._apply(self.new)

    def revert(self) -> None:
        self._apply(self.old)


def mask_state(subsystem: Block) -> dict[str, Any]:
    return {
        "variables": subsystem.mask.to_list(),  # type: ignore[attr-defined]
        "icon": subsystem.mask_icon,  # type: ignore[attr-defined]
        "description": subsystem.mask_description,  # type: ignore[attr-defined]
    }


def variant_state(subsystem: Block) -> dict[str, Any]:
    """Snapshot of a subsystem's variants (the diagrams themselves are shared, not copied)."""
    from nervus.blocks.ports import Variant

    return {
        "variants": {k: Variant(v.system, v.condition) for k, v in subsystem.variants.items()},  # type: ignore[attr-defined]
        "active": subsystem.active_variant,  # type: ignore[attr-defined]
        "system": subsystem.system,  # type: ignore[attr-defined]
    }


class SetVariantsCommand(Command):
    """Add, remove, rename or switch the variants of a subsystem."""

    def __init__(self, model: DiagramModel, subsystem: Block, new: dict[str, Any], text: str) -> None:
        super().__init__(text)
        self.model, self.subsystem = model, subsystem
        self.old = variant_state(subsystem)
        self.new = new

    def _apply(self, state: dict[str, Any]) -> None:
        self.model.set_variants(self.subsystem, state["variants"], state["active"], state["system"])

    def do(self) -> None:
        self._apply(self.new)

    def revert(self) -> None:
        self._apply(self.old)


class SetLinkCommand(Command):
    """Link a subsystem to a library component, or break the link (``link`` = None)."""

    def __init__(self, model: DiagramModel, subsystem: Block, link: Any, text: str) -> None:
        super().__init__(text)
        self.model, self.subsystem = model, subsystem
        self.old, self.new = getattr(subsystem, "library_link", None), link

    def _apply(self, link: Any) -> None:
        self.model.set_library_link(self.subsystem, link)

    def do(self) -> None:
        self._apply(self.new)

    def revert(self) -> None:
        self._apply(self.old)


class SetMetaCommand(Command):
    """Change drawing-only entries of ``block.meta`` (orientation, size): ``changes = [(block, {key: value})]``,
    where ``None`` removes the key."""

    def __init__(self, model: DiagramModel, text: str, changes: list[tuple[Block, dict[str, Any]]]) -> None:
        super().__init__(text)
        self.model = model
        self.new = [(b, dict(c)) for b, c in changes]
        self.old = [(b, {k: b.meta.get(k) for k in c}) for b, c in changes]

    @staticmethod
    def _apply(model: DiagramModel, changes: list[tuple[Block, dict[str, Any]]]) -> None:
        for b, c in changes:
            model.set_meta(b, c)

    def do(self) -> None:
        self._apply(self.model, self.new)

    def revert(self) -> None:
        self._apply(self.model, self.old)


class SetWirePointsCommand(Command):
    """Change the manually placed corners of wires: ``changes = [(connection, old points, new points)]``."""

    def __init__(
        self, model: DiagramModel, changes: list[tuple[Connection, Any, Any]], text: str = "Route wire"
    ) -> None:
        super().__init__(text)
        self.model, self.changes = model, changes

    def do(self) -> None:
        for conn, _old, new in self.changes:
            self.model.set_wire_points(conn, new)

    def revert(self) -> None:
        for conn, old, _new in self.changes:
            self.model.set_wire_points(conn, old)


class FlipBlocksCommand(Command):
    """Flip blocks left-right (inputs on the right, outputs on the left), e.g. for feedback paths."""

    def __init__(self, model: DiagramModel, blocks: list[Block]) -> None:
        super().__init__(f"Flip {len(blocks)} block{'s' if len(blocks) != 1 else ''}")
        self.model, self.blocks = model, list(blocks)

    def _toggle(self) -> None:
        for b in self.blocks:
            self.model.set_meta(b, {"flipped": not b.meta.get("flipped")})

    def do(self) -> None:
        self._toggle()

    def revert(self) -> None:
        self._toggle()


class SnapshotCommand(Command):
    """A change already made to the model by code that works on the core objects (a console script, refreshing
    library links): the model before and after, as model-file data. The first ``redo`` keeps the edited objects
    (they already changed); undo and later redos rebuild the model from the snapshots."""

    def __init__(self, model: DiagramModel, before: dict[str, Any], after: dict[str, Any], text: str) -> None:
        super().__init__(text)
        self.model, self.before, self.after = model, before, after
        self._first = True

    def _restore(self, data: dict[str, Any]) -> None:
        from nervus.io.model_file import from_dict

        diagram = from_dict(data)
        diagram.source_path = self.model.root.source_path
        self.model.set_diagram(diagram, self.model.file_path, keep_history=True)

    def do(self) -> None:
        if self._first:
            self._first = False
            self.model.set_diagram(self.model.root, self.model.file_path, keep_history=True)
        else:
            self._restore(self.after)

    def revert(self) -> None:
        self._restore(self.before)


class ScriptCommand(SnapshotCommand):
    """Changes made by a script in the console."""

    def __init__(self, model: DiagramModel, before: dict[str, Any], after: dict[str, Any], text: str) -> None:
        super().__init__(model, before, after, f"Script: {text[:40]}")


class SetSignalObjectCommand(Command):
    """Add, edit, rename or delete one data-dictionary signal object."""

    def __init__(self, model: DiagramModel, old: Any, new: Any) -> None:
        verb = "Add" if old is None else "Delete" if new is None else "Change"
        super().__init__(f"{verb} signal {(new or old).name}")
        self.model, self.old, self.new = model, old, new
        names = list(model.root.signals)
        self._index = names.index(old.name) if old is not None else len(names)

    def do(self) -> None:
        self.model.set_signal_object(self.old.name if self.old else None, self.new)

    def revert(self) -> None:
        if self.new is None:
            self.model.set_signal_object(None, self.old, index=self._index)
        else:
            self.model.set_signal_object(self.new.name, self.old, index=self._index)


class SetRequirementsCommand(Command):
    """Replace the model's requirements (add, edit, delete or import)."""

    def __init__(self, model: DiagramModel, new: Any, text: str) -> None:
        super().__init__(text)
        self.model, self.old, self.new = model, model.root.requirements.copy(), new.copy()

    def do(self) -> None:
        self.model.set_requirements(self.new)

    def revert(self) -> None:
        self.model.set_requirements(self.old)
