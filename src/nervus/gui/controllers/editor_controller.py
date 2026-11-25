"""Editing operations on the diagram, all undoable."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPointF, Signal
from PySide6.QtGui import QUndoStack

from nervus.blocks import create_block, get_block_class
from nervus.core import Block, Connection, Link, NervusError, ParameterSet, SimulationSettings, Variable
from nervus.core.expressions import parse_value
from nervus.gui.controllers.commands import (
    AddBlockCommand,
    ApplyValuesCommand,
    Command,
    ConnectCommand,
    CreateSubsystemCommand,
    FlipBlocksCommand,
    LinkCommand,
    MoveBlocksCommand,
    RemoveCommand,
    RenameCommand,
    SetLinkCommand,
    SetMaskCommand,
    SetMetaCommand,
    SetParamsCommand,
    SetRequirementsCommand,
    SetSettingsCommand,
    SetSignalObjectCommand,
    SetTestsCommand,
    SetVariableCommand,
    SetVariantsCommand,
    SetWirePointsCommand,
    SnapshotCommand,
)
from nervus.gui.models.diagram_model import DiagramModel, block_position

CLIPBOARD_MIME = "application/x-nervus-blocks"


LIBRARY_PREFIX = "library:"
"""Type ids ``library:<library path>|<component>`` add a linked copy of a library component."""

_DIAGRAM_EDITS = (
    AddBlockCommand,
    ConnectCommand,
    RemoveCommand,
    RenameCommand,
    SetParamsCommand,
    MoveBlocksCommand,
    CreateSubsystemCommand,
    FlipBlocksCommand,
)
"""Commands that change the shown diagram (refused inside linked library subsystems)."""


class EditorController(QObject):
    errorOccurred = Signal(str)
    editingEnabledChanged = Signal(bool)

    def __init__(self, model: DiagramModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.undo_stack = QUndoStack(self)
        self._editing_enabled = True
        model.diagramReset.connect(lambda: None if model.keeping_history else self.undo_stack.clear())

    # ------------------------------------------------------------------ state

    @property
    def editing_enabled(self) -> bool:
        return self._editing_enabled

    def set_editing_enabled(self, enabled: bool) -> None:
        """Editing is disabled while a simulation runs."""
        if enabled != self._editing_enabled:
            self._editing_enabled = enabled
            self.editingEnabledChanged.emit(enabled)

    def _push(self, cmd: Command) -> bool:
        if not self._editing_enabled:
            self.errorOccurred.emit("The diagram cannot be edited while a simulation is running.")
            return False
        if isinstance(cmd, _DIAGRAM_EDITS):
            from nervus.library import locked_by

            owner = locked_by(self.model.diagram)
            if owner is not None:
                self.errorOccurred.emit(
                    f"{owner.name!r} is a linked copy of library component {owner.library_link}. Edit the library "
                    "(File → Open), or break the link in the Properties panel."
                )
                return False
        self.undo_stack.push(cmd)  # an obsolete (failed) command is dropped by Qt
        if cmd.error is not None:
            self.errorOccurred.emit(str(cmd.error))
            return False
        return True

    # ------------------------------------------------------------------ operations

    def add_block(self, type_id: str, position: QPointF, **params: Any) -> Block | None:
        try:
            if type_id.startswith(LIBRARY_PREFIX):
                block = self._library_instance(type_id)
            else:
                block = create_block(type_id, **params)
        except (NervusError, KeyError, OSError) as exc:
            self.errorOccurred.emit(str(exc))
            return None
        return block if self._push(AddBlockCommand(self.model, block, position)) else None

    def delete(self, blocks: Sequence[Block], connections: Sequence[Connection | Link]) -> None:
        if blocks or connections:
            self._push(RemoveCommand(self.model, list(blocks), list(connections)))

    def flip_blocks(self, blocks: list[Block]) -> bool:
        """Flip blocks left-right (undoable)."""
        return bool(blocks) and self._push(FlipBlocksCommand(self.model, blocks))

    def rotate_blocks(self, blocks: list[Block]) -> bool:
        """Rotate blocks a quarter turn clockwise: right -> down -> left -> up (undoable)."""
        from nervus.core.layout import ROTATE_CLOCKWISE, orientation_of, rotated_meta

        if not blocks:
            return False
        changes: list[tuple[Block, dict[str, Any]]] = [
            (b, dict(rotated_meta(ROTATE_CLOCKWISE[orientation_of(b)]))) for b in blocks
        ]
        n = len(blocks)
        return self._push(SetMetaCommand(self.model, f"Rotate {n} block{'s' if n != 1 else ''}", changes))

    def resize_block(self, block: Block, old: list[float] | None, new: list[float] | None) -> bool:
        """Set the drawn size of a block (``None``: its natural size). ``old`` is the size before a live drag."""
        if old == new:
            return False
        cmd = SetMetaCommand(self.model, f"Resize {block.name}", [(block, {"size": new})])
        cmd.old = [(block, {"size": old})]
        return self._push(cmd)

    def set_wire_points(self, changes: list[tuple[Connection, Any, Any]], text: str = "Route wire") -> bool:
        """Set the manual corners of wires (``None``: automatic routing), undoable."""
        changes = [(c, o, n) for c, o, n in changes if o != n]
        return bool(changes) and self._push(SetWirePointsCommand(self.model, changes, text))

    def move_blocks(self, moves: list[tuple[Block, QPointF, QPointF]]) -> None:
        moves = [(b, o, n) for b, o, n in moves if o != n]
        if moves:
            self._push(MoveBlocksCommand(self.model, moves))

    def set_params(self, block: Block, params: dict[str, Any]) -> bool:
        changed = {k: v for k, v in params.items() if not _same(block.param_source(k), v)}
        if not changed:
            return True
        try:  # validate on a scratch copy first, so a bad value never reaches the undo stack
            probe = type(block)(**{k: block.param_source(k) for k in block.params})
            ns = block.diagram.namespace() if block.diagram is not None else self.model.root.workspace.namespace()
            probe.resolve(ns)
            for k, v in changed.items():
                probe._assign(probe.param_spec(k), v, ns)
            probe.validate_params()
        except (NervusError, ValueError, TypeError) as exc:
            self.errorOccurred.emit(f"{block.name}: {exc}")
            return False
        return self._push(SetParamsCommand(self.model, block, changed))

    def set_chart(self, block: Block, definition: str, layout: dict[str, list[float]]) -> bool:
        """The graphical chart editor's result: the chart text and the states' boxes, as one undo step."""
        if _same(block.param_source("definition"), definition) and block.meta.get("chart_layout") == layout:
            return True
        self.undo_stack.beginMacro(f"Edit chart {block.name}")
        try:
            ok = self.set_params(block, {"definition": definition})
            if ok and block.meta.get("chart_layout") != layout:
                self._push(
                    SetMetaCommand(self.model, f"Chart layout of {block.name}", [(block, {"chart_layout": layout})])
                )
        finally:
            self.undo_stack.endMacro()
        return ok

    def rename(self, block: Block, new_name: str) -> bool:
        new_name = new_name.strip()
        if new_name == block.name:
            return True
        if not new_name:
            self.errorOccurred.emit("Block names cannot be empty.")
            return False
        if block.diagram is not None and new_name in block.diagram:
            self.errorOccurred.emit(f"A block named '{new_name}' already exists.")
            return False
        return self._push(RenameCommand(self.model, block, new_name))

    def link_ports(self, a: Block, a_port: str, b: Block, b_port: str) -> bool:
        """Join two physical ports (undoable). Ports of different domains cannot be linked."""
        d = a.diagram
        if d is None or (a is b and a_port == b_port):
            return False
        for k in d.links:
            if {(id(k.a), k.a_port), (id(k.b), k.b_port)} == {(id(a), a_port), (id(b), b_port)}:
                return True  # already linked
        doms = dict(a.physical_ports()).get(a_port), dict(b.physical_ports()).get(b_port)
        if doms[0] != doms[1]:
            self.errorOccurred.emit(f"Cannot link a {doms[0]} port to a {doms[1]} port.")
            return False
        return self._push(LinkCommand(self.model, a, a_port, b, b_port))

    def connect_ports(self, src: Block, src_port: int, dst: Block, dst_port: int) -> bool:
        if self.model.find_connection(src, src_port, dst, dst_port):
            return True
        return self._push(ConnectCommand(self.model, src, src_port, dst, dst_port))

    def set_settings(self, settings: SimulationSettings) -> bool:
        try:
            settings.validate()
        except ValueError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        return self._push(SetSettingsCommand(self.model, settings))

    def auto_layout(self) -> None:
        """Arrange all blocks of the shown diagram left to right (one undo step)."""
        from nervus.core.layout import auto_layout

        d = self.model.diagram
        old = {id(b): block_position(b) for b in d.blocks}
        saved = {id(b): b.meta.get("position") for b in d.blocks}
        auto_layout(d, only_missing=False, width_of=self.model.width_of)
        moves = [(b, old[id(b)], block_position(b)) for b in d.blocks]
        for b in d.blocks:  # restore, then apply through an undoable command
            if saved[id(b)] is None:
                b.meta.pop("position", None)
            else:
                b.meta["position"] = saved[id(b)]
        self.move_blocks(moves)

    def _library_instance(self, type_id: str) -> Block:
        """A linked subsystem for ``library:<path>|<component>``; the path is stored relative to the model."""
        from nervus.library import instantiate

        path, component = type_id[len(LIBRARY_PREFIX) :].rsplit("|", 1)
        stored = path
        base = Path(self.model.root.source_path).parent if self.model.root.source_path else None
        if base is not None:
            with contextlib.suppress(ValueError):
                stored = os.path.relpath(path, base)
        block: Block = instantiate(stored, component, base_dir=base)
        taken = {b.name for b in self.model.diagram.blocks}
        name, k = component, 1
        while name in taken:
            k += 1
            name = f"{component}{k}"
        block.name = name
        return block

    def refresh_links(self) -> list[str] | None:
        """Update every linked subsystem from its library (one undo step). Returns the warnings (e.g. a missing
        library, whose subsystems keep the copy saved in the model), or ``None`` if editing is disabled."""
        from nervus.io.model_file import to_dict
        from nervus.library import refresh_links

        if not self._editing_enabled:
            self.errorOccurred.emit("The diagram cannot be edited while a simulation is running.")
            return None
        root = self.model.root
        before = to_dict(root)
        warnings = refresh_links(root)
        after = to_dict(root)
        if json.dumps(before, sort_keys=True, default=str) != json.dumps(after, sort_keys=True, default=str):
            self._push(SnapshotCommand(self.model, before, after, "Refresh library links"))
        return warnings

    def save_to_library(self, subsystem: Block, library: str | Path, link: bool = True) -> bool:
        """Add ``subsystem`` to a library file as a component and (by default) link it to that component."""
        from nervus.library import LibraryLink, save_to_library

        try:
            path = save_to_library(subsystem, library)
        except (NervusError, OSError) as exc:
            self.errorOccurred.emit(f"Could not save to library: {exc}")
            return False
        if not link:
            return True
        stored = str(path.resolve())
        if self.model.root.source_path:
            with contextlib.suppress(ValueError):
                stored = os.path.relpath(stored, Path(self.model.root.source_path).parent)
        new = LibraryLink(stored, subsystem.name)
        return self._push(SetLinkCommand(self.model, subsystem, new, f"Link {subsystem.name} to {path.name}"))

    def break_link(self, subsystem: Block) -> bool:
        if getattr(subsystem, "library_link", None) is None:
            return False
        return self._push(SetLinkCommand(self.model, subsystem, None, f"Break library link of {subsystem.name}"))

    def set_mask(self, subsystem: Block, variables: list[dict[str, Any]], icon: str, description: str) -> bool:
        """Replace a subsystem's mask. Validates that the mask variables evaluate in their context."""
        from nervus.core.workspace import Workspace

        sub: Any = subsystem
        if sub.library_link is not None:
            names = [v["name"] for v in variables]
            same_definition = names == list(sub.mask) and (icon, description) == (sub.mask_icon, sub.mask_description)
            if not same_definition:
                self.errorOccurred.emit(
                    f"{subsystem.name}: the mask of a linked subsystem is defined in its library; only the "
                    "parameter values can be changed here"
                )
                return False

        try:
            ws = Workspace.from_list(variables)
            outer = subsystem.diagram.namespace() if subsystem.diagram is not None else {}
            ws.namespace(base=outer)
        except (NervusError, KeyError, TypeError, ValueError) as exc:
            self.errorOccurred.emit(f"{subsystem.name}: invalid mask: {exc}")
            return False
        state: dict[str, Any] = {"variables": variables, "icon": icon, "description": description}
        return self._push(SetMaskCommand(self.model, subsystem, state))

    def set_variants(self, subsystem: Block, rows: list[tuple[str | None, str, str]], active: str) -> bool:
        """Replace a subsystem's variants. ``rows`` are (existing variant name or None for a new copy of the
        active variant, name, condition). An empty list removes the variants and keeps the active diagram."""
        if getattr(subsystem, "library_link", None) is not None:
            self.errorOccurred.emit(f"{subsystem.name}: the variants of a linked subsystem are defined in its library")
            return False
        from nervus.blocks.ports import Variant
        from nervus.io.model_file import system_from_dict, system_to_dict

        sub: Any = subsystem
        names = [name.strip() for _old, name, _cond in rows]
        if any(not n for n in names) or len(set(names)) != len(names):
            self.errorOccurred.emit(f"{sub.name}: variant names must be unique and not empty")
            return False
        if rows and active not in names:
            self.errorOccurred.emit(f"{sub.name}: the active variant {active!r} is not in the list")
            return False
        variants: dict[str, Variant] = {}
        for old, name, cond in rows:
            if old is not None and old in sub.variants:
                system = sub.variants[old].system
            else:
                system = system_from_dict(system_to_dict(sub.system), sub.system.name)
            variants[name.strip()] = Variant(system, cond.strip())
        state: dict[str, Any] = {
            "variants": variants,
            "active": active,
            "system": variants[active].system if rows else sub.system,
        }
        if rows:  # check the conditions and ports before changing anything
            from nervus.blocks.ports import check_variant_ports, choose_variant

            try:
                check_variant_ports(variants, active)
                if subsystem.diagram is not None:
                    state["active"] = choose_variant(variants, subsystem.diagram.namespace(), active)
                    state["system"] = variants[state["active"]].system
            except (NervusError, ValueError) as exc:
                self.errorOccurred.emit(f"{sub.name}: {exc}")
                return False
        return self._push(SetVariantsCommand(self.model, subsystem, state, f"Edit variants of {sub.name}"))

    def activate_variant(self, subsystem: Block, name: str) -> bool:
        from nervus.gui.controllers.commands import variant_state

        state = variant_state(subsystem)
        if name == state["active"] or name not in state["variants"]:
            return False
        state["active"] = name
        return self._push(SetVariantsCommand(self.model, subsystem, state, f"Activate variant {name}"))

    def set_mask_value(self, subsystem: Block, name: str, value: object) -> bool:
        from nervus.gui.controllers.commands import mask_state

        state = mask_state(subsystem)
        variables = [dict(v) for v in state["variables"]]
        for v in variables:
            if v["name"] == name:
                v["value"] = value
        return self.set_mask(subsystem, variables, str(state["icon"]), str(state["description"]))

    def set_tests(self, tests: list[object], text: str = "Edit test cases") -> bool:
        return self._push(SetTestsCommand(self.model, tests, text))

    def create_subsystem(self, blocks: list[Block]) -> Block | None:
        """Move ``blocks`` into a new Subsystem (one undo step)."""
        if not blocks:
            return None
        cmd = CreateSubsystemCommand(self.model, blocks)
        return cmd.subsystem if self._push(cmd) else None

    # ------------------------------------------------------------------ workspace

    @staticmethod
    def parse_variable_value(text: str) -> object:
        """A literal (number/array) if ``text`` is one, otherwise the text as an expression."""
        try:
            return parse_value(text)
        except ValueError:
            return text.strip()

    def add_variable(self, name: str | None = None, value: object = 0.0) -> Variable | None:
        ws = self.model.root.workspace
        if name is None:
            i = 1
            while f"var{i}" in ws:
                i += 1
            name = f"var{i}"
        if name in ws:
            self.errorOccurred.emit(f"A variable named '{name}' already exists.")
            return None
        try:
            var = Variable(name, value)
        except NervusError as exc:
            self.errorOccurred.emit(str(exc))
            return None
        return var if self._push(SetVariableCommand(self.model, None, var)) else None

    def update_variable(self, name: str, /, **changes: object) -> bool:
        """Change fields of a variable (name, value, unit, description, min, max)."""
        ws = self.model.root.workspace
        old = ws.variable(name)
        fields = {**old.__dict__, **changes}
        if fields["name"] != name and fields["name"] in ws:
            self.errorOccurred.emit(f"A variable named '{fields['name']}' already exists.")
            return False
        try:
            new = Variable(**fields)
            trial = ws.copy()
            trial.replace(name, new)
            trial.namespace()
        except NervusError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        if new.__dict__ == old.__dict__:
            return True
        copy = Variable(**old.__dict__)
        return self._push(SetVariableCommand(self.model, copy, new))

    def delete_variable(self, name: str) -> bool:
        ws = self.model.root.workspace
        from nervus.core.expressions import names_in

        users = [p for p, b in self.model.root.walk() if any(name in names_in(e) for e in b.expressions.values())]
        if users:
            self.errorOccurred.emit(f"'{name}' is used by {', '.join(users)}; change those parameters first.")
            return False
        return self._push(SetVariableCommand(self.model, Variable(**ws.variable(name).__dict__), None))

    # ------------------------------------------------------------------ data dictionary

    def name_signal(self, block: Block, port: int, name: str | None) -> bool:
        """Give output ``port`` of ``block`` a signal name ("" or None removes it); one undo step."""
        from nervus.core.dictionary import META_KEY, named_signals

        name = (name or "").strip()
        if name and not name.isidentifier():
            self.errorOccurred.emit(f"Invalid signal name {name!r}: use letters, digits and _.")
            return False
        path = self.model.path_of(block)
        taken = named_signals(self.model.root).get(name) if name else None
        if taken is not None and taken != (path, port):
            self.errorOccurred.emit(f"The signal name {name!r} is already used by {taken[0]}:{taken[1]}.")
            return False
        names = dict(block.meta.get(META_KEY, {}))
        if name:
            names[str(port)] = name
        else:
            names.pop(str(port), None)
        text = f"Name signal {name}" if name else f"Remove the signal name of {block.name}"
        return self._push(SetMetaCommand(self.model, text, [(block, {META_KEY: names or None})]))

    def add_signal_object(self, name: str | None = None, **fields: Any) -> Any:
        from nervus.core.dictionary import SignalObject

        dd = self.model.root.signals
        if name is None:
            i = 1
            while f"signal{i}" in dd:
                i += 1
            name = f"signal{i}"
        if name in dd:
            self.errorOccurred.emit(f"A signal object named '{name}' already exists.")
            return None
        try:
            obj = SignalObject(name, **fields)
        except NervusError as exc:
            self.errorOccurred.emit(str(exc))
            return None
        return obj if self._push(SetSignalObjectCommand(self.model, None, obj)) else None

    def update_signal_object(self, name: str, /, **changes: Any) -> bool:
        from nervus.core.dictionary import SignalObject

        dd = self.model.root.signals
        old = dd[name]
        fields = {**old.__dict__, **changes}
        if fields["name"] != name and fields["name"] in dd:
            self.errorOccurred.emit(f"A signal object named '{fields['name']}' already exists.")
            return False
        try:
            new = SignalObject(**fields)
        except NervusError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        if new.__dict__ == old.__dict__:
            return True
        return self._push(SetSignalObjectCommand(self.model, SignalObject(**old.__dict__), new))

    def delete_signal_object(self, name: str) -> bool:
        from nervus.core.dictionary import SignalObject

        old = self.model.root.signals[name]
        return self._push(SetSignalObjectCommand(self.model, SignalObject(**old.__dict__), None))

    # ------------------------------------------------------------------ requirements

    def set_requirement(self, old_id: str | None, req: Any, text: str | None = None) -> bool:
        """Add (``old_id`` None), change or rename (``req`` with another id) or delete (``req`` None) one
        requirement; links of blocks and tests follow a rename."""
        reqs = self.model.root.requirements.copy()
        if req is not None and req.id != old_id and req.id in reqs:
            self.errorOccurred.emit(f"A requirement {req.id!r} already exists.")
            return False
        items = reqs.items
        if old_id is None:
            items.append(req)
        else:
            k = [r.id for r in items].index(old_id)
            if req is None:
                del items[k]
            else:
                items[k] = req
        from nervus.core.requirements import Requirements

        new = Requirements({r.id: r for r in items})
        verb = "Add" if old_id is None else "Delete" if req is None else "Change"
        if old_id is None or req is None or req.id == old_id:
            rid = req.id if req is not None else old_id
            return self._push(SetRequirementsCommand(self.model, new, text or f"{verb} requirement {rid}"))
        self.undo_stack.beginMacro(f"Rename requirement {old_id} to {req.id}")
        try:
            ok = self._push(SetRequirementsCommand(self.model, new, "Rename requirement"))
            self._relink(old_id, req.id)
        finally:
            self.undo_stack.endMacro()
        return ok

    def _relink(self, old: str, new: str) -> None:
        from nervus.core.requirements import META_KEY

        changes = [(b, {META_KEY: [new if r == old else r for r in b.meta[META_KEY]]})
                   for _p, b in self.model.root.walk() if old in b.meta.get(META_KEY, [])]  # fmt: skip
        if changes:
            self._push(SetMetaCommand(self.model, "Relink blocks", changes))
        if any(old in c.requirements for c in self.model.root.tests):
            import copy

            tests = copy.deepcopy(self.model.root.tests)
            for c in tests:
                c.requirements = [new if r == old else r for r in c.requirements]
            self.set_tests(tests, "Relink test cases")

    def import_requirements(self, path: str | Path) -> list[str]:
        """Add or update requirements from a CSV file; returns the ids read (one undo step)."""
        from nervus.core.requirements import read_csv

        try:
            read = read_csv(path)
        except (OSError, ValueError, NervusError) as exc:
            self.errorOccurred.emit(f"Could not read {Path(path).name}: {exc}")
            return []
        if not read:
            self.errorOccurred.emit(f"No requirements found in {Path(path).name}.")
            return []
        reqs = self.model.root.requirements.copy()
        for r in read:
            reqs.add(r)
        self._push(SetRequirementsCommand(self.model, reqs, f"Import {Path(path).name}"))
        return [r.id for r in read]

    def link_requirements(self, block: Block, ids: list[str]) -> bool:
        from nervus.core.requirements import META_KEY

        return self._push(
            SetMetaCommand(self.model, f"Requirements of {block.name}", [(block, {META_KEY: list(ids) or None})])
        )

    def import_dbc(self, path: str | Path) -> list[str]:
        """Add the signals and value tables of a DBC file as workspace variables (existing names are kept).
        Returns the names added."""
        from nervus.can import dbc_variables

        try:
            new = [v for v in dbc_variables(path) if v.name not in self.model.root.workspace]
        except NervusError as exc:
            self.errorOccurred.emit(str(exc))
            return []
        if not new:
            return []
        self.undo_stack.beginMacro(f"Import {Path(path).name}")
        try:
            added = [v.name for v in new if self._push(SetVariableCommand(self.model, None, v))]
        finally:
            self.undo_stack.endMacro()
        return added

    def load_parameter_set(self, path: str | Path) -> bool:
        try:
            ps = ParameterSet.load(path)
            known = {k: v for k, v in ps.values.items() if k in self.model.root.workspace}
            missing = sorted(set(ps.values) - set(known))
        except NervusError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        ok = self._push(ApplyValuesCommand(self.model, known, ps.name))
        if ok and missing:
            self.errorOccurred.emit(f"Ignored values for unknown variables: {', '.join(missing)}")
        return ok

    def save_parameter_set(self, path: str | Path, name: str | None = None) -> Path | None:
        p = Path(path)
        try:
            return ParameterSet.from_workspace(self.model.root.workspace, name or p.stem).save(p)
        except OSError as exc:
            self.errorOccurred.emit(f"Could not save {p.name}: {exc}")
            return None

    # ------------------------------------------------------------------ clipboard

    def copy_to_text(self, blocks: list[Block]) -> str:
        names = {b.name for b in blocks}
        data = {
            "blocks": [
                {"name": b.name, "type": b.type_id, "params": b.params_jsonable(), "meta": dict(b.meta)} for b in blocks
            ],
            "connections": [
                {"src": c.src.name, "src_port": c.src_port, "dst": c.dst.name, "dst_port": c.dst_port}
                for c in self.model.diagram.connections
                if c.src.name in names and c.dst.name in names
            ],
        }
        return json.dumps(data)

    def paste_from_text(self, text: str, offset: QPointF | None = None) -> list[Block]:
        offset = offset if offset is not None else QPointF(30, 30)
        try:
            data = json.loads(text)
            entries = data["blocks"]
        except (ValueError, KeyError, TypeError):
            return []
        if not self._editing_enabled:
            self.errorOccurred.emit("The diagram cannot be edited while a simulation is running.")
            return []
        created: dict[str, Block] = {}
        self.undo_stack.beginMacro("Paste")
        try:
            for e in entries:
                try:
                    block = get_block_class(e["type"])(**e.get("params", {}))
                except (NervusError, KeyError) as exc:
                    self.errorOccurred.emit(str(exc))
                    continue
                block.name = self.model.diagram.unique_name(e.get("name") or block.label)
                block.meta = dict(e.get("meta", {}))
                pos = block_position(block) + offset
                self.undo_stack.push(AddBlockCommand(self.model, block, pos))
                created[e.get("name", "")] = block
            for c in data.get("connections", []):
                src, dst = created.get(c["src"]), created.get(c["dst"])
                if src and dst:
                    self.undo_stack.push(ConnectCommand(self.model, src, c["src_port"], dst, c["dst_port"]))
        finally:
            self.undo_stack.endMacro()
        return list(created.values())


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a.strip() == b.strip()
    try:
        import numpy as np

        return bool(np.array_equal(np.asarray(a), np.asarray(b))) if a is not None else False
    except (TypeError, ValueError):
        return a == b
