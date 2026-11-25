"""Model-view-controller: views change the model only through controllers, as undoable commands."""

from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtWidgets import QGraphicsSceneMouseEvent

from nervus.gui.models import DiagramModel


def _mouse(scene, kind: QEvent.Type, pos: QPointF) -> None:  # type: ignore[no-untyped-def]
    event = QGraphicsSceneMouseEvent(kind)
    event.setScenePos(pos)
    event.setButton(Qt.MouseButton.LeftButton)
    event.setButtons(Qt.MouseButton.LeftButton)
    handler = {
        QEvent.Type.GraphicsSceneMousePress: scene.mousePressEvent,
        QEvent.Type.GraphicsSceneMouseMove: scene.mouseMoveEvent,
        QEvent.Type.GraphicsSceneMouseRelease: scene.mouseReleaseEvent,
    }[kind]
    handler(event)


def test_resizing_a_block_changes_the_model_only_on_release(window) -> None:  # type: ignore[no-untyped-def]
    g = window.editor.add_block("math.gain", QPointF(0, 0))
    scene = window.scene
    item = scene._item(g)
    item.setSelected(True)
    grip = item.mapToScene(item.resize_handle().center())
    _mouse(scene, QEvent.Type.GraphicsSceneMousePress, grip)
    assert scene._resizing is not None
    _mouse(scene, QEvent.Type.GraphicsSceneMouseMove, grip + QPointF(100, 60))
    assert "size" not in g.meta, "the drag is a preview: the model is unchanged until the mouse is released"
    assert item.size[0] > 80
    count = window.editor.undo_stack.count()
    _mouse(scene, QEvent.Type.GraphicsSceneMouseRelease, grip + QPointF(100, 60))
    assert g.meta["size"] == list(item.size)
    assert window.editor.undo_stack.count() == count + 1
    window.editor.undo_stack.undo()
    assert "size" not in g.meta


def test_refresh_links_is_one_undo_step(window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from nervus.io import load, save

    ed = window.editor
    c = ed.add_block("sources.constant", QPointF(0, 0))
    g = ed.add_block("math.gain", QPointF(150, 0), gain=3.0)
    ed.connect_ports(c, 0, g, 0)
    sub = ed.create_subsystem([g])
    lib_path = tmp_path / "parts.nervlib"
    assert ed.save_to_library(sub, lib_path)
    lib = load(lib_path)
    lib.block(sub.name).system.block("Gain").set_param("gain", 7.0)  # type: ignore[attr-defined]
    save(lib, lib_path)

    window.refresh_links()
    gain = lambda: window.diagram_model.root.block(sub.name).system.block("Gain").params["gain"]  # noqa: E731
    assert gain() == 7.0
    assert window.files.is_modified
    assert ed.undo_stack.undoText() == "Refresh library links"
    ed.undo_stack.undo()
    assert gain() == 3.0
    ed.undo_stack.redo()
    assert gain() == 7.0


def test_refresh_links_is_refused_while_editing_is_disabled(window) -> None:  # type: ignore[no-untyped-def]
    window.editor.set_editing_enabled(False)
    try:
        assert window.editor.refresh_links() is None
    finally:
        window.editor.set_editing_enabled(True)


def test_the_canvas_gives_the_model_block_widths(window) -> None:  # type: ignore[no-untyped-def]
    from nervus.gui.views.items import block_width

    assert window.diagram_model.width_of is block_width
    assert DiagramModel().width_of is None  # without a canvas: the layout's default width


def test_imports_report_errors_through_the_file_controller(window, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    messages: list[str] = []
    window.files.errorOccurred.disconnect()  # no message box in a test
    window.files.errorOccurred.connect(messages.append)
    before = window.diagram_model.root
    assert window.files.import_spice(tmp_path / "missing.cir") is None
    assert window.files.import_simulink(tmp_path / "missing.slx") is None
    assert len(messages) == 2 and all(m.startswith("Cannot read") for m in messages)
    assert window.diagram_model.root is before


def test_saving_an_untitled_model_names_it(window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    names: list[str] = []
    window.diagram_model.nameChanged.connect(names.append)
    window.files.new()
    assert window.files.save_as(tmp_path / "pump.nervus")
    assert window.diagram_model.root.name == "pump" and names == ["pump"]


def test_test_suite_files_go_through_the_test_controller(window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from nervus.core.testcase import TestCase

    window.editor.set_tests([TestCase("a")])
    path = window.tests.export_suite(tmp_path / "s.nervtest", list(window.diagram_model.root.tests))
    assert window.tests.import_suite(path) == 1
    assert [c.name for c in window.diagram_model.root.tests] == ["a", "a 2"]
    window.editor.undo_stack.undo()
    assert [c.name for c in window.diagram_model.root.tests] == ["a"]
