"""Help text for blocks, generated from their docstrings and parameter declarations.

The same Markdown is used for the generated block reference (``docs/blocks``) and for the in-app help.
"""

from __future__ import annotations

import inspect
import math
import re
from pathlib import Path

from nervus.blocks.registry import categories
from nervus.core.block import Block
from nervus.core.params import Param, ParamKind


def _fmt_default(p: Param) -> str:
    v = p.default
    if p.kind is ParamKind.CODE:
        return "*(code)*"
    if isinstance(v, float) and math.isinf(v):
        return "inf" if v > 0 else "-inf"
    if p.kind is ParamKind.SAMPLE_TIME and v == -1:
        return "-1 (inherited)"
    return f"`{v!r}`" if isinstance(v, str) else f"`{v}`"


def block_doc(cls: type[Block]) -> str:
    """The block's docstring, dedented, with Sphinx-style roles turned into plain code."""
    doc = inspect.cleandoc(cls.__doc__ or "")
    return re.sub(r":\w+:`([^`]+)`", r"`\1`", doc)


_LATEX_SYMBOLS = {
    r"\tau": "τ",
    r"\int": "∫",
    r"\cdot": "·",
    r"\le": "≤",
    r"\leq": "≤",
    r"\ge": "≥",
    r"\geq": "≥",
    r"\left": "",
    r"\right": "",
    r"\infty": "∞",
    r"\qquad": "\n",
    r"\quad": "  ",
    r"\,": " ",
    r"\dots": "…",
}


