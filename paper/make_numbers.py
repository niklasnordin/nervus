"""Write ``numbers.tex`` from ``results.json``: every number quoted in ``validation.tex`` comes from here.

uv run python paper/make_numbers.py
"""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

HERE = Path(__file__).parent


def sci(x: float) -> str:
    """A number for the text: 0 stays 0, small or large values in scientific notation."""
    if x == 0:
        return "$0$"
    if math.isinf(x):
        return "$\\infty$"
    mant, exp = f"{x:.1e}".split("e")
    return f"${mant}\\times10^{{{int(exp)}}}$"


def _failed(section: dict) -> str:
    return section.get("error", "")


def verification_numbers(vs: dict, c1: dict) -> dict[str, str]:
    m: dict[str, str] = {}
    conv = vs["solvers"]["convergence"]
    m["EulerOrder"] = f"{conv['euler']['observed_order']:.2f}"
    m["RKOrder"] = f"{conv['rk4']['observed_order']:.2f}"
    m["RKFFSlope"] = f"{conv['RK45']['slope']:.2f}"
    d = vs["discrete"]
    m["StabExact"] = f"{1000 * d['stability_limit_exact_s']:.3f}"
    m["StabSim"] = f"{1000 * d['stability_limit_from_simulation_s']:.3f}"
    e = vs["events"]
    m["SatErrOn"], m["SatErrOff"] = sci(e["saturated_err_zc_on"]), sci(e["saturated_err_zc_off"])
    cg = vs["codegen"]
    if "sil_max_abs_diff" in cg:
        m["SilDiff"] = sci(cg["sil_max_abs_diff"])
        m["SilSteps"] = f"{cg['sil_steps']:,}".replace(",", "\\,")
        m["FixLSB"] = f"{cg['fixed_point_max_err_lsb']:.4f}"
    m["HydTHalf"] = f"{vs['physical']['hydraulic_t_half_s']:.3f}"
    return m


def verification_rows(vs: dict, c1: dict) -> list[str]:
    s, e, d, p, f, la, cg = (vs[k] for k in ("solvers", "events", "discrete", "physical", "fluid", "linear_analysis",
                                             "codegen"))  # fmt: skip
    conv = s["convergence"]
    rows = [
        ("Solvers", "first-order lag, unit step", "$1-\\mathrm{e}^{-t/\\tau}$", sci(s["first_order_max_err"])),
        ("", "second-order system, $\\zeta = 0.2$", "closed form", sci(s["second_order_max_err"])),
        ("", "Euler, $h = 0.1/2^k$", "observed order", f"{conv['euler']['observed_order']:.2f}"),
        ("", "Runge--Kutta 4, $h = 0.1/2^k$", "observed order", f"{conv['rk4']['observed_order']:.2f}"),
        ("Events", "thrown body on an integrator limit", "impact time", sci(e["impact_time_error_s"]) + " s"),
        ("", "integral of a saturated sine", "closed form", sci(e["saturated_err_zc_on"])),
        (
            "Discrete",
            "PI loop, $T = 10$ ms and 25 ms",
            "exact $z$-domain recursion",
            sci(max(d["T10ms_max_err"], d["T25ms_max_err"])) + " rad/s",
        ),
        (
            "",
            "stability limit (pole at $z=-1$)",
            f"{1000 * d['stability_limit_exact_s']:.3f} ms",
            f"{1000 * abs(d['stability_limit_from_simulation_s'] - d['stability_limit_exact_s']):.3f} ms",
        ),
        ("Physical", "RC circuit", "exponential", sci(p["rc_max_err_V"]) + " V"),
        ("", "series RLC circuit", "damped sine", sci(p["rlc_max_err_A"]) + " A"),
        ("", "thermal capacity cooling", "exponential", sci(p["thermal_max_err_K"]) + " K"),
        ("", "inertia, damper, torque", "exponential", sci(p["rotational_max_err_rad_s"]) + " rad/s"),
        ("", "volume filled through an orifice", "implicit closed form", sci(p["hydraulic_max_err_bar"]) + " bar"),
        (
            "Fluid",
            "Torricelli draining, constant area",
            "closed form",
            sci(c1["verification"]["auto"]["max_abs_error_cm"]) + " cm",
        ),
        ("", "sphere, constant $C_D$: speed", "$v_t \\tanh(t/T)$", sci(f["sphere_v_max_err"]) + " m/s"),
        ("", "sphere, constant $C_D$: height", "$v_t T \\ln\\cosh(t/T)$", sci(f["sphere_z_max_err"]) + " m"),
        (
            "Linear",
            "Bode, $1/(0.5s+1)$",
            "magnitude, phase",
            sci(la["bode_mag_max_err_db"]) + " dB, " + sci(la["bode_phase_max_err_deg"]) + "$^\\circ$",
        ),
    ]
    for key, label in (("L1", "$1/(s(s+1))$"), ("L2", "$10/(s(s+1)(0.1s+1))$"), ("L3", "$(s+1)^2/s^3$")):
        c = la[key]
        gm = "$\\infty$" if math.isinf(c["gm_exact_db"]) else f"${c['gm_exact_db']:.3f}$ dB"
        gm_err = "--" if math.isinf(c["gm_exact_db"]) else sci(c["gm_err"]) + " dB"
        rows.append(("", f"margins, {label}", f"PM {c['pm_exact']:.3f}$^\\circ$, GM {gm}",
                     sci(c["pm_err"]) + "$^\\circ$, " + gm_err))  # fmt: skip
    if "sil_max_abs_diff" in cg:
        rows.append(("Code", "generated C vs.\\ simulation", "identical", sci(cg["sil_max_abs_diff"])))
        rows.append(("", "fixed point, fixdt(1,16,8)", "$\\le$ 1/2 LSB", f"{cg['fixed_point_max_err_lsb']:.4f} LSB"))
    return [" & ".join(r) + " \\\\" for r in rows]


