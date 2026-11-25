"""Reading and writing model files.

A model file is UTF-8 JSON with the extension ``.nervus``::

    {
      "format": "nervus-model",
      "schema_version": 1,
      "name": "first_order",
      "meta": {},
      "settings": {"t_start": 0.0, "t_final": 10.0, "solver": "auto", ...},
      "workspace": [{"name": "Kp", "value": 1.5, "unit": "", "description": "..."}],
      "blocks": [
        {"name": "u", "type": "sources.step", "params": {"time": 1.0, ...}, "meta": {"position": [0, 0]}},
        {"name": "k", "type": "math.gain", "params": {"gain": "Kp", ...}}
      ],
      "connections": [
        {"src": "u", "src_port": 0, "dst": "sum", "dst_port": 0}
      ]
    }

Blocks are referred to by their stable ``type_id``. Every parameter is written out, including defaults, so a
file means the same thing even if a default changes later. Output is deterministic (stable ordering,
2-space indent), which keeps git diffs small and readable.

When the format changes, ``SCHEMA_VERSION`` goes up and a function is added to ``MIGRATIONS`` that upgrades a
file from the previous version, so old files keep loading.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import nervus.blocks  # noqa: F401  (registers the standard blocks)
from nervus.blocks.ports import Variant
from nervus.blocks.registry import get_block_class
from nervus.core.block import Block
from nervus.core.diagram import Diagram
from nervus.core.errors import ModelError, ParameterError
from nervus.core.settings import SimulationSettings
from nervus.core.testcase import TestCase
from nervus.core.workspace import Workspace

FORMAT = "nervus-model"
SCHEMA_VERSION = 4
FILE_EXTENSION = ".nervus"


def _v1_to_v2(doc: dict[str, Any]) -> dict[str, Any]:
    """Version 2 added the model workspace."""
    doc.setdefault("workspace", [])
    return doc


def _v2_to_v3(doc: dict[str, Any]) -> dict[str, Any]:
    """Version 3 added subsystems (a block may contain a nested "system"); nothing to convert."""
    return doc


def _v3_to_v4(doc: dict[str, Any]) -> dict[str, Any]:
    """Version 4 added test cases."""
    doc.setdefault("tests", [])
    return doc


MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4}
"""``MIGRATIONS[n]`` upgrades a version-``n`` document to version ``n + 1``."""


def _block_dict(b: Block) -> dict[str, Any]:
    d: dict[str, Any] = {"name": b.name, "type": b.type_id, "params": b.params_jsonable()}
    if b.meta:
        d["meta"] = b.meta
    system = getattr(b, "system", None)
    if isinstance(system, Diagram):
        d["system"] = system_to_dict(system)
        mask = getattr(b, "mask", None)
        if mask is not None and (len(mask) > 0 or bool(getattr(b, "mask_icon", ""))):
            d["mask"] = {
                "variables": mask.to_list(),
                "icon": getattr(b, "mask_icon", ""),
                "description": getattr(b, "mask_description", ""),
            }
        link = getattr(b, "library_link", None)
        if link is not None:
            d["link"] = {"library": link.library, "component": link.component}
        variants = getattr(b, "variants", None)
        if variants:
            active = getattr(b, "active_variant", "")
            d["active_variant"] = active
            d["variants"] = [
                {"name": name, "condition": v.condition}
                | ({} if name == active else {"system": system_to_dict(v.system)})
                for name, v in variants.items()
            ]
    return d


def system_to_dict(diagram: Diagram) -> dict[str, Any]:
    """Blocks and connections of a diagram (used for the model and for nested subsystems)."""
    out: dict[str, Any] = {}
    if diagram.meta:
        out["meta"] = diagram.meta
    out["blocks"] = [_block_dict(b) for b in diagram.blocks]
    out["connections"] = [
        {"src": c.src.name, "src_port": c.src_port, "dst": c.dst.name, "dst_port": c.dst_port}
        | ({"meta": c.meta} if c.meta else {})
        for c in diagram.connections
    ]
    if diagram.links:
        out["links"] = [
            {"a": k.a.name, "a_port": k.a_port, "b": k.b.name, "b_port": k.b_port}
            | ({"meta": k.meta} if k.meta else {})
            for k in diagram.links
        ]
    return out


def to_dict(diagram: Diagram) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "name": diagram.name,
        "meta": diagram.meta,
        "settings": diagram.settings.to_dict(),
        "workspace": diagram.workspace.to_list(),
        **({"signals": diagram.signals.to_list()} if len(diagram.signals) else {}),
        **({"requirements": diagram.requirements.to_list()} if len(diagram.requirements) else {}),
        **{k: v for k, v in system_to_dict(diagram).items() if k != "meta"},
        "tests": [t.to_dict() for t in diagram.tests],
    }


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a model document to the current schema version."""
    version = data.get("schema_version")
    if not isinstance(version, int):
        raise ModelError("model file has no valid schema_version")
    if version > SCHEMA_VERSION:
        raise ModelError(
            f"model file uses schema version {version}, but this version of Nervus only supports up to "
            f"{SCHEMA_VERSION}. Please upgrade Nervus."
        )
    while version < SCHEMA_VERSION:
        if version not in MIGRATIONS:
            raise ModelError(f"no migration from schema version {version}")
        data = MIGRATIONS[version](data)
        version += 1
        data["schema_version"] = version
    return data


