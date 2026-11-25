"""Runs simulations on a worker thread so the window stays responsive."""

from __future__ import annotations

import threading
import time
import traceback

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from nervus.core import ModelError, NervusError, SimulationSettings
from nervus.gui.controllers.editor_controller import EditorController
from nervus.gui.models.diagram_model import DiagramModel
from nervus.gui.models.simulation_model import SimulationModel, SimulationState
from nervus.sim import CompiledModel, SimResult, compile_diagram, simulate


class _Worker(QObject):
    progress = Signal(float)
    partial = Signal(object)  # SimResult with the Scope signals so far
    finished = Signal(object)  # SimResult
    failed = Signal(str, tuple)

    def __init__(self, model: CompiledModel, settings: SimulationSettings, stop: threading.Event) -> None:
        super().__init__()
        self.model, self.settings, self.stop = model, settings, stop
        self._last_emit = 0.0

    def _on_progress(self, t: float) -> None:
        now = time.monotonic()
        if now - self._last_emit > 0.05:  # at most 20 updates per second
            self._last_emit = now
            span = self.settings.t_final - self.settings.t_start
            self.progress.emit((t - self.settings.t_start) / span if span > 0 else 1.0)

    @Slot()
    def run(self) -> None:
        try:
            result = simulate(
                self.model,
                settings=self.settings,
                progress=self._on_progress,
                should_stop=self.stop.is_set,
                live=self.partial.emit,
            )
        except (NervusError, ValueError) as exc:
            blocks = getattr(exc, "blocks", ())
            self.failed.emit(str(exc), tuple(blocks))
        except Exception as exc:  # a bug in a block or user code; report rather than crash the GUI
            self.failed.emit(f"Unexpected error: {exc}\n\n{traceback.format_exc()}", ())
        else:
            self.finished.emit(result)


class SimulationController(QObject):
    def __init__(
        self,
        diagram_model: DiagramModel,
        sim_model: SimulationModel,
        editor: EditorController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.diagram_model = diagram_model
        self.sim_model = sim_model
        self.editor = editor
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._stop = threading.Event()
        diagram_model.diagramReset.connect(lambda: sim_model.set_result(None))

    @property
    def is_running(self) -> bool:
        return self.sim_model.is_running

    def run(self) -> bool:
        """Compile the diagram and start simulating. Returns False if compilation failed."""
        if self.is_running:
            return False
        try:
            # Compile on the GUI thread: it touches the blocks, which only this thread may change.
            compiled = compile_diagram(self.diagram_model.root)
        except ModelError as exc:
            self.sim_model.set_error(str(exc), exc.blocks)
            self.sim_model.set_state(SimulationState.FAILED)
            return False
        settings = SimulationSettings(**self.diagram_model.root.settings.to_dict())
        self._stop.clear()
        self.editor.set_editing_enabled(False)
        self.sim_model.set_error("")
        self.sim_model.set_state(SimulationState.RUNNING)
        self.sim_model.set_progress(0.0)

        thread = QThread(self)
        worker = _Worker(compiled, settings, self._stop)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.sim_model.set_progress)
        worker.partial.connect(self.sim_model.set_live_result)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        # Direct: quit() is thread-safe, and a queued call would never run while the GUI thread waits in wait().
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.failed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()
        return True

    def stop(self) -> None:
        if self.is_running:
            self._stop.set()

    def wait(self, timeout_ms: int = 30000) -> None:
        """Block until the current run ends (used by tests and on application exit)."""
        if self._thread is not None:
            self._thread.wait(timeout_ms)

    @Slot(object)
    def _on_finished(self, result: SimResult) -> None:
        self.editor.set_editing_enabled(True)
        self.sim_model.set_progress(1.0)
        self.sim_model.set_result(result)
        self.sim_model.add_run(result)
        stopped = result.stats is not None and result.stats.stopped_early
        self.sim_model.set_state(SimulationState.STOPPED if stopped else SimulationState.FINISHED)

    @Slot(str, tuple)
    def _on_failed(self, message: str, blocks: tuple[str, ...]) -> None:
        self.editor.set_editing_enabled(True)
        self.sim_model.set_error(message, blocks)
        self.sim_model.set_state(SimulationState.FAILED)


__all__ = ["SimulationController"]
