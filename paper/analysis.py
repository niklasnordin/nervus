"""Validation of the Nervus simulator against two sets of fluid-mechanical measurements.

Reproduces every number and figure of ``validation.tex``:

    uv run python paper/analysis.py

Case 1  Gravity draining of a tank through a small side orifice (Fabusola & Simon 2025, Chem. Eng. Sci. 309,
        121488). The discharge coefficient is fitted to the calibration ("train") run only; the replicate ("test")
        run is then predicted with nothing refitted. The raw data are read from ``paper/data`` (see its README).
Case 2  Free fall of light spheres through air from the dome of St Paul's Cathedral, 1710 and 1719, as tabulated by
        Newton (Principia, 3rd edn, Book II, Prop. XL, Scholium, Exper. 13-14). No parameter is fitted.

Both cases are built as Nervus block diagrams and solved by Nervus; SciPy is used only for the calibration
sensor spline, the one-parameter fit and an independent reference solution.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.integrate import solve_ivp
from scipy.interpolate import UnivariateSpline
from scipy.optimize import brentq, minimize_scalar

import nervus
from nervus import Diagram, simulate
from nervus.blocks import Constant, Gain, Integrator, Product, PythonFunction, Saturation, Sum
from nervus.blocks.math_ops import MathFunction
from nervus.core import Variable

HERE = Path(__file__).parent
DATA = HERE / "data"
FIG = HERE / "figures"
RESULTS: dict[str, Any] = {"nervus_version": nervus.__version__}

plt.rcParams.update({"font.size": 9, "font.family": "serif", "mathtext.fontset": "cm", "axes.linewidth": 0.6,
                     "lines.linewidth": 1.0, "legend.frameon": False, "savefig.bbox": "tight"})  # fmt: skip

# ======================================================================================================== case 1

G_CM = 980.665  # cm/s^2, as in the authors' code
H_MAX = 28.6  # cm, tank height (paper)
H_O = 0.9  # cm, height of the orifice centre (paper)
R_O = 5 / 64 * 2.54 / 2  # cm, orifice radius from the 5/64 in drill bit (paper, authors' code)
SENSOR_MIN = 508  # counts: below this the jet has died (authors' code)
N_DOWNSAMPLE, N_OMIT = 20, 3  # the authors' downsampling and the end points they omit for surface tension


def rounded_rectangle(length: float, width: float, perimeter: float) -> tuple[float, float, float]:
    """(l, w, r) of a rounded rectangle from its outer length, width and perimeter (the authors' tape measurements):
    p = 2(l + w + pi r) with L = l + 2r, W = w + 2r."""
    r = (perimeter / 2 - (length + width)) / (math.pi - 4)
    return length - 2 * r, width - 2 * r, r


TOP = rounded_rectangle(14.6, 9.0, 44.3)
BOTTOM = rounded_rectangle(13.4, 7.8, 40.1)


def tank_area(h: float) -> float:
    """Horizontal cross-section a(h) [cm^2]: l, w and r vary linearly from the bottom to the top (paper eq. 2-3)."""
    f = h / H_MAX
    ln, wd, r = ((1 - f) * b + f * t for b, t in zip(BOTTOM, TOP, strict=True))
    return ln * wd + 2 * r * (ln + wd) + math.pi * r * r


AREA_CODE = """
def f(t, h):
    lb, wb, rb, lt, wt, rt, hmax = p
    x = min(max(h, 0.0), hmax) / hmax
    l = (1 - x) * lb + x * lt
    w = (1 - x) * wb + x * wt
    r = (1 - x) * rb + x * rt
    return l * w + 2 * r * (l + w) + math.pi * r * r
"""


def tank_diagram(h0: float, constant_area: float | None = None) -> Diagram:
    """The tank as a block diagram: a(h) dh/dt = -c pi r_o^2 sqrt(2 g (h - h_o))."""
    d = Diagram("draining_tank")
    ws = d.workspace
    ws.add(Variable("c", 0.65, "Discharge coefficient", ""))
    ws.add(Variable("r_o", R_O, "Orifice radius", "cm"))
    ws.add(Variable("h_o", H_O, "Orifice height", "cm"))
    ws.add(Variable("g", G_CM, "Gravity", "cm/s^2"))
    ws.add(Variable("h0", h0, "Initial level", "cm"))
    level = d.add(Integrator(initial="h0"), name="h")
    head = d.add(Sum(signs="+-"), name="head")
    orifice = d.add(Constant(value="h_o"), name="orifice_height")
    clip = d.add(Saturation(lower=0.0, upper=1e9), name="no_backflow")
    energy = d.add(Gain(gain="2 * g"), name="2g")
    speed = d.add(MathFunction(function="sqrt"), name="torricelli")
    flow = d.add(Gain(gain="c * pi * r_o**2"), name="Q")
    if constant_area is None:
        area = d.add(PythonFunction(code=AREA_CODE, parameters=[*BOTTOM, *TOP, H_MAX]), name="area")
        d.connect(level, area)
    else:
        area = d.add(Constant(value=constant_area), name="area")
    rate = d.add(Product(ops="*/"), name="Q_over_a")
    sign = d.add(Gain(gain=-1.0), name="outflow")
    d.connect(level, (head, 0))
    d.connect(orifice, (head, 1))
    d.chain(head, clip, energy, speed, flow)
    d.connect(flow, (rate, 0))
    d.connect(area, (rate, 1))
    d.chain(rate, sign, level)
    d.settings.rtol, d.settings.atol, d.settings.max_step = 1e-9, 1e-11, 1.0
    return d


def read_run(name: str, to_h: UnivariateSpline) -> dict[str, np.ndarray]:
    """Logged time and sensor counts from the maximum reading on (t = 0 there, as the authors define it), counts
    below 508 dropped, level from the calibration spline."""
    rows = list(csv.reader((DATA / f"liq_level_data_empty_{name}.csv").open()))[1:]
    t = np.array([float(a) for a, _ in rows])
    counts = np.array([float(b) for _, b in rows])
    k = int(np.argmax(counts))
    t, counts = t[k:] - t[k], counts[k:]
    h = to_h(counts)
    keep = h >= float(to_h(SENSOR_MIN))
    return {"t": t[keep], "h": h[keep], "counts": counts[keep]}


def downsample_ids(n: int) -> np.ndarray:
    """The authors' downsampling: trunc.(Int, range(1, n, length=20)), converted to 0-based indices."""
    return np.trunc(np.linspace(1, n, N_DOWNSAMPLE)).astype(int) - 1


def errors(model: np.ndarray, data: np.ndarray) -> dict[str, float]:
    r = model - data
    return {"rmse_cm": float(np.sqrt(np.mean(r**2))), "mae_cm": float(np.mean(np.abs(r))),
            "max_abs_cm": float(np.max(np.abs(r))), "bias_cm": float(np.mean(r)), "n": int(r.size)}  # fmt: skip


def simulate_level(d: Diagram, c: float, t: np.ndarray) -> np.ndarray:
    res = simulate(d, t_final=float(t[-1]), params={"c": c})
    return np.interp(t, res.t, res["h"])


def case1() -> None:
    cal = np.array(
        [[float(a), float(b)] for a, b in list(csv.reader((DATA / "level_sensor_calibration.csv").open()))[1:]]
    )
    to_h = UnivariateSpline(cal[:, 1], cal[:, 0], k=2, s=4.0)  # FITPACK, as Dierckx.Spline1D(k=2, s=4.0)
    runs = {name: read_run(name, to_h) for name in ("train", "test")}
    out: dict[str, Any] = {
        "geometry": {"top_lwr_cm": TOP, "bottom_lwr_cm": BOTTOM, "area_top_cm2": tank_area(H_MAX),
                     "area_bottom_cm2": tank_area(0.0), "h_max_cm": H_MAX, "h_o_cm": H_O, "r_o_cm": R_O},
        "h_at_508_counts_cm": float(to_h(SENSOR_MIN)),
        "calibration_spline_max_residual_cm": float(np.max(np.abs(to_h(cal[:, 1]) - cal[:, 0]))),
        "calibration_spline_rms_residual_cm": float(np.sqrt(np.mean((to_h(cal[:, 1]) - cal[:, 0]) ** 2))),
        "calibration_step_counts_per_cm": np.diff(cal[:, 1]).tolist(),
    }  # fmt: skip

    # The calibration window: the authors' 20 downsampled points minus the last 3 (surface tension) give the
    # end time; we fit every logged sample up to that time.
    for name, run in runs.items():
        ids = downsample_ids(run["t"].size)
        run["ids"] = ids
        run["t_cut"] = float(run["t"][ids[N_DOWNSAMPLE - N_OMIT - 1]])
        run["h0"] = float(run["h"][0])
        out[name] = {"n_samples": int(run["t"].size), "t_end_s": float(run["t"][-1]), "t_cut_s": run["t_cut"],
                     "h0_cm": run["h0"], "h_at_cut_cm": float(np.interp(run["t_cut"], run["t"], run["h"]))}  # fmt: skip

    train = runs["train"]
    window = train["t"] <= train["t_cut"]
    d_train = tank_diagram(train["h0"])

    def sse(c: float) -> float:
        return float(np.sum((simulate_level(d_train, c, train["t"][window]) - train["h"][window]) ** 2))

    fit = minimize_scalar(sse, bounds=(0.3, 1.0), method="bounded", options={"xatol": 1e-6})
    c_fit = float(fit.x)
    out["c_fit"] = c_fit
    out["train"]["fit_window"] = errors(simulate_level(d_train, c_fit, train["t"][window]), train["h"][window])
    # Sensitivity of the fitted c to the end of the window: fit on the first 90 % and 75 % of it as well.
    for frac in (0.9, 0.75):
        w = train["t"] <= frac * train["t_cut"]
        fs = minimize_scalar(
            lambda c, w=w: float(np.sum((simulate_level(d_train, c, train["t"][w]) - train["h"][w]) ** 2)),
            bounds=(0.3, 1.0), method="bounded", options={"xatol": 1e-6})  # fmt: skip
        out[f"c_fit_window_{int(frac * 100)}pct"] = float(fs.x)

    # Prediction of the replicate run: c from the train run, h0 from the test run's own first sample.
    test = runs["test"]
    pred = {name: simulate_level(tank_diagram(run["h0"]), c_fit, run["t"]) for name, run in runs.items()}
    tw = test["t"] <= test["t_cut"]
    out["test"]["window"] = errors(pred["test"][tw], test["h"][tw])
    out["test"]["all_points"] = errors(pred["test"], test["h"])
    out["train"]["all_points"] = errors(pred["train"], train["h"])
    # Like-for-like with the authors' reported mean absolute residuals (20 downsampled points; 17 for train).
    out["train"]["downsampled_17"] = errors(pred["train"][train["ids"][: N_DOWNSAMPLE - N_OMIT]],
                                            train["h"][train["ids"][: N_DOWNSAMPLE - N_OMIT]])  # fmt: skip
    out["test"]["downsampled_20"] = errors(pred["test"][test["ids"]], test["h"][test["ids"]])
    # Emptying-time check: when the model and the data reach 3 cm.
    for name, run in runs.items():
        t_model = float(np.interp(-3.0, -pred[name], run["t"]))
        t_data = float(run["t"][np.argmax(run["h"] <= 3.0)])
        out[name]["t_to_3cm_model_s"], out[name]["t_to_3cm_data_s"] = t_model, t_data

    # Are the residuals a property of the level (the sensor calibration) rather than of time? Compare the two runs'
    # residuals as functions of h, binned in 0.5 cm, over the window both runs share.
    bins = np.arange(3.0, 26.0, 0.5)
    binned = {}
    for name, run in runs.items():
        w = run["t"] <= run["t_cut"]
        res_h, h_w = (pred[name] - run["h"])[w], run["h"][w]
        idx = np.digitize(h_w, bins)
        binned[name] = np.array([res_h[idx == i].mean() if np.any(idx == i) else np.nan for i in range(1, bins.size)])
    ok = np.isfinite(binned["train"]) & np.isfinite(binned["test"])
    out["residual_vs_h_correlation"] = float(np.corrcoef(binned["train"][ok], binned["test"][ok])[0, 1])
    out["residual_vs_h_bins"] = int(ok.sum())
    centres = 0.5 * (bins[1:] + bins[:-1])

    # Verification: constant-area tank against the closed form h(t) = h_o + (sqrt(h0 - h_o) - k t / (2 A))^2.
    a0, h0, c = 120.0, 25.0, c_fit
    k = c * math.pi * R_O**2 * math.sqrt(2 * G_CM)
    t_empty = 2 * a0 * math.sqrt(h0 - H_O) / k
    tv = np.linspace(0.0, 0.98 * t_empty, 400)
    exact = H_O + (math.sqrt(h0 - H_O) - k * tv / (2 * a0)) ** 2
    ver = {}
    for solver in ("auto", "rk4"):
        dv = tank_diagram(h0, constant_area=a0)
        kw: dict[str, Any] = {"solver": solver}
        if solver == "rk4":
            kw["step"] = 0.5
        res = simulate(dv, t_final=float(tv[-1]), params={"c": c}, **kw)
        num = np.interp(tv, res.t, res["h"])
        ver[solver] = {"max_abs_error_cm": float(np.max(np.abs(num - exact))), "n_points": int(res.t.size)}
    # Independent reference for the real (variable-area) tank: SciPy's Radau at tight tolerance.
    ref = solve_ivp(lambda t, y: [-k * math.sqrt(max(y[0] - H_O, 0.0)) / tank_area(y[0])], (0, train["t"][-1]),
                    [train["h0"]], method="Radau", rtol=1e-11, atol=1e-12, dense_output=True)  # fmt: skip
    ver["variable_area_vs_scipy_radau_max_abs_cm"] = float(np.max(np.abs(pred["train"] - ref.sol(train["t"])[0])))
    ver["t_empty_constant_area_s"] = t_empty
    out["verification"] = ver
    RESULTS["case1"] = out

    # ---------------------------------------------------------------- figures
    fig, axs = plt.subplots(2, 2, figsize=(6.4, 4.2), sharex="col", height_ratios=[2.2, 1])
    for j, name in enumerate(("train", "test")):
        run, ax, axr = runs[name], axs[0, j], axs[1, j]
        ax.plot(run["t"], run["h"], ".", ms=0.8, color="0.55", label="measured", rasterized=True)
        ax.plot(run["t"], pred[name], "-", color="k", lw=1.0, label=f"Nervus, $C_d = {c_fit:.3f}$")
        ax.axvline(run["t_cut"], color="0.3", ls=":", lw=0.7)
        ax.axhline(H_O, color="0.3", ls="--", lw=0.6)
        ax.set_title("(a) calibration run" if name == "train" else "(b) replicate run (prediction)", fontsize=9)
        axr.plot(run["t"], pred[name] - run["h"], ".", ms=0.8, color="0.3", rasterized=True)
        axr.axhline(0, color="k", lw=0.5)
        axr.axvline(run["t_cut"], color="0.3", ls=":", lw=0.7)
        axr.set_xlabel("$t$ (s)")
        axr.set_ylim(-1.2, 1.2)
    axs[0, 0].set_ylabel("$h$ (cm)")
    axs[1, 0].set_ylabel("$h_{model}-h$ (cm)")
    axs[0, 0].legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "tank.pdf")
    fig.savefig(FIG / "tank.png", dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.plot(centres[ok], binned["train"][ok], "o-", mfc="none", ms=3, color="k", lw=0.7, label="calibration run")
    ax.plot(centres[ok], binned["test"][ok], "s--", mfc="none", ms=3, color="0.45", lw=0.7, label="replicate run")
    ax.axhline(0, color="k", lw=0.4)
    ax.set_xlabel("$h$ (cm)")
    ax.set_ylabel("mean $h_{model}-h$ (cm)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "residual_h.pdf")
    fig.savefig(FIG / "residual_h.png", dpi=200)
    plt.close(fig)


# ======================================================================================================== case 2

G_SI = 9.80665  # m/s^2
GRAIN = 64.79891e-6  # kg
RHO_WATER = 1000.0  # kg/m^3 ("rain water")
RHO_AIR_NEWTON = RHO_WATER / 860  # Newton's assumed ratio of air to rain water
MU_AIR = 1.81e-5  # Pa s, air near 20 C


def morrison_cd(re: float, variant: str = "handout") -> float:
    """Sphere drag coefficient over all Re up to 1e6 (Morrison 2013).

    ``handout``: Morrison's 2013 sphere-drag handout, eq. (1) (the primary form here). ``sheet``: the fourth term
    as printed in Morrison's CM3110 2013 correlation sheet, 0.25 (Re/1e6) / (1 + Re/1e6). That sheet also prints
    236 000 instead of 263 000 in the denominator of the third term (``sheet_misprint``): this makes C_D about 0.9
    below the drag crisis, contradicting the sheet's own C_D = 0.445 for 1e3 <= Re <= 2.6e5, so it is a misprint
    and only reported. ``constant``: that simplified value, 0.445."""
    if variant == "constant":
        return 0.445
    re = max(re, 1e-9)
    cd = 24 / re + 2.6 * (re / 5.0) / (1 + (re / 5.0) ** 1.52)
    third_den = 236000 if variant == "sheet_misprint" else 263000
    cd += 0.411 * (re / 263000) ** -7.94 / (1 + (re / third_den) ** -8.00)
    if variant == "handout":
        return cd + re**0.80 / 461000
    if variant in ("sheet", "sheet_misprint"):
        return cd + 0.25 * (re / 1e6) / (1 + re / 1e6)
    raise ValueError(variant)


DRAG_CODE = """
def f(t, v):
    rho, mu, d, variant, cd_const = p
    re = max(rho * abs(v) * d / mu, 1e-9)
    if variant == 2:
        cd = cd_const
    else:
        cd = 24 / re + 2.6 * (re / 5.0) / (1 + (re / 5.0) ** 1.52)
        cd += 0.411 * (re / 263000) ** -7.94 / (1 + (re / 263000) ** -8.00)
        cd += re**0.80 / 461000 if variant == 0 else 0.25 * (re / 1e6) / (1 + re / 1e6)
    return 0.5 * rho * cd * math.pi * d * d / 4 * v * abs(v)
"""
VARIANTS = {"handout": 0, "sheet": 1, "constant": 2}


def drop_diagram(
    m_app: float, d: float, rho: float, added_mass: bool = True, variant: str = "handout", cd_const: float = 0.445
) -> Diagram:
    """A sphere falling from rest: (m + C_a rho V) dv/dt = m_app g - D(v), dz/dt = v.

    ``m_app`` is the weight in air as tabulated (mass minus displaced air), so m_app g is weight minus buoyancy;
    the true mass is m_app + rho V, as Newton computed it."""
    vol = math.pi * d**3 / 6
    inertia = m_app + rho * vol + (0.5 * rho * vol if added_mass else 0.0)
    dg = Diagram("falling_sphere")
    net_weight = dg.add(Constant(value=m_app * G_SI), name="net_weight")
    drag = dg.add(PythonFunction(code=DRAG_CODE, parameters=[rho, MU_AIR, d, VARIANTS[variant], cd_const]), name="drag")
    force = dg.add(Sum(signs="+-"), name="force")
    accel = dg.add(Gain(gain=1 / inertia), name="accel")
    v = dg.add(Integrator(), name="v")
    z = dg.add(Integrator(), name="z")
    dg.connect(net_weight, (force, 0))
    dg.connect(drag, (force, 1))
    dg.chain(force, accel, v, z)
    dg.connect(v, drag)
    dg.settings.rtol, dg.settings.atol, dg.settings.max_step = 1e-9, 1e-11, 0.02
    return dg


def terminal_speed(m_app: float, d: float, rho: float, variant: str = "handout", cd_const: float = 0.445) -> float:
    """The speed at which the drag equals the net weight (bisection on the same drag law)."""
    lo, hi = 1e-6, 1e3
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        cd = cd_const if variant == "constant" else morrison_cd(rho * mid * d / MU_AIR, variant)
        drag = 0.5 * rho * cd * math.pi * d * d / 4 * mid * mid
        lo, hi = (mid, hi) if drag < m_app * G_SI else (lo, mid)
    return 0.5 * (lo + hi)


def fall(m_app: float, d: float, height: float, rho: float = RHO_AIR_NEWTON, **kw: Any) -> dict[str, float]:
    variant = kw.get("variant", "handout")
    cd_const = kw.get("cd_const", 0.445)
    v_term = terminal_speed(m_app, d, rho, variant, cd_const)
    # Long enough to hit the ground: the time at terminal speed plus the time a vacuum fall would take.
    t_final = height / v_term + math.sqrt(2 * height / G_SI) + 2.0
    res = simulate(drop_diagram(m_app, d, rho, **kw), t_final=t_final)
    if res["z"][-1] < height:
        raise RuntimeError("the run ended before the object reached the ground")
    t_hit = float(np.interp(height, res["z"], res.t))  # z rises monotonically
    v_hit = float(np.interp(t_hit, res.t, res["v"]))
    v = res["v"]
    t95 = float(res.t[np.argmax(v >= 0.95 * v_term)]) if np.any(v >= 0.95 * v_term) else float("nan")
    re_hit = rho * v_hit * d / MU_AIR
    return {"t_s": t_hit, "v_impact_m_s": v_hit, "v_terminal_m_s": v_term, "v_ratio": v_hit / v_term,
            "t95_s": t95, "re_impact": re_hit, "cd_impact": cd_const if variant == "constant" else morrison_cd(re_hit, variant)}  # fmt: skip


def effective_cd(m_app: float, d: float, height: float, t_measured: float) -> float:
    """The constant drag coefficient with which the simulated fall takes the measured time."""
    return float(
        brentq(
            lambda cd: fall(m_app, d, height, variant="constant", cd_const=cd)["t_s"] - t_measured,
            0.05, 3.0, xtol=1e-4,
        )
    )  # fmt: skip


def case2() -> None:
    rows = list(csv.DictReader(line for line in (HERE / "newton1726_st_pauls.csv").open() if not line.startswith("#")))
    objects = []
    for r in rows:
        m_app, d, height = float(r["weight_kg"]), float(r["diameter_m"]), float(r["height_m"])
        base = fall(m_app, d, height)
        sens = {
            "no_added_mass": fall(m_app, d, height, added_mass=False)["t_s"],
            "cd_sheet": fall(m_app, d, height, variant="sheet")["t_s"],
            "cd_constant_0.445": fall(m_app, d, height, variant="constant")["t_s"],
            "rho_1.15": fall(m_app, d, height, rho=1.15)["t_s"],
            "rho_1.23": fall(m_app, d, height, rho=1.23)["t_s"],
        }
        obs = float(r["time_s"]) if r["time_s"] else None
        cor = float(r["time_corrected_s"]) if r["time_corrected_s"] else None
        measured = cor if cor is not None else obs
        objects.append({
            "exper": int(r["exper"]), "object": r["object"], "n": int(r["n"]), "weight_text": r["weight_grains_text"],
            "time_text": r["time_text"], "m_app_kg": m_app, "d_m": d, "height_m": height, "t_observed_s": obs,
            "t_corrected_s": cor, "t_measured_s": measured, **base,
            "error_s": None if measured is None else base["t_s"] - measured,
            "t_vacuum_s": math.sqrt(2 * height / G_SI), "sensitivity_t_s": sens,
            "cd_effective": None if measured is None or "mercury" in r["object"]
            else effective_cd(m_app, d, height, measured),
        })  # fmt: skip
    out: dict[str, Any] = {
        "rho_air": RHO_AIR_NEWTON,
        "mu_air": MU_AIR,
        "objects": objects,
        "cd_at_re_5e4": {v: morrison_cd(5e4, v) for v in ("handout", "sheet", "sheet_misprint")},
    }
    for label, sel in (("air_globes", "air-filled"), ("bladders", "bladder")):
        group = [o for o in objects if sel in o["object"]]
        err = np.array([o["error_s"] for o in group])
        rel = np.array([o["error_s"] / o["t_measured_s"] for o in group])
        out[label] = {"mean_error_s": float(err.mean()), "mean_abs_error_s": float(np.abs(err).mean()),
                      "max_abs_error_s": float(np.abs(err).max()), "mean_rel_error": float(rel.mean()),
                      "re_range": [min(o["re_impact"] for o in group), max(o["re_impact"] for o in group)],
                      "v_ratio_min": min(o["v_ratio"] for o in group),
                      "cd_effective_mean": float(np.mean([o["cd_effective"] for o in group])),
                      "cd_effective_range": [min(o["cd_effective"] for o in group), max(o["cd_effective"] for o in group)],
                      "cd_model_mean": float(np.mean([o["cd_impact"] for o in group])),
                      "t95_fraction_max": max(o["t95_s"] / o["t_s"] for o in group),
                      "n": len(group)}  # fmt: skip
    merc = [o for o in objects if "mercury" in o["object"]]
    out["mercury_globes"] = {
        "t_pred_range_s": [min(o["t_s"] for o in merc), max(o["t_s"] for o in merc)],
        "re_range": [min(o["re_impact"] for o in merc), max(o["re_impact"] for o in merc)],
    }
    RESULTS["case2"] = out

    fig, ax = plt.subplots(figsize=(3.4, 3.2))
    for sel, marker, label in (("air-filled", "o", "glass globes, 1710 (corrected)"),
                               ("bladder", "s", "bladders, 1719")):  # fmt: skip
        g = [o for o in objects if sel in o["object"]]
        ax.plot([o["t_measured_s"] for o in g], [o["t_s"] for o in g], marker, mfc="none", color="k", ms=4, label=label)
        lo = [min(o["sensitivity_t_s"].values()) for o in g]
        hi = [max(o["sensitivity_t_s"].values()) for o in g]
        ax.vlines([o["t_measured_s"] for o in g], lo, hi, color="0.6", lw=0.8)
    merc = [o for o in objects if "mercury" in o["object"]]
    ax.plot(
        [4.0] * len(merc), [o["t_s"] for o in merc], "^", mfc="none", color="k", ms=4, label='mercury globes ("4 s")'
    )
    lim = (3, 24)
    ax.plot(lim, lim, "-", color="0.4", lw=0.6)
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_xlabel("measured fall time (s)")
    ax.set_ylabel("simulated fall time (s)")
    ax.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "stpauls.pdf")
    fig.savefig(FIG / "stpauls.png", dpi=200)
    plt.close(fig)

    re = np.logspace(3, 6.3, 400)
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    for variant, ls, label in (
        ("handout", "-", "Morrison (2013), handout"),
        ("sheet", "--", "fourth term of the sheet"),
    ):
        ax.semilogx(re, [morrison_cd(x, variant) for x in re], ls, color="k", lw=0.9, label=label)
    for sel, marker in (("air-filled", "o"), ("bladder", "s"), ("mercury", "^")):
        g = [o for o in objects if sel in o["object"]]
        ax.plot([o["re_impact"] for o in g], [o["cd_impact"] for o in g], marker, mfc="none", color="k", ms=4)
    ax.set_xlabel("$Re$ at impact")
    ax.set_ylabel("$C_D$")
    ax.set_ylim(0, 0.6)
    ax.legend(fontsize=7, loc="lower left")
    fig.tight_layout()
    fig.savefig(FIG / "cd.pdf")
    fig.savefig(FIG / "cd.png", dpi=200)
    plt.close(fig)


def main() -> None:
    FIG.mkdir(exist_ok=True)
    import verification  # code verification against exact solutions (section 3 of the paper)

    RESULTS["verification_suite"] = verification.run()
    case1()
    case2()
    (HERE / "results.json").write_text(json.dumps(RESULTS, indent=2, default=float), encoding="utf-8")
    c1, c2 = RESULTS["case1"], RESULTS["case2"]
    print(f"c_fit = {c1['c_fit']:.4f}; train window {c1['train']['fit_window']}")
    print(f"test window {c1['test']['window']}")
    print(f"test all {c1['test']['all_points']}")
    print(f"verification {c1['verification']}")
    for k in ("air_globes", "bladders", "mercury_globes"):
        print(k, c2[k])


if __name__ == "__main__":
    main()
