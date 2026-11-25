"""The architecture rules of CONTRIBUTING.md, checked on the source code (no Qt needed).

- Only ``nervus.gui`` may use Qt; the rest of the package (core, sim, solvers, blocks, io, codegen, ...) imports
  neither Qt nor the GUI, so it works in scripts, tests and CI without a display.
- In the GUI (model-view-controller), models and controllers never import views, and models never import
  controllers.
- Views do not change the core objects themselves: they call a controller, which pushes an undoable command.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "nervus"
GUI = PACKAGE / "gui"

QT_MODULES = ("PySide6", "shiboken6", "pyqtgraph", "PyQt5", "PyQt6")


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path, top_level_only: bool = False) -> Iterator[tuple[int, str]]:
    """(line, absolute module name) of every import in ``path``; relative imports are resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    package = _module_name(path) if path.name == "__init__.py" else _module_name(path).rpartition(".")[0]
    nodes = tree.body if top_level_only else ast.walk(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            yield node.lineno, module
            for alias in node.names:  # "from nervus import gui"
                yield node.lineno, f"{module}.{alias.name}"


def _is_under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_only_the_gui_uses_qt() -> None:
    bad = []
    for path in _python_files(PACKAGE):
        if GUI in path.parents:
            continue
        # The command line starts the GUI, but only inside a function, after checking that PySide6 is installed.
        top_level_only = path == PACKAGE / "cli.py"
        for line, module in _imports(path, top_level_only):
            if any(_is_under(module, q) for q in QT_MODULES) or _is_under(module, "nervus.gui"):
                bad.append(f"{path.relative_to(SRC)}:{line} imports {module}")
    assert not bad, "Qt and the GUI may only be used in nervus.gui:\n" + "\n".join(bad)


def test_cli_imports_the_gui_lazily() -> None:
    """cli.py may start the GUI, but not import it when the module loads (``nervus --help`` without PySide6)."""
    path = PACKAGE / "cli.py"
    lazy = [m for _line, m in _imports(path) if _is_under(m, "nervus.gui") or _is_under(m, "PySide6")]
    assert lazy, "expected cli.py to start the GUI with a guarded import"


def test_models_and_controllers_do_not_import_views() -> None:
    bad = []
    for layer, forbidden in (
        ("models", ("nervus.gui.views", "nervus.gui.controllers")),
        ("controllers", ("nervus.gui.views",)),
    ):
        for path in _python_files(GUI / layer):
            for line, module in _imports(path):
                if any(_is_under(module, f) for f in forbidden):
                    bad.append(f"{path.relative_to(SRC)}:{line} imports {module}")
    assert not bad, "models must not import controllers or views, controllers must not import views:\n" + "\n".join(bad)


# ---------------------------------------------------------------------------- views do not mutate the model

MUTATORS = {
    "add",
    "remove",
    "connect",
    "disconnect",
    "link",
    "unlink",
    "rename",
    "chain",
    "reconnect",
    "relink",
    "set_param",
    "set_params",
    "replace",
    "insert",
    "apply",
    "activate",
    "add_variant",
}
"""Methods of Diagram, Block, Workspace and the data dictionary that change the model."""

CORE_NAMES = {"diagram", "root", "system", "inner", "workspace", "ws", "requirements", "reqs", "block", "subsystem",
              "sub", "chart_block"}  # fmt: skip
"""Names that hold core objects in the views. A call is checked when its receiver ends in one of them, e.g.
``self.model.root.workspace.add(...)``; ``self.diagram_model.blockAdded.connect(...)`` (a Qt signal) is not."""


def _names_core_object(expr: ast.expr) -> bool:
    last = expr.attr if isinstance(expr, ast.Attribute) else expr.id if isinstance(expr, ast.Name) else None
    return last in CORE_NAMES


MODEL_ATTRIBUTES = {"settings", "tests", "requirements", "workspace", "signals", "mask", "mask_icon",
                    "mask_description", "library_link", "variants", "active_variant", "system", "name", "meta",
                    "params"}  # fmt: skip
"""Attributes of core objects that views must not assign."""

ALLOWED: dict[str, str] = {
    # "path: code" (as the failure message prints it, without the line number) -> why it is not a violation, e.g.
    # a change to a working copy that a controller commits later. Keep this short: views call a controller.
}


def _mutations(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            if (node.func.attr in MUTATORS and _names_core_object(node.func.value)) or (
                node.func.attr in {"pop", "update", "setdefault", "clear"} and receiver.endswith(".meta")
            ):
                yield f"{node.lineno}: {ast.unparse(node.func)}()"
        elif isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
            targets = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
            for t in targets:
                if isinstance(t, ast.Subscript):
                    owner = t.value
                    if (isinstance(owner, ast.Attribute) and owner.attr in {"meta", "params"}) or _names_core_object(
                        owner
                    ):
                        yield f"{node.lineno}: {ast.unparse(t)} ="
                elif isinstance(t, ast.Attribute) and t.attr in MODEL_ATTRIBUTES and _names_core_object(t.value):
                    yield f"{node.lineno}: {ast.unparse(t)} ="


def test_views_change_the_model_only_through_controllers() -> None:
    bad = []
    for path in [*_python_files(GUI / "views"), GUI / "app.py"]:
        rel = path.relative_to(SRC).as_posix()
        for hit in _mutations(path):
            line, _, code = hit.partition(": ")
            if f"{rel}: {code}" not in ALLOWED:
                bad.append(f"{rel}:{line}: {code}")
    assert not bad, (
        "views must change the model through a controller (an undoable command), not directly:\n" + "\n".join(bad)
    )


def test_the_mutation_scan_finds_mutations(tmp_path: Path) -> None:
    """The scan above is a heuristic; make sure it still recognises the forms it is meant to catch."""
    src = tmp_path / "view.py"
    src.write_text(
        "def f(self, block, d):\n"
        "    block.meta['size'] = [1, 2]\n"
        "    block.meta.pop('size')\n"
        "    self.diagram_model.root.diagram.connect(a, b)\n"
        "    block.set_params({'gain': 2})\n"
        "    self.model.root.settings = s\n"
        "    self.model.root.workspace['k'] = 1\n"
        "    self.button.clicked.connect(self.go)\n"  # a Qt signal: not a mutation
        "    self.diagram_model.blockAdded.connect(self.go)\n",
        encoding="utf-8",
    )
    hits = list(_mutations(src))
    assert len(hits) == 6, hits
