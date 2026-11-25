"""Runs test cases on a worker thread and reports each result."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from nervus.core.testcase import TestCase
from nervus.coverage import Coverage
from nervus.gui.controllers.editor_controller import EditorController
from nervus.gui.models.diagram_model import DiagramModel
from nervus.gui.models.simulation_model import SimulationModel
from nervus.testing import TestResult, run_test


class _Worker(QObject):
    resultReady = Signal(object)  # TestResult
    done = Signal()

    def __init__(self, model: DiagramModel, cases: list[TestCase], stop: threading.Event, coverage: Coverage) -> None:
        super().__init__()
        self.model, self.cases, self.stop, self.coverage = model, cases, stop, coverage

    @Slot()
    def run(self) -> None:
        base = self.model.file_path.parent if self.model.file_path else None
        for case in self.cases:
            if self.stop.is_set():
                break
            self.resultReady.emit(run_test(self.model.root, case, base, self.coverage))
        self.done.emit()


class TestController(QObject):
    __test__ = False

    started = Signal()
    resultReady = Signal(object)  # TestResult
    finished = Signal()

    def __init__(
        self, model: DiagramModel, sim: SimulationModel, editor: EditorController, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.model, self.sim, self.editor = model, sim, editor
        self.results: dict[str, TestResult] = {}
        self.coverage: Coverage | None = None
        """Model coverage of the last test run."""
        self._thread: QThread | None = None
        self._stop = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def run(self, cases: list[TestCase]) -> bool:
        if self.is_running or self.sim.is_running or not cases:
            return False
        self._stop.clear()
        self.editor.set_editing_enabled(False)  # tests resolve parameters and drive In blocks
        thread = QThread(self)
        self.coverage = Coverage()
        worker = _Worker(self.model, cases, self._stop, self.coverage)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.resultReady.connect(self._on_result)
        worker.done.connect(self._on_done)
        worker.done.connect(thread.quit, Qt.ConnectionType.DirectConnection)  # see SimulationController.run
        thread.finished.connect(worker.deleteLater)
        self._thread, self._worker = thread, worker
        self.started.emit()
        thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout_ms: int = 60000) -> None:
        if self._thread is not None:
            self._thread.wait(timeout_ms)

    # ------------------------------------------------------------------ test cases and files

    def unique_name(self, base: str) -> str:
        """``base``, or ``base 2``, ``base 3``, ... whichever no test case of the model has yet."""
        names = {c.name for c in self.model.root.tests}
        if base not in names:
            return base
        k = 2
        while f"{base} {k}" in names:
            k += 1
        return f"{base} {k}"

    def export_suite(self, path: str | Path, cases: list[TestCase]) -> Path:
        from nervus.testing import save_suite

        return Path(save_suite(cases, path))

    def import_suite(self, path: str | Path) -> int:
        """Add the test cases of a suite file (one undo step); cases whose names exist get a unique name. Raises
        :class:`~nervus.core.NervusError` if the file cannot be read."""
        from nervus.testing import load_suite

        new = load_suite(path)
        tests = list(self.model.root.tests)
        for case in new:
            case.name = self.unique_name(case.name)
            tests.append(case)
        self.editor.set_tests(tests, f"Import {len(new)} test case{'s' if len(new) != 1 else ''}")
        return len(new)

    def save_report(self, path: str | Path) -> Path:
        """An HTML report of the last results, in the order of the model's test cases."""
        from nervus.testing import save_html_report

        results = [self.results[c.name] for c in self.model.root.tests if c.name in self.results]
        title = f"Test report — {self.model.root.name or 'model'}"
        return Path(save_html_report(results, path, title, self.coverage))

    @Slot(object)
    def _on_result(self, res: TestResult) -> None:
        self.results[res.case.name] = res
        if res.result is not None:
            self.sim.add_run(res.result, f"test: {res.case.name}")
        self.resultReady.emit(res)

    @Slot()
    def _on_done(self) -> None:
        self.editor.set_editing_enabled(True)
        self.finished.emit()
