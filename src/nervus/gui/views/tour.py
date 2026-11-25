"""A guided tour of the editor: the window is dimmed except for one part at a time, with a note next to it.

**Help ▸ Editor Tour** starts it; the welcome window offers it too. Keys: → or Enter for the next stop, ← for the
previous one, Esc to leave.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from nervus.gui.theme import current_theme
from nervus.tutorials import TUTORIALS

MARGIN = 6  # px around the highlighted part
BUBBLE_WIDTH = 340


@dataclass(frozen=True)
class Stop:
    title: str
    text: str
    target: Callable[[], QWidget | None]
    """The part of the window to highlight; ``None`` centres the note on the window."""


class Bubble(QFrame):
    """The note: a title, a text, the step counter and the buttons."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("tourBubble")
        self.setFixedWidth(BUBBLE_WIDTH)
        self.title = QLabel()
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.counter = QLabel()
        self.counter.setObjectName("muted")
        self.skip = QPushButton("Skip Tour")
        self.back = QPushButton("Back")
        self.next = QPushButton("Next")
        self.next.setDefault(True)
        for b in (self.skip, self.back, self.next):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the overlay keeps the keyboard
        row = QHBoxLayout()
        row.addWidget(self.counter)
        row.addStretch(1)
        row.addWidget(self.skip)
        row.addWidget(self.back)
        row.addWidget(self.next)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.addWidget(self.title)
        layout.addWidget(self.text)
        layout.addSpacing(4)
        layout.addLayout(row)
        self.apply_theme()

    def apply_theme(self) -> None:
        t = current_theme()
        self.setStyleSheet(
            f"#tourBubble {{ background: {t.panel}; border: 2px solid {t.accent}; border-radius: 8px; }}"
            f"#tourBubble QLabel {{ color: {t.text}; background: transparent; }}"
        )


class EditorTour(QWidget):
    """The overlay. Create it with the main window as parent, then call :meth:`start`."""

    finished = Signal()

    def __init__(self, window: QWidget, stops: list[Stop]) -> None:
        super().__init__(window)
        self.window_ = window
        self.stops = stops
        self.index = 0
        self.hole: QRect | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bubble = Bubble(self)
        self.bubble.skip.clicked.connect(self.close)
        self.bubble.back.clicked.connect(lambda: self.go(self.index - 1))
        self.bubble.next.clicked.connect(self._next)
        window.installEventFilter(self)

    # ------------------------------------------------------------------ navigation

    def start(self) -> None:
        self.setGeometry(self.window_.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.go(0)

    def go(self, index: int) -> None:
        self.index = max(0, min(index, len(self.stops) - 1))
        stop = self.stops[self.index]
        last = self.index == len(self.stops) - 1
        self.bubble.title.setText(stop.title)
        self.bubble.text.setText(stop.text)
        self.bubble.counter.setText(f"{self.index + 1} of {len(self.stops)}")
        self.bubble.back.setEnabled(self.index > 0)
        self.bubble.next.setText("Finish" if last else "Next")
        self.bubble.skip.setVisible(not last)
        self.bubble.adjustSize()
        self._place()

    def _next(self) -> None:
        if self.index >= len(self.stops) - 1:
            self.close()
        else:
            self.go(self.index + 1)

    # ------------------------------------------------------------------ geometry

    def _target_rect(self) -> QRect | None:
        widget = self.stops[self.index].target()
        if widget is None or not widget.isVisible():
            return None
        top_left = widget.mapTo(self.window_, QPoint(0, 0))
        return QRect(top_left, widget.size()).adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)

    def _place(self) -> None:
        self.setGeometry(self.window_.rect())
        self.hole = self._target_rect()
        # The size hint ignores the fixed width: take the height the text needs at that width.
        height = self.bubble.heightForWidth(BUBBLE_WIDTH)
        b = QSize(BUBBLE_WIDTH, height if height > 0 else self.bubble.sizeHint().height())
        area = self.rect()
        if self.hole is None:
            pos = QPoint((area.width() - b.width()) // 2, (area.height() - b.height()) // 2)
        else:
            h = self.hole
            # Beside the highlighted part where there is room (right, left, below, above), else inside it.
            candidates = [
                QPoint(h.right() + 12, h.top()),
                QPoint(h.left() - 12 - b.width(), h.top()),
                QPoint(h.left(), h.bottom() + 12),
                QPoint(h.left(), h.top() - 12 - b.height()),
            ]
            pos = next(
                (p for p in candidates if area.contains(QRect(p, b))),
                QPoint(h.center().x() - b.width() // 2, h.center().y() - b.height() // 2),
            )
            pos.setX(max(8, min(pos.x(), area.width() - b.width() - 8)))
            pos.setY(max(8, min(pos.y(), area.height() - b.height() - 8)))
        self.bubble.setGeometry(QRect(pos, b))
        self.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.window_ and event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self._place()
        return False

    # ------------------------------------------------------------------ drawing and keys

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        if self.hole is not None:
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(self.hole), 8, 8)
            shade = shade.subtracted(hole)
        p.fillPath(shade, QColor(0, 0, 0, 120))
        if self.hole is not None:
            p.setPen(QPen(QColor(current_theme().accent), 2))
            p.drawRoundedRect(QRectF(self.hole), 8, 8)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._next()
        elif key == Qt.Key.Key_Left:
            self.go(self.index - 1)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: object) -> None:  # clicks on the dimmed window do nothing
        pass

    def closeEvent(self, event: object) -> None:
        self.window_.removeEventFilter(self)
        self.finished.emit()


