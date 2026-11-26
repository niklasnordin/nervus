"""Data Inspector: overlay and compare signals from several simulation runs (like Simulink's SDI)."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from nervus.gui.models.simulation_model import Run, SimulationModel
from nervus.gui.theme import Theme, ThemeManager, current_theme
from nervus.sim import SimResult
from nervus.testing import compare_results

LOG_FILTER = "Vehicle logs (*.csv *.txt *.asc *.blf *.trc *.log);;All files (*)"
LOAD_FILTER = (
    "Results and logs (*.nervres *.mf4 *.mdf *.csv *.txt *.asc *.blf *.trc *.log);;Nervus results (*.nervres);;"
    "MDF4 measurements (*.mf4 *.mdf);;" + LOG_FILTER
)


class DataInspector(QWidget):
    """Select runs and signals to overlay. With *Difference* on, every run is plotted relative to the first
    selected run. Series colours follow a fixed order (run × signal), so a series keeps its colour when other
    series are hidden."""

    def __init__(self, sim: SimulationModel, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.sim = sim
        self.setWindowTitle("Data Inspector")
        self.resize(1000, 600)

        self.runs = QListWidget()
        self.signals = QListWidget()
        self.signals.setSortingEnabled(True)
        self.diff = QCheckBox("Difference to first run")
        self.summary = QLabel(" ")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        load = QPushButton("Load…")
        save = QPushButton("Save run…")
        export = QPushButton("Export CSV/MAT…")
        clear = QPushButton("Clear")
        for b, tip in (
            (load, "Load a saved result, an MDF4 measurement or a vehicle log (CSV, CAN log)"),
            (save, "Save the selected run as .nervres"),
            (export, "Export the selected run"),
        ):
            b.setToolTip(tip)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("<b>Runs</b>"))
        lv.addWidget(self.runs, 1)
        row = QHBoxLayout()
        for b in (load, save, export, clear):
            row.addWidget(b)
        lv.addLayout(row)
        lv.addWidget(QLabel("<b>Signals</b>"))
        lv.addWidget(self.signals, 2)
        lv.addWidget(self.diff)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.6)
        self.plot.setLabel("bottom", "time", units="s")
        self.legend = self.plot.addLegend(offset=(10, 10))
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.plot, 1)
        rv.addWidget(self.summary)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 720])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self.runs.itemChanged.connect(lambda _i: self._runs_changed())
        self.signals.itemChanged.connect(lambda _i: self.refresh())
        self.diff.toggled.connect(lambda _v: self.refresh())
        load.clicked.connect(self._load)
        save.clicked.connect(self._save)
        export.clicked.connect(self._export)
        clear.clicked.connect(sim.clear_runs)
        sim.runsChanged.connect(self.rebuild_runs)
        ThemeManager.instance().themeChanged.connect(self._apply_theme)
        self._apply_theme(current_theme())
        self.rebuild_runs()

    # ------------------------------------------------------------------ lists

    def rebuild_runs(self) -> None:
        checked = set(self.checked_run_labels())
        self.runs.blockSignals(True)
        self.runs.clear()
        for k, run in enumerate(self.sim.runs):
            item = QListWidgetItem(run.label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            last = k == len(self.sim.runs) - 1
            item.setCheckState(
                Qt.CheckState.Checked if (run.label in checked or (not checked and last)) else Qt.CheckState.Unchecked
            )
            stats = run.result.stats
            item.setToolTip(f"{len(run.result.t)} points" + (f", {stats.solver}" if stats else ""))
            self.runs.addItem(item)
        self.runs.blockSignals(False)
        self._runs_changed()

    def checked_runs(self) -> list[Run]:
        labels = set(self.checked_run_labels())
        return [r for r in self.sim.runs if r.label in labels]

    def checked_run_labels(self) -> list[str]:
        return [
            self.runs.item(i).text()
            for i in range(self.runs.count())
            if self.runs.item(i).checkState() == Qt.CheckState.Checked
        ]

    def checked_signals(self) -> list[str]:
        return [
            self.signals.item(i).text()
            for i in range(self.signals.count())
            if self.signals.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _runs_changed(self) -> None:
        checked = set(self.checked_signals())
        names: set[str] = set()
        for run in self.checked_runs():
            names.update(run.result.names())
        self.signals.blockSignals(True)
        self.signals.clear()
        units = {n: r.result.unit(n) for r in self.checked_runs() for n in r.result.names() if r.result.unit(n)}
        for name in sorted(names):
            item = QListWidgetItem(name)
            if name in units:
                item.setToolTip(units[name])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked)
            self.signals.addItem(item)
        self.signals.blockSignals(False)
        self.refresh()

    def select(self, runs: list[str], signals: list[str]) -> None:
        """Check the given runs and signals (used by the Test Manager and tests)."""
        self.runs.blockSignals(True)
        for i in range(self.runs.count()):
            item = self.runs.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in runs else Qt.CheckState.Unchecked)
        self.runs.blockSignals(False)
        self._runs_changed()
        self.signals.blockSignals(True)
        for i in range(self.signals.count()):
            item = self.signals.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in signals else Qt.CheckState.Unchecked)
        self.signals.blockSignals(False)
        self.refresh()

    # ------------------------------------------------------------------ plotting

    def _apply_theme(self, t: Theme) -> None:
        self.plot.setBackground(t.plot_surface)
        for axis in ("left", "bottom"):
            ax = self.plot.getAxis(axis)
            ax.setPen(pg.mkPen(t.plot_grid))
            ax.setTextPen(pg.mkPen(t.text_secondary))
        self.legend.setLabelTextColor(t.text_secondary)
        self.refresh()

    def refresh(self) -> None:
        t = current_theme()
        self.plot.clear()
        self.legend.clear()
        runs, signals = self.checked_runs(), self.checked_signals()
        series = [(r, s) for s in signals for r in runs if s in r.result]
        diff = self.diff.isChecked() and len(runs) >= 2
        ref = runs[0].result if diff else None
        shown, units = 0, set()
        for run, name in series:
            if shown >= len(t.series):
                break
            res = run.result
            y = np.asarray(res[name], dtype=float)
            if y.ndim > 1:
                y = y[:, 0]
            if ref is not None:
                if run.result is ref or name not in ref:
                    continue
                yr = np.asarray(ref[name], dtype=float)
                yr = yr[:, 0] if yr.ndim > 1 else yr
                y = y - np.interp(res.t, ref.t, yr)
            unit = res.unit(name)
            units.add(unit)
            label = f"{run.label}: {name}" + (f" [{unit}]" if unit else "") + (" − first" if ref is not None else "")
            self.plot.plot(res.t, y, pen=pg.mkPen(t.series[shown], width=2), name=label)
            shown += 1
        self.legend.setVisible(shown > 1)
        self.plot.setLabel("left", units.pop() if len(units) == 1 else "")
        self.summary.setText(self._summary(runs, signals))

    def _summary(self, runs: list[Run], signals: list[str]) -> str:
        if len(runs) >= 2 and signals:
            diffs = compare_results(runs[1].result, runs[0].result, signals)
            parts = [f"{k}: max |Δ| = {v:.3g}" for k, v in diffs.items()]
            return f"{runs[1].label} vs {runs[0].label} — " + ", ".join(parts)
        if not runs:
            return "Select one or more runs. Every simulation and every test result appears here."
        return "Select signals to plot. Select two runs to see their difference."

    # ------------------------------------------------------------------ files

    def _selected_run(self) -> Run | None:
        runs = self.checked_runs()
        return runs[-1] if runs else None

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load result or log", "", LOAD_FILTER)
        if path:
            self.load_file(path)

    def open_log(self) -> None:
        """Ask for a vehicle log (a scanner app's CSV or a CAN log) and add it."""
        path, _ = QFileDialog.getOpenFileName(self, "Open vehicle log", "", LOG_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str, dbc: str | None = None) -> bool:
        """Add a saved result (.nervres), a measurement file (MDF4, e.g. from INCA, CANape or a logger) or a
        vehicle log (CSV from an OBD-II scanner app, or a CAN log: for a CAN log Nervus asks for a DBC file, and
        without one decodes its OBD-II answers)."""
        from nervus import logs

        try:
            lower = path.lower()
            if lower.endswith((".mf4", ".mdf")):
                from nervus.mdf import read_mdf

                result = read_mdf(path)
            elif lower.endswith(".nervres"):
                result = SimResult.load(path)
            else:
                if dbc is None and logs.is_can_log(path):
                    dbc, _ = QFileDialog.getOpenFileName(
                        self, "DBC file to decode the CAN log (Cancel: OBD-II answers only)", "", "DBC (*.dbc)"
                    )
                result = logs.read_log(path, dbc=dbc or None)
        except Exception as exc:  # unreadable or foreign files: show why
            QMessageBox.warning(self, "Load result", f"Could not read {path}:\n{exc}")
            return False
        self.sim.add_run(result, path.rsplit("/", 1)[-1])
        return True

    def _save(self) -> None:
        run = self._selected_run()
        if run is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save result", run.label.replace(":", ""), "Nervus results (*.nervres)"
        )
        if path:
            run.result.save(path)

    def _export(self) -> None:
        run = self._selected_run()
        if run is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export result", run.label.replace(":", ""), "CSV (*.csv);;MATLAB (*.mat);;MDF4 (*.mf4)"
        )
        if path.endswith(".mat"):
            run.result.to_mat(path)
        elif path.endswith(".mf4"):
            from nervus.mdf import write_mdf

            write_mdf(run.result, path)
        elif path:
            run.result.to_csv(path if path.endswith(".csv") else path + ".csv")
