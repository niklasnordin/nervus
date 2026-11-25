import pytest

from nervus import Diagram, ModelError, simulate
from nervus.blocks import Constant, Gain, Inport, Outport, Scope, Subsystem
from nervus.blocks.ports import Variant
from nervus.core.errors import ParameterError
from nervus.io import dumps, loads


def gain_system(k: float) -> Diagram:
    d = Diagram()
    u = d.add(Inport(port=1), name="u")
    d.chain(u, d.add(Gain(gain=k), name="g"), d.add(Outport(port=1), name="y"))
    return d


def model() -> Diagram:
    d = Diagram()
    d.workspace["ENGINE"] = 1
    sub = Subsystem(system=gain_system(2.0))
    sub.active_variant = "petrol"
    sub.add_variant("diesel", gain_system(3.0), "ENGINE == 2")
    sub.variants["petrol"].condition = "ENGINE == 1"
    d.add(sub, name="engine")
    d.chain(d.add(Constant(value=1.0)), sub, d.add(Scope()))
    return d


def test_variant_selected_by_workspace_condition() -> None:
    d = model()
    assert list(d.block("engine").variants) == ["petrol", "diesel"]
    assert simulate(d, 0.0)["engine/g"][-1] == 2.0
    assert simulate(d, 0.0, params={"ENGINE": 2})["engine/g"][-1] == 3.0
    d.workspace["ENGINE"] = 2
    assert simulate(d, 0.0)["engine/g"][-1] == 3.0
    assert d.block("engine").active_variant == "diesel"


def test_no_true_condition_default_and_errors() -> None:
    d = model()
    with pytest.raises(ModelError, match="no variant condition is true"):
        simulate(d, 0.0, params={"ENGINE": 7})
    d.block("engine").variants["petrol"].condition = ""  # petrol becomes the default
    assert simulate(d, 0.0, params={"ENGINE": 7})["engine/g"][-1] == 2.0
    d.block("engine").variants["petrol"].condition = "ENGINE > 0"
    with pytest.raises(ModelError, match="several variant conditions"):
        simulate(d, 0.0, params={"ENGINE": 2})


def test_manual_selection_without_conditions_and_copy() -> None:
    sub = Subsystem(system=gain_system(5.0))
    copy = sub.add_variant("tuned")
    assert list(sub.variants) == ["default", "tuned"] and copy is not sub.system
    copy.block("g").set_param("gain", 6.0)
    d = Diagram()
    d.add(sub, name="s")
    d.chain(d.add(Constant()), sub, d.add(Scope()))
    assert simulate(d, 0.0)["s/g"][-1] == 5.0
    sub.activate("tuned")
    assert simulate(d, 0.0)["s/g"][-1] == 6.0
    sub.remove_variant("tuned")
    assert sub.active_variant == "default"
    with pytest.raises(ParameterError, match="only variant"):
        sub.remove_variant("default")


def test_variants_need_same_ports() -> None:
    d = model()
    other = Diagram()
    other.add(Inport(port=1), name="u")
    d.block("engine").variants["diesel"] = Variant(other, "ENGINE == 2")
    assert any("same ports" in i.message for i in d.check())


def test_roundtrip_keeps_variants() -> None:
    again = loads(dumps(model()))
    sub = again.block("engine")
    assert list(sub.variants) == ["petrol", "diesel"] and sub.active_variant == "petrol"
    assert sub.variants["diesel"].condition == "ENGINE == 2"
    assert simulate(again, 0.0, params={"ENGINE": 2})["engine/g"][-1] == 3.0


def test_codegen_uses_active_variant() -> None:
    from nervus.blocks import ZeroOrderHold
    from nervus.codegen import generate_c
    from nervus.codegen.sil import back_to_back, find_compiler

    d = Diagram("variants")
    d.workspace["ENGINE"] = 2
    sub = Subsystem(system=gain_system(2.0))
    sub.active_variant = "petrol"
    sub.add_variant("diesel", gain_system(3.0), "ENGINE == 2")
    sub.variants["petrol"].condition = "ENGINE == 1"
    d.add(sub, name="engine")
    u = d.add(Inport(port=1), name="u")
    d.chain(u, d.add(ZeroOrderHold(sample_time=0.01)), sub, d.add(Outport(port=1), name="y"))
    code = generate_c(d)
    assert any(n.startswith("engine/g") for n in code.model.names)
    body = code.files["variants.c"].split("*/", 1)[1]  # after the header, which names the Nervus version
    assert "3.0" in body and "2.0" not in body
    if find_compiler() is not None:
        res = back_to_back(d, {"u": 1.0}, 5, tolerance=0.0)
        assert res.passed and res.c["y"][-1, 0] == 3.0


def test_condition_expressions() -> None:
    from nervus.core.expressions import parse_value

    assert parse_value("(A > 1) && (B ~= 3)", {"A": 2, "B": 1}) == 1.0
    assert parse_value("~A || A == 4", {"A": 4}) == 1.0
    assert parse_value("0 < x < 3", {"x": 5}) == 0.0
