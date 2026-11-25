"""Code generation dialog: shows the generated C files, the interface, and runs the SIL check."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nervus.codegen import GeneratedCode
from nervus.core import Block, Diagram
from nervus.gui.controllers.codegen_controller import CodegenController
from nervus.gui.theme import monospace_font


class CodegenDialog(QDialog):
    def __init__(
        self,
        controller: CodegenController,
        target: Diagram | Block,
        code: GeneratedCode,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.controller, self.target, self.code = controller, target, code
        self.saved_to: Path | None = None
        self.setWindowTitle(f"C Code — {code.name}")
        self.resize(940, 700)

        info = self.info = QLabel()
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        self.tasks_box = QCheckBox("One function per task (for an RTOS)")
        self.tasks_box.setToolTip(
            "Generate one function per rate, e.g. model_task_10ms() and model_task_100ms(), to call from the "
            "tasks of an RTOS. Signals between tasks need Rate Transition blocks."
        )
        self.tasks_box.setChecked(bool(code.tasks))
        self.tasks_box.toggled.connect(lambda _on: self._regenerate())
        self.integer_box = QCheckBox("Integer arithmetic only (no floating point)")
        self.integer_box.setToolTip(
            "For processors without a floating-point unit: signals stay stored integers and every operation is "
            "integer arithmetic, bit-true with the simulation. Needs integer or fixed-point types (binary-point "
            "scaling) on every signal."
        )
        self.integer_box.setChecked(code.integer_only)
        self.integer_box.toggled.connect(lambda _on: self._regenerate())

        self.tabs = QTabWidget()
        self._show_code()

        self.sil_label = QLabel(
            "Software-in-the-loop: compile this code and compare it with the model, step by step."
            if controller.compiler_available()
            else "Install a C compiler (gcc or clang) to run the software-in-the-loop check."
        )
        self.sil_label.setObjectName("muted")
        self.sil_label.setWordWrap(True)
        self.sil_button = QPushButton("Run SIL check")
        self.sil_button.setEnabled(controller.compiler_available())
        self.misra_button = QPushButton("MISRA check")
        self.misra_button.setEnabled(controller.misra_available())
        self.misra_button.setToolTip(
            "Check the code against MISRA C:2012 with cppcheck's MISRA addon"
            if controller.misra_available()
            else "Install cppcheck (brew install cppcheck, apt install cppcheck) to check the code against MISRA C"
        )
        self.coverage_button = QPushButton("C coverage (tests)")
        self.coverage_button.setEnabled(controller.coverage_available())
        self.coverage_button.setToolTip(
            "Run the model's test cases through this code (compiled with gcov) and show which lines and branches "
            "they execute"
            if controller.coverage_available()
            else "Needs a C compiler and gcov"
        )
        self.save_button = QPushButton("Save to folder…")
        self.save_button.setObjectName("primary")
        self.report_button = QPushButton("Open report")
        self.report_button.setEnabled(False)
        self.copy_button = QPushButton("Copy")
        close = QPushButton("Close")

        buttons = QHBoxLayout()
        buttons.addWidget(self.sil_button)
        buttons.addWidget(self.misra_button)
        buttons.addWidget(self.coverage_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        buttons.addWidget(self.report_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.tasks_box)
        layout.addWidget(self.integer_box)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.sil_label)
        layout.addLayout(buttons)

        self.sil_button.clicked.connect(self.run_sil)
        self.misra_button.clicked.connect(self.run_misra)
        self.coverage_button.clicked.connect(lambda _c=False: self.run_coverage())
        self.save_button.clicked.connect(self._save)
        self.report_button.clicked.connect(self._open_report)
        self.copy_button.clicked.connect(self._copy)
        close.clicked.connect(self.accept)

    def _show_code(self) -> None:
        code = self.code
        rates = ", ".join(f"{r:g} s" for r in code.rates)
        ins = ", ".join(f"{n}[{w}]" for n, w in code.inputs) or "none"
        outs = ", ".join(f"{n}[{w}]" for n, w in code.outputs) or "none"
        call = (
            "call " + ", ".join(f"<code>{t}()</code>" for t in code.tasks) + " from their tasks, or "
            if code.tasks
            else "call "
        )
        self.info.setText(
            f"<b>{code.name}</b>: {call}<code>{code.name}_step()</code> every <b>{code.base_period:g} s</b> "
            f"(rates: {rates}).<br>Inputs: {ins}<br>Outputs: {outs}<br>"
            f"Tunable parameters: {len(code.params)}"
        )
        current = self.tabs.currentIndex()
        self.tabs.clear()
        mono = monospace_font()
        for fname in (f"{code.name}.h", f"{code.name}.c", "main_example.c", f"{code.name}.a2l"):
            edit = QPlainTextEdit(code.files[fname])
            edit.setReadOnly(True)
            edit.setFont(mono)
            edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.tabs.addTab(edit, fname)
        self.tabs.setCurrentIndex(max(0, current))

    def _regenerate(self) -> None:
        tasks, integer = self.tasks_box.isChecked(), self.integer_box.isChecked()
        code = self.controller.generate(self.target, tasks=tasks, integer_only=integer)
        if code is None:  # e.g. a missing Rate Transition, or a double signal: the controller reported why
            for box, was in ((self.tasks_box, bool(self.code.tasks)), (self.integer_box, self.code.integer_only)):
                box.blockSignals(True)
                box.setChecked(was)
                box.blockSignals(False)
            self.sil_label.setText("Cannot generate this kind of code; see Diagnostics for the reason.")
            return
        self.code = code
        self._show_code()

    def run_sil(self) -> None:
        self.sil_label.setText("Compiling and running…")
        QGuiApplication.processEvents()
        res = self.controller.sil_check(self.target, self.code)
        if res is None:
            self.sil_label.setText("The SIL check could not run; see the status bar.")
            return
        icon = "✓" if res.passed else "✗"
        self.sil_label.setText(f"{icon} " + res.summary().replace("\n", "   "))

    def run_misra(self) -> None:
        self.sil_label.setText("Checking against MISRA C:2012…")
        QGuiApplication.processEvents()
        findings = self.controller.misra_check(self.code)
        if findings is None:
            self.sil_label.setText("The MISRA check could not run; see Diagnostics.")
        elif not findings:
            self.sil_label.setText("✓ MISRA C:2012: no findings (cppcheck), apart from the documented deviations")
        else:
            shown = "   ".join(str(f) for f in findings[:3])
            more = f"   … and {len(findings) - 3} more" if len(findings) > 3 else ""
            self.sil_label.setText(f"✗ MISRA C:2012: {len(findings)} finding(s): {shown}{more}")

    def run_coverage(self, open_report: bool = True) -> Path | None:
        """C code coverage by the test cases; the annotated report opens in the browser."""
        import tempfile

        self.sil_label.setText("Running the test cases through the C code…")
        QGuiApplication.processEvents()
        cov = self.controller.c_coverage(self.target, self.code)
        if cov is None:
            self.sil_label.setText("The C coverage could not run; see Diagnostics.")
            return None
        (lh, lt), (bh, bt) = cov.lines, cov.branch_counts
        self.sil_label.setText(f"C coverage by {cov.runs} test case(s): lines {cov.line_percent:.0f} % ({lh}/{lt}), "
                               f"branches {cov.branch_percent:.0f} % ({bh}/{bt})")  # fmt: skip
        path = cov.save_html(Path(tempfile.mkdtemp()) / f"{self.code.name}_c_coverage.html",
                             f"C code coverage — {self.code.name}")  # fmt: skip
        if open_report:
            webbrowser.open(path.as_uri())
        return path

    def save_to(self, folder: str | Path) -> None:
        self.controller.save(self.code, folder)
        self.saved_to = Path(folder)
        self.report_button.setEnabled(True)

    def _save(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save generated code")
        if folder:
            self.save_to(folder)
            self.sil_label.setText(f"Saved {len(self.code.files)} files to {folder}")

    def _open_report(self) -> None:
        if self.saved_to is not None:
            webbrowser.open((self.saved_to / f"{self.code.name}_report.html").as_uri())

    def _copy(self) -> None:
        edit = self.tabs.currentWidget()
        if isinstance(edit, QPlainTextEdit):
            QGuiApplication.clipboard().setText(edit.toPlainText())
