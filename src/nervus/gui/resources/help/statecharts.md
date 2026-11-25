# State machines

Operating modes, start sequences and fault handling are naturally described as **state machines**. In
Simulink they are Stateflow charts; in Nervus they are **State Chart** blocks
(**Library → State Machines → State Chart**).

![](figures/chart_engine_modes.png)

## The chart editor

**Double-click** a State Chart block to open the chart editor:

![](figures/chart-editor.png)

- **States** are boxes. Double-click empty space (or press **Add state**) to add one; drag it to move it. Select a
  state to edit its name and its *entry*, *during* and *exit* actions on the right. **Add child state** puts a state
  inside the selected one (a nested state); **Make initial** marks the state entered first at its level (the dot
  and arrow).
- **Transitions** are arrows. Select the source state, press **Add transition** and click the target state. Select
  an arrow to edit its *guard* and *action*. When several transitions leave the same state, the number on the
  label is the order in which they are checked: **Check earlier** and **Check later** change it.
- With nothing selected, the panel shows the chart's **inputs, outputs, locals, parameters and events**.
- The **Text** tab shows the same chart in the chart language below; edit either one.
- **Apply** or **OK** writes the chart to the block, as one step you can undo (Ctrl+Z). A chart that is not valid
  (an unknown name in a guard, say) shows the reason at the bottom and cannot be applied.

![](figures/chart-editor-transition.png)

The positions of the states are saved in the model file. A chart without positions (written as text) is laid out
automatically, from the initial state on the left; **Tidy up** does that again. Comments in the chart text are not
kept when the chart is edited graphically.

## Writing a chart as text

Charts are stored in a short text language. Besides the chart editor's **Text** tab, you can select the block and
edit its *definition* in the Properties panel, then press **Apply code**:

```
# Engine operating modes
input rpm, key
output mode = 0, fuel = 0
local starts = 0

state off
  entry: mode = 0; fuel = 0

state cranking
  entry: mode = 1; starts += 1
  during: fuel = 1

state running
  entry: mode = 2; fuel = 1

transition off -> cranking: key > 0.5
transition cranking -> running: rpm > 600
transition cranking -> off: after(300) / fuel = 0
transition running -> off: key < 0.5 or rpm < 300
```

| Line | Meaning |
|---|---|
| `input a, b` | input ports, in this order |
| `output y = 0, z = 1` | output ports with initial values |
| `local n = 0` | variables kept between steps, not visible outside |
| `state NAME` | a state; the first one is the initial state (or write `initial NAME`) |
| `  entry: ...` | actions when the state is entered |
| `  during: ...` | actions every period the state stays active |
| `  exit: ...` | actions when the state is left |
| `transition A -> B: guard / action` | leave A for B when *guard* is true; the action is optional |

Actions are assignments separated by `;` (`y = 1; n += 1`). Guards and actions can use `+ - * / **`,
comparisons, `and or not`, `abs min max sqrt exp log sin cos floor ceil`, `pi` and `e`.

### Timing

- `after(n)` is true once the state has been active for **n sample periods**.
- `t_in_state` is the time in seconds since the state was entered. One period has passed at the first step
  after entering.

### Semantics

The chart runs at its sample time (set *sample_time*, or let it inherit a discrete rate). Every period:

1. The transitions leaving the active state are checked **in the order they are written**. The first with a
   true guard fires: the source's `exit` actions run, then the transition action, then the target's `entry`
   actions.
2. If none fires, the active state's `during` actions run.

With *state_output* on, an extra last output gives the active state's number (0, 1, 2, ... in the order the
states are written). It's handy for plotting.

## Nested states, parallel states, history and events

Larger charts are easier to read with **hierarchy**. Indent a `state` under another one to nest it:

