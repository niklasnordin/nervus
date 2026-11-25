"""Generate the tutorial overview, each tutorial's "at a glance" box and "Next" link, and the docs navigation, from
the registry in ``nervus.tutorials``. Run by ``scripts/gen_block_reference.py``."""

from __future__ import annotations

import re
from pathlib import Path

from nervus.tutorials import CATEGORIES, TUTORIALS, Tutorial, by_slug, ordered

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs" / "tutorials"


def _link(t: Tutorial) -> str:
    return f"[{t.title}]({t.slug}.md)"


def _next(t: Tutorial) -> Tutorial | None:
    seq = ordered()
    k = seq.index(t)
    return seq[k + 1] if k + 1 < len(seq) else None


def _box(t: Tutorial) -> str:
    tuts = by_slug()
    before = ", ".join(_link(tuts[s]) for s in t.before if s in tuts)
    if t.read_first:
        before = f"{before}; read {t.read_first} first" if before else f"read {t.read_first} first"
    if t.slug == "first-model":
        before = "nothing: start here (the editor needs `uv sync --extra gui`)"
        follow = "build the model yourself, step by step below; the finished model is `examples/cruise_control.nervus`"
    else:
        opens = ", ".join(f"`examples/{m}`" for m in t.models)
        follow = (f"start the editor (`uv run nervus`), **File ▸ Open** {opens}, press **▶** and double-click the "
                  "scopes; the figures below come from the same model")  # fmt: skip
    where = t.category + (f" ▸ {t.subcategory}" if t.subcategory else "")
    return (
        "| | |\n|---|---|\n"
        f"| **You will learn** | {t.learn} |\n"
        f"| **Before this** | {before or 'the first tutorial, ' + _link(tuts['first-model'])} |\n"
        f"| **Level and time** | {t.level}, about {t.minutes} minutes |\n"
        f"| **Follow along** | {follow} |\n"
        f"| **Topic** | {where} ([all tutorials](index.md)) |\n"
    )


def frame(t: Tutorial) -> None:
    p = DOCS / f"{t.slug}.md"
    s = p.read_text(encoding="utf-8")
    body = s.split("\n", 1)[1].lstrip("\n")
    if body.startswith("| | |"):  # replace the old box
        lines = body.split("\n")
        k = 0
        while k < len(lines) and lines[k].startswith("|"):
            k += 1
        body = "\n".join(lines[k:]).lstrip("\n")
    body = re.sub(r"\n## Next\n[\s\S]*$", "\n", body).rstrip() + "\n"
    nxt = _next(t)
    if nxt is not None and nxt.category == t.category:
        tail = f"Next in this topic: {_link(nxt)}. Or pick another one from the [tutorial overview](index.md).\n"
    elif nxt is not None:
        tail = (f"That was the last tutorial on {t.category.lower()}. Continue with {nxt.category.lower()}: "
                f"{_link(nxt)}, or pick another one from the [tutorial overview](index.md).\n")  # fmt: skip
    else:
        tail = "Pick another topic from the [tutorial overview](index.md), or build your own model.\n"
    p.write_text(f"# Tutorial: {t.title}\n\n{_box(t)}\n{body}\n## Next\n\n{tail}", encoding="utf-8")


