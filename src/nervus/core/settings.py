"""Simulation settings stored with a diagram (the equivalent of Simulink's model configuration)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from typing import Any

FIXED_STEP_SOLVERS = ("euler", "rk4")
VARIABLE_STEP_SOLVERS = ("RK45", "RK23", "DOP853", "Radau", "BDF", "LSODA")
SOLVERS = ("auto", *FIXED_STEP_SOLVERS, *VARIABLE_STEP_SOLVERS)


@dataclass
class SimulationSettings:
    """Time span and solver settings.

    ``solver``: ``"auto"`` (RK45), a fixed-step method (``"euler"``, ``"rk4"``) or a variable-step method
    (``"RK45"``, ``"RK23"``, ``"DOP853"`` non-stiff; ``"Radau"``, ``"BDF"``, ``"LSODA"`` stiff).
    ``step``: fixed step size; ``None`` picks the fastest discrete rate, or (t_final - t_start)/1000.
    ``max_step``: largest step a variable-step solver may take; ``None`` means (t_final - t_start)/50.
    """

    t_start: float = 0.0
    t_final: float = 10.0
    solver: str = "auto"
    step: float | None = None
    max_step: float | None = None
    rtol: float = 1e-6
    atol: float = 1e-8
    zero_crossing: bool = True
    """Locate discontinuities (saturation limits, switches, ...) exactly with variable-step solvers."""
    algebraic_loops: str = "error"
    """``"error"``: a loop of direct-feedthrough blocks is an error (add a Unit Delay, a filter or a state).
    ``"solve"``: solve such loops at every evaluation with Newton's method (slower, reported as a warning; such
    models cannot become C code)."""
    pacing: float = 0.0
    """Run no faster than ``pacing`` × real time (1 = real time, 0.5 = half speed), e.g. to watch live scopes or
    to run a plant against real hardware. 0: as fast as possible."""
    mode: str = "normal"
    """``"normal"``: blocks run in Python. ``"accelerated"``: the whole model runs as compiled C (needs a C
    compiler; with a fixed-step solver, RK4 for variable-step solvers). Models that cannot be compiled run
    normally, with a note in the run's warnings (see :mod:`nervus.codegen.accelerator`)."""

    @property
    def method(self) -> str:
        return "RK45" if self.solver == "auto" else self.solver

    @property
    def is_fixed_step(self) -> bool:
        return self.method in FIXED_STEP_SOLVERS

    def validate(self) -> None:
        if self.solver not in SOLVERS:
            raise ValueError(f"unknown solver {self.solver!r}; choose one of {', '.join(SOLVERS)}")
        if not (math.isfinite(self.t_start) and math.isfinite(self.t_final)):
            raise ValueError("start and stop times must be finite")
        if self.t_final < self.t_start:
            raise ValueError("stop time must be >= start time")
        if self.step is not None and self.step <= 0:
            raise ValueError("step must be > 0")
        if self.max_step is not None and self.max_step <= 0:
            raise ValueError("max_step must be > 0")
        if self.rtol <= 0 or self.atol <= 0:
            raise ValueError("tolerances must be > 0")
        if self.algebraic_loops not in ("error", "solve"):
            raise ValueError("algebraic_loops must be 'error' or 'solve'")
        if self.mode not in ("normal", "accelerated"):
            raise ValueError("mode must be 'normal' or 'accelerated'")
        if not (self.pacing >= 0 and math.isfinite(self.pacing)):
            raise ValueError("pacing must be >= 0 (0 = as fast as possible)")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not self.pacing:
            del d["pacing"]  # the default; keeps older model files unchanged
        if self.algebraic_loops == "error":
            del d["algebraic_loops"]
        if self.mode == "normal":
            del d["mode"]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationSettings:
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown simulation setting(s): {', '.join(sorted(unknown))}")
        s = cls(**data)
        s.validate()
        return s
