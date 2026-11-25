"""Regression tests for editor bugs found in review: dead menu actions, stale undo references, the unsaved-changes
flag after Refresh Links, a blocking close during a run, scope windows that outlived their model, and loading a
model mid-run."""

import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF
from PySide6.QtWidgets import QFileDialog

from nervus.gui.controllers import EditorController
from nervus.gui.models import DiagramModel, SimulationState
from nervus.gui.views.scope_window import ScopeWindow

EXAMPLES = Path(__file__).parent.parent.parent / "examples"


def test_file_actions_open_a_dialog(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """triggered(checked) used to fill the slots' optional path with False, so no dialog appeared."""
    asked = []

    def fake_dialog(*args, **kwargs):  # type: ignore[no-untyped-def]
        asked.append(args[1])
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_dialog)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_dialog)
    for act in (window.act_import_simulink, window.act_import_spice, window.act_export_spice):
        act.trigger()
    assert len(asked) == 3


def test_undo_connect_after_delete_and_undo(qtbot) -> None:  # type: ignore[no-untyped-def]
    editor = EditorController(DiagramModel())
    s = editor.add_block("sources.step", QPointF(0, 0))
    g = editor.add_block("math.gain", QPointF(150, 0))
    assert s is not None and g is not None
    assert editor.connect_ports(s, 0, g, 0)
    conn = editor.model.diagram.connections[0]
    assert editor.set_wire_points([(conn, None, [[80.0, 0.0], [80.0, 40.0]])])
    editor.delete([g], [])
    editor.undo_stack.undo()  # the block and its wire are back: the same wire, with its corners
    assert editor.model.diagram.connections == [conn]
    assert conn.meta["points"] == [[80.0, 0.0], [80.0, 40.0]]
    editor.undo_stack.undo()  # the corners
    editor.undo_stack.undo()  # the connection itself; this used to raise and leave the wire in place
    assert editor.model.diagram.connections == []
    editor.undo_stack.redo()
    editor.undo_stack.redo()
    (again,) = editor.model.diagram.connections
    assert again.meta["points"] == [[80.0, 0.0], [80.0, 40.0]]
    editor.undo_stack.redo()  # the delete
    assert editor.model.diagram.connections == [] and len(editor.model.diagram) == 1


def test_refresh_links_keeps_the_model_modified(window) -> None:  # type: ignore[no-untyped-def]
    window.files.open(EXAMPLES / "engine_comparison.nervus")
    assert not window.files.is_modified
    window.editor.add_block("math.gain", QPointF(0, 400))
    window.refresh_links()
    assert window.files.is_modified, "closing would lose the edit without asking"


def _run_until(window, qtbot, state: SimulationState) -> None:  # type: ignore[no-untyped-def]
    with qtbot.waitSignal(window.sim_model.stateChanged, timeout=30000, check_params_cb=lambda s: s is state):
        assert window.simulation.run()


def test_stop_and_wait_returns_promptly(window, qtbot) -> None:  # type: ignore[no-untyped-def]
    """The thread's quit() was queued to the GUI thread, which was blocked in wait(): closing took 5 s."""
    window.files.open(EXAMPLES / "mass_spring_damper_pid.nervus")
    window.diagram_model.root.settings.t_final = 1e6
    window.diagram_model.root.settings.max_step = 1e-3
    with qtbot.waitSignal(window.sim_model.stateChanged, timeout=10000):
        assert window.simulation.run()
    window.simulation.stop()
    start = time.perf_counter()
    window.simulation.wait(5000)
    assert time.perf_counter() - start < 2.0
    qtbot.waitUntil(lambda: not window.simulation.is_running, timeout=5000)


def test_new_and_open_are_disabled_while_running(window, qtbot) -> None:  # type: ignore[no-untyped-def]
    window.files.open(EXAMPLES / "mass_spring_damper_pid.nervus")
    window.diagram_model.root.settings.t_final = 1e6
    window.diagram_model.root.settings.max_step = 1e-3
    with qtbot.waitSignal(window.sim_model.stateChanged, timeout=10000):
        assert window.simulation.run()
    assert not window.act_new.isEnabled() and not window.act_open.isEnabled()
    assert not window.examples_menu.isEnabled()
    window.simulation.stop()
    qtbot.waitUntil(lambda: window.act_new.isEnabled(), timeout=10000)


def test_scope_windows_are_deleted_with_their_model(window, qtbot) -> None:  # type: ignore[no-untyped-def]
    for _ in range(3):
        window.files.open(EXAMPLES / "mass_spring_damper_pid.nervus")
        _run_until(window, qtbot, SimulationState.FINISHED)
    window.files.new()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.findChildren(ScopeWindow) == []
