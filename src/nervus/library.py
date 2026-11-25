"""Component libraries: reusable subsystems shared between models.

A library is an ordinary model file with the extension ``.nervlib``. Each subsystem at its top level is a
**component** (usually masked, e.g. an engine model with ``displacement`` and ``n_cyl``). A model uses a
component through a **linked** subsystem: a copy that remembers where it came from. Whenever the model is
loaded (or :func:`refresh_links` is called), linked subsystems are updated from the library, so fixing a
component once fixes every model that uses it. Each instance keeps its own mask parameter values.

The model file also stores the linked copy, so a model still opens when the library is missing.

    from nervus.library import instantiate, save_to_library

    save_to_library(engine_subsystem, "components.nervlib")           # add or replace a component
    engine = model.add(instantiate("components.nervlib", "engine"))     # a linked copy
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nervus.core.diagram import Diagram
from nervus.core.errors import ModelError

LIBRARY_EXTENSION = ".nervlib"


@dataclass
class LibraryLink:
    library: str
    """Path of the library file, relative to the model file (or absolute)."""
    component: str
    """Name of the subsystem in the library."""

    def resolve(self, base_dir: str | Path | None) -> Path:
        # A model saved on Windows stores "..\\libs\\parts.nervlib"; "/" works on every system.
        p = Path(self.library.replace("\\", "/"))
        if not p.is_absolute() and base_dir is not None:
            p = Path(base_dir) / p
        return p.resolve()

    def __str__(self) -> str:
        return f"{self.library}:{self.component}"


_CACHE: dict[Path, tuple[float, Diagram]] = {}


def load_library(path: str | Path) -> Diagram:
    """Load a library file (cached until the file changes)."""
    from nervus.io.model_file import load

    p = Path(path).resolve()
    if not p.exists():
        raise ModelError(f"library not found: {p}")
    mtime = p.stat().st_mtime
    hit = _CACHE.get(p)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    lib = load(p)
    _CACHE[p] = (mtime, lib)
    return lib


def components(library: Diagram) -> list[Any]:
    """The components of a library: its top-level subsystems, by name."""
    from nervus.blocks.ports import Subsystem

    return sorted((b for b in library.blocks if isinstance(b, Subsystem)), key=lambda b: b.name)


def _copy_block(block: Any) -> Any:
    from nervus.io.model_file import _block_dict, system_from_dict

    temp = system_from_dict({"blocks": [_block_dict(block)], "connections": []})
    copy = temp.block(block.name)
    temp.remove(copy)
    return copy


def _component(path: Path, name: str) -> Any:
    lib = load_library(path)
    try:
        comp = lib.block(name)
    except KeyError:
        raise ModelError(f"library {path.name} has no component {name!r}") from None
    if not hasattr(comp, "system"):
        raise ModelError(f"{name!r} in {path.name} is not a subsystem")
    return comp


def instantiate(library: str | Path, component: str, base_dir: str | Path | None = None) -> Any:
    """A new linked subsystem: a copy of ``component`` from ``library``.

    ``library`` is stored as given; pass a path relative to the model file's folder (and ``base_dir`` = that
    folder) to keep models and libraries movable together.
    """
    link = LibraryLink(str(library), component)
    block = _copy_block(_component(link.resolve(base_dir), component))
    block.meta.pop("position", None)
    block.library_link = link
    return block


def _apply(instance: Any, comp: Any) -> None:
    """Make ``instance`` a fresh copy of library component ``comp``, keeping its own mask values."""
    fresh = _copy_block(comp)
    values = {v.name: v.value for v in instance.mask.variables}
    instance.system = fresh.system
    instance.system.parent = instance
    instance.variants = fresh.variants
    for v in instance.variants.values():
        v.system.parent = instance
    instance.active_variant = fresh.active_variant
    instance.mask = fresh.mask
    for name, value in values.items():
        if name in instance.mask:
            instance.mask[name] = value
    instance.mask_icon = fresh.mask_icon
    instance.mask_description = fresh.mask_description
    for k, v in fresh.params.items():
        instance.params[k] = v
    instance.expressions = dict(fresh.expressions)


def linked_subsystems(diagram: Diagram) -> list[tuple[str, Any]]:
    """(path, block) of every linked subsystem in the model (not looking inside linked ones)."""
    out = []
    for b in diagram.blocks:
        if getattr(b, "library_link", None) is not None:
            out.append((b.name, b))
        elif isinstance(getattr(b, "system", None), Diagram):
            out += [(f"{b.name}/{p}", x) for p, x in linked_subsystems(b.system)]  # type: ignore[attr-defined]
    return out


def refresh_links(diagram: Diagram, base_dir: str | Path | None = None) -> list[str]:
    """Update every linked subsystem from its library. Returns warnings (e.g. a missing library), in which
    case the copy stored in the model is kept."""
    root = diagram.root
    if base_dir is None and root.source_path:
        base_dir = Path(root.source_path).parent
    warnings = []
    for path, block in linked_subsystems(diagram):
        link: LibraryLink = block.library_link
        try:
            _apply(block, _component(link.resolve(base_dir), link.component))
        except ModelError as exc:
            warnings.append(f"{path}: {exc}; using the copy saved in the model")
    return warnings


def save_to_library(subsystem: Any, library: str | Path, name: str | None = None) -> Path:
    """Add ``subsystem`` to ``library`` as component ``name`` (default: its name), replacing a component with
    the same name. Creates the library file if needed. Returns the library path."""
    from nervus.io.model_file import load, save

    p = Path(library)
    if not p.suffix:
        p = p.with_suffix(LIBRARY_EXTENSION)
    lib = load(p) if p.exists() else Diagram(p.stem)
    name = name or subsystem.name
    position = None
    if name in [b.name for b in lib.blocks]:
        position = lib.block(name).meta.get("position")
        lib.remove(name)
    copy = _copy_block(subsystem)
    copy.library_link = None
    copy.meta.pop("position", None)
    if position is not None:
        copy.meta["position"] = position
    lib.add(copy, name=name)
    save(lib, p)
    _CACHE.pop(p.resolve(), None)
    return p


def locked_by(diagram: Diagram | None) -> Any:
    """The linked subsystem that ``diagram`` is part of (its contents come from a library), or ``None``."""
    d = diagram
    while d is not None and d.parent is not None:
        if getattr(d.parent, "library_link", None) is not None:
            return d.parent
        d = d.parent.diagram
    return None
