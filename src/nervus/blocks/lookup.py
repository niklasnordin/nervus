"""Lookup tables: 1-D curves and 2-D maps, the core of ECU calibration (fuel, ignition, boost, ...)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from nervus.blocks.registry import register
from nervus.core.block import Block, Width, broadcast_widths
from nervus.core.errors import ParameterError
from nervus.core.params import Param, ParamKind, output_type_param
from nervus.core.types import Array

CATEGORY = "Lookup Tables"
INTERPOLATION = ("linear", "nearest", "flat")
EXTRAPOLATION = ("clip", "linear")


def _check_breakpoints(bp: Array, name: str) -> None:
    if bp.ndim != 1 or bp.size < 2:
        raise ParameterError(f"{name} must be a vector with at least 2 points")
    if np.any(np.diff(bp) <= 0):
        raise ParameterError(f"{name} must be strictly increasing")


def locate(x: Array, bp: Array, interpolation: str, extrapolation: str) -> tuple[npt.NDArray[np.intp], Array]:
    """For each x, the index ``i`` of the interval [bp[i], bp[i+1]] and the fraction ``f`` within it.

    ``f`` follows the interpolation mode (0/1 for ``nearest``, 0 for ``flat``) and the extrapolation mode
    (clamped to [0, 1] for ``clip``). This is also exactly what generated C code computes.
    """
    n = bp.size
    i = np.clip(np.searchsorted(bp, x, side="right") - 1, 0, n - 2)
    f = (x - bp[i]) / (bp[i + 1] - bp[i])
    if extrapolation == "clip":
        f = np.clip(f, 0.0, 1.0)
    if interpolation == "nearest":  # a tie (exactly half way) goes to the upper point, in both extrapolation modes
        f = np.where(np.clip(f, 0.0, 1.0) >= 0.5, 1.0, 0.0)
    elif interpolation == "flat":
        f = np.where(f >= 1.0, 1.0, 0.0) if extrapolation == "clip" else np.where(x >= bp[-1], 1.0, 0.0)
    return i, f


@register
class Lookup1D(Block):
    """1-D lookup table (a calibration *curve*): maps the input through a table of points.

    ![1-D lookup table interpolation](figures/lookup_1d.png)

    For an input between breakpoints $x_i$ and $x_{i+1}$ with linear interpolation:

    $$ y = t_i + \\frac{u - x_i}{x_{i+1} - x_i}\\,(t_{i+1} - t_i) $$

    - *interpolation*: ``linear``; ``nearest`` (closest point); ``flat`` (value of the breakpoint at or
      below the input, a staircase).
    - *extrapolation*: ``clip`` holds the end values; ``linear`` extends the first/last segment.

    Each element of a vector input is looked up separately.
    """

    type_id = "lookup.table_1d"
    label = "1-D Lookup Table"
    category = CATEGORY
    PARAMS = (
        Param("breakpoints", [0.0, 1.0, 2.0], ParamKind.ARRAY, "Input values (strictly increasing)"),
        Param("table", [0.0, 1.0, 4.0], ParamKind.ARRAY, "Output value at each breakpoint"),
        Param("interpolation", "linear", ParamKind.CHOICE, "Between breakpoints", choices=INTERPOLATION),
        Param("extrapolation", "clip", ParamKind.CHOICE, "Outside the breakpoints", choices=EXTRAPOLATION),
        output_type_param("double"),
    )

    def _data(self) -> tuple[Array, Array]:
        bp = np.asarray(self.params["breakpoints"], dtype=float).ravel()
        tab = np.asarray(self.params["table"], dtype=float).ravel()
        return bp, tab

    def validate_params(self) -> None:
        bp, tab = self._data()
        _check_breakpoints(bp, "breakpoints")
        if tab.size != bp.size:
            raise ParameterError(f"table has {tab.size} values but there are {bp.size} breakpoints")

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        bp, tab = self._data()
        i, f = locate(u[0], bp, self.params["interpolation"], self.params["extrapolation"])
        return [tab[i] + f * (tab[i + 1] - tab[i])]

    def coverage_outcomes(self) -> list[str]:
        bp, _ = self._data()
        return ["below range", *[f"[{bp[k]:g}, {bp[k + 1]:g}]" for k in range(bp.size - 1)], "above range"]

    def coverage_hits(self, t: float, u: Sequence[Array], y: Sequence[Array], xd: Array) -> list[str]:
        bp, _ = self._data()
        names = self.coverage_outcomes()
        hits = set()
        for x in np.atleast_1d(u[0]):
            if x < bp[0]:
                hits.add(names[0])
            elif x > bp[-1]:
                hits.add(names[-1])
            else:
                k = min(int(np.searchsorted(bp, x, side="right")) - 1, bp.size - 2)
                hits.add(names[1 + k])
        return sorted(hits)


@register
class Lookup2D(Block):
    """2-D lookup table (a calibration *map*), e.g. ignition advance over engine speed and load.

    ![2-D lookup table](figures/lookup_2d.png)

    Input 0 selects the **row** (``row_breakpoints``) and input 1 the **column** (``column_breakpoints``).
    ``table`` has one row per row breakpoint and one column per column breakpoint. With linear interpolation
    the result is bilinear between the four surrounding points:

    $$ y = (1-f_r)(1-f_c)\\,T_{i,j} + (1-f_r) f_c\\,T_{i,j+1} + f_r (1-f_c)\\,T_{i+1,j} + f_r f_c\\,T_{i+1,j+1} $$

    where $f_r$ and $f_c$ are the fractions within the row and column intervals. Interpolation and
    extrapolation work as in the 1-D table.
    """

    type_id = "lookup.table_2d"
    label = "2-D Lookup Table"
    category = CATEGORY
    NUM_INPUTS = 2
    PARAMS = (
        Param("row_breakpoints", [0.0, 1.0], ParamKind.ARRAY, "Breakpoints for input 0 (rows)"),
        Param("column_breakpoints", [0.0, 1.0], ParamKind.ARRAY, "Breakpoints for input 1 (columns)"),
        Param("table", [[0.0, 1.0], [1.0, 2.0]], ParamKind.ARRAY, "Table: rows × columns"),
        Param("interpolation", "linear", ParamKind.CHOICE, "Between breakpoints", choices=("linear", "nearest")),
        Param("extrapolation", "clip", ParamKind.CHOICE, "Outside the breakpoints", choices=EXTRAPOLATION),
        output_type_param("double"),
    )

    def _data(self) -> tuple[Array, Array, Array]:
        r = np.asarray(self.params["row_breakpoints"], dtype=float).ravel()
        c = np.asarray(self.params["column_breakpoints"], dtype=float).ravel()
        tab = np.atleast_2d(np.asarray(self.params["table"], dtype=float))
        return r, c, tab

    def validate_params(self) -> None:
        r, c, tab = self._data()
        _check_breakpoints(r, "row_breakpoints")
        _check_breakpoints(c, "column_breakpoints")
        if tab.shape != (r.size, c.size):
            raise ParameterError(f"table must be {r.size}×{c.size} (rows × columns), got {tab.shape[0]}×{tab.shape[1]}")

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        return [broadcast_widths(in_widths)]

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        r, c, tab = self._data()
        w = self.out_widths[0]
        x0, x1 = np.broadcast_to(u[0], (w,)), np.broadcast_to(u[1], (w,))
        interp, extrap = self.params["interpolation"], self.params["extrapolation"]
        i, fr = locate(x0, r, interp, extrap)
        j, fc = locate(x1, c, interp, extrap)
        y = (
            (1 - fr) * (1 - fc) * tab[i, j]
            + (1 - fr) * fc * tab[i, j + 1]
            + fr * (1 - fc) * tab[i + 1, j]
            + fr * fc * tab[i + 1, j + 1]
        )
        return [np.asarray(y, dtype=float)]


@register
class Prelookup(Block):
    """Breakpoint search on its own: finds the interval index *k* and the fraction *f* of the input.

    ![Prelookup and Interpolation](figures/prelookup.png)

    For an input between breakpoints $x_k$ and $x_{k+1}$:

    $$ f = \\frac{u - x_k}{x_{k+1} - x_k} $$

    Output 0 is *k* (0-based), output 1 is *f*. Feed both into one or more **Interpolation** blocks. ECUs
    calibrate many maps over the same axes (engine speed, load); searching the axis once and sharing the
    result saves time on the processor, and the A2L file can declare the axis as a shared axis.

    With ``clip`` extrapolation, *f* stays in [0, 1]; with ``linear`` it continues outside.
    """

    type_id = "lookup.prelookup"
    label = "Prelookup"
    category = CATEGORY
    NUM_OUTPUTS = 2
    PARAMS = (
        Param("breakpoints", [0.0, 1.0, 2.0], ParamKind.ARRAY, "Axis points (strictly increasing)"),
        Param("extrapolation", "clip", ParamKind.CHOICE, "Outside the breakpoints", choices=EXTRAPOLATION),
    )

    def _bp(self) -> Array:
        return np.asarray(self.params["breakpoints"], dtype=float).ravel()

    def validate_params(self) -> None:
        _check_breakpoints(self._bp(), "breakpoints")

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        return [in_widths[0], in_widths[0]]

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        i, f = locate(u[0], self._bp(), "linear", self.params["extrapolation"])
        return [np.asarray(i, dtype=float), np.asarray(f, dtype=float)]


@register
class Interpolation(Block):
    """Interpolates an n-D *table* (1 to 4 dimensions) at the index and fraction from Prelookup blocks.

    Inputs: ``k1, f1`` (1-D), ``k1, f1, k2, f2`` (2-D, rows then columns), and so on: one Prelookup per
    dimension. In 2-D the result is the same as a 2-D Lookup Table with linear interpolation over the same
    breakpoints:

    $$ y = (1-f_1)(1-f_2)\\,T_{k_1,k_2} + (1-f_1) f_2\\,T_{k_1,k_2+1} + f_1 (1-f_2)\\,T_{k_1+1,k_2} + f_1 f_2\\,T_{k_1+1,k_2+1} $$

    With more dimensions the same rule weights all $2^n$ corners of the cell around the input. ECUs use 3-D
    maps for example for a base value over speed × load, with a third axis such as coolant temperature or
    the camshaft position. A 3-D *table* is a nested list: ``table[i][j][k]``.
    """

    type_id = "lookup.interpolation"
    label = "Interpolation using Prelookup"
    category = CATEGORY
    MAX_DIMENSIONS = 4
    PARAMS = (
        Param("dimensions", 1, ParamKind.INT, "Number of table dimensions (1 to 4)", min=1, max=4),
        Param("table", [0.0, 1.0, 4.0], ParamKind.ARRAY, "Table values (1-D: one per breakpoint; 2-D: rows × columns)"),
        output_type_param("double"),
    )

    def _table(self) -> Array:
        tab = np.asarray(self.params["table"], dtype=float)
        n = int(self.params["dimensions"])
        return tab.ravel() if n == 1 else tab

    def validate_params(self) -> None:
        tab = self._table()
        n = int(self.params["dimensions"])
        if tab.ndim != n:
            raise ParameterError(f"a {n}-D table needs {n} nested levels, got {tab.ndim}")
        if min(tab.shape) < 2:
            raise ParameterError("the table needs at least 2 points in every dimension")

    def num_inputs(self) -> int:
        return 2 * int(self.params["dimensions"])

    def input_names(self) -> list[str]:
        return [f"{c}{d + 1}" for d in range(int(self.params["dimensions"])) for c in "kf"]

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        return [broadcast_widths(in_widths)]

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        tab = self._table()
        w = self.out_widths[0]
        n = tab.ndim
        ks = [np.clip(np.broadcast_to(u[2 * d], (w,)).astype(np.intp), 0, tab.shape[d] - 2) for d in range(n)]
        fs = [np.broadcast_to(u[2 * d + 1], (w,)) for d in range(n)]
        y = np.zeros(w)
        for corner in range(1 << n):  # every corner of the cell, weighted by the fractions
            weight = np.ones(w)
            ix = []
            for d in range(n):
                up = (corner >> (n - 1 - d)) & 1
                weight = weight * (fs[d] if up else 1 - fs[d])
                ix.append(ks[d] + up)
            y = y + weight * tab[tuple(ix)]
        return [np.asarray(y, dtype=float)]


@register
class AdaptiveMap(Block):
    """A 2-D map that **learns**, like an ECU's long-term fuel trim or knock adaptation per load/speed cell.

    Output: the table interpolated at (x, y), like a 2-D Lookup Table with clipped axes. Every sample while
    *learn* is ≥ 0.5 the table moves towards the correction input *e* at the cells around (x, y), each by its
    interpolation weight:

    $$ T_{ij} \\leftarrow \\mathrm{clip}(T_{ij} + r\\,T_s\\,w_{ij}\\,e,\\ -L,\\ L) $$

    with the learning *rate* r (1/s) and the *limit* L. So each operating region gets its own correction: a lean
    error that only exists at idle is learned in the idle cells and does not disturb full load. The learned
    table is a state (it would be kept in the ECU's non-volatile memory).
    """

    type_id = "lookup.adaptive_map"
    label = "Adaptive Map"
    category = CATEGORY
    NUM_INPUTS = 4
    PARAMS = (
        Param("row_breakpoints", [0.0, 1.0], ParamKind.ARRAY, "Breakpoints for x (rows)"),
        Param("column_breakpoints", [0.0, 1.0], ParamKind.ARRAY, "Breakpoints for y (columns)"),
        Param("initial", 0.0, ParamKind.ARRAY, "Initial table (rows × columns, or one value for all cells)"),
        Param("rate", 0.1, ParamKind.FLOAT, "Learning rate", "1/s", min=0.0),
        Param("limit", math.inf, ParamKind.FLOAT, "Largest |value| of a cell", min=0.0),
        Param("sample_time", 0.01, ParamKind.SAMPLE_TIME, "Sample period", "s"),
    )

    def _axes(self) -> tuple[Array, Array]:
        return (
            np.asarray(self.params["row_breakpoints"], dtype=float).ravel(),
            np.asarray(self.params["column_breakpoints"], dtype=float).ravel(),
        )

    def validate_params(self) -> None:
        r, c = self._axes()
        _check_breakpoints(r, "row_breakpoints")
        _check_breakpoints(c, "column_breakpoints")
        init = np.asarray(self.params["initial"], dtype=float)
        if init.size not in (1, r.size * c.size):
            raise ParameterError(f"initial must be one value or a {r.size} × {c.size} table")

    def input_names(self) -> list[str]:
        return ["x", "y", "correction", "learn"]

    def feedthrough_ports(self) -> list[int]:
        return [0, 1]  # the output reads the table at (x, y); the correction only changes the table

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        return [1]

    def initial_discrete_state(self) -> Array:
        r, c = self._axes()
        return np.broadcast_to(np.asarray(self.params["initial"], dtype=float).ravel(), (r.size * c.size,)).copy()

    def weights(self, x: float, y: float) -> tuple[int, int, float, float]:
        r, c = self._axes()
        i, fr = locate(np.array([x]), r, "linear", "clip")
        j, fc = locate(np.array([y]), c, "linear", "clip")
        return int(i[0]), int(j[0]), float(fr[0]), float(fc[0])

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        nc = self._axes()[1].size
        i, j, fr, fc = self.weights(float(np.ravel(u[0])[0]), float(np.ravel(u[1])[0]))
        tab = xd.reshape(-1, nc)
        y = (
            (1 - fr) * (1 - fc) * tab[i, j]
            + (1 - fr) * fc * tab[i, j + 1]
            + fr * (1 - fc) * tab[i + 1, j]
            + fr * fc * tab[i + 1, j + 1]
        )
        return [np.array([y])]

    def update(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> Array:
        new = xd.copy()
        if float(np.ravel(u[3])[0]) < 0.5:
            return new
        nc = self._axes()[1].size
        i, j, fr, fc = self.weights(float(np.ravel(u[0])[0]), float(np.ravel(u[1])[0]))
        step = self.params["rate"] * self.period() * float(np.ravel(u[2])[0])
        lim = self.params["limit"]
        for di, dj, w in ((0, 0, (1 - fr) * (1 - fc)), (0, 1, (1 - fr) * fc), (1, 0, fr * (1 - fc)), (1, 1, fr * fc)):
            k = (i + di) * nc + j + dj
            new[k] = min(max(new[k] + step * w, -lim), lim)
        return new
