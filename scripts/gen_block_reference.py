"""Regenerate generated documentation. Run: uv run python scripts/gen_block_reference.py

- docs/blocks/: the block reference, from the block docstrings and parameter declarations
- docs/app-help/: copies of the in-app help pages (single source: src/nervus/gui/resources/help)
"""

import re
import sys
from pathlib import Path

from nervus.help import reference_pages

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
OUT = ROOT / "docs" / "blocks"
HELP_SRC = ROOT / "src" / "nervus" / "gui" / "resources" / "help"
HELP_OUT = ROOT / "docs" / "app-help"


def _site_image(m: re.Match[str]) -> str:
    name = m.group(1)
    for folder in ("images/blocks", "images"):
        if (ROOT / "docs" / folder / name).exists():
            return f"](../{folder}/{name})"
    return m.group(0)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.md"):
        old.unlink()
    for name, text in reference_pages().items():
        (OUT / name).write_text(text.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {len(list(OUT.glob('*.md')))} pages to {OUT}")
    import gen_tutorials

    gen_tutorials.main()  # tutorial boxes, overview and navigation, from nervus.tutorials
    from nervus.help import SHARED_PAGES, docs_page_for_app

    for docs_name, app_name in SHARED_PAGES.items():  # written for GitHub, converted for the app
        text = (ROOT / "docs" / docs_name).read_text(encoding="utf-8")
        (HELP_SRC / app_name).write_text(docs_page_for_app(text), encoding="utf-8")
    HELP_OUT.mkdir(parents=True, exist_ok=True)
    for page in HELP_SRC.glob("*.md"):  # the app's figures/ are the site's images/blocks/ (figures) and images/
        text = page.read_text(encoding="utf-8")
        (HELP_OUT / page.name).write_text(re.sub(r"\]\(figures/([^)]+)\)", _site_image, text), encoding="utf-8")
    print(f"copied {len(list(HELP_SRC.glob('*.md')))} in-app help pages to {HELP_OUT}")


if __name__ == "__main__":
    main()
