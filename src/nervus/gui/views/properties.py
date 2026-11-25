"""Property editor: a form generated from the selected block's parameter declarations."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from nervus.core import Block, Param, ParamKind
from nervus.core.block import EXPRESSION_KINDS
from nervus.core.expressions import format_value, parse_value
from nervus.gui.controllers.editor_controller import EditorController
from nervus.gui.models.diagram_model import DiagramModel
from nervus.gui.theme import monospace_font
from nervus.gui.views.completer import attach_completer
from nervus.help import block_doc


def _parse(p: Param, text: str) -> Any:
    if p.kind in (ParamKind.STR, ParamKind.CODE, ParamKind.CHOICE):
        return text
    value = parse_value(text)
    if p.kind is ParamKind.INT and isinstance(value, float):
        return value
    return value


def _help_html(p: Param) -> str:
    """A parameter's help (description, what can be written, range, default) as rich text."""
    lines = [html.escape(x) for x in p.help_text().split("\n")]
    return f"<b>{html.escape(p.name)}</b>: " + "<br>".join(lines)


class PropertyPanel(QWidget):
    helpRequested = Signal(str)  # type_id
    editMaskRequested = Signal(object)  # Subsystem
    editVariantsRequested = Signal(object)  # Subsystem

    def __init__(self, model: DiagramModel, controller: EditorController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.controller = controller
        self.block: Block | None = None
        self._editors: dict[str, QWidget] = {}
        self._updating = False

        self.title = QLabel()
        self.title.setObjectName("heading")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("muted")
        self.help_button = QPushButton("?")
        self.help_button.setFixedSize(30, 28)
        self.help_button.setProperty("compact", True)
        self.help_button.setToolTip("Help for this block (F1)")
        self.help_button.clicked.connect(lambda: self.block and self.helpRequested.emit(self.block.type_id))
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        header.addWidget(self.help_button, 0, Qt.AlignmentFlag.AlignTop)

        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setObjectName("muted")
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.field_help = QLabel()  # what can be written in the field being edited (or hovered)
        self.field_help.setObjectName("muted")
        self.field_help.setWordWrap(True)
        self.field_help.setTextFormat(Qt.TextFormat.RichText)
        self._help_for: dict[int, str] = {}  # id(widget) -> help (HTML)

        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.scroll_area = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_host)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the fields grow instead

        self.placeholder = QLabel(
            "Select a block to edit its parameters.\n\nTip: double-click a block in the library, "
            "or drag it onto the canvas."
        )
        self.placeholder.setObjectName("muted")
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addLayout(header)
        layout.addWidget(self.description)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.field_help)
        layout.addWidget(self.error)
        layout.addWidget(self.placeholder, 1)

        model.paramsChanged.connect(self._on_model_changed)
        model.blockRenamed.connect(lambda b, _old: self._on_model_changed(b))
        model.blockRemoved.connect(self._on_block_removed)
        model.diagramReset.connect(lambda: self.show_blocks([]))
        controller.editingEnabledChanged.connect(self.form_host.setEnabled)
        self.show_blocks([])

    # ------------------------------------------------------------------ display

    def show_blocks(self, blocks: list[Block]) -> None:
        self.block = blocks[0] if len(blocks) == 1 else None
        has = self.block is not None
        for w in (self.title, self.subtitle, self.description, self.form_host, self.help_button):
            w.setVisible(has)
        self.scroll_area.setVisible(has)
        self.placeholder.setVisible(not has)
        if len(blocks) > 1:
            self.placeholder.setText(f"{len(blocks)} blocks selected.")
        else:
            self.placeholder.setText(
                "Select a block to edit its parameters.\n\nTip: double-click a block in the library, "
                "or drag it onto the canvas."
            )
        self.error.clear()
        self._build()

    def _on_block_removed(self, block: Block) -> None:
        if block is self.block:
            self.show_blocks([])

    def _on_model_changed(self, block: Block) -> None:
        if block is self.block:
            self._build()

    def _build(self) -> None:
        self._updating = True
        while self.form.rowCount():
            self.form.removeRow(0)
        self._editors.clear()
        b = self.block
        if b is None:
            self._updating = False
            return
        self.title.setText(b.name)
        self.subtitle.setText(f"{b.label} · {b.category}")
        self.description.setText(
            getattr(b, "mask_description", "") or block_doc(type(b)).split("\n\n")[0].replace("``", "").replace("*", "")
        )

        name_edit = QLineEdit(b.name)
        name_edit.editingFinished.connect(lambda: self._rename(name_edit))
        self.form.addRow("Name", name_edit)
        self._editors["__name__"] = name_edit

        mask = getattr(b, "mask", None)
        if mask is not None:
            for v in mask.variables:
                edit = QLineEdit(v.display_value)
                edit.setToolTip(v.description or v.name)
                attach_completer(edit, b.diagram)  # a mask value may use the variables outside
                edit.editingFinished.connect(lambda name=v.name, le=edit: self._apply_mask(name, le))
                label = QLabel(v.name + (f" [{v.unit}]" if v.unit else ""))
                label.setToolTip(v.description)
                self.form.addRow(label, edit)
                self._editors[f"mask:{v.name}"] = edit
            link = getattr(b, "library_link", None)
            if link is not None:
                info = QLabel(f"🔗 {link.component} from <b>{Path(link.library).name}</b>")
                info.setWordWrap(True)
                info.setToolTip(
                    f"{link.library}\n\nA linked copy of a library component: it is updated from the library when "
                    "the model is opened. Its contents and mask are edited in the library."
                )
                self.form.addRow("library", info)
                unlink = QPushButton("Break Link")
                unlink.setToolTip("Make this an ordinary subsystem that you can edit here")
                unlink.clicked.connect(lambda _c=False: self.controller.break_link(b))
                self.form.addRow("", unlink)
            masked = len(mask) > 0 or bool(getattr(b, "mask_icon", ""))
            button = QPushButton("Edit Mask…" if masked else "Create Mask…")
            button.clicked.connect(lambda _c=False: self.editMaskRequested.emit(b))
            if link is None:  # a linked subsystem's mask and variants are defined in its library
                self.form.addRow("", button)
            variants = getattr(b, "variants", {})
            if variants:
                combo = QComboBox()
                combo.addItems(list(variants))
                combo.setCurrentText(getattr(b, "active_variant", ""))
                if any(v.condition for v in variants.values()):
                    combo.setEnabled(False)
                    combo.setToolTip("Chosen by the variant conditions and the model workspace")
                combo.currentTextChanged.connect(lambda name: self.controller.activate_variant(b, name))
                self.form.addRow("variant", combo)
                self._editors["variant"] = combo
            vbutton = QPushButton("Variants…" if variants else "Create Variants…")
            vbutton.clicked.connect(lambda _c=False: self.editVariantsRequested.emit(b))
            if link is None:
                self.form.addRow("", vbutton)
        self._help_for = {}
        self.field_help.clear()
        for p in b.PARAMS:
            editor = self._editor_for(p, b.param_source(p.name))
            label = QLabel(p.name + (f" [{p.unit}]" if p.unit else ""))
            help_html = _help_html(p)
            for w in (label, editor, *editor.findChildren(QWidget)):
                w.setToolTip(help_html)
                self._help_for[id(w)] = help_html
                w.installEventFilter(self)
            self.form.addRow(label, editor)
            self._editors[p.name] = editor
        self._updating = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Clicking into (or hovering over) a field shows what can be written in it, under the fields."""
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.Enter):
            text = self._help_for.get(id(watched))
            if text is not None:
                self.field_help.setText(text)
        return super().eventFilter(watched, event)

    def _editor_for(self, p: Param, value: Any) -> QWidget:
        if p.kind is ParamKind.BOOL:
            cb = QCheckBox()
            cb.setChecked(bool(value))
            cb.toggled.connect(lambda v, name=p.name: self._apply(name, v))
            return cb
        if p.kind is ParamKind.DATATYPE:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(
                [
                    "inherit",
                    "double",
                    "single",
                    "boolean",
                    "int8",
                    "uint8",
                    "int16",
                    "uint16",
                    "int32",
                    "uint32",
                    "fixdt(1, 16, 8)",
                    "fixdt(0, 16, 4)",
                ]
            )
            combo.setCurrentText(str(value))
            combo.setToolTip(p.description)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.editingFinished.connect(lambda name=p.name, c=combo: self._apply(name, c.currentText()))
            combo.activated.connect(lambda _i, name=p.name, c=combo: self._apply(name, c.currentText()))
            return combo
        if p.kind is ParamKind.CHOICE:
            combo = QComboBox()
            combo.addItems(list(p.choices))
            combo.setCurrentText(str(value))
            combo.currentTextChanged.connect(lambda v, name=p.name: self._apply(name, v))
            return combo
        if p.kind is ParamKind.INT:
            spin = QSpinBox()
            spin.setRange(
                int(p.min) if p.min is not None else -1_000_000, int(p.max) if p.max is not None else 1_000_000
            )
            spin.setValue(int(value))
            spin.editingFinished.connect(lambda name=p.name, s=spin: self._apply(name, s.value()))
            return spin
        if p.kind is ParamKind.CODE:
            host = QWidget()
            box = QVBoxLayout(host)
            box.setContentsMargins(0, 0, 0, 0)
            edit = QPlainTextEdit(str(value))
            edit.setFont(monospace_font())
            edit.setMinimumHeight(140)
            apply = QPushButton("Apply code")
            apply.clicked.connect(lambda _=False, name=p.name, e=edit: self._apply(name, e.toPlainText()))
            box.addWidget(edit)
            box.addWidget(apply, 0, Qt.AlignmentFlag.AlignRight)
            return host
        if p.kind is ParamKind.STR and self.block is not None and self.block.suggestions(p.name):
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(self.block.suggestions(p.name))
            combo.setCurrentText(str(value))
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.editingFinished.connect(lambda name=p.name, c=combo: self._apply(name, c.currentText()))
            combo.activated.connect(lambda _i, name=p.name, c=combo: self._apply(name, c.currentText()))
            return combo
        if p.kind is ParamKind.STR and p.name.endswith("_file"):
            host = QWidget()
            row = QHBoxLayout(host)
            row.setContentsMargins(0, 0, 0, 0)
            path_edit = QLineEdit(str(value))
            browse = QPushButton("…")
            browse.setFixedWidth(30)
            browse.setProperty("compact", True)
            browse.setToolTip("Choose a file")
            path_edit.editingFinished.connect(lambda name=p.name, le=path_edit: self._apply(name, le.text().strip()))
            browse.clicked.connect(lambda _c=False, name=p.name, le=path_edit: self._browse(name, le))
            row.addWidget(path_edit, 1)
            row.addWidget(browse)
            return host
        line = QLineEdit(value if isinstance(value, str) else format_value(value))
        if p.kind is ParamKind.ARRAY:
            line.setPlaceholderText("e.g. 2, [1 2 3], [1 0; 0 1], 2*pi, Kp")
        if p.kind is ParamKind.SAMPLE_TIME:
            line.setPlaceholderText("-1 inherit · 0 continuous · T or [T, offset]")
        if p.kind in EXPRESSION_KINDS and self.block is not None:
            attach_completer(line, self.block.diagram)
        line.editingFinished.connect(lambda name=p.name, le=line: self._apply_text(name, le))
        return line

    # ------------------------------------------------------------------ edits

    def _rename(self, edit: QLineEdit) -> None:
        if self._updating or self.block is None:
            return
        if not self.controller.rename(self.block, edit.text()):
            edit.setText(self.block.name)

    def _apply_text(self, name: str, edit: QLineEdit) -> None:
        if self._updating or self.block is None:
            return
        p = self.block.param_spec(name)
        if p.kind in EXPRESSION_KINDS:
            value: Any = edit.text().strip()  # a literal or a workspace expression; the block decides
        else:
            try:
                value = _parse(p, edit.text())
            except ValueError as exc:
                self._show_error(edit, f"{name}: {exc}")
                return
        if self.controller.set_params(self.block, {name: value}):
            self._clear_error(edit)
        else:
            self._show_error(edit, f"{name}: invalid value")

    def _apply(self, name: str, value: Any) -> None:
        if self._updating or self.block is None:
            return
        self.controller.set_params(self.block, {name: value})

    def _apply_mask(self, name: str, edit: QLineEdit) -> None:
        if self._updating or self.block is None:
            return
        text = edit.text().strip()
        try:
            value: Any = parse_value(text)
        except ValueError:
            value = text
        if not self.controller.set_mask_value(self.block, name, value):
            self._show_error(edit, f"{name}: invalid value")

    def _browse(self, name: str, edit: QLineEdit) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, f"Choose {name.replace('_', ' ')}", edit.text())
        if path:
            edit.setText(path)
            self._apply(name, path)

    def _show_error(self, edit: QLineEdit, message: str) -> None:
        self.error.setText(message)
        if not isValid(edit):  # applying the value rebuilt the panel: this field is gone
            return
        edit.setProperty("invalid", True)
        edit.style().unpolish(edit)
        edit.style().polish(edit)

    def _clear_error(self, edit: QLineEdit) -> None:
        self.error.clear()
        if not isValid(edit):  # applying the value rebuilt the panel: this field is gone
            return
        edit.setProperty("invalid", False)
        edit.style().unpolish(edit)
        edit.style().polish(edit)

    def show_controller_error(self, message: str) -> None:
        if self.block is not None and message.startswith(self.block.name):
            self.error.setText(message)