def main() -> None:
    r = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    c1, c2 = r["case1"], r["case2"]
    tr, te, ver = c1["train"], c1["test"], c1["verification"]
    m: dict[str, str] = {
        "NervusVersion": r["nervus_version"],
        "Cfit": f"{c1['c_fit']:.3f}",
        "CfitNinety": f"{c1['c_fit_window_90pct']:.3f}",
        "CfitSeventyFive": f"{c1['c_fit_window_75pct']:.3f}",
        "AreaTop": f"{c1['geometry']['area_top_cm2']:.1f}",
        "AreaBottom": f"{c1['geometry']['area_bottom_cm2']:.1f}",
        "RTop": f"{c1['geometry']['top_lwr_cm'][2]:.2f}",
        "RBottom": f"{c1['geometry']['bottom_lwr_cm'][2]:.2f}",
        "Ro": f"{c1['geometry']['r_o_cm']:.4f}",
        "HCountsMin": f"{c1['h_at_508_counts_cm']:.2f}",
        "TrainN": f"{tr['n_samples']:,}".replace(",", "\\,"),
        "TestN": f"{te['n_samples']:,}".replace(",", "\\,"),
        "TrainNfit": f"{tr['fit_window']['n']:,}".replace(",", "\\,"),
        "TestNwin": f"{te['window']['n']:,}".replace(",", "\\,"),
        "TrainTend": f"{tr['t_end_s']:.0f}",
        "TestTend": f"{te['t_end_s']:.0f}",
        "TrainTcut": f"{tr['t_cut_s']:.0f}",
        "TestTcut": f"{te['t_cut_s']:.0f}",
        "TrainHcut": f"{tr['h_at_cut_cm']:.2f}",
        "TestHcut": f"{te['h_at_cut_cm']:.2f}",
        "TrainHzero": f"{tr['h0_cm']:.2f}",
        "TestHzero": f"{te['h0_cm']:.2f}",
        "TrainRMSE": f"{tr['fit_window']['rmse_cm']:.2f}",
        "TrainMAE": f"{tr['fit_window']['mae_cm']:.2f}",
        "TrainMax": f"{tr['fit_window']['max_abs_cm']:.2f}",
        "TrainAllRMSE": f"{tr['all_points']['rmse_cm']:.2f}",
        "TrainAllMax": f"{tr['all_points']['max_abs_cm']:.2f}",
        "TestRMSE": f"{te['window']['rmse_cm']:.2f}",
        "TestMAE": f"{te['window']['mae_cm']:.2f}",
        "TestMax": f"{te['window']['max_abs_cm']:.2f}",
        "TestBias": f"{te['window']['bias_cm']:+.2f}",
        "TestAllRMSE": f"{te['all_points']['rmse_cm']:.2f}",
        "TestAllMAE": f"{te['all_points']['mae_cm']:.2f}",
        "TestAllMax": f"{te['all_points']['max_abs_cm']:.2f}",
        "TrainDSMAE": f"{tr['downsampled_17']['mae_cm']:.2f}",
        "TestDSMAE": f"{te['downsampled_20']['mae_cm']:.2f}",
        "TrainTthreeModel": f"{tr['t_to_3cm_model_s']:.0f}",
        "TrainTthreeData": f"{tr['t_to_3cm_data_s']:.0f}",
        "TestTthreeModel": f"{te['t_to_3cm_model_s']:.0f}",
        "TestTthreeData": f"{te['t_to_3cm_data_s']:.0f}",
        "VerAuto": f"{ver['auto']['max_abs_error_cm']:.1e}",
        "VerRKfour": f"{ver['rk4']['max_abs_error_cm']:.1e}",
        "VerScipy": f"{ver['variable_area_vs_scipy_radau_max_abs_cm']:.1e}",
        "VerTempty": f"{ver['t_empty_constant_area_s']:.0f}",
        "RhoAir": f"{c2['rho_air']:.3f}",
        "ResidCorr": f"{c1['residual_vs_h_correlation']:.2f}",
        "ResidBins": str(c1["residual_vs_h_bins"]),
    }
    # Derived quantities (simple arithmetic on the results, kept here so that the text quotes no hand-made number).
    m["CdEighth"] = f"{c1['c_fit'] * ((5 / 64) / (1 / 8)) ** 2:.2f}"  # same C_d r_o^2 with a 1/8 in hole
    slant = (14.6 - 13.4) / 2  # cm, horizontal offset of the wall over the height (authors' tape measurements)
    m["SlantFactor"] = f"{math.sqrt(28.6**2 - slant**2) / 28.6:.4f}"
    m["LevelDrop"] = f"{te['h0_cm'] - te['h_at_cut_cm']:.0f}"
    m["CalibSplineMax"] = f"{10 * c1['calibration_spline_max_residual_cm']:.1f}"  # mm
    m["CalibSplineRMS"] = f"{10 * c1['calibration_spline_rms_residual_cm']:.1f}"  # mm
    steps = c1["calibration_step_counts_per_cm"]
    m["CalibStepMedian"] = f"{statistics.median(steps):.1f}"
    m["CalibStepMax"] = f"{max(steps):.0f}"
    # Dry air at 101.325 kPa: the temperatures at which rho = 1.23 and 1.15 kg/m^3 (ideal gas, R = 287.05 J/kg/K).
    m["RhoTempLow"] = f"{101325 / (287.05 * 1.23) - 273.15:.0f}"
    m["RhoTempHigh"] = f"{101325 / (287.05 * 1.15) - 273.15:.0f}"
    cd5 = c2["cd_at_re_5e4"]
    m["CdHandoutFiftyK"] = f"{cd5['handout']:.2f}"
    m["CdMisprintFiftyK"] = f"{cd5['sheet_misprint']:.2f}"
    for key, name in (("air_globes", "Globe"), ("bladders", "Bladder")):
        g = c2[key]
        m[f"{name}MeanErr"] = f"{g['mean_error_s']:+.2f}"
        m[f"{name}MAE"] = f"{g['mean_abs_error_s']:.2f}"
        m[f"{name}Max"] = f"{g['max_abs_error_s']:.2f}"
        m[f"{name}Rel"] = f"{100 * g['mean_rel_error']:+.1f}"
        m[f"{name}ReMin"] = f"{g['re_range'][0] / 1e4:.1f}"
        m[f"{name}ReMax"] = f"{g['re_range'][1] / 1e4:.1f}"
        m[f"{name}CdEff"] = f"{g['cd_effective_mean']:.2f}"
        m[f"{name}CdEffMin"] = f"{g['cd_effective_range'][0]:.2f}"
        m[f"{name}CdEffMax"] = f"{g['cd_effective_range'][1]:.2f}"
        m[f"{name}CdModel"] = f"{g['cd_model_mean']:.2f}"
        m[f"{name}CdRatio"] = f"{100 * (g['cd_effective_mean'] / g['cd_model_mean'] - 1):.0f}"
        m[f"{name}VRatioMin"] = f"{100 * g['v_ratio_min']:.1f}"
        m[f"{name}TninetyFive"] = f"{100 * g['t95_fraction_max']:.0f}"
    merc = c2["mercury_globes"]
    m["MercTmin"], m["MercTmax"] = f"{merc['t_pred_range_s'][0]:.2f}", f"{merc['t_pred_range_s'][1]:.2f}"
    m["MercReMin"], m["MercReMax"] = f"{merc['re_range'][0] / 1e4:.1f}", f"{merc['re_range'][1] / 1e4:.1f}"
    vac = {round(o["height_m"], 1): o["t_vacuum_s"] for o in c2["objects"]}
    m["TvacThirteen"], m["TvacFourteen"] = f"{vac[67.1]:.2f}", f"{vac[82.9]:.2f}"

    # Per-object table rows, and the spread of each object's sensitivity runs.
    rows, spreads = [], []
    for o in c2["objects"]:
        kind = {"mercury": "Hg globe", "air-filled": "glass globe", "bladder": "bladder"}
        label = next(v for k, v in kind.items() if k in o["object"])
        meas = "4" + {"4-": "$^-$", "4+": "$^+$"}.get(o["time_text"], "") if "mercury" in o["object"] else (
            f"{o['t_measured_s']:.2f}")  # fmt: skip
        if o["t_observed_s"] is not None and o["t_corrected_s"] is not None:
            meas = f"{o['t_observed_s']:.2f} ({o['t_corrected_s']:.2f})"
        sens = [*o["sensitivity_t_s"].values(), o["t_s"]]
        spreads.append(max(sens) - min(sens))
        err = "--" if o["error_s"] is None or "mercury" in o["object"] else f"${o['error_s']:+.2f}$"
        cdeff = "--" if o.get("cd_effective") is None else f"{o['cd_effective']:.2f}"
        rows.append(
            f"{o['exper']} & {label} {o['n']} & {o['m_app_kg'] * 1e3:.1f} & {o['d_m'] * 1e3:.0f} & {meas} & "
            f"{o['t_s']:.2f} & {err} & {o['re_impact'] / 1e4:.1f} & {cdeff} \\\\"
        )
    light = [s for s, o in zip(spreads, c2["objects"], strict=True) if "mercury" not in o["object"]]
    m["SensSpreadMax"] = f"{max(light):.2f}"
    reached = 0
    for key, name in (("air-filled", "Globe"), ("bladder", "Bladder")):
        group = [o for o in c2["objects"] if key in o["object"]]
        ups = [max(o["sensitivity_t_s"].values()) - o["t_s"] for o in group]
        m[f"{name}SensUp"] = f"{max(ups):.2f}"
        m[f"{name}MinDeficit"] = f"{min(abs(o['error_s']) for o in group):.2f}"
        reached += sum(max(o["sensitivity_t_s"].values()) >= o["t_measured_s"] for o in group)
    m["SensReached"] = str(reached)
    m["SensReachedText"] = (
        "none of them brings any of the eleven light objects up to its measured time"
        if reached == 0
        else f"{reached} of the eleven light objects reach their measured time in at least one of them"
    )
    tmin, tmax = merc["t_pred_range_s"]
    m["MercT"] = f"{tmin:.2f}" if f"{tmin:.2f}" == f"{tmax:.2f}" else f"{tmin:.2f}--{tmax:.2f}"
    vs = r.get("verification_suite", {})
    if vs:
        m.update(verification_numbers(vs, c1))
    out = ["% Generated by make_numbers.py from results.json. Do not edit."]
    # Signed numbers in math mode, so that the minus sign is a minus and not a hyphen.
    m = {k: (f"${v}$" if re.fullmatch(r"[+-][0-9.]+", v) else v) for k, v in m.items()}
    out += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in m.items()]
    out.append("\\newcommand{\\StPaulsRows}{%\n" + "\n".join(rows) + "}")
    if vs:
        out.append("\\newcommand{\\VerRows}{%\n" + "\n".join(verification_rows(vs, c1)) + "}")
    (HERE / "numbers.tex").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {len(m)} numbers and {len(rows)} table rows")


if __name__ == "__main__":
    main()
