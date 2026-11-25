"""The 12 V charging system on a winter night: battery, alternator, loads and electrical energy management.

On a cold dark evening a car needs about 70 A: the ECUs, ignition and fuel pump (20 A), the headlights (10 A), the
blower (15 A), the heated rear window (15 A) and the seat heaters (10 A). The alternator (120 A at full speed) is
driven by the engine through a 2.5:1 belt, and at idle (800 rpm) it can deliver only about 24 A, so in a traffic jam
the battery supplies the rest and discharges.

The **energy management** function of the engine ECU (a State Chart, every 200 ms) watches the battery current,
measured by an intelligent battery sensor at the battery terminal:

* **normal**: idle at 800 rpm;
* **boost**: the battery has been discharging by more than 10 A for 3 s: raise the idle speed to 1100 rpm (the
  alternator then delivers about 42 A);
* **shed**: still discharging after 5 s more: also switch off the heated rear window and the seat heaters (25 A);
* back to **normal** when the battery charges by more than 10 A again (after at least 10 s in the state).

The scenario: 4 minutes in a traffic jam (idle), then 4 minutes of driving at 2000 rpm. Set ``energy_mgmt`` = 0 to
switch the function off.

Run with ``uv run python examples/charging_system.py``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from nervus import Diagram, SimResult, save, simulate
from nervus.blocks import (
    Alternator,
    Battery,
    Constant,
    CurrentSensor,
    ElectricalReference,
    FromWorkspace,
    Gain,
    Outport,
    PythonFunction,
    Scope,
    SpeedSource,
    StateChart,
    UnitDelay,
    VariableResistor,
    VoltageSensor,
    ZeroOrderHold,
)
from nervus.core import Check, TestCase, Variable

HERE = Path(__file__).parent
TS = 0.2

CHART = """
input i_bat, enabled
output idle_rpm = 800, shed = 0
param i_discharge, i_charge, idle_normal, idle_boost

state normal
  entry: idle_rpm = idle_normal; shed = 0
state boost
  entry: idle_rpm = idle_boost; shed = 0
state shed_loads
  entry: idle_rpm = idle_boost; shed = 1

