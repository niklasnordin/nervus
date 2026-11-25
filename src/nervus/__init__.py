"""Nervus: open-source block-diagram modeling and simulation in Python."""

from nervus import blocks
from nervus.core import (
    Block,
    Diagram,
    ModelError,
    ParameterError,
    ParameterSet,
    SimulationError,
    SimulationSettings,
    Workspace,
)
from nervus.io import load, save
from nervus.sim import SimResult, compile_diagram, simulate

__version__ = "0.2.0"

__all__ = [
    "Block",
    "Diagram",
    "ModelError",
    "ParameterError",
    "ParameterSet",
    "SimResult",
    "SimulationError",
    "SimulationSettings",
    "Workspace",
    "__version__",
    "blocks",
    "compile_diagram",
    "load",
    "save",
    "simulate",
]
