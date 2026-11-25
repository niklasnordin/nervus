# Model files

Models are saved as UTF-8 JSON files with the extension `.nervus`:

```json
{
  "format": "nervus-model",
  "schema_version": 4,
  "name": "first_order",
  "meta": {},
  "settings": {"t_start": 0.0, "t_final": 10.0, "solver": "auto", "step": null,
               "max_step": null, "rtol": 1e-06, "atol": 1e-08},
  "workspace": [
    {"name": "Kp", "value": 1.5, "unit": "%/°", "description": "Proportional gain", "min": 0.0}
  ],
  "blocks": [
    {"name": "u", "type": "sources.step",
     "params": {"time": 0.0, "initial": 0.0, "final": 1.0, "sample_time": 0.0}}
  ],
  "connections": [
    {"src": "u", "src_port": 0, "dst": "err", "dst_port": 0}
  ]
}
```

- `type` is the block's stable `type_id` (listed in the [block reference](https://ikjbi.github.io/nervus/latest/blocks/)).
- Every parameter is written out, including defaults, so a file keeps its meaning if a default changes.
- Infinite values are written as the strings `"inf"` / `"-inf"`, so the file stays strict JSON.
- A parameter given as an expression of workspace variables is stored as the expression string, e.g.
  `"gain": "Kp"`. See [Workspace and calibrations](workspace.md).
- `meta` holds GUI data (block positions, view state) and is ignored by the simulator.
- Output is deterministic, so a small change to the model gives a small `git diff`.

## Versioning

`schema_version` is increased whenever the format changes, and Nervus upgrades older files automatically when
it loads them. Version 2 added `workspace`; version 3 added subsystems (a block entry may contain a nested
`"system"` with its own `blocks` and `connections`); version 4 added `tests` (test cases with inputs,
parameters and checks, see [Test cases and results](testing.md)). Files from a *newer* version of Nervus are rejected with a message asking you to upgrade.

> **Python Function blocks run code:** A model containing a Python Function block runs that code when it is loaded and simulated. Only open model files you trust.
