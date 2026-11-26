"""Simulation results."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nervus.core.types import Array


@dataclass
class SimStats:
    solver: str
    step: float | None
    n_points: int
    n_major_steps: int
    wall_time: float
    stopped_early: bool = False
    zero_crossings: int = 0
    """Number of discontinuities located by zero-crossing detection."""
    warnings: list[str] | None = None


@dataclass
class SimResult:
    """Logged signals of a simulation run.

    Every block output is logged. Look signals up by block name (output port 0) or ``"block:port"``::

        res["y"]          # output 0 of block 'y'; shape (N,) if scalar, (N, width) if vector
        res["mux:1"]      # output 1 of block 'mux'
        res.inputs("scope")  # list of the signals going into block 'scope'
        res.t             # time vector, shape (N,)
    """

    t: Array
    signals: dict[str, Array]
    """Raw logged data, keyed ``"block:port"``, each of shape (N, width)."""
    input_keys: dict[str, list[str]] = field(default_factory=dict)
    """For each block, the signal keys driving its inputs."""
    aliases: dict[str, str] = field(default_factory=dict)
    """Extra names (e.g. To Workspace variable names) mapped to signal keys."""
    stats: SimStats | None = None
    units: dict[str, str] = field(default_factory=dict)
    """Units of measured signals (from a measurement or log file), keyed like :attr:`signals`."""

    @classmethod
    def from_channels(cls, channels: dict[str, tuple[Array, Array]], raster: float | None = None,
                      units: dict[str, str] | None = None) -> SimResult:  # fmt: skip
        """Measured values, each with its own time stamps, on one time base: the time stamps of the value with
        the most samples, or a fixed *raster* from the first to the last time stamp. The others are linearly
        interpolated (and held before their first and after their last sample)."""
        found = {}
        for name, (ts, vs) in channels.items():
            ts, vs = np.asarray(ts, dtype=float), np.asarray(vs, dtype=float)
            if ts.size and np.any(np.diff(ts) < 0):  # out-of-order lines in a log
                order = np.argsort(ts, kind="stable")
                ts, vs = ts[order], vs[order]
            if ts.size:
                found[name] = (ts, vs)
        if not found:
            raise ValueError("no samples")
        if raster:
            lo = min(ts[0] for ts, _ in found.values())
            hi = max(ts[-1] for ts, _ in found.values())
            t = np.arange(lo, hi + raster / 2, raster)
        else:
            t = max((ts for ts, _ in found.values()), key=len)
        signals = {f"{n}:0": np.interp(t, ts, v).reshape(-1, 1) for n, (ts, v) in found.items()}
        unit_of = {f"{n}:0": u for n, u in (units or {}).items() if u and n in found}
        return cls(t=t, signals=signals, units=unit_of)

    def unit(self, name: str) -> str:
        """The unit of a measured signal, or ''."""
        try:
            return self.units.get(self._key(name), "")
        except KeyError:
            return ""

    def _key(self, name: str) -> str:
        if name in self.signals:
            return name
        if name in self.aliases:
            return self.aliases[name]
        if f"{name}:0" in self.signals:
            return f"{name}:0"
        if f"{name}:0" in self.aliases:
            return self.aliases[f"{name}:0"]
        raise KeyError(f"no signal named {name!r}; available: {', '.join(self.names())}")

    def __getitem__(self, name: str) -> Array:
        data = self.signals[self._key(name)]
        return data[:, 0] if data.shape[1] == 1 else data

    def __contains__(self, name: object) -> bool:
        try:
            self._key(str(name))
        except KeyError:
            return False
        return True

    def __iter__(self) -> Iterator[str]:
        return iter(self.signals)

    def signal(self, block: str, port: int = 0) -> Array:
        return self[f"{block}:{port}"]

    def inputs(self, block: str) -> list[Array]:
        """The signals driving each input of ``block`` (useful for Scope blocks)."""
        try:
            keys = self.input_keys[block]
        except KeyError:
            raise KeyError(f"no block named {block!r}") from None
        return [self[k] for k in keys]

    def names(self) -> list[str]:
        """Friendly signal names: ``"block"`` for single-output blocks, ``"block:port"`` otherwise."""
        counts: dict[str, int] = {}
        for k in self.signals:
            b = k.rsplit(":", 1)[0]
            counts[b] = counts.get(b, 0) + 1
        return [k.rsplit(":", 1)[0] if counts[k.rsplit(":", 1)[0]] == 1 else k for k in self.signals]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"t": self.t}
        for name in self.names():
            out[name] = self[name]
        return out

    def to_csv(self, path: str | Path) -> None:
        """Write all signals to a CSV file; vector signals get one column per element."""
        headers = ["t"]
        columns: list[Array] = [self.t]
        for name in self.names():
            data = self.signals[self._key(name)]
            if data.shape[1] == 1:
                headers.append(name)
            else:
                headers.extend(f"{name}[{j}]" for j in range(data.shape[1]))
            columns.extend(data.T)
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows(np.column_stack(columns).tolist())

    # ------------------------------------------------------------------ files

    def save(self, path: str | Path) -> Path:
        """Save to a compressed ``.nervres`` file (NumPy NPZ with the signal names and metadata)."""
        import json

        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(".nervres")
        meta = {
            "format": "nervus-result",
            "schema_version": 1,
            "keys": list(self.signals),
            "input_keys": self.input_keys,
            "aliases": self.aliases,
            "stats": self.stats.__dict__ if self.stats is not None else None,
            "units": self.units,
        }
        arrays: dict[str, Any] = {f"s{k}": v for k, v in enumerate(self.signals.values())}
        arrays["t"] = self.t
        arrays["meta"] = np.array(json.dumps(meta))
        with open(p, "wb") as fh:
            np.savez_compressed(fh, **arrays)
        return p

    @classmethod
    def load(cls, path: str | Path) -> SimResult:
        import json

        with np.load(Path(path), allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            if meta.get("format") != "nervus-result":
                raise ValueError(f"{path} is not a Nervus result file")
            signals = {key: data[f"s{k}"] for k, key in enumerate(meta["keys"])}
            t = data["t"]
        stats = SimStats(**meta["stats"]) if meta.get("stats") else None
        return cls(t, signals, meta.get("input_keys", {}), meta.get("aliases", {}), stats, meta.get("units", {}))

    def to_mat(self, path: str | Path) -> None:
        """Save as a MATLAB ``.mat`` file: ``t`` plus one variable per signal (names made MATLAB-safe)."""
        import re

        from scipy.io import savemat

        out: dict[str, Any] = {"t": self.t}
        for name in self.names():
            var = re.sub(r"\W", "_", name)
            if not var or not var[0].isalpha():
                var = "s_" + var
            out[var[:63]] = self.signals[self._key(name)]
        savemat(str(path), out)

    def to_dataframe(self) -> Any:
        """Return a :class:`pandas.DataFrame` indexed by time (requires pandas)."""
        import pandas as pd

        cols: dict[str, Array] = {}
        for name in self.names():
            data = self.signals[self._key(name)]
            if data.shape[1] == 1:
                cols[name] = data[:, 0]
            else:
                for j in range(data.shape[1]):
                    cols[f"{name}[{j}]"] = data[:, j]
        return pd.DataFrame(cols, index=pd.Index(self.t, name="t"))
