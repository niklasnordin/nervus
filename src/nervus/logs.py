"""Vehicle logs: CSV files from OBD-II scanner apps and data loggers, and raw CAN logs.

    from nervus.logs import read_log, log_channels
    log_channels("torque_log.csv")   # {'Engine RPM': 'rpm', 'Speed (OBD)': 'km/h', ...}
    rec = read_log("torque_log.csv")  # a SimResult: open it in the Data Inspector, or drive a model with it
    can = read_log("truck.asc", dbc="j1939.dbc")  # a CAN log, decoded with a DBC file
    obd = read_log("candump.log")                # a CAN log of OBD-II requests and answers

**CSV files.** Scanner apps write either one column per value (*wide*: Torque, OBD Fusion, OBDLink, FORScan,
ScanMaster, Nervus's own export) or one row per sample (*long*: Car Scanner's ``SECONDS;PID;VALUE;UNITS``).
Both are recognised from the header. The separator (``,`` ``;`` or tab), a decimal comma, lines before the
header, units in the header (``Engine RPM(rpm)``, ``Speed [km/h]``), empty or ``-`` cells and repeated headers
are handled. The time column is the first one called time, seconds, timestamp and the like (``ms`` in its name
or unit means milliseconds), else the first column; date and clock times (``18-Nov-2020 14:32:01.123``) count
from the first sample.

**CAN logs** (``.asc``, ``.blf``, ``.trc``, candump ``.log``, python-can ``.csv``; read with python-can). With a
DBC file every signal of every known message is decoded; an extended (29-bit) frame that matches no message
exactly is matched by its J1939 parameter group (PGN), so one J1939 DBC file fits trucks with any source
address. Without a DBC file, OBD-II answers (identifiers 0x7E8 to 0x7EF, mode 01) are decoded with the PIDs of
:mod:`nervus.obd`.

Every value gets its own time stamps; like :func:`nervus.mdf.read_mdf`, the result puts all of them on the
time base of the value with the most samples (or a fixed *raster*), linearly interpolated.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Container, Iterable
from datetime import datetime
from pathlib import Path

import numpy as np

from nervus.core.errors import MissingDependencyError, NervusError, install_hint
from nervus.core.types import Array
from nervus.sim.result import SimResult

CAN_LOG_SUFFIXES = (".asc", ".blf", ".trc", ".log")

_TIME_NAMES = ("device time", "time", "t", "seconds", "second", "sec", "secs", "timestamp", "time stamp",
               "elapsed", "elapsed time", "gps time", "date time", "datetime", "clock")  # fmt: skip
_NAME_COLUMNS = ("pid", "name", "sensor", "parameter", "signal", "channel")
_VALUE_COLUMNS = ("value", "val")
_UNIT_COLUMNS = ("units", "unit")
_EMPTY = {"", "-", "--", "—", "n/a", "na", "nan", "null", "none", "?"}
_UNITS = {
    "rpm", "s", "ms", "us", "min", "h", "v", "mv", "a", "ma", "w", "kw", "hp", "ps", "nm", "lb-ft", "kpa", "pa",
    "hpa", "mbar", "bar", "psi", "inhg", "c", "degc", "f", "degf", "k", "deg", "%", "km", "m", "mi", "ft", "l",
    "ml", "gal", "g", "kg", "lb", "ohm", "hz", "khz", "lambda", "ratio", "afr", "mpg", "-", "1",
}  # fmt: skip
_DATE_FORMATS = (
    "%d-%b-%Y %H:%M:%S.%f", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S",
    "%a %b %d %H:%M:%S %Y", "%H:%M:%S.%f", "%H:%M:%S",
)  # fmt: skip

Channels = dict[str, tuple[Array, Array, str]]
"""Value name -> (time stamps, values, unit)."""


def read_log(path: str | Path, channels: Iterable[str] | None = None, raster: float | None = None,
             dbc: str | Path | None = None) -> SimResult:  # fmt: skip
    """Read a vehicle log (see the module docs) into a :class:`SimResult` on one time base. *channels* limits it
    to some values (names as in :func:`log_channels`)."""
    found = _read(path, dbc)
    if channels is not None:
        wanted = list(channels)
        missing = [c for c in wanted if c not in found]
        if missing:
            raise NervusError(f"{Path(path).name}: no value {missing[0]!r}; it has {', '.join(found)}")
        found = {c: found[c] for c in wanted}
    return SimResult.from_channels({n: (t, v) for n, (t, v, _u) in found.items()}, raster,
                                   {n: u for n, (_t, _v, u) in found.items()})  # fmt: skip


def log_channels(path: str | Path, dbc: str | Path | None = None) -> dict[str, str]:
    """The values in a log with their units."""
    return {name: unit for name, (_t, _v, unit) in _read(path, dbc).items()}


def is_can_log(path: str | Path) -> bool:
    """Whether a file is a CAN log by its name (python-can's ``.csv`` CAN logs are recognised by content)."""
    return Path(path).suffix.lower() in CAN_LOG_SUFFIXES


def _read(path: str | Path, dbc: str | Path | None) -> Channels:
    p = Path(path)
    if is_can_log(p) or dbc is not None or _python_can_csv(p):
        found = read_can_channels(p, dbc)
    else:
        found = read_csv_channels(p)
    if not found:
        raise NervusError(f"{p.name}: no numeric values to read")
    return found


# ---------------------------------------------------------------------------------------------------- CSV files


def read_csv_channels(path: str | Path) -> Channels:
    """The values of a scanner app's or logger's CSV file, each with its own time stamps (see the module docs)."""
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    rows = _rows(text)
    start = _header_row(rows)
    if start is None:
        raise NervusError(f"{Path(path).name}: no table of numbers found")
    if start < 0:  # no header: number the columns
        header, body = [f"column {k + 1}" for k in range(len(rows[0]))], rows
    else:
        header, body = rows[start], rows[start + 1 :]
    width = len(header)
    body = [r for r in body if len(r) == width and r != header]
    names = [_split_unit(h) for h in header]
    lower = [n.lower() for n, _u in names]
    if any(c in lower for c in _NAME_COLUMNS) and any(c in lower for c in _VALUE_COLUMNS):
        return _long(names, lower, body)
    tcol = _time_column(lower)
    times = _times([r[tcol] for r in body], names[tcol])
    out: Channels = {}
    for k, (name, unit) in enumerate(names):
        if k == tcol:
            continue
        values = np.array([_number(r[k]) for r in body])
        ok = np.isfinite(values) & np.isfinite(times)
        if ok.any():
            out[_unique(_clean(name) or f"column {k + 1}", out)] = (times[ok], values[ok], unit)
    return out


def _long(names: list[tuple[str, str]], lower: list[str], body: list[list[str]]) -> Channels:
    """One row per sample: time, value name, value (and unit)."""
    ncol = next(lower.index(c) for c in _NAME_COLUMNS if c in lower)
    vcol = next(lower.index(c) for c in _VALUE_COLUMNS if c in lower)
    ucol = next((lower.index(c) for c in _UNIT_COLUMNS if c in lower), None)
    tcol = _time_column([n if k not in (ncol, vcol) else "" for k, n in enumerate(lower)])
    times = _times([r[tcol] for r in body], names[tcol])
    samples: dict[str, tuple[list[float], list[float], str]] = {}
    for r, t in zip(body, times, strict=True):
        v = _number(r[vcol])
        name = _clean(r[ncol].strip())
        if not name or not np.isfinite(v) or not np.isfinite(t):
            continue
        ts, vs, _unit = samples.setdefault(name, ([], [], r[ucol].strip() if ucol is not None else ""))
        ts.append(float(t))
        vs.append(v)
    return {n: (np.array(ts), np.array(vs), u) for n, (ts, vs, u) in samples.items()}


def _rows(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    sample = "\n".join(lines[:50])
    delimiter = max((";", ",", "\t", "|"), key=lambda d: (_columns(sample, d), d == ","))
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)]


