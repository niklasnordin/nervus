"""Test Manager: create, edit and run test cases stored with the model."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nervus.blocks.ports import Inport
from nervus.core.errors import NervusError
from nervus.core.expressions import format_value, parse_value
from nervus.core.signals import Signal as InputSignal
from nervus.core.testcase import CHECK_KINDS, Check, TestCase
from nervus.gui.controllers.editor_controller import EditorController
from nervus.gui.controllers.test_controller import TestController
from nervus.gui.models.diagram_model import DiagramModel
from nervus.testing import TestResult


def parse_points(text: str) -> list[tuple[float, Any]]:
    """``"0: 5, 0.2: 60, 1.2: 20"`` -> [(0, 5), (0.2, 60), (1.2, 20)]. Values may be vectors ``[1 2]``."""
    points = []
    depth, start = 0, 0
    parts = []
    for k, ch in enumerate(text):
        depth += ch == "["
        depth -= ch == "]"
        if ch == "," and depth == 0:
            parts.append(text[start:k])
            start = k + 1
    parts.append(text[start:])
    for part in (p.strip() for p in parts):
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"expected 'time: value', got {part!r}")
        t, v = part.split(":", 1)
        points.append((float(parse_value(t)), parse_value(v)))
    if not points:
        raise ValueError("no points")
    return points


def format_points(sig: InputSignal) -> str:
    return ", ".join(
        f"{format_value(float(t))}: {format_value(v[0] if v.size == 1 else v.tolist())}"
        for t, v in zip(sig.time, sig.values, strict=True)
    )


def parse_args(text: str) -> dict[str, Any]:
    """``"lower=0, upper=1.5"`` -> {"lower": 0.0, "upper": 1.5}. Non-numeric values stay strings."""
    out: dict[str, Any] = {}
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"expected 'name=value', got {part!r}")
        k, v = (x.strip() for x in part.split("=", 1))
        try:
            out[k] = parse_value(v)
        except ValueError:
            out[k] = v
    return out


class TestCaseDialog(QDialog):
    __test__ = False

    def __init__(self, case: TestCase, inport_names: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Test Case — {case.name}")
        self.resize(760, 640)
        self.case: TestCase | None = None
        self.name = QLineEdit(case.name)
        self.description = QLineEdit(case.description)
        self.stop = QLineEdit("" if case.stop_time is None else format_value(case.stop_time))
        self.stop.setPlaceholderText("model stop time")
        self.pset = QLineEdit(case.parameter_set)
        self.reqs = QLineEdit(", ".join(case.requirements))
        self.reqs.setPlaceholderText("ids of the requirements this test verifies, e.g. IDLE-01, IDLE-02")
        self.pset.setPlaceholderText("optional .nervcal file")
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)
        pset_row = QHBoxLayout()
        pset_row.addWidget(self.pset)
        pset_row.addWidget(browse)

        self.inputs = QTableWidget(len(inport_names), 3)
        self.inputs.setHorizontalHeaderLabels(["In block", "points  (time: value, ...)", "interpolation"])
        self.inputs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.inputs.verticalHeader().setVisible(False)
        for row, name in enumerate(inport_names):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.inputs.setItem(row, 0, item)
            sig = case.inputs.get(name)
            self.inputs.setItem(row, 1, QTableWidgetItem(format_points(sig) if sig else ""))
            combo = QComboBox()
            combo.addItems(["hold", "linear"])
            combo.setCurrentText(sig.interpolation if sig else "hold")
            self.inputs.setCellWidget(row, 2, combo)

        self.params = QTableWidget(0, 2)
        self.params.setHorizontalHeaderLabels(["variable", "value"])
        self.params.horizontalHeader().setStretchLastSection(True)
        self.params.verticalHeader().setVisible(False)
        for k, v in case.parameters.items():
            self._add_param(k, v if isinstance(v, str) else format_value(v))
        add_param = QPushButton("Add override")
        add_param.clicked.connect(lambda: self._add_param("", ""))

        self.checks = QTableWidget(0, 3)
        self.checks.setHorizontalHeaderLabels(["check", "signal", "arguments  (name=value, ...)"])
        self.checks.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.checks.verticalHeader().setVisible(False)
        for c in case.checks:
            self._add_check(c.kind, c.signal, ", ".join(f"{k}={v}" for k, v in c.args.items()))
        add_check = QPushButton("Add check")
        add_check.clicked.connect(lambda: self._add_check("bounds", "", "lower=0, upper=1"))
        kinds_help = QLabel("<br>".join(f"<b>{k}</b>: {v}" for k, v in CHECK_KINDS.items()))
        kinds_help.setObjectName("muted")
        kinds_help.setWordWrap(True)

        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Stop time [s]", self.stop)
        form.addRow("Parameter set", pset_row)
        form.addRow("Requirements", self.reqs)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("<b>Inputs</b> (empty = use the In block's value)"))
        layout.addWidget(self.inputs, 2)
        params_bar = QHBoxLayout()
        params_bar.addWidget(QLabel("<b>Parameter overrides</b>"))
        params_bar.addStretch(1)
        params_bar.addWidget(add_param)
        layout.addLayout(params_bar)
        layout.addWidget(self.params, 1)
        checks_bar = QHBoxLayout()
        checks_bar.addWidget(QLabel("<b>Checks</b>"))
        checks_bar.addStretch(1)
        checks_bar.addWidget(add_check)
        layout.addLayout(checks_bar)
        layout.addWidget(self.checks, 2)
        layout.addWidget(kinds_help)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Parameter set", "", "Parameter sets (*.nervcal)")
        if path:
            self.pset.setText(path)

    def _add_param(self, name: str, value: str) -> None:
        r = self.params.rowCount()
        self.params.insertRow(r)
        self.params.setItem(r, 0, QTableWidgetItem(name))
        self.params.setItem(r, 1, QTableWidgetItem(value))

    def _add_check(self, kind: str, signal: str, args: str) -> None:
        r = self.checks.rowCount()
        self.checks.insertRow(r)
        combo = QComboBox()
        combo.addItems(list(CHECK_KINDS))
        combo.setCurrentText(kind)
        self.checks.setCellWidget(r, 0, combo)
        self.checks.setItem(r, 1, QTableWidgetItem(signal))
        self.checks.setItem(r, 2, QTableWidgetItem(args))

    @staticmethod
    def _text(table: QTableWidget, r: int, c: int) -> str:
        item = table.item(r, c)
        return item.text().strip() if item is not None else ""

    def build(self) -> TestCase:
        """The edited test case. Raises ValueError/NervusError with a message on invalid input."""
        name = self.name.text().strip()
        if not name:
            raise ValueError("the test case needs a name")
        stop_text = self.stop.text().strip()
        stop = float(parse_value(stop_text)) if stop_text else None
        inputs = {}
        for r in range(self.inputs.rowCount()):
            text = self._text(self.inputs, r, 1)
            if text:
                combo = self.inputs.cellWidget(r, 2)
                interp = combo.currentText() if isinstance(combo, QComboBox) else "hold"
                pts = parse_points(text)
                sig = InputSignal.steps(pts) if interp == "hold" else InputSignal.ramps(pts)
                inputs[self._text(self.inputs, r, 0)] = sig
        params: dict[str, Any] = {}
        for r in range(self.params.rowCount()):
            k, v = self._text(self.params, r, 0), self._text(self.params, r, 1)
            if k:
                params[k] = parse_value(v)
        checks = []
        for r in range(self.checks.rowCount()):
            combo = self.checks.cellWidget(r, 0)
            signal = self._text(self.checks, r, 1)
            if not signal:
                continue
            kind = combo.currentText() if isinstance(combo, QComboBox) else "bounds"
            checks.append(Check(kind, signal, parse_args(self._text(self.checks, r, 2))))
        reqs = [r.strip() for r in self.reqs.text().replace(";", ",").split(",") if r.strip()]
        return TestCase(name, self.description.text().strip(), inputs, params, self.pset.text().strip(), stop, checks,
                        reqs)  # fmt: skip

    def _accept(self) -> None:
        try:
            self.case = self.build()
        except (ValueError, NervusError) as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class TestManagerPanel(QWidget):
    __test__ = False

    openResult = Signal(str)  # test name: show it in the Data Inspector
    statusMessage = Signal(str)

    def __init__(
        self, model: DiagramModel, editor: EditorController, runner: TestController, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.model, self.editor, self.runner = model, editor, runner
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Test case", "Result", "Details"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 70)
        self.buttons: dict[str, QPushButton] = {}
        bar = QHBoxLayout()
        for key, label in (
            ("new", "New"),
            ("edit", "Edit…"),
            ("duplicate", "Duplicate"),
            ("delete", "Delete"),
            ("import", "Import…"),
            ("export", "Export…"),
        ):
            self.buttons[key] = QPushButton(label)
            bar.addWidget(self.buttons[key])
        self.buttons["import"].setToolTip("Add the test cases of a .nervtest file to this model")
        self.buttons["export"].setToolTip("Save the selected (or all) test cases to a .nervtest file")
        bar.addStretch(1)
        self.coverage_label = QLabel("")
        self.coverage_label.setObjectName("muted")
        self.buttons["coverage"] = QPushButton("Coverage report")
        self.buttons["coverage"].setToolTip("Open the model coverage report of the last test run")
        self.buttons["coverage"].setEnabled(False)
        self.buttons["report"] = QPushButton("Report…")
        self.buttons["report"].setToolTip("Save an HTML report of the last test run")
        self.buttons["report"].setEnabled(False)
        bar.addWidget(self.buttons["report"])
        bar.addWidget(self.coverage_label)
        bar.addWidget(self.buttons["coverage"])
        for key, label in (("run", "Run selected"), ("run_all", "Run all")):
            self.buttons[key] = QPushButton(label)
            bar.addWidget(self.buttons[key])
        self.buttons["run_all"].setObjectName("primary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(bar)
        layout.addWidget(self.tree)

        self.buttons["new"].clicked.connect(self.new_case)
        self.buttons["edit"].clicked.connect(self.edit_selected)
        self.buttons["duplicate"].clicked.connect(self.duplicate_selected)
        self.buttons["delete"].clicked.connect(self.delete_selected)
        self.buttons["run"].clicked.connect(self.run_selected)
        self.buttons["run_all"].clicked.connect(self.run_all)
        self.buttons["coverage"].clicked.connect(self.open_coverage_report)
        self.buttons["report"].clicked.connect(self._ask_report)
        self.buttons["import"].clicked.connect(self._ask_import)
        self.buttons["export"].clicked.connect(self._ask_export)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        model.testsChanged.connect(self.refresh)
        model.diagramReset.connect(self._reset)
        runner.resultReady.connect(self._on_result)
        runner.started.connect(lambda: self._set_running(True))
        runner.finished.connect(self._finished)
        self.refresh()

    # ------------------------------------------------------------------ list

    def _reset(self) -> None:
        self.runner.results.clear()
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        for case in self.model.root.tests:
            item = QTreeWidgetItem([case.name, "", f"{len(case.checks)} check(s)"])
            item.setToolTip(0, case.description or case.name)
            res = self.runner.results.get(case.name)
            if res is not None:
                self._show_result(item, res)
            self.tree.addTopLevelItem(item)

    def _show_result(self, item: QTreeWidgetItem, res: TestResult) -> None:
        item.setText(1, "✓ pass" if res.passed else "✗ fail")
        failed = [c for c in res.checks if not c.passed]
        details = (
            res.error.splitlines()[0]
            if res.error
            else (
                "; ".join(f"{c.check.describe()}: {c.message}" for c in failed)
                or f"all {len(res.checks)} checks passed"
            )
        )
        item.setText(2, details)
        item.setToolTip(2, res.summary())

    def selected_names(self) -> list[str]:
        return [i.text(0) for i in self.tree.selectedItems()]

    def _cases(self, names: list[str] | None = None) -> list[TestCase]:
        return [c for c in self.model.root.tests if names is None or c.name in names]

    def _inports(self) -> list[str]:
        return [b.name for b in self.model.root.blocks if isinstance(b, Inport)]

    # ------------------------------------------------------------------ editing

    def _unique(self, base: str) -> str:
        return self.runner.unique_name(base)

    def edit_case(self, case: TestCase, is_new: bool) -> TestCase | None:
        dlg = TestCaseDialog(case, self._inports(), self)
        if not dlg.exec() or dlg.case is None:
            return None
        new = dlg.case
        tests = list(self.model.root.tests)
        if any(c.name == new.name for c in tests if c is not case):
            self.statusMessage.emit(f"A test case named '{new.name}' already exists.")
            return None
        if is_new:
            tests.append(new)
        else:
            tests[tests.index(case)] = new
        self.editor.set_tests(tests, f"{'Add' if is_new else 'Edit'} test case {new.name}")
        return new

    def new_case(self) -> None:
        self.edit_case(TestCase(self._unique("test")), is_new=True)

    def edit_selected(self) -> None:
        cases = self._cases(self.selected_names())
        if cases:
            self.edit_case(cases[0], is_new=False)

    def duplicate_selected(self) -> None:
        tests = list(self.model.root.tests)
        for case in self._cases(self.selected_names()):
            copy = TestCase.from_dict(case.to_dict())
            copy.name = self._unique(case.name + " copy")
            tests.append(copy)
        self.editor.set_tests(tests, "Duplicate test case")

    def delete_selected(self) -> None:
        names = set(self.selected_names())
        if names:
            self.editor.set_tests([c for c in self.model.root.tests if c.name not in names], "Delete test case")

    # ------------------------------------------------------------------ running

    def run_selected(self) -> None:
        self.runner.run(self._cases(self.selected_names()))

    def run_all(self) -> None:
        self.runner.run(self._cases())

    def _set_running(self, running: bool) -> None:
        for b in self.buttons.values():
            b.setEnabled(not running)
        if running:
            self.statusMessage.emit("Running test cases…")

    def _on_result(self, res: TestResult) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item is not None and item.text(0) == res.case.name:
                self._show_result(item, res)

    def _finished(self) -> None:
        self._set_running(False)
        results = [self.runner.results[c.name] for c in self.model.root.tests if c.name in self.runner.results]
        passed = sum(r.passed for r in results)
        cov = self.runner.coverage
        if cov is not None and cov.total:
            self.coverage_label.setText(
                f"Coverage {cov.percent:.0f} %" + (f", MC/DC {cov.mcdc_percent:.0f} %" if cov.conditions else "")
            )
            self.coverage_label.setToolTip(cov.summary())
        self.buttons["coverage"].setEnabled(cov is not None and cov.total > 0)
        self.buttons["report"].setEnabled(bool(results))
        self.statusMessage.emit(f"Tests: {passed} of {len(results)} passed")

    # ------------------------------------------------------------------ files

    def export_suite(self, path: str, names: list[str] | None = None) -> str:
        return str(self.runner.export_suite(path, self._cases(names or None)))

    def import_suite(self, path: str) -> int:
        """Add the test cases of a suite file; cases whose names exist get a unique name."""
        from nervus.core import NervusError

        try:
            return self.runner.import_suite(path)
        except NervusError as exc:
            self.statusMessage.emit(str(exc))
            return 0

    def save_report(self, path: str) -> str:
        return str(self.runner.save_report(path))

    def _ask_export(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(self, "Export test cases", "tests.nervtest", "Test suites (*.nervtest)")
        if path:
            self.statusMessage.emit(f"Saved {self.export_suite(path, self.selected_names())}")

    def _ask_import(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "Import test cases", "", "Test suites (*.nervtest)")
        if path:
            self.statusMessage.emit(f"Imported {self.import_suite(path)} test cases")

    def _ask_report(self) -> None:
        import webbrowser
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(self, "Save test report", "test_report.html", "HTML (*.html)")
        if path:
            webbrowser.open(Path(self.save_report(path)).resolve().as_uri())

    def open_coverage_report(self) -> str | None:
        import tempfile
        import webbrowser
        from pathlib import Path

        cov = self.runner.coverage
        if cov is None:
            return None
        path = Path(tempfile.mkdtemp(prefix="nervus_cov_")) / "coverage.html"
        cov.save_html(path, f"Model coverage — {self.model.root.name}")
        webbrowser.open(path.as_uri())
        return str(path)

    def _double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            cases = self._cases([item.text(0)])
            if cases:
                self.edit_case(cases[0], is_new=False)
        else:
            self.openResult.emit(item.text(0))
