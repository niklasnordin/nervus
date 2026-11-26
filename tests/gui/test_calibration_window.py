"""The Calibration window against the virtual ECU of the throttle example."""

from pathlib import Path

import pytest

from nervus.codegen.sil import find_compiler

pytestmark = pytest.mark.skipif(find_compiler() is None, reason="needs a C compiler")


def test_measure_calibrate_and_record(window, qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from nervus import load

    window.diagram_model.set_diagram(load(Path(__file__).parents[2] / "examples" / "throttle_ecu.nervus"))
    cal = window.show_calibration()
    cal._fill_subsystems()
    cal.subsystem.setCurrentIndex(cal.subsystem.findData("ECU"))
    assert cal.connect_ecu()
    assert cal.a2l is not None and "ECU_P.Kp" in cal.a2l.characteristics
    cal.watch(["ECU_B.error", "ECU_B.throttle_pi"])
    assert cal.daq  # 8-byte doubles over UDP fit in a DAQ packet
    cal.record_btn.setChecked(True)
    qtbot.waitUntil(lambda: len(cal.data.get("ECU_B.error", ([], []))[0]) > 5, timeout=3000)
    cal.show_characteristic("ECU_P.Kp")
    assert cal.editor.item(0, 0).text() == "1.5"
    cal.write_value("ECU_P.Kp", 0, 0, 2.5)
    assert cal.editor.item(0, 0).text() == "2.5" and "ECU_P.Kp: 1.5 → 2.5" in cal.log[-1]
    cal.page.setCurrentIndex(1)  # the reference page: the original value
    assert cal.editor.item(0, 0).text() == "1.5"
    warnings = []
    monkeypatch.setattr("nervus.gui.views.calibration.QMessageBox.warning", lambda *a: warnings.append(a))
    cal.write_value("ECU_P.Kp", 0, 0, 3.0)
    assert warnings and "write protected" in str(warnings[-1])
    cal.page.setCurrentIndex(0)
    assert cal.editor.item(0, 0).text() == "2.5"
    cal.editor.item(0, 0).setText("1e13")  # outside the limits of the A2L file: refused
    assert "outside the allowed range" in str(warnings[-1])
    monkeypatch.setattr("nervus.gui.views.calibration.QFileDialog.getSaveFileName", lambda *a, **k: ("", ""))
    cal.record_btn.setChecked(False)
    assert "ECU_B.error" in cal.last_recording.names()
    assert window.sim_model.runs[-1].label.startswith("ECU recording")
    cal.disconnect_ecu()
    assert cal.vecu is None and cal.master is None


def test_board_dialog_creates_and_builds_a_host_project(window, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import shutil

    from nervus import load

    window.diagram_model.set_diagram(load(Path(__file__).parents[2] / "examples" / "throttle_ecu.nervus"))
    dlg = window.show_board_project()
    dlg.board.setCurrentIndex(dlg.board.findData("host"))
    dlg.folder.setText(str(tmp_path / "board"))
    assert dlg.make_project() is not None and (tmp_path / "board" / "main.c").exists()
    if shutil.which("make"):
        assert dlg.build(), dlg.output.toPlainText()
        assert "A2L file with the program's addresses" in dlg.output.toPlainText()
    dlg.close()
