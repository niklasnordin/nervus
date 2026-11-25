# Tutorial: Idle speed control

| | |
|---|---|
| **You will learn** | how an ECU holds the idle speed: a mean-value engine model, a PI controller with feed-forward, requirements as test cases, C code |
| **Before this** | [Your first model: a cruise control](tutorial_first_model.md) |
| **Level and time** | beginner, about 45 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/idle_speed_control.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Powertrain and engine control ▸ Engine management ([all tutorials](tutorials.md)) |

This tutorial walks through `examples/idle_speed_control.py`. It's a complete engine-control loop: a
physical engine model, an ECU function that holds the idle speed, a calibration, requirement tests, and C
code. Open `examples/idle_speed_control.nervus` in the editor to follow along.

## 1. The plant: a mean-value engine model

A *mean-value engine model* (MVEM) averages over the engine cycle. It ignores individual combustion events
but captures how air, pressure and speed evolve, which is exactly what idle control needs.

| Part | Equation | Block(s) |
|---|---|---|
| Throttle flow | $\dot m_{th} = C_d A(\alpha) \frac{p_{amb}}{\sqrt{R T}} \Psi\left(\frac{p_{im}}{p_{amb}}\right)$ | `throttle_flow` (Python Function) |
| Intake manifold | $\dot p_{im} = \frac{R T}{V_{im}} (\dot m_{th} - \dot m_{cyl})$ | `manifold_net_flow`, `R_T_over_V`, `p_intake` |
| Cylinder air flow | $\dot m_{cyl} = \eta_{vol} \frac{p_{im} V_d \omega}{4 \pi R T}$ | `cylinder_flow` |
| Torque | $T = \eta_{th} Q_{LHV} \frac{\dot m_{cyl}/14.7}{\omega} - (T_{f0} + c_f \omega) - T_{load}$ | `net_torque` |
| Crankshaft | $J \dot\omega = T$ | `1/J`, `omega` |

$\Psi$ is the compressible-flow function. Below a pressure ratio of 0.528 the flow is *choked* (sonic) and
no longer depends on the manifold pressure. At idle the throttle is almost closed and the manifold is at about
20 kPa, so the engine runs on choked flow: the air flow depends on the throttle angle only.

The manifold acts as a **delay**: when the throttle opens, the pressure (and therefore the torque) needs
roughly a quarter of a second to follow. That makes idle control hard.

## 2. The ECU function

Double-click the `ECU` subsystem. It does what the idle speed controller in a real ECU does, every 10 ms:

1. sample the engine speed (`rpm_sample`, a Zero-Order Hold at `Ts = 0.01`),
2. compute the speed error to the target `rpm_idle`,
3. run a **discrete PI controller** with clamping anti-windup (`idle_pi`),
4. add a **base throttle opening** `alpha_base` (feed-forward for the nominal idle air demand),
5. add `alpha_ac` when the air-conditioning is requested (**AC feed-forward**).

All gains are workspace variables, so they are calibration parameters with units and limits, and appear in
the generated A2L file.

## 3. Why feed-forward?

At t = 5 s the AC compressor adds 12 N·m of load. Feedback alone can only react once the speed has already
dropped, and the manifold delay makes the reaction slow, so the speed falls to about 685 rpm. Production ECUs
receive the AC *request* about 200 ms before the compressor clutch engages, and open the throttle in
advance. With `alpha_ac = 3°` the dip shrinks to about 775 rpm:

![](../images/blocks/idle_speed_control.png)

Try it: set `alpha_ac` to 0 in the Model Workspace and run again, or compare both in the Data Inspector.

## 4. Requirements as test cases

The model stores three test cases (**Test Manager → Run all**):

| Test case | Checks |
|---|---|
| AC compressor on at 5 s | 780–820 rpm before the event, never below 750 rpm, 780–820 rpm again after 9 s |
| AC compressor on, no feed-forward | `alpha_ac = 0`: between 600 and 900 rpm (no stall) |
| higher idle target | `rpm_idle = 1000`: settles at 1000 ± 15 rpm |

## 5. C code for the ECU

Select the `ECU` subsystem and press **Code → Generate C Code**. *Run SIL check* compiles the controller and
compares it with the model step by step. The engine model stays in simulation; only the controller becomes
code.

## Things to try

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

- Make the controller faster: increase `Kp_idle`. When does it start to oscillate, and why does the
  manifold delay limit it?
- Replace the PI controller's feed-forward with a 2-D lookup table of base opening over coolant temperature
  and target speed. Real ECUs need more air when the engine is cold.
- Add a State Chart that switches between *crank*, *idle* and *drive* modes and only enables idle control in
  *idle*.

## Next

Next in this topic: [Lambda control and fuel trims](tutorial_lambda.md). Or pick another one from the [tutorial overview](tutorials.md).
