"""Entry point for the desktop editor: ``nervus [model.nervus]`` or ``python -m nervus.gui``."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from nervus.gui.qtfix import unhide_qt_plugins
from nervus.gui.theme import ThemeManager, ThemeMode
from nervus.gui.views.welcome import show_at_startup


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if unhide_qt_plugins():
        print("Nervus: cleared the macOS 'hidden' flag on Qt plugin files so Qt can load them.", file=sys.stderr)
    app = QApplication.instance() or QApplication(argv)
    assert isinstance(app, QApplication)
    app.setApplicationName("Nervus")
    app.setOrganizationName("Nervus")
    app.setStyle("Fusion")

    settings = QSettings("Nervus", "Nervus")
    try:
        mode = ThemeMode(settings.value("theme", ThemeMode.SYSTEM.value))
    except ValueError:
        mode = ThemeMode.SYSTEM
    ThemeManager.instance().set_mode(mode)

    from nervus.gui.views.main_window import MainWindow  # after the theme, so icons use its colours

    window = MainWindow(settings)
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        window.files.open(files[0])
    window.show()
    if not files and show_at_startup(settings):
        window.show_welcome()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
