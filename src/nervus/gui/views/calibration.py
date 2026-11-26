"""The Calibration window: measure and calibrate an ECU over XCP (like INCA or CANape), or the virtual ECU of a model."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nervus.a2l import A2lError, A2lFile, Characteristic, Measurement, load_a2l
from nervus.gui.theme import Theme, ThemeManager, current_theme
from nervus.sim.result import SimResult

TARGETS = ("Virtual ECU of this model", "XCP on UDP", "XCP on TCP", "XCP on CAN")
WINDOW = 10.0
"""Seconds of measurement shown in the plot."""


def load_seed_key(path: str) -> Callable[[bytes], bytes]:
    """A seed-and-key function from a Python file that defines ``key(seed: bytes) -> bytes``."""
    namespace: dict[str, Any] = {}
    exec(compile(Path(path).read_text(encoding="utf-8"), path, "exec"), namespace)
    fn = namespace.get("key")
    if not callable(fn):
        raise ValueError(f"{path} must define key(seed) -> bytes")
    return fn


class CalibrationWindow(QWidget):
    """Connect to an ECU, measure its variables (DAQ, else polling), record them, and change its calibration
    (values, curves and maps), with plausibility limits from the A2L file, the working and the reference page, and
    a log of every write."""

    def __init__(self, main: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.main = main
        self.setWindowTitle("Calibration")
        self.resize(1200, 760)
        self.a2l: A2lFile | None = None
        self.master: Any = None
        self.vecu: Any = None
        self.seed_key: Callable[[bytes], bytes] | None = None
        self.daq = False
        self.watched: list[str] = []
        self.data: dict[str, tuple[list[float], list[float]]] = {}
        self.recording: dict[str, tuple[list[float], list[float]]] | None = None
        self.current: Characteristic | None = None
        self._raw = b""
        self._loading = False
        self.t0 = time.monotonic()
        self.log: list[str] = []

        # connection ---------------------------------------------------------------------------------------
        self.target = QComboBox()
        self.target.addItems(TARGETS)
        self.subsystem = QComboBox()
        self.address = QLineEdit("127.0.0.1:5555")
        self.address.setToolTip("UDP/TCP: host:port. CAN: interface:channel:bitrate:request id:response id")
        self.a2l_label = QLabel("no A2L file")
        self.a2l_label.setObjectName("muted")
        a2l_btn = QPushButton("A2L file…")
        key_btn = QPushButton("Seed && key…")
        key_btn.setToolTip("A Python file defining key(seed) -> bytes, for ECUs that protect calibration")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setDefault(True)
        self.page = QComboBox()
        self.page.addItems(["Working page", "Reference page"])
        self.page.setEnabled(False)
        self.page.setToolTip("The reference page holds the safe calibration; switch to it in one click")
        self.record_btn = QPushButton("● Record")
        self.record_btn.setCheckable(True)
        self.record_btn.setEnabled(False)
        self.status = QLabel("Not connected")
        self.status.setObjectName("muted")
        form = QFormLayout()
        row = QHBoxLayout()
        row.addWidget(self.target)
        row.addWidget(self.subsystem, 1)
        row.addWidget(self.address, 1)
        form.addRow("Target", row)
        row2 = QHBoxLayout()
        row2.addWidget(a2l_btn)
        row2.addWidget(self.a2l_label, 1)
        row2.addWidget(key_btn)
        row2.addWidget(self.connect_btn)
        row2.addWidget(self.page)
        row2.addWidget(self.record_btn)
        form.addRow("", row2)

        # variables ----------------------------------------------------------------------------------------
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter variables")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Variable", "Unit"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.filter)
        lv.addWidget(self.tree, 1)
        hint = QLabel("Tick measurements to plot them; click a characteristic to edit it.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lv.addWidget(hint)

        # measurement -------------------------------------------------------------------------------------
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.6)
        self.plot.setLabel("bottom", "time", units="s")
        self.legend = self.plot.addLegend(offset=(10, 10))
        self.values = QTableWidget(0, 3)
        self.values.setHorizontalHeaderLabels(["Measurement", "Value", "Unit"])
        self.values.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.values.verticalHeader().setVisible(False)
        self.values.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # calibration --------------------------------------------------------------------------------------
        self.editor_title = QLabel("<b>Calibration</b>: select a characteristic")
        self.editor = QTableWidget(0, 0)
        self.editor.itemChanged.connect(self._edited)
        self.editor_plot = pg.PlotWidget()
        self.editor_plot.showGrid(x=True, y=True, alpha=0.6)
        self.limits = QLabel("")
        self.limits.setObjectName("muted")
        self.writes = QListWidget()
        self.writes.setToolTip("Every value written to the ECU in this session")
        ed = QWidget()
        ev = QVBoxLayout(ed)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.addWidget(self.editor_title)
        eh = QSplitter()
        eh.addWidget(self.editor)
        eh.addWidget(self.editor_plot)
        eh.setSizes([500, 300])
        ev.addWidget(eh, 1)
        ev.addWidget(self.limits)
        ev.addWidget(QLabel("<b>Write log</b>"))
        ev.addWidget(self.writes)

        meas = QSplitter()
        meas.addWidget(self.plot)
        meas.addWidget(self.values)
        meas.setSizes([650, 300])
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(meas)
        right.addWidget(ed)
        right.setSizes([380, 380])
        body = QSplitter()
        body.addWidget(left)
        body.addWidget(right)
        body.setSizes([360, 940])
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(body, 1)
        layout.addWidget(self.status)

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._acquire)
        self.target.currentIndexChanged.connect(self._target_changed)
        a2l_btn.clicked.connect(self._choose_a2l)
        key_btn.clicked.connect(self._choose_seed_key)
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.page.currentIndexChanged.connect(self._set_page)
        self.record_btn.toggled.connect(self._record)
        self.filter.textChanged.connect(self._filter)
        self.tree.itemChanged.connect(self._ticked)
        self.tree.itemClicked.connect(self._clicked)
        ThemeManager.instance().themeChanged.connect(self._apply_theme)
        self._apply_theme(current_theme())
        self._fill_subsystems()
        self._target_changed()

    # connection -----------------------------------------------------------------------------------------------

    def _fill_subsystems(self) -> None:
        self.subsystem.clear()
        self.subsystem.addItem("whole model", "")
        diagram = getattr(getattr(self.main, "diagram_model", None), "diagram", None)
        if diagram is not None:
            for b in diagram.blocks:
                if getattr(b, "system", None) is not None:
                    self.subsystem.addItem(b.name, b.name)
            k = self.subsystem.findData("ECU")
            if k >= 0:
                self.subsystem.setCurrentIndex(k)

    def _target_changed(self) -> None:
        vecu = self.target.currentIndex() == 0
        self.subsystem.setVisible(vecu)
        self.address.setVisible(not vecu)
        if self.target.currentText() == "XCP on CAN" and ":" not in self.address.text().split(".")[-1]:
            self.address.setText("virtual:nervus:500000:0x7F0:0x7F1")
        elif self.target.currentText() != "XCP on CAN" and self.address.text().startswith(
            ("virtual", "pcan", "kvaser")
        ):
            self.address.setText("127.0.0.1:5555")

    def _choose_a2l(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "A2L file of the ECU", "", "A2L (*.a2l);;All files (*)")
        if path:
            self.load_a2l(path)

    def load_a2l(self, path: str | Path) -> bool:
        try:
            self.a2l = load_a2l(path)
        except (A2lError, OSError) as exc:
            QMessageBox.warning(self, "A2L file", f"Could not read {path}:\n{exc}")
            return False
        self.a2l_label.setText(f"{Path(path).name}: {len(self.a2l.measurements)} measurements, "
                               f"{len(self.a2l.characteristics)} characteristics")  # fmt: skip
        if self.a2l.xcp_ip and self.target.currentIndex() == 1:
            ip = self.a2l.xcp_ip[0]
            self.address.setText(f"{ip.host or '127.0.0.1'}:{ip.port}")
        if self.a2l.xcp_can and self.target.currentText() == "XCP on CAN":
            parts = self.address.text().split(":")
            c = self.a2l.xcp_can
            self.address.setText(f"{parts[0]}:{parts[1] if len(parts) > 1 else ''}:{c.baudrate or 500000}:"
                                 f"0x{c.request_id:X}:0x{c.response_id:X}")  # fmt: skip
        self._fill_tree()
        return True

    def _choose_seed_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seed and key (Python)", "", "Python (*.py)")
        if path:
            try:
                self.seed_key = load_seed_key(path)
                self.status.setText(f"Seed and key from {Path(path).name}")
            except Exception as exc:  # the user's file: show why it fails
                QMessageBox.warning(self, "Seed and key", str(exc))

    def toggle_connection(self) -> None:
        if self.master is not None:
            self.disconnect_ecu()
        else:
            self.connect_ecu()

    def _transport(self) -> Any:
        from nervus.xcp import CanTransport, TcpTransport, UdpTransport

        kind = self.target.currentText()
        text = self.address.text().strip()
        if kind == "XCP on CAN":
            import can

            parts = text.split(":")
            interface, channel = parts[0], parts[1] if len(parts) > 1 else None
            bitrate = int(parts[2]) if len(parts) > 2 and parts[2] else None
            req = int(parts[3], 0) if len(parts) > 3 else 0x7F0
            resp = int(parts[4], 0) if len(parts) > 4 else 0x7F1
            kwargs: dict[str, Any] = {"interface": interface, "channel": channel or None}
            if bitrate and interface != "virtual":
                kwargs["bitrate"] = bitrate
            self._bus: Any = can.Bus(**kwargs)
            return CanTransport(self._bus, req, resp)
        host, _, port = text.rpartition(":")
        return (UdpTransport if kind == "XCP on UDP" else TcpTransport)(host or "127.0.0.1", int(port or 5555))

    def connect_ecu(self) -> bool:
        from nervus.xcp import XcpMaster

        try:
            if self.target.currentIndex() == 0:
                transport = self._start_vecu()
            else:
                if self.a2l is None:
                    QMessageBox.information(self, "Calibration", "Choose the ECU's A2L file first.")
                    return False
                transport = self._transport()
            self.master = XcpMaster(transport, timeout=0.3)
            info = self.master.connect()
            if self.seed_key is not None:
                self.master.unlock(self.seed_key)
        except Exception as exc:  # no answer, a wrong address, a missing compiler, ...
            self.master = None
            self._stop_vecu()
            QMessageBox.warning(self, "Connect", f"Could not connect:\n{exc}")
            return False
        self.connect_btn.setText("Disconnect")
        self.page.setEnabled(True)
        self.record_btn.setEnabled(True)
        self.status.setText(f"Connected: MAX_CTO {info['max_cto']}, MAX_DTO {info['max_dto']}"
                            + (f"; virtual ECU, base period {self.vecu.period * 1000:g} ms" if self.vecu else ""))  # fmt: skip
        self.t0 = time.monotonic()
        self.data = {}
        self._restart_measurement()
        self.timer.start()
        return True

    def _start_vecu(self) -> Any:
        from nervus.vecu import VirtualEcu
        from nervus.xcp import UdpTransport

        diagram = self.main.diagram_model.diagram
        name = self.subsystem.currentData()
        target = diagram.block(name) if name else diagram
        self.vecu = VirtualEcu(target, seed_key=self.seed_key)
        self.vecu.start(udp_port=0)
        self.a2l = self.vecu.a2l
        self.a2l_label.setText(f"{self.vecu.a2l_path.name} (generated): {len(self.a2l.measurements)} measurements, "
                               f"{len(self.a2l.characteristics)} characteristics")  # fmt: skip
        self._fill_tree()
        return UdpTransport("127.0.0.1", self.vecu.udp_port)

    def _stop_vecu(self) -> None:
        if self.vecu is not None:
            self.vecu.stop()
            self.vecu = None

    def disconnect_ecu(self) -> None:
        self.timer.stop()
        if self.master is not None:
            try:
                if self.daq:
                    self.master.stop_daq()
                self.master.disconnect()
            except Exception:  # the ECU may be gone already
                pass
        self.master = None
        self.daq = False
        self._stop_vecu()
        bus = getattr(self, "_bus", None)
        if bus is not None:
            bus.shutdown()
            self._bus = None
        if self.record_btn.isChecked():
            self.record_btn.setChecked(False)
        self.connect_btn.setText("Connect")
        self.page.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.status.setText("Not connected")

    def closeEvent(self, event: Any) -> None:
        self.disconnect_ecu()
        super().closeEvent(event)

    def _set_page(self, index: int) -> None:
        if self.master is None:
            return
        try:
            self.master.set_page(index)
        except Exception as exc:  # the ECU may have no pages
            QMessageBox.warning(self, "Page", str(exc))
            return
        self._log(f"page → {'reference' if index else 'working'}")
        if self.current is not None:
            self.show_characteristic(self.current.name)

    # variables ------------------------------------------------------------------------------------------------

    def _fill_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        assert self.a2l is not None
        groups = []
        sections: list[tuple[str, dict[str, Any]]] = [("Measurements", dict(self.a2l.measurements)),
                                                      ("Characteristics", dict(self.a2l.characteristics))]  # fmt: skip
        for title, objects in sections:
            g = QTreeWidgetItem([f"{title} ({len(objects)})", ""])
            g.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for name, obj in sorted(objects.items()):
                it = QTreeWidgetItem([name, obj.unit])
                it.setToolTip(0, f"{obj.description or name}\n{getattr(obj, 'type', 'MEASUREMENT')}, "
                                 f"limits {obj.lower:g} … {obj.upper:g}")  # fmt: skip
                it.setData(0, Qt.ItemDataRole.UserRole, (title[0], name))
                if title == "Measurements":
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.CheckState.Checked if name in self.watched else Qt.CheckState.Unchecked)
                g.addChild(it)
            groups.append(g)
        self.tree.addTopLevelItems(groups)
        for g in groups:
            g.setExpanded(True)
        self.tree.blockSignals(False)

    def _filter(self, text: str) -> None:
        t = text.lower()
        for k in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(k)
            assert g is not None
            for j in range(g.childCount()):
                child = g.child(j)
                assert child is not None
                child.setHidden(bool(t) and t not in child.text(0).lower())

    def _ticked(self, item: QTreeWidgetItem, _col: int) -> None:
        kind, name = item.data(0, Qt.ItemDataRole.UserRole) or ("", "")
        if kind != "M":
            return
        if item.checkState(0) == Qt.CheckState.Checked and name not in self.watched:
            self.watched.append(name)
        elif item.checkState(0) != Qt.CheckState.Checked and name in self.watched:
            self.watched.remove(name)
        self._restart_measurement()

    def watch(self, names: list[str]) -> None:
        """Measure these measurements (used by tests and scripts)."""
        self.watched = list(names)
        self._fill_tree()
        self._restart_measurement()

    def _clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        kind, name = item.data(0, Qt.ItemDataRole.UserRole) or ("", "")
        if kind == "C":
            self.show_characteristic(name)

    # measurement ----------------------------------------------------------------------------------------------

    def _daq_signal(self, m: Measurement) -> Any:
        from nervus.xcp import DaqSignal

        a2l = self.a2l
        assert a2l is not None

        def decode(raw: bytes, m: Measurement = m) -> float:
            return m.decode(raw, a2l)[0]

        return DaqSignal(m.name, m.address, m.size(), decode)

    def _restart_measurement(self) -> None:
        self.values.setRowCount(len(self.watched))
        for r, name in enumerate(self.watched):
            m = self.a2l.measurements[name] if self.a2l else None
            for c, text in enumerate((name, "", m.unit if m else "")):
                self.values.setItem(r, c, QTableWidgetItem(text))
        if self.master is None or self.a2l is None:
            return
        self.daq = False
        if not self.watched:
            return
        try:
            self.master.start_daq([self._daq_signal(self.a2l.measurements[n]) for n in self.watched], event=0)
            self.daq = True
            self.status.setText("Measuring with DAQ (event 0)")
        except Exception as exc:  # no DAQ, or the values do not fit in a packet: poll them
            self.status.setText(f"Measuring by polling ({exc})" if "fit" in str(exc) else "Measuring by polling")

    def _acquire(self) -> None:
        if self.master is None or self.a2l is None:
            return
        now = time.monotonic() - self.t0
        new: dict[str, list[tuple[float, float]]] = {}
        try:
            if self.daq:
                new = self.master.read_daq(0.0)
            else:
                for name in self.watched:
                    m = self.a2l.measurements[name]
                    new[name] = [(now, m.decode(self.master.upload(m.address, m.size()), self.a2l)[0])]
        except Exception as exc:  # the ECU stopped answering
            self.status.setText(f"Measurement failed: {exc}")
            return
        for name, samples in new.items():
            ts, vs = self.data.setdefault(name, ([], []))
            for t, v in samples:
                ts.append(t)
                vs.append(v)
            if self.recording is not None:
                rt, rv = self.recording.setdefault(name, ([], []))
                rt.extend(t for t, _v in samples)
                rv.extend(v for _t, v in samples)
        self._refresh_plot()

    def _refresh_plot(self) -> None:
        t = current_theme()
        self.plot.clear()
        self.legend.clear()
        for k, name in enumerate(self.watched):
            ts, vs = self.data.get(name, ([], []))
            if not ts:
                continue
            tend = ts[-1]
            start = next((i for i, x in enumerate(ts) if x >= tend - WINDOW), 0)
            del ts[: max(0, start - 1)]
            del vs[: max(0, start - 1)]
            self.plot.plot(ts, vs, pen=pg.mkPen(t.series[k % len(t.series)], width=2), name=name)
            item = self.values.item(k, 1)
            if item is not None:
                item.setText(f"{vs[-1]:.6g}")
        self.legend.setVisible(len(self.watched) > 1)

    def _record(self, on: bool) -> None:
        if on:
            self.recording = {}
            self.record_btn.setText("■ Stop recording")
            return
        self.record_btn.setText("● Record")
        rec, self.recording = self.recording, None
        if not rec or not any(ts for ts, _v in rec.values()):
            return
        assert self.a2l is not None
        units = {n: self.a2l.measurements[n].unit for n in rec if n in self.a2l.measurements}
        result = SimResult.from_channels({n: (np.array(ts), np.array(vs)) for n, (ts, vs) in rec.items() if ts},
                                         units=units)  # fmt: skip
        self.last_recording = result
        sim = getattr(self.main, "sim_model", None)
        if sim is not None:
            sim.add_run(result, f"ECU recording {time.strftime('%H:%M:%S')}")
        path, _ = QFileDialog.getSaveFileName(self, "Save the recording", "recording.mf4",
                                              "MDF4 (*.mf4);;Nervus results (*.nervres)")  # fmt: skip
        if path:
            if path.endswith(".nervres"):
                result.save(path)
            else:
                from nervus.mdf import write_mdf

                write_mdf(result, path, units=units, comment="Nervus calibration recording")

    # calibration ----------------------------------------------------------------------------------------------

    def show_characteristic(self, name: str) -> None:
        """Read a characteristic from the ECU and show it in the editor."""
        assert self.a2l is not None
        c = self.a2l.characteristics[name]
        self.current = c
        self.editor_title.setText(f"<b>{name}</b> ({c.type}{', ' + c.unit if c.unit else ''}): {c.description}")
        lo, hi = c.extended_limits or (c.lower, c.upper)
        self.limits.setText(f"Allowed values: {c.lower:g} … {c.upper:g}" + (
            f" (extended {lo:g} … {hi:g})" if c.extended_limits else ""))  # fmt: skip
        if self.master is None:
            self.editor.setRowCount(0)
            return
        try:
            self._raw = self.master.upload(c.address, c.size(self.a2l))
            com = {ax.reference: self._axis(ax.reference) for ax in c.axes if ax.attribute == "COM_AXIS"}
            dec = c.decode(self._raw, self.a2l, com)
        except Exception as exc:  # unsupported layout or an ECU error
            self.editor.setRowCount(0)
            self.limits.setText(f"Cannot read {name}: {exc}")
            return
        self._loading = True
        values, axes = dec["values"], dec["axes"]
        if c.type == "MAP":
            nx, ny = len(values), len(values[0]) if values else 0
            self.editor.setRowCount(nx)
            self.editor.setColumnCount(ny)
            self.editor.setVerticalHeaderLabels([f"{v:g}" for v in axes[0]])
            self.editor.setHorizontalHeaderLabels([f"{v:g}" for v in axes[1]])
            flat = [v for row in values for v in row]
            vmin, vmax = (min(flat), max(flat)) if flat else (0.0, 1.0)
            for i in range(nx):
                for j in range(ny):
                    self.editor.setItem(i, j, self._cell(values[i][j], vmin, vmax))
        else:
            n = len(values)
            self.editor.setRowCount(1)
            self.editor.setColumnCount(n)
            self.editor.setVerticalHeaderLabels(["value"])
            labels = [f"{v:g}" for v in axes[0]] if axes else [str(k) for k in range(n)]
            self.editor.setHorizontalHeaderLabels(labels)
            for j, v in enumerate(values):
                self.editor.setItem(0, j, self._cell(v, min(values), max(values)))
        self._loading = False
        self._plot_characteristic(values, axes, c)

    def _axis(self, name: str) -> list[float]:
        assert self.a2l is not None
        ap = self.a2l.axis_pts[name]
        return ap.decode(self.master.upload(ap.address, ap.size(self.a2l)), self.a2l)

    def _cell(self, v: float, vmin: float, vmax: float) -> QTableWidgetItem:
        item = QTableWidgetItem(f"{v:.6g}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if vmax > vmin:  # a light colour scale for maps and curves
            f = (v - vmin) / (vmax - vmin)
            accent = QColor(current_theme().accent)
            accent.setAlphaF(0.08 + 0.32 * f)
            item.setBackground(QBrush(accent))
        return item

    def _plot_characteristic(self, values: list[Any], axes: list[list[float]], c: Characteristic) -> None:
        t = current_theme()
        self.editor_plot.clear()
        self.editor_plot.setVisible(c.type in ("MAP", "CURVE") or len(values) > 1)
        if c.type == "MAP":
            for j in range(len(values[0]) if values else 0):  # one curve per column (y axis point)
                self.editor_plot.plot(axes[0], [row[j] for row in values],
                                      pen=pg.mkPen(t.series[j % len(t.series)], width=2), symbol="o", symbolSize=5)  # fmt: skip
        elif len(values) > 1:
            x = axes[0] if axes else list(range(len(values)))
            self.editor_plot.plot(x, values, pen=pg.mkPen(t.series[0], width=2), symbol="o", symbolSize=6)

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or self.current is None or self.master is None or self.a2l is None:
            return
        c = self.current
        try:
            value = float(item.text())
        except ValueError:
            self.show_characteristic(c.name)
            return
        lo, hi = c.lower, c.upper
        if not lo <= value <= hi:
            QMessageBox.warning(self, "Calibration", f"{value:g} is outside the allowed range {lo:g} … {hi:g} of "
                                f"{c.name} (from the A2L file); not written.")  # fmt: skip
            self.show_characteristic(c.name)
            return
        self.write_value(c.name, item.row(), item.column(), value)

    def write_value(self, name: str, row: int, column: int, value: float) -> None:
        """Write one value of a characteristic (row and column of the editor) to the ECU, and log it."""
        assert self.a2l is not None
        c = self.a2l.characteristics[name]
        if not self.a2l.writable(c.address, c.size(self.a2l)):
            QMessageBox.warning(self, "Calibration", f"{name} is not in a calibration memory segment; not written.")
            return
        com = {ax.reference: self._axis(ax.reference) for ax in c.axes if ax.attribute == "COM_AXIS"}
        raw = self.master.upload(c.address, c.size(self.a2l))
        values = c.decode(raw, self.a2l, com)["values"]
        if c.type == "MAP":
            old = values[row][column]
            values[row][column] = value
        else:
            old = values[column]
            values[column] = value
        try:
            new = c.encode(values, self.a2l, raw)
            changed = [k for k in range(len(raw)) if raw[k] != new[k]]
            if changed:  # only the bytes that change
                self.master.download(c.address + changed[0], new[changed[0] : changed[-1] + 1])
        except Exception as exc:  # write protected, locked, out of range, ...
            QMessageBox.warning(self, "Calibration", f"Could not write {name}: {exc}")
        else:
            where = f"[{row}][{column}]" if c.type == "MAP" else f"[{column}]" if c.type != "VALUE" else ""
            self._log(f"{name}{where}: {old:.6g} → {value:.6g}")
        self.show_characteristic(name)

    def _log(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {text}"
        self.log.append(line)
        self.writes.addItem(line)
        self.writes.scrollToBottom()

    def _apply_theme(self, t: Theme) -> None:
        for p in (self.plot, self.editor_plot):
            p.setBackground(t.plot_surface)
            for axis in ("left", "bottom"):
                ax = p.getAxis(axis)
                ax.setPen(pg.mkPen(t.plot_grid))
                ax.setTextPen(pg.mkPen(t.text_secondary))
        self.legend.setLabelTextColor(t.text_secondary)
