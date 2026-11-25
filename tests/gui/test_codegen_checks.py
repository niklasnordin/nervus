"""Code menu checks from the editor: Export FMU and the MISRA check."""

from pathlib import Path

import pytest

from nervus.codegen.sil import find_compiler

pytest.importorskip("fmpy")
EXAMPLES = Path(__file__).parent.parent.parent / "examples"


@pytest.mark.skipif(find_compiler() is None, reason="no C compiler")
def test_export_from_the_editor(window, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fmpy.validation import validate_fmu

    window.files.open(EXAMPLES / "throttle_ecu.nervus")
    window.scene.select_blocks(["ECU"])
    out = window.export_fmu(str(tmp_path / "ecu.fmu"))
    assert out is not None and validate_fmu(str(out)) == []


def test_misra_check_from_the_dialog(window) -> None:  # type: ignore[no-untyped-def]
    from nervus.codegen.misra import cppcheck_path

    if cppcheck_path() is None:
        pytest.skip("needs cppcheck")
    window.files.open(EXAMPLES / "throttle_ecu.nervus")
    window.scene.select_blocks(["ECU"])
    dlg = window.generate_code()
    assert dlg.misra_button.isEnabled()
    dlg.run_misra()
    assert dlg.sil_label.text().startswith("✓ MISRA C:2012: no findings")
    dlg.close()


def test_open_example_menu(window) -> None:  # type: ignore[no-untyped-def]
    """File > Open Example lists the tutorials' models by topic and opens them."""
    menus = [a.menu() for a in window.examples_menu.actions() if a.menu() is not None]
    assert len(menus) >= 6  # the tutorial topics
    actions = [a for m in menus for a in m.actions()]
    first = next(a for a in actions if "cruise_control.nervus" in a.text())
    first.trigger()
    assert window.diagram_model.root.name == "cruise_control"


def test_c_coverage_button(window) -> None:  # type: ignore[no-untyped-def]
    from nervus.codegen.c_coverage import gcov_path

    if gcov_path() is None:
        pytest.skip("needs gcov")
    window.files.open(EXAMPLES / "throttle_ecu.nervus")
    window.scene.select_blocks(["ECU"])
    dlg = window.generate_code()
    path = dlg.run_coverage(open_report=False)
    assert path is not None and path.exists() and "lines 100 %" in dlg.sil_label.text()
    dlg.close()
