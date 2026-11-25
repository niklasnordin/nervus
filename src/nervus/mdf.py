"""ASAM MDF4 (``.mf4``) files: the measurement format of INCA, CANape, vehicle loggers and test benches.

    from nervus.mdf import read_mdf, write_mdf
    write_mdf(result, "run.mf4")                 # a simulation result, e.g. to compare in CANape
    rec = read_mdf("drive.mf4", ["n_engine"])    # a vehicle log as a SimResult
    FromWorkspace(time=rec.t, data=rec["n_engine"])

Uses ``asammdf`` (``uv sync --extra mdf``). An MDF file stores channels in **groups**, each with its own time
base (a CAN logger writes every message at its own rate). :func:`read_mdf` puts every channel on one time base:
the times of the group with the most samples, or a fixed *raster*; others are linearly interpolated (and held
before their first and after their last sample).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from nervus.core.errors import NervusError, install_hint
from nervus.sim.result import SimResult


def _asammdf() -> Any:
    try:
        import asammdf
    except ImportError as exc:
        raise NervusError(f"MDF4 files need asammdf: {install_hint('mdf')}") from exc
    return asammdf


def write_mdf(result: SimResult, path: str | Path, names: Iterable[str] | None = None,
              units: dict[str, str] | None = None, comment: str = "Nervus simulation") -> Path:  # fmt: skip
    """Write signals of a result to an MDF 4.10 file (all on the result's time base). Vector signals become one
    channel per element, ``name[k]``. Returns the path."""
    asammdf = _asammdf()
    units = units or {}
    t = np.asarray(result.t, dtype=float)
    keep = np.concatenate([[True], np.diff(t) > 0])  # MDF time must increase: drop repeated sample instants
    sigs = []
    for name in names if names is not None else result.names():
        data = np.asarray(result[name], dtype=float)
        columns = [(name, data)] if data.ndim == 1 else [(f"{name}[{k}]", data[:, k]) for k in range(data.shape[1])]
        for ch, values in columns:
            sigs.append(asammdf.Signal(values[keep], t[keep], name=ch, unit=units.get(name, "")))
    mdf = asammdf.MDF(version="4.10")
    if sigs:
        mdf.append(sigs, comment=comment)
    p = Path(path)
    if p.suffix.lower() not in (".mf4", ".mdf"):
        p = p.with_suffix(".mf4")
    mdf.save(p, overwrite=True)
    mdf.close()
    return p


def _channel_index(mdf: Any) -> dict[str, tuple[int, int, str]]:
    """Channel name -> (group, index, unit), time channels left out. A name that occurs in several groups (a
    CAN log's ``CRC`` or ``Counter`` in every message) is told apart by its group: ``CRC@1``, ``CRC@4``, ... (and
    ``CRC@1.3``, the channel index, if it occurs twice in one group)."""
    places: dict[str, list[tuple[int, int, str]]] = {}
    for g, group in enumerate(mdf.groups):
        for i, ch in enumerate(group.channels):
            if ch.channel_type != 2:  # 2 = master (time) channel
                places.setdefault(ch.name, []).append((g, i, getattr(ch, "unit", "") or ""))
    out: dict[str, tuple[int, int, str]] = {}
    for name, found in places.items():
        if len(found) == 1:
            out[name] = found[0]
            continue
        groups = [g for g, _i, _u in found]
        for g, i, unit in found:
            out[f"{name}@{g}" if groups.count(g) == 1 else f"{name}@{g}.{i}"] = (g, i, unit)
    return out


def mdf_channels(path: str | Path) -> dict[str, str]:
    """The channel names in a file, with their units (time channels left out). Names that occur in several
    groups get the group number: ``CRC@1``, ``CRC@4`` (these names also work with :func:`read_mdf`)."""
    asammdf = _asammdf()
    with asammdf.MDF(str(path)) as mdf:
        return {name: unit for name, (_g, _i, unit) in _channel_index(mdf).items()}


def read_mdf(path: str | Path, channels: Iterable[str] | None = None, raster: float | None = None) -> SimResult:
    """Read channels (all numeric ones if ``None``) into a :class:`SimResult` on one time base (see the module
    docs). Each channel is a signal named like the channel in :func:`mdf_channels` (``CRC@1`` for a name that
    occurs in several groups)."""
    asammdf = _asammdf()
    with asammdf.MDF(str(path)) as mdf:
        index = _channel_index(mdf)
        wanted = list(channels) if channels is not None else list(index)
        found = {}
        for name in wanted:
            try:
                if name in index:
                    g, i, _unit = index[name]
                    sig = mdf.get(group=g, index=i)
                else:
                    sig = mdf.get(name)
            except Exception as exc:  # asammdf raises MdfException for missing or ambiguous names
                if channels is not None:
                    alternatives = [k for k in index if k.startswith(f"{name}@")]
                    hint = f"; it occurs in several groups: {', '.join(alternatives)}" if alternatives else ""
                    raise NervusError(f"{Path(path).name}: no channel {name!r} ({exc}){hint}") from exc
                continue
            values = np.asarray(sig.samples)
            if values.dtype.kind not in "biuf" or values.ndim != 1 or len(sig.timestamps) == 0:
                continue  # text, byte arrays, structures
            found[name] = (np.asarray(sig.timestamps, dtype=float), values.astype(float))
    if not found:
        raise NervusError(f"{Path(path).name}: no numeric channels to read")
    if raster:
        lo = min(ts[0] for ts, _ in found.values())
        hi = max(ts[-1] for ts, _ in found.values())
        t = np.arange(lo, hi + raster / 2, raster)
    else:
        t = max((ts for ts, _ in found.values()), key=len)
    signals = {f"{n}:0": np.interp(t, ts, v).reshape(-1, 1) for n, (ts, v) in found.items()}
    return SimResult(t=t, signals=signals)
