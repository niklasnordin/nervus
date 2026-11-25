"""Code verification of Nervus: each module against a closed-form or otherwise exactly known answer.

Called by ``analysis.py`` (its results go to ``results.json`` under ``"verification_suite"``); run on its own with

    uv run python paper/verification.py

to update only that part of ``results.json``. The exact answers are computed here, independently of Nervus (NumPy
and SciPy only), and every case reports an error norm; the fixed-step solvers also report their observed order.
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import brentq

from nervus import Diagram, simulate
from nervus.blocks import (
    Capacitor,
    Constant,
    CurrentSensor,
    DataTypeConversion,
    DiscretePID,
    DiscreteTransferFunction,
    ElectricalReference,
    HydraulicVolume,
    Inductor,
    Inertia,
    Inport,
    Integrator,
    Lookup1D,
    Orifice,
    Outport,
    PressureSensor,
    PressureSource,
    Resistor,
    RotationalDamper,
    RotationalReference,
    Saturation,
    Scope,
    Sine,
    SpeedSensor,
    Step,
    TemperatureSensor,
    TemperatureSource,
    ThermalMass,
    ThermalResistance,
    TorqueSource,
    TransferFunction,
    VoltageSensor,
    VoltageSource,
    ZeroOrderHold,
)

HERE = Path(__file__).parent
FIG = HERE / "figures"
TIGHT = {"rtol": 1e-10, "atol": 1e-12}


def _max_err(t: np.ndarray, y: np.ndarray, exact: Any) -> float:
    return float(np.max(np.abs(np.asarray(y, float) - exact(np.asarray(t, float)))))


def _probe(d: Diagram, block: Any, name: str) -> Any:
    d.add(block, name=name)
    d.connect(block, d.add(Scope()))
    return block


# ================================================================================================ 1. solvers


def first_order() -> Diagram:
    """y' = (u - y) / tau with a unit step at t = 0, tau = 0.5 s: y = 1 - exp(-t/tau)."""
    d = Diagram("first_order")
    u = d.add(Step(time=0.0, initial=0.0, final=1.0), name="u")
    g = d.add(TransferFunction(numerator=[1.0], denominator=[0.5, 1.0]), name="y")
    d.chain(u, g)
    return d


ZETA, W0 = 0.2, 2.0


def second_order() -> Diagram:
    """w0^2 / (s^2 + 2 zeta w0 s + w0^2), unit step at t = 0, zeta = 0.2, w0 = 2 rad/s."""
    d = Diagram("second_order")
    u = d.add(Step(time=0.0, initial=0.0, final=1.0), name="u")
    g = d.add(TransferFunction(numerator=[W0**2], denominator=[1.0, 2 * ZETA * W0, W0**2]), name="y")
    d.chain(u, g)
    return d


def second_order_exact(t: np.ndarray) -> np.ndarray:
    wd = W0 * math.sqrt(1 - ZETA**2)
    return 1 - np.exp(-ZETA * W0 * t) * (np.cos(wd * t) + ZETA / math.sqrt(1 - ZETA**2) * np.sin(wd * t))