class _Source:
    path: str | None = None


_SOURCE = _Source()


def from_dict(data: dict[str, Any]) -> Diagram:
    if data.get("format") != FORMAT:
        raise ModelError("not a Nervus model file (missing 'format': 'nervus-model')")
    data = migrate(dict(data))
    d = Diagram(str(data.get("name", "untitled")))
    d.source_path = _SOURCE.path
    d.meta = dict(data.get("meta", {}))
    try:
        d.settings = SimulationSettings.from_dict(dict(data.get("settings", {})))
    except (TypeError, ValueError) as exc:
        raise ModelError(f"invalid simulation settings: {exc}") from exc
    try:
        d.workspace = Workspace.from_list(list(data.get("workspace", [])))
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelError(f"invalid workspace: {exc}") from exc
    try:
        from nervus.core.dictionary import DataDictionary

        d.signals = DataDictionary.from_list(list(data.get("signals", [])))
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelError(f"invalid data dictionary: {exc}") from exc
    try:
        from nervus.core.requirements import Requirements

        d.requirements = Requirements.from_list(list(data.get("requirements", [])))
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelError(f"invalid requirements: {exc}") from exc
    _fill_system(d, data, "")
    try:
        d.tests = [TestCase.from_dict(t) for t in data.get("tests", [])]
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelError(f"invalid test case: {exc}") from exc
    # Expressions inside subsystems can only be resolved once the whole tree (and workspace) exists.
    with contextlib.suppress(ModelError):
        d.resolve_parameters()
    return d


def system_from_dict(data: dict[str, Any], name: str = "system", prefix: str = "") -> Diagram:
    """Build a (sub)system diagram from :func:`system_to_dict` output."""
    inner = Diagram(name)
    inner.meta = dict(data.get("meta", {}))
    _fill_system(inner, data, prefix)
    return inner


