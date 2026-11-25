# Tutorial: Lambda control and fuel trims

| | |
|---|---|
| **You will learn** | why petrol engines run at lambda = 1, closed-loop fuel control with a lambda sensor, short- and long-term fuel trims (as read over OBD-II), an ECU task in an atomic subsystem |
| **Before this** | [Idle speed control](tutorial_idle.md) |
| **Level and time** | intermediate, about 45 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/lambda_control.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Powertrain and engine control ▸ Engine management ([all tutorials](tutorials.md)) |

This tutorial follows `examples/lambda_control.py`. It shows how a petrol engine's ECU keeps the air-fuel
ratio at the value the catalytic converter needs, and what the **fuel trims** that you can read from your car
over OBD-II actually are.

```bash
uv run python examples/lambda_control.py      # simulates, saves lambda_control.nervus, runs the tests
uv run nervus examples/lambda_control.nervus  # open it in the editor
```

## Why lambda = 1

Burning petrol completely takes 14.7 kg of air per kg of fuel, the *stoichiometric* air-fuel ratio. The
**air-fuel equivalence ratio** lambda compares the actual mixture with it:

$$ \lambda = \frac{m_{air}}{14.7\, m_{fuel}} $$

λ > 1 is lean (too much air), λ < 1 is rich. A three-way catalyst removes CO, HC and NOx at the same time
only in a narrow window around λ = 1, so the ECU controls λ to 1 at part load all the time.

## The model

**Plant** (the engine), top and bottom rows:

- the air charge per cylinder stroke: 250 mg at part load, a tip-in to 400 mg from 4 s to 8 s;
- from t = 12 s a **vacuum leak**: 6 % extra air that enters behind the MAF sensor, so the ECU doesn't see it;
- the injectors: fuel mass = (injection time − dead time) × flow rate;
- the exhaust gas needs $n_{exh}$ = 8 samples × 10 ms to reach the lambda sensor, and the sensor itself has a
  time constant $\tau$ = 0.1 s.

**ECU**: the `ECU` block is an [atomic subsystem](subsystems.md#atomic-subsystems-one-ecu-task) with
`sample_time = Ts` (10 ms), the fuel task:

$$ m_{fuel,cmd} = \frac{m_{air,MAF}}{14.7}\,(1 + STFT + LTFT), \qquad t_{inj} = \frac{m_{fuel,cmd}}{q_{inj}} + t_{dead} $$

- The **base fuel** comes from the measured air (feed-forward). If the sensors and injectors were perfect,
  this alone would give λ = 1.
- The **short-term fuel trim (STFT)** is a discrete PI controller on the lambda error $e = \lambda_{meas} - 1$,
  limited to ±25 %. Lean (e > 0) means more fuel.
- The **long-term fuel trim (LTFT)** integrates the STFT slowly:
  $LTFT[k+1] = LTFT[k] + K_{ltft}\,T_s\,STFT[k]$. It moves the average correction out of the STFT, which then
  returns to 0 and has its full ±25 % available again for fast disturbances.

## What happens

![](figures/lambda_trims.png)

1. Up to 12 s λ stays at 1, even during the tip-in: the base fuel follows the measured air.
2. At 12 s the leak makes the mixture 6 % lean. The lambda sensor sees it about 0.2 s later (exhaust delay
   and sensor lag); the STFT rises to about +5 % within three seconds and brings λ back to 1.
3. Over the next 25 s the LTFT learns the leak: it rises to almost +6 % while the STFT falls back to 0.

A real ECU stores the LTFT, often per load and speed range, and keeps it when the engine is switched off. It
also sets a trouble code when a trim reaches its limit, such as P0171 *System too lean* (see the OBD-II page).

## Compare with your car

Your car reports the same two numbers as OBD PIDs 06 (`stft_b1`) and 07 (`ltft_b1`):

```bash
uv run python -m nervus.obd record --port /dev/tty.usbserial-1110 \
    --pids rpm,maf,stft_b1,ltft_b1,o2_b1s1 --duration 300 --out my_drive.nervres
```

- A healthy engine has an LTFT within about ±5 % and an STFT that keeps moving around 0.
- An LTFT of +10 % or more at idle that gets smaller at higher load is the classic sign of a vacuum leak:
  the leak is a fixed amount of air, so it matters most when little air is flowing.
- An LTFT that is high at all loads points more to fuel delivery (pump pressure, injectors) or the MAF sensor.

## Experiments

To try one: change the value in the **Model Workspace** tab (double-click it), or for a block parameter select the block and edit it in **Properties**; press **▶** and compare with the previous run in the **Data Inspector** (Ctrl+I). **Ctrl+Z** undoes the change.

Change the workspace variables in the **Model Workspace** panel and run again:

| Try | What you will see |
|---|---|
| `injector_error = -0.05`, `leak = 0` | clogged injectors: the LTFT learns about +5.3 % (= 1/0.95 − 1) |
| `K_ltft = 0.5` | faster learning, but the trims overshoot: the STFT goes negative and λ dips rich (0.987) |
| `Ki_stft = 4` | a faster STFT (peak after 0.7 s instead of 2.7 s), with some overshoot |
| `n_exh = 20` and `Ki_stft = 4` | a longer exhaust delay with a fast STFT: much more overshoot, λ dips to 0.98. The delay limits how fast the STFT can be |

The test cases in the **Test Manager** check the leak and clogged-injector cases, and that λ stays at 1
during the tip-in. Generate C code for the `ECU` block (Code → Generate C) to get the fuel task as ECU code.

## Next

Next in this topic: [Ignition timing and knock control](tutorial_ignition.md). Or pick another one from the [tutorial overview](tutorials.md).
