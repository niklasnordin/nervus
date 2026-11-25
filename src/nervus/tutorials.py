"""The tutorials, by category: the single source for the tutorial overview, the docs navigation, the in-app help
list and the "at a glance" box and "Next" link of every tutorial (``scripts/gen_block_reference.py`` writes them).

Every tutorial is a page ``docs/tutorials/<slug>.md`` with a finished model in ``examples/``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tutorial:
    slug: str
    title: str
    category: str
    subcategory: str
    level: str  # beginner, intermediate, advanced
    minutes: int
    learn: str
    models: tuple[str, ...]
    before: tuple[str, ...] = ()
    """Slugs of tutorials to do first."""
    read_first: str = ""
    """A guide page to read first, as a docs link, e.g. ``[Physical modelling](../physical.md)``."""
    app_page: str = ""
    """Name of the in-app help page (default: ``tutorial_<slug with _>``)."""

    @property
    def help_page(self) -> str:
        return self.app_page or "tutorial_" + self.slug.replace("-", "_")


CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("Getting started", "Start here: the editor, and how to organise larger models.", [("", "")]),
    ("Control engineering", "The experiments of a control-engineering lab course, each with a plant you can "
     "touch in the lab and the controller design that goes with it.",
     [("Classic lab experiments", "")]),
    ("Powertrain and engine control", "ECU functions of a petrol engine and its transmission, as they run in "
     "production cars.",
     [("Engine management", ""), ("Transmission and driveline", "")]),
    ("Vehicle dynamics and energy", "How a vehicle moves, uses energy, and stays controllable.",
     [("Longitudinal dynamics and energy", ""), ("Chassis control", "")]),
    ("Electrics and electronics", "Circuits from the textbook to the car's wiring harness and the ECU's "
     "circuit board, simulated like in LTspice.",
     [("Circuit fundamentals", ""), ("Vehicle electrical system", ""), ("ECU hardware and PCB", "")]),
    ("ECU software, communication and diagnostics", "What happens inside and between ECUs: networks, "
     "calibration, diagnostics and the C code itself.",
     [("Vehicle networks", ""), ("Diagnostics", ""), ("From model to ECU code", "")]),
    ("Testing and verification", "Showing that a function does what it should: requirements, tests and "
     "tolerances.", [("", "")]),
]  # fmt: skip

_ENGINE = "Powertrain and engine control"
_VEH = "Vehicle dynamics and energy"
_EL = "Electrics and electronics"
_ECU = "ECU software, communication and diagnostics"

TUTORIALS: list[Tutorial] = [
    # ------------------------------------------------------------------ getting started
    Tutorial("first-model", "Your first model: a cruise control", "Getting started", "", "beginner", 30,
             "the editor, blocks and wires, workspace variables, scopes, the Data Inspector, PI control and "
             "integrator windup", ("cruise_control.nervus",)),
    Tutorial("component-libraries", "Reusable components with subsystems, masks and libraries", "Getting started",
             "", "beginner", 30, "subsystems, masks (parameters for a subsystem), component libraries and linked "
             "copies, updating every model from one library", ("engine_comparison.nervus",), ("first-model",)),
    Tutorial("python-scripting", "Scripting and testing models from Python", "Getting started", "", "beginner", 25,
             "loading and running a model from a script, changing parameters for one run or for good, parameter "
             "sweeps, running a model's test cases in pytest, building a model in code", ("cruise_control.nervus",),
             ("first-model",)),
    # ------------------------------------------------------------------ control labs
    Tutorial("mass-spring-damper", "Position control of a mass-spring-damper", "Control engineering",
             "Classic lab experiments", "beginner", 30,
             "what the P, I and D terms do, natural frequency and damping ratio, steady-state error, actuator "
             "limits and anti-windup", ("mass_spring_damper_pid.nervus",), ("first-model",)),
    Tutorial("dc-motor", "DC motor speed and position control", "Control engineering", "Classic lab experiments",
             "intermediate", 45, "system identification from a step response, PI design by pole-zero "
             "cancellation, PD design from damping and natural frequency, a physical motor model and a 1 kHz "
             "controller", ("dc_motor_speed.nervus", "dc_motor_position.nervus"), ("mass-spring-damper",)),
    Tutorial("coupled-tanks", "Level control of coupled water tanks", "Control engineering", "Classic lab experiments",
             "intermediate", 40, "a nonlinear process, linearization at an operating point, PI design for a "
             "second-order process, gain scheduling, actuator limits", ("coupled_tanks.nervus",),
             ("dc-motor",)),
    Tutorial("inverted-pendulum", "Balancing an inverted pendulum with state feedback", "Control engineering",
             "Classic lab experiments", "advanced", 45, "an unstable plant, state-space models, LQR state "
             "feedback, non-minimum-phase behaviour, redesigning a gain in the Console",
             ("inverted_pendulum.nervus",), ("dc-motor",)),
    # ------------------------------------------------------------------ engine
    Tutorial("engine-modes", "Engine operating modes", _ENGINE, "Engine management", "beginner", 30,
             "an ECU's mode logic as a State Chart: cranking, starter protection, idle, fuel cut-off, stall; "
             "calibration parameters in a chart; behaviour specified by test cases", ("engine_modes.nervus",),
             ("first-model",)),
    Tutorial("coolant-fan", "Radiator fan control", _ENGINE, "Engine management", "beginner", 25,
             "on/off control with hysteresis, a thermal model with a lookup table, sensor filtering and its "
             "delay, switching rate versus temperature swing", ("coolant_fan.nervus",), ("first-model",)),
    Tutorial("idle-speed-control", "Idle speed control", _ENGINE, "Engine management", "beginner", 45,
             "how an ECU holds the idle speed: a mean-value engine model, a PI controller with feed-forward, "
             "requirements as test cases, C code", ("idle_speed_control.nervus",), ("first-model",), app_page="tutorial_idle"),
    Tutorial("lambda-control", "Lambda control and fuel trims", _ENGINE, "Engine management", "intermediate", 45,
             "why petrol engines run at lambda = 1, closed-loop fuel control with a lambda sensor, short- and "
             "long-term fuel trims (as read over OBD-II), an ECU task in an atomic subsystem",
             ("lambda_control.nervus",), ("idle-speed-control",), app_page="tutorial_lambda"),
    Tutorial("ignition-knock", "Ignition timing and knock control", _ENGINE, "Engine management", "intermediate", 30,
             "spark advance from a speed/load map (2-D lookup table), knock and the knock limit, a knock "
             "controller that retards and recovers, noisy sensors", ("ignition_knock.nervus",),
             ("idle-speed-control", "lambda-control"), app_page="tutorial_ignition"),
    Tutorial("fuel-film", "Wall wetting and learned fuel trims", _ENGINE, "Engine management", "intermediate", 30,
             "fuel film dynamics (x-tau) and transient compensation, a switching lambda sensor, long-term trims "
             "learned per load cell (Adaptive Map)", ("fuel_film.nervus",), ("lambda-control",)),
    Tutorial("gear-shift", "Gear-shift logic", _ENGINE, "Transmission and driveline", "intermediate", 60,
             "an automatic gearbox's shift schedule, hysteresis, a State Chart in its own ECU task, kickdown and "
             "hills, fuel consumption per gear", ("gear_shift.nervus",), ("first-model", "drive-cycle"),
             "[State machines](../statecharts.md)"),
    Tutorial("truck", "A truck on a mountain road", _ENGINE, "Transmission and driveline", "intermediate", 30,
             "heavy-duty vehicles: a diesel's narrow torque band, a 12-speed automated gearbox (State Chart), "
             "cruise control, an exhaust brake on a descent", ("truck.nervus",), ("drive-cycle", "gear-shift")),
    # ------------------------------------------------------------------ vehicle
    Tutorial("drive-cycle", "A car on a drive cycle", _VEH, "Longitudinal dynamics and energy", "beginner", 40,
             "vehicle longitudinal dynamics, a driver model, standard drive cycles (ECE-15, WLTC, FTP-75), where "
             "the energy goes, fuel consumption", ("drive_cycle.nervus",), ("first-model",)),
    Tutorial("ev-range", "Electric vehicle range on the WLTP cycle", _VEH, "Longitudinal dynamics and energy",
             "beginner", 30, "the energy chain of an electric car, drivetrain efficiency, regenerative braking, the "
             "winter heater, consumption in kWh/100 km and range", ("ev_range.nervus",), ("drive-cycle",)),
    Tutorial("traction-control", "Tyre slip and traction control", _VEH, "Chassis control", "intermediate", 30,
             "tyre slip and Pacejka's magic formula, wheel and vehicle dynamics, a traction control ECU on dry, "
             "wet and snowy roads", ("traction_control.nervus",), ("first-model",), app_page="tutorial_traction"),
    Tutorial("abs", "Anti-lock braking (ABS)", _VEH, "Chassis control", "intermediate", 35,
             "braking slip and the tyre's friction peak, a quarter-car model, hydraulic pressure modulation, a "
             "threshold ABS as a State Chart, stopping distances on dry, wet and icy roads", ("abs_braking.nervus",),
             ("traction-control",)),
    # ------------------------------------------------------------------ electrics and electronics
    Tutorial("rc-rlc", "RC and RLC circuits", _EL, "Circuit fundamentals", "beginner", 30,
             "time constant and step response, cut-off frequency and the Bode plot, resonance, damping and the "
             "quality factor, physical circuit blocks and linear analysis", ("rc_filter.nervus", "rlc_circuit.nervus"),
             ("first-model",)),
    Tutorial("circuits", "Rectifier, relay driver, buck converter and active filter", _EL, "Circuit fundamentals",
             "advanced", 45, "circuit simulation like LTspice: a rectifier, a relay driver and its freewheeling "
             "diode, a buck converter, an active filter; SPICE netlists",
             ("rectifier.nervus", "relay_driver.nervus", "buck_converter.nervus", "active_filter.nervus"),
             ("rc-rlc", "physical-models")),
    Tutorial("physical-models", "Starter motor and electronic throttle", _EL, "Vehicle electrical system",
             "intermediate", 45, "physical modelling (circuits, shafts, springs connected by links instead of "
             "signals): cranking an engine on a weak battery, a drive-by-wire throttle servo",
             ("starter_motor.nervus", "electronic_throttle.nervus"), ("first-model",),
             "[Physical modelling](../physical.md)", app_page="tutorial_physical"),
    Tutorial("charging-system", "The 12 V charging system and energy management", _EL, "Vehicle electrical system",
             "intermediate", 35, "the power balance of battery, alternator and loads, alternator output versus engine "
             "speed, an electrical energy management function (idle boost and load shedding) as a State Chart",
             ("charging_system.nervus",), ("physical-models",)),
    Tutorial("ntc-sensor", "An NTC temperature sensor interface", _EL, "ECU hardware and PCB", "intermediate", 35,
             "a thermistor in a voltage divider, the ECU's pull-up, input filter and ADC, a conversion table, "
             "resolution over the range, why software and hardware must match", ("ntc_sensor.nervus",),
             ("first-model",)),
    Tutorial("injector-driver", "The injector driver power stage", _EL, "ECU hardware and PCB", "intermediate", 30,
             "a low-side switch driving a solenoid injector, opening delay versus battery voltage, freewheeling "
             "diode versus clamp, why injection needs a battery voltage correction", ("injector_driver.nervus",),
             ("rc-rlc",)),
    # ------------------------------------------------------------------ ECU software
    Tutorial("obd-diagnostics", "On-board diagnostics and trouble codes", _ECU, "Diagnostics", "intermediate", 35,
             "diagnostic monitors: debouncing, enable conditions, pending and confirmed codes, two-trip detection, "
             "the MIL, readiness; reading the codes with an OBD tool", ("obd_ecu.nervus",), ("engine-modes",),
             "[OBD-II live data](../obd.md)"),
    Tutorial("sensor-diagnostics", "Sensor fault diagnosis and substitute values", _ECU, "Diagnostics",
             "intermediate", 30, "range checks on a sensor signal, open circuit and short to ground (P0117/P0118), "
             "debouncing, substitute values for limp-home, fault injection in a test", ("ntc_sensor.nervus",),
             ("ntc-sensor",)),
    Tutorial("discrete-control", "From continuous to discrete: sample times", _ECU, "From model to ECU code",
             "beginner", 30, "what running a controller every T milliseconds does to a loop, the zero-order hold, "
             "choosing a sample time, discrete poles and stability, aliasing and anti-aliasing filters",
             ("discrete_control.nervus", "aliasing.nervus"), ("first-model",),
             "[Sample times](../concepts.md#sample-times)"),
    Tutorial("model-to-code", "From model to ECU C code", _ECU, "From model to ECU code", "intermediate", 40,
             "an ECU software component with an interface, generated C code and its structure, calibration "
             "parameters and the A2L file, a back-to-back (SIL) test of the code, a MISRA C check", ("throttle_ecu.nervus",),
             ("first-model",), "[C code generation](../codegen.md)"),
    Tutorial("fixed-point", "A sensor signal chain in fixed point", _ECU, "From model to ECU code", "intermediate",
             30, "fixed-point data types (word length, fraction bits, resolution, range), an ADC model, "
             "quantization error, and the silent failure of a badly chosen range", ("fixed_point_sensor.nervus",),
             ("model-to-code",), "[Data types and fixed point](../datatypes.md)"),
    Tutorial("fmi", "Exchanging models with FMI", _ECU, "From model to ECU code", "intermediate", 30,
             "FMI 2.0 co-simulation FMUs: exporting a subsystem, running it in another tool (fmpy, Simulink), "
             "importing an FMU as a block, and the communication-step delay of co-simulation",
             ("fmi_plant.nervus",), ("model-to-code",)),
    Tutorial("can-network", "A vehicle network (CAN)", _ECU, "Vehicle networks", "advanced", 40,
             "ECUs on simulated CAN buses: DBC files, arbitration and bus load, a gateway, rest-bus simulation, "
             "E2E protection, fault injection and timeouts, the bus trace", ("can_network.nervus",),
             ("first-model",), "[CAN communication](../can.md)"),
    Tutorial("xcp-calibration", "Live calibration over XCP", _ECU, "Vehicle networks", "intermediate", 35,
             "how calibration tools work: XCP on CAN, A2L files, measuring and changing values in a running ECU, "
             "a command-line calibration tool, real-time simulation", ("xcp_calibration.nervus",),
             ("first-model",)),
    # ------------------------------------------------------------------ testing and verification
    Tutorial("requirements-testing", "Requirements-based testing and coverage", "Testing and verification", "",
             "intermediate", 40, "requirements imported from a CSV file, traceability from requirements to blocks "
             "and tests, model coverage to find untested behaviour, closing the gap, review reports",
             ("idle_requirements.nervus",), ("idle-speed-control",)),
    Tutorial("robustness-monte-carlo", "Robustness and tolerances with Monte Carlo", "Testing and verification", "",
             "intermediate", 30, "parameter spread in a vehicle range, Monte Carlo studies in the editor and in "
             "Python, custom metrics, why the average hides the failures", ("cruise_control.nervus",),
             ("first-model",)),
]  # fmt: skip


def by_slug() -> dict[str, Tutorial]:
    return {t.slug: t for t in TUTORIALS}


def ordered() -> list[Tutorial]:
    """All tutorials in the order of the categories and subcategories."""
    out = []
    for cat, _intro, subs in CATEGORIES:
        for sub, _ in subs:
            out += [t for t in TUTORIALS if t.category == cat and t.subcategory == sub]
    return out
