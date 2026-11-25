# Tutorial: Requirements-based testing and coverage

| | |
|---|---|
| **You will learn** | requirements imported from a CSV file, traceability from requirements to blocks and tests, model coverage to find untested behaviour, closing the gap, review reports |
| **Before this** | [Idle speed control](tutorial_idle.md) |
| **Level and time** | intermediate, about 40 minutes |
| **Follow along** | start the editor (`uv run nervus`), **File ▸ Open** `examples/idle_requirements.nervus`, press **▶** and double-click the scopes; the figures below come from the same model |
| **Topic** | Testing and verification ([all tutorials](tutorials.md)) |

In the automotive industry a function is finished when every **requirement** is verified by a **test**, and the
tests exercise the whole function (**coverage**). This tutorial takes the idle speed controller of
[the idle speed tutorial](tutorial_idle.md) through that process: import its requirements, link them to the
model and the tests, find the gap, close it, and write the reports a review asks for.

```bash
uv run python examples/idle_requirements.py   # the finished result: idle_requirements.nervus and two reports
```

## Step 1: import the requirements

The requirements of the function are in `examples/data/idle_requirements.csv`, as exported from a requirements
tool or a spreadsheet (`;`-separated, with `id`, `title`, `text` and `source` columns):

| Id | Title | Requirement |
|---|---|---|
| IDLE-01 | Idle speed | With the engine warm and no load change, the idle speed stays within 800 ± 20 rpm. |
| IDLE-02 | Load rejection | When the AC compressor switches on, the speed drops by less than 50 rpm and is back within ± 20 rpm after 4 s. |
| IDLE-03 | Stall protection | Even without the AC feed-forward the engine must not stall: the speed stays above 600 rpm. |
| IDLE-04 | Calibratable target | The idle target is a calibration parameter; the engine reaches a new target within ± 15 rpm. |
| IDLE-05 | Limited correction | The correction is limited to 15° of throttle, and a large target change still settles without windup. |

Open `examples/idle_speed_control.nervus`, go to the **Requirements** tab and press **Import CSV…**. All five appear
as *not implemented*: nothing in the model claims them yet.

## Step 2: link the model and the tests

- Right-click the **ECU** subsystem ▸ **Requirements…** and tick all five: the ECU implements them. They change to
  *not tested*.
- In the **Test Manager**, open each test case and fill in **Requirements**: *AC compressor on at 5 s* verifies
  IDLE-01 and IDLE-02, *AC compressor on, no feed-forward* IDLE-03, *higher idle target* IDLE-04.
- Press **Run All** (Ctrl+T). Four requirements are now **verified**, and IDLE-05 is still **not tested**:
  no test checks the limit.

## Step 3: find what the tests miss

Requirements tell you what to test; **coverage** tells you whether the tests reached every decision in the
model. The Test Manager measures it with every **Run All**: the line under the results shows the coverage, and
**Coverage report** lists what was missed (in Python: `run_tests(model, coverage=Coverage())`, as in the example):

```
Decision coverage: 4/8 outcomes (50 %) in 3 run(s)
  ECU/idle_pi: never output at upper limit
  p_intake: never at upper limit, at lower limit
  omega: never at lower limit
```

The controller `idle_pi` never reached its upper limit, exactly the behaviour IDLE-05 is about. The other three
outcomes are limits of the **plant** (the manifold pressure and a stopped engine): physically impossible or a
stall, so they are justified in the review rather than tested.

## Step 4: close the gap

Add a test case in the Test Manager:

| Field | Value |
|---|---|
| Name | large target change: correction limited |
| Parameter overrides | `rpm_idle` = 1500 |
| Requirements | IDLE-05 |
| Checks | `bounds` on `ECU/idle_pi` (lower −2, upper 15); `final_value` of `engine_rpm` = 1500 ± 20 |

Run all tests again: the controller now saturates at 15°, the engine still settles at 1500 rpm, IDLE-05 is
**verified**, and the controller's decisions are fully covered (5 of 8 outcomes, the rest being the plant).

## Step 5: the reports

**Report…** in the Requirements tab writes the traceability matrix (requirement → blocks → tests → status) as an
HTML page; the coverage report lists every decision outcome. The example writes both to `examples/generated/`.
Together with the test results they are what a design review or an ISO 26262 assessment asks for. The
**Model Advisor** (Analysis ▸ Model Advisor) adds the static checks: it warns about requirements without blocks or
tests, and about links to unknown ids.

## Experiments

| Try | What you will see |
|---|---|
| `Kp_idle` = 0.01 (a weak controller), run the tests | the two AC tests fail: IDLE-01, IDLE-02 and IDLE-03 show **failed**, IDLE-04 and IDLE-05 still pass |
| rename IDLE-05 to IDLE-06 in the table | the links in the ECU block and the new test follow the rename |
| delete the link from the ECU to IDLE-03 | IDLE-03 becomes *not implemented*, even though its test still passes |

## Next

Next in this topic: [Robustness and tolerances with Monte Carlo](tutorial_robustness_monte_carlo.md). Or pick another one from the [tutorial overview](tutorials.md).