def index() -> None:
    n = len(TUTORIALS)
    out = [
        "# Tutorials",
        "",
        f"{n} tutorials, grouped by topic. Each one uses a finished model in `examples/`: open it in the editor, run "
        "it, read why it behaves as it does, then change it. They are built around problems from the automotive "
        "industry and the classic experiments of engineering lab courses.",
        "",
        "Every tutorial has the same parts:",
        "",
        "- **At a glance**: what you will learn, what to do first, the level and the time it takes.",
        "- **Follow along**: which model to open, and what to run and look at.",
        "- **Experiments**: changes to try, each with the effect you should see.",
        "- **Next**: the next tutorial on the same topic.",
        "",
        f"New to Nervus? Start with **{_link(by_slug()['first-model'])}**: it builds a model from scratch in the "
        "editor, step by step, with a screenshot for each step. Then pick a topic.",
        "",
    ]
    for cat, intro, subs in CATEGORIES:
        tuts = [t for t in TUTORIALS if t.category == cat]
        if not tuts:
            continue
        out += [f"## {cat}", "", intro, ""]
        for sub, sub_intro in subs:
            group = [t for t in ordered() if t.category == cat and t.subcategory == sub]
            if not group:
                continue
            if sub:
                out += [f"### {sub}", ""] + ([sub_intro, ""] if sub_intro else [])
            out += ["| Tutorial | Level | Time | You will learn |", "|---|---|---|---|"]
            out += [f"| {_link(t)} | {t.level} | {t.minutes} min | {t.learn} |" for t in group]
            out.append("")
    out += [
        "## Tips for every tutorial",
        "",
        "- **Running the Python version**: each model is built by a script, e.g. `uv run python examples/truck.py`. "
        "It runs the model's tests and prints the key numbers quoted in the tutorial.",
        "- **Getting lost**: **File ▸ Open** the example again to start over; your changes are only saved if you save.",
        "- **Looking inside**: double-click a subsystem to open it, **Ctrl+↑** to go back up. Press **F1** on any "
        "block for its help page with the equations.",
        "- **Comparing**: every run is kept in the **Data Inspector** (Ctrl+I). Tick two runs and a signal to "
        "overlay them.",
        '- **Scripting**: the **Console** tab runs Python on the open model, e.g. `res["speed"].max()`.',
        "",
    ]
    (DOCS / "index.md").write_text("\n".join(out), encoding="utf-8")


def nav() -> None:
    """The Tutorials section of mkdocs.yml and the tutorial pages of the in-app help copies."""
    p = ROOT / "mkdocs.yml"
    s = p.read_text(encoding="utf-8")
    lines = ["  - Tutorials:", "      - Overview: tutorials/index.md"]
    for cat, _intro, subs in CATEGORIES:
        if not any(t.category == cat for t in TUTORIALS):
            continue
        lines.append(f"      - {cat}:")
        for sub, _ in subs:
            group = [t for t in ordered() if t.category == cat and t.subcategory == sub]
            if not group:
                continue
            indent = "          "
            if sub:
                lines.append(f"          - {sub}:")
                indent = "              "
            lines += [f'{indent}- "{t.title}": tutorials/{t.slug}.md' for t in group]
    s = re.sub(r"  - Tutorials:\n(?:      .*\n)+", "\n".join(lines) + "\n", s, count=1)
    app = ["          - Tutorials (app):", "              - Overview: app-help/tutorials.md"]  # a folded section
    app += [f'              - "{t.title}": app-help/{t.help_page}.md' for t in ordered()]
    s = re.sub(r"          - Tutorials \(app\):.*\n(?:(?:              |          - \"Tutorial)[^\n]*\n)*",
               "\n".join(app) + "\n", s, count=1)  # fmt: skip
    p.write_text(s, encoding="utf-8")


def readme() -> None:
    """The tutorial table in README.md (between the tutorials:begin/end markers)."""
    p = ROOT / "README.md"
    s = p.read_text(encoding="utf-8")
    rows = ["| Topic | Tutorials |", "|---|---|"]
    for cat, _intro, subs in CATEGORIES:
        for sub, _ in subs:
            group = [t for t in ordered() if t.category == cat and t.subcategory == sub]
            if group:
                label = f"{cat}: {sub.lower()}" if sub else cat
                rows.append(
                    f"| {label} | " + ", ".join(f"[{t.title}](docs/tutorials/{t.slug}.md)" for t in group) + " |"
                )
    s = re.sub(r"<!-- tutorials:begin -->[\s\S]*?<!-- tutorials:end -->",
               "<!-- tutorials:begin -->\n" + "\n".join(rows) + "\n<!-- tutorials:end -->", s)  # fmt: skip
    p.write_text(s, encoding="utf-8")


def main() -> None:
    for t in TUTORIALS:
        frame(t)
    index()
    nav()
    readme()


if __name__ == "__main__":
    main()
