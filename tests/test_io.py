import json

import numpy as np
import pytest

from nervus import Diagram, ModelError, load, save, simulate
from nervus.blocks import Gain, Integrator, Saturation, block_types
from nervus.io import SCHEMA_VERSION, dumps, loads, model_file


def test_roundtrip_preserves_simulation(first_order: Diagram, tmp_path) -> None:  # type: ignore[no-untyped-def]
    first_order.block("y").meta["position"] = [100, 50]
    first_order.settings.t_final = 2.0
    path = save(first_order, tmp_path / "model")
    assert path.suffix == ".nervus"
    loaded = load(path)
    assert [b.name for b in loaded] == [b.name for b in first_order]
    assert loaded.block("y").meta == {"position": [100, 50]}
    assert loaded.settings.t_final == 2.0
    a, b = simulate(first_order), simulate(loaded)
    np.testing.assert_array_equal(a["y"], b["y"])


def test_output_is_deterministic(first_order: Diagram) -> None:
    assert dumps(first_order) == dumps(loads(dumps(first_order)))


def test_every_block_type_roundtrips() -> None:
    d = Diagram()
    for cls in block_types().values():
        d.add(cls())
    again = loads(dumps(d))
    for a, b in zip(d, again, strict=True):
        assert type(a) is type(b)
        assert a.params_jsonable() == b.params_jsonable()


def test_infinite_values_survive() -> None:
    d = Diagram()
    d.add(Integrator(upper=np.inf), name="i")
    text = dumps(d)
    json.loads(text)  # strict JSON, no Infinity literal
    assert loads(text).block("i").params["upper"] == np.inf


def test_unknown_block_type() -> None:
    d = Diagram()
    d.add(Gain(), name="g")
    data = model_file.to_dict(d)
    data["blocks"][0]["type"] = "nope.block"
    with pytest.raises(ModelError, match="unknown block type") as exc:
        model_file.from_dict(data)
    assert exc.value.blocks == ("g",)


def test_bad_parameter_reports_block() -> None:
    d = Diagram()
    d.add(Saturation(), name="sat")
    data = model_file.to_dict(d)
    data["blocks"][0]["params"]["upper"] = -10
    with pytest.raises(ModelError, match="sat"):
        model_file.from_dict(data)


def test_not_a_model() -> None:
    with pytest.raises(ModelError):
        loads("[]")
    with pytest.raises(ModelError):
        loads('{"format": "other"}')
    with pytest.raises(ModelError, match="valid JSON"):
        loads("{")


def test_newer_schema_rejected(first_order: Diagram) -> None:
    data = model_file.to_dict(first_order)
    data["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ModelError, match="upgrade"):
        model_file.from_dict(data)


def test_migration_hook(first_order: Diagram, monkeypatch: pytest.MonkeyPatch) -> None:
    data = model_file.to_dict(first_order)
    data["schema_version"] = 0
    data["title"] = data.pop("name")  # pretend v0 called it "title"

    def v0_to_v1(doc: dict) -> dict:  # type: ignore[type-arg]
        doc["name"] = doc.pop("title")
        return doc

    monkeypatch.setitem(model_file.MIGRATIONS, 0, v0_to_v1)
    assert model_file.from_dict(data).name == "first_order"


@pytest.mark.parametrize("content", [b'{"format": "nervus-model", "schema_version": 4, "name": "x"', b"{\xff\xfe"])
def test_damaged_file_is_a_model_error(content: bytes, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: a truncated (or binary) model file raised JSONDecodeError / UnicodeDecodeError from load()."""
    path = tmp_path / "damaged.nervus"
    path.write_bytes(content)
    with pytest.raises(ModelError):
        load(path)
