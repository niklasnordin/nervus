"""Generate the explanatory block figures used in the docs and the in-app help.

Run: uv run python scripts/gen_figures.py

Each figure is written to ``src/nervus/gui/resources/figures/`` (bundled with the app) and copied to
``docs/images/blocks/`` (for the GitHub/MkDocs docs). Response plots are simulated with Nervus itself.
Figures have a transparent background and neutral grey text so they read well in light and dark themes.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from nervus import Diagram, simulate
from nervus.blocks import (
    PID,
    Constant,
    Debounce,
    DiscreteFilter,
    DiscreteIntegrator,
    EdgeDetect,
    Integrator,
    Pulse,
    RateLimiter,
    Relay,
    Scope,
    Sine,
    Step,
    Sum,
    TransferFunction,
    ZeroOrderHold,
)

ROOT = Path(__file__).parent.parent
APP_DIR = ROOT / "src" / "nervus" / "gui" / "resources" / "figures"
DOCS_DIR = ROOT / "docs" / "images" / "blocks"

# Categorical palette (validated for colour-vision deficiency), used in this fixed order.
C1, C2, C3, C4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK = "#7d7c76"  # neutral grey: readable on light and dark backgrounds
GRID = "#9a99924d"

plt.rcParams.update(
    {
        "font.size": 10,
        "text.color": INK,
        "axes.labelcolor": INK,
        "axes.edgecolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.linewidth": 2.0,
        "legend.frameon": False,
        "savefig.transparent": True,
        "mathtext.fontset": "dejavusans",
    }
)

FIGURES: dict[str, Callable[[], plt.Figure]] = {}


def figure(name: str) -> Callable[[Callable[[], plt.Figure]], Callable[[], plt.Figure]]:
    def deco(fn: Callable[[], plt.Figure]) -> Callable[[], plt.Figure]:
        FIGURES[name] = fn
        return fn

    return deco


# ---------------------------------------------------------------------------- schematic helpers


def box(
    ax: Axes, x: float, y: float, w: float, h: float, text: str, color: str = INK, fill: str = "none", size: float = 11
) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", ec=color, fc=fill, lw=1.6))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=color)


def arrow(
    ax: Axes, a: tuple[float, float], b: tuple[float, float], color: str = INK, text: str = "", above: bool = True
) -> None:
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=12, lw=1.5, color=color))
    if text:
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        ax.text(
            mx,
            my + (0.12 if above else -0.12),
            text,
            ha="center",
            va="bottom" if above else "top",
            fontsize=10,
            color=color,
        )


def vector(ax: Axes, x: float, y: float, labels: list[str], color: str, cell: float = 0.42) -> None:
    """A column vector drawn as stacked cells, top to bottom."""
    for i, lab in enumerate(labels):
        yy = y - i * cell
        ax.add_patch(
            FancyBboxPatch((x, yy - cell), cell * 1.6, cell, boxstyle="square,pad=0", ec=color, fc=color + "22", lw=1.2)
        )
        ax.text(x + cell * 0.8, yy - cell / 2, lab, ha="center", va="center", fontsize=10, color=color)


def schematic(
    width: float, height: float, xlim: tuple[float, float], ylim: tuple[float, float]
) -> tuple[plt.Figure, Axes]:
    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def run(d: Diagram, t: float, **kw: object):  # type: ignore[no-untyped-def]
    return simulate(d, t, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- figures


@figure("gain_modes")
def gain_modes() -> plt.Figure:
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.3))
    titles = ["Scalar gain", "Vector gain (elementwise)", "Matrix gain (matrix)"]
    for ax, title in zip(axes, titles, strict=True):
        ax.set_xlim(0, 6)
        ax.set_ylim(-2.2, 1.2)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(title, color=INK, fontsize=11, pad=4)

    ax = axes[0]
    vector(ax, 0.2, 0.6, ["$u_1$", "$u_2$", "$u_3$"], C1)
    ax.text(1.4, -0.03, r"$\times$", fontsize=14, ha="center", va="center", color=INK)
    vector(ax, 1.8, 0.18, ["$k$"], C2)
    ax.text(3.0, -0.03, "=", fontsize=14, ha="center", va="center", color=INK)
    vector(ax, 3.4, 0.6, ["$k\\,u_1$", "$k\\,u_2$", "$k\\,u_3$"], C3)
    ax.text(3.0, -1.55, "gain = 2  →  every element doubled", ha="center", fontsize=9, color=INK)

    ax = axes[1]
    vector(ax, 0.2, 0.6, ["$u_1$", "$u_2$", "$u_3$"], C1)
    ax.text(1.4, -0.03, r"$\odot$", fontsize=14, ha="center", va="center", color=INK)
    vector(ax, 1.8, 0.6, ["$k_1$", "$k_2$", "$k_3$"], C2)
    ax.text(3.0, -0.03, "=", fontsize=14, ha="center", va="center", color=INK)
    vector(ax, 3.4, 0.6, ["$k_1u_1$", "$k_2u_2$", "$k_3u_3$"], C3)
    ax.text(3.0, -1.55, "gain = [1 2 3]  →  element by element", ha="center", fontsize=9, color=INK)

    ax = axes[2]
    cell = 0.42
    for r in range(2):
        for c in range(3):
            x, y = 0.1 + c * cell * 1.3, 0.39 - r * cell
            ax.add_patch(
                FancyBboxPatch((x, y - cell), cell * 1.3, cell, boxstyle="square,pad=0", ec=C2, fc=C2 + "22", lw=1.2)
            )
            ax.text(
                x + cell * 0.65, y - cell / 2, f"$K_{{{r + 1}{c + 1}}}$", ha="center", va="center", fontsize=9, color=C2
            )
    ax.text(1.95, -0.03, "@", fontsize=13, ha="center", va="center", color=INK)
    vector(ax, 2.25, 0.6, ["$u_1$", "$u_2$", "$u_3$"], C1)
    ax.text(3.55, -0.03, "=", fontsize=14, ha="center", va="center", color=INK)
    vector(ax, 3.85, 0.39, ["$y_1$", "$y_2$"], C3)
    ax.text(3.0, -1.55, "K is 2×3, u has 3 elements  →  y has 2", ha="center", fontsize=9, color=INK)
    fig.tight_layout()
    return fig


@figure("sum_signs")
def sum_signs() -> plt.Figure:
    fig, ax = schematic(6.0, 2.6, (0, 9), (-0.6, 3.3))
    for i, (lab, sign) in enumerate((("$u_0$", "+"), ("$u_1$", "−"), ("$u_2$", "+"))):
        y = 2.6 - i * 1.0
        arrow(ax, (0.6, y), (3.3, 1.6 + (1 - i) * 0.45), C1)
        ax.text(0.3, y, lab, ha="center", va="center", color=C1, fontsize=12)
        ax.text(3.0, 1.6 + (1 - i) * 0.45 + 0.08, sign, ha="right", va="bottom", fontsize=12, color=INK)
    box(ax, 3.35, 0.9, 1.4, 1.4, "Σ", size=18)
    arrow(ax, (4.75, 1.6), (6.4, 1.6), C3)
    ax.text(6.5, 1.6, r"$y = u_0 - u_1 + u_2$", va="center", fontsize=12, color=C3)
    ax.text(4.05, 0.35, 'signs = "+-+"', ha="center", fontsize=10, color=INK)
    return fig


@figure("saturation")
def saturation() -> plt.Figure:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.0))
    u = np.linspace(-2, 2, 400)
    a1.plot(u, np.clip(u, -0.5, 1.0), color=C1)
    a1.plot(u, u, "--", color=INK, lw=1, alpha=0.6)
    a1.axhline(1.0, color=C2, lw=1, ls=":")
    a1.axhline(-0.5, color=C2, lw=1, ls=":")
    a1.text(-1.9, 1.05, "upper", color=C2, va="bottom")
    a1.text(-1.9, -0.55, "lower", color=C2, va="top")
    a1.set_xlabel("input u")
    a1.set_ylabel("output y")
    a1.set_title("Characteristic", color=INK)
    t = np.linspace(0, 10, 500)
    a2.plot(t, 1.6 * np.sin(t), color=INK, lw=1, ls="--", label="input")
    a2.plot(t, np.clip(1.6 * np.sin(t), -0.5, 1.0), color=C1, label="output")
    a2.set_xlabel("time [s]")
    a2.set_title("In time", color=INK)
    a2.legend(loc="lower left")
    fig.tight_layout()
    return fig


def _source_plot(t: np.ndarray, y: np.ndarray, title: str, notes: list[tuple[float, float, str]]) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(6.0, 2.6))
    ax.plot(t, y, color=C1)
    for x, yy, text in notes:
        ax.annotate(text, (x, yy), color=C2, fontsize=10)
    ax.set_xlabel("time [s]")
    ax.set_title(title, color=INK)
    fig.tight_layout()
    return fig


@figure("step")
def step() -> plt.Figure:
    t = np.linspace(0, 3, 601)
    y = np.where(t >= 1.0, 2.0, 0.5)
    fig = _source_plot(t, y, "Step: time = 1, initial = 0.5, final = 2", [])
    ax = fig.axes[0]
    ax.axvline(1.0, color=C2, ls=":", lw=1)
    ax.text(1.03, 1.2, "time", color=C2)
    ax.text(0.1, 0.6, "initial", color=C2)
    ax.text(2.0, 2.06, "final", color=C2)
    ax.set_ylim(0, 2.4)
    return fig


@figure("ramp")
def ramp() -> plt.Figure:
    t = np.linspace(0, 3, 301)
    y = 0.5 + 1.5 * np.maximum(0, t - 1)
    fig = _source_plot(t, y, "Ramp: slope = 1.5, start_time = 1, initial = 0.5", [])
    ax = fig.axes[0]
    ax.axvline(1.0, color=C2, ls=":", lw=1)
    ax.text(1.03, 2.2, "start_time", color=C2)
    ax.annotate("", (2.5, 2.75), (2.0, 2.0), arrowprops={"arrowstyle": "-", "color": C2})
    ax.text(2.3, 2.1, "slope", color=C2)
    return fig


@figure("sine")
def sine() -> plt.Figure:
    t = np.linspace(0, 4, 800)
    w = 2 * np.pi * 0.5
    y = 1.5 * np.sin(w * t + 0.6) + 0.5
    fig = _source_plot(t, y, r"Sine Wave: amplitude 1.5, frequency $\pi$ rad/s (0.5 Hz), phase 0.6, bias 0.5", [])
    ax = fig.axes[0]
    ax.axhline(0.5, color=C2, ls=":", lw=1)
    ax.text(3.6, 0.55, "bias", color=C2)
    ax.annotate("", (0.0, 2.0), (0.0, 0.5), arrowprops={"arrowstyle": "<->", "color": C2})
    ax.text(0.05, 1.2, "amplitude", color=C2)
    ax.annotate("", (2.0, -1.4), (0.0, -1.4), arrowprops={"arrowstyle": "<->", "color": C2})
    ax.text(0.6, -1.33, r"period = $2\pi$ / frequency", color=C2)
    ax.set_ylim(-1.7, 2.4)
    return fig


@figure("pulse")
def pulse() -> plt.Figure:
    t = np.linspace(0, 4, 4001)
    on = ((t - 0.5) % 1.0 < 0.3) & (t >= 0.5)
    fig = _source_plot(t, np.where(on, 1.0, 0.0), "Pulse Generator: period 1, duty 30 %, delay 0.5", [])
    ax = fig.axes[0]
    ax.annotate("", (1.5, 1.15), (0.5, 1.15), arrowprops={"arrowstyle": "<->", "color": C2})
    ax.text(0.85, 1.2, "period", color=C2)
    ax.annotate("", (1.8, 0.5), (1.5, 0.5), arrowprops={"arrowstyle": "<->", "color": C2})
    ax.text(1.85, 0.47, "duty × period", color=C2)
    ax.text(0.03, 0.08, "delay", color=C2)
    ax.set_ylim(-0.1, 1.45)
    return fig


@figure("zero_order_hold")
def zero_order_hold() -> plt.Figure:
    d = Diagram()
    s = d.add(Sine(frequency=2.0), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.25), name="zoh")
    d.chain(s, z, d.add(Scope()))
    r = run(d, 3.0, solver="rk4", step=0.005)
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    ax.plot(r.t, r["u"], color=INK, lw=1.2, ls="--", label="continuous input")
    ax.step(r.t, r["zoh"], where="post", color=C1, label="ZOH output (T = 0.25 s)")
    ts = np.arange(0, 3.0001, 0.25)
    ax.plot(ts, np.sin(2 * ts), "o", color=C2, ms=5, label="samples")
    ax.set_xlabel("time [s]")
    ax.legend(loc="lower left", ncols=3, fontsize=9)
    ax.set_ylim(-1.6, 1.3)
    fig.tight_layout()
    return fig


@figure("unit_delay")
def unit_delay() -> plt.Figure:
    k = np.arange(0, 9)
    u = np.array([0, 1, 3, 2, 4, 1, 0, 2, 3], dtype=float)
    y = np.concatenate([[0.0], u[:-1]])
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.stem(k - 0.08, u, linefmt=C1, markerfmt="o", basefmt=" ", label="input u[k]")
    ml, _sl, _ = ax.stem(k + 0.08, y, linefmt=C2, markerfmt="s", basefmt=" ", label="output y[k] = u[k−1]")
    ml.set_color(C2)
    ax.set_xlabel("sample k")
    ax.legend(loc="upper right", fontsize=9)
    ax.set_title("Unit Delay: the output is the input from one sample earlier (initial = 0)", color=INK, fontsize=10)
    fig.tight_layout()
    return fig


@figure("integrator_limits")
def integrator_limits() -> plt.Figure:
    d = Diagram()
    src = d.add(Sine(frequency=1.0, amplitude=1.0, bias=0.3), name="u")
    i = d.add(Integrator(upper=1.5, lower=-0.2), name="limited")
    j = d.add(Integrator(), name="free")
    d.chain(src, i, d.add(Scope()))
    d.connect(src, j)
    d.chain(j, d.add(Scope()))
    r = run(d, 12.0)
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    ax.plot(r.t, r["free"], color=INK, ls="--", lw=1.2, label="no limits")
    ax.plot(r.t, r["limited"], color=C1, label="upper = 1.5, lower = −0.2")
    ax.axhline(1.5, color=C2, ls=":", lw=1)
    ax.set_xlabel("time [s]")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("Integrator of 0.3 + sin(t): at a limit, integration stops", color=INK, fontsize=10)
    fig.tight_layout()
    return fig


@figure("pid_structure")
def pid_structure() -> plt.Figure:
    fig, ax = schematic(8.4, 3.4, (0, 13), (-0.4, 4.6))
    ax.text(0.2, 2.1, "e", fontsize=13, color=C1, va="center")
    ax.plot([0.6, 1.6], [2.1, 2.1], color=INK, lw=1.5)
    ax.plot([1.6, 1.6], [0.6, 3.6], color=INK, lw=1.5)
    for y, label, color in ((3.6, "P", C1), (2.1, r"$\frac{I}{s}$", C2), (0.6, r"$D\,\frac{N s}{s+N}$", C3)):
        arrow(ax, (1.6, y), (3.0, y))
        box(ax, 3.0, y - 0.5, 2.6, 1.0, label, color=color, size=13)
        arrow(ax, (5.6, y), (7.0, 2.1 + (y - 2.1) * 0.25))
    box(ax, 7.0, 1.4, 1.4, 1.4, "Σ", size=18)
    arrow(ax, (8.4, 2.1), (9.4, 2.1))
    box(ax, 9.4, 1.5, 1.6, 1.2, "", size=10)
    ax.plot([9.6, 10.0, 10.4, 10.8], [1.75, 1.75, 2.45, 2.45], color=INK, lw=1.4)
    ax.text(10.2, 1.25, "lower / upper", ha="center", va="top", color=INK, fontsize=9)
    arrow(ax, (11.0, 2.1), (12.3, 2.1), text="u")
    ax.text(4.3, 4.35, "proportional · integral · filtered derivative", ha="center", color=INK, fontsize=10)
    return fig


@figure("prelookup")
def prelookup() -> plt.Figure:
    fig, ax = schematic(8.4, 3.6, (0, 13), (-0.6, 4.8))
    ax.text(0.1, 3.0, "engine\nspeed", fontsize=10, color=INK, va="center")
    arrow(ax, (1.3, 3.0), (2.4, 3.0))
    box(ax, 2.4, 2.3, 2.6, 1.4, "Prelookup\nspeed axis", color=C1, size=10)
    ax.text(0.1, 0.6, "load", fontsize=10, color=INK, va="center")
    arrow(ax, (1.3, 0.6), (2.4, 0.6))
    box(ax, 2.4, -0.1, 2.6, 1.4, "Prelookup\nload axis", color=C2, size=10)
    for y_from, color, label in ((3.0, C1, "k₁, f₁"), (0.6, C2, "k₂, f₂")):
        ax.text(5.6, y_from + 0.15, label, fontsize=10, color=INK, ha="center", va="bottom")
        for y_to in (3.6, 1.4):
            arrow(ax, (5.0, y_from), (7.6, y_to + (0.25 if y_from > 2 else -0.25)), color=color)
    box(ax, 7.6, 3.0, 3.0, 1.2, "Interpolation\nignition map", size=10)
    box(ax, 7.6, 0.8, 3.0, 1.2, "Interpolation\nfuel map", size=10)
    arrow(ax, (10.6, 3.6), (12.0, 3.6), text="advance")
    arrow(ax, (10.6, 1.4), (12.0, 1.4), text="fuel")
    ax.text(
        6.5,
        4.6,
        "each axis is searched once; every map over the same axes reuses k and f",
        ha="center",
        color=INK,
        fontsize=10,
    )
    return fig


@figure("rate_transition")
def rate_transition() -> plt.Figure:
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(6.4, 3.6), sharex=True)
    t = np.arange(0, 0.501, 0.01)
    fast = np.sin(2 * np.pi * 2 * t) + 1.5
    slow_t = np.arange(0, 0.501, 0.1)
    top.step(t, fast, where="post", color=C1, lw=1.2, label="10 ms signal")
    held = fast[(np.floor(t / 0.1 + 1e-9) * 10).astype(int)]
    top.step(t, held, where="post", color=C2, lw=2, label="fast_to_slow: sampled at 100 ms")
    top.set_ylabel("value")
    top.legend(loc="upper right", fontsize=8)
    top.set_title("Rate Transition between a 10 ms and a 100 ms task", color=INK, fontsize=10)
    slow = np.array([1.0, 2.0, 1.5, 3.0, 2.5, 2.0])
    bottom.step(slow_t, slow, where="post", color=C3, lw=2, label="100 ms signal")
    delayed = np.concatenate([[0.0], slow[:-1]])
    bottom.step(slow_t, delayed, where="post", color=C4, lw=2, ls="--", label="slow_to_fast: one slow period later")
    bottom.set_ylabel("value")
    bottom.set_xlabel("time [s]")
    bottom.legend(loc="upper left", fontsize=8)
    bottom.set_xlim(0, 0.5)
    for ax in (top, bottom):
        for x in slow_t:
            ax.axvline(x, color=INK, lw=0.6, ls=":")
    fig.tight_layout()
    return fig


@figure("pid_anti_windup")
def pid_anti_windup() -> plt.Figure:
    def response(aw: str):  # type: ignore[no-untyped-def]
        d = Diagram()
        r = d.add(Step(time=0.0, final=1.2), name="r")
        e = d.add(Sum(signs="+-"), name="e")
        c = d.add(PID(P=1.0, I=5.0, upper=1.5, lower=-1.5, anti_windup=aw), name="pid")
        p = d.add(TransferFunction(numerator=[1.0], denominator=[1.0, 1.0]), name="y")
        d.connect(r, (e, 0))
        d.connect(p, (e, 1))
        d.chain(e, c, p, d.add(Scope()))
        return run(d, 8.0, solver="rk4", step=0.005)

    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    for aw, color, label in (
        ("none", C2, "none"),
        ("clamping", C1, "clamping"),
        ("back_calculation", C3, "back_calculation"),
    ):
        res = response(aw)
        ax.plot(res.t, res["y"], color=color, label=f"anti_windup = {label}")
    ax.axhline(1.2, color=INK, ls=":", lw=1)
    ax.text(7.9, 1.23, "setpoint", color=INK, ha="right", va="bottom", fontsize=9)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("plant output")
    ax.set_title("Output limited to ±1.5: without anti-windup the integrator overshoots", color=INK, fontsize=10)
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    return fig


@figure("discrete_integrator")
def discrete_integrator() -> plt.Figure:
    def seq(method: str):  # type: ignore[no-untyped-def]
        d = Diagram()
        i = d.add(DiscreteIntegrator(sample_time=1.0, method=method), name="i")
        d.chain(d.add(Constant(value=1.0)), i, d.add(Scope()))
        return run(d, 5.0)

    f, b = seq("forward_euler"), seq("backward_euler")
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.stem(f.t - 0.07, f["i"], linefmt=C1, markerfmt="o", basefmt=" ", label="forward_euler: y[k] = x[k]")
    ml, _, _ = ax.stem(
        b.t + 0.07, b["i"], linefmt=C2, markerfmt="s", basefmt=" ", label="backward_euler: y[k] = x[k] + K·T·u[k]"
    )
    ml.set_color(C2)
    ax.set_xlabel("time [s]  (T = 1 s, input u = 1)")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    return fig


@figure("transfer_function")
def transfer_function() -> plt.Figure:
    d = Diagram()
    u = d.add(Step(time=0.0), name="u")
    g1 = d.add(TransferFunction(numerator=[1.0], denominator=[0.5, 1.0]), name="first")
    g2 = d.add(TransferFunction(numerator=[4.0], denominator=[1.0, 0.8, 4.0]), name="second")
    d.chain(u, g1, d.add(Scope()))
    d.connect(u, g2)
    d.chain(g2, d.add(Scope()))
    r = run(d, 8.0, rtol=1e-8)
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    ax.plot(r.t, r["first"], color=C1, label=r"$\frac{1}{0.5 s + 1}$  (num [1], den [0.5 1])")
    ax.plot(r.t, r["second"], color=C2, label=r"$\frac{4}{s^2 + 0.8 s + 4}$  (num [4], den [1 0.8 4])")
    ax.axvline(0.5, color=C1, ls=":", lw=1)
    ax.text(0.55, 0.15, "τ = 0.5 s\n(63 %)", color=C1, fontsize=9)
    ax.set_xlabel("time [s]")
    ax.set_title("Unit step responses", color=INK, fontsize=10)
    ax.legend(loc="lower right", fontsize=10)
    fig.tight_layout()
    return fig


@figure("from_workspace")
def from_workspace() -> plt.Figure:
    tp = np.array([0, 1, 2, 3, 4.0])
    yp = np.array([0, 2, 1, 3, 3.0])
    t = np.linspace(0, 4.5, 500)
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.plot(t, np.interp(t, tp, yp), color=C1, label="interpolation = linear")
    ax.step(np.append(tp, 4.5), np.append(yp, 3.0), where="post", color=C2, label="interpolation = hold")
    ax.plot(tp, yp, "o", color=INK, ms=5, label="data points")
    ax.set_xlabel("time [s]")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    return fig


@figure("switch")
def switch() -> plt.Figure:
    fig, ax = schematic(6.4, 2.8, (0, 10), (-0.4, 3.8))
    for y, lab, color in (
        (3.0, "input 0  (passed when the condition holds)", C1),
        (1.7, "input 1  (control)", C4),
        (0.4, "input 2  (passed otherwise)", C2),
    ):
        arrow(ax, (0.2, y), (5.0, 1.7 + (y - 1.7) * 0.4), color)
        ax.text(0.2, y + 0.12, lab, color=color, fontsize=9, va="bottom")
    box(ax, 5.0, 0.9, 1.6, 1.6, "switch", size=10)
    arrow(ax, (6.6, 1.7), (8.4, 1.7), text="y")
    ax.text(5.8, 0.35, "criterion: u1 >= threshold", ha="center", color=INK, fontsize=9)
    return fig


@figure("mux_demux")
def mux_demux() -> plt.Figure:
    fig, ax = schematic(7.6, 2.6, (0, 12), (-0.2, 3.6))
    for i, (lab, color) in enumerate((("a (width 1)", C1), ("b (width 2)", C2))):
        y = 2.6 - i * 1.4
        arrow(ax, (0.2, y), (2.4, 1.6 + (0.5 - i) * 0.8), color)
        ax.text(0.2, y + 0.1, lab, color=color, fontsize=9, va="bottom")
    box(ax, 2.4, 0.6, 0.6, 2.0, "", size=9)
    ax.text(2.7, 0.35, "Mux", ha="center", color=INK, fontsize=9)
    arrow(ax, (3.0, 1.6), (6.4, 1.6), text="[a, b₁, b₂]  (width 3)")
    box(ax, 6.4, 0.6, 0.6, 2.0, "", size=9)
    ax.text(6.7, 0.35, "Demux (3 outputs)", ha="center", color=INK, fontsize=9)
    for i, lab in enumerate(("a", "b₁", "b₂")):
        y = 2.3 - i * 0.7
        arrow(ax, (7.0, y), (9.0, y), C3)
        ax.text(9.15, y, lab, color=C3, va="center", fontsize=10)
    return fig


# Sequential single-hue ramp (light -> dark blue) for magnitude, e.g. a calibration map.
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


@figure("lookup_1d")
def lookup_1d() -> plt.Figure:
    from nervus.blocks.lookup import locate

    bp = np.array([0.0, 1.0, 2.0, 3.0])
    tab = np.array([0.0, 2.0, 1.5, 3.0])
    x = np.linspace(-0.7, 3.7, 900)

    def lut(interp: str, extrap: str) -> np.ndarray:
        i, f = locate(x, bp, interp, extrap)
        return tab[i] + f * (tab[i + 1] - tab[i])

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 3.0), sharey=True)
    a1.plot(x, lut("linear", "clip"), color=C1, label="linear")
    a1.plot(x, lut("nearest", "clip"), color=C2, label="nearest")
    a1.plot(x, lut("flat", "clip"), color=C3, label="flat")
    a1.set_title("interpolation (extrapolation = clip)", color=INK, fontsize=10)
    a2.plot(x, lut("linear", "clip"), color=C1, label="clip")
    a2.plot(x, lut("linear", "linear"), color=C2, ls="--", label="linear")
    a2.set_title("extrapolation (interpolation = linear)", color=INK, fontsize=10)
    for ax in (a1, a2):
        ax.plot(bp, tab, "o", color=INK, ms=6, label="table points")
        ax.axvspan(-0.7, 0, color=GRID, lw=0)
        ax.axvspan(3, 3.7, color=GRID, lw=0)
        ax.set_xlabel("input u")
        ax.legend(loc="upper left", fontsize=9)
    a1.set_ylabel("output y")
    fig.tight_layout()
    return fig


@figure("lookup_2d")
def lookup_2d() -> plt.Figure:
    from matplotlib.colors import LinearSegmentedColormap

    from nervus.blocks import Lookup2D

    rpm = np.array([800, 1500, 2500, 3500, 4500, 6000.0])
    load = np.array([10, 30, 50, 70, 90.0])
    advance = np.array(
        [
            [12, 10, 8, 6, 4],
            [20, 17, 14, 11, 8],
            [28, 25, 21, 17, 13],
            [33, 30, 26, 21, 16],
            [36, 33, 28, 23, 18],
            [38, 35, 30, 25, 20.0],
        ]
    )
    blk = Lookup2D(row_breakpoints=rpm, column_breakpoints=load, table=advance)
    blk.configure([1, 1], [1])
    rr, ll = np.meshgrid(np.linspace(800, 6000, 160), np.linspace(10, 90, 120), indexing="ij")
    zz = np.array(
        [
            [
                blk.outputs(0, [np.array([r]), np.array([lv])], np.zeros(0), np.zeros(0))[0][0]
                for r, lv in zip(rrow, lrow, strict=True)
            ]
            for rrow, lrow in zip(rr, ll, strict=True)
        ]
    )
    cmap = LinearSegmentedColormap.from_list("seq", SEQ_BLUE)
    fig, ax = plt.subplots(figsize=(6.6, 3.4))
    mesh = ax.pcolormesh(ll, rr, zz, cmap=cmap, shading="auto")
    lv, rv = np.meshgrid(load, rpm)
    ax.plot(lv, rv, "o", color="#ffffff", ms=4, mec=INK, mew=0.6)
    cb = fig.colorbar(mesh, ax=ax)
    cb.set_label("ignition advance [°]", color=INK)
    cb.outline.set_edgecolor(INK)
    ax.set_xlabel("input 1: load [%]  (column_breakpoints)")
    ax.set_ylabel("input 0: speed [rpm]\n(row_breakpoints)")
    ax.set_title("Ignition map: bilinear between table points (dots)", color=INK, fontsize=10)
    ax.grid(False)
    fig.tight_layout()
    return fig


@figure("dead_zone")
def dead_zone() -> plt.Figure:
    u = np.linspace(-2, 2, 400)
    y = np.where(u > 0.5, u - 0.5, np.where(u < -0.5, u + 0.5, 0.0))
    fig, ax = plt.subplots(figsize=(4.4, 2.8))
    ax.plot(u, y, color=C1)
    ax.axvspan(-0.5, 0.5, color=C2 + "22", lw=0)
    ax.text(0, 0.9, "dead zone\n[start, end]", ha="center", color=C2, fontsize=9)
    ax.set_xlabel("input u")
    ax.set_ylabel("output y")
    fig.tight_layout()
    return fig


def _discrete_run(block, src, t_final: float, ts: float):  # type: ignore[no-untyped-def]
    d = Diagram()
    s = d.add(src, name="u")
    z = d.add(ZeroOrderHold(sample_time=ts), name="zoh")
    b = d.add(block, name="y")
    d.chain(s, z, b, d.add(Scope()))
    return run(d, t_final, solver="rk4", step=ts / 10)


@figure("relay")
def relay() -> plt.Figure:
    r = _discrete_run(Relay(on_point=0.5, off_point=-0.5), Sine(amplitude=1.0, frequency=1.5), 8.0, 0.02)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 3.0), gridspec_kw={"width_ratios": [1, 1.6]})
    a1.plot([-1.2, 0.5, 0.5, 1.2], [0, 0, 1, 1], color=C1)
    a1.plot([1.2, -0.5, -0.5, -1.2], [1, 1, 0, 0], color=C2, ls="--")
    a1.annotate("", (0.5, 0.6), (0.5, 0.35), arrowprops={"arrowstyle": "-|>", "color": C1})
    a1.annotate("", (-0.5, 0.4), (-0.5, 0.65), arrowprops={"arrowstyle": "-|>", "color": C2})
    a1.text(0.55, 0.15, "on_point", color=C1, fontsize=9)
    a1.text(-1.15, 0.82, "off_point", color=C2, fontsize=9)
    a1.set_xlabel("input u")
    a1.set_ylabel("output")
    a1.set_title("Hysteresis", color=INK, fontsize=10)
    a2.plot(r.t, r["u"], color=INK, ls="--", lw=1.2, label="input")
    a2.step(r.t, r["y"], where="post", color=C1, label="relay output")
    a2.axhline(0.5, color=C1, ls=":", lw=1)
    a2.axhline(-0.5, color=C2, ls=":", lw=1)
    a2.set_xlabel("time [s]")
    a2.legend(loc="lower left", fontsize=9, ncols=2)
    a2.set_ylim(-1.5, 1.4)
    fig.tight_layout()
    return fig


@figure("rate_limiter")
def rate_limiter() -> plt.Figure:
    d = Diagram()
    s = d.add(Pulse(amplitude=1.0, period=4.0, duty=50.0, delay=0.5), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="zoh")
    b = d.add(RateLimiter(rising_rate=1.0, falling_rate=-2.0), name="y")
    d.chain(s, z, b, d.add(Scope()))
    r = run(d, 6.0, solver="rk4", step=0.01)
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.step(r.t, r["zoh"], where="post", color=INK, ls="--", lw=1.2, label="input")
    ax.plot(r.t, r["y"], color=C1, label="rising_rate = 1/s, falling_rate = −2/s")
    ax.set_xlabel("time [s]")
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    return fig


def _bool_figure(t: np.ndarray, rows: list[tuple[str, np.ndarray, str]], title: str) -> plt.Figure:
    fig, axes = plt.subplots(len(rows), 1, figsize=(6.4, 0.9 + 0.75 * len(rows)), sharex=True)
    for ax, (label, y, color) in zip(axes, rows, strict=True):
        ax.step(t, y, where="post", color=color)
        ax.set_ylim(-0.25, max(1.25, float(np.max(y)) + 0.25))
        ax.set_ylabel(label, rotation=0, ha="right", va="center", fontsize=9)
        ax.set_yticks([0, 1] if np.max(y) <= 1 else [0, int(np.max(y))])
    axes[0].set_title(title, color=INK, fontsize=10)
    axes[-1].set_xlabel("sample k")
    fig.tight_layout()
    return fig


def _bool_input() -> np.ndarray:
    return np.array([0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0], dtype=float)


def _seq(block) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # type: ignore[no-untyped-def]
    from nervus.blocks import FromWorkspace

    u = _bool_input()
    k = np.arange(u.size, dtype=float)
    d = Diagram()
    s = d.add(FromWorkspace(time=k, data=u, interpolation="hold"), name="u")
    z = d.add(ZeroOrderHold(sample_time=1.0), name="zoh")
    b = d.add(block, name="y")
    d.chain(s, z, b, d.add(Scope()))
    r = run(d, float(u.size - 1), solver="rk4", step=1.0)
    return r.t, r["zoh"], r["y"]


@figure("edge_detect")
def edge_detect() -> plt.Figure:
    t, u, rising = _seq(EdgeDetect(direction="rising"))
    _, _, falling = _seq(EdgeDetect(direction="falling"))
    return _bool_figure(
        t,
        [("input", u, INK), ("rising", rising, C1), ("falling", falling, C2)],
        "Edge Detect: a one-sample pulse on each change",
    )


@figure("debounce")
def debounce() -> plt.Figure:
    t, u, y = _seq(Debounce(on_samples=3, off_samples=2))
    return _bool_figure(
        t, [("input", u, INK), ("output", y, C1)], "Debounce: on after 3 true samples, off after 2 false samples"
    )


@figure("counter")
def counter() -> plt.Figure:
    from nervus.blocks import Counter

    t, u, y = _seq(Counter(limit=5, at_limit="wrap"))
    return _bool_figure(
        t,
        [("count enable", u, INK), ("count", y, C1)],
        "Counter (limit = 5, wrap): counts samples while the input is true",
    )


@figure("discrete_filter")
def discrete_filter() -> plt.Figure:
    rng = np.random.default_rng(3)
    t = np.arange(0, 3, 0.01)
    noisy = np.where(t >= 0.5, 1.0, 0.0) + 0.15 * rng.standard_normal(t.size)
    from nervus.blocks import FromWorkspace

    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.plot(t, noisy, color=INK, lw=0.8, alpha=0.7, label="noisy sensor")
    for tau, color in ((0.05, C1), (0.25, C2)):
        d = Diagram()
        s = d.add(FromWorkspace(time=t, data=noisy, interpolation="hold"), name="u")
        z = d.add(ZeroOrderHold(sample_time=0.01), name="zoh")
        b = d.add(DiscreteFilter(time_constant=tau), name="y")
        d.chain(s, z, b, d.add(Scope()))
        r = run(d, float(t[-1]), solver="rk4", step=0.01)
        ax.plot(r.t, r["y"], color=color, label=f"τ = {tau} s")
    ax.set_xlabel("time [s]  (T = 10 ms)")
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    return fig


@figure("chart_engine_modes")
def chart_engine_modes() -> plt.Figure:
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    ax.set_xlim(0, 12)
    ax.set_ylim(0.2, 4.0)
    ax.axis("off")
    states = {"off": (1.0, 2.0), "cranking": (5.0, 2.0), "running": (9.0, 2.0)}
    notes = {
        "off": "entry: mode = 0; fuel = 0",
        "cranking": "entry: mode = 1\nduring: fuel = 1",
        "running": "entry: mode = 2",
    }
    colors = {"off": INK, "cranking": C2, "running": C3}
    for name, (x, y) in states.items():
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                2.2,
                1.3,
                boxstyle="round,pad=0.05,rounding_size=0.3",
                ec=colors[name],
                fc=colors[name] + "18",
                lw=1.8,
            )
        )
        ax.text(x + 1.1, y + 0.95, name, ha="center", va="center", fontsize=12, color=colors[name], weight="bold")
        ax.text(x + 1.1, y + 0.4, notes[name], ha="center", va="center", fontsize=8, color=INK)

    def arrow(a: tuple[float, float], b: tuple[float, float], label: str, rad: float, ly: float) -> None:
        ax.add_patch(
            FancyArrowPatch(
                a, b, connectionstyle=f"arc3,rad={rad}", arrowstyle="-|>", mutation_scale=13, lw=1.4, color=INK
            )
        )
        ax.text((a[0] + b[0]) / 2, ly, label, ha="center", fontsize=8.5, color=C1)

    arrow((3.2, 2.95), (5.0, 2.95), "key > 0.5", -0.35, 3.45)
    arrow((7.2, 2.95), (9.0, 2.95), "rpm > 600", -0.35, 3.45)
    arrow((5.0, 2.15), (3.2, 2.15), "after(300) / fuel = 0", -0.35, 1.72)
    arrow((10.1, 2.0), (2.1, 2.0), "key < 0.5 or rpm < 300", -0.28, 0.42)
    ax.plot([0.55], [2.65], "o", color=INK, ms=7)
    ax.annotate("", (1.0, 2.65), (0.6, 2.65), arrowprops={"arrowstyle": "-|>", "color": INK})
    ax.text(0.35, 3.0, "initial", fontsize=8, color=INK)
    fig.tight_layout()
    return fig


@figure("idle_speed_control")
def idle_speed_control() -> plt.Figure:
    import importlib.util

    spec = importlib.util.spec_from_file_location("idle", ROOT / "examples" / "idle_speed_control.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = mod.build()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.2, 4.6), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for alpha, color, label in (
        (0.0, C2, "feedback only (alpha_ac = 0)"),
        (3.0, C1, "with AC feed-forward (alpha_ac = 3°)"),
    ):
        r = simulate(d, params={"alpha_ac": alpha})
        a1.plot(r.t, r["engine_rpm"], color=color, label=label)
        a2.plot(r.t, r["throttle_actuator"], color=color)
    a1.axvline(5.0, color=INK, ls=":", lw=1)
    a1.text(5.05, 870, "AC compressor on", color=INK, fontsize=9)
    a1.axvline(4.8, color=C1, ls=":", lw=1)
    a1.set_ylabel("engine speed [rpm]")
    a1.set_ylim(650, 900)
    a1.set_xlim(3.5, 12)
    a1.legend(loc="lower right", fontsize=9)
    a2.set_ylabel("throttle [°]")
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("datatype_conversion")
def datatype_conversion() -> plt.Figure:
    from nervus.core.datatypes import parse_datatype

    t = np.linspace(0, 1, 2000)
    u = 160 * np.sin(2 * np.pi * t)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 3.0))
    i8 = parse_datatype("int8")
    a1.plot(t, u, color=INK, ls="--", lw=1.2, label="input (double)")
    a1.plot(t, i8.quantize(u), color=C1, label="int8, saturate")
    a1.plot(t, i8.quantize(u, overflow="wrap"), color=C2, lw=1.4, label="int8, wrap")
    a1.set_title("Overflow: int8 holds -128 … 127", color=INK, fontsize=10)
    a1.set_xlabel("time [s]")
    a1.legend(loc="lower left", fontsize=8)
    x = np.linspace(0, 1, 2000)
    v = 0.37 * np.sin(2 * np.pi * x) + 0.4
    fx = parse_datatype("fixdt(0, 8, 4)")
    a2.plot(x, v, color=INK, ls="--", lw=1.2, label="input (double)")
    a2.plot(x, fx.quantize(v), color=C1, label="fixdt(0, 8, 4): steps of 1/16")
    a2.set_title("Resolution: fixed point with 4 fraction bits", color=INK, fontsize=10)
    a2.set_xlabel("time [s]")
    a2.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    return fig


@figure("can_frame")
def can_frame() -> plt.Figure:
    from matplotlib.patches import Rectangle

    from nervus.can import load_dbc

    msg = load_dbc(ROOT / "examples" / "data" / "vehicle.dbc")["EngineData"]
    colors = [C1, C2, C3, C4, "#e87ba4"]
    fig, ax = plt.subplots(figsize=(9.0, 3.4))
    ax.set_xlim(-0.6, 8.4)
    ax.set_ylim(-2.3, 8.6)
    ax.invert_yaxis()
    ax.axis("off")
    owner: dict[int, int] = {}
    for k, s in enumerate(msg.signals):
        for b in range(s.length):
            owner[s.start + b] = k  # Intel signals: consecutive bits from the start bit
    for byte in range(8):
        ax.text(-0.35, byte + 0.5, f"byte {byte}", ha="right", va="center", fontsize=8, color=INK)
        for bit in range(8):
            n = byte * 8 + bit
            x = 7 - bit  # bit 7 on the left, as in DBC editors
            k = owner.get(n)
            fc = colors[k % len(colors)] + "55" if k is not None else "none"
            ax.add_patch(Rectangle((x, byte), 1, 1, fc=fc, ec=INK, lw=0.6))
            ax.text(x + 0.5, byte + 0.5, str(n), ha="center", va="center", fontsize=7, color=INK)
    for k, s in enumerate(msg.signals):
        ax.text(
            8.3,
            0.5 + k * 1.1,
            f"■ {s.name}: start {s.start}, {s.length} bits, ×{s.factor:g} {s.offset:+g} {s.unit}",
            color=colors[k % len(colors)],
            fontsize=9,
            va="center",
        )
    ax.set_xlim(-0.6, 15.5)
    ax.text(4, -0.9, "EngineData (0x100): Intel byte order, bit numbers per byte", ha="center", color=INK, fontsize=10)
    fig.tight_layout()
    return fig


@figure("engine_comparison")
def engine_comparison() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "engine_comparison.nervus")
    res = simulate(d)
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    for name, color, label in (("engine_1_6l", C1, "1.6 l"), ("engine_2_0l", C2, "2.0 l")):
        rpm = res[f"{name}/to_rpm"]
        ax.plot(res.t, rpm, color=color, label=f"{label} engine", lw=2)
        ax.text(res.t[-1] + 0.1, rpm[-1], label, color=INK, va="center", fontsize=9)
    for t_ev, text in ((2.0, "throttle 3° → 6°"), (5.0, "+15 N·m load")):
        ax.axvline(t_ev, color=INK, ls=":", lw=1)
        ax.text(t_ev + 0.08, 2350, text, color=INK, fontsize=9, va="top")
    ax.set_xlim(0, 8.7)
    ax.set_ylim(600, 2400)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("engine speed [rpm]")
    ax.set_title("Same library component, two parameter sets", color=INK, fontsize=10)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    return fig


@figure("lambda_trims")
def lambda_trims() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "lambda_control.nervus")
    res = simulate(d)
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(6.4, 4.2), sharex=True, height_ratios=[1, 1.3])
    top.plot(res.t, res["lambda_cyl"], color=C1, lw=2)
    top.axhline(1.0, color=INK, ls=":", lw=1)
    top.set_ylabel("lambda")
    top.set_ylim(0.97, 1.07)
    top.set_title("6 % unmetered air at 12 s: the STFT reacts, the LTFT learns", color=INK, fontsize=10)
    for name, color, label in (("ECU/stft_pct", C2, "STFT"), ("ECU/ltft_pct", C3, "LTFT")):
        y = res[name]
        bottom.plot(res.t, y, color=color, lw=2, label=label)
        bottom.text(res.t[-1] + 0.4, y[-1], label, color=INK, va="center", fontsize=9)
    for ax in (top, bottom):
        ax.axvline(12.0, color=INK, ls=":", lw=1)
    bottom.text(12.3, 6.6, "leak", color=INK, fontsize=9, va="top")
    bottom.set_ylabel("fuel trim [%]")
    bottom.set_xlabel("time [s]")
    bottom.set_xlim(0, 43)
    bottom.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    return fig


@figure("drive_cycle")
def drive_cycle() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "drive_cycle.nervus")
    res = simulate(d)
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(6.4, 4.2), sharex=True, height_ratios=[1.4, 1])
    top.plot(res.t, res["cycle_kmh"], color=INK, ls=":", lw=1.5, label="ECE-15 cycle")
    top.plot(res.t, res["speed_kmh"], color=C1, lw=2, label="car (driver model)")
    top.set_ylabel("speed [km/h]")
    top.set_ylim(0, 58)
    top.legend(loc="upper left", fontsize=9)
    top.set_title("Following the urban drive cycle: 994 m in 195 s", color=INK, fontsize=10)
    bottom.plot(res.t, res["vehicle/wheel_power"] / 1000, color=C2, lw=1.5)
    bottom.axhline(0, color=INK, lw=0.8)
    bottom.text(150, -9, "braking", color=INK, fontsize=9)
    bottom.set_ylabel("wheel power [kW]")
    bottom.set_xlabel("time [s]")
    bottom.set_xlim(0, 195)
    fig.tight_layout()
    return fig


@figure("engine_modes")
def engine_modes() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "engine_modes.nervus")
    case = next(c for c in d.tests if c.name == "overrun fuel cut-off")
    res = simulate(d, case.stop_time, inputs=case.inputs)
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(6.4, 4.8), sharex=True, height_ratios=[1.2, 0.8, 1.2])
    a1.plot(res.t, res["rpm_10ms"], color=C1, lw=2)
    for y, text in ((1500, "fuel cut-off above"), (1100, "fuel back below")):
        a1.axhline(y, color=INK, ls=":", lw=1)
        a1.text(1.6, y + 60, f"{text} {y} rpm", color=INK, fontsize=8, ha="left")
    a1.set_ylabel("rpm")
    a1.set_title("Overrun fuel cut-off: no fuel while coasting", color=INK, fontsize=10)
    a2.plot(res.t, res["pedal_10ms"], color=C2, lw=2)
    a2.set_ylabel("pedal [%]")
    names = ["off", "crank", "crank_fail", "idle", "run", "fuel_cut", "stalled"]
    a3.step(res.t, res["mode"], where="post", color=C3, lw=2, label="mode")
    a3.step(res.t, res["fuel"] * 5.4, where="post", color=C4, lw=1.5, ls="--", label="fuel on")
    a3.set_yticks(range(6), names[:6])
    a3.set_ylim(-0.4, 5.8)
    a3.legend(loc="lower right", fontsize=8)
    a3.set_xlabel("time [s]")
    a3.set_xlim(0, 10)
    fig.tight_layout()
    return fig


@figure("gear_shift")
def gear_shift() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "gear_shift.nervus")
    res = simulate(d)
    fig, (a0, a1, a2, a3) = plt.subplots(4, 1, figsize=(6.4, 6.0), sharex=True, height_ratios=[0.8, 1, 1, 1])
    a0.plot(res.t, res["pedal"], color=C1, lw=2)
    a0.plot(res.t, res["grade"], color=C2, lw=2)
    a0.text(12, 30, "pedal", color=INK, fontsize=8)
    a0.text(31, 20, "road grade", color=INK, fontsize=8)
    a0.set_ylabel("%")
    a0.set_title("Launch, cruise, kickdown, hill", color=INK, fontsize=10)
    a1.plot(res.t, res["speed"], color=C1, lw=2)
    a1.set_ylabel("speed [km/h]")
    a2.plot(res.t, res["rpm"], color=C3, lw=2)
    a2.set_ylabel("engine [rpm]")
    a3.step(res.t, res["gear"], where="post", color=C4, lw=2)
    a3.set_yticks(range(1, 6))
    a3.set_ylabel("gear")
    for t, y, text in ((0.5, 4.2, "full throttle:\nlate upshifts"), (13, 4.2, "20 %: up to 5th"),
                       (22.5, 1.6, "kickdown"), (38.5, 3.0, "hill")):  # fmt: skip
        a3.text(t, y, text, color=INK, fontsize=8)
    a3.set_xlabel("time [s]")
    a3.set_xlim(0, 45)
    fig.tight_layout()
    return fig


@figure("diagnostic_monitor")
def diagnostic_monitor() -> plt.Figure:
    from nervus.blocks import DiagnosticMonitor, Inport, Outport

    ts, n = 0.01, 60  # four operation cycles of 0.6 s
    d = Diagram()
    mon = d.add(DiagnosticMonitor(dtc="P0300", threshold=5.0, cycle_input=True, sample_time=ts), name="m")
    for k, name in enumerate(("fault", "cycle")):
        d.connect(d.add(Inport(port=k + 1), name=name), (mon, k))
    for k, name in enumerate(("mil", "pending", "confirmed")):
        d.connect((mon, k), d.add(Outport(port=k + 1), name=name))
    rng = np.random.default_rng(7)
    fault, cycle = [], []
    for failing in (True, True, False, False):
        f = (rng.random(n) < (0.85 if failing else 0.08)).astype(float)
        f[:5] = 0.0
        c = np.zeros(n)
        c[1:3] = 1.0
        fault += f.tolist()
        cycle += c.tolist()
    t = np.arange(len(fault)) * ts
    res = simulate(d, t[-1], solver="rk4", step=ts,
                   inputs={"fault": (t, np.c_[fault]), "cycle": (t, np.c_[cycle])})  # fmt: skip
    fig, axes = plt.subplots(4, 1, figsize=(6.4, 4.2), sharex=True)
    rows = (("fault", fault, C1), ("pending", res["pending"], C2), ("confirmed", res["confirmed"], C3),
            ("MIL", res["mil"], C4))  # fmt: skip
    for ax, (label, y, color) in zip(axes, rows, strict=True):
        ax.step(t, y, where="post", color=color, lw=1.5 if label == "fault" else 2)
        ax.set_ylim(-0.2, 1.3)
        ax.set_yticks([0, 1])
        ax.set_ylabel(label, rotation=0, ha="right", va="center")
        for k in range(1, 4):
            ax.axvline(k * n * ts, color=INK, ls=":", lw=1)
    for k, text in enumerate(("cycle 1: fails", "cycle 2: fails", "cycle 3: passes", "cycle 4: passes")):
        axes[0].text((k + 0.5) * n * ts, 1.15, text, ha="center", va="bottom", color=INK, fontsize=8)
    axes[-1].set_xlabel("time [s]")
    axes[-1].set_xlim(0, t[-1])
    fig.tight_layout()
    return fig


@figure("circuits")
def circuits() -> plt.Figure:
    sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))
    import circuits as ex  # type: ignore[import-not-found]

    from nervus.analysis import linearize

    fig, ((a1, a2), (a3, a4)) = plt.subplots(2, 2, figsize=(8.6, 5.6))
    r = simulate(ex.rectifier())
    a1.plot(r.t * 1e3, r["V(in)"], color=C1, lw=1.5, label="V(in)")
    a1.plot(r.t * 1e3, r["V(out)"], color=C2, lw=2, label="V(out)")
    a1.set_title("Rectifier with smoothing capacitor", color=INK, fontsize=10)
    a1.set_xlabel("time [ms]")
    a1.set_ylabel("V")
    a1.set_ylim(-11, 15)
    a1.legend(loc="upper right", ncol=2, fontsize=8)
    for k, (fly, color, label) in enumerate(((True, C3, "with flyback diode"), (False, C2, "without"))):
        rr = simulate(ex.relay_driver(flyback=fly))
        a2.plot(rr.t * 1e3, rr["V(col)"], color=color, lw=2 - k * 0.5, label=label)
    a2.set_title("Relay driver: collector voltage", color=INK, fontsize=10)
    a2.set_xlabel("time [ms]")
    a2.set_ylabel("V")
    a2.legend(loc="upper left", fontsize=8)
    b = simulate(ex.buck_converter())
    a3.plot(b.t * 1e3, b["V(out)"], color=C1, lw=1.5)
    a3.axhline(0.42 * 12, color=INK, ls=":", lw=1)
    a3.text(0.2, 0.42 * 12 + 0.25, "D · V_in = 5.04 V", color=INK, fontsize=8)
    a3.set_title("Buck converter 12 V → 5 V (20 kHz)", color=INK, fontsize=10)
    a3.set_xlabel("time [ms]")
    a3.set_ylabel("V(out) [V]")
    lin = linearize(ex.active_filter())
    w, mag, _ = lin.bode(2 * np.pi * np.logspace(1, 5, 300))
    a4.semilogx(w / (2 * np.pi), mag, color=C3, lw=2)
    a4.axhline(-3, color=INK, ls=":", lw=1)
    a4.axvline(1000, color=INK, ls=":", lw=1)
    a4.text(1100, -10, "−3 dB at 1 kHz", color=INK, fontsize=8)
    a4.set_title("Sallen-Key low-pass: Bode magnitude", color=INK, fontsize=10)
    a4.set_xlabel("frequency [Hz]")
    a4.set_ylabel("gain [dB]")
    a4.set_ylim(-60, 5)
    fig.tight_layout()
    return fig


@figure("physical_models")
def physical_models() -> plt.Figure:
    from nervus import load

    ex = Path(__file__).parent.parent / "examples"
    st = simulate(load(ex / "starter_motor.nervus"))
    et = simulate(load(ex / "electronic_throttle.nervus"))
    fig, axes = plt.subplots(3, 2, figsize=(8.6, 6.2), sharex="col")
    (a1, b1), (a2, b2), (a3, b3) = axes
    a1.plot(st.t, st["battery_voltage"], color=C1, lw=2)
    a1.set_ylabel("battery [V]")
    a1.set_title("Cranking: key to start at 0.2 s", color=INK, fontsize=10)
    a2.plot(st.t, st["starter_current"], color=C2, lw=2)
    a2.set_ylabel("starter [A]")
    a3.plot(st.t, st["engine_rpm"], color=C3, lw=2)
    a3.set_ylabel("engine [rpm]")
    a3.set_xlabel("time [s]")
    b1.plot(et.t, et["pedal_target"], color=INK, lw=1.2, ls="--")
    b1.plot(et.t, et["throttle_deg"], color=C1, lw=2)
    b1.text(0.62, 60, "target (dashed)", color=INK, fontsize=8)
    b1.set_ylabel("throttle [°]")
    b1.set_title("Electronic throttle: angle control", color=INK, fontsize=10)
    b2.plot(et.t, et["duty"], color=C2, lw=1.5)
    b2.set_ylabel("H-bridge duty")
    b3.plot(et.t, et["throttle_deg"] - np.ravel(et["pedal_target"]), color=C3, lw=1.5)
    b3.set_ylabel("error [°]")
    b3.set_ylim(-10, 10)
    b3.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("traction_control")
def traction_control() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "traction_control.nervus")
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.4, 4.4), sharex=True)
    for tcs, color, label in ((0.0, C2, "TCS off"), (1.0, C1, "TCS on")):
        r = simulate(d, params={"mu_road": 0.5, "tcs_enable": tcs})
        a1.plot(r.t, np.ravel(r["slip"]) * 100, color=color, lw=2, label=label)
        a2.plot(r.t, r["speed"], color=color, lw=2, label=label)
    a1.axhline(10, color=INK, ls=":", lw=1)
    a1.text(4.6, 13, "target 10 %", color=INK, fontsize=8)
    a1.set_ylabel("slip [%]")
    a1.set_title("Full-throttle launch on a wet road (μ = 0.5)", color=INK, fontsize=10)
    a1.legend(loc="center right", fontsize=8)
    a2.set_ylabel("speed [km/h]")
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("sensor_model")
def sensor_model() -> plt.Figure:
    from nervus.blocks import SensorModel

    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    cases = (("noise σ 0.1, 0.2 resolution", {"noise": 0.1, "resolution": 0.2}, C1),
             ("stuck at 0.4 s", {"fault": "stuck", "fault_time": 0.4}, C2),
             ("short to supply (5 V) at 0.6 s", {"fault": "short_to_supply", "fault_time": 0.6, "y_max": 5.0}, C3),
             ("drift +4/s from 0.3 s", {"fault": "drift", "fault_time": 0.3, "drift_rate": 4.0}, C4))  # fmt: skip
    for label, params, color in cases:
        d = Diagram()
        d.chain(d.add(Sine(amplitude=1.5, frequency=2 * np.pi, bias=2.5), name="u"),
                d.add(SensorModel(**params), name="y"), d.add(Scope()))  # fmt: skip
        r = simulate(d, 1.0, solver="rk4", step=0.01)
        ax.step(r.t, r["y"], where="post", color=color, lw=1.6, label=label)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("sensor output")
    ax.legend(loc="upper left", fontsize=7, ncol=2)
    ax.set_ylim(0, 8.5)
    fig.tight_layout()
    return fig


@figure("ignition_knock")
def ignition_knock() -> plt.Figure:
    from nervus import load

    r = simulate(load(Path(__file__).parent.parent / "examples" / "ignition_knock.nervus"))
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(6.4, 5.0), sharex=True)
    a1.plot(r.t, r["advance"], color=C1, lw=2)
    a1.plot(r.t, r["knock_limit_deg"], color=INK, lw=1.2, ls="--")
    a1.text(0.2, 16.4, "knock limit (dashed)", color=INK, fontsize=8)
    a1.set_ylabel("advance [°]")
    a1.set_title("Low-octane fuel at 4 s: knock control retards the spark", color=INK, fontsize=10)
    a2.plot(r.t, r["knock_sensor"], color=C2, lw=1)
    a2.axhline(0.5, color=INK, ls=":", lw=1)
    a2.set_ylabel("knock sensor")
    a3.plot(r.t, np.ravel(r["torque_factor"]) * 100, color=C3, lw=2)
    a3.set_ylabel("torque [%]")
    a3.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("fuel_film")
def fuel_film() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "fuel_film.nervus")
    on, off = simulate(d), simulate(d, params={"xtau_comp": 0.0})
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(6.4, 5.2), sharex=True)
    a1.plot(off.t, off["lambda"], color=C2, lw=1.2, label="no film compensation")
    a1.plot(on.t, on["lambda"], color=C1, lw=1.5, label="x-tau compensation")
    a1.set_ylabel("lambda")
    a1.set_ylim(0.7, 1.4)
    a1.legend(loc="upper left", fontsize=8, ncol=2)
    a1.set_title("Tip-ins at 20 and 30 s; injectors 8 % lean at idle", color=INK, fontsize=10)
    a2.plot(on.t, on["air"], color=C3, lw=1.5)
    a2.set_ylabel("air [g/s]")
    a3.plot(on.t, on["stft"], color=C4, lw=1, label="short-term trim")
    a3.plot(on.t, on["ltft"], color=C1, lw=2, label="long-term trim (map)")
    a3.set_ylabel("trim [%]")
    a3.set_ylim(-6, 14)
    a3.legend(loc="upper right", fontsize=8, ncol=2)
    a3.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("drive_cycle_block")
def drive_cycle_block() -> plt.Figure:
    from nervus.cycles import cycle, cycle_stats

    fig, axes = plt.subplots(3, 1, figsize=(6.4, 5.0))
    for ax, (name, title, color) in zip(axes, (("wltc3b", "WLTC class 3b", C1), ("ftp75", "FTP-75", C2),
                                               ("ece15", "ECE-15", C3)), strict=True):  # fmt: skip
        t, v = cycle(name)
        st = cycle_stats(name)
        ax.plot(t, v, color=color, lw=1.4)
        ax.set_title(f"{title}: {st['duration_s']:.0f} s, {st['distance_km']:.2f} km", color=INK, fontsize=9)
        ax.set_ylabel("km/h")
    axes[-1].set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("truck")
def truck() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "truck.nervus")
    r, off = simulate(d), simulate(d, params={"exhaust_brake": 0.0})
    fig, (a0, a1, a2, a3) = plt.subplots(4, 1, figsize=(6.4, 6.0), sharex=True, height_ratios=[0.6, 1, 1, 0.8])
    a0.plot(r.t, r["grade"], color=INK, lw=1.5)
    a0.set_ylabel("grade [%]")
    a0.set_title("40 t truck: a 6 % climb, then a 6 % descent", color=INK, fontsize=10)
    a1.plot(off.t, np.minimum(off["speed"], 140), color=C2, lw=1.2, label="no exhaust brake (runs away; clipped)")
    a1.plot(r.t, r["speed"], color=C1, lw=2, label="exhaust brake")
    a1.set_ylabel("speed [km/h]")
    a1.set_ylim(0, 145)
    a1.legend(loc="upper left", fontsize=8)
    a2.plot(r.t, r["rpm"], color=C3, lw=1.5)
    a2.set_ylabel("engine [rpm]")
    a3.step(r.t, r["gear"], where="post", color=C4, lw=2)
    a3.set_ylabel("gear")
    a3.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("can_arbitration")
def can_arbitration() -> plt.Figure:
    from nervus.canbus import CanBus

    b = CanBus("fig", bitrate=125_000)
    queued = [(0.0, 0x300), (0.0, 0x100), (0.0, 0x200), (0.0012, 0x080), (0.0030, 0x100)]
    for t, i in queued:
        b.send(t, i, bytes(8))
    b.receive(0.01, -1)
    ids = sorted({i for _, i in queued})
    colors = dict(zip(ids, (C1, C2, C3, C4), strict=False))
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    for f in b.trace:
        row = ids.index(f.can_id)
        ax.plot([f.queued * 1e3, f.start * 1e3], [row, row], color=GRID, lw=2, ls=":")
        ax.broken_barh([(f.start * 1e3, (f.end - f.start) * 1e3)], (row - 0.3, 0.6), color=colors[f.can_id])
        ax.plot(f.queued * 1e3, row, "v", color=INK, ms=6)
    ax.set_yticks(range(len(ids)), [f"0x{i:03X}" for i in ids])
    ax.invert_yaxis()
    ax.set_xlabel("time [ms]  (▼ queued, dotted: waiting, bar: on the wire)")
    ax.set_title("Arbitration at 125 kbit/s: the lowest identifier goes first", color=INK, fontsize=10)
    fig.tight_layout()
    return fig


@figure("can_network")
def can_network() -> plt.Figure:
    from nervus import load

    d = load(Path(__file__).parent.parent / "examples" / "can_network.nervus")
    bad = simulate(d, 3.0)  # 20 % corrupted frames from 1 s
    off = simulate(d, 3.0, params={"ecm_bus_off": 1.0, "corrupt_rate": 0.0, "corrupt_from": 2.0})
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 5.4), sharex=True, height_ratios=[2, 1, 1.4])
    a0.plot(bad.t, bad["rpm"], color=GRID, lw=3, label="engine")
    a0.plot(bad.t, bad["rpm_display"], color=C1, lw=1.3, label="cluster (E2E rejects corrupted frames)")
    a0.axvspan(1.0, 3.0, color=C2, alpha=0.08)
    a0.set_ylabel("rpm")
    a0.legend(loc="lower left", fontsize=8)
    a0.set_title("20 % corrupted frames from 1 s", color=INK, fontsize=9)
    a1.step(bad.t, np.ravel(bad["e2e_status"]), where="post", color=C3, lw=1.0)
    a1.set_yticks([0, 2, 4], ["ok", "CRC", "sequence"])
    a1.set_title("E2E check status", color=INK, fontsize=9)
    a2.plot(off.t, off["rpm"], color=GRID, lw=3)
    a2.plot(off.t, off["rpm_display"], color=C1, lw=1.3)
    a2.step(off.t, 1000 * np.ravel(off["u0100"]), where="post", color=C2, lw=1.5, label="U0100 pending (×1000)")
    a2.axvline(2.0, color=INK, lw=0.8, ls="--")
    a2.set_title("ECM bus-off at 2 s: 100 ms timeout, needle to 0", color=INK, fontsize=9)
    a2.set_ylabel("rpm")
    a2.legend(loc="center left", fontsize=8)
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("xcp_live")
def xcp_live() -> plt.Figure:
    import tempfile
    import threading

    import can

    from nervus import Diagram
    from nervus.blocks import CanBusSetup, Gain, Outport, Product, Sine, XcpSlave
    from nervus.canbus import bus
    from nervus.core import Variable
    from nervus.xcp import CanTransport, XcpMaster

    d = Diagram("xcp")
    d.workspace.add(Variable("Kp", 2.0, "Controller gain"))
    src = d.add(Sine(amplitude=1.0, frequency=6.0), name="sensor")
    slave = d.add(XcpSlave(measurements="sensor, output", calibrations="Kp"), name="xcp")
    prod = d.add(Product(ops="**"), name="out")
    g = d.add(Gain(gain=1.0), name="g")
    d.connect(src, (prod, 0))
    d.connect((slave, 0), (prod, 1))
    d.chain(prod, g)
    d.connect(src, (slave, 0))
    d.connect(g, (slave, 1))
    d.connect(g, d.add(Outport(port=1), name="y"))
    d.connect((slave, 0), d.add(Outport(port=2), name="kp"))
    d.add(CanBusSetup(interface="virtual", channel="nervus_fig"), name="can")
    d.settings.pacing, d.settings.solver, d.settings.step = 1.0, "rk4", 0.001
    simulate(d, 0.0)
    a2l = d.block("xcp").write_a2l(Path(tempfile.mkdtemp()) / "slave.a2l")
    tool_bus = can.Bus(interface="virtual", channel="nervus_fig")
    reads: list[tuple[float, float]] = []

    def tool() -> None:
        import time

        m = XcpMaster(CanTransport(tool_bus), a2l=a2l, timeout=0.5)
        t0 = time.monotonic()
        time.sleep(0.1)
        m.connect()
        for k in range(30):
            if k == 10:
                m.write("Kp", 4.0)
            if k == 22:
                m.write("Kp", 1.0)
            reads.append((time.monotonic() - t0, float(m.read("output"))))
            time.sleep(0.04)

    th = threading.Thread(target=tool)
    th.start()
    r = simulate(d, 2.2)
    th.join(5)
    tool_bus.shutdown()
    bus("can0").disconnect_hardware()
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(6.4, 3.8), sharex=True, height_ratios=[2, 1])
    a0.plot(r.t, r["y"], color=C1, lw=1.5, label="model output")
    a0.plot(*zip(*reads, strict=True), "o", color=C2, ms=4, label="read by the tool (XCP polling)")
    a0.legend(loc="upper right", fontsize=8)
    a0.set_ylabel("output")
    a0.set_title("Live calibration over XCP on CAN: the tool writes Kp while the model runs", color=INK, fontsize=9)
    a1.step(r.t, r["kp"], where="post", color=C3, lw=2)
    a1.set_ylabel("Kp")
    a1.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


CHART_POWER = """input key, rpm, fault
output mode = 0, mil = 0, stalls = 0
event stalled

