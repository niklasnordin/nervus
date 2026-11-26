"""Simulation settings dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from nervus.core import SimulationSettings
from nervus.core.expressions import format_value, parse_value
from nervus.core.settings import FIXED_STEP_SOLVERS, SOLVERS

SOLVER_HELP = {
    "auto": "Variable step (RK45). A good default.",
    "RK45": "Variable step, explicit Runge-Kutta 4(5). Non-stiff models.",
    "RK23": "Variable step, explicit Runge-Kutta 2(3). Low accuracy, fast.",
    "DOP853": "Variable step, explicit 8th order. High accuracy.",
    "Radau": "Variable step, implicit. Stiff models.",
    "BDF": "Variable step, implicit multistep. Stiff models.",
    "LSODA": "Variable step, switches automatically between stiff and non-stiff.",
    "euler": "Fixed step, forward Euler. Simplest; matches basic embedded code.",
    "rk4": "Fixed step, classic Runge-Kutta 4. Use for models that will become code.",
}


class SettingsDialog(QDialog):
    def __init__(self, settings: SimulationSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Simulation Settings")
        self.setMinimumWidth(420)
        self.start = QLineEdit(format_value(settings.t_start))
        self.stop = QLineEdit(format_value(settings.t_final))
        self.solver = QComboBox()
        self.solver.addItems(list(SOLVERS))
        self.solver.setCurrentText(settings.solver)
        self.solver_help = QLabel()
        self.solver_help.setObjectName("muted")
        self.solver_help.setWordWrap(True)
        self.step = QLineEdit("" if settings.step is None else format_value(settings.step))
        self.step.setPlaceholderText("auto")
        self.max_step = QLineEdit("" if settings.max_step is None else format_value(settings.max_step))
        self.max_step.setPlaceholderText("auto")
        self.rtol = QLineEdit(format_value(settings.rtol))
        self.atol = QLineEdit(format_value(settings.atol))
        self.zero_crossing = QCheckBox("Locate discontinuities exactly (zero-crossing detection)")
        self.zero_crossing.setChecked(settings.zero_crossing)
        self.loops = QComboBox()
        self.loops.addItem("Error (add a delay or a state to break the loop)", "error")
        self.loops.addItem("Solve iteratively (Newton; slower, no C code)", "solve")
        self.loops.setCurrentIndex(1 if settings.algebraic_loops == "solve" else 0)
        self.loops.setToolTip("A feedback loop of blocks whose outputs depend directly on their inputs")
        self.mode = QComboBox()
        self.mode.addItem("Normal (blocks run in Python)", "normal")
        self.mode.addItem("Accelerated (the model as compiled C)", "accelerated")
        self.mode.setCurrentIndex(1 if settings.mode == "accelerated" else 0)
        self.mode.setToolTip(
            "Accelerated: 10 to 300 times faster for models whose blocks all have C code (needs a C compiler). "
            "Other models run normally; the run's warnings say why."
        )
        self.pacing = QLineEdit(format_value(settings.pacing) if settings.pacing else "")
        self.pacing.setPlaceholderText("off (as fast as possible)")
        self.pacing.setToolTip("1 = real time, 0.5 = half speed; to watch live scopes or to run against hardware")
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Start time [s]", self.start)
        form.addRow("Stop time [s]", self.stop)
        form.addRow("Solver", self.solver)
        form.addRow("", self.solver_help)
        form.addRow("Fixed step size [s]", self.step)
        form.addRow("Max step size [s]", self.max_step)
        form.addRow("Relative tolerance", self.rtol)
        form.addRow("Absolute tolerance", self.atol)
        form.addRow("", self.zero_crossing)
        form.addRow("Algebraic loops", self.loops)
        form.addRow("Simulation mode", self.mode)
        form.addRow("Pacing (× real time)", self.pacing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.solver.currentTextChanged.connect(self._solver_changed)
        self._solver_changed(settings.solver)
        self.result_settings: SimulationSettings | None = None

    def _solver_changed(self, name: str) -> None:
        fixed = name in FIXED_STEP_SOLVERS
        self.step.setEnabled(fixed)
        for w in (self.max_step, self.rtol, self.atol, self.zero_crossing):
            w.setEnabled(not fixed)
        self.solver_help.setText(SOLVER_HELP.get(name, ""))

    def _num(self, edit: QLineEdit, optional: bool = False) -> float | None:
        text = edit.text().strip()
        if optional and not text:
            return None
        v = parse_value(text)
        if not isinstance(v, float):
            raise ValueError(f"'{text}' is not a number")
        return v

    def _accept(self) -> None:
        try:
            s = SimulationSettings(
                t_start=self._num(self.start) or 0.0,
                t_final=self._num(self.stop) or 0.0,
                solver=self.solver.currentText(),
                step=self._num(self.step, optional=True),
                max_step=self._num(self.max_step, optional=True),
                rtol=self._num(self.rtol) or 0.0,
                atol=self._num(self.atol) or 0.0,
                zero_crossing=self.zero_crossing.isChecked(),
                algebraic_loops=str(self.loops.currentData()),
                pacing=self._num(self.pacing, optional=True) or 0.0,
                mode=str(self.mode.currentData()),
            )
            s.validate()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.result_settings = s
        self.accept()
