"""The welcome window and the editor tour."""

from pathlib import Path

from PySide6.QtCore import Qt

from nervus.gui.views.tour import EditorTour, editor_stops
from nervus.gui.views.welcome import SHOW_AT_STARTUP, WelcomeDialog, beginner_examples, show_at_startup
from nervus.tutorials import by_slug

EXAMPLES = Path(__file__).parent.parent.parent / "examples"


def test_welcome_lists_beginner_examples_and_recent_files(window, tmp_path) -> None:  # type: ignore[no-untyped-def]
    examples = beginner_examples()
    assert examples, "beginner tutorials with installed models"
    assert all(model.exists() for _t, _d, model, _p in examples)
    assert by_slug()["first-model"].title not in [t for t, *_ in examples]  # it has its own button
    missing = tmp_path / "gone.nervus"
    dlg = WelcomeDialog(window.qsettings, [EXAMPLES / "throttle_pi.nervus", missing], window)
    assert dlg.examples.count() == len(examples)
    assert dlg.recent.count() == 1  # the missing file is left out
    assert dlg.recent.item(0).data(Qt.ItemDataRole.UserRole) == EXAMPLES / "throttle_pi.nervus"
    dlg.close()


def test_welcome_show_at_startup_setting(window) -> None:  # type: ignore[no-untyped-def]
    assert show_at_startup(window.qsettings)  # on by default
    dlg = window.show_welcome()
    dlg.show_checkbox.setChecked(False)
    window.qsettings.sync()
    assert not show_at_startup(window.qsettings)  # also when the INI file returns the string "false"
    assert window.qsettings.value(SHOW_AT_STARTUP) in (False, "false")
    dlg.close()


def test_welcome_opens_example_with_its_tutorial(window) -> None:  # type: ignore[no-untyped-def]
    dlg = window.show_welcome()
    dlg.examples.setCurrentRow(0)
    model = dlg.examples.item(0).data(Qt.ItemDataRole.UserRole)[0]
    dlg.open_example_button.click()
    assert window.diagram_model.file_path.resolve() == model.resolve()
    assert window._help is not None and window._help.isVisible()
    assert window._help._current.startswith("page:tutorial_")


def test_welcome_first_model_opens_tutorial_beside_empty_model(window) -> None:  # type: ignore[no-untyped-def]
    window.files.open(EXAMPLES / "throttle_pi.nervus")
    dlg = window.show_welcome()
    dlg.first_model_button.click()
    assert len(window.diagram_model.root) == 0
    assert window._help._current == "page:tutorial_first_model"


def test_help_menu_has_welcome_and_tour(window) -> None:  # type: ignore[no-untyped-def]
    texts = [a.text() for a in window.menuBar().actions()[-1].menu().actions()]
    assert "&Welcome…" in texts and "Editor &Tour" in texts


def test_tour_visits_every_stop(window, qtbot) -> None:  # type: ignore[no-untyped-def]
    window.library_dock.hide()  # a hidden panel is shown when the tour gets to it
    tour = window.start_tour()
    assert isinstance(tour, EditorTour) and tour.isVisible()
    assert tour.hole is None  # the first stop is a centred note
    holes = []
    for _ in range(len(tour.stops) - 1):
        qtbot.keyClick(tour, Qt.Key.Key_Right)
        holes.append(tour.hole)
        assert window.rect().contains(tour.bubble.geometry()), tour.bubble.title.text()
    assert window.library_dock.isVisible()
    assert sum(h is not None for h in holes) == 6  # six parts of the window highlighted
    assert tour.bubble.next.text() == "Finish"
    qtbot.keyClick(tour, Qt.Key.Key_Left)
    assert tour.index == len(tour.stops) - 2
    with qtbot.waitSignal(tour.finished):
        qtbot.keyClick(tour, Qt.Key.Key_Escape)


def test_tour_stops_name_real_shortcuts(window) -> None:  # type: ignore[no-untyped-def]
    text = " ".join(s.text for s in editor_stops(window))
    assert window.act_run.shortcut().toString() in text  # Ctrl+R
    assert window.act_inspector.shortcut().toString() in text  # Ctrl+I
    assert window.act_flip.shortcut().toString() in text  # Ctrl+Shift+F


def test_tour_bubble_beside_its_target_and_not_at_the_bottom(window) -> None:  # type: ignore[no-untyped-def]
    """Regression: the note for the canvas sat at the bottom edge of the window and the one for the panels covered
    them, so on a screen with a dock the Next button was out of reach."""
    window.resize(1280, 800)
    tour = window.start_tour()
    for i, stop in enumerate(tour.stops):
        tour.go(i)
        bubble = tour.bubble.geometry()
        if tour.hole is not None and stop.title != "2. The canvas":
            assert not bubble.intersects(tour.hole), stop.title  # beside a small part, not on it
        assert bubble.bottom() < window.height() - 100, stop.title
    tour.go(2)
    assert tour.hole is not None and tour.hole.contains(tour.bubble.geometry())  # inside the canvas, at its top
    assert tour.bubble.y() < tour.hole.center().y()
    tour.close()


def test_tour_bubble_stays_on_the_screen(window) -> None:  # type: ignore[no-untyped-def]
    """A window taller than the screen: the bubble stays in the part that can be seen."""
    screen = window.screen().availableGeometry()
    window.resize(1000, screen.height() + 300)
    tour = window.start_tour()
    for i, stop in enumerate(tour.stops):
        tour.go(i)
        top_left = tour.bubble.mapToGlobal(tour.bubble.rect().topLeft())
        assert screen.contains(tour.bubble.rect().translated(top_left)), stop.title
    tour.close()


def test_tour_note_is_on_top_of_the_panels(window) -> None:  # type: ignore[no-untyped-def]
    """Regression: showing a panel raised it above the tour, so the Properties panel covered the note (and its
    Next button) from the third stop on, and was not dimmed."""
    tour = window.start_tour()
    for i, stop in enumerate(tour.stops):
        tour.go(i)
        for button in (tour.bubble.next, tour.bubble.back):
            centre = button.mapTo(window, button.rect().center())
            assert window.childAt(centre) is button, (stop.title, window.childAt(centre))
        for name in ("library_dock", "properties_dock", "diagnostics_dock"):
            dock = getattr(window, name)
            centre = dock.mapTo(window, dock.rect().center())
            hit = window.childAt(centre)
            assert hit is tour or tour.isAncestorOf(hit), (stop.title, name, hit)  # under the overlay
    tour.close()
