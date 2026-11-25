"""Variable-step integration with scipy's solvers (RK45, BDF, ...), with robust zero-crossing location."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from scipy.integrate import BDF, DOP853, LSODA, RK23, RK45, Radau
from scipy.optimize import brentq

from nervus.core.errors import SimulationError
from nervus.core.types import Array

_METHODS = {"RK45": RK45, "RK23": RK23, "DOP853": DOP853, "Radau": Radau, "BDF": BDF, "LSODA": LSODA}
_EPS = np.finfo(float).eps


def _crossed(g_old: float, g_new: float, direction: float) -> bool:
    """The same rule as scipy's solve_ivp: a sign change or reaching zero, upwards (``direction`` > 0), downwards
    (< 0) or either way (0). A signal that stays at exactly zero does not cross (a saturation input resting on its
    limit would otherwise fire at the start of every step)."""
    if g_old == 0.0 and g_new == 0.0:
        return False
    up, down = g_old <= 0.0 <= g_new, g_old >= 0.0 >= g_new
    return (up and direction > 0) or (down and direction < 0) or ((up or down) and direction == 0)


def _locate(event: Callable[[float, Array], float], sol: Callable[[float], Array], t_old: float, t_new: float,
            g_old: float) -> float:  # fmt: skip
    """The time of the zero crossing of ``event`` in [t_old, t_new], on the step's interpolating polynomial.

    The crossing was found from the values at the step's end points; the polynomial reproduces them only up to
    rounding, so a value of (almost) exactly zero at an end can come out with the other sign (this depends on the
    platform's floating point). Then the bracket has no sign change, and the crossing is at that end.
    """

    def h(t: float) -> float:
        return float(event(t, sol(t)))

    ha, hb = h(t_old), h(t_new)
    if ha == 0.0:
        return t_old
    if hb == 0.0 or ha * hb > 0.0:
        return t_old if g_old == 0.0 else t_new
    return float(brentq(h, t_old, t_new, xtol=4 * _EPS, rtol=4 * _EPS))


def integrate_variable(
    f: Callable[[float, Array], Array],
    t0: float,
    t1: float,
    x0: Array,
    method: str,
    rtol: float,
    atol: float,
    max_step: float,
    events: list[Callable[[float, Array], float]] | None = None,
) -> tuple[Array, Array, int | None]:
    """Integrate from ``t0`` towards ``t1``, stopping early at the first event (zero crossing).

    Returns the accepted time points, the states (shape ``(n_states, n_points)``) including both end points,
    and the index of the event that stopped the integration (``None`` if it reached ``t1``).

    This is the loop of :func:`scipy.integrate.solve_ivp` with the same solvers and the same rule for detecting a
    crossing, but the crossing is located robustly (see :func:`_locate`): ``solve_ivp`` raises an error instead.
    """
    if method not in _METHODS:
        raise SimulationError(f"unknown variable-step solver {method!r}")
    x0 = np.asarray(x0, dtype=np.float64)
    solver = _METHODS[method](f, t0, x0, t1, rtol=rtol, atol=atol, max_step=max_step)
    ts, ys = [t0], [x0.copy()]
    events = events or []
    g = [float(ev(t0, x0)) for ev in events]
    while solver.status == "running":
        message = solver.step()
        if solver.status == "failed":
            raise SimulationError(f"solver {method} failed between t={t0:g} and t={t1:g}: {message}")
        t_old, t_new, y_new = float(solver.t_old), float(solver.t), solver.y
        if events:
            g_new = [float(ev(t_new, y_new)) for ev in events]
            active = [k for k, ev in enumerate(events) if _crossed(g[k], g_new[k], getattr(ev, "direction", 0.0))]
            if active:
                sol = solver.dense_output()
                roots = [(_locate(events[k], sol, t_old, t_new, g[k]), k) for k in active]
                t_ev, fired = min(roots)  # the earliest crossing ends the integration
                ts.append(t_ev)
                ys.append(np.asarray(sol(t_ev), dtype=np.float64))
                return np.asarray(ts), np.stack(ys, axis=1), fired
            g = g_new
        ts.append(t_new)
        ys.append(np.array(y_new, dtype=np.float64))
    return np.asarray(ts), np.stack(ys, axis=1), None
