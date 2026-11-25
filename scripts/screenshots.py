"""Render screenshots of the editor for the documentation (docs/images/).

Run: QT_QPA_PLATFORM=offscreen uv run python scripts/screenshots.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs" / "images"


def masked_example():  # type: ignore[no-untyped-def]
    """A small model with a masked combustion-torque subsystem (for the mask screenshots)."""
    from nervus.blocks import Constant, Gain, Inport, Outport, Scope, Subsystem
    from nervus.core import Diagram, Variable

    md = Diagram("masked_example")
    inner = Diagram()
    air = inner.add(Inport(port=1), name="air_flow")
    gain = inner.add(Gain(gain="eta * Q_lhv / afr"), name="torque_gain")
    inner.chain(air, gain, inner.add(Outport(port=1), name="torque"))
    sub = Subsystem(system=inner)
    sub.mask.add(Variable("eta", 0.3, "Thermal efficiency", "-"))
    sub.mask.add(Variable("Q_lhv", 44e6, "Fuel heating value", "J/kg"))
    sub.mask.add(Variable("afr", 14.7, "Air-fuel ratio", "-"))
    sub.mask_icon = "Combustion\\ntorque"
    md.add(sub, name="combustion")
    md.chain(md.add(Constant(value=0.003), name="air"), sub, md.add(Scope(), name="scope"))
    return md, sub


def gated_example():  # type: ignore[no-untyped-def]
    """A subsystem with an enable and a rising-edge trigger input (for the docs)."""
    from nervus.blocks import Constant, Counter, Inport, Outport, Pulse, Scope, Subsystem
    from nervus.core import Diagram

    d = Diagram("gated_example")
    inner = Diagram()
    x = inner.add(Inport(port=1), name="tooth")
    cnt = inner.add(Counter(limit=1000, sample_time=0.001), name="count")
    inner.chain(x, cnt, inner.add(Outport(port=1), name="teeth"))
    sub = Subsystem(system=inner, enable_port=True, trigger="rising")
    d.add(sub, name="tooth_counter")
    d.connect(d.add(Constant(value=1.0), name="one"), (sub, 0))
    d.connect(d.add(Constant(value=1.0), name="engine_running"), (sub, 1))
    d.connect(d.add(Pulse(period=0.01, sample_time=0.001), name="crank_sensor"), (sub, 2))
    d.connect(sub, d.add(Scope(), name="scope"))
    return d


def rotated_example():  # type: ignore[no-untyped-def]
    """A sensor chain drawn top to bottom with rotated blocks, feeding a controller on the right."""
    from nervus.blocks import Constant, DiscreteFilter, Gain, Saturation, Scope, Sum, ZeroOrderHold
    from nervus.core import Diagram

    d = Diagram("rotated_example")
    sensor = d.add(Constant(value=2.5), name="sensor_volts")
    adc = d.add(ZeroOrderHold(sample_time=0.01), name="adc")
    scale = d.add(Gain(gain=40.0), name="to_kpa")
    filt = d.add(DiscreteFilter(time_constant=0.05), name="filter")
    d.chain(sensor, adc, scale, filt)
    target = d.add(Constant(value=100.0), name="target_kpa")
    err = d.add(Sum(signs="+-"), name="error")
    sat = d.add(Saturation(upper=1.0, lower=0.0), name="limit")
    d.connect(target, (err, 0))
    d.connect(filt, (err, 1))
    d.chain(err, d.add(Gain(gain=0.02), name="Kp"), sat, d.add(Scope(), name="valve"))
    positions = {"sensor_volts": (0, 0), "adc": (0, 110), "to_kpa": (0, 220), "filter": (0, 330),
                 "target_kpa": (160, 200), "error": (300, 220), "Kp": (430, 220), "limit": (560, 220),
                 "valve": (690, 220)}  # fmt: skip
    for name, (x, y) in positions.items():
        d.block(name).meta["position"] = [float(x), float(y)]
    for name in ("sensor_volts", "adc", "to_kpa", "filter"):
        d.block(name).meta["vertical"] = True
    return d


def circuit_example():  # type: ignore[no-untyped-def]
    """A battery, a switch and a DC motor with its rotor: a starter motor circuit."""
    from nervus.blocks import (
        Battery,
        CurrentSensor,
        DCMotor,
        ElectricalReference,
        ElectricalSwitch,
        Inertia,
        RotationalDamper,
        RotationalReference,
        Scope,
        SpeedSensor,
        Step,
    )  # fmt: skip
    from nervus.core import Diagram

    d = Diagram("starter_circuit")
    bat = d.add(Battery(), name="battery")
    key = d.add(Step(time=0.2), name="key")
    sw = d.add(ElectricalSwitch(), name="starter_relay")
    amp = d.add(CurrentSensor(), name="current")
    motor = d.add(DCMotor(R=0.02, L=1e-4, k=0.02), name="starter_motor")
    rotor = d.add(Inertia(J=0.002), name="rotor")
    friction = d.add(RotationalDamper(d=0.0005), name="friction")
    housing = d.add(RotationalReference(), name="housing")
    speed = d.add(SpeedSensor(), name="speed")
    gnd = d.add(ElectricalReference(), name="ground")
    d.connect(key, sw)
    d.link((bat, "p"), (sw, "p"))
    d.link((sw, "n"), (amp, "p"))
    d.link((amp, "n"), (motor, "p"))
    d.link((motor, "n"), (gnd, "p"))
    d.link((bat, "n"), (gnd, "p"))
    d.link((motor, "shaft"), (rotor, "a"))
    d.link((motor, "shaft"), (friction, "a"))
    d.link((friction, "b"), (housing, "a"))
    d.link((rotor, "a"), (speed, "a"))
    scope = d.add(Scope(num_inputs=2), name="scope")
    d.connect(amp, (scope, 0))
    d.connect(speed, (scope, 1))
    positions = {"battery": (0, 100), "key": (130, -20), "starter_relay": (130, 90), "current": (260, 90),
                 "starter_motor": (400, 90), "ground": (260, 230), "rotor": (560, 60), "friction": (560, 170),
                 "housing": (700, 170), "speed": (560, -40), "scope": (720, 0)}  # fmt: skip
    for name, (x, y) in positions.items():
        d.block(name).meta["position"] = [float(x), float(y)]
    return d


def tasks_example():  # type: ignore[no-untyped-def]
    """An ECU with a 10 ms and a 100 ms task (atomic subsystems with their own sample time)."""
    from nervus.blocks import DiscretePID, Inport, MovingAverage, Outport, Scope, Sine, Subsystem, Sum
    from nervus.core import Diagram

    d = Diagram("tasks_example")
    fast = Diagram()
    rpm = fast.add(Inport(port=1), name="rpm")
    target = fast.add(Inport(port=2), name="target")
    err = fast.add(Sum(signs="-+"), name="error")
    fast.connect(rpm, err.in_port(0))
    fast.connect(target, err.in_port(1))
    fast.chain(
        err,
        fast.add(DiscretePID(P=0.04, I=0.03, sample_time=-1), name="idle_pi"),
        fast.add(Outport(port=1), name="throttle"),
    )
    slow = Diagram()
    temp = slow.add(Inport(port=1), name="coolant_temp")
    slow.chain(
        temp,
        slow.add(MovingAverage(length=10, sample_time=-1), name="filter"),
        slow.add(Outport(port=1), name="temp_filtered"),
    )
    t10 = d.add(Subsystem(system=fast, sample_time=0.01), name="task_10ms")
    t100 = d.add(Subsystem(system=slow, sample_time=0.1), name="task_100ms")
    d.connect(d.add(Sine(amplitude=20.0, bias=800.0), name="engine_rpm"), (t10, 0))
    d.connect(d.add(Sine(amplitude=0.0, bias=800.0), name="target_rpm"), (t10, 1))
    d.chain(d.add(Sine(amplitude=2.0, bias=90.0), name="coolant_sensor"), t100)
    d.connect(t10, d.add(Scope(), name="throttle_cmd"))
    d.connect(t100, d.add(Scope(), name="coolant"))
    for name, (x, y) in {
        "engine_rpm": (0, 0),
        "target_rpm": (0, 110),
        "task_10ms": (200, 40),
        "throttle_cmd": (520, 40),
        "coolant_sensor": (0, 260),
        "task_100ms": (200, 260),
        "coolant": (520, 260),
    }.items():
        d.block(name).meta["position"] = [float(x), float(y)]
    return d


def console_and_can_shots(win) -> None:  # type: ignore[no-untyped-def]
    """The scripting console and the CAN bus trace (also runnable alone: ``screenshots.py console``)."""
    from nervus import simulate

    win.files.open(ROOT / "examples" / "can_network.nervus")
    simulate(win.diagram_model.root, 0.3)
    tr = win.show_can_trace()
    tr.bus_box.setCurrentText("body")
    tr.refresh()
    tr.resize(1100, 420)
    grab(tr, "can-trace.png")
    tr.close()
    win.files.open(ROOT / "examples" / "throttle_pi.nervus")
    win.simulation.run()
    win.simulation.wait()
    QApplication.processEvents()
    win.console_dock.raise_()
    win.resizeDocks([win.console_dock], [260], Qt.Orientation.Vertical)
    con = win.console
    con.output.clear()
    for line in ("[b.name for b in model.blocks][:6]", "model.workspace['Kp']",
                 "print(f\"max plate angle {res['plate_angle'].max():.2f} deg\")",
                 "model.workspace['Kp'] = 1.2", "r = simulate(model)", "r['plate_angle'][-1]"):  # fmt: skip
        con.run(line if not line.startswith("[") else f"print({line})")
    QApplication.processEvents()
    grab(win, "console.png")
    win.editor.undo_stack.undo()


def dictionary_shots(win) -> None:  # type: ignore[no-untyped-def]
    """The Data Dictionary panel with named wires in the throttle example."""
    from nervus.core.dictionary import SignalObject

    win.files.open(ROOT / "examples" / "throttle_pi.nervus")
    root = win.diagram_model.root
    for obj in (SignalObject("throttle_cmd", "double", "%", 0.0, 100.0, "Duty cycle to the H-bridge"),
                SignalObject("plate_angle_meas", "double", "deg", 0.0, 90.0, "Throttle position sensor")):  # fmt: skip
        win.editor.add_signal_object(obj.name, data_type=obj.data_type, unit=obj.unit, min=obj.min, max=obj.max,
                                     description=obj.description)  # fmt: skip
    for block, name in (("throttle_pi", "throttle_cmd"), ("tps_sample", "plate_angle_meas")):
        wire = next(c for c in root.connections if c.src.name == block)
        win.name_signal(wire, name)
    win.view.fit()
    win.dictionary_dock.raise_()
    win.resizeDocks([win.dictionary_dock], [190], Qt.Orientation.Vertical)
    QApplication.processEvents()
    grab(win, "data-dictionary.png")
    for _ in range(4):
        win.editor.undo_stack.undo()


def first_model_shots(win) -> None:  # type: ignore[no-untyped-def]
    """The first tutorial, step by step: blocks, wires, parameters, scope, Data Inspector."""
    import importlib.util

    from PySide6.QtCore import QPointF

    spec = importlib.util.spec_from_file_location("cruise", ROOT / "examples" / "cruise_control.py")
    assert spec is not None and spec.loader is not None
    cruise = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cruise)
    win.files.new()
    ed, model = win.editor, win.diagram_model
    types = {"set_speed": ("sources.step", {"time": 1.0, "final": 100.0}), "error": ("math.sum", {"signs": "+-"}),
             "cruise_control": ("control.pid", {}), "net_force": ("math.sum", {"signs": "+-"}),
             "accel": ("math.gain", {}), "speed": ("continuous.integrator", {}), "drag": ("math.gain", {}),
             "scope": ("sinks.scope", {"num_inputs": 2})}  # fmt: skip
    blocks = {}
    for name, (type_id, params) in types.items():
        x, y = cruise.POSITIONS[name]
        b = ed.add_block(type_id, QPointF(x, y), **params)
        ed.rename(b, name)
        blocks[name] = b
    win.view.fit()
    grab(win, "first-model-1-blocks.png")
    for src, sp, dst, dp in (("set_speed", 0, "error", 0), ("speed", 0, "error", 1), ("error", 0, "cruise_control", 0),
                             ("cruise_control", 0, "net_force", 0), ("drag", 0, "net_force", 1),
                             ("net_force", 0, "accel", 0), ("accel", 0, "speed", 0), ("speed", 0, "drag", 0),
                             ("set_speed", 0, "scope", 0), ("speed", 0, "scope", 1)):  # fmt: skip
        ed.connect_ports(blocks[src], sp, blocks[dst], dp)
    ed.flip_blocks([blocks["drag"]])
    win.view.fit()
    grab(win, "first-model-2-wired.png")
    for name, value, unit in (("m", 1500.0, "kg"), ("c_drag", 8.0, "N/(km/h)"), ("Kp", 300.0, "N/(km/h)"),
                              ("Ki", 0.0, "N/(km/h·s)")):  # fmt: skip
        ed.add_variable(name, value)
        ed.update_variable(name, unit=unit)
    ed.set_params(blocks["cruise_control"], {"P": "Kp", "I": "Ki", "upper": 4000.0, "lower": -6000.0})
    ed.set_params(blocks["accel"], {"gain": "3.6 / m"})
    ed.set_params(blocks["drag"], {"gain": "c_drag"})
    from dataclasses import replace

    ed.set_settings(replace(model.root.settings, t_final=40.0))
    win.scene.select_blocks(["cruise_control"])
    win.workspace_dock.raise_()
    win.resizeDocks([win.workspace_dock], [270], Qt.Orientation.Vertical)
    QApplication.processEvents()
    grab(win, "first-model-3-parameters.png")
    for ki, aw in ((0.0, "none"), (30.0, "none"), (30.0, "clamping")):
        ed.update_variable("Ki", value=ki)
        ed.set_params(blocks["cruise_control"], {"anti_windup": aw})
        win.simulation.run()
        win.simulation.wait()
        for _ in range(10):
            QApplication.processEvents()
        if ki == 0.0:
            scope = win.open_scope(blocks["scope"])
            scope.resize(760, 420)
            grab(scope, "first-model-4-scope.png")
            scope.close()
    inspector = win.show_inspector()
    inspector.resize(1100, 560)
    labels = [r.label for r in win.sim_model.runs[-3:]]
    inspector.select(labels, ["speed"])
    grab(inspector, "first-model-5-inspector.png")
    inspector.close()
    import shutil

    for shot in OUT.glob("first-model-*.png"):  # Tutorial 1 shows its screenshots in the app's help too
        shutil.copy(shot, ROOT / "src" / "nervus" / "gui" / "resources" / "figures" / shot.name)
    ed.undo_stack.setClean()


def requirements_shots(win) -> None:  # type: ignore[no-untyped-def]
    """The Requirements panel on the idle speed example, after running its tests."""
    import copy

    from nervus.core.requirements import Requirement

    win.files.open(ROOT / "examples" / "idle_speed_control.nervus")
    ed, root = win.editor, win.diagram_model.root
    for req in (Requirement("IDLE-01", "Idle speed", "The engine idles at the target speed ± 50 rpm when warm.",
                            "ECU spec 4.2"),
                Requirement("IDLE-02", "Load changes", "Switching on the AC compressor drops the speed by < 100 rpm.",
                            "ECU spec 4.3"),
                Requirement("IDLE-03", "Cold start", "The idle target is raised while the coolant is below 60 °C.",
                            "ECU spec 4.5")):  # fmt: skip
        ed.set_requirement(None, req)
    ed.link_requirements(root.block("ECU"), ["IDLE-01", "IDLE-02", "IDLE-03"])
    tests = copy.deepcopy(root.tests)
    tests[0].requirements = ["IDLE-02"]
    tests[2].requirements = ["IDLE-01"]
    ed.set_tests(tests)
    win.test_panel.run_all()
    win.tests.wait()
    for _ in range(30):
        QApplication.processEvents()
    win.requirements_dock.raise_()
    win.resizeDocks([win.requirements_dock], [220], Qt.Orientation.Vertical)
    QApplication.processEvents()
    grab(win.requirements_panel, "requirements.png")
    ed.undo_stack.setClean()


def chart_shots(win) -> None:  # type: ignore[no-untyped-def]
    """The graphical chart editor, on the engine operating modes."""
    win.files.open(ROOT / "examples" / "engine_modes.nervus")
    block = next(b for _p, b in win.diagram_model.root.walk() if b.type_id == "charts.state_chart")
    ed = win.open_chart_editor(block)
    ed.resize(1280, 760)
    QApplication.processEvents()
    ed.fit()
    ed.state_items["crank"].setSelected(True)
    QApplication.processEvents()
    grab(ed, "chart-editor.png")
    ed.scene.clearSelection()
    tr = next(t for t in ed.transition_items if t.transition.source == "crank" and t.transition.target == "idle")
    tr.setSelected(True)
    QApplication.processEvents()
    grab(ed, "chart-editor-transition.png")
    ed.close()
    for shot in ("chart-editor.png", "chart-editor-transition.png"):  # shown in the app's help too
        shutil.copy(OUT / shot, ROOT / "src" / "nervus" / "gui" / "resources" / "figures" / shot)


def signal_shots(win) -> None:  # type: ignore[no-untyped-def]
    """The Signal Builder's editor: a pedal and a brake signal, two scenarios."""
    from PySide6.QtCore import QPointF

    win.diagram_model.set_diagram(__import__("nervus").Diagram("signals"))
    block = win.editor.add_block("sources.signal_builder", QPointF(0, 0))
    win.editor.set_params(block, {"definition": (
        "group tip_in\n  pedal: 0 0, 1 0, 1 80, 6 80, 6 0, 10 0\n  brake: 0 0, 7 0, 7.5 40, 10 40\n"
        "group panic_stop\n  pedal: 0 30, 2 30, 2 0, 10 0\n  brake: 0 0, 2 0, 2.2 100, 10 100\n")})  # fmt: skip
    ed = win.open_signal_editor(block)
    ed.resize(1200, 640)
    ed.select(0, 2)
    QApplication.processEvents()
    grab(ed, "signal-editor.png")
    shutil.copy(
        OUT / "signal-editor.png", ROOT / "src" / "nervus" / "gui" / "resources" / "figures" / "signal-editor.png"
    )
    ed.close()


