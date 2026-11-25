"""Generate C code from the editor, and run the software-in-the-loop check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal

from nervus.codegen import CodegenError, GeneratedCode, generate_c
from nervus.codegen.sil import SilComparison, back_to_back, find_compiler
from nervus.core import Block, Diagram, NervusError
from nervus.gui.models.diagram_model import DiagramModel


class CodegenController(QObject):
    errorOccurred = Signal(str, tuple)

    def __init__(self, model: DiagramModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.model = model

    def target_for(self, selection: list[Block]) -> Diagram | Block:
        """The selected subsystem if exactly one is selected, else the whole model."""
        if len(selection) == 1 and getattr(selection[0], "system", None) is not None:
            return selection[0]
        return self.model.root

    def generate(
        self, target: Diagram | Block, tasks: bool = False, integer_only: bool = False
    ) -> GeneratedCode | None:
        try:
            return generate_c(target, tasks=tasks, integer_only=integer_only)
        except CodegenError as exc:
            self.errorOccurred.emit(str(exc), exc.blocks)
        except NervusError as exc:
            self.errorOccurred.emit(str(exc), tuple(getattr(exc, "blocks", ())))
        return None

    @staticmethod
    def misra_available() -> bool:
        from nervus.codegen.misra import cppcheck_path

        return cppcheck_path() is not None

    def misra_check(self, code: GeneratedCode) -> list[Any] | None:
        """MISRA C:2012 findings (cppcheck's MISRA addon); None if the check could not run."""
        from nervus.codegen.misra import misra_check

        try:
            return misra_check(code)
        except RuntimeError as exc:
            self.errorOccurred.emit(str(exc), ())
            return None

    @staticmethod
    def coverage_available() -> bool:
        from nervus.codegen.c_coverage import gcov_path
        from nervus.codegen.sil import find_compiler

        return find_compiler() is not None and gcov_path() is not None

    def c_coverage(self, target: Diagram | Block, code: GeneratedCode) -> Any:
        """Coverage of the generated code by the model's test cases; None if it could not run (reported)."""
        from nervus.codegen.c_coverage import coverage_of_tests

        try:
            return coverage_of_tests(self.model.root, target, integer_only=code.integer_only, tasks=bool(code.tasks))
        except NervusError as exc:
            self.errorOccurred.emit(str(exc), tuple(getattr(exc, "blocks", ())))
            return None

    def export_fmu(self, target: Diagram | Block, path: str | Path) -> Path | None:
        """Export ``target`` as an FMI 2.0 co-simulation FMU (generated C code, compiled for this platform)."""
        from nervus.codegen.fmu import export_fmu

        try:
            return export_fmu(target, path)
        except CodegenError as exc:
            self.errorOccurred.emit(str(exc), exc.blocks)
        except NervusError as exc:
            self.errorOccurred.emit(str(exc), tuple(getattr(exc, "blocks", ())))
        return None

    def save(self, code: GeneratedCode, folder: str | Path) -> list[Path]:
        return code.write(folder)

    @staticmethod
    def compiler_available() -> bool:
        return find_compiler() is not None

    def sil_check(self, target: Diagram | Block, code: GeneratedCode, steps: int = 500) -> SilComparison | None:
        """Back-to-back test with reproducible random inputs in [-1, 1] on every input port."""
        rng = np.random.default_rng(0)
        inputs = {}
        for name, width in code.inputs:
            inputs[name] = rng.uniform(-1.0, 1.0, (steps, width))
        try:
            return back_to_back(target, inputs, steps, tolerance=1e-9, tasks=bool(code.tasks),
                                integer_only=code.integer_only)  # fmt: skip
        except NervusError as exc:
            self.errorOccurred.emit(str(exc), tuple(getattr(exc, "blocks", ())))
            return None
