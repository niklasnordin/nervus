"""The welcome window: where a new user starts (a guided first model, a tour, a beginner example with its
tutorial) and where a returning user finds the recent models. Shown at startup until it is switched off;
**Help ▸ Welcome** brings it back."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal, SignalInstance
from PySide6.QtWidgets import (
    QCheckBox,
    QCommandLinkButton,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nervus import __version__

SHOW_AT_STARTUP = "welcome/showAtStartup"
FIRST_TUTORIAL = "first-model"


def setting_bool(settings: QSettings, key: str, default: bool) -> bool:
    """A boolean from QSettings: INI files and some platforms return the strings "true"/"false"."""
    value = settings.value(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def show_at_startup(settings: QSettings) -> bool:
    return setting_bool(settings, SHOW_AT_STARTUP, True)


def beginner_examples() -> list[tuple[str, str, Path, str]]:
    """(title, detail, model path, help page) for the beginner tutorials whose model is installed, in tutorial
    order; the first tutorial is left out (it has its own button)."""
    from nervus.examples import examples_dir
    from nervus.tutorials import ordered

    folder = examples_dir()
    out = []
    for t in ordered():
        if t.level != "beginner" or t.slug == FIRST_TUTORIAL:
            continue
        model = folder / t.models[0]
        if model.exists():
            out.append((t.title, f"{t.minutes} min · {t.category}", model, t.help_page))
    return out


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.1)
    label.setFont(font)
    return label


class WelcomeDialog(QDialog):
    """The start window. It only asks; the main window acts on the signals."""

    firstModelRequested = Signal()
    tourRequested = Signal()
    newModelRequested = Signal()
    openRequested = Signal()
    exampleRequested = Signal(Path, str)
    """The model to open and the help page of its tutorial."""
    recentRequested = Signal(Path)
    tutorialsRequested = Signal()

    def __init__(self, settings: QSettings, recent: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Welcome to Nervus")
        self.setMinimumSize(820, 500)

        title = QLabel("<h2>Welcome to Nervus</h2>")
        intro = QLabel(
            f"Version {__version__}. Model a system as a block diagram, simulate it, test it and generate C code "
            "for an ECU. New here? Build your first model: a cruise control, step by step, in about 30 minutes."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")

        # ---------------------------------------------------------------- start
        start = QVBoxLayout()
        start.addWidget(_heading("Start"))
        self.first_model_button = QCommandLinkButton(
            "Build your first model", "Opens the step-by-step tutorial next to an empty model"
        )
        self.tour_button = QCommandLinkButton("Take a tour of the editor", "Eight stops, about a minute")
        self.new_button = QCommandLinkButton("Blank model", "Start from an empty canvas")
        self.open_button = QCommandLinkButton("Open a model…", "A .nervus model or a .nervlib library")
        for button, signal in (
            (self.first_model_button, self.firstModelRequested),
            (self.tour_button, self.tourRequested),
            (self.new_button, self.newModelRequested),
            (self.open_button, self.openRequested),
        ):
            button.clicked.connect(lambda _c=False, s=signal: self._emit_and_close(s))
            start.addWidget(button)
        start.addStretch(1)

        # ---------------------------------------------------------------- examples
        examples = QVBoxLayout()
        examples.addWidget(_heading("Learn from an example"))
        hint = QLabel("Opens a finished model and its tutorial. Press ▶ to run it.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        examples.addWidget(hint)
        self.examples = _wrapping_list()
        for title_text, detail, model, page in beginner_examples():
            item = QListWidgetItem(f"{title_text}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, (model, page))
            item.setToolTip(f"{model.name}: open the model and the tutorial")
            self.examples.addItem(item)
        self.examples.itemActivated.connect(self._open_example)
        examples.addWidget(self.examples, 1)
        row = QHBoxLayout()
        self.open_example_button = QPushButton("Open Example")
        self.open_example_button.clicked.connect(lambda: self._open_example(self.examples.currentItem()))
        self.open_example_button.setEnabled(False)
        self.examples.currentItemChanged.connect(lambda cur, _p: self.open_example_button.setEnabled(cur is not None))
        all_tutorials = QPushButton("All Tutorials")
        all_tutorials.clicked.connect(lambda: self._emit_and_close(self.tutorialsRequested))
        row.addWidget(all_tutorials)
        row.addStretch(1)
        row.addWidget(self.open_example_button)
        examples.addLayout(row)

        # ---------------------------------------------------------------- recent
        recents = QVBoxLayout()
        recents.addWidget(_heading("Recent"))
        self.recent = _wrapping_list()
        for path in recent:
            if not path.exists():
                continue
            item = QListWidgetItem(f"{path.name}\n{_short(path.parent)}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(str(path))
            self.recent.addItem(item)
        if self.recent.count() == 0:
            empty = QListWidgetItem("No recent models yet")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(empty)
        self.recent.itemActivated.connect(self._open_recent)
        recents.addWidget(self.recent, 1)

        columns = QHBoxLayout()
        columns.addLayout(start, 5)
        columns.addLayout(examples, 6)
        columns.addLayout(recents, 4)

        self.show_checkbox = QCheckBox("Show this window at startup")
        self.show_checkbox.setChecked(show_at_startup(settings))
        self.show_checkbox.toggled.connect(lambda on: self.settings.setValue(SHOW_AT_STARTUP, on))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.show_checkbox)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addSpacing(8)
        layout.addLayout(columns, 1)
        layout.addLayout(bottom)

    def _emit_and_close(self, signal: SignalInstance) -> None:
        self.accept()
        signal.emit()

    def _open_example(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        model, page = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        self.exampleRequested.emit(model, page)

    def _open_recent(self, item: QListWidgetItem | None) -> None:
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if path is None:
            return
        self.accept()
        self.recentRequested.emit(path)


def _wrapping_list() -> QListWidget:
    """A list whose long titles wrap instead of being cut off behind a horizontal scroll bar."""
    lst = QListWidget()
    lst.setWordWrap(True)
    lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    lst.setSpacing(2)
    return lst


def _short(folder: Path) -> str:
    """A folder for display: ``~`` for the home folder."""
    try:
        return "~/" + folder.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(folder)