state off
  entry: mode = 0
state on parallel
  state engine
    state cranking
      entry: mode = 1
    state running
      entry: mode = 2
  state diagnosis
    state ok
      entry: mil = 0
    state failed
      entry: mil = 1

transition off -> on: key > 0.5
transition on -> off: key < 0.5
transition cranking -> running: rpm > 600
transition running -> cranking: rpm < 300 / stalls += 1; send(stalled)
transition ok -> failed: fault > 0.5 or stalled
transition failed -> ok: fault < 0.5 and after(30)
"""


@figure("chart_hierarchy")
def chart_hierarchy() -> plt.Figure:
    from nervus import Diagram
    from nervus.blocks import FromWorkspace, StateChart, Terminator

    t = np.arange(0, 12.0, 0.1)
    key = (t >= 1.0) & (t < 10.5)
    rpm = np.interp(t, [0, 1, 2.5, 5.0, 5.5, 6.0, 7.5, 12], [0, 0, 800, 800, 150, 150, 800, 800])
    d = Diagram("power")
    c = d.add(StateChart(definition=CHART_POWER, sample_time=0.1, state_output=False), name="chart")
    for k, (name, data) in enumerate((("key", key * 1.0), ("rpm", rpm), ("fault", 0 * t))):
        d.connect(d.add(FromWorkspace(time=t, data=data, interpolation="hold"), name=name), (c, k))
    for k in range(3):
        d.connect((c, k), d.add(Terminator()))
    r = simulate(d, 12.0, solver="rk4", step=0.1)
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 4.6), sharex=True, height_ratios=[1, 1.2, 0.8])
    a0.plot(t, rpm, color=C1, lw=1.5, label="rpm")
    a0.step(t, key * 800, where="post", color=GRID, lw=2, label="key (×800)")
    a0.legend(loc="upper right", fontsize=8)
    a0.set_title("Engine region and diagnosis region, linked by the event 'stalled'", color=INK, fontsize=9)
    a1.step(r.t, r.signal("chart", 0), where="post", color=C2, lw=2)
    a1.set_yticks([0, 1, 2], ["off", "cranking", "running"])
    a1.set_ylabel("mode")
    a2.step(r.t, r.signal("chart", 1), where="post", color=C3, lw=2)
    a2.set_yticks([0, 1], ["ok", "failed"])
    a2.set_ylabel("diagnosis")
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


def _example(name: str):  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent.parent / "examples" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@figure("lab_mass_spring_damper")
def lab_mass_spring_damper() -> plt.Figure:
    d = _example("mass_spring_damper_pid").build()
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(6.4, 4.6), sharex=True, height_ratios=[2, 1])
    for (label, p), color in zip((("P (Kp = 40)", {"Ki": 0.0, "Kd": 0.0}), ("PD (+ Kd = 8)", {"Ki": 0.0}),
                                  ("PID (+ Ki = 30)", {})), (C2, C3, C1), strict=True):  # fmt: skip
        r = simulate(d, 10.0, params=p)
        a0.plot(r.t, r["position"], color=color, lw=1.6, label=label)
        a1.plot(r.t, r["pid"], color=color, lw=1.2)
    a0.axhline(1.0, color=GRID, lw=1, ls="--")
    a0.set_ylabel("position [m]")
    a0.legend(loc="lower right", fontsize=8)
    a0.set_title("Step from 0 to 1 m: P oscillates, PD leaves an error, PID removes it", color=INK, fontsize=9)
    a1.set_ylabel("force [N]")
    a1.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("lab_dc_motor")
def lab_dc_motor() -> plt.Figure:
    m = _example("dc_motor_lab")
    sp, po = m.build_speed(), m.build_position()
    ol, pi = simulate(sp, params={"closed_loop": 0.0}), simulate(sp)
    pd, p_only = simulate(po), simulate(po, params={"Kd_th": 0.0})
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 6.0))
    a0.plot(ol.t, ol["speed"], color=C1, lw=1.6)
    final = float(np.ravel(ol["speed"])[-1])
    a0.axhline(final, color=GRID, ls="--", lw=1)
    a0.axhline(0.632 * final, color=GRID, ls=":", lw=1)
    a0.axvline(0.2, color=GRID, ls=":", lw=1)
    a0.set_title(f"1. Open loop, 5 V step: K = {final / 5:.1f} rad/s/V, τ = 0.10 s (63 % point)", color=INK, fontsize=9)
    a0.set_ylabel("speed [rad/s]")
    a1.plot(pi.t, pi["speed"], color=C3, lw=1.6, label="speed")
    a1.step(pi.t, np.where(pi.t >= 0.1, 50, 0), where="post", color=GRID, lw=1, ls="--", label="reference")
    a1.set_title("2. PI speed control: first order, τ = 34 ms, no overshoot", color=INK, fontsize=9)
    a1.set_ylabel("speed [rad/s]")
    a1.legend(loc="lower right", fontsize=8)
    a2.plot(p_only.t, p_only["angle"], color=C2, lw=1.2, label="P only (Kd = 0)")
    a2.plot(pd.t, pd["angle"], color=C1, lw=1.8, label="PD (ζ = 0.7)")
    a2.set_title("3. Position control: the D term damps the motor", color=INK, fontsize=9)
    a2.set_ylabel("angle [rad]")
    a2.set_xlabel("time [s]")
    a2.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    return fig


@figure("lab_coupled_tanks")
def lab_coupled_tanks() -> plt.Figure:
    m = _example("coupled_tanks")
    d = m.build()
    r = simulate(d)
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(6.4, 4.6), sharex=True, height_ratios=[2, 1])
    t, ref = zip(*m.REFERENCE, strict=True)
    a0.plot(t, ref, color=GRID, lw=1.5, ls="--", label="reference")
    a0.plot(r.t, r["level_2"], color=C1, lw=1.8, label="lower tank")
    a0.plot(r.t, r["level_1"], color=C3, lw=1.2, label="upper tank")
    a0.set_ylabel("level [cm]")
    a0.legend(loc="upper right", fontsize=8)
    a0.set_title("PI level control: 44 s to settle near 15 cm, 62 s at 5 cm", color=INK, fontsize=9)
    a1.plot(r.t, r["voltage"], color=C2, lw=1.4)
    a1.set_ylabel("pump [V]")
    a1.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("lab_inverted_pendulum")
def lab_inverted_pendulum() -> plt.Figure:
    m = _example("inverted_pendulum")
    d = m.build()
    r, fast = simulate(d), simulate(d, params={"K_lqr": m.lqr_gain(q_x=200.0)})
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 5.4), sharex=True)
    a0.plot(r.t, np.ravel(r["angle"]) * 180 / np.pi, color=C1, lw=1.6)
    a0.axvline(3.0, color=GRID, ls=":", lw=1)
    a0.text(3.05, 2.2, "tap", color=INK, fontsize=8)
    a0.set_ylabel("angle [°]")
    a0.set_title("Balancing: start at 3°, a tap at 3 s, the cart moves to 0.3 m at 5 s", color=INK, fontsize=9)
    a1.plot(r.t, np.ravel(r["cart_position"]) * 1000, color=C1, lw=1.6, label="q_x = 50")
    a1.plot(fast.t, np.ravel(fast["cart_position"]) * 1000, color=C3, lw=1.2, label="q_x = 200")
    a1.set_ylabel("cart [mm]")
    a1.legend(loc="center left", fontsize=8)
    a1.annotate("first the wrong way", xy=(5.15, -24), xytext=(6.3, -120), color=INK, fontsize=8,
                arrowprops={"arrowstyle": "->", "color": INK})  # fmt: skip
    a2.plot(r.t, r["motor_force"], color=C2, lw=1.2)
    a2.set_ylabel("force [N]")
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("coolant_fan")
def coolant_fan() -> plt.Figure:
    d = _example("coolant_fan").build()
    r = simulate(d, solver="rk4", step=0.01)
    narrow = simulate(d, solver="rk4", step=0.01, params={"T_off": 97.0})
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 5.0), sharex=True, height_ratios=[0.7, 1.6, 0.8])
    a0.plot(r.t, r["engine_load"], color=INK, lw=1.4)
    a0.set_ylabel("load [%]")
    a0.set_title("Radiator fan with 6 °C hysteresis: 12 switchings in 10 minutes", color=INK, fontsize=9)
    a1.plot(r.t, r["coolant_temp"], color=C1, lw=1.6, label="coolant")
    a1.plot(r.t, r["temp_filtered"], color=C3, lw=1.0, label="filtered sensor")
    for y in (98, 92):
        a1.axhline(y, color=GRID, ls=":", lw=1)
    a1.set_ylabel("°C")
    a1.legend(loc="lower right", fontsize=8)
    a2.step(r.t, r["fan_on"], where="post", color=C2, lw=1.6, label="6 °C hysteresis")
    a2.step(
        narrow.t, np.ravel(narrow["fan_on"]) * 0.8 + 1.4, where="post", color=C4, lw=1.0, label="1 °C (36 switchings)"
    )
    a2.set_yticks([0, 1, 1.4, 2.2], ["off", "on", "off", "on"])
    a2.legend(loc="center left", fontsize=7)
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("fixed_point_sensor")
def fixed_point_sensor() -> plt.Figure:
    m = _example("fixed_point_sensor")
    r = simulate(m.build())
    d = m.build()
    d.block("scale_fix").set_param("output_type", "fixdt(0, 16, 10)")
    bad = simulate(d)
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(6.4, 4.6), sharex=True, height_ratios=[2, 1])
    a0.plot(r.t, r["filtered_dbl"], color=GRID, lw=3, label="double")
    a0.plot(r.t, r["filtered_fix"], color=C1, lw=1.2, label="fixdt(0, 16, 6)")
    a0.plot(bad.t, bad["filtered_fix"], color=C2, lw=1.2, label="fixdt(0, 16, 10): saturates at 64")
    a0.axhline(150, color=INK, ls=":", lw=1)
    a0.text(0.02, 154, "limit 150 kPa", color=INK, fontsize=8)
    a0.set_ylabel("pressure [kPa]")
    a0.legend(loc="lower right", fontsize=8)
    a0.set_title("Manifold pressure in floating and fixed point", color=INK, fontsize=9)
    a1.plot(r.t, np.ravel(r["fixed_minus_double"]) * 1000, color=C3, lw=1)
    a1.axhline(1000 / 64, color=GRID, ls="--", lw=1)
    a1.axhline(-1000 / 64, color=GRID, ls="--", lw=1)
    a1.set_ylabel("fix − double [Pa]")
    a1.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("xcp_calibration")
def xcp_calibration() -> plt.Figure:
    r = _example("xcp_calibration").main(speedup=8.0, interface="virtual")
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(6.4, 4.2), sharex=True, height_ratios=[2, 1])
    a0.plot(r.t, r["set_speed_used"], color=GRID, lw=2, ls="--", label="set speed (calibrated)")
    a0.plot(r.t, r["speed_kmh"], color=C1, lw=1.6, label="speed")
    a0.set_ylabel("km/h")
    a0.legend(loc="lower right", fontsize=8)
    a0.set_title("A tool changes the set speed at 20 s, the gain trim at 35 s and the set speed at 40 s",
                 color=INK, fontsize=9)  # fmt: skip
    a1.step(r.t, r["gain_trim_used"], where="post", color=C3, lw=1.6)
    a1.set_ylabel("gain trim")
    a1.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("obd_ecu")
def obd_ecu() -> plt.Figure:
    r = simulate(_example("obd_ecu").build())
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 5.0), sharex=True, height_ratios=[1, 0.8, 1.2])
    a0.plot(r.t, r["coolant_temp"], color=C1, lw=1.6)
    a0.axhline(40, color=GRID, ls=":", lw=1)
    a0.text(60, 43, "monitor enabled above 40 °C", color=INK, fontsize=8)
    a0.set_ylabel("coolant [°C]")
    a0.set_title("P0302 misfire monitor: pending in the first key cycle, MIL in the second", color=INK, fontsize=9)
    a1.step(r.t, r["misfire_test"], where="post", color=C2, lw=1.0)
    a1.set_ylabel("raw test")
    a1.set_yticks([0, 1], ["pass", "fail"])
    a2.step(r.t, np.ravel(r["pending"]) + 1.2, where="post", color=C3, lw=1.6, label="pending")
    a2.step(r.t, r["mil"], where="post", color=C2, lw=1.6, label="MIL (confirmed)")
    a2.set_yticks([0, 1, 1.2, 2.2], ["off", "on", "no", "yes"])
    for k in (30, 60, 90):
        for ax in (a0, a1, a2):
            ax.axvline(k, color=GRID, lw=1, ls="--")
    a2.text(30.5, 0.4, "key cycle", color=INK, fontsize=8)
    a2.legend(loc="center left", fontsize=8)
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("ntc_divider")
def ntc_divider() -> plt.Figure:
    m = _example("ntc_sensor")
    temps = np.linspace(-40, 150, 400)
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(6.4, 4.6), sharex=True)
    for r_pull, color in ((2490.0, C1), (10e3, C3), (470.0, C2)):
        v = m.v_sig(temps, r_pull)
        a0.plot(temps, v, color=color, lw=1.6, label=f"R_pull = {r_pull / 1000:g} kΩ")
        res = 1.0 / (np.abs(np.gradient(v, temps)) / 5.0 * 4095.0)
        a1.plot(temps, res, color=color, lw=1.6)
    a0.axhline(0.1, color=GRID, ls=":", lw=1)
    a0.axhline(4.9, color=GRID, ls=":", lw=1)
    a0.text(-38, 4.6, "open circuit above 4.9 V", color=INK, fontsize=7)
    a0.text(-38, 0.2, "short to ground below 0.1 V", color=INK, fontsize=7)
    a0.set_ylabel("signal [V]")
    a0.legend(loc="center right", fontsize=8)
    a0.set_title("NTC voltage divider: the pull-up decides where the sensor is accurate", color=INK, fontsize=9)
    a1.set_yscale("log")
    a1.set_ylabel("°C per ADC count")
    a1.set_xlabel("coolant temperature [°C]")
    fig.tight_layout()
    return fig


@figure("ntc_fault")
def ntc_fault() -> plt.Figure:
    d = _example("ntc_sensor").build()
    r = simulate(d, 230.0, params={"fault": 1.0})
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 4.8), sharex=True, height_ratios=[1.4, 1, 0.8])
    a0.plot(r.t, r["true_temp"], color=GRID, lw=3, label="true")
    a0.plot(r.t, r["measured_temp"], color=C2, lw=1.2, label="measured (raw)")
    a0.plot(r.t, r["ecu_temp"], color=C1, lw=1.6, label="used by the ECU")
    a0.set_ylabel("°C")
    a0.legend(loc="upper left", fontsize=8)
    a0.set_title("Open circuit at 200 s: P0118 and the 80 °C substitute value", color=INK, fontsize=9)
    a1.plot(r.t, r["signal_volts"], color=C3, lw=1.4)
    a1.axhline(4.9, color=GRID, ls=":", lw=1)
    a1.set_ylabel("signal [V]")
    a2.step(r.t, r["P0118_pending"], where="post", color=C2, lw=1.6)
    a2.set_yticks([0, 1], ["ok", "P0118"])
    a2.set_xlabel("time [s]")
    a2.set_xlim(150, 230)
    fig.tight_layout()
    return fig


@figure("abs_braking")
def abs_braking() -> plt.Figure:
    m = _example("abs_braking")
    d = m.build()
    on, off = simulate(d, 4.5), simulate(d, 4.5, params={"abs_on": 0.0})
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 5.4), sharex=True, height_ratios=[1.4, 1, 1])
    a0.plot(on.t, on["speed"], color=GRID, lw=3, label="car")
    a0.plot(on.t, on["wheel_speed"], color=C1, lw=1.2, label="wheel, with ABS")
    a0.plot(off.t, off["wheel_speed"], color=C2, lw=1.4, label="wheel, no ABS (locked)")
    a0.set_ylabel("km/h")
    a0.legend(loc="upper right", fontsize=8)
    a0.set_title("Full braking from 100 km/h on a dry road", color=INK, fontsize=9)
    a1.plot(on.t, np.ravel(on["wheel_slip"]) * 100, color=C1, lw=1.0)
    a1.plot(off.t, np.ravel(off["wheel_slip"]) * 100, color=C2, lw=1.2)
    for y in (10, 20):
        a1.axhline(y, color=GRID, ls=":", lw=1)
    a1.set_ylabel("slip [%]")
    a2.plot(on.t, on["brake_torque_out"], color=C1, lw=1.0, label="with ABS")
    a2.plot(off.t, off["brake_torque_out"], color=C2, lw=1.2, label="no ABS")
    a2.set_ylabel("brake [N·m]")
    a2.set_xlabel("time [s]")
    a2.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


@figure("cruise_tolerance")
def cruise_tolerance() -> plt.Figure:
    m = _example("cruise_tolerance")
    a, b = m.study(30.0), m.study(60.0)
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(7.2, 3.2))
    bins = np.linspace(9, 24, 31)
    a0.hist(a.column("settling_s"), bins=bins, color=C1, alpha=0.8, label="Ki = 30")
    a0.hist(b.column("settling_s"), bins=bins, color=C2, alpha=0.6, label="Ki = 60")
    a0.axvline(20, color=INK, ls="--", lw=1)
    a0.text(20.2, a0.get_ylim()[1] * 0.85, "required", color=INK, fontsize=8)
    a0.set_xlabel("settling time [s]")
    a0.set_ylabel("cars (of 200)")
    a0.legend(loc="upper left", fontsize=8)
    a1.scatter(a.column("m"), a.column("settling_s"), s=8, color=C1, label="Ki = 30")
    a1.scatter(b.column("m"), b.column("settling_s"), s=8, color=C2, label="Ki = 60")
    a1.axhline(20, color=INK, ls="--", lw=1)
    a1.set_xlabel("vehicle mass [kg]")
    a1.set_ylabel("settling time [s]")
    fig.suptitle("200 cars each: Ki = 60 is faster on average, but the heavy cars fail", color=INK, fontsize=9)
    fig.tight_layout()
    return fig


@figure("rc_rlc")
def rc_rlc() -> plt.Figure:
    m = _example("rc_rlc")
    rc, rlc = m.build_rc(), m.build_rlc()
    fig, ((a0, a1), (b0, b1)) = plt.subplots(2, 2, figsize=(7.4, 5.0))
    r = simulate(rc)
    a0.plot(r.t * 1000, r["v_out"], color=C1, lw=1.6)
    a0.axhline(5 * 0.632, color=GRID, ls=":", lw=1)
    a0.axvline(1.0, color=GRID, ls=":", lw=1)
    a0.set_title("RC step: 63 % after τ = 1 ms", color=INK, fontsize=9)
    a0.set_xlabel("time [ms]")
    a0.set_ylabel("V")
    for rv, color in ((10.0, C2), (63.2, C3), (200.0, C4)):
        rr = simulate(rlc, params={"R": rv})
        a1.plot(rr.t * 1000, rr["v_out"], color=color, lw=1.4, label=f"R = {rv:g} Ω")
    a1.set_title("RLC step: under-, critically, overdamped", color=INK, fontsize=9)
    a1.set_xlabel("time [ms]")
    a1.legend(loc="lower right", fontsize=7)
    f = np.logspace(1, 4, 300)
    for d, ax, title in ((rc, b0, "RC: −3 dB at 159 Hz"), (rlc, b1, "RLC: +10 dB peak at 503 Hz (Q = 3.2)")):
        gains = [m.bode_point(d, x)[0] for x in f]
        ax.semilogx(f, gains, color=C1, lw=1.6)
        ax.axhline(-3, color=GRID, ls=":", lw=1)
        ax.set_title(title, color=INK, fontsize=9)
        ax.set_xlabel("frequency [Hz]")
    b0.set_ylabel("gain [dB]")
    fig.tight_layout()
    return fig


@figure("charging_system")
def charging_system() -> plt.Figure:
    d = _example("charging_system").build()
    on, off = simulate(d), simulate(d, params={"energy_mgmt": 0.0})
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 5.2), sharex=True, height_ratios=[1, 1.2, 0.8])
    a0.plot(on.t, on["rpm"], color=C1, lw=1.6, label="with energy management")
    a0.plot(off.t, off["rpm"], color=C2, lw=1.2, ls="--", label="without")
    a0.set_ylabel("engine [rpm]")
    a0.legend(loc="upper left", fontsize=8)
    a0.set_title("Winter night: a 4-minute traffic jam, then driving", color=INK, fontsize=9)
    a1.plot(on.t, on["battery_charging"], color=C1, lw=1.6)
    a1.plot(off.t, off["battery_charging"], color=C2, lw=1.2, ls="--")
    a1.axhline(0, color=GRID, lw=1)
    a1.set_ylabel("battery [A]\n(+ charging)")
    a2.plot(on.t, on["soc"], color=C1, lw=1.6)
    a2.plot(off.t, off["soc"], color=C2, lw=1.2, ls="--")
    a2.set_ylabel("state of charge")
    a2.set_xlabel("time [s]")
    fig.tight_layout()
    return fig


@figure("ev_range")
def ev_range() -> plt.Figure:
    m = _example("ev_range")
    d = m.build()
    r = simulate(d)
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(7.6, 3.4), width_ratios=[1.7, 1])
    a0.plot(r.t, np.ravel(r["battery_W"]) / 1000, color=C1, lw=0.8)
    a0.axhline(0, color=GRID, lw=1)
    a0.set_xlabel("time [s]")
    a0.set_ylabel("battery power [kW]")
    a0.set_title("WLTC: negative = recovered while braking", color=INK, fontsize=9)
    labels, values = [], []
    for label, params in (("summer", {}), ("no regen", {"regen_share": 0.0}), ("heat pump", {"P_heater": 1200.0}),
                          ("heater", {"P_heater": 3000.0})):  # fmt: skip
        c = m.consumption(simulate(d, params=params), d)
        labels.append(label)
        values.append(c["range_km"])
    a1.barh(labels, values, color=[C1, C2, C3, C4])
    for k, v in enumerate(values):
        a1.text(v - 5, k, f"{v:.0f} km", va="center", ha="right", color="white", fontsize=8)
    a1.set_xlabel("range [km], 60 kWh")
    a1.invert_yaxis()
    fig.tight_layout()
    return fig


@figure("injector_driver")
def injector_driver() -> plt.Figure:
    m = _example("injector_driver")
    d = m.build()
    runs = {(v, fw): simulate(d, params={"V_bat": v, "freewheel": fw}) for v in (9.0, 14.0) for fw in (0.0, 1.0)}
    fig, (a0, a1, a2) = plt.subplots(3, 1, figsize=(6.4, 5.6), sharex=True, height_ratios=[1, 1.2, 1])
    r = runs[(14.0, 0.0)]
    a0.step(r.t * 1000, r["command"], where="post", color=INK, lw=1.4)
    a0.set_ylabel("command")
    a0.set_title("A 3 ms injection pulse: current, needle and drain voltage", color=INK, fontsize=9)
    for (v, fw), color, ls in (((14.0, 0.0), C1, "-"), ((9.0, 0.0), C3, "-"), ((14.0, 1.0), C2, "--")):
        rr = runs[(v, fw)]
        label = f"{v:g} V, {'freewheel diode' if fw else 'clamp'}"
        a1.plot(rr.t * 1000, rr["current"], color=color, lw=1.4, ls=ls, label=label)
        a2.plot(rr.t * 1000, rr["needle_lift"], color=color, lw=1.4, ls=ls)
    a1.axhline(0.6, color=GRID, ls=":", lw=1)
    a1.text(6.2, 0.63, "pull-in", color=INK, fontsize=8)
    a1.set_ylabel("coil current [A]")
    a1.legend(loc="upper right", fontsize=7)
    a2.set_ylabel("needle lift")
    a2.set_xlabel("time [ms]")
    fig.tight_layout()
    return fig


@figure("fmi_exchange")
def fmi_exchange() -> plt.Figure:
    runs = _example("fmi_exchange").main()
    a, b = runs["subsystem"], runs["fmu"]
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.plot(a.t, a["plate_angle"], color=GRID, lw=3, label="ECU as a subsystem")
    ax.plot(b.t, b["plate_angle"], color=C1, lw=1.4, label="ECU as an FMU (co-simulation, 10 ms step)")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("plate angle [°]")
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title("The same controller, inline and as an FMU", color=INK, fontsize=9)
    ax.set_xlim(0, 1.0)
    fig.tight_layout()
    return fig


@figure("discrete_control")
def discrete_control() -> plt.Figure:
    m = _example("discrete_control")
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    base = simulate(m.build(0.01))
    ax.plot(base.t, base["motor_c"], color=GRID, lw=4, label="continuous")
    for ts, color in ((0.01, C1), (0.03, C3), (0.05, C4), (0.06, C2)):
        r = simulate(m.build(ts))
        ax.plot(r.t, r["motor_d"], color=color, lw=1.3, label=f"T = {ts * 1000:.0f} ms")
    ax.set_ylim(-20, 230)
    ax.set_xlim(0, 0.4)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("speed [rad/s]")
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.set_title("The same PI controller, run every T seconds (closed-loop τ = 20 ms)", color=INK, fontsize=9)
    fig.tight_layout()
    return fig


@figure("aliasing")
def aliasing() -> plt.Figure:
    r = simulate(_example("discrete_control").build_aliasing())
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    ax.plot(r.t, r["vibration"], color=GRID, lw=1.0, label="45 Hz vibration")
    ax.step(r.t, r["sampled"], where="post", color=C1, lw=1.5, label="sampled every 20 ms: looks like 5 Hz")
    ax.set_xlabel("time [s]")
    ax.set_ylim(-1.3, 1.6)
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.set_title("Aliasing: a fast signal sampled too slowly", color=INK, fontsize=9)
    fig.tight_layout()
    return fig


def main() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])  # optional: names of the figures to regenerate
    for name, fn in FIGURES.items():
        if only and name not in only:
            continue
        fig = fn()
        out = APP_DIR / f"{name}.png"
        fig.savefig(out, dpi=160, bbox_inches="tight")
        plt.close(fig)
        shutil.copy(out, DOCS_DIR / out.name)
        print("wrote", out.name)


if __name__ == "__main__":
    main()
