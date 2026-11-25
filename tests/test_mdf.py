"""MDF4 import and export."""

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("asammdf")
import asammdf

from nervus import Diagram, simulate
from nervus.blocks import Mux, Scope, Sine, Step
from nervus.core.errors import NervusError
from nervus.mdf import mdf_channels, read_mdf, write_mdf


def test_roundtrip(tmp_path: Path) -> None:
    d = Diagram()
    s = d.add(Sine(amplitude=2.0), name="wave")
    st = d.add(Step(time=0.5), name="step")
    m = d.add(Mux(num_inputs=2), name="both")
    d.connect(s, (m, 0))
    d.connect(st, (m, 1))
    d.connect(m, d.add(Scope()))
    r = simulate(d, 1.0, solver="rk4", step=0.01)
    p = write_mdf(r, tmp_path / "run", units={"wave": "V"})
    assert p.suffix == ".mf4"
    chans = mdf_channels(p)
    assert chans["wave"] == "V" and "both[1]" in chans
    back = read_mdf(p)
    assert np.allclose(back["wave"], np.interp(back.t, r.t, r["wave"]))
    assert back["both[1]"][-1] == 1.0


def test_groups_with_different_rates(tmp_path: Path) -> None:
    # A logger: engine speed every 10 ms, coolant temperature every 1 s, and a text channel.
    mdf = asammdf.MDF(version="4.10")
    t_fast, t_slow = np.arange(0, 5, 0.01), np.arange(0, 5.5, 1.0)
    mdf.append([asammdf.Signal(800 + 100 * t_fast, t_fast, name="n_engine", unit="rpm")])
    mdf.append([asammdf.Signal(20 + 10 * t_slow, t_slow, name="T_coolant", unit="degC")])
    mdf.append([asammdf.Signal(np.array([b"start", b"stop"]), np.array([0.0, 4.0]), name="event", encoding="utf-8")])
    path = tmp_path / "drive.mf4"
    mdf.save(path)
    rec = read_mdf(path)
    assert set(rec.names()) == {"n_engine", "T_coolant"}  # the text channel is skipped
    assert len(rec.t) == len(t_fast)  # the fastest group's time base
    assert rec["T_coolant"][np.searchsorted(rec.t, 2.5)] == pytest.approx(45.0)  # interpolated
    fixed = read_mdf(path, ["T_coolant"], raster=0.5)
    assert fixed.t[1] - fixed.t[0] == 0.5 and fixed.names() == ["T_coolant"]
    with pytest.raises(NervusError, match="no channel"):
        read_mdf(path, ["nope"])


def test_channel_names_in_several_groups(tmp_path: Path) -> None:
    # A decoded CAN log: every message has its own CRC channel. None of them may be dropped.
    mdf = asammdf.MDF(version="4.10")
    t1, t2 = np.arange(0, 1, 0.01), np.arange(0, 1, 0.1)
    mdf.append([asammdf.Signal(10 * t1, t1, name="rpm"), asammdf.Signal(1 + 0 * t1, t1, name="CRC")])
    mdf.append([asammdf.Signal(5 * t2, t2, name="speed"), asammdf.Signal(2 + 0 * t2, t2, name="CRC")])
    p = tmp_path / "log.mf4"
    mdf.save(p, overwrite=True)
    mdf.close()
    assert list(mdf_channels(p)) == ["rpm", "CRC@0", "CRC@1", "speed"]
    back = read_mdf(p)
    assert set(back.names()) == {"rpm", "CRC@0", "CRC@1", "speed"}
    assert np.all(back["CRC@0"] == 1.0) and np.all(back["CRC@1"] == 2.0)
    assert read_mdf(p, list(mdf_channels(p))).names() == back.names()
    with pytest.raises(NervusError, match="CRC@0, CRC@1"):
        read_mdf(p, ["CRC"])