def _columns(sample: str, delimiter: str) -> float:
    """How well a separator splits the lines into equal columns (the median number of columns, if > 1)."""
    counts = sorted(len(r) for r in csv.reader(io.StringIO(sample), delimiter=delimiter))
    return counts[len(counts) // 2] if counts and counts[len(counts) // 2] > 1 else 0


def _header_row(rows: list[list[str]]) -> int | None:
    """The index of the header row: the row before the first row of numbers with as many columns. -1: the file
    starts with numbers (no header). None: no numbers at all."""
    for k, row in enumerate(rows):
        if len(row) >= 2 and _numeric_share(row) >= 0.5:
            if k == 0:
                return -1
            for h in range(k - 1, -1, -1):  # the nearest row above with as many columns
                if len(rows[h]) == len(row):
                    return h
            return -1
    return None


def _numeric_share(row: list[str]) -> float:
    filled = [c for c in row if c.lower() not in _EMPTY]
    if not filled:
        return 0.0
    return sum(np.isfinite(_number(c)) or _clock(c) is not None for c in filled) / len(filled)


def _number(cell: str) -> float:
    c = cell.strip().strip('"').replace("−", "-")
    if c.lower() in _EMPTY:
        return float("nan")
    if "," in c and "." not in c:  # decimal comma (the separator is then ';' or a tab)
        c = c.replace(",", ".")
    try:
        return float(c)
    except ValueError:
        return float("nan")


def _split_unit(header: str) -> tuple[str, str]:
    """``'Engine RPM(rpm)'`` -> ('Engine RPM', 'rpm'); ``'Speed (OBD)'`` stays one name (OBD is no unit)."""
    h = header.strip().strip('"')
    m = re.fullmatch(r"(.*?)\s*[(\[]([^()\[\]]*)[)\]]", h)
    if m and m.group(1) and _is_unit(m.group(2)):
        return m.group(1).strip(), m.group(2).strip()
    return h, ""


def _is_unit(text: str) -> bool:
    u = text.strip()
    return bool(u) and (u.lower().replace("°", "").replace("º", "") in _UNITS or "/" in u or "%" in u
                        or "°" in u or "º" in u)  # fmt: skip


def _clean(name: str) -> str:
    """A signal name: no ':' (it separates a block from its port in result keys)."""
    return name.replace(":", "_").strip()


def _unique(name: str, taken: Container[str]) -> str:
    k, out = 2, name
    while out in taken:
        out, k = f"{name} ({k})", k + 1
    return out


def _time_column(lower: list[str]) -> int:
    for wanted in _TIME_NAMES:
        if wanted in lower:
            return lower.index(wanted)
    for k, name in enumerate(lower):
        if "time" in name or name in ("ms", "msec"):
            return k
    return 0


def _times(cells: list[str], name_unit: tuple[str, str]) -> Array:
    """Time stamps in seconds: numbers (milliseconds if the name or unit says so), or date/clock times counted
    from the first one."""
    values = np.array([_number(c) for c in cells])
    if np.isfinite(values).sum() >= max(1, len(cells) // 2):
        name, unit = name_unit[0].lower(), name_unit[1].lower()
        if unit in ("ms", "msec") or re.search(r"\bms\b|msec|millisec", name):
            values = values / 1000.0
        elif unit in ("us", "µs") or "microsec" in name:
            values = values / 1e6
        return values
    clocks = np.array([_clock(c) for c in cells], dtype=float)  # None -> nan
    ok = np.isfinite(clocks)
    if not ok.any():
        raise NervusError(f"no time stamps in the column {name_unit[0]!r}")
    t = clocks - clocks[ok][0]
    if (t[ok] < -43200).any():  # clock times only, past midnight
        t[ok] = np.where(t[ok] < -43200, t[ok] + 86400, t[ok])
    return t


_formats_tried: dict[str, str] = {}


def _clock(cell: str) -> float | None:
    """A date or clock time as seconds (since 1970, or since midnight for clock times), else None."""
    c = re.sub(r"\s(GMT|UTC)[+-]\d\d:?\d\d|\s[A-Z]{3,4}(?=\s\d{4}$)", "", cell.strip().strip('"'))
    if not c or not any(ch.isdigit() for ch in c) or ":" not in c:
        return None
    last = _formats_tried.get("last")
    for fmt in ((last,) if last else ()) + _DATE_FORMATS:
        try:
            d = datetime.strptime(c, fmt)
        except ValueError:
            continue
        _formats_tried["last"] = fmt
        if d.year == 1900:  # clock time only
            return d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
        return d.timestamp()
    try:
        return datetime.fromisoformat(c).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------- CAN logs


def _python_can_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline().strip().lower()
    return first.startswith("timestamp,arbitration_id,extended,remote,error,dlc,data")


def read_can_channels(path: str | Path, dbc: str | Path | None = None) -> Channels:
    """The signals in a CAN log: decoded with a DBC file, or the OBD-II answers in it (see the module docs)."""
    try:
        import can
    except ImportError as exc:
        raise MissingDependencyError(f"reading CAN logs needs python-can: {install_hint('can')}") from exc
    frames: list[tuple[float, int, bool, bytes]] = []
    try:
        for msg in can.LogReader(str(path)):
            if not (msg.is_error_frame or msg.is_remote_frame):
                frames.append((float(msg.timestamp), msg.arbitration_id, bool(msg.is_extended_id), bytes(msg.data)))
    except Exception as exc:  # python-can raises various errors for unknown or broken files
        raise NervusError(f"cannot read the CAN log {Path(path).name}: {exc}") from exc
    if not frames:
        raise NervusError(f"{Path(path).name}: no CAN frames")
    return _decode_dbc(frames, dbc) if dbc is not None else _decode_obd(frames)


def _pgn(can_id: int) -> int:
    """The J1939 parameter group number of a 29-bit identifier (without priority, source and, for PDU1
    groups, the destination address)."""
    pgn = (can_id >> 8) & 0x3FFFF
    return pgn & 0x3FF00 if (pgn >> 8) & 0xFF < 240 else pgn


def _decode_dbc(frames: list[tuple[float, int, bool, bytes]], dbc: str | Path) -> Channels:
    from nervus.can import load_dbc, present, unpack, unpack_raw

    messages = list(load_dbc(dbc).values())
    exact = {(m.frame_id, m.is_extended): m for m in messages}
    by_pgn = {_pgn(m.frame_id): m for m in messages if m.is_extended}
    count: dict[str, int] = {}
    for m in messages:
        for s in m.signals:
            count[s.name] = count.get(s.name, 0) + 1
    samples: dict[str, tuple[list[float], list[float], str]] = {}
    for t, can_id, extended, data in frames:
        msg = exact.get((can_id, extended)) or (by_pgn.get(_pgn(can_id)) if extended else None)
        if msg is None:
            continue
        payload = np.frombuffer(data, dtype=np.uint8).astype(float)
        mux = msg.multiplexer
        mux_value = unpack_raw(mux, payload) if mux is not None else None
        for s in msg.signals:
            if s.length > 8 * len(data) or not present(s, mux_value):
                continue
            name = s.name if count[s.name] == 1 else f"{msg.name}.{s.name}"
            ts, vs, _u = samples.setdefault(name, ([], [], s.unit))
            ts.append(t)
            vs.append(unpack(s, payload))
    if not samples:
        raise NervusError("no frame in the log matches a message of the DBC file")
    return {n: (np.array(ts), np.array(vs), u) for n, (ts, vs, u) in samples.items()}


def _decode_obd(frames: list[tuple[float, int, bool, bytes]]) -> Channels:
    """Single-frame OBD-II answers: ISO-TP length, 0x41, PID, data bytes."""
    from nervus.obd import BY_NUMBER

    samples: dict[str, tuple[list[float], list[float], str]] = {}
    for t, can_id, extended, data in frames:
        if extended or not 0x7E8 <= can_id <= 0x7EF or len(data) < 3:
            continue
        n = data[0]
        if n >> 4 != 0 or n < 2 or data[1] != 0x41 or data[2] not in BY_NUMBER:
            continue
        pid = BY_NUMBER[data[2]]
        body = data[3 : 1 + n]
        if len(body) < pid.n_bytes:
            continue
        name = pid.name if can_id == 0x7E8 else f"{pid.name}@{can_id:X}"  # other ECUs (gearbox, ...)
        ts, vs, _u = samples.setdefault(name, ([], [], pid.unit))
        ts.append(t)
        vs.append(pid.decode(body))
    if not samples:
        raise NervusError("no OBD-II answers in the CAN log; give a DBC file to decode its other messages")
    return {n: (np.array(ts), np.array(vs), u) for n, (ts, vs, u) in samples.items()}