def about_shots(win) -> None:  # type: ignore[no-untyped-def]
    """Help > About Nervus, on the Contributors tab."""
    dlg = win.about()
    dlg.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    grab(dlg, "about-contributors.png")
    dlg.close()


def grab(widget: QWidget, name: str) -> None:
    QApplication.processEvents()
    widget.grab().save(str(OUT / name))
    print("wrote", OUT / name)


def main() -> None:
    from nervus.gui.qtfix import unhide_qt_plugins

    unhide_qt_plugins()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from nervus.gui.theme import ThemeManager, ThemeMode

    if sys.argv[1:] in (
        ["console"],
        ["dictionary"],
        ["first"],
        ["requirements"],
        ["chart"],
        ["signals"],
        ["about"],
    ):  # only the newest shots
        from nervus.gui.views.main_window import MainWindow

        ThemeManager.instance().set_mode(ThemeMode.LIGHT)
        win = MainWindow(QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
        win.resize(1400, 860)
        win.show()
        shots = {"console": console_and_can_shots, "dictionary": dictionary_shots, "first": first_model_shots,
                 "requirements": requirements_shots, "chart": chart_shots,
                 "signals": signal_shots, "about": about_shots}  # fmt: skip
        shots[sys.argv[1]](win)
        win.editor.undo_stack.setClean()
        win.close()
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
        ThemeManager.instance().set_mode(mode)
        from nervus.gui.views.main_window import MainWindow

        settings = QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat)
        win = MainWindow(settings)
        win.resize(1400, 860)
        win.show()
        win.files.open(ROOT / "examples" / "throttle_pi.nervus")
        win.view.fit()
        win.scene.select_blocks(["throttle_pi"])
        suffix = "" if mode is ThemeMode.LIGHT else "-dark"
        grab(win, f"editor{suffix}.png")
        if mode is ThemeMode.LIGHT:
            win.workspace_dock.raise_()
            win.resizeDocks([win.workspace_dock], [210], Qt.Orientation.Vertical)
            grab(win, "workspace.png")
            grab(win.workspace_panel, "workspace-panel.png")
            win.diagnostics_dock.raise_()

        win.simulation.run()
        win.simulation.wait()
        for _ in range(20):
            QApplication.processEvents()
        scope = next(iter(win.scopes.values()))
        scope.resize(900, 520)
        grab(scope, f"scope{suffix}.png")

        if mode is ThemeMode.LIGHT:
            # Subsystems: group the ECU part of the throttle model.
            root = win.diagram_model.root
            ecu = win.editor.create_subsystem([root.block(n) for n in ("tps_sample", "error", "throttle_pi")])
            assert ecu is not None
            win.editor.rename(ecu, "ECU")
            win.editor.auto_layout()
            win.view.fit()
            win.scene.select_blocks(["ECU"])
            grab(win, "subsystem-outer.png")
            win.diagram_model.enter(ecu)
            win.editor.auto_layout()
            win.view.fit()
            grab(win, "subsystem-inner.png")
            win.diagram_model.up()
            for _ in range(4):
                win.editor.undo_stack.undo()
            # Code generation dialog for the ECU subsystem of the throttle_ecu example.
            win.files.open(ROOT / "examples" / "throttle_ecu.nervus")
            win.view.fit()
            win.scene.select_blocks(["ECU"])
            dlg = win.generate_code()
            assert dlg is not None
            dlg.resize(1000, 720)
            dlg.tabs.setCurrentIndex(1)
            dlg.run_sil()
            grab(dlg, "codegen.png")
            dlg.close()

            # Test Manager with results, then the Data Inspector comparing two calibrations.
            win.tests_dock.raise_()
            win.resizeDocks([win.tests_dock], [230], Qt.Orientation.Vertical)
            win.test_panel.run_all()
            win.tests.wait()
            for _ in range(30):
                QApplication.processEvents()
            win.test_panel.tree.resizeColumnToContents(0)
            grab(win, "test-manager.png")
            inspector = win.show_inspector()
            inspector.resize(1100, 560)
            inspector.select(["test: tip-in, baseline calibration", "test: tip-in, tight calibration"], ["plate_angle"])
            grab(inspector, "data-inspector.png")
            inspector.close()
            win.diagnostics_dock.raise_()

            # A masked subsystem: parameters in the Properties panel, and the Edit Mask dialog.
            md, sub = masked_example()
            win.diagram_model.set_diagram(md)
            win.scene.select_blocks(["combustion"])
            win.view.fit()
            grab(win, "mask-properties.png")
            from nervus.gui.views.mask_dialog import MaskDialog

            mdlg = MaskDialog(sub, win)
            mdlg.show()
            grab(mdlg, "mask-dialog.png")
            mdlg.close()

            # Right-click menu of a subsystem.
            md4, sub4 = masked_example()
            win.diagram_model.set_diagram(md4)
            win.scene.select_blocks([sub4.name])
            cmenu = win.context_menu(sub4)
            cmenu.popup(win.mapToGlobal(win.rect().center()))
            QApplication.processEvents()
            grab(cmenu, "context-menu.png")
            cmenu.close()

            # Atomic subsystems with their own task period.
            win.diagram_model.set_diagram(tasks_example())
            win.view.fit()
            grab(win.view, "subsystem-tasks.png")
            win.act_rates.setChecked(True)
            win.act_widths.setChecked(True)
            win.refresh_types()
            grab(win, "sample-time-colours.png")
            win.act_rates.setChecked(False)
            win.act_widths.setChecked(False)
            win.refresh_types()

            # Parameter sweep of the throttle controller gain.
            win.files.open(ROOT / "examples" / "throttle_pi.nervus")
            sw = win.show_sweep()
            sw.signal.setCurrentText("plate_angle")
            sw.add_parameter("Kp", "0.5, 1, 1.5, 3")
            sw.add_parameter("Ki", "20, 40")
            sw.stat.setCurrentText("max")
            sw.start()
            sw.wait()
            QApplication.processEvents()
            QApplication.processEvents()
            grab(sw, "sweep-signals.png")
            sw.tabs.setCurrentIndex(1)
            grab(sw, "sweep-metric.png")
            sw.close()

            # The physical examples.
            for model, shot in (
                ("electronic_throttle", "electronic-throttle.png"),
                ("starter_motor", "starter-motor.png"),
            ):
                win.files.open(ROOT / "examples" / f"{model}.nervus")
                win.view.fit()
                grab(win.view, shot)

            # A circuit drawn with physical links.
            win.diagram_model.set_diagram(circuit_example())
            win.view.fit()
            grab(win.view, "circuit.png")

            # Rotated blocks: a vertical sensor chain.
            win.diagram_model.set_diagram(rotated_example())
            win.view.fit()
            grab(win.view, "rotated-blocks.png")

            # Enabled and triggered subsystem.
            gd = gated_example()
            win.diagram_model.set_diagram(gd)
            win.scene.select_blocks(["tooth_counter"])
            win.view.fit()
            grab(win.view, "subsystem-gated.png")

            # Variant subsystem: the Variants dialog.
            md2, sub2 = masked_example()
            sub2.mask_icon = ""
            sub2.active_variant = "petrol"
            sub2.add_variant("diesel", condition="ENGINE == 2")
            sub2.variants["petrol"].condition = "ENGINE == 1"
            md2.workspace["ENGINE"] = 1
            win.diagram_model.set_diagram(md2)
            win.scene.select_blocks(["combustion"])
            win.view.fit()
            grab(win, "variants-properties.png")
            from nervus.gui.views.variant_dialog import VariantDialog

            vdlg = VariantDialog(sub2, win)
            vdlg.show()
            grab(vdlg, "variants-dialog.png")
            vdlg.close()

            # Component library: a library in the panel and a linked copy (dashed outline).
            from nervus.library import instantiate, save_to_library

            lib_dir = Path(tempfile.mkdtemp())
            md3, comp = masked_example()
            lib_path = save_to_library(comp, lib_dir / "engine_parts.nervlib")
            win.library.add_library(lib_path)
            md3.remove("combustion")
            linked = instantiate(lib_path, "combustion")
            md3.add(linked, name="combustion")
            md3.connect(md3.block("air"), linked)
            md3.connect(linked, md3.block("scope"))
            win.diagram_model.set_diagram(md3)
            win.scene.select_blocks(["combustion"])
            win.view.fit()
            tree = win.library.tree
            tree.scrollToBottom()
            grab(win, "library-linked.png")
            win.library.remove_library(lib_path.resolve())

            # Drive cycle example.
            win.files.open(ROOT / "examples" / "drive_cycle.nervus")
            win.view.fit()
            grab(win.view, "drive-cycle-model.png")
            win.diagram_model.enter(win.diagram_model.root.block("vehicle"))
            win.view.fit()
            grab(win.view, "drive-cycle-vehicle.png")
            win.diagram_model.up()

            # Gear-shift example: the model and the TCU.
            win.files.open(ROOT / "examples" / "gear_shift.nervus")
            win.view.fit()
            grab(win.view, "gear-shift-model.png")
            win.diagram_model.enter(win.diagram_model.root.block("tcu"))
            win.view.fit()
            grab(win.view, "gear-shift-tcu.png")
            win.diagram_model.up()
            win.diagram_model.enter(win.diagram_model.root.block("car"))
            win.view.fit()
            grab(win.view, "gear-shift-car.png")
            win.diagram_model.enter(win.diagram_model.diagram.block("engine"))
            win.view.fit()
            grab(win.view, "gear-shift-engine.png")
            win.diagram_model.up()
            win.diagram_model.up()

            # Lambda control example: the plant and the fuel task.
            win.files.open(ROOT / "examples" / "lambda_control.nervus")
            win.view.fit()
            grab(win.view, "lambda-model.png")
            win.diagram_model.enter(win.diagram_model.root.block("ECU"))
            win.view.fit()
            grab(win.view, "lambda-ecu.png")
            win.diagram_model.up()

            # Library example: two linked engines, and the inside of one.
            win.files.open(ROOT / "examples" / "engine_comparison.nervus")
            win.view.fit()
            grab(win.view, "engine-comparison.png")
            win.diagram_model.enter(win.diagram_model.root.block("engine_1_6l"))
            win.view.fit()
            grab(win.view, "engine-component.png")
            win.diagram_model.up()

            # Data types shown on the wires.
            win.files.open(ROOT / "examples" / "fixed_point_sensor.nervus")
            win.act_types.setChecked(True)
            win.refresh_types()
            win.view.fit()
            grab(win.view, "datatypes.png")
            win.act_types.setChecked(False)
            win.refresh_types()

            # Linear analysis of a textbook open loop.
            from nervus import Diagram
            from nervus.blocks import Inport, Outport, TransferFunction

            ol = Diagram("open_loop")
            u = ol.add(Inport(port=1), name="u")
            g = ol.add(TransferFunction(numerator=[10.0], denominator=[0.1, 1.1, 1.0, 0.0]), name="L")
            ol.chain(u, g, ol.add(Outport(port=1), name="y"))
            win.diagram_model.set_diagram(ol)
            adlg = win.show_analysis()
            adlg.resize(980, 680)
            adlg.run()
            grab(adlg, "analysis.png")
            adlg.tabs.setCurrentIndex(2)
            grab(adlg, "analysis-nyquist.png")
            adlg.tabs.setCurrentIndex(3)
            grab(adlg, "analysis-root-locus.png")
            adlg.close()
            win.files.open(ROOT / "examples" / "throttle_pi.nervus")
            adlg = win.show_analysis()
            adlg.resize(980, 680)
            adlg.tabs.setCurrentIndex(4)
            adlg.tune()
            grab(adlg, "analysis-pid-tuning.png")
            adlg.close()
            win.show_help_for_type("control.discrete_pid")
            assert win._help is not None
            grab(win._help, "help.png")
            win.show_help_for_type("math.gain")
            win._help.resize(980, 720)
            grab(win._help, "help-gain.png")
            console_and_can_shots(win)
            dictionary_shots(win)
            first_model_shots(win)
            requirements_shots(win)
            dlg_mod = __import__("nervus.gui.views.settings_dialog", fromlist=["SettingsDialog"])
            dlg = dlg_mod.SettingsDialog(win.diagram_model.diagram.settings, win)
            dlg.show()
            grab(dlg, "settings.png")
            dlg.close()
        win.files.editor.undo_stack.setClean()
        for w in list(win.scopes.values()):
            w.close()
        win.close()


if __name__ == "__main__":
    main()
