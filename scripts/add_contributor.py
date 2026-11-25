"""Add a contributor to CONTRIBUTORS.md, from a merged pull request (run by .github/workflows/credit.yml).

    python scripts/add_contributor.py --login octocat --pr 12 --title "Faster lookup tables" --body-file body.md

The pull request's *Credit* section (``Name:`` and ``Contribution:`` lines, see .github/pull_request_template.md) says
how the author wants to be credited; without it, the GitHub name and the pull request's title are used. A row that is
already there is not added twice.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def credit_from_body(body: str) -> tuple[str, str]:
    """(name, contribution) from the pull request's Credit section; "" where it is not filled in."""
    section = body.split("## Credit", 1)[1] if "## Credit" in body else body
    section = re.sub(r"<!--.*?-->", "", section, flags=re.S)

    def field(label: str) -> str:
        m = re.search(rf"^\s*{label}\s*:[ \t]*(.*)$", section, re.M | re.I)
        return m.group(1).strip() if m else ""

    return field("Name"), field("Contribution")


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "/").split())[:200]


def add_contributor(text: str, name: str, login: str, contribution: str, pr: str) -> str:
    """``text`` (CONTRIBUTORS.md) with a row for this contribution at the end of the table."""
    row = f"| {_cell(name)} | {_cell(login)} | {_cell(contribution)} | {('#' + pr) if pr else ''} |"
    lines = text.rstrip("\n").split("\n")
    if any(line.strip() == row for line in lines):
        return text
    table_rows = [k for k, line in enumerate(lines) if line.startswith("|")]
    at = table_rows[-1] + 1 if table_rows else len(lines)
    lines.insert(at, row)
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--login", required=True)
    ap.add_argument("--pr", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--body-file", default="")
    ap.add_argument("--file", default=str(ROOT / "CONTRIBUTORS.md"))
    args = ap.parse_args()
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else ""
    name, contribution = credit_from_body(body)
    path = Path(args.file)
    path.write_text(add_contributor(path.read_text(encoding="utf-8"), name or args.login, args.login,
                                    contribution or args.title, args.pr), encoding="utf-8")  # fmt: skip
    print(f"credited {name or args.login} for #{args.pr}: {contribution or args.title}")


if __name__ == "__main__":
    main()