def solvers() -> dict[str, Any]:
    out: dict[str, Any] = {}
    r = simulate(first_order(), 5.0, **TIGHT)
    out["first_order_max_err"] = _max_err(r.t, r["y"], lambda t: 1 - np.exp(-t / 0.5))
    r = simulate(second_order(), 10.0, **TIGHT)
    out["second_order_max_err"] = _max_err(r.t, r["y"], second_order_exact)

    steps = [0.1 / 2**k for k in range(7)]
    conv: dict[str, Any] = {}
    for solver in ("euler", "rk4"):
        errs = []
        for h in steps:
            r = simulate(second_order(), 5.0, solver=solver, step=h)
            errs.append(_max_err(r.t, r["y"], second_order_exact))
        # Observed order from the finest half of the steps (above round-off), least squares on log-log.
        use = [i for i, e in enumerate(errs) if e > 1e-12][-4:]
        order = float(np.polyfit(np.log([steps[i] for i in use]), np.log([errs[i] for i in use]), 1)[0])
        conv[solver] = {"steps": steps, "max_err": errs, "observed_order": order}
    rtols = [10.0**-k for k in range(3, 12)]
    verr = []
    for rt in rtols:
        r = simulate(second_order(), 10.0, rtol=rt, atol=rt * 1e-2)
        verr.append(_max_err(r.t, r["y"], second_order_exact))
    # The slope where the tolerance controls the error: at looser tolerances the default maximum step does.
    tight = [i for i, rt in enumerate(rtols) if rt <= 1e-6]
    conv["RK45"] = {"rtol": rtols, "max_err": verr, "slope_fit_rtol_max": 1e-6,
                    "slope": float(np.polyfit(np.log([rtols[i] for i in tight]), np.log([verr[i] for i in tight]), 1)[0])}  # fmt: skip
    out["convergence"] = conv

    fig, (a0, a1) = plt.subplots(1, 2, figsize=(6.4, 2.6))
    for solver, marker in (("euler", "o"), ("rk4", "s")):
        c = conv[solver]
        a0.loglog(c["steps"], c["max_err"], marker + "-", mfc="none", color="k", ms=3, lw=0.8,
                  label=f"{solver} (order {c['observed_order']:.2f})")  # fmt: skip
    h = np.array(steps)
    a0.loglog(h, 0.5 * h, ":", color="0.5", lw=0.7)
    a0.loglog(h, 0.05 * h**4, ":", color="0.5", lw=0.7)
    a0.set_xlabel("step $h$ (s)")
    a0.set_ylabel("max. error")
    a0.legend(fontsize=7)
    a0.set_title("(a) fixed step", fontsize=9)
    a1.loglog(rtols, verr, "^-", mfc="none", color="k", ms=3, lw=0.8)
    a1.loglog(rtols, rtols, ":", color="0.5", lw=0.7)
    a1.set_xlabel("relative tolerance")
    a1.set_title("(b) variable step (RK45)", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / "convergence.pdf")
    fig.savefig(FIG / "convergence.png", dpi=200)
    plt.close(fig)
    return out


# ================================================================================================ 2. events


def events() -> dict[str, Any]:
    out: dict[str, Any] = {}
    # (a) A body thrown upwards from h0 stops on the integrator's lower limit z = 0.
    g, v0, h0 = 9.80665, 7.0, 3.0
    d = Diagram("thrown")
    acc = d.add(Constant(value=-g), name="a")
    v = d.add(Integrator(initial=v0), name="v")
    z = d.add(Integrator(initial=h0, lower=0.0), name="z")
    d.chain(acc, v, z)
    r = simulate(d, 3.0)
    t_exact = (v0 + math.sqrt(v0**2 + 2 * g * h0)) / g
    t_hit = float(r.t[np.argmax(r["z"] <= 0.0)])
    out["impact_time_exact_s"], out["impact_time_error_s"] = t_exact, abs(t_hit - t_exact)
    out["impact_z_before_max_err"] = _max_err(
        r.t[r.t < t_exact], r["z"][r.t < t_exact], lambda t: h0 + v0 * t - 0.5 * g * t * t
    )

    # (b) The integral of a saturated sine, with and without zero-crossing detection.
    a_sat, n_per = 0.5, 5
    t_end = 2 * math.pi * n_per

    def saturated(zc: bool) -> float:
        d = Diagram("saturated_sine")
        s = d.add(Sine(amplitude=1.0, frequency=1.0), name="s")
        sat = d.add(Saturation(lower=-a_sat, upper=a_sat), name="sat")
        integ = d.add(Integrator(), name="I")
        d.chain(s, sat, integ)
        d.settings.zero_crossing = zc
        return float(simulate(d, t_end / 4, rtol=1e-6, atol=1e-9)["I"][-1])

    # Quarter period: the integral of min(sin t, a) from 0 to pi/2.
    ts = math.asin(a_sat)
    exact = (1 - math.cos(ts)) + a_sat * (math.pi / 2 - ts)
    out["saturated_quarter_exact"] = exact
    out["saturated_err_zc_on"] = abs(saturated(True) - exact)
    out["saturated_err_zc_off"] = abs(saturated(False) - exact)
    return out