def _fill_system(d: Diagram, data: dict[str, Any], prefix: str) -> None:
    for entry in data.get("blocks", []):
        name = entry.get("name", "")
        path = prefix + name
        try:
            cls = get_block_class(entry["type"])
        except KeyError as exc:
            raise ModelError(f"block {path!r}: {exc.args[0]}", (path,)) from exc
        try:
            if "system" in entry:
                inner = Diagram(name)
                inner.meta = dict(entry["system"].get("meta", {}))
                inner.source_path = d.root.source_path  # relative file paths (DBC, data) resolve while loading
                block = cls(name, system=inner, **entry.get("params", {}))
                if "mask" in entry:
                    block.mask = Workspace.from_list(list(entry["mask"].get("variables", [])))  # type: ignore[attr-defined]
                    block.mask_icon = str(entry["mask"].get("icon", ""))  # type: ignore[attr-defined]
                    block.mask_description = str(entry["mask"].get("description", ""))  # type: ignore[attr-defined]
                _fill_system(inner, entry["system"], path + "/")
                if "link" in entry:
                    from nervus.library import LibraryLink

                    block.library_link = LibraryLink(str(entry["link"]["library"]), str(entry["link"]["component"]))  # type: ignore[attr-defined]
                if entry.get("variants"):
                    active = str(entry.get("active_variant", ""))
                    block.active_variant = active  # type: ignore[attr-defined]
                    for v in entry["variants"]:
                        vname = str(v["name"])
                        system = inner if vname == active else system_from_dict(v["system"], name, f"{path}[{vname}]/")
                        system.parent = block
                        block.variants[vname] = Variant(system, str(v.get("condition", "")))  # type: ignore[attr-defined]
                    block.activate(active)  # type: ignore[attr-defined]
            else:
                block = cls(name, **entry.get("params", {}))
        except ParameterError as exc:
            raise ModelError(f"block {path!r}: {exc}", (path,)) from exc
        block.meta = dict(entry.get("meta", {}))
        d.add(block)
    for c in data.get("connections", []):
        try:
            src, dst = d.block(c["src"]), d.block(c["dst"])
            conn = d.connect((src, int(c["src_port"])), (dst, int(c["dst_port"])))
            conn.meta.update(c.get("meta", {}))
        except (KeyError, IndexError) as exc:
            why = ""
            for name in (c.get("src"), c.get("dst")):  # a block with fewer ports than saved: say why, if it knows
                try:
                    d.block(name).validate_params()
                except KeyError:
                    pass
                except Exception as reason:  # e.g. its DBC file cannot be read without cantools
                    why = f" ({name}: {reason})"
                    break
            raise ModelError(f"invalid connection {prefix}{c}: {exc}{why}") from exc
    for k in data.get("links", []):
        try:
            link = d.link((d.block(k["a"]), str(k["a_port"])), (d.block(k["b"]), str(k["b_port"])))
            link.meta.update(k.get("meta", {}))
        except KeyError as exc:
            raise ModelError(f"invalid physical link {prefix}{k}: {exc}") from exc


def dumps(diagram: Diagram) -> str:
    return json.dumps(to_dict(diagram), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def loads(text: str) -> Diagram:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelError(f"model file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelError("model file must contain a JSON object")
    return from_dict(data)


def save(diagram: Diagram, path: str | Path) -> Path:
    """Save ``diagram`` to ``path`` (the ``.nervus`` extension is added if missing). Returns the path."""
    p = Path(path)
    if not p.suffix:
        p = p.with_suffix(FILE_EXTENSION)
    diagram.source_path = str(p.resolve())
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(dumps(diagram), encoding="utf-8")
    tmp.replace(p)  # atomic: never leave a half-written model file
    return p


def load(path: str | Path) -> Diagram:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ModelError(f"{Path(path).name} is not a model file (not UTF-8 text): {exc}") from exc
    try:
        data = json.loads(text) if text.strip().startswith("{") else None
    except json.JSONDecodeError as exc:
        raise ModelError(f"model file is not valid JSON: {exc}") from exc
    if isinstance(data, dict) and data.get("format") == FORMAT:
        # Set the source path before building blocks, so relative file parameters (e.g. DBC files) resolve.
        _SOURCE.path = str(Path(path).resolve())
    try:
        d = loads(text)
    finally:
        _SOURCE.path = None
    d.source_path = str(Path(path).resolve())
    from nervus.library import refresh_links

    d.link_warnings = refresh_links(d)  # linked subsystems are updated from their libraries
    return d
