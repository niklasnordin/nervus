"""Data Inspector: load an MDF4 measurement file."""

import numpy as np
import pytest

asammdf = pytest.importorskip("asammdf")


def test_load_mdf_into_inspector(window, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mdf = asammdf.MDF(version="4.10")
    t = np.arange(0, 2, 0.01)
    mdf.append([asammdf.Signal(800 + 100 * t, t, name="n_engine", unit="rpm")])
    mdf.save(tmp_path / "log.mf4")
    insp = window.show_inspector()
    assert insp.load_file(str(tmp_path / "log.mf4"))
    run = window.sim_model.runs[-1]
    assert run.label == "log.mf4" and "n_engine" in run.result.names()
    warnings = []
    monkeypatch.setattr("nervus.gui.views.data_inspector.QMessageBox.warning", lambda *a: warnings.append(a))
    (tmp_path / "bad.mf4").write_text("not an MDF file")
    assert not insp.load_file(str(tmp_path / "bad.mf4")) and warnings


def test_load_scanner_csv_and_can_log(window, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "torque.csv").write_text("Device Time,Engine RPM(rpm)\n18-Nov-2020 14:32:01.000,800\n"
                                         "18-Nov-2020 14:32:02.000,900\n", encoding="utf-8")  # fmt: skip
    insp = window.show_inspector()
    assert insp.load_file(str(tmp_path / "torque.csv"))
    run = window.sim_model.runs[-1]
    assert run.label == "torque.csv" and run.result.unit("Engine RPM") == "rpm"
    insp.select(["torque.csv"], ["Engine RPM"])
    assert insp.plot.getAxis("left").labelText == "rpm"
    pytest.importorskip("can")
    (tmp_path / "obd.log").write_text("(1.0) can0 7E8#04410C0C80AAAAAA\n(1.1) can0 7E8#04410C1900AAAAAA\n")
    monkeypatch.setattr("nervus.gui.views.data_inspector.QFileDialog.getOpenFileName", lambda *a, **k: ("", ""))
    assert insp.load_file(str(tmp_path / "obd.log"))  # no DBC file chosen: the OBD-II answers
    assert window.sim_model.runs[-1].result["rpm"][-1] == 1600.0
