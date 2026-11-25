# State Machines

## State Chart

`charts.state_chart` · State Machines

A state machine (chart) for operating modes, sequences and fault handling, like Simulink's Stateflow.

![An engine operating-mode chart](../images/blocks/chart_engine_modes.png)

The chart is written in a short text language in *definition*:

```
input rpm, key
output mode = 0, fuel = 0

state off
  entry: mode = 0; fuel = 0
state cranking
  entry: mode = 1
  during: fuel = 1
state running
  entry: mode = 2

transition off -> cranking: key > 0.5
transition cranking -> running: rpm > 600
transition cranking -> off: after(300) / fuel = 0
transition running -> off: key < 0.5 or rpm < 300
```

Inputs and outputs become the block's ports, in the order they are declared; with *state_output* an extra
last output gives the active state's number (0, 1, ... in the order the states are written). Every sample
period the transitions leaving the active state are checked in the order written; the first true guard
fires (exit → transition action → entry). Otherwise the state's *during* actions run. ``after(n)`` is true
after *n* periods in the state, and ``t_in_state`` is the time in the state in seconds. The initial state is
entered in the first sample (its entry actions see the inputs of that sample), and its clock starts there.

The chart runs at a discrete rate, and can be generated as C code.

### Parameters

| Name | Default | Unit | Description |
|---|---|---|---|
| `definition` | *(code)* |  | The chart: inputs, outputs, states and transitions |
| `state_output` | `True` |  | Add an output with the number of the active state |
| `sample_time` | -1 (inherited) | s | Sample period (-1 = inherit) |

*Direct feedthrough:* yes

---
