"""In-app help: user guide pages and the block reference."""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QTextCursor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QWidget

from nervus.blocks import categories
from nervus.gui.theme import ThemeManager, current_theme
from nervus.gui.views.math_render import markdown_with_math
from nervus.help import admonitions_to_quotes, block_help_markdown

FIGURE_WIDTH = 680  # px; figures are rendered at 2x for sharp display


class HelpText(QTextBrowser):
    """A text browser that serves typeset equations (``math:<id>``) and block figures (``figures/<name>``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.images: dict[str, QImage] = {}

    def loadResource(self, type_: int, url: QUrl | str) -> Any:
        key = url.toString() if isinstance(url, QUrl) else url
        if key in self.images:
            return self.images[key]
        if "figures/" in key:
            name = key.rsplit("figures/", 1)[1]
            path = resources.files("nervus.gui.resources.figures").joinpath(name)
            if path.is_file():
                img = QImage.fromData(path.read_bytes())
                if img.width() > 2 * FIGURE_WIDTH:
                    img = img.scaledToWidth(2 * FIGURE_WIDTH, Qt.TransformationMode.SmoothTransformation)
                img.setDevicePixelRatio(2.0)
                return img
        return super().loadResource(type_, url)

    def show_markdown(self, markdown: str) -> None:
        md = markdown_with_math(admonitions_to_quotes(markdown), self.images, current_theme().text)
        self.setMarkdown(md)


_TUTORIALS_PAGE = ("tutorials", "Tutorials")

HELP_PAGES = [
    ("getting_started", "Getting started"),
    ("concepts", "Concepts"),
    ("editor", "Using the editor"),
    ("simulation", "Running simulations"),
    ("workspace", "Workspace and calibrations"),
    ("subsystems", "Subsystems"),
    ("libraries", "Component libraries"),
    ("datatypes", "Data types and fixed point"),
    ("statecharts", "State machines"),
    ("physical", "Physical modelling"),
    ("can", "CAN communication"),
    ("obd", "OBD-II live data"),
    ("calibration", "Measurement and calibration"),
    ("analysis", "Linear analysis"),
    ("codegen", "C code generation"),
    ("boards", "Development boards"),
    ("testing", "Test cases and the Data Inspector"),
    ("verification", "Coverage, requirements, Model Advisor"),
    _TUTORIALS_PAGE,
    ("coming_from_simulink", "Coming from Simulink"),
    ("model_files", "Model files"),
    ("shortcuts", "Keyboard shortcuts"),
]
"""The guide pages, in the order of the list; the tutorials (from :mod:`nervus.tutorials`) are under *Tutorials*."""


def _tutorial_pages() -> list[tuple[str, str]]:
    from nervus.tutorials import ordered

    return [(t.help_page, t.title) for t in ordered()]


HELP_PAGES += _tutorial_pages()

BLOCK_REFERENCE = "block_reference"
"""The page key of the block reference overview (help pages link to it as ``block_reference.md``)."""


def help_page(name: str) -> str:
    return resources.files("nervus.gui.resources.help").joinpath(f"{name}.md").read_text(encoding="utf-8")


def _slug(text: str) -> str:
    """A heading's anchor, as the docs site makes it: ``## Solver settings`` → ``solver-settings``."""
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s]+", "-", text)