# ================================================================================================ 3. discrete


def _discrete_example() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("dc", HERE.parent / "examples" / "discrete_control.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


K_M, TAU_M, TAU_CL = 2.0, 0.1, 0.02
KP, KI = TAU_M / (K_M * TAU_CL), TAU_M / (K_M * TAU_CL) / TAU_M


def discrete_recursion(ts: float, n: int, t_step: float = 0.05, r_final: float = 100.0) -> np.ndarray:
    """The exact sampled response of the discrete PI loop: the motor discretized exactly under a zero-order
    hold, the PI with forward-Euler integration (DiscretePID's own law)."""
    a = math.exp(-ts / TAU_M)
    b = K_M * (1 - a)
    y, xi, out = 0.0, 0.0, []
    for k in range(n):
        r = r_final if k * ts >= t_step - 1e-12 else 0.0
        e = r - y
        u = KP * e + xi
        out.append(y)
        xi += KI * ts * e
        y = a * y + b * u
    return np.array(out)


def spectral_radius(ts: float) -> float:
    a = math.exp(-ts / TAU_M)
    b = K_M * (1 - a)
    return float(max(abs(np.roots([1, -1 - a + b * KP, a - b * KP + b * KI * ts]))))


def discrete() -> dict[str, Any]:
    mod = _discrete_example()
    out: dict[str, Any] = {}
    for ts in (0.01, 0.025):
        d = mod.build(ts)
        r = simulate(d, 0.4, **TIGHT)
        n = round(0.4 / ts)
        tk = np.arange(n) * ts
        sim = np.array([r["motor_d"][np.argmin(np.abs(r.t - t))] for t in tk])  # logged at every sample hit
        out[f"T{int(ts * 1000)}ms_max_err"] = float(np.max(np.abs(sim - discrete_recursion(ts, n))))
    t_star = brentq(lambda ts: spectral_radius(ts) - 1.0, 0.03, 0.08, xtol=1e-12)
    out["stability_limit_exact_s"] = t_star

    def growth(ts: float) -> float:
        """Per-sample growth of the error, estimated from the simulation (late part of a long run)."""
        r = simulate(mod.build(ts), 12.0, **TIGHT)
        n = int(12.0 / ts)
        tk = np.arange(n) * ts
        e = np.abs(np.interp(tk, r.t, r["motor_d"]) - 100.0)
        k1, k2 = int(0.5 * n), int(0.9 * n)
        e1 = np.max(e[k1 - 4 : k1 + 4])
        e2 = np.max(e[k2 - 4 : k2 + 4])
        return float((e2 / e1) ** (1.0 / (k2 - k1)))

    t_sim = brentq(lambda ts: growth(ts) - 1.0, 0.054, 0.062, xtol=2e-5)
    out["stability_limit_from_simulation_s"] = t_sim
    return out


# ================================================================================================ 4. physical


def physical() -> dict[str, Any]:
    out: dict[str, Any] = {}
    # RC charging, tau = RC = 1 s.
    d = Diagram("rc")
    src, res = d.add(VoltageSource(V=10.0), name="V"), d.add(Resistor(R=1000.0), name="R")
    cap, gnd = d.add(Capacitor(C=1e-3), name="C"), d.add(ElectricalReference(), name="gnd")
    vc = _probe(d, VoltageSensor(), "vc")
    for a, b in (((src, "p"), (res, "p")), ((res, "n"), (cap, "p")), ((cap, "n"), (gnd, "p")),
                 ((src, "n"), (gnd, "p")), ((vc, "p"), (cap, "p")), ((vc, "n"), (gnd, "p"))):  # fmt: skip
        d.link(a, b)
    r = simulate(d, 5.0, **TIGHT)
    out["rc_max_err_V"] = _max_err(r.t, r["vc"], lambda t: 10 * (1 - np.exp(-t)))

    # Series RLC at a 1 V step (R = 2, L = 1, C = 0.25): i = exp(-t) sin(sqrt3 t) / sqrt3.
    d = Diagram("rlc")
    src, res = d.add(VoltageSource(V=1.0), name="V"), d.add(Resistor(R=2.0), name="R")
    ind, cap = d.add(Inductor(L=1.0), name="L"), d.add(Capacitor(C=0.25), name="C")
    am, gnd = _probe(d, CurrentSensor(), "i"), d.add(ElectricalReference(), name="gnd")
    for a, b in (((src, "p"), (am, "p")), ((am, "n"), (res, "p")), ((res, "n"), (ind, "p")),
                 ((ind, "n"), (cap, "p")), ((cap, "n"), (gnd, "p")), ((src, "n"), (gnd, "p"))):  # fmt: skip
        d.link(a, b)
    r = simulate(d, 8.0, **TIGHT)
    wd = math.sqrt(3.0)
    out["rlc_max_err_A"] = _max_err(r.t, r["i"], lambda t: np.exp(-t) * np.sin(wd * t) / wd)

    # Thermal: a capacity C = 500 J/K at 90 C cooling through R = 0.2 K/W to 20 C: tau = 100 s.
    d = Diagram("cooling")
    mass, amb = d.add(ThermalMass(C=500.0, T0=90.0), name="m"), d.add(TemperatureSource(T=20.0), name="amb")
    rth = d.add(ThermalResistance(R=0.2), name="R")
    ts_ = _probe(d, TemperatureSensor(), "T")
    d.link((mass, "a"), (rth, "a"))
    d.link((rth, "b"), (amb, "a"))
    d.link((ts_, "a"), (mass, "a"))
    r = simulate(d, 500.0, **TIGHT)
    out["thermal_max_err_K"] = _max_err(r.t, r["T"], lambda t: 20 + 70 * np.exp(-t / 100.0))

    # Rotational: torque 2 N m on J = 0.05 kg m^2 with damping 0.1 N m s: w = 20 (1 - exp(-2 t)).
    d = Diagram("spin_up")
    tq, j = d.add(TorqueSource(tau=2.0), name="tq"), d.add(Inertia(J=0.05), name="J")
    damp = d.add(RotationalDamper(d=0.1), name="d")
    housing = d.add(RotationalReference(), name="housing")
    d.link((damp, "b"), (housing, "a"))  # bearing friction against the housing
    w = _probe(d, SpeedSensor(), "w")
    d.link((tq, "a"), (j, "a"))
    d.link((damp, "a"), (j, "a"))
    d.link((w, "a"), (j, "a"))
    r = simulate(d, 4.0, **TIGHT)
    out["rotational_max_err_rad_s"] = _max_err(r.t, r["w"], lambda t: 20 * (1 - np.exp(-2 * t)))

    # Hydraulic: a closed volume pressurised from 0 to p_s through an orifice, q = q1 dp / sqrt(|dp| + p_lam):
    # with u = p_s - p,  du/dt = -k u / sqrt(u + p_lam),  k = beta q1 / (60 V)  (q in L/min, V in L). Separable:
    # t(u) = [F(u0) - F(u)] / k,  F(u) = 2 sqrt(u + pl) + sqrt(pl) ln((sqrt(u + pl) - sqrt(pl)) / (sqrt(u + pl) + sqrt(pl))).
    ps, q1, pl, vol, beta = 5.0, 2.0, 0.01, 0.5, 100.0  # an effective (gas-charged) bulk modulus: k = 6.7
    k = beta * q1 / (60 * vol)
    d = Diagram("hydraulic_fill")
    sup, ori = d.add(PressureSource(p=ps), name="supply"), d.add(Orifice(q1=q1, p_lam=pl), name="orifice")
    v = d.add(HydraulicVolume(V=vol, beta=beta, p0=0.0), name="volume")
    sens = _probe(d, PressureSensor(), "p")
    d.link((sup, "a"), (ori, "a"))  # the source holds its port at p_s above the tank
    d.link((ori, "b"), (v, "a"))
    d.link((sens, "a"), (v, "a"))
    r = simulate(d, 1.0, **TIGHT)

    def big_f(u: float) -> float:
        s, sp = math.sqrt(u + pl), math.sqrt(pl)
        return 2 * s + sp * math.log((s - sp) / (s + sp))

    def p_exact(t: float) -> float:
        target = big_f(ps) - k * t
        if target <= big_f(1e-14):
            return ps
        return ps - brentq(lambda u: big_f(u) - target, 1e-14, ps, xtol=1e-15, rtol=1e-15)

    out["hydraulic_max_err_bar"] = _max_err(r.t, r["p"], np.vectorize(p_exact))
    out["hydraulic_t_half_s"] = (big_f(ps) - big_f(ps / 2)) / k
    return out


# ================================================================================================ 5. fluid analytics


def fluid() -> dict[str, Any]:
    """A sphere falling from rest with constant C_D and added mass: v = v_t tanh(t/T), z = v_t T ln cosh(t/T),
    with v_t = sqrt(W / k), T = m_eff / sqrt(W k), W = m_app g, k = rho C_D A / 2."""
    import analysis  # the same diagram as case 2

    m_app, dia, rho, cd = 0.0312979, 0.127, 1.163, 0.445
    vol = math.pi * dia**3 / 6
    m_eff = m_app + 1.5 * rho * vol
    wgt, kk = m_app * analysis.G_SI, 0.5 * rho * cd * math.pi * dia**2 / 4
    vt, tt = math.sqrt(wgt / kk), m_eff / math.sqrt(wgt * kk)
    d = analysis.drop_diagram(m_app, dia, rho, variant="constant", cd_const=cd)
    d.settings.max_step = 0.01
    r = simulate(d, 10.0, **TIGHT)
    return {
        "sphere_v_max_err": _max_err(r.t, r["v"], lambda t: vt * np.tanh(t / tt)),
        "sphere_z_max_err": _max_err(r.t, r["z"], lambda t: vt * tt * np.log(np.cosh(t / tt))),
        "sphere_v_terminal": vt,
    }


# ================================================================================================ 6. linear analysis


def linear_analysis() -> dict[str, Any]:
    from nervus.analysis import linearize

    def io(num: list[float], den: list[float]) -> Any:
        d = Diagram("loop")
        u = d.add(Inport(port=1), name="u")
        d.chain(u, d.add(TransferFunction(numerator=num, denominator=den), name="L"), d.add(Outport(port=1), name="y"))
        return linearize(d)

    out: dict[str, Any] = {}
    # Bode of 1/(0.5 s + 1).
    w = np.logspace(-2, 3, 41)
    _w, mag, ph = io([1.0], [0.5, 1.0]).bode(w)
    out["bode_mag_max_err_db"] = float(np.max(np.abs(mag - (-10 * np.log10(1 + (0.5 * w) ** 2)))))
    out["bode_phase_max_err_deg"] = float(np.max(np.abs(ph - (-np.degrees(np.arctan(0.5 * w))))))

    cases: dict[str, tuple[list[float], list[float], float, float]] = {}
    # L1 = 1/(s(s+1)): |L| = 1 at w^2 = (sqrt5 - 1)/2; PM = 90 - atan(w); GM infinite.
    wc = math.sqrt((math.sqrt(5) - 1) / 2)
    cases["L1"] = ([1.0], [1.0, 1.0, 0.0], 90 - math.degrees(math.atan(wc)), math.inf)
    # L2 = 10/(s(s+1)(0.1s+1)): phase crossover at sqrt(10), GM = 20 log10(1.1); |L| = 1 where x(1+x)(1+0.01x) = 100.
    x = max(rt.real for rt in np.roots([0.01, 1.01, 1.0, -100.0]) if abs(rt.imag) < 1e-12 and rt.real > 0)
    wg = math.sqrt(x)
    cases["L2"] = ([10.0], [0.1, 1.1, 1.0, 0.0], 90 - math.degrees(math.atan(wg) + math.atan(0.1 * wg)),
                   20 * math.log10(1.1))  # fmt: skip
    # L3 = (s+1)^2/s^3: phase -270 + 2 atan(w); |L| = 1 where 1 + w^2 = w^3; -180 at w = 1, |L(j1)| = 2 -> GM = -6.02 dB.
    w3 = max(rt.real for rt in np.roots([1.0, -1.0, 0.0, -1.0]) if abs(rt.imag) < 1e-12)
    cases["L3"] = ([1.0, 2.0, 1.0], [1.0, 0.0, 0.0, 0.0], 180 - 270 + 2 * math.degrees(math.atan(w3)),
                   -20 * math.log10(2.0))  # fmt: skip
    for name, (num, den, pm, gm) in cases.items():
        m = io(num, den).margins()
        out[name] = {"pm_exact": pm, "pm_nervus": float(m.phase_margin_deg), "gm_exact_db": gm,
                     "gm_nervus_db": float(m.gain_margin_db),
                     "pm_err": abs(float(m.phase_margin_deg) - pm),
                     "gm_err": 0.0 if math.isinf(gm) and math.isinf(m.gain_margin_db) else abs(float(m.gain_margin_db) - gm)}  # fmt: skip
    return out


# ================================================================================================ 7. code generation


def codegen() -> dict[str, Any]:
    from nervus.codegen.sil import back_to_back, find_compiler

    out: dict[str, Any] = {"compiler": find_compiler() or "none"}
    if find_compiler() is None:
        return out
    rng = np.random.default_rng(1)
    steps = 2000
    d = Diagram("controller")
    e = d.add(Inport(port=1), name="e")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="zoh")
    pid = d.add(DiscretePID(P=2.5, I=25.0, D=0.01, N=50.0, upper=24.0, lower=-24.0, anti_windup="clamping",
                            sample_time=0.01), name="pid")  # fmt: skip
    filt = d.add(DiscreteTransferFunction(numerator=[0.2, 0.1], denominator=[1.0, -0.7], sample_time=0.01), name="f")
    lut = d.add(Lookup1D(breakpoints=[-30, -10, 0, 10, 30], table=[-1.0, -0.6, 0.0, 0.5, 1.0]), name="lut")
    d.chain(e, z, pid, filt, lut, d.add(Outport(port=1), name="y"))
    with tempfile.TemporaryDirectory() as tmp:
        res = back_to_back(d, {"e": rng.uniform(-20, 20, steps)}, steps, tolerance=1e-12, folder=tmp)
        out["sil_max_abs_diff"] = float(max(res.max_abs_error.values()))
        out["sil_steps"] = steps

    # Fixed point: fixdt(1, 16, 8), round to nearest: |y - u| <= LSB/2 = 2^-9 inside the range, in Python and in C.
    lsb = 2.0**-8
    u_val = rng.uniform(-100.0, 100.0, steps)
    d = Diagram("quantizer")
    u = d.add(Inport(port=1), name="u")
    z = d.add(ZeroOrderHold(sample_time=0.01), name="zoh")
    q = d.add(DataTypeConversion(output_type="fixdt(1, 16, 8)", rounding="nearest"), name="q")
    d.chain(u, z, q, d.add(Outport(port=1), name="y"))
    with tempfile.TemporaryDirectory() as tmp:
        res = back_to_back(d, {"u": u_val}, steps, tolerance=0.0, folder=tmp)
        y_py, y_c = np.ravel(res.python["y"]), np.ravel(res.c["y"])
    out["fixed_point_max_err_lsb"] = float(np.max(np.abs(y_py - u_val)) / lsb)
    out["fixed_point_c_vs_python_max"] = float(np.max(np.abs(y_c - y_py)))
    return out


# ================================================================================================ driver


def run() -> dict[str, Any]:
    FIG.mkdir(exist_ok=True)
    results: dict[str, Any] = {}
    for name, fn in (("solvers", solvers), ("events", events), ("discrete", discrete), ("physical", physical),
                     ("fluid", fluid), ("linear_analysis", linear_analysis), ("codegen", codegen)):  # fmt: skip
        try:
            results[name] = fn()
        except Exception as exc:  # record a failure as a finding instead of hiding it
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
        print(name, json.dumps(results[name], default=float)[:400])
    return results


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(HERE))
    path = HERE / "results.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["verification_suite"] = run()
    path.write_text(json.dumps(data, indent=2, default=float), encoding="utf-8")