def latex_to_text(tex: str) -> str:
    """Turn the small LaTeX subset used in block docstrings into readable plain text (for the in-app help,
    whose Markdown renderer has no math support)."""
    s = tex
    s = re.sub(r"\\begin\{cases\}|\\end\{cases\}", "", s)
    s = re.sub(r"\\text\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\dot(?![a-zA-Z])\s*\{?(\w)\}?", lambda m: m.group(1) + "\u0307", s)
    for _ in range(3):  # nested fractions
        s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1) / (\2)", s)
    for k, v in _LATEX_SYMBOLS.items():  # whole commands only: \le is not the start of \left or \leq
        whole = r"(?![a-zA-Z])" if k[-1].isalpha() else ""
        s = re.sub(re.escape(k) + whole, v.replace("\\", r"\\"), s)
    s = s.replace("\\\\", "\n").replace("&", " ")
    s = re.sub(r",?\s*\n\s*", "\n", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def admonitions_to_quotes(markdown: str) -> str:
    """MkDocs ``!!! note`` admonitions become block quotes, which Qt's Markdown renderer understands."""
    return re.sub(
        r"!!! (\w+)(?: \"[^\"]*\")?\n((?:    .*\n?)+)",
        lambda m: f"> **{m.group(1).title()}:** " + " ".join(x.strip() for x in m.group(2).splitlines()) + "\n",
        markdown,
    )


def app_markdown(markdown: str) -> str:
    """Prepare Markdown for a renderer without math support: math becomes plain-text code blocks."""

    def block(m: re.Match[str]) -> str:
        lines = [ln.strip() for ln in latex_to_text(m.group(1)).splitlines() if ln.strip()]
        return "\n\n```\n" + "\n".join(lines) + "\n```\n\n"

    out = re.sub(r"\$\$(.+?)\$\$", block, markdown, flags=re.S)
    out = re.sub(r"\$([^$\n]+)\$", lambda m: "`" + latex_to_text(m.group(1)) + "`", out)
    return admonitions_to_quotes(out)


def block_help_markdown(cls: type[Block], heading_level: int = 2) -> str:
    h = "#" * heading_level
    lines = [f"{h} {cls.label}", "", f"`{cls.type_id}` · {cls.category}", "", block_doc(cls), ""]
    if cls.PARAMS:
        lines += [f"{h}# Parameters", "", "| Name | Default | Unit | Description |", "|---|---|---|---|"]
        for p in cls.PARAMS:
            desc = p.description
            if p.choices:
                desc += " — one of " + ", ".join(f"`{c}`" for c in p.choices)
            lines.append(f"| `{p.name}` | {_fmt_default(p)} | {p.unit} | {desc} |")
        lines.append("")
    ports = []
    if cls.NUM_INPUTS == 0:
        ports.append("no inputs")
    if cls.NUM_OUTPUTS == 0:
        ports.append("no outputs")
    if cls.feedthrough_ports is not Block.feedthrough_ports:
        feed = "only some inputs (see above)"
    elif cls.direct_feedthrough is not Block.direct_feedthrough:
        feed = "depends on parameters"
    else:
        feed = "yes" if cls.DIRECT_FEEDTHROUGH else "no"
    lines += [f"*Direct feedthrough:* {feed}" + (f" · {', '.join(ports)}" if ports else ""), ""]
    return "\n".join(lines)


def category_slug(category: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", category.lower()).strip("-")


def docs_markdown(markdown: str) -> str:
    """Point figure links at the copies in docs/images/blocks (for the MkDocs block reference)."""
    return markdown.replace("](figures/", "](../images/blocks/")


def reference_pages() -> dict[str, str]:
    """Markdown pages for the block reference, keyed by file name."""
    pages: dict[str, str] = {}
    cats = dict(sorted(categories().items()))
    index = [
        "# Block reference",
        "",
        "This reference is generated from the block source code (`scripts/gen_block_reference.py`).",
        "",
    ]
    for cat, classes in cats.items():
        slug = category_slug(cat)
        index.append(f"## [{cat}]({slug}.md)\n")
        index += [f"- **{c.label}**: {block_doc(c).splitlines()[0]}" for c in classes]
        index.append("")
        body = [f"# {cat}", ""]
        for c in classes:
            body.append(docs_markdown(block_help_markdown(c)))
            body.append("---\n")
        pages[f"{slug}.md"] = "\n".join(body)
    pages["index.md"] = "\n".join(index)
    return pages


# Pages written once for GitHub (docs/) and converted for the in-app help: GitHub name -> in-app name.
SHARED_PAGES = {
    "concepts.md": "concepts.md",
    "coming-from-simulink.md": "coming_from_simulink.md",
    "model-files.md": "model_files.md",
    "physical.md": "physical.md",
    "statecharts.md": "statecharts.md",
    "tutorials/index.md": "tutorials.md",
}
"""Pages written for GitHub (docs/...) and converted for the in-app help by ``scripts/gen_block_reference.py``.
Edit the docs version; the app version is generated."""


def _tutorial_pages() -> None:
    from nervus.tutorials import TUTORIALS

    for t in TUTORIALS:
        SHARED_PAGES[f"tutorials/{t.slug}.md"] = f"{t.help_page}.md"


_tutorial_pages()


def docs_page_for_app(markdown: str) -> str:
    """Convert a GitHub docs page for the in-app help: block figures are bundled, screenshots are not, and links
    to other pages become plain text (the Help window has its own page list)."""
    import re

    s = re.sub(r"!\[[^\]]*\]\((?:\.\./)?images/blocks/([^)]+)\)", r"![](figures/\1)", markdown)
    figures = Path(__file__).parent / "gui" / "resources" / "figures"

    def screenshot(m: re.Match[str]) -> str:  # bundled screenshots stay (Tutorial 1), others are left out
        return f"![]({'figures/' + m.group(2)})\n\n" if (figures / m.group(2)).exists() else ""

    s = re.sub(r"!\[[^\]]*\]\((\.\./)?images/([^)/]+)\)\n\n?", screenshot, s)
    s = re.sub(r"\[([^\]]+)\]\((?!https?:)[^)]*\.md(#[^)]*)?\)", r"*\1*", s)

    def admonition(m: re.Match[str]) -> str:  # !!! tip "Title" + indented body → a quoted paragraph
        title = m.group(2) or m.group(1).capitalize()
        body = " ".join(line.strip() for line in m.group(3).splitlines() if line.strip())
        return f"> **{title}:** {body}\n"

    return re.sub(r'!!! (\w+)(?: "([^"]*)")?\n((?:    .*\n?)+)', admonition, s)
