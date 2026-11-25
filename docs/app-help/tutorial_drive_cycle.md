# Tutorial: A car on a drive cycle

| | |
|---|---|
| **You will learn** | vehicle longitudinal dynamics, a driver model, standard drive cycles (ECE-15, WLTC, FTP-75), where the energy goes, fuel consumption |
| **Before this** | *Your first model: a cruise control* |
| **Level and time** | beginner, about 40 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/drive_cycle.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Vehicle dynamics and energy ▸ Longitudinal dynamics and energy (*all tutorials*) |

This tutorial follows `examples/drive_cycle.py`: a car with a driver model drives the **ECE-15** urban
cycle. You will see where the energy for city driving goes, and how vehicle mass changes it.

```bash
uv run python examples/drive_cycle.py      # simulates, saves drive_cycle.nervus, runs the tests
uv run nervus examples/drive_cycle.nervus  # open it in the editor
```

## Drive cycles

A drive cycle is a speed-over-time profile that every car of a type approval test has to drive on a
dynamometer. Fuel consumption and emissions figures come from such cycles. The ECE-15 urban cycle (the city
part of the old European NEDC, also called UDC) is 195 s long, reaches 50 km/h and covers 994 m. Today's
WLTP cycle is more dynamic: 30 minutes, up to 131 km/h. The official WLTC class 3b and FTP-75 tables come
with Nervus: use the **Drive Cycle** block (Sources), or `build("wltc3b")` in the example (see below).

## The model

**driver**: like a test driver following the speed trace on a screen, a PI controller acts on the speed
error. A feed-forward term adds the pedal needed for the cycle's acceleration, $a_{ref}\,m/F_{max}$. The
pedal is limited to −1 (full brake) … +1 (full throttle).

**vehicle**: Newton's second law along the road:

$$ m\,\frac{dv}{dt} = F_{wheel} - \underbrace{\tfrac{1}{2}\rho\,c_d A\,v^2}_{\text{aerodynamic drag}} - \underbrace{m\,g\,c_{rr}}_{\text{rolling resistance}} $$

The wheel force comes from the pedal. Traction is limited by the maximum force (wheel slip, first gear) and by
the engine power, $F \le P_{max}/v$, and the brakes provide up to $F_{brake,max}$. The rolling resistance uses
$\tanh(10\,v)$ instead of a sign function, so it fades out smoothly at standstill. The block
`wheel_energy_J` integrates the positive wheel power: the energy the engine must deliver.

## Results

![](../images/blocks/drive_cycle.png)

| | 1300 kg | 1800 kg |
|---|---|---|
| distance | 993 m | 992 m |
| max. speed error | 0.75 km/h | 1.0 km/h |
| energy at the wheels | 340 kJ = 9.5 kWh/100 km | 460 kJ = 12.9 kWh/100 km |

Where does the energy go? For the 1300 kg car:

| | Estimate | |
|---|---|---|
| accelerating: $\tfrac{1}{2} m v^2$ for the three accelerations to 15, 32 and 50 km/h | ≈ 188 kJ | lost again as heat in the brakes (the negative power) |
| rolling resistance: $m g c_{rr} \times 994$ m | ≈ 152 kJ | the simulation gives the same |
| aerodynamic drag: $\int F_{aero}\,v\,dt$ from the simulation | ≈ 39 kJ | small at city speeds |

These add up to 379 kJ, a bit more than the 340 kJ the engine delivers: while the car slows down, part of
the rolling and drag losses is paid from its kinetic energy instead of by the brakes. Both big terms grow
with the mass. That is why mass matters so much in city driving, and why hybrids and electric cars, which
recover braking energy, gain most in the city.

The example also prints a rough fuel estimate: wheel energy / (25 % average efficiency × 43 MJ/kg) gives
about 4.3 l/100 km. A real car uses clearly more in the city. It burns fuel while idling (almost a third of
this cycle is standstill), and its efficiency at light load is much lower than 25 %. Modelling that needs an engine
efficiency map: a 2-D lookup table over speed and torque, the next step.

## WLTP and FTP-75

`build("wltc3b")` and `build("ftp75")` replace the ECE-15 by the **Drive Cycle** block with the official
second-by-second tables (WLTC class 3b from UNECE GTR No. 15, FTP-75 from the US EPA); its second output, the
cycle's acceleration, is the driver's feed-forward. The same car and driver:

| Cycle | distance | max. speed error | energy at the wheels | fuel (25 % efficiency) |
|---|---|---|---|---|
| ECE-15 | 0.99 km | 0.75 km/h | 9.5 kWh/100 km | 4.3 L/100 km |
| FTP-75 | 17.77 km | 1.3 km/h | 11.3 kWh/100 km | 5.1 L/100 km |
| WLTC class 3b | 23.26 km | 1.3 km/h | 13.0 kWh/100 km | 5.8 L/100 km |

The WLTC needs more energy per kilometre: its extra-high phase runs at up to 131 km/h, where the drag grows with
the square of the speed. Use `nervus.cycles.cycle_stats("wltc3b")` for the cycle's own figures.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

| Try | What you will see |
|---|---|
| `m = 1800` | about 35 % more energy: accelerating and rolling grow with the mass, only drag does not |
| `cd_A = 0.9` (a van) | only 3 % more energy: drag matters at motorway speeds, not at 50 km/h |
| `P_max = 30e3` | still enough power for this gentle cycle; on the WLTC the car falls behind in the extra-high phase |
| `Kp_driver = 0.1` | a lazier driver: the speed error reaches 2.1 km/h, just enough to fail the tracking test |

The test cases in the **Test Manager** check the tracking, the distance and the energy of both masses.

## Next

Next in this topic: *Electric vehicle range on the WLTP cycle*. Or pick another one from the *tutorial overview*.
