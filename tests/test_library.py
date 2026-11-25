from pathlib import Path

import pytest

from nervus import Diagram, ModelError, simulate
from nervus.blocks import Constant, Gain, Inport, Outport, Scope, Subsystem
from nervus.core import Variable
from nervus.io import load, save
from nervus.library import components, instantiate, load_library, refresh_links, save_to_library


def component(k_default: float = 2.0) -> Subsystem:
    inner = Diagram()
    u = inner.add(Inport(port=1), name="u")
    inner.chain(u, inner.add(Gain(gain="K"), name="g"), inner.add(Outport(port=1), name="y"))
    sub = Subsystem("scale", system=inner)
    sub.mask.add(Variable("K", k_default, "Scale"))
    sub.mask_icon = "×K"
    return sub


def model_with(lib: Path, folder: Path) -> Diagram:
    d = Diagram("m")
    a = d.add(instantiate(lib.name, "scale", base_dir=folder), name="a")
    b = d.add(instantiate(lib.name, "scale", base_dir=folder), name="b")
    b.mask["K"] = 5.0
    c = d.add(Constant(value=1.0))
    d.chain(c, a, d.add(Scope()))
    d.chain(c, b, d.add(Scope()))
    return d


def test_save_instantiate_and_update(tmp_path: Path) -> None:
    lib = save_to_library(component(), tmp_path / "parts")
    assert lib.suffix == ".nervlib" and [c.name for c in components(load_library(lib))] == ["scale"]
    d = model_with(lib, tmp_path)
    assert d.block("a").library_link.component == "scale"
    save(d, tmp_path / "m.nervus")
    r = simulate(load(tmp_path / "m.nervus"), 0.0)
    assert r["a/g"][-1] == 2.0 and r["b/g"][-1] == 5.0

    # Change the component in the library: an extra gain inside, new default K.
    changed = component(3.0)
    changed.system.block("g").set_param("gain", "10 * K")
    save_to_library(changed, lib)
    again = load(tmp_path / "m.nervus")
    assert not again.link_warnings
    r = simulate(again, 0.0)
    assert r["a/g"][-1] == 20.0  # instance a kept its own K = 2 (stored in the model)
    assert r["b/g"][-1] == 50.0  # instance b kept K = 5


def test_missing_library_keeps_copy(tmp_path: Path) -> None:
    lib = save_to_library(component(), tmp_path / "parts.nervlib")
    d = model_with(lib, tmp_path)
    save(d, tmp_path / "m.nervus")
    lib.unlink()
    again = load(tmp_path / "m.nervus")
    assert again.link_warnings and "using the copy" in again.link_warnings[0]
    assert any("not found" in i.message for i in again.check())
    assert simulate(again, 0.0)["b/g"][-1] == 5.0


def test_unknown_component_and_refresh(tmp_path: Path) -> None:
    lib = save_to_library(component(), tmp_path / "parts.nervlib")
    with pytest.raises(ModelError, match="no component"):
        instantiate(lib, "nope")
    d = model_with(lib, tmp_path)
    d.block("a").system.block("g").set_param("gain", 99.0)  # local edit is overwritten by a refresh
    assert refresh_links(d, tmp_path) == []
    assert d.block("a").system.block("g").expressions["gain"] == "K"


def test_link_saved_on_windows_resolves(tmp_path: Path) -> None:
    """A model saved on Windows stores the library path with backslashes; it must resolve on every system."""
    from nervus.library import LibraryLink

    (tmp_path / "libs").mkdir()
    lib = save_to_library(component(), tmp_path / "libs" / "parts")
    (tmp_path / "models").mkdir()
    link = LibraryLink("..\\libs\\parts.nervlib", "scale")
    assert link.resolve(tmp_path / "models") == lib.resolve()
