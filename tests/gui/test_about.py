"""Help > About Nervus."""

from nervus import __version__
from nervus.about import parse_contributors


def test_about_window(window) -> None:  # type: ignore[no-untyped-def]
    dlg = window.about()
    assert dlg.tabs.count() == 3 and dlg.tabs.tabText(1) == "Contributors"
    assert __version__ in dlg.env_text and "Python" in dlg.env_text
    assert "Johan Björk" in dlg.contributors_view.toPlainText()
    assert "MIT License" in dlg.tabs.widget(2).toPlainText()
    dlg.close()


def test_contributors_table() -> None:
    text = """| Name | GitHub | Contribution | Pull request |
|---|---|---|---|
| Ada Lovelace | @ada | Faster lookup tables | #12 |
| Grace Hopper | | Tutorial on CAN | |
"""
    rows = parse_contributors(text)
    assert [r.name for r in rows] == ["Ada Lovelace", "Grace Hopper"]
    assert rows[0].github == "ada" and rows[0].pr_url.endswith("/pull/12") and rows[1].profile == ""