def editor_stops(window: object) -> list[Stop]:
    """The stops of the tour through the main window (a :class:`MainWindow`)."""
    w: object = window

    def dock(name: str) -> Callable[[], QWidget | None]:
        def target() -> QWidget | None:
            d = getattr(w, name)
            d.show()
            d.raise_()
            return d

        return target

    def run_button() -> QWidget | None:
        return w.toolbar.widgetForAction(w.act_run)  # type: ignore[attr-defined]

    def help_button() -> QWidget | None:
        return w.toolbar.widgetForAction(w.act_help)  # type: ignore[attr-defined]

    return [
        Stop(
            "Welcome to the editor",
            "This tour shows the six parts of the editor you use all the time. Use <b>→</b> and <b>←</b> to "
            "move, <b>Esc</b> to leave.",
            lambda: None,
        ),
        Stop(
            "1. The block library",
            "Every block, by category. Type in the search box, then <b>drag</b> a block onto the canvas or "
            "double-click it. Press <b>F1</b> on a block for its help page, with the equations.",
            dock("library_dock"),
        ),
        Stop(
            "2. The canvas",
            "Your model. Drag from an <b>output port</b> (right side of a block) to an <b>input port</b> to "
            "connect them. Double-click a subsystem to open it, <b>Ctrl+↑</b> to go back up. "
            "<b>Ctrl+Shift+F</b> flips a block for feedback paths.",
            lambda: w.view,  # type: ignore[attr-defined]
        ),
        Stop(
            "3. Properties",
            "The parameters of the selected block. A parameter can be a number, a workspace variable or an "
            "expression such as <code>3.6 / m</code>.",
            dock("properties_dock"),
        ),
        Stop(
            "4. Run",
            "Press <b>▶</b> (Ctrl+R) to simulate. Set the stop time next to it. Scopes open on the first run; "
            "every run is kept in the Data Inspector (Ctrl+I) for comparison.",
            run_button,
        ),
        Stop(
            "5. The panels below",
            "<b>Diagnostics</b> lists problems (click one to select the block). The other tabs hold the "
            "<b>Model Workspace</b> (variables), the <b>Test Manager</b>, the Data Dictionary, Requirements and "
            "a Python <b>Console</b> with the model loaded.",
            dock("diagnostics_dock"),
        ),
        Stop(
            "6. Help",
            f"The user guide and {len(TUTORIALS)} tutorials, each with a finished model. <b>Help ▸ Welcome</b> "
            "brings back the "
            "start window, and <b>Help ▸ Editor Tour</b> shows this tour again.",
            help_button,
        ),
        Stop(
            "Ready",
            "The best next step is the first tutorial: you build a cruise control from scratch in about 30 "
            "minutes. Find it in <b>Help ▸ Welcome</b> or in the help under <i>Tutorials</i>.",
            lambda: None,
        ),
    ]
