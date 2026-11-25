"""What the About window shows: the version, the environment, the links and the contributors.

The contributors are read from ``CONTRIBUTORS.md`` (in a source checkout the repository's, in an installed Nervus the
copy in the package): a Markdown table with the columns Name, GitHub, Contribution and Pull request.
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from pathlib import Path

from nervus import __version__

LINKS = {
    "Documentation": "https://ikjbi.github.io/nervus/",
    "Source code": "https://github.com/ikjbi/nervus",
    "Report a problem": "https://github.com/ikjbi/nervus/issues",
    "PyPI": "https://pypi.org/project/nervus/",
}
LICENSE = "MIT License"


@dataclass(frozen=True)
class Contributor:
    name: str
    github: str
    contribution: str
    pull_request: str = ""
    """The pull request's number ("" for contributions without one)."""

    @property
    def profile(self) -> str:
        return f"https://github.com/{self.github}" if self.github else ""

    @property
    def pr_url(self) -> str:
        return f"https://github.com/ikjbi/nervus/pull/{self.pull_request}" if self.pull_request else ""


def contributors_file() -> Path:
    here = Path(__file__).resolve().parent
    checkout = here.parent.parent / "CONTRIBUTORS.md"
    return checkout if checkout.exists() else here / "CONTRIBUTORS.md"


def parse_contributors(text: str) -> list[Contributor]:
    """The rows of the contributors table (header and separator rows skipped)."""
    out = []
    for line in text.splitlines():
        if not line.startswith("|") or re.match(r"^\|\s*-", line) or line.lower().startswith("| name"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        cells += [""] * (4 - len(cells))
        name, github, contribution, pr = cells[:4]
        if name:
            out.append(Contributor(name, github.lstrip("@"), contribution, pr.lstrip("#")))
    return out


def contributors() -> list[Contributor]:
    path = contributors_file()
    return parse_contributors(path.read_text(encoding="utf-8")) if path.exists() else []


def environment() -> dict[str, str]:
    """Versions, for the About window and for bug reports."""
    info = {
        "Nervus": __version__,
        "Python": platform.python_version(),
        "System": f"{platform.system()} {platform.release()}",
    }
    try:
        import numpy
        import scipy

        info["NumPy / SciPy"] = f"{numpy.__version__} / {scipy.__version__}"
    except ImportError:  # pragma: no cover
        pass
    try:  # the installed version, without importing Qt (only nervus.gui may)
        from importlib.metadata import PackageNotFoundError, version

        info["Qt (PySide6)"] = version("PySide6")
    except PackageNotFoundError:
        pass
    return info