class HelpBrowser(QDialog):
    """The Help window: the guide, the tutorials and the block reference in a tree on the left (the tutorials and
    the block categories fold away), the page on the right. Links between pages open in the window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Nervus Help")
        self.resize(1000, 680)
        self.index = QTreeWidget()
        self.index.setHeaderHidden(True)
        self.index.setMinimumWidth(220)
        self.index.setMaximumWidth(340)
        self.index.setIndentation(14)
        self.index.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.text = HelpText()
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self.open_link)
        splitter = QSplitter()
        splitter.addWidget(self.index)
        splitter.addWidget(self.text)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 720])
        layout = QHBoxLayout(self)
        layout.addWidget(splitter)

        self._pages: dict[str, str] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        from nervus.tutorials import CATEGORIES, TUTORIALS

        tutorials: QTreeWidgetItem | None = None
        for key, title in HELP_PAGES:
            if key.startswith("tutorial_"):
                continue
            item = self._add(None, f"page:{key}", title, help_page(key))
            if key == _TUTORIALS_PAGE[0]:
                tutorials = item
        assert tutorials is not None
        for cat, _intro, _subs in CATEGORIES:
            group = self._group(tutorials, cat)
            for tut in TUTORIALS:
                if tut.category == cat:
                    self._add(group, f"page:{tut.help_page}", tut.title, help_page(tut.help_page))
        blocks = self._add(None, f"page:{BLOCK_REFERENCE}", "Block reference", self._block_overview())
        for cat, classes in sorted(categories().items()):
            group = self._group(blocks, cat)
            for cls in sorted(classes, key=lambda c: c.label):
                self._add(group, f"block:{cls.type_id}", cls.label, block_help_markdown(cls, heading_level=1))
        self._current = ""
        self.index.currentItemChanged.connect(self._show)
        ThemeManager.instance().themeChanged.connect(self._on_theme)
        self.show_page("page:getting_started")

    @staticmethod
    def _block_overview() -> str:
        lines = ["# Block reference", "", "Every block, its equation and its parameters. Select a block on the canvas "
                 "and press **F1** to open its page.", ""]  # fmt: skip
        for cat, classes in sorted(categories().items()):
            names = ", ".join(f"[{c.label}](block:{c.type_id})" for c in sorted(classes, key=lambda c: c.label))
            lines += [f"## {cat}", "", names, ""]
        return "\n".join(lines)

    def _on_theme(self, _theme: object) -> None:
        if self._current:  # equations are rendered in the theme's text colour
            self.text.show_markdown(self._pages[self._current])

    def _group(self, parent: QTreeWidgetItem, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [title])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setToolTip(0, title)
        f = item.font(0)
        f.setBold(True)
        item.setFont(0, f)
        return item

    def _add(self, parent: QTreeWidgetItem | None, key: str, title: str, markdown: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setToolTip(0, title)
        if parent is None:
            self.index.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._pages[key] = markdown
        self._items[key] = item
        return item

    def _show(self, item: QTreeWidgetItem | None, _prev: QTreeWidgetItem | None = None) -> None:
        if item is None:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key in self._pages:
            self._current = key
            self.text.show_markdown(self._pages[key])
        elif item.childCount():  # a group heading: open or close it
            item.setExpanded(not item.isExpanded())

    def show_page(self, key: str, anchor: str = "") -> None:
        item = self._items.get(key)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:  # a folded section opens to show where the page is
            parent.setExpanded(True)
            parent = parent.parent()
        self.index.setCurrentItem(item)
        self.index.scrollToItem(item)
        if anchor:
            self.scroll_to_heading(anchor)

    def show_block(self, type_id: str) -> None:
        self.show_page(f"block:{type_id}")

    def scroll_to_heading(self, anchor: str) -> None:
        """Scroll the page to the heading whose anchor (as on the docs site) is ``anchor``."""
        block = self.text.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() and _slug(block.text()) == anchor:
                cursor = QTextCursor(block)
                self.text.setTextCursor(cursor)
                bar = self.text.verticalScrollBar()
                bar.setValue(bar.maximum())  # then back up, so the heading ends up at the top
                self.text.ensureCursorVisible()
                return
            block = block.next()

    def open_link(self, url: QUrl) -> None:
        """A link in a page: another help page (``editor.md#anchor``), a block (``block:<type>``), a heading on
        this page (``#anchor``), or a web page (opened in the browser)."""
        link = url.toString()
        if link.startswith("block:"):
            self.show_block(link.removeprefix("block:"))
            return
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        page, _, anchor = link.partition("#")
        if not page:
            self.scroll_to_heading(anchor)
            return
        key = f"page:{page.removesuffix('.md')}"
        if key in self._items:
            self.show_page(key, anchor)
