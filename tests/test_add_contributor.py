"""Crediting the author of a merged pull request (scripts/add_contributor.py)."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "add_contributor.py"
spec = importlib.util.spec_from_file_location("add_contributor", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TABLE = """# Contributors

| Name | GitHub | Contribution | Pull request |
|---|---|---|---|
| Johan Björk | ikjbi | Creator | |
"""


def test_credit_section_of_the_template() -> None:
    body = (SCRIPT.parent.parent / ".github" / "pull_request_template.md").read_text()
    assert mod.credit_from_body(body) == ("", "")  # the empty template: nothing filled in
    filled = body.replace("Name:\n", "Name: Ada Lovelace\n").replace("Contribution:\n", "Contribution: Faster maps\n")
    assert mod.credit_from_body(filled) == ("Ada Lovelace", "Faster maps")


def test_rows_are_added_once() -> None:
    from nervus.about import parse_contributors

    once = mod.add_contributor(TABLE, "Ada Lovelace", "ada", "Faster | lookup maps", "12")
    twice = mod.add_contributor(once, "Ada Lovelace", "ada", "Faster | lookup maps", "12")
    assert once == twice
    rows = parse_contributors(once)
    assert [r.name for r in rows] == ["Johan Björk", "Ada Lovelace"]
    assert rows[1].contribution == "Faster / lookup maps" and rows[1].pull_request == "12"


def test_the_real_file_parses() -> None:
    from nervus.about import contributors

    assert contributors()[0].github == "ikjbi"
