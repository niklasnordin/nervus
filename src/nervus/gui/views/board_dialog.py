"""Code ▸ Board Project…: the generated code as a project for a development board, built and flashed from Nervus."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nervus.codegen.boards import BOARDS, build, create_project, fill_a2l_addresses, flash, missing_tool


class BoardDialog(QDialog):
    def __init__(self, diagram: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Board Project")
        self.resize(720, 460)
        self.diagram = diagram
        self.subsystem = QComboBox()
        self.subsystem.addItem("whole model", "")
        for b in diagram.blocks:
            if getattr(b, "system", None) is not None:
                self.subsystem.addItem(b.name, b.name)
        k = self.subsystem.findData("ECU")
        if k >= 0:
            self.subsystem.setCurrentIndex(k)
        self.board = QComboBox()
        for key, b in BOARDS.items():
            self.board.addItem(b.name, key)
        self.can_info = QLabel()
        self.can_info.setObjectName("muted")
        self.bitrate = QComboBox()
        for rate in ("125000", "250000", "500000", "1000000"):
            self.bitrate.addItem(rate)
        self.bitrate.setCurrentText("500000")
        self.ids = QLineEdit("0x7F0, 0x7F1")
        self.ids.setToolTip("XCP request (tool → board) and response (board → tool) CAN identifiers")
        self.folder = QLineEdit(str(Path.home() / "nervus_board"))
        browse = QPushButton("…")
        browse.setFixedWidth(36)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form = QFormLayout()
        form.addRow("Code of", self.subsystem)
        form.addRow("Board", self.board)
        form.addRow("", self.can_info)
        form.addRow("CAN bit rate", self.bitrate)
        form.addRow("XCP identifiers", self.ids)
        form.addRow("Folder", row)
        self.create_btn = QPushButton("Create project")
        self.create_btn.setDefault(True)
        self.build_btn = QPushButton("Build")
        self.flash_btn = QPushButton("Flash")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (self.create_btn, self.build_btn, self.flash_btn):
            buttons.addWidget(b)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.output, 1)
        browse.clicked.connect(self._browse)
        self.board.currentIndexChanged.connect(self._board_changed)
        self.create_btn.clicked.connect(self.make_project)
        self.build_btn.clicked.connect(self.build)
        self.flash_btn.clicked.connect(self.flash)
        self._board_changed()

    @property
    def key(self) -> str:
        return str(self.board.currentData())

    def _board_changed(self) -> None:
        b = BOARDS[self.key]
        tool = missing_tool(self.key)
        self.can_info.setText(f"CAN: {b.can}" + (f". To build, install {tool}." if tool else ""))
        self.build_btn.setEnabled(tool is None)
        self.flash_btn.setEnabled(tool is None and bool(b.flash))

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Project folder", self.folder.text())
        if path:
            self.folder.setText(path)

    def _say(self, text: str) -> None:
        self.output.appendPlainText(text)

    def make_project(self) -> Path | None:
        name = self.subsystem.currentData()
        target = self.diagram.block(name) if name else self.diagram
        try:
            req, resp = (int(x.strip(), 0) for x in self.ids.text().split(","))
            out = create_project(target, self.key, self.folder.text(), req, resp, int(self.bitrate.currentText()))
        except Exception as exc:  # code generation problems, bad identifiers
            self._say(f"Could not create the project:\n{exc}")
            return None
        self._say(f"Created {BOARDS[self.key].name} project in {out}. Edit io.c for the sensors and actuators.")
        return out

    def build(self) -> bool:
        out = self.make_project()
        if out is None:
            return False
        self._say(f"Building: {' '.join(BOARDS[self.key].build)} …")
        try:
            proc = build(out, self.key)
        except Exception as exc:  # missing tools
            self._say(str(exc))
            return False
        self._say((proc.stdout + proc.stderr)[-4000:])
        if proc.returncode != 0:
            self._say("The build failed (see above).")
            return False
        try:
            a2l = fill_a2l_addresses(out)
            self._say(f"Built. The A2L file with the program's addresses: {a2l}")
        except (FileNotFoundError, StopIteration) as exc:
            self._say(f"Built, but the addresses could not be read: {exc}")
        return True

    def flash(self) -> None:
        if not self.build():
            return
        self._say(f"Flashing: {' '.join(BOARDS[self.key].flash)} …")
        proc = flash(self.folder.text(), self.key)
        self._say((proc.stdout + proc.stderr)[-4000:] or "Done.")
