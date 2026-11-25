"""Light and dark themes.

Colours are defined once as tokens and used by the stylesheet, the canvas and the scope plots.
Plot series colours use a categorical palette validated for colour-vision deficiency; they are assigned
in a fixed order and never cycled.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


class ThemeMode(Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class Theme:
    name: str
    window: str
    panel: str
    panel_alt: str
    border: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_text: str
    accent_hover: str
    accent_soft: str  # selected rows and tabs: a tint of the accent, text stays in text colours
    hover: str
    canvas: str
    grid: str
    block_fill: str
    block_border: str
    block_text: str
    port: str
    wire: str
    selection: str
    error: str
    warning: str
    plot_surface: str
    plot_grid: str
    series: tuple[str, ...]

    def color(self, token: str) -> QColor:
        return QColor(getattr(self, token))


LIGHT = Theme(
    name="light",
    window="#f3f3f1",
    panel="#fbfbfa",
    panel_alt="#efefec",
    border="#dcdbd5",
    text="#1d1d1b",
    text_secondary="#52514e",
    text_muted="#86857f",
    accent="#2a78d6",
    accent_text="#ffffff",
    accent_hover="#1f66bd",
    accent_soft="#dce8f8",
    hover="#ebebe7",
    canvas="#fcfcfb",
    grid="#e3e2dc",
    block_fill="#ffffff",
    block_border="#9c9b94",
    block_text="#1d1d1b",
    port="#6b6a64",
    wire="#4b4a46",
    selection="#2a78d6",
    error="#d03b3b",
    warning="#b97800",
    plot_surface="#fcfcfb",
    plot_grid="#e1e0d9",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"),
)

DARK = Theme(
    name="dark",
    window="#161615",
    panel="#1f1f1e",
    panel_alt="#262625",
    border="#353532",
    text="#f2f2ef",
    text_secondary="#c3c2b7",
    text_muted="#8d8c85",
    accent="#3987e5",
    accent_text="#ffffff",
    accent_hover="#5598eb",
    accent_soft="#1e3350",
    hover="#2c2c2a",
    canvas="#1a1a19",
    grid="#2a2a28",
    block_fill="#262625",
    block_border="#6c6b65",
    block_text="#f2f2ef",
    port="#a3a29b",
    wire="#bdbcb4",
    selection="#3987e5",
    error="#e66767",
    warning="#d9a020",
    plot_surface="#1a1a19",
    plot_grid="#2c2c2a",
    series=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"),
)


def _assets(t: Theme) -> dict[str, str]:
    """Small SVG images the stylesheet needs (arrows, the check mark), written once per theme to a cache folder."""
    folder = Path(tempfile.gettempdir()) / f"nervus-theme-{t.name}"
    folder.mkdir(exist_ok=True)
    chevron = "<polyline points='{}' fill='none' stroke='{}' stroke-width='1.6' stroke-linecap='round' " \
              "stroke-linejoin='round'/>"  # fmt: skip
    images = {
        "down": chevron.format("3,5.5 8,10.5 13,5.5", t.text_secondary),
        "up": chevron.format("3,10.5 8,5.5 13,10.5", t.text_secondary),
        "check": chevron.format("3.5,8.5 6.5,11.5 12.5,4.5", t.accent_text),
        "dash": f"<line x1='4' y1='8' x2='12' y2='8' stroke='{t.accent_text}' stroke-width='1.8' "
        "stroke-linecap='round'/>",
    }
    paths = {}
    for name, body in images.items():
        path = folder / f"{name}.svg"
        svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'>{body}</svg>"
        if not path.exists() or path.read_text() != svg:
            path.write_text(svg)
        paths[name] = path.as_posix()
    return paths


def _stylesheet(t: Theme) -> str:
    """The application's look: flat surfaces, 6 px corners, an 8 px spacing rhythm, one accent colour, tinted
    selections, slim scrollbars and visible keyboard focus."""
    a = _assets(t)
    return f"""
    QMainWindow, QDialog {{ background: {t.window}; }}
    QWidget {{ color: {t.text}; font-size: 13px; }}
    QToolTip {{
        background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px; padding: 4px 8px;
    }}

    /* Panels */
    QMainWindow::separator {{ background: {t.window}; width: 6px; height: 6px; }}
    QMainWindow::separator:hover {{ background: {t.accent_soft}; }}
    QDockWidget {{ titlebar-close-icon: none; }}
    QDockWidget::title {{
        background: {t.window}; padding: 8px 8px 6px 8px; color: {t.text_secondary}; font-weight: 600;
    }}
    #panel, QTreeWidget, QTreeView, QListWidget, QListView, QTableWidget, QTableView, QTextBrowser,
    QPlainTextEdit, QTextEdit, QScrollArea {{
        background: {t.panel}; border: 1px solid {t.border}; border-radius: 8px;
    }}
    QScrollArea > QWidget > QWidget {{ background: {t.panel}; }}
    QSplitter::handle {{ background: {t.window}; }}
    QSplitter::handle:hover {{ background: {t.accent_soft}; }}
    QGroupBox {{
        border: 1px solid {t.border}; border-radius: 8px; margin-top: 18px; padding: 12px 8px 8px 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.text_secondary}; font-weight: 600;
    }}

    /* Inputs */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {t.panel}; border: 1px solid {t.border}; border-radius: 6px; padding: 4px 8px;
        min-height: 20px; selection-background-color: {t.accent}; selection-color: {t.accent_text};
    }}
    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {t.text_muted}; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus,
    QTextEdit:focus {{ border: 1px solid {t.accent}; }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        background: {t.panel_alt}; color: {t.text_muted};
    }}
    QLineEdit[invalid="true"] {{ border: 1px solid {t.error}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{ image: url("{a["down"]}"); width: 14px; height: 14px; }}
    QComboBox QAbstractItemView {{
        background: {t.panel}; border: 1px solid {t.border}; border-radius: 6px; padding: 4px; outline: none;
        selection-background-color: {t.accent_soft}; selection-color: {t.text};
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
        border: none; width: 18px;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{a["up"]}"); width: 12px; height: 12px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{a["down"]}"); width: 12px; height: 12px; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator, QTreeView::indicator, QListView::indicator,
    QTableView::indicator {{
        width: 16px; height: 16px; border: 1px solid {t.block_border}; background: {t.panel};
    }}
    QCheckBox::indicator, QTreeView::indicator, QListView::indicator, QTableView::indicator {{
        border-radius: 4px;
    }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
    QCheckBox::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked,
    QTableView::indicator:checked {{
        background: {t.accent}; border-color: {t.accent}; image: url("{a["check"]}");
    }}
    QCheckBox::indicator:indeterminate {{ background: {t.accent}; border-color: {t.accent}; image: url("{a["dash"]}"); }}
    QRadioButton::indicator:checked {{
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {t.accent_text},
                                    stop:0.35 {t.accent_text}, stop:0.45 {t.accent}, stop:1 {t.accent});
        border-color: {t.accent};
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {t.panel_alt}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {t.panel}; border: 1px solid {t.block_border}; width: 14px; height: 14px; margin: -6px 0;
        border-radius: 8px;
    }}

    /* Buttons */
    QPushButton {{
        background: {t.panel}; border: 1px solid {t.border}; border-radius: 6px; padding: 5px 14px;
        min-height: 18px;
    }}
    QPushButton[compact="true"] {{ padding: 0; }}  /* small fixed-size buttons such as "?" and "…" */
    QPushButton:hover {{ background: {t.hover}; }}
    QPushButton:pressed {{ background: {t.panel_alt}; }}
    QPushButton:focus {{ border-color: {t.accent}; }}
    QPushButton:disabled {{ color: {t.text_muted}; background: {t.panel_alt}; }}
    QPushButton:default, QPushButton#primary {{
        background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent}; font-weight: 600;
    }}
    QPushButton:default:hover, QPushButton#primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
    QPushButton:default:disabled, QPushButton#primary:disabled {{
        background: {t.accent_soft}; border-color: {t.accent_soft}; color: {t.text_muted};
    }}
    QToolBar {{ background: {t.window}; border: none; spacing: 4px; padding: 4px 8px; }}
    QToolBar::separator {{ background: {t.border}; width: 1px; margin: 6px 6px; }}
    QToolButton {{ border: none; border-radius: 6px; padding: 5px; }}
    QToolButton:hover {{ background: {t.hover}; }}
    QToolButton:pressed, QToolButton:checked {{ background: {t.accent_soft}; }}

    /* Tabs: the panels' tabs and the dialogs' */
    QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 8px; background: {t.panel}; top: -1px; }}
    QTabBar {{ qproperty-drawBase: 0; background: transparent; }}
    QTabBar::tab {{
        background: transparent; color: {t.text_secondary}; border: none; border-bottom: 2px solid transparent;
        padding: 6px 12px; margin: 0 2px;
    }}
    QTabBar::tab:hover {{ color: {t.text}; background: {t.hover}; border-radius: 6px; }}
    QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; font-weight: 600; }}
    QTabBar::tab:selected:hover {{ border-bottom-left-radius: 0; border-bottom-right-radius: 0; }}
    QTabBar::close-button {{ subcontrol-position: right; }}

    /* Lists, trees, tables */
    QTreeView::item, QListView::item {{ padding: 3px 4px; border-radius: 4px; }}
    QTreeView::item:hover, QListView::item:hover {{ background: {t.hover}; }}
    QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
        background: {t.accent_soft}; color: {t.text};
    }}
    QTableView {{ gridline-color: {t.border}; selection-background-color: {t.accent_soft};
                  selection-color: {t.text}; alternate-background-color: {t.panel_alt}; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{
        background: {t.panel}; color: {t.text_secondary}; border: none; border-bottom: 1px solid {t.border};
        padding: 6px 8px; font-weight: 600;
    }}
    QTableCornerButton::section {{ background: {t.panel}; border: none; }}

    /* Scrollbars: slim, no arrow buttons */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {t.border}; border-radius: 3px; }}
    QScrollBar::handle:hover {{ background: {t.text_muted}; }}
    QScrollBar::handle:vertical {{ min-height: 28px; }}
    QScrollBar::handle:horizontal {{ min-width: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    /* Menus and the status bar */
    QMenuBar {{ background: {t.window}; padding: 2px 4px; }}
    QMenuBar::item {{ padding: 4px 8px; border-radius: 6px; background: transparent; }}
    QMenuBar::item:selected {{ background: {t.hover}; }}
    QMenu {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 5px 24px 5px 20px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QMenu::item:disabled {{ color: {t.text_muted}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
    QStatusBar {{ background: {t.window}; color: {t.text_secondary}; }}
    QStatusBar::item {{ border: none; }}
    QProgressBar {{
        border: none; border-radius: 3px; background: {t.panel_alt}; max-height: 6px; text-align: center;
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}

    QLabel#heading {{ font-size: 15px; font-weight: 600; }}
    QLabel#muted {{ color: {t.text_muted}; }}
    QLabel#error {{ color: {t.error}; }}
    """


def _palette(t: Theme) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.window,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.panel,
        QPalette.ColorRole.AlternateBase: t.panel_alt,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.panel,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.accent_text,
        QPalette.ColorRole.ToolTipBase: t.panel,
        QPalette.ColorRole.ToolTipText: t.text,
        QPalette.ColorRole.PlaceholderText: t.text_muted,
        QPalette.ColorRole.Link: t.accent,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t.text_muted))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(t.text_muted))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(t.text_muted))
    return p


class ThemeManager(QObject):
    """Holds the active theme and re-applies it when the mode or the system colour scheme changes."""

    themeChanged = Signal(object)  # Theme

    _instance: ThemeManager | None = None

    def __init__(self) -> None:
        super().__init__()
        self._mode = ThemeMode.SYSTEM
        self._theme = LIGHT
        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda _scheme: self.apply())

    @classmethod
    def instance(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    def set_mode(self, mode: ThemeMode) -> None:
        self._mode = mode
        self.apply()

    def _resolve(self) -> Theme:
        if self._mode is ThemeMode.LIGHT:
            return LIGHT
        if self._mode is ThemeMode.DARK:
            return DARK
        scheme = QGuiApplication.styleHints().colorScheme()
        return DARK if scheme == Qt.ColorScheme.Dark else LIGHT

    def apply(self) -> None:
        self._theme = self._resolve()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(_palette(self._theme))
            app.setStyleSheet(_stylesheet(self._theme))
        self.themeChanged.emit(self._theme)


def current_theme() -> Theme:
    return ThemeManager.instance().theme


def monospace_font(point_size: float = 11.0) -> QFont:
    """A fixed-width font for code (Menlo on macOS, Consolas on Windows, DejaVu Sans Mono on Linux)."""
    available = set(QFontDatabase.families())
    for family in ("Menlo", "SF Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono", "Courier New"):
        if family in available:
            font = QFont(family)
            break
    else:
        font = QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPointSizeF(point_size)
    return font
