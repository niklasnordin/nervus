"""Keep the GitHub docs and the in-app help complete and consistent."""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
HELP = ROOT / "src" / "nervus" / "gui" / "resources" / "help"
NAV = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")


def help_pages() -> set[str]:
    import pytest

    pytest.importorskip("PySide6")
    from nervus.gui.views.help_browser import HELP_PAGES

    return {name for name, _title in HELP_PAGES}


def test_every_help_file_is_in_the_help_window() -> None:
    files = {p.stem for p in HELP.glob("*.md")}
    assert files == help_pages(), f"missing in HELP_PAGES: {files - help_pages()}, no file: {help_pages() - files}"


def test_every_page_is_in_the_github_navigation() -> None:
    pages = [p.relative_to(ROOT / "docs").as_posix() for p in (ROOT / "docs").glob("*.md")]
    pages += [p.relative_to(ROOT / "docs").as_posix() for p in (ROOT / "docs" / "tutorials").glob("*.md")]
    pages += [f"app-help/{p.name}" for p in HELP.glob("*.md")]
    missing = [p for p in pages if p not in NAV]
    assert not missing, f"not in mkdocs.yml nav: {missing}"


def test_help_pages_have_no_broken_links() -> None:
    for page in HELP.glob("*.md"):
        text = page.read_text(encoding="utf-8")
        assert "](images/" not in text and "](../images/" not in text, f"{page.name}: screenshots are not bundled"
        for link in re.findall(r"\]\((?!https?:|figures/)([^)]+)\)", text):  # other help pages, opened in the app
            name = link.partition("#")[0]
            assert name == "block_reference.md" or (HELP / name).exists(), f"{page.name}: no help page {link}"
        for fig in re.findall(r"\]\(figures/([^)]+)\)", text):
            assert (ROOT / "src" / "nervus" / "gui" / "resources" / "figures" / fig).exists(), f"{page.name}: {fig}"


def test_every_example_is_documented() -> None:
    getting_started = (HELP / "getting_started.md").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    for model in (ROOT / "examples").glob("*.nervus"):
        assert model.name in getting_started, f"{model.name} missing in the in-app Getting started page"
        script = model.with_suffix(".py").name
        assert script in docs or model.stem in docs, f"{model.name} missing in docs/getting-started.md"


def test_latex_to_text_replaces_whole_commands_only() -> None:
    from nervus.help import latex_to_text

    assert latex_to_text(r"i = I_s\left(e^{v} - 1\right)") == "i = I_s(e^{v} - 1)"
    assert latex_to_text(r"a \le b \leq c \ge d \geq e") == "a ≤ b ≤ c ≥ d ≥ e"
    assert latex_to_text(r"\dot{x} \dots") == "x\u0307 …"