transition normal -> boost: enabled > 0.5 and i_bat < -i_discharge and after(15)
transition boost -> shed_loads: i_bat < -i_discharge and after(25)
transition boost -> normal: i_bat > i_charge and after(50)
transition shed_loads -> normal: i_bat > i_charge and after(50)
"""

LOADS = '''def f(t, shed):
    """The electrical loads as one resistance [Ω]: 45 A always (ECUs, lights, blower), 25 A sheddable (heated
    rear window, seat heaters), at a nominal 13.5 V."""
    current = p[0] + (0.0 if shed > 0.5 else p[1])
    return 13.5 / current
'''

ENGINE = '''def f(t, idle_rpm, driving):
    """Engine speed [rad/s]: the idle speed the ECU asks for in the traffic jam, 2000 rpm when driving."""
    rpm = 2000.0 if driving > 0.5 else idle_rpm
    return rpm * 2 * math.pi / 60
'''

CONSTANTS = {
    "I_base": (45.0, "A", "Loads that are always on: ECUs, ignition, fuel pump, headlights, blower"),
    "I_comfort": (25.0, "A", "Sheddable loads: heated rear window and seat heaters"),
    "i_discharge": (10.0, "A", "Battery discharging by more than this: raise the idle speed (after 3 s), then shed"),
    "i_charge": (10.0, "A", "Battery charging by more than this for 10 s: back to normal"),
    "idle_normal": (800.0, "rpm", "Normal idle speed"),
    "idle_boost": (1100.0, "rpm", "Idle speed for more charging current"),
    "energy_mgmt": (1.0, "-", "1: energy management on, 0: off"),
    "soc0": (0.75, "-", "Battery state of charge at the start"),
}


def build() -> Diagram:
    d = Diagram("charging_system")
    d.settings.t_final = 480.0
    d.settings.solver = "BDF"
    d.settings.max_step = TS
    for name, (value, unit, desc) in CONSTANTS.items():
        d.workspace.add(Variable(name, value, desc, unit))
    # Circuit.
    gnd = d.add(ElectricalReference(), name="gnd")
    bat = d.add(Battery(capacity=60.0, R_internal=0.012, soc0="soc0"), name="battery")
    i_bat = d.add(CurrentSensor(), name="battery_current")
    alt = d.add(Alternator(K=500.0), name="alternator")
    loads = d.add(VariableResistor(R_min=0.05), name="loads")
    v_sense = d.add(VoltageSensor(), name="v_battery")
    engine = d.add(SpeedSource(controlled=True), name="crankshaft")
    for a, b in (((bat, "n"), (gnd, "p")), ((bat, "p"), (i_bat, "n")), ((i_bat, "p"), (alt, "p")),
                 ((alt, "n"), (gnd, "p")), ((loads, "p"), (alt, "p")), ((loads, "n"), (gnd, "p")),
                 ((v_sense, "p"), (alt, "p")), ((v_sense, "n"), (gnd, "p")), ((engine, "a"), (alt, "shaft"))):  # fmt: skip
        d.link(a, b)
    # Driving: a traffic jam, then driving.
    driving = d.add(FromWorkspace(time=[0, 240, 240.01, 480], data=[[0], [0], [1], [1]]), name="driving")
    eng = d.add(PythonFunction(code=ENGINE, num_inputs=2), name="engine_speed")
    load_r = d.add(PythonFunction(code=LOADS, num_inputs=1, parameters="[I_base, I_comfort]"), name="load_demand")
    # ECU: energy management (100 ms).
    i_ecu = d.add(ZeroOrderHold(sample_time=TS), name="battery_sensor")  # an intelligent battery sensor (IBS)
    on = d.add(Constant(value="energy_mgmt"), name="enabled")
    ems = d.add(StateChart(definition=CHART, sample_time=TS), name="energy_management")
    d.chain(i_bat, i_ecu)
    d.connect(i_ecu, (ems, 0))
    d.connect(on, (ems, 1))
    # The ECU's outputs take effect at the next task (like the output stage of a real ECU).
    idle_out = d.add(UnitDelay(initial="idle_normal", sample_time=TS), name="idle_request")
    shed_out = d.add(UnitDelay(initial=0.0, sample_time=TS), name="load_relay")
    d.connect((ems, 0), idle_out)
    d.connect((ems, 1), shed_out)
    d.connect(idle_out, (eng, 0))
    d.connect(driving, (eng, 1))
    d.connect(eng, engine)
    d.connect(shed_out, load_r)
    d.connect(load_r, loads)
    rpm = d.add(Gain(gain="60 / (2 * pi)"), name="engine_rpm")
    d.chain(eng, rpm)
    scope = d.add(Scope(num_inputs=2), name="scope")
    d.connect(v_sense, (scope, 0))
    d.connect(bat, (scope, 1))
    outs = ((v_sense, "voltage"), (bat, "soc"), (i_bat, "battery_charging"), (alt, "alternator_current"), (rpm, "rpm"),
            ((ems, 1), "loads_shed"), ((ems, 2), "mode"))  # fmt: skip
    for k, (src, name) in enumerate(outs):
        d.connect(src, d.add(Outport(port=k + 1), name=name))
    positions = {"driving": (0, 0), "engine_speed": (150, 20), "crankshaft": (300, 20), "alternator": (450, 60),
                 "battery_current": (600, 0), "battery": (750, 0), "loads": (600, 140), "v_battery": (750, 140),
                 "gnd": (600, 260), "load_demand": (450, 220), "battery_sensor": (900, 140), "enabled": (900, 240),
                 "energy_management": (1050, 180), "idle_request": (1200, 120), "load_relay": (1200, 240), "engine_rpm": (300, -90), "scope": (900, -90)}  # fmt: skip
    for name, (x, y) in positions.items():
        d.block(name).meta["position"] = [float(x), float(y)]
    d.tests = [
        TestCase(
            "energy management protects the battery",
            "From 8.5 s into the traffic jam (loads shed at 8 s) the battery is no longer discharged by more than 5 A, and "
            "it charges when driving",
            stop_time=480.0,
            checks=[
                Check("bounds", "battery_charging", {"lower": -5.0, "t_from": 8.5, "t_to": 240.0}),
                Check("bounds", "battery_charging", {"lower": 10.0, "t_from": 245.0}),
            ],
        ),
    ]
    return d


def ah(res: SimResult, t0: float, t1: float) -> float:
    """Charge taken from the battery between t0 and t1 [Ah] (negative: it was charged)."""
    m = (res.t >= t0) & (res.t <= t1)
    return -float(np.trapezoid(np.ravel(res["battery_charging"])[m], res.t[m]) / 3600)


def main() -> dict[str, SimResult]:
    from nervus.testing import run_tests

    d = build()
    save(d, HERE / "charging_system.nervus")
    for r in run_tests(d):
        print(r.summary())
    out = {}
    for on in (1.0, 0.0):
        res = simulate(d, params={"energy_mgmt": on})
        out["on" if on else "off"] = res
        v = np.ravel(res["voltage"])[res.t > 1.0]
        print(f"energy management {'on ' if on else 'off'}: lowest voltage {v.min():.2f} V, "
              f"{ah(res, 0, 240):.2f} Ah from the battery in the jam, SOC {np.ravel(res['soc'])[-1]:.3f} at the end")  # fmt: skip
    return out


if __name__ == "__main__":
    main()
