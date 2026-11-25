# Verification: coverage, requirements and the Model Advisor

## Model coverage

Passing tests only say something if they exercise the model. **Model coverage** measures which *decision
outcomes* the tests reached:

| Block | Outcomes |
|---|---|
| Switch | input 0 passed, input 2 passed |
| Relational Operator, Compare To Constant, Logical Operator | true, false |
| Saturation, Dead Zone | below, within, above |
| Integrator, PID (with limits) | at upper limit, at lower limit, between limits |
| Relay | on, off |
| Rate Limiter | rising limited, falling limited, not limited |
| 1-D Lookup Table | each interval between breakpoints, below and above the range |
| State Chart | every state, every transition |

The **Test Manager** measures coverage on every run and shows it next to the buttons (*Coverage 50 %*).
**Coverage report** opens an HTML report listing every block with its outcomes, exercised (green, with
counts) or never reached (red). A red outcome usually means a missing test case. For example, the idle speed
example's tests never drive the controller to its upper limit, so no test checks full-throttle authority.

From Python:

```python
from nervus import load
from nervus.coverage import Coverage
from nervus.testing import run_tests

model = load("examples/idle_speed_control.nervus")
cov = Coverage()
run_tests(model, coverage=cov)
print(cov.summary())  # Decision coverage: 4/8 outcomes (50 %) ...
cov.save_html("coverage.html")
```

`simulate(model, coverage=cov)` measures a single run. One `Coverage` object can collect several runs.

### Condition coverage and MC/DC

Decisions with several conditions, the **Logical Operator** block with two or more inputs (`enable AND speed_ok AND
no_fault`), are measured more strictly as well:

| Measure | Satisfied when |
|---|---|
| condition coverage | every condition was true at least once and false at least once |
| **MC/DC** (modified condition/decision coverage) | for every condition, two evaluations that differ *only* in that condition gave different results: each condition is shown to change the decision on its own |

MC/DC is what ISO 26262 recommends for the most critical software (ASIL D) and DO-178C requires for level A. It
finds tests that look complete but are not: `000` and `111` make every condition of a three-input AND true and false
(100 % condition coverage) but show none of them independently (0 % MC/DC). The minimal set for an AND of *n*
conditions is *n* + 1 evaluations: all true, and each condition false on its own (`111`, `011`, `101`, `110`).

The Test Manager shows *MC/DC* next to the coverage, and the coverage report lists each decision with the
conditions shown (green) and, for the others, the evaluation a test still needs, for example:

```
permit: condition 2: needs (1, 0, 1) → 0 (the pair (1, 1, 1) → 1 / (1, 0, 1) → 0)
```

State chart **transition guards** with several conditions count as decisions too, named after the transition
(`modes: crank -> idle`); nested guards such as `a > 0.5 and (b > 0.5 or not c > 0.5)` are evaluated exactly, so
masking is taken into account (with `c` false, `b` cannot change the result, and the report asks for an evaluation
with `c` true).

From Python: `cov.mcdc_percent`, `cov.condition_percent`, `cov.mcdc_missing()`.

## Requirements traceability

ECU software is written against **requirements** ("the engine idles at 800 ± 50 rpm when warm"), and a review
asks for each one: which part of the model implements it, and which test shows that it works? The
**Requirements** tab (next to the Test Manager) keeps that link.

1. **Add requirements**: press **Add** and type the id, title and text in the table, or **Import CSV…** a file
   exported from Excel, DOORS, Polarion or Jira. The file needs an `id` column; `title` (or `name`), `text`
   (or `description`) and `source` are optional, and both `,` and `;` separators work.
2. **Link blocks**: right-click a block (usually an ECU subsystem) ▸ **Requirements…** and tick the
   requirements it implements.
3. **Link test cases**: in the Test Manager's test editor, list the ids in the **Requirements** field.
4. **Run the tests** (Ctrl+T). Each requirement's **status** updates:

| Status | Meaning |
|---|---|
| verified | it has test cases and all of them passed |
| failed | a test case linked to it failed |
| not run | it has test cases that have not run yet |
| not tested | a block implements it, but no test case checks it |
| not implemented | no block is linked to it |


**Report…** writes the traceability matrix as an HTML page for reviews; double-click a row to show its block.
Renaming a requirement's id updates the links in blocks and test cases. The **Model Advisor** warns about
requirements without blocks or tests, and about links to ids that do not exist. Requirements are saved in the
model file.

```python
from nervus.core.requirements import Requirement, read_csv, trace, write_report
from nervus.testing import run_tests

for req in read_csv("requirements.csv"):
    model.requirements.add(req)
model.block("ECU").meta["requirements"] = ["IDLE-01", "IDLE-02"]
model.tests[0].requirements = ["IDLE-02"]
results = {r.case.name: r.passed for r in run_tests(model)}
for row in trace(model, results):
    print(row.requirement.id, row.status)
write_report(model, "traceability.html", results)
```

## Model Advisor

**Analysis → Model Advisor** (⇧⌘A / Ctrl+Shift+A) checks the model for practices that often cause bugs in
ECU software. Click a finding to open the block.

| Rule | Checks for |
|---|---|
| `float-equality` | `==` / `!=` on floating-point signals |
| `division` | Product blocks that divide (possible division by zero) |
| `antiwindup` | PI controllers with output limits but no anti-windup, or an integral term without limits |
| `implicit-sampling` | discrete blocks that read a continuous signal without a Zero-Order Hold |
| `chart-unreachable` | chart states that can never become active |
| `chart-shadowed` | chart transitions that come after an always-true transition |
| `lookup-extrapolation` | lookup tables that extrapolate linearly beyond the calibrated range |
| `workspace-docs` | workspace variables without a unit or description (they end up in the A2L file) |
| `workspace-unused` | workspace variables no block uses |
| `default-names` | blocks inside subsystems that still have default names |

```python
from nervus.advisor import advise

for finding in advise(model):
    print(finding)
```