```
input key, rpm, fault
output mode = 0, mil = 0, stalls = 0
event stalled

state off
  entry: mode = 0
state on parallel             # two regions, both active while 'on' is
  state engine
    state cranking            # the first child is the initial one
      entry: mode = 1
    state running
      entry: mode = 2
  state diagnosis
    state ok
      entry: mil = 0
    state failed
      entry: mil = 1

transition off -> on: key > 0.5
transition on -> off: key < 0.5                  # leaves every state inside 'on'
transition cranking -> running: rpm > 600
transition running -> cranking: rpm < 300 / stalls += 1; send(stalled)
transition ok -> failed: fault > 0.5 or stalled
transition failed -> ok: fault < 0.5 and after(30)
```

- **Nested (OR) states**: a state with child states has exactly one active child. Entering it enters its
  initial child: the first one, or the one named by an indented `initial NAME`.
- **Parallel (AND) states**: `state NAME parallel` makes all its children active at the same time, as
  independent regions (here the engine and its diagnosis). There are no transitions between regions; they
  communicate through variables and events.
- **History**: an indented `history` line makes a state remember which child was active when it was left;
  entering it again goes back to that child instead of the initial one.
- **Events**: `event NAME` declares an event. An action `send(NAME)` broadcasts it: after the current pass
  the chart runs once more with `NAME` true in the guards. Here the stall in the engine region sets the
  diagnosis region to *failed* in the same sample. At most 8 events are processed per sample.
- **Transitions across levels**: a transition may connect any two states (not in different parallel regions).
  It exits the states it leaves from the inside out (`exit` actions, innermost first), runs its action, and
  enters the states down to the target from the outside in (`entry` actions), then the target's initial
  children.

**Priority**: outer transitions are checked first: while `on` is active, `on -> off` is checked before anything
inside it. If no transition of a state fires, its `during` actions run and then its active children are
processed in the order they are written. `after(n)` and `t_in_state` count the time in the state whose
transition or `during` action is being evaluated. The state output gives the innermost active state of the
first region.

![](figures/chart_hierarchy.png)

Hierarchical charts are generated as C code like flat ones (the same algorithm, unrolled over the states), and
back-to-back tests check them sample by sample. The Model Advisor knows that initial children and parallel
regions are entered by default.

## Calibration parameters in a chart

Thresholds and times in an ECU's mode logic are calibrated like any other value. Declare them with
**`param`**: the chart reads them, but cannot change them, and their values come from the model workspace (or
from the mask of a subsystem around the chart):

```text
input rpm, pedal
output fuel = 1
param rpm_cut_on, rpm_cut_off          # model workspace variables

state run
state fuel_cut
  entry: fuel = 0
  exit: fuel = 1

transition run -> fuel_cut: pedal < 1 and rpm > rpm_cut_on
transition fuel_cut -> run: pedal > 2 or rpm < rpm_cut_off
```

Change `rpm_cut_on` in the Model Workspace (or in a parameter set or test case) and the chart follows. In
generated C, `param` names become calibration parameters in `<model>_P`, with the variable's data type, and they
appear in the A2L file.

## Example: engine operating modes

`examples/engine_modes.py` is the mode logic of an engine ECU: **off**, **crank** (starter on, with a 3 s
starter protection into **crank_fail**), **idle** (idle speed control on), **run**, **fuel_cut** (overrun fuel
cut-off while coasting) and **stalled** (no automatic restart). All thresholds are `param`s. Four test cases
drive the key, engine speed and pedal with scenarios: a normal start, a failed start, fuel cut-off, and a stall.

![](figures/engine_modes.png)

Coasting from 3000 rpm with the pedal released, the chart switches the fuel off (no fuel is needed: the car's
momentum turns the engine) and back on at 1100 rpm, before the engine could stall, and goes to idle control.

## Example: gear-shift logic

`examples/gear_shift.py` has one state per gear. Each transition compares the vehicle speed with the shift
speed read from a map for the current gear and pedal, `speed > up_speed and after(min_gear_time)`; the minimum
time in a gear and the hysteresis between the up- and downshift maps keep it from hunting. See the
[gear-shift tutorial](tutorial_gear_shift.md).

## C code

Charts are generated as C code (the transitions of each state as `if` / `else if`, and the entry and exit
actions inlined), so they can be part of the ECU code. A back-to-back test
in the test suite checks the generated chart against the simulation sample by sample, with zero tolerance.
