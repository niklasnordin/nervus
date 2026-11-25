"""Help ▸ About Nervus: the version, the links, the contributors and the licence."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from nervus import __version__
from nervus.about import LICENSE, LINKS, contributors, environment


def _license_text() -> str:
    """The LICENSE file: the repository's, or the copy in the installed package."""
    here = Path(__file__).resolve()
    for path in (here.parents[4] / "LICENSE", here.parents[2] / "LICENSE"):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return "MIT License: https://github.com/ikjbi/nervus/blob/main/LICENSE"


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Nervus")
        self.resize(640, 520)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._about_tab(), "About")
        self.tabs.addTab(self._contributors_tab(), "Contributors")
        self.tabs.addTab(self._license_tab(), "Licence")
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(row)

    def _about_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        title = QLabel(f"<h2>Nervus {html.escape(__version__)}</h2>")
        text = QLabel(
            "<p>Open-source block-diagram modelling and simulation in Python: control systems, physical models, "
            "state machines and ECU code generation, for learning and for engineering.</p>"
            f"<p>{LICENSE}. " + " · ".join(f"<a href='{url}'>{html.escape(name)}</a>" for name, url in LINKS.items())
            + "</p>"
        )  # fmt: skip
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        env = environment()
        self.env_text = "\n".join(f"{k}: {v}" for k, v in env.items())
        table = QLabel("<table>" + "".join(f"<tr><td><b>{html.escape(k)}</b>&nbsp;&nbsp;</td><td>{html.escape(v)}</td></tr>"
                                           for k, v in env.items()) + "</table>")  # fmt: skip
        table.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy = QPushButton("Copy for a bug report")
        copy.setToolTip("Copy the versions above, to paste into an issue on GitHub")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.env_text))
        row = QHBoxLayout()
        row.addWidget(copy)
        row.addStretch(1)
        for x in (title, text, table):
            v.addWidget(x)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _contributors_tab(self) -> QWidget:
        people = contributors()
        rows = []
        for c in people:
            name = f"<a href='{c.profile}'>{html.escape(c.name)}</a>" if c.profile else html.escape(c.name)
            pr = f" (<a href='{c.pr_url}'>#{html.escape(c.pull_request)}</a>)" if c.pr_url else ""
            rows.append(
                f"<tr><td valign='top'><b>{name}</b>&nbsp;&nbsp;</td><td>{html.escape(c.contribution)}{pr}</td></tr>"
            )
        self.contributors_view = QTextBrowser()
        self.contributors_view.setOpenExternalLinks(True)
        self.contributors_view.setHtml(
            f"<p>Nervus is made by {len(people)} contributor{'s' * (len(people) != 1)}. Thank you!</p>"
            f"<table cellspacing='6'>{''.join(rows)}</table>"
            "<p>Want to be on this list? Contributions are welcome: see "
            "<a href='https://github.com/ikjbi/nervus/blob/main/CONTRIBUTING.md'>CONTRIBUTING.md</a>. When your pull "
            "request is merged, you are added here with what you contributed.</p>"
        )  # fmt: skip
        return self.contributors_view

    def _license_tab(self) -> QWidget:
        view = QTextBrowser()
        view.setPlainText(_license_text())
        return view
