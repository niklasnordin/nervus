"""The main window: assembles views, connects them to the controllers, and owns the menus and toolbar."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nervus import __version__
from nervus.core import Block, Connection, Diagram, Link, NervusError
from nervus.core.expressions import format_value, parse_value
from nervus.gui.controllers import (
    CLIPBOARD_MIME,
    CodegenController,
    EditorController,
    FileController,
    SimulationController,
)
from nervus.gui.controllers.test_controller import TestController
from nervus.gui.models import DiagramModel, SimulationModel, SimulationState
from nervus.gui.theme import ThemeManager, ThemeMode, current_theme
from nervus.gui.views.advisor_dialog import AdvisorDialog
from nervus.gui.views.analysis_dialog import AnalysisDialog
from nervus.gui.views.can_trace_dialog import CanTraceDialog
from nervus.gui.views.canvas import DiagramScene, DiagramView
from nervus.gui.views.chart_editor import ChartEditor
from nervus.gui.views.codegen_dialog import CodegenDialog
from nervus.gui.views.console_panel import ConsolePanel
from nervus.gui.views.data_inspector import DataInspector
from nervus.gui.views.diagnostics import DiagnosticsPanel
from nervus.gui.views.dictionary_panel import DictionaryPanel
from nervus.gui.views.help_browser import HelpBrowser
from nervus.gui.views.icons import icon
from nervus.gui.views.library import LibraryBrowser
from nervus.gui.views.obd_dialog import ObdRecordDialog
from nervus.gui.views.properties import PropertyPanel
from nervus.gui.views.requirements_panel import LinkRequirementsDialog, RequirementsPanel
from nervus.gui.views.scope_window import ScopeWindow
from nervus.gui.views.settings_dialog import SettingsDialog
from nervus.gui.views.signal_editor import SignalEditor
from nervus.gui.views.sweep_dialog import SweepDialog
from nervus.gui.views.test_manager import TestManagerPanel
from nervus.gui.views.tour import EditorTour, editor_stops
from nervus.gui.views.usages_dialog import UsagesDialog
from nervus.gui.views.welcome import WelcomeDialog
from nervus.gui.views.workspace_panel import WorkspacePanel
from nervus.io import FILE_EXTENSION

FILE_FILTER = f"Nervus models (*{FILE_EXTENSION});;Component libraries (*.nervlib);;All files (*)"
OPEN_FILTER = f"Nervus models and libraries (*{FILE_EXTENSION} *.nervlib);;All files (*)"
LIBRARY_FILTER = "Component libraries (*.nervlib)"


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.qsettings = settings or QSettings("Nervus", "Nervus")

        # Models
        self.diagram_model = DiagramModel(parent=self)
        self.sim_model = SimulationModel(self)
        # Controllers
        self.editor = EditorController(self.diagram_model, self)
        self.files = FileController(self.diagram_model, self.editor, self.qsettings, self)
        self.simulation = SimulationController(self.diagram_model, self.sim_model, self.editor, self)
        self.codegen = CodegenController(self.diagram_model, self)
        self.tests = TestController(self.diagram_model, self.sim_model, self.editor, self)
        # Views
        self.scene = DiagramScene(self.diagram_model, self.editor, self)
        self.view = DiagramView(self.scene)
        self.library = LibraryBrowser()
        self.properties = PropertyPanel(self.diagram_model, self.editor)
        self.diagnostics = DiagnosticsPanel(self.diagram_model, self.sim_model)
        self.workspace_panel = WorkspacePanel(self.diagram_model, self.editor)
        self.test_panel = TestManagerPanel(self.diagram_model, self.editor, self.tests)
        self.console = ConsolePanel(self.diagram_model, self.editor, self.sim_model, self)
        self.dictionary_panel = DictionaryPanel(self.diagram_model, self.editor)
        self.requirements_panel = RequirementsPanel(self.diagram_model, self.editor, self.tests)
        self._inspector: DataInspector | None = None
        self.scopes: dict[int, ScopeWindow] = {}
        from PySide6.QtCore import QTimer

        self._types_timer = QTimer(self)
        self._types_timer.setSingleShot(True)
        self._types_timer.setInterval(300)
        self._types_timer.timeout.connect(self.refresh_types)
        self._help: HelpBrowser | None = None
        self._welcome: WelcomeDialog | None = None
        self._tour: EditorTour | None = None

        self.breadcrumb = QWidget()
        self.breadcrumb_layout = QHBoxLayout(self.breadcrumb)
        self.breadcrumb_layout.setContentsMargins(8, 4, 8, 4)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.breadcrumb)
        central_layout.addWidget(self.view, 1)
        self.setCentralWidget(central)
        self._docks()
        self._actions()
        self._menus()
        self._toolbar()
        self._statusbar()
        self._wire()
        self._update_title()
        self._rebuild_breadcrumb()
        self.resize(1280, 800)
        self._restore_layout()
        self._load_libraries()

    # ------------------------------------------------------------------ construction

    def _dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea, name: str) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(area, dock)
        return dock

    def _docks(self) -> None:
        self.library_dock = self._dock("Library", self.library, Qt.DockWidgetArea.LeftDockWidgetArea, "library")
        self.properties_dock = self._dock(
            "Properties", self.properties, Qt.DockWidgetArea.RightDockWidgetArea, "properties"
        )
        self.diagnostics_dock = self._dock(
            "Diagnostics", self.diagnostics, Qt.DockWidgetArea.BottomDockWidgetArea, "diagnostics"
        )
        self.workspace_dock = self._dock(
            "Model Workspace", self.workspace_panel, Qt.DockWidgetArea.BottomDockWidgetArea, "workspace"
        )
        self.tests_dock = self._dock("Test Manager", self.test_panel, Qt.DockWidgetArea.BottomDockWidgetArea, "tests")
        self.console_dock = self._dock("Console", self.console, Qt.DockWidgetArea.BottomDockWidgetArea, "console")
        self.dictionary_dock = self._dock(
            "Data Dictionary", self.dictionary_panel, Qt.DockWidgetArea.BottomDockWidgetArea, "dictionary"
        )
        self.tabifyDockWidget(self.diagnostics_dock, self.workspace_dock)
        self.tabifyDockWidget(self.workspace_dock, self.tests_dock)
        self.requirements_dock = self._dock(
            "Requirements", self.requirements_panel, Qt.DockWidgetArea.BottomDockWidgetArea, "requirements"
        )
        self.tabifyDockWidget(self.workspace_dock, self.dictionary_dock)
        self.tabifyDockWidget(self.tests_dock, self.requirements_dock)
        self.tabifyDockWidget(self.tests_dock, self.console_dock)
        self.diagnostics_dock.raise_()
        self.resizeDocks([self.library_dock, self.properties_dock], [230, 300], Qt.Orientation.Horizontal)
        self.resizeDocks([self.diagnostics_dock], [130], Qt.Orientation.Vertical)

    def _action(
        self,
        text: str,
        slot: Callable[[], object],
        shortcut: QKeySequence | QKeySequence.StandardKey | Qt.Key | str | None = None,
        tip: str = "",
        icon_name: str | None = None,
    ) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(
                f"{tip} ({a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})" if shortcut else tip
            )
        if icon_name:
            a.setIcon(icon(icon_name))
            a.setProperty("icon_name", icon_name)
        # Drop the 'checked' argument of triggered(): several slots take an optional path, which it would fill.
        a.triggered.connect(lambda _checked=False, s=slot: s())
        return a

    def _actions(self) -> None:
        K = QKeySequence.StandardKey
        self.act_new = self._action("&New", self.new_file, K.New, "New model", "new")
        self.act_open = self._action("&Open…", self.open_file, K.Open, "Open a model", "open")
        self.act_save = self._action("&Save", self.save_file, K.Save, "Save the model", "save")
        self.act_save_as = self._action("Save &As…", self.save_file_as, K.SaveAs, "Save under a new name")
        self.act_import_simulink = self._action(
            "Import Simulink Model…",
            self.import_simulink,
            None,
            "Open a Simulink .slx model (the common blocks are translated; a report lists the rest)",
        )
        self.act_import_spice = self._action(
            "Import SPICE Netlist…", self.import_spice, None, "Open a .cir/.net netlist (e.g. from LTspice)"
        )
        self.act_export_spice = self._action(
            "Export SPICE Netlist…", self.export_spice, None, "Write the circuit of this model as a netlist"
        )
        self.act_quit = self._action("&Quit", self.close, K.Quit)
        self.act_undo = self.editor.undo_stack.createUndoAction(self, "&Undo")
        self.act_undo.setShortcut(K.Undo)
        self.act_undo.setIcon(icon("undo"))
        self.act_undo.setProperty("icon_name", "undo")
        self.act_redo = self.editor.undo_stack.createRedoAction(self, "&Redo")
        self.act_redo.setShortcuts([QKeySequence(K.Redo), QKeySequence("Ctrl+Y")])
        self.act_redo.setIcon(icon("redo"))
        self.act_redo.setProperty("icon_name", "redo")
        self.act_cut = self._action("Cu&t", self.cut, K.Cut)
        self.act_copy = self._action("&Copy", self.copy, K.Copy)
        self.act_paste = self._action("&Paste", self.paste, K.Paste)
        self.act_delete = self._action("&Delete", self.delete_selection, None, "Delete selection")
        self.act_delete.setShortcuts([QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)])
        self.act_select_all = self._action("Select &All", self.select_all, K.SelectAll)
        self.act_create_subsystem = self._action(
            "Create &Subsystem from Selection", self.create_subsystem, "Ctrl+G", "Group the selected blocks"
        )
        self.act_add_library = self._action(
            "Add Component &Library…",
            self.add_library,
            None,
            "Show the components of a .nervlib file in the Library panel",
        )
        self.act_refresh_links = self._action(
            "&Update Library Links", self.refresh_links, None, "Update linked subsystems from their libraries"
        )
        self.act_save_to_library = self._action(
            "Save Subsystem to Li&brary…",
            self.save_to_library,
            None,
            "Add the selected subsystem to a component library",
        )
        self.act_flip = self._action(
            "&Flip Block", self.flip_selection, "Ctrl+Shift+F", "Swap the inputs and outputs sides (for feedback paths)"
        )
        self.act_rotate = self._action(
            "&Rotate Block", self.rotate_selection, "Ctrl+Shift+R", "Turn blocks a quarter turn (vertical signal flow)"
        )
        self.act_reset_size = self._action(
            "Reset Block &Size", self.reset_size_selection, None, "Back to the natural size after resizing"
        )
        self.act_types = self._action("Show &Data Types", self.refresh_types, None, "Label wires with their data type")
        self.act_types.setCheckable(True)
        self.act_widths = self._action(
            "Show Signal &Widths", self.refresh_types, None, "Label vector wires with their width and draw them thicker"
        )
        self.act_widths.setCheckable(True)
        self.act_rates = self._action(
            "Sample &Time Colours", self.refresh_types, None, "Colour blocks and wires by their sample time"
        )
        self.act_rates.setCheckable(True)
        self.act_up = self._action("&Up to Parent", self.diagram_model.up, "Ctrl+Up", "Leave this subsystem")
        self.act_layout = self._action(
            "Auto &Layout", self.auto_layout, "Ctrl+Shift+L", "Arrange the blocks of this diagram left to right"
        )
        self.act_zoom_in = self._action("Zoom &In", self.view.zoom_in, K.ZoomIn)
        self.act_zoom_out = self._action("Zoom &Out", self.view.zoom_out, K.ZoomOut)
        self.act_fit = self._action("&Fit to Window", self.view.fit, "Ctrl+0", "Show the whole diagram", "fit")
        self.act_run = self._action("&Run", self.run_simulation, "Ctrl+R", "Run the simulation", "run")
        self.act_run.setShortcuts([QKeySequence("Ctrl+R"), QKeySequence(Qt.Key.Key_F5)])
        self.act_stop = self._action("&Stop", self.simulation.stop, "Ctrl+.", "Stop the simulation", "stop")
        self.act_stop.setEnabled(False)
        self.act_inspector = self._action(
            "&Data Inspector", self.show_inspector, "Ctrl+I", "Compare signals from several runs"
        )
        self.act_run_tests = self._action("Run All &Tests", self.test_panel.run_all, "Ctrl+T", "Run every test case")
        self.act_settings = self._action(
            "Simulation &Settings…", self.edit_settings, "Ctrl+E", "Solver and time span", "settings"
        )
        self.act_codegen = self._action(
            "Generate &C Code…",
            self.generate_code,
            "Ctrl+B",
            "Generate C code for the selected subsystem, or for the whole model",
        )
        self.act_export_fmu = self._action(
            "Export &FMU…",
            self.export_fmu,
            None,
            "Export the selected subsystem, or the whole model, as an FMI 2.0 co-simulation FMU",
        )
        self.act_obd = self._action(
            "Record from &OBD-II…", self.show_obd, None, "Read live data from a car through its OBD-II port"
        )
        self.act_can_trace = self._action(
            "CAN Bus &Trace…", self.show_can_trace, None, "Frames on the simulated CAN buses of the last run"
        )
        self.act_sweep = self._action(
            "Parameter &Sweep / Monte Carlo…",
            self.show_sweep,
            "Ctrl+Shift+W",
            "Run the model many times with different workspace values",
        )
        self.act_analysis = self._action(
            "&Linear Analysis…", self.show_analysis, "Ctrl+L", "Linearize the model; Bode plot, margins, step response"
        )
        self.act_advisor = self._action(
            "Model &Advisor…", self.show_advisor, "Ctrl+Shift+A", "Check the model for risky modelling practices"
        )
        self.act_help = self._action("Nervus &Help", self.show_help, "Ctrl+?", "Open the help", "help")
        self.act_welcome = self._action(
            "&Welcome…", self.show_welcome, None, "Start here: the first tutorial, examples and recent models"
        )
        self.act_tour = self._action("Editor &Tour", self.start_tour, None, "A one-minute tour of the editor")
        self.act_block_help = self._action("Help for Selected &Block", self.show_block_help, Qt.Key.Key_F1)
        self.act_about = self._action("&About Nervus", self.about)

    def _menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addActions([self.act_new, self.act_open])
        self.examples_menu = m.addMenu("Open &Example")
        self._build_examples_menu()
        self.recent_menu = m.addMenu("Open &Recent")
        m.addSeparator()
        m.addActions([self.act_save, self.act_save_as])
        m.addSeparator()
        m.addActions([self.act_import_simulink, self.act_import_spice, self.act_export_spice])
        m.addSeparator()
        m.addActions([self.act_add_library, self.act_refresh_links])
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&Edit")
        m.addActions([self.act_undo, self.act_redo])
        m.addSeparator()
        m.addActions([self.act_cut, self.act_copy, self.act_paste, self.act_delete])
        m.addSeparator()
        m.addAction(self.act_select_all)
        m.addSeparator()
        m.addAction(self.act_create_subsystem)
        m.addAction(self.act_flip)
        m.addAction(self.act_rotate)
        m.addAction(self.act_reset_size)
        m.addAction(self.act_save_to_library)
        m.addAction(self.act_layout)
        m = mb.addMenu("&View")
        m.addActions([self.act_zoom_in, self.act_zoom_out, self.act_fit])
        m.addAction(self.act_up)
        m.addAction(self.act_types)
        m.addAction(self.act_widths)
        m.addAction(self.act_rates)
        m.addSeparator()
        for dock in (
            self.library_dock,
            self.properties_dock,
            self.diagnostics_dock,
            self.workspace_dock,
            self.dictionary_dock,
            self.tests_dock,
            self.requirements_dock,
            self.console_dock,
        ):
            m.addAction(dock.toggleViewAction())
        m.addSeparator()
        theme_menu = m.addMenu("&Theme")
        for mode, label in ((ThemeMode.SYSTEM, "Follow System"), (ThemeMode.LIGHT, "Light"), (ThemeMode.DARK, "Dark")):
            a = theme_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(ThemeManager.instance().mode is mode)
            a.triggered.connect(lambda _c=False, md=mode: self.set_theme(md))
            a.setData(mode.value)
        self.theme_menu = theme_menu
        m = mb.addMenu("&Simulation")
        m.addActions([self.act_run, self.act_stop])
        m.addSeparator()
        m.addActions([self.act_run_tests, self.act_inspector])
        m.addSeparator()
        m.addAction(self.act_settings)
        m = mb.addMenu("&Analysis")
        m.addAction(self.act_analysis)
        m.addAction(self.act_sweep)
        m.addAction(self.act_advisor)
        m = mb.addMenu("&Tools")
        m.addAction(self.act_obd)
        m.addAction(self.act_can_trace)
        m = mb.addMenu("&Code")
        m.addAction(self.act_codegen)
        m.addAction(self.act_export_fmu)
        m = mb.addMenu("&Help")
        m.addActions([self.act_welcome, self.act_tour])
        m.addSeparator()
        m.addActions([self.act_help, self.act_block_help])
        m.addSeparator()
        m.addAction(self.act_about)
        self._rebuild_recent(self.files.recent_files())

    def _toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(tb)
        tb.addActions([self.act_new, self.act_open, self.act_save])
        tb.addSeparator()
        tb.addActions([self.act_undo, self.act_redo])
        tb.addSeparator()
        tb.addAction(self.act_fit)
        tb.addSeparator()
        tb.addAction(self.act_run)
        tb.addAction(self.act_stop)
        stop_label = QLabel("  Stop time ")
        stop_label.setObjectName("muted")
        tb.addWidget(stop_label)
        self.stop_time = QLineEdit()
        self.stop_time.setFixedWidth(80)
        self.stop_time.setToolTip("Simulation stop time in seconds")
        self.stop_time.editingFinished.connect(self._stop_time_edited)
        tb.addWidget(self.stop_time)
        self.solver_label = QLabel()
        self.solver_label.setObjectName("muted")
        tb.addWidget(self.solver_label)
        tb.addAction(self.act_settings)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding, spacer.sizePolicy().verticalPolicy())
        tb.addWidget(spacer)
        tb.addAction(self.act_help)
        self.toolbar = tb
        self._sync_settings_widgets()

    def _statusbar(self) -> None:
        sb = self.statusBar()
        self.status_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(180)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.rates_legend = QLabel()
        self.rates_legend.setTextFormat(Qt.TextFormat.RichText)
        self.rates_legend.hide()
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("muted")
        sb.addWidget(self.status_label, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.rates_legend)
        sb.addPermanentWidget(self.zoom_label)

    def _wire(self) -> None:
        self.library.addRequested.connect(self._add_from_library)
        self.library.librariesChanged.connect(self._store_libraries)
        self.library.openLibraryRequested.connect(self._open_path)
        self.files.saved.connect(self._saved)
        self.library.helpRequested.connect(self.show_help_for_type)
        self.properties.helpRequested.connect(self.show_help_for_type)
        self.properties.editMaskRequested.connect(self.edit_mask)
        self.properties.editVariantsRequested.connect(self.edit_variants)
        self.scene.selectedBlocksChanged.connect(self.properties.show_blocks)
        self.scene.blockActivated.connect(self._block_activated)
        self.scene.contextMenuRequested.connect(self._show_context_menu)
        self.diagnostics.blocksActivated.connect(self._highlight)
        self.diagnostics.errorBlocksChanged.connect(self.scene.mark_errors)
        self.editor.errorOccurred.connect(self._editor_error)
        self.editor.undo_stack.cleanChanged.connect(lambda _c: self._update_title())
        self.diagram_model.filePathChanged.connect(lambda _p: self._update_title())
        self.diagram_model.settingsChanged.connect(self._sync_settings_widgets)
        self.diagram_model.diagramReset.connect(self._on_reset)
        self.diagram_model.currentChanged.connect(self._on_current_changed)
        self.diagram_model.changed.connect(lambda: self._annotations_on() and self._types_timer.start())
        self.diagram_model.blockRenamed.connect(self._on_renamed)
        self.diagram_model.nameChanged.connect(lambda _n: self._rebuild_breadcrumb())
        self.diagram_model.blockRemoved.connect(self._close_scope_of)
        self.files.errorOccurred.connect(lambda msg: QMessageBox.warning(self, "Nervus", msg))
        self.codegen.errorOccurred.connect(self._codegen_error)
        self.files.recentFilesChanged.connect(self._rebuild_recent)
        self.workspace_panel.statusMessage.connect(self.status_label.setText)
        self.workspace_panel.usagesRequested.connect(self.show_usages)
        self.test_panel.statusMessage.connect(self.status_label.setText)
        self.console.statusMessage.connect(self.status_label.setText)
        self.dictionary_panel.statusMessage.connect(self.status_label.setText)
        self.requirements_panel.statusMessage.connect(self.status_label.setText)
        self.requirements_panel.blockActivated.connect(lambda path: self._highlight((path,)))
        self.dictionary_panel.blockActivated.connect(lambda path: self._highlight((path,)))
        self.test_panel.openResult.connect(self._open_test_result)
        self.sim_model.stateChanged.connect(self._sim_state_changed)
        self.sim_model.progressChanged.connect(lambda f: self.progress.setValue(int(f * 1000)))
        self.view.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        ThemeManager.instance().themeChanged.connect(self._refresh_icons)

    def show_inspector(self) -> DataInspector:
        if self._inspector is None:
            self._inspector = DataInspector(self.sim_model, self)
        self._inspector.show()
        self._inspector.raise_()
        return self._inspector

    def show_advisor(self) -> AdvisorDialog:
        dlg = AdvisorDialog(self.diagram_model.root, self)
        dlg.blocksActivated.connect(self._highlight)
        dlg.show()
        return dlg

    def show_obd(self) -> ObdRecordDialog:
        dlg = ObdRecordDialog(self.sim_model, self)
        dlg.show()
        return dlg

    def show_can_trace(self) -> CanTraceDialog:
        dlg = CanTraceDialog(self.diagram_model.root, self)
        dlg.show()
        return dlg

    def show_sweep(self) -> SweepDialog:
        dlg = SweepDialog(self.diagram_model.root, self.editor, self)
        dlg.show()
        return dlg

    def show_analysis(self) -> AnalysisDialog:
        dlg = AnalysisDialog(self.diagram_model.root, self, self.editor)
        dlg.show()
        return dlg

    def _annotations_on(self) -> bool:
        return self.act_types.isChecked() or self.act_widths.isChecked() or self.act_rates.isChecked()

    def refresh_types(self) -> None:
        """Compile the model and annotate the canvas: data types and widths on wires, sample-time colours
        (View → Show Data Types / Show Signal Widths / Sample Time Colours)."""
        self.rates_legend.setVisible(self.act_rates.isChecked())
        if not self._annotations_on():
            self.scene.set_type_labels({})
            return
        from nervus.gui.controllers.annotations import signal_annotations

        try:
            ann = signal_annotations(
                self.diagram_model,
                types=self.act_types.isChecked(),
                widths=self.act_widths.isChecked(),
                rates=self.act_rates.isChecked(),
            )
        except NervusError:
            self.status_label.setText("Annotations are shown once the model compiles (see Diagnostics).")
            return
        legend = sample_time_colors(ann.rates)
        legend["continuous"] = current_theme().wire  # the normal wire colour
        colors = {path: QColor(legend[key]) for path, key in ann.rate_of.items() if key in legend}
        self._colour_subsystems(colors)
        self.scene.set_type_labels(ann.labels, ann.wide, colors)
        self.rates_legend.setText(
            "  ".join(f'<span style="color:{legend[key]}">■</span> {name}' for name, key in ann.legend) + "  "
        )

    def _colour_subsystems(self, colors: dict[str, QColor]) -> None:
        """A subsystem whose blocks all run at one rate gets that colour; with several rates, none."""
        by_parent: dict[str, set[str]] = {}
        for path, c in colors.items():
            parts = path.split("/")
            for k in range(1, len(parts)):
                by_parent.setdefault("/".join(parts[:k]), set()).add(c.name())
        for parent, names in by_parent.items():
            if len(names) == 1 and parent not in colors:
                colors[parent] = QColor(next(iter(names)))

    def _open_test_result(self, name: str) -> None:
        res = self.tests.results.get(name)
        if res is None or res.result is None:
            self.status_label.setText(f"Run '{name}' first.")
            return
        inspector = self.show_inspector()
        signals = sorted({c.signal for c in res.case.checks})
        inspector.select([f"test: {name}"], signals)

    # ------------------------------------------------------------------ code generation

    def generate_code(self) -> CodegenDialog | None:
        target = self.codegen.target_for(self.scene.selected_blocks())
        code = self.codegen.generate(target)
        if code is None:
            return None
        dlg = CodegenDialog(self.codegen, target, code, self)
        dlg.show()
        self.status_label.setText(f"Generated C code for {code.name}")
        return dlg

    def export_fmu(self, path: str | None = None) -> Path | None:
        target = self.codegen.target_for(self.scene.selected_blocks())
        if path is None:
            name = getattr(target, "name", "") or self.diagram_model.root.name
            path, _ = QFileDialog.getSaveFileName(self, "Export FMU", f"{name}.fmu", "FMU (*.fmu)")
            if not path:
                return None
        out = self.codegen.export_fmu(target, path)
        if out is not None:
            self.status_label.setText(f"Exported {out.name} (FMI 2.0 co-simulation)")
        return out

    def _codegen_error(self, message: str, blocks: tuple[str, ...]) -> None:
        self.sim_model.set_error(message, blocks)
        self.status_label.setText("Code generation failed — see Diagnostics.")
        self.diagnostics_dock.show()
        self.diagnostics_dock.raise_()

    # ------------------------------------------------------------------ helpers

    def _update_title(self) -> None:
        self.setWindowTitle(f"{self.diagram_model.title}[*] — Nervus")
        self.setWindowModified(self.files.is_modified)

    def _rebuild_breadcrumb(self) -> None:
        while self.breadcrumb_layout.count():
            item = self.breadcrumb_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        crumbs = self.diagram_model.breadcrumbs()
        for k, (label, diagram) in enumerate(crumbs):
            if k:
                sep = QLabel("›")
                sep.setObjectName("muted")
                self.breadcrumb_layout.addWidget(sep)
            button = QToolButton()
            button.setText(label)
            button.setEnabled(k < len(crumbs) - 1)
            button.clicked.connect(lambda _c=False, d=diagram: self.diagram_model.show(d))
            self.breadcrumb_layout.addWidget(button)
        self.breadcrumb_layout.addStretch(1)
        self.act_up.setEnabled(len(crumbs) > 1)

    def _on_current_changed(self, _diagram: object) -> None:
        self._rebuild_breadcrumb()
        self.properties.show_blocks([])
        self.view.fit()
        if self._annotations_on():
            self.refresh_types()

    def _on_renamed(self, block: Block, _old: str) -> None:
        if getattr(block, "system", None) is not None:
            self._rebuild_breadcrumb()

    def _sync_settings_widgets(self) -> None:
        s = self.diagram_model.root.settings
        self.stop_time.setText(format_value(s.t_final))
        self.solver_label.setText(f"  {s.solver}" + (f", h={format_value(s.step)}" if s.step else "") + "  ")

    def _stop_time_edited(self) -> None:
        s = self.diagram_model.root.settings
        try:
            value = parse_value(self.stop_time.text())
            if not isinstance(value, float):
                raise ValueError
        except ValueError:
            self._sync_settings_widgets()
            self.status_label.setText("Stop time must be a number.")
            return
        if value != s.t_final:
            new = type(s)(**{**s.to_dict(), "t_final": value})
            if not self.editor.set_settings(new):
                self._sync_settings_widgets()

    def _refresh_icons(self, _theme: object = None) -> None:
        for a in self.findChildren(QAction):
            name = a.property("icon_name")
            if name:
                a.setIcon(icon(name))

    def _rebuild_recent(self, files: list[Path]) -> None:
        self.recent_menu.clear()
        for p in files:
            a = self.recent_menu.addAction(p.name)
            a.setToolTip(str(p))
            a.triggered.connect(lambda _c=False, path=p: self._open_path(path))
        if files:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear Menu", self.files.clear_recent)
        self.recent_menu.setEnabled(bool(files))

    def _on_reset(self) -> None:
        for w in self.scopes.values():
            w.close()
            w.deleteLater()  # else it stays connected to the results and redraws for a model that is gone
        self.scopes.clear()
        warnings = self.diagram_model.root.link_warnings
        if warnings:
            self.status_label.setText(
                f"Library links: {warnings[0]}" + (f" (+{len(warnings) - 1} more)" if len(warnings) > 1 else "")
            )
        self._sync_settings_widgets()
        self._rebuild_breadcrumb()
        self.view.fit()

    def _editor_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.properties.show_controller_error(message)

    def show_usages(self, name: str) -> UsagesDialog:
        """Where a workspace variable is used (Model Workspace → Find Usages)."""
        dlg = UsagesDialog(self.diagram_model.root, name, self)
        dlg.blockActivated.connect(lambda path: self._highlight((path,)))
        dlg.show()
        return dlg

    def _highlight(self, names: tuple[str, ...]) -> None:
        # Open the subsystem that contains the first block, then select it.
        if names and "/" in names[0]:
            target = self._diagram_of_path(names[0])
            if target is not None:
                self.diagram_model.show(target)
        elif names:
            self.diagram_model.show(self.diagram_model.root)
        self.scene.select_blocks(names)
        items = [i for i in self.scene.blocks.values() if i.block.name in names]
        if items:
            self.view.centerOn(items[0])

    # ------------------------------------------------------------------ file actions

    def _confirm_discard(self) -> bool:
        if not self.files.is_modified:
            return True
        r = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to {self.diagram_model.title}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if r == QMessageBox.StandardButton.Save:
            return self.save_file()
        return r == QMessageBox.StandardButton.Discard

    def new_file(self) -> None:
        if self._confirm_discard():
            self.files.new()

    def _build_examples_menu(self) -> None:
        """The example models, grouped like the tutorials (each tutorial's models under its topic)."""
        from nervus.examples import examples_dir
        from nervus.tutorials import CATEGORIES, ordered

        folder = examples_dir()
        tutorials = ordered()
        for category, _desc, _subs in CATEGORIES:
            items = [(t, m) for t in tutorials if t.category == category for m in t.models if (folder / m).exists()]
            if not items:
                continue
            sub = self.examples_menu.addMenu(category)
            for t, model in items:
                act = sub.addAction(f"{t.title} ({model})")
                act.setToolTip(f"Open examples/{model}, the model of the tutorial {t.title!r}")
                act.triggered.connect(lambda _=False, p=folder / model: self.open_example(p))
        self.examples_menu.addSeparator()
        self.examples_menu.addAction("All examples…", self._open_example_dialog)

    def open_example(self, path: Path) -> None:
        if not self._confirm_discard():
            return
        self.files.open(path)
        if "site-packages" in path.parts or "dist-packages" in path.parts:
            self.status_label.setText(
                "An example from the installed Nervus: use File ▸ Save As to keep your changes in a folder of your own"
            )

    def _open_example_dialog(self) -> None:
        from nervus.examples import examples_dir

        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open example", str(examples_dir()), OPEN_FILTER)
        if path:
            self.files.open(path)

    def open_file(self) -> None:
        if not self._confirm_discard():
            return
        start = str(self.diagram_model.file_path.parent) if self.diagram_model.file_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open model", start, OPEN_FILTER)
        if path:
            self.files.open(path)

    def _open_path(self, path: Path) -> None:
        if self._confirm_discard():
            self.files.open(path)

    def save_file(self) -> bool:
        if self.files.save():
            self.status_label.setText(f"Saved {self.diagram_model.title}")
            return True
        return self.save_file_as() if self.diagram_model.file_path is None else False

    def import_simulink(self, path: str | None = None, show_report: bool = True) -> bool:
        """Open a Simulink .slx model as a new (unsaved) model, and show what was not translated."""
        if path is None:
            if not self._confirm_discard():
                return False
            path, _ = QFileDialog.getOpenFileName(self, "Import Simulink model", "", "Simulink models (*.slx)")
            if not path:
                return False
        model = self.files.import_simulink(path)
        if model is None:
            return False
        report = str(model.meta.get("simulink_report", ""))
        self.status_label.setText(f"Imported {Path(path).name}: {report.splitlines()[0] if report else ''}")
        if show_report and "\n" in report:
            QMessageBox.information(self, "Simulink import", report)
        return True

    def import_spice(self, path: str | None = None) -> bool:
        """Open a SPICE netlist as a new (unsaved) model."""
        if path is None:
            if not self._confirm_discard():
                return False
            path, _ = QFileDialog.getOpenFileName(
                self, "Import SPICE netlist", "", "SPICE netlists (*.cir *.net *.sp *.txt);;All files (*)"
            )
            if not path:
                return False
        model = self.files.import_spice(path)
        if model is None:
            return False
        skipped = model.meta.get("spice_skipped")
        self.status_label.setText(
            f"Imported {Path(path).name}" + (f" — not supported, left out: {', '.join(skipped)}" if skipped else "")
        )
        return True

    def export_spice(self, path: str | None = None) -> bool:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export SPICE netlist", "circuit.cir", "SPICE netlists (*.cir)")
            if not path:
                return False
        if not self.files.export_spice(path):
            return False
        self.status_label.setText(f"Wrote {path}")
        return True

    def save_file_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save model", self.diagram_model.title, FILE_FILTER)
        if not path:
            return False
        ok = self.files.save_as(path)
        if ok:
            self.status_label.setText(f"Saved {self.diagram_model.title}")
        return ok

    # ------------------------------------------------------------------ edit actions

    # ------------------------------------------------------------------ component libraries

    def _load_libraries(self) -> None:
        stored = self.qsettings.value("libraries", [])
        for p in stored if isinstance(stored, list) else [stored] if stored else []:
            self.library.add_library(str(p))

    def _store_libraries(self, paths: list[Path]) -> None:
        self.qsettings.setValue("libraries", [str(p) for p in paths])

    def add_library(self) -> None:
        start = str(self.diagram_model.file_path.parent) if self.diagram_model.file_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Add component library", start, LIBRARY_FILTER)
        if path:
            error = self.library.add_library(path)
            self.status_label.setText(error or f"Added library {Path(path).name}")

    def refresh_links(self) -> None:
        from nervus.library import linked_subsystems

        if not linked_subsystems(self.diagram_model.root):
            self.status_label.setText("This model has no linked library subsystems.")
            return
        warnings = self.editor.refresh_links()  # one undo step; the model stays modified until saved
        if warnings is not None:
            self.status_label.setText(warnings[0] if warnings else "Library links updated.")

    def save_to_library(self) -> None:
        blocks = [b for b in self.scene.selected_blocks() if hasattr(b, "system")]
        if len(blocks) != 1:
            self.status_label.setText("Select one subsystem to save it to a library.")
            return
        start = str(self.diagram_model.file_path.parent) if self.diagram_model.file_path else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save to component library", start, LIBRARY_FILTER, options=QFileDialog.Option.DontConfirmOverwrite
        )
        if path and self.editor.save_to_library(blocks[0], path):
            lib = Path(path) if Path(path).suffix else Path(path).with_suffix(".nervlib")
            self.library.add_library(lib)
            self.status_label.setText(f"Saved {blocks[0].name} to {lib.name} and linked it")

    def _saved(self, path: Path) -> None:
        if path.suffix == ".nervlib":
            self.library.reload_libraries()  # an edited library: show its new components

    def _add_from_library(self, type_id: str) -> None:
        pos = self.view.view_center_scene() - QPointF(35, 23)
        # Offset successive blocks so they don't stack exactly on top of each other.
        n = len(self.diagram_model.diagram)
        pos += QPointF((n % 5) * 20, (n % 5) * 20)
        block = self.editor.add_block(type_id, pos)
        if block is not None:
            self.scene.select_blocks([block.name])

    def delete_selection(self) -> None:
        if self.view.hasFocus() or self.scene.selectedItems():
            self.editor.delete(
                self.scene.selected_blocks(), [*self.scene.selected_connections(), *self.scene.selected_links()]
            )

    def copy(self) -> None:
        blocks = self.scene.selected_blocks()
        if blocks:
            from PySide6.QtCore import QMimeData

            mime = QMimeData()
            text = self.editor.copy_to_text(blocks)
            mime.setData(CLIPBOARD_MIME, text.encode())
            mime.setText(text)
            QGuiApplication.clipboard().setMimeData(mime)

    def cut(self) -> None:
        self.copy()
        self.editor.delete(self.scene.selected_blocks(), [])

    def paste(self) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None or not mime.hasFormat(CLIPBOARD_MIME):
            return
        created = self.editor.paste_from_text(bytes(mime.data(CLIPBOARD_MIME).data()).decode())
        self.scene.select_blocks([b.name for b in created])

    def _diagram_of_path(self, path: str) -> Diagram | None:
        d: Diagram = self.diagram_model.root
        for part in path.split("/")[:-1]:
            if part not in d:
                return None
            system = getattr(d.block(part), "system", None)
            if not isinstance(system, Diagram):
                return None
            d = system
        return d

    def edit_mask(self, subsystem: Block) -> None:
        from nervus.gui.views.mask_dialog import MaskDialog

        dlg = MaskDialog(subsystem, self)
        if dlg.exec():
            self.editor.set_mask(subsystem, dlg.variables(), dlg.icon.text(), dlg.description.text())

    def edit_variants(self, subsystem: Block) -> None:
        from nervus.gui.views.variant_dialog import VariantDialog

        dlg = VariantDialog(subsystem, self)
        if dlg.exec():
            self.editor.set_variants(subsystem, dlg.rows(), dlg.active.currentText())

    def auto_layout(self) -> None:
        self.editor.auto_layout()
        self.view.fit()

    def create_subsystem(self) -> None:
        blocks = self.scene.selected_blocks()
        if not blocks:
            self.status_label.setText("Select the blocks to group first.")
            return
        sub = self.editor.create_subsystem(blocks)
        if sub is not None:
            self.scene.select_blocks([sub.name])

    def select_all(self) -> None:
        for item in self.scene.blocks.values():
            item.setSelected(True)

    def set_theme(self, mode: ThemeMode) -> None:
        ThemeManager.instance().set_mode(mode)
        self.qsettings.setValue("theme", mode.value)
        for a in self.theme_menu.actions():
            a.setChecked(a.data() == mode.value)

    # ------------------------------------------------------------------ simulation

    def run_simulation(self) -> None:
        self.status_label.setText("Compiling…")
        if not self.simulation.run():
            self.status_label.setText("The model has errors — see Diagnostics.")
            self.diagnostics_dock.show()

    def edit_settings(self) -> None:
        dlg = SettingsDialog(self.diagram_model.root.settings, self)
        if dlg.exec() and dlg.result_settings is not None:
            self.editor.set_settings(dlg.result_settings)

    def _sim_state_changed(self, state: SimulationState) -> None:
        running = state is SimulationState.RUNNING
        self.act_run.setEnabled(not running)
        self.act_stop.setEnabled(running)
        self.progress.setVisible(running)
        self.stop_time.setEnabled(not running)
        # Loading another model mid-run would hand the old run's result to the new model.
        for act in (self.act_new, self.act_open, self.act_import_simulink, self.act_import_spice):
            act.setEnabled(not running)
        self.examples_menu.setEnabled(not running)
        self.recent_menu.setEnabled(not running and bool(self.files.recent_files()))
        if running:
            self.status_label.setText("Simulating…")
            self._open_scopes()  # at the start, so they plot live
        elif state in (SimulationState.FINISHED, SimulationState.STOPPED):
            res = self.sim_model.result
            info = ""
            if res is not None and res.stats is not None:
                info = f" — {res.stats.n_points} points in {res.stats.wall_time:.2f} s ({res.stats.solver})"
                if res.stats.zero_crossings:
                    info += f", {res.stats.zero_crossings} zero crossings"
            self.status_label.setText(("Stopped" if state is SimulationState.STOPPED else "Finished") + info)
            if res is not None and res.stats is not None and res.stats.warnings:
                self.sim_model.set_error("Warning: " + "; ".join(res.stats.warnings), ())
                self.diagnostics_dock.show()
            self._open_scopes()
        elif state is SimulationState.FAILED:
            self.status_label.setText("Simulation failed — see Diagnostics.")
            self.diagnostics_dock.show()

    def _open_scopes(self) -> None:
        """Open every scope the first time a model runs, like Simulink's 'open at simulation start'."""
        if self.scopes:
            return
        for _path, b in self.diagram_model.root.walk():
            if b.type_id == "sinks.scope":
                self.open_scope(b)

    def _close_scope_of(self, block: Block) -> None:
        win = self.scopes.get(id(block))
        if win is not None and win.block is block:
            win.close()

    def open_scope(self, block: Block) -> ScopeWindow:
        win = self.scopes.get(id(block))
        if win is None:
            win = ScopeWindow(block, self.sim_model, self, self.diagram_model.path_of(block))
            self.scopes[id(block)] = win
        win.show()
        win.raise_()
        return win

    # ------------------------------------------------------------------ context menus

    def _show_context_menu(self, target: object, screen_pos: QPoint) -> None:
        self.context_menu(target).exec(screen_pos)

    def context_menu(self, target: object) -> QMenu:
        """The right-click menu for a block, a wire (Connection) or the empty canvas (None)."""
        menu = QMenu(self)
        if isinstance(target, Block):
            blocks = self.scene.selected_blocks()
            if len(blocks) <= 1:
                is_sub = getattr(target, "system", None) is not None
                if is_sub:
                    menu.addAction("Open Subsystem", lambda: self.diagram_model.enter(target))
                elif target.type_id == "sinks.scope":
                    menu.addAction("Open Scope", lambda: self.open_scope(target))
                menu.addAction("Rename…", lambda: self.rename_block(target))
                menu.addAction("Requirements…", lambda: self.link_requirements(target))
                menu.addAction(self.act_block_help)
                if is_sub:
                    menu.addSeparator()
                    if getattr(target, "library_link", None) is None:
                        menu.addAction("Edit Mask…", lambda: self.edit_mask(target))
                        menu.addAction("Variants…", lambda: self.edit_variants(target))
                    else:
                        menu.addAction("Break Library Link", lambda: self.editor.break_link(target))
                    menu.addAction(self.act_save_to_library)
                    menu.addAction(self.act_codegen)
            menu.addSeparator()
            menu.addActions([self.act_cut, self.act_copy, self.act_delete])
            menu.addAction(self.act_flip)
            menu.addAction(self.act_rotate)
            if any("size" in b.meta for b in self.scene.selected_blocks()):
                menu.addAction(self.act_reset_size)
            menu.addAction(self.act_create_subsystem)
        elif isinstance(target, Link):  # a physical link
            link = target
            menu.addAction("Delete Link", lambda: self.editor.delete([], [link]))
        elif isinstance(target, Connection):  # a wire
            wire = target
            menu.addAction("Name Signal…", lambda: self.name_signal(wire))
            menu.addAction("Delete Wire", lambda: self.editor.delete([], [wire]))
            if wire.meta.get("points"):
                menu.addAction(
                    "Reset Route",
                    lambda: self.editor.set_wire_points([(wire, wire.meta.get("points"), None)], "Reset route"),
                )
        else:
            menu.addAction(self.act_paste)
            menu.addAction(self.act_select_all)
            menu.addSeparator()
            menu.addActions([self.act_layout, self.act_fit])
            if self.diagram_model.diagram.parent is not None:
                menu.addAction(self.act_up)
        return menu

    def link_requirements(self, block: Block, ids: list[str] | None = None) -> bool:
        """Choose the requirements a block implements (right-click ▸ Requirements…)."""
        if ids is None:
            reqs = self.diagram_model.root.requirements
            dlg = LinkRequirementsDialog(block, list(reqs), {r.id: r.title for r in reqs.items}, self)
            if dlg.exec() != LinkRequirementsDialog.DialogCode.Accepted:
                return False
            ids = dlg.chosen()
        return self.editor.link_requirements(block, ids)

    def name_signal(self, wire: Connection, name: str | None = None) -> bool:
        """Name the signal on a wire (its source output); offers the data dictionary's names."""
        from nervus.core.dictionary import signal_names

        if name is None:
            current = signal_names(wire.src).get(wire.src_port, "")
            choices = [current, *[n for n in self.diagram_model.root.signals if n != current]]
            name, ok = QInputDialog.getItem(
                self, "Name Signal", f"Signal name of {wire.src.name} output {wire.src_port + 1}\n"
                "(a data dictionary object of that name sets its type, unit and range; empty: no name):",
                choices, 0, True,
            )  # fmt: skip
            if not ok:
                return False
        return self.editor.name_signal(wire.src, wire.src_port, name)

    def flip_selection(self) -> None:
        self.editor.flip_blocks(self.scene.selected_blocks())

    def rotate_selection(self) -> None:
        self.editor.rotate_blocks(self.scene.selected_blocks())

    def reset_size_selection(self) -> None:
        for b in self.scene.selected_blocks():
            size = b.meta.get("size")
            if size is not None:
                self.editor.resize_block(b, list(size), None)

    def rename_block(self, block: Block) -> None:
        name, ok = QInputDialog.getText(self, "Rename block", "New name:", text=block.name)
        if ok and name.strip() and name.strip() != block.name:
            self.editor.rename(block, name.strip())

    def _block_activated(self, block: Block) -> None:
        if getattr(block, "system", None) is not None:
            self.diagram_model.enter(block)
        elif block.LOGS_INPUTS and block.type_id == "sinks.scope":
            self.open_scope(block)
        elif block.type_id == "charts.state_chart":
            self.open_chart_editor(block)
        elif block.type_id == "sources.signal_builder":
            self.open_signal_editor(block)
        else:
            self.properties_dock.show()
            self.properties_dock.raise_()

    def open_signal_editor(self, block: Block) -> SignalEditor:
        editor = SignalEditor(block, self.editor, self)
        editor.show()
        return editor

    def open_chart_editor(self, block: Block) -> ChartEditor:
        editor = ChartEditor(block, self.editor, self)
        editor.show()
        return editor

    # ------------------------------------------------------------------ help

    def _help_browser(self) -> HelpBrowser:
        if self._help is None:
            self._help = HelpBrowser(self)
        self._help.show()
        self._help.raise_()
        return self._help

    # ------------------------------------------------------------------ onboarding

    def show_welcome(self) -> WelcomeDialog:
        """The start window (non-modal, so the editor stays usable behind it)."""
        dlg = WelcomeDialog(self.qsettings, self.files.recent_files(), self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.firstModelRequested.connect(self.start_first_tutorial)
        dlg.tourRequested.connect(self.start_tour)
        dlg.newModelRequested.connect(self.new_file)
        dlg.openRequested.connect(self.open_file)
        dlg.exampleRequested.connect(self.open_example_with_tutorial)
        dlg.recentRequested.connect(self._open_path)
        dlg.tutorialsRequested.connect(lambda: self._help_browser().show_page("page:tutorials"))
        dlg.show()
        self._welcome = dlg
        return dlg

    def start_tour(self) -> EditorTour:
        tour = EditorTour(self, editor_stops(self))
        tour.start()
        self._tour = tour
        return tour

    def start_first_tutorial(self) -> None:
        """An empty model, with the first tutorial open beside it."""
        if not self._confirm_discard():
            return
        self.files.new()
        self._help_browser().show_page("page:tutorial_first_model")
        self.status_label.setText("Follow the tutorial in the help window: start by adding the 8 blocks of step 1.")

    def open_example_with_tutorial(self, model: Path, help_page: str) -> None:
        self.open_example(model)
        if self.diagram_model.file_path is not None and self.diagram_model.file_path.resolve() == model.resolve():
            self._help_browser().show_page(f"page:{help_page}")

    def show_help(self) -> None:
        self._help_browser().show_page("page:getting_started")

    def show_help_for_type(self, type_id: str) -> None:
        self._help_browser().show_block(type_id)

    def show_block_help(self) -> None:
        blocks = self.scene.selected_blocks()
        type_id = blocks[0].type_id if blocks else self.library.selected_type()
        if type_id and self.library.selected_is_component() and not blocks:
            type_id = "ports.subsystem"
        if type_id:
            self.show_help_for_type(type_id)
        else:
            self.show_help()

    def about(self) -> None:
        QMessageBox.about(
            self,
            "About Nervus",
            f"<h3>Nervus {__version__}</h3>"
            "<p>Open-source block-diagram modeling and simulation in Python.</p>"
            "<p>MIT License · <a href='https://github.com/ikjbi/nervus'>github.com/ikjbi/nervus</a></p>",
        )

    # ------------------------------------------------------------------ lifecycle

    def _restore_layout(self) -> None:
        geo = self.qsettings.value("geometry")
        state = self.qsettings.value("windowState")
        if geo is not None:
            self.restoreGeometry(geo)
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.simulation.stop()
        self.simulation.wait(5000)
        self.tests.stop()
        self.tests.wait(5000)
        self.qsettings.setValue("geometry", self.saveGeometry())
        self.qsettings.setValue("windowState", self.saveState())
        for w in self.scopes.values():
            w.close()
        event.accept()


def sample_time_colors(rates: list[Any]) -> dict[Any, str]:
    """Colour per sample time for View → Sample Time Colours: discrete rates fastest first, then continuous
    (the normal wire colour, so not listed) and constant."""
    series = current_theme().series
    order = (1, 2, 0, 3, 5, 6, 7)  # orange, green, blue, amber, ...
    colors: dict[Any, str] = {r: series[order[k % len(order)]] for k, r in enumerate(rates)}
    colors["constant"] = series[4]
    return colors
