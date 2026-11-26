"""A2L files of any ECU (ASAM MCD-2 MC): what a calibration tool needs to measure and calibrate it.

    from nervus.a2l import load_a2l
    a2l = load_a2l("ecu.a2l")
    a2l.measurements["engine_speed"]       # address, data type, conversion, limits, unit
    a2l.characteristics["ign_map"]         # a MAP: its axes, record layout, conversion
    raw = master.upload(c.address, c.size(a2l))
    values = c.decode(raw, a2l)            # physical values, with the axis points
    master.download(c.address, c.encode(values, a2l))

Reads MEASUREMENT, CHARACTERISTIC (VALUE, VAL_BLK, CURVE, MAP), AXIS_PTS, AXIS_DESCR (STD_AXIS, FIX_AXIS,
COM_AXIS), COMPU_METHOD (IDENTICAL, LINEAR, RAT_FUNC, TAB_INTP, TAB_NOINTP, TAB_VERB), COMPU_VTAB, COMPU_TAB,
RECORD_LAYOUT, MOD_COMMON (byte order, alignment), MEMORY_SEGMENT and the XCP connection (IF_DATA XCP: CAN
identifiers and baud rate, UDP/TCP port and address). Other blocks are skipped.
"""

from __future__ import annotations

import bisect
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nervus.core.errors import NervusError

DATATYPES = {"UBYTE": "B", "SBYTE": "b", "UWORD": "H", "SWORD": "h", "ULONG": "I", "SLONG": "i",
             "A_UINT64": "Q", "A_INT64": "q", "FLOAT32_IEEE": "f", "FLOAT64_IEEE": "d", "FLOAT16_IEEE": "e"}  # fmt: skip
_ALIGN_KEYS = {"ALIGNMENT_BYTE": 1, "ALIGNMENT_WORD": 2, "ALIGNMENT_LONG": 4, "ALIGNMENT_INT64": 8,
               "ALIGNMENT_FLOAT32_IEEE": 4, "ALIGNMENT_FLOAT64_IEEE": 8, "ALIGNMENT_FLOAT16_IEEE": 2}  # fmt: skip
_TOKEN = re.compile(r'"((?:[^"\\]|\\.|"")*)"|(/begin|/end)\s+(\w+)|(\S+)')


class A2lError(NervusError):
    pass


# ---------------------------------------------------------------------------------------------------- parsing


@dataclass
class Node:
    """A ``/begin TAG ... /end TAG`` block: its tokens (strings unquoted) and the blocks inside."""

    tag: str
    args: list[str] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)

    def all(self, tag: str) -> list[Node]:
        return [c for c in self.children if c.tag == tag]

    def first(self, tag: str) -> Node | None:
        return next((c for c in self.children if c.tag == tag), None)

    def keyword(self, key: str, n: int = 1) -> list[str] | None:
        """The *n* tokens after a keyword among the block's own tokens (not in its children)."""
        for k, tok in enumerate(self.args):
            if tok == key and k + n < len(self.args) + 1:
                return self.args[k + 1 : k + 1 + n]
        return None

    def walk(self) -> list[Node]:
        out = [self]
        for c in self.children:
            out += c.walk()
        return out


def parse(text: str) -> Node:
    """The block tree of an A2L file."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"(?m)//.*$", " ", text)
    root = Node("FILE")
    stack = [root]
    for m in _TOKEN.finditer(text):
        string, begin_end, tag, word = m.groups()
        if begin_end == "/begin":
            node = Node(tag)
            stack[-1].children.append(node)
            stack.append(node)
        elif begin_end == "/end":
            if len(stack) > 1 and stack[-1].tag == tag:
                stack.pop()
            else:  # tolerate a stray /end: close up to the matching block
                for k in range(len(stack) - 1, 0, -1):
                    if stack[k].tag == tag:
                        del stack[k:]
                        break
        elif string is not None:
            stack[-1].args.append(string.replace('""', '"').replace('\\"', '"'))
        else:
            stack[-1].args.append(word)
    return root


def _num(tok: str) -> float:
    try:
        return float(int(tok, 0)) if re.fullmatch(r"[+-]?0[xX][0-9A-Fa-f]+|[+-]?\d+", tok) else float(tok)
    except ValueError as exc:
        raise A2lError(f"not a number: {tok!r}") from exc


def _int(tok: str) -> int:
    return int(_num(tok))


# ---------------------------------------------------------------------------------------------------- objects


@dataclass
class CompuMethod:
    name: str
    kind: str = "IDENTICAL"
    format: str = ""
    unit: str = ""
    coeffs: tuple[float, ...] = ()
    table: list[tuple[float, float]] = field(default_factory=list)
    """TAB_INTP / TAB_NOINTP: (raw, physical) pairs, sorted by raw."""
    labels: dict[int, str] = field(default_factory=dict)
    """TAB_VERB: raw value -> text."""

    def to_physical(self, raw: float) -> float:
        k = self.kind
        if k == "LINEAR":
            a, b = self.coeffs
            return a * raw + b
        if k == "RAT_FUNC":
            a, b, c, d, e, f = self.coeffs
            if a or d:
                raise A2lError(f"{self.name}: quadratic RAT_FUNC conversions are not supported")
            den = e * raw - b
            return (c - f * raw) / den if den else float("nan")
        if k in ("TAB_INTP", "TAB_NOINTP") and self.table:
            xs = [x for x, _ in self.table]
            ys = [y for _, y in self.table]
            if raw <= xs[0]:
                return ys[0]
            if raw >= xs[-1]:
                return ys[-1]
            i = bisect.bisect_right(xs, raw) - 1
            if k == "TAB_NOINTP":
                return ys[i] if raw - xs[i] < xs[i + 1] - raw else ys[i + 1]
            return ys[i] + (ys[i + 1] - ys[i]) * (raw - xs[i]) / (xs[i + 1] - xs[i])
        return raw  # IDENTICAL, TAB_VERB (the number; see label)

    def to_raw(self, phys: float) -> float:
        k = self.kind
        if k == "LINEAR":
            a, b = self.coeffs
            return (phys - b) / a
        if k == "RAT_FUNC":
            a, b, c, d, e, f = self.coeffs
            if a or d:
                raise A2lError(f"{self.name}: quadratic RAT_FUNC conversions are not supported")
            return (b * phys + c) / (e * phys + f)
        if k in ("TAB_INTP", "TAB_NOINTP") and self.table:
            pairs = sorted((y, x) for x, y in self.table)
            ys, xs = [p[0] for p in pairs], [p[1] for p in pairs]
            if phys <= ys[0]:
                return xs[0]
            if phys >= ys[-1]:
                return xs[-1]
            i = bisect.bisect_right(ys, phys) - 1
            return xs[i] + (xs[i + 1] - xs[i]) * (phys - ys[i]) / (ys[i + 1] - ys[i])
        return phys

    def label(self, raw: float) -> str:
        return self.labels.get(int(raw), "") if self.kind == "TAB_VERB" else ""


IDENTICAL = CompuMethod("NO_COMPU_METHOD")


@dataclass
class Measurement:
    name: str
    description: str
    datatype: str
    conversion: str
    lower: float
    upper: float
    address: int = 0
    count: int = 1
    byte_order: str | None = None
    bit_mask: int | None = None
    unit: str = ""

    def size(self, a2l: A2lFile | None = None) -> int:
        return struct.calcsize(DATATYPES[self.datatype]) * self.count

    def decode(self, raw: bytes, a2l: A2lFile) -> list[float]:
        fmt = a2l.format(self.datatype, self.byte_order)
        values = [v[0] for v in struct.iter_unpack(fmt, raw[: self.size()])]
        if self.bit_mask is not None:
            mask = self.bit_mask
            shift = (mask & -mask).bit_length() - 1
            values = [float((int(v) & mask) >> shift) for v in values]
        cm = a2l.compu(self.conversion)
        return [cm.to_physical(v) for v in values]


@dataclass
class Axis:
    """An axis of a CURVE or MAP."""

    attribute: str  # STD_AXIS, FIX_AXIS, COM_AXIS
    input_quantity: str
    conversion: str
    max_points: int
    lower: float
    upper: float
    fixed: list[float] | None = None
    """FIX_AXIS: the axis points."""
    reference: str = ""
    """COM_AXIS: the AXIS_PTS object."""


@dataclass
class RecordLayout:
    name: str
    items: list[tuple[int, str, str, list[str]]] = field(default_factory=list)
    """(position, keyword, data type, other tokens), e.g. (1, "NO_AXIS_PTS_X", "UWORD", []) or
    (3, "FNC_VALUES", "FLOAT32_IEEE", ["ROW_DIR", "DIRECT"])."""
    alignment: dict[str, int] = field(default_factory=dict)
    static: bool = False
    """STATIC_RECORD_LAYOUT: room for the maximum number of axis points, also when fewer are used."""

    def item(self, keyword: str) -> tuple[int, str, str, list[str]] | None:
        return next((it for it in self.items if it[1] == keyword), None)

    @property
    def fnc_type(self) -> str:
        it = self.item("FNC_VALUES")
        if it is None:
            raise A2lError(f"record layout {self.name} has no FNC_VALUES")
        return it[2]


@dataclass
class Characteristic:
    name: str
    description: str
    type: str  # VALUE, VAL_BLK, CURVE, MAP, ASCII, ...
    address: int
    deposit: str
    conversion: str
    lower: float
    upper: float
    axes: list[Axis] = field(default_factory=list)
    count: int = 1
    """VAL_BLK: number of values (NUMBER or the product of MATRIX_DIM)."""
    byte_order: str | None = None
    bit_mask: int | None = None
    extended_limits: tuple[float, float] | None = None
    unit: str = ""

    def shape(self, a2l: A2lFile, counts: list[int] | None = None) -> list[int]:
        if self.type in ("CURVE", "MAP", "CUBOID"):
            return counts or [ax.max_points if ax.fixed is None else len(ax.fixed) for ax in self.axes]
        return [self.count]

    def _layout(self, a2l: A2lFile, counts: list[int]) -> list[tuple[str, int, int, str]]:
        """(keyword, offset, number of values, data type) of each item of the record, in memory order."""
        rl = a2l.record_layout(self.deposit)
        out, offset = [], 0
        axes_letters = "XYZ45"
        for _pos, key, dtype, _rest in sorted(rl.items):
            if key == "FNC_VALUES":
                n = 1
                for c in self.shape(a2l, counts):
                    n *= c
            elif key.startswith("NO_AXIS_PTS_"):
                n = 1
            elif key.startswith("AXIS_PTS_"):
                k = axes_letters.index(key[-1])
                n = counts[k] if k < len(counts) else 0
            else:
                continue  # SRC_ADDR, RIP, IDENTIFICATION, ...: not used here
            size = struct.calcsize(DATATYPES[dtype])
            align = a2l.alignment(dtype, rl)
            offset = (offset + align - 1) // align * align
            out.append((key, offset, n, dtype))
            offset += n * size
        return out

    def size(self, a2l: A2lFile, counts: list[int] | None = None) -> int:
        counts = counts or self.shape(a2l)
        lay = self._layout(a2l, counts)
        return max((off + n * struct.calcsize(DATATYPES[dt]) for _k, off, n, dt in lay), default=0)

    def counts_from(self, raw: bytes, a2l: A2lFile) -> list[int]:
        """The number of points of each axis: from the record (NO_AXIS_PTS_*), else the maximum."""
        counts = self.shape(a2l)
        for key, off, _n, dtype in self._layout(a2l, counts):
            if key.startswith("NO_AXIS_PTS_"):
                k = "XYZ45".index(key[-1])
                fmt = a2l.format(dtype, self.byte_order)
                if off + struct.calcsize(fmt) <= len(raw):
                    counts[k] = min(counts[k], int(struct.unpack_from(fmt, raw, off)[0]))
        return counts

    def decode(self, raw: bytes, a2l: A2lFile, com_axes: dict[str, list[float]] | None = None,
               counts: list[int] | None = None) -> dict[str, Any]:  # fmt: skip
        """Physical values: ``{"values": [...] (a list of rows for a MAP: values[x][y]), "axes": [[x...], [y...]]}``.
        *com_axes* gives the points of COM_AXIS axes (read from their AXIS_PTS objects)."""
        if counts is None:
            counts = self.counts_from(raw, a2l) if self.type in ("CURVE", "MAP") else self.shape(a2l)
            if a2l.record_layout(self.deposit).static and self.type in ("CURVE", "MAP"):
                return self._decode_static(raw, a2l, com_axes, counts)
        lay = {k: (off, n, dt) for k, off, n, dt in self._layout(a2l, counts)}
        off, n, dt = lay["FNC_VALUES"]
        fmt = a2l.format(dt, self.byte_order)
        size = struct.calcsize(fmt)
        cm = a2l.compu(self.conversion)
        raws = [struct.unpack_from(fmt, raw, off + k * size)[0] for k in range(n)]
        if self.bit_mask is not None:
            shift = (self.bit_mask & -self.bit_mask).bit_length() - 1
            raws = [(int(v) & self.bit_mask) >> shift for v in raws]
        vals = [cm.to_physical(float(v)) for v in raws]
        axes = []
        for k, ax in enumerate(self.axes):
            axes.append(self._axis_points(k, ax, raw, lay, counts[k], a2l, com_axes or {}))
        if self.type == "MAP":
            nx, ny = counts[0], counts[1]
            column_dir = "COLUMN_DIR" in a2l.record_layout(self.deposit).item("FNC_VALUES")[3]  # type: ignore[index]
            if column_dir:
                values: list[Any] = [vals[i * ny : (i + 1) * ny] for i in range(nx)]
            else:
                values = [[vals[j * nx + i] for j in range(ny)] for i in range(nx)]
            return {"values": values, "axes": axes}
        return {"values": vals, "axes": axes}

    def _decode_static(self, raw: bytes, a2l: A2lFile, com_axes: dict[str, list[float]] | None,
                       counts: list[int]) -> dict[str, Any]:  # fmt: skip
        """A static record: decoded with room for the maximum number of points, then cut to the points in use."""
        full = self.decode(raw, a2l, com_axes, self.shape(a2l))
        axes = [ax[:n] for ax, n in zip(full["axes"], counts, strict=False)]
        if self.type == "MAP":
            return {"values": [row[: counts[1]] for row in full["values"][: counts[0]]], "axes": axes}
        return {"values": full["values"][: counts[0]], "axes": axes}

    def _axis_points(self, k: int, ax: Axis, raw: bytes, lay: dict[str, tuple[int, int, str]], n: int,
                     a2l: A2lFile, com_axes: dict[str, list[float]]) -> list[float]:  # fmt: skip
        if ax.fixed is not None:
            return ax.fixed[:n]
        if ax.attribute == "COM_AXIS":
            return com_axes.get(ax.reference, [float(i) for i in range(n)])[:n]
        key = f"AXIS_PTS_{'XYZ45'[k]}"
        if key not in lay:
            return [float(i) for i in range(n)]
        off, _n, dt = lay[key]
        fmt = a2l.format(dt, self.byte_order)
        cm = a2l.compu(ax.conversion)
        return [
            cm.to_physical(float(struct.unpack_from(fmt, raw, off + i * struct.calcsize(fmt))[0])) for i in range(n)
        ]

    def encode(self, values: list[Any], a2l: A2lFile, raw: bytes) -> bytes:
        """The record with new physical function values (the axes stay; a MAP's values as values[x][y]).
        *raw* is the current record (read before), so axis points and other items are kept."""
        counts = self.counts_from(raw, a2l) if self.type in ("CURVE", "MAP") else self.shape(a2l)
        static = a2l.record_layout(self.deposit).static and self.type in ("CURVE", "MAP")
        room = self.shape(a2l) if static else counts  # a static record keeps room for the maximum
        lay = {k: (off, n, dt) for k, off, n, dt in self._layout(a2l, room)}
        off, n, dt = lay["FNC_VALUES"]
        places: list[tuple[int, float]] = []
        if self.type == "MAP":
            nx, ny = counts[0], counts[1]
            column_dir = "COLUMN_DIR" in a2l.record_layout(self.deposit).item("FNC_VALUES")[3]  # type: ignore[index]
            for i in range(nx):
                for j in range(ny):
                    places.append((i * room[1] + j if column_dir else j * room[0] + i, float(values[i][j])))
        else:
            places = [(k, float(v)) for k, v in enumerate(values)]
        expected = counts[0] * counts[1] if self.type == "MAP" else counts[0] if self.type == "CURVE" else n
        if len(places) != expected:
            raise A2lError(f"{self.name}: {len(places)} values for {expected} places")
        fmt = a2l.format(dt, self.byte_order)
        cm = a2l.compu(self.conversion)
        out = bytearray(raw)
        for k, v in places:
            r = cm.to_raw(float(v))
            if fmt[-1] not in "fde":
                lo, hi = _int_range(fmt[-1])
                r = min(max(round(r), lo), hi)
            struct.pack_into(fmt, out, off + k * struct.calcsize(fmt), r)
        return bytes(out)


@dataclass
class AxisPts:
    name: str
    description: str
    address: int
    input_quantity: str
    deposit: str
    conversion: str
    max_points: int
    lower: float
    upper: float
    byte_order: str | None = None

    def size(self, a2l: A2lFile) -> int:
        rl = a2l.record_layout(self.deposit)
        n_item = rl.item("NO_AXIS_PTS_X")
        pts = rl.item("AXIS_PTS_X")
        if pts is None:
            raise A2lError(f"record layout {rl.name} has no AXIS_PTS_X")
        size = struct.calcsize(DATATYPES[pts[2]]) * self.max_points
        if n_item is not None:
            align = a2l.alignment(pts[2], rl)
            size += (struct.calcsize(DATATYPES[n_item[2]]) + align - 1) // align * align
        return size

    def decode(self, raw: bytes, a2l: A2lFile) -> list[float]:
        rl = a2l.record_layout(self.deposit)
        n_item, pts = rl.item("NO_AXIS_PTS_X"), rl.item("AXIS_PTS_X")
        assert pts is not None
        n, offset = self.max_points, 0
        if n_item is not None and (n_item[0] < pts[0]):
            fmt_n = a2l.format(n_item[2], self.byte_order)
            n = min(n, int(struct.unpack_from(fmt_n, raw, 0)[0]))
            align = a2l.alignment(pts[2], rl)
            offset = (struct.calcsize(fmt_n) + align - 1) // align * align
        fmt = a2l.format(pts[2], self.byte_order)
        cm = a2l.compu(self.conversion)
        return [cm.to_physical(float(struct.unpack_from(fmt, raw, offset + i * struct.calcsize(fmt))[0]))
                for i in range(n)]  # fmt: skip


@dataclass
class MemorySegment:
    name: str
    kind: str  # CODE, DATA, OFFLINE_DATA, ...
    memory: str  # RAM, FLASH, EEPROM, ...
    address: int
    size: int


@dataclass
class XcpOnCan:
    request_id: int
    response_id: int
    baudrate: int | None = None
    fd: bool = False


@dataclass
class XcpOnIp:
    protocol: str  # UDP or TCP
    port: int
    host: str = ""


@dataclass
class A2lFile:
    name: str = ""
    byte_order: str = "MSB_LAST"
    """MSB_LAST (Intel, little endian; also LITTLE_ENDIAN) or MSB_FIRST (Motorola, big endian)."""
    alignments: dict[str, int] = field(default_factory=dict)
    measurements: dict[str, Measurement] = field(default_factory=dict)
    characteristics: dict[str, Characteristic] = field(default_factory=dict)
    axis_pts: dict[str, AxisPts] = field(default_factory=dict)
    compu_methods: dict[str, CompuMethod] = field(default_factory=dict)
    record_layouts: dict[str, RecordLayout] = field(default_factory=dict)
    memory_segments: list[MemorySegment] = field(default_factory=list)
    xcp_can: XcpOnCan | None = None
    xcp_ip: list[XcpOnIp] = field(default_factory=list)

    def compu(self, name: str) -> CompuMethod:
        return self.compu_methods.get(name, IDENTICAL)

    def record_layout(self, name: str) -> RecordLayout:
        try:
            return self.record_layouts[name]
        except KeyError:
            raise A2lError(f"no RECORD_LAYOUT {name}") from None

    def format(self, datatype: str, byte_order: str | None = None) -> str:
        order = byte_order or self.byte_order
        big = order in ("MSB_FIRST", "BIG_ENDIAN")
        try:
            return (">" if big else "<") + DATATYPES[datatype]
        except KeyError:
            raise A2lError(f"unknown data type {datatype}") from None

    def alignment(self, datatype: str, layout: RecordLayout | None = None) -> int:
        key = {"UBYTE": "ALIGNMENT_BYTE", "SBYTE": "ALIGNMENT_BYTE", "UWORD": "ALIGNMENT_WORD",
               "SWORD": "ALIGNMENT_WORD", "ULONG": "ALIGNMENT_LONG", "SLONG": "ALIGNMENT_LONG",
               "A_UINT64": "ALIGNMENT_INT64", "A_INT64": "ALIGNMENT_INT64", "FLOAT16_IEEE": "ALIGNMENT_FLOAT16_IEEE",
               "FLOAT32_IEEE": "ALIGNMENT_FLOAT32_IEEE", "FLOAT64_IEEE": "ALIGNMENT_FLOAT64_IEEE"}[datatype]  # fmt: skip
        if layout is not None and key in layout.alignment:
            return layout.alignment[key]
        return self.alignments.get(key, struct.calcsize(DATATYPES[datatype]))

    def unit(self, conversion: str) -> str:
        return self.compu(conversion).unit

    def writable(self, address: int, size: int) -> bool:
        """Whether a memory range lies in a calibration (DATA) segment; True if the file lists no segments."""
        if not self.memory_segments:
            return True
        return any(s.kind in ("DATA", "OFFLINE_DATA") and s.address <= address and address + size <= s.address + s.size
                   for s in self.memory_segments)  # fmt: skip


def _int_range(code: str) -> tuple[int, int]:
    bits = struct.calcsize(code) * 8
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if code.islower() else (0, (1 << bits) - 1)


def _byte_order(node: Node) -> str | None:
    bo = node.keyword("BYTE_ORDER")
    return bo[0] if bo else None


def _bit_mask(node: Node) -> int | None:
    bm = node.keyword("BIT_MASK")
    return _int(bm[0]) if bm else None


def load_a2l(path: str | Path) -> A2lFile:
    """Read an A2L file (see the module docs)."""
    return parse_a2l(Path(path).read_text(encoding="utf-8", errors="replace"))


def parse_a2l(text: str) -> A2lFile:
    tree = parse(text)
    modules = [n for n in tree.walk() if n.tag == "MODULE"]
    if not modules:
        raise A2lError("no MODULE in the A2L file")
    mod = modules[0]
    out = A2lFile(name=mod.args[0] if mod.args else "")
    common = mod.first("MOD_COMMON")
    if common is not None:
        out.byte_order = _byte_order(common) or out.byte_order
        for key in _ALIGN_KEYS:
            v = common.keyword(key)
            if v:
                out.alignments[key] = _int(v[0])
    par = mod.first("MOD_PAR")
    if par is not None:
        for seg in par.all("MEMORY_SEGMENT"):
            a = seg.args
            try:
                out.memory_segments.append(MemorySegment(a[0], a[2], a[3], _int(a[5]), _int(a[6])))
            except (IndexError, A2lError):
                continue
    vtabs = {n.args[0]: n for n in mod.all("COMPU_VTAB") if n.args}
    tabs = {n.args[0]: n for n in mod.all("COMPU_TAB") if n.args}
    for n in mod.all("COMPU_METHOD"):
        a = n.args
        if len(a) < 5:
            continue
        cm = CompuMethod(a[0], a[2], a[3], a[4])
        if cm.kind == "LINEAR":
            coef = n.keyword("COEFFS_LINEAR", 2)
            cm.coeffs = tuple(_num(x) for x in coef) if coef else (1.0, 0.0)
        elif cm.kind == "RAT_FUNC":
            coef = n.keyword("COEFFS", 6)
            cm.coeffs = tuple(_num(x) for x in coef) if coef else (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        elif cm.kind in ("TAB_INTP", "TAB_NOINTP", "TAB_VERB"):
            ref = n.keyword("COMPU_TAB_REF")
            if ref and ref[0] in vtabs and cm.kind == "TAB_VERB":
                t = vtabs[ref[0]].args
                pairs = t[4 : 4 + 2 * _int(t[3])]
                cm.labels = {int(_num(pairs[k])): pairs[k + 1] for k in range(0, len(pairs) - 1, 2)}
            elif ref and ref[0] in tabs:
                t = tabs[ref[0]].args
                pairs = t[4 : 4 + 2 * _int(t[3])]
                cm.table = sorted((_num(pairs[k]), _num(pairs[k + 1])) for k in range(0, len(pairs) - 1, 2))
        unit_ref = n.keyword("REF_UNIT")
        if unit_ref and not cm.unit:
            cm.unit = unit_ref[0]
        out.compu_methods[cm.name] = cm
    for n in mod.all("RECORD_LAYOUT"):
        rl = RecordLayout(n.args[0])
        a = n.args
        k = 1
        while k < len(a):
            key = a[k]
            if key in ("FNC_VALUES", "AXIS_PTS_X", "AXIS_PTS_Y", "AXIS_PTS_Z", "NO_AXIS_PTS_X", "NO_AXIS_PTS_Y",
                       "NO_AXIS_PTS_Z") and k + 2 < len(a):  # fmt: skip
                extra = 2 if key.startswith(("FNC", "AXIS_PTS")) else 0
                rl.items.append((_int(a[k + 1]), key, a[k + 2], a[k + 3 : k + 3 + extra]))
                k += 3 + extra
            elif key == "STATIC_RECORD_LAYOUT":
                rl.static = True
                k += 1
            elif key in _ALIGN_KEYS and k + 1 < len(a):
                rl.alignment[key] = _int(a[k + 1])
                k += 2
            else:
                k += 1
        out.record_layouts[rl.name] = rl
    for n in mod.all("MEASUREMENT"):
        a = n.args
        if len(a) < 8:
            continue
        addr = n.keyword("ECU_ADDRESS")
        count = n.keyword("ARRAY_SIZE")
        dims = n.keyword("MATRIX_DIM", 3)
        total = _int(count[0]) if count else 1
        if dims:
            total = 1
            for d in dims:
                try:
                    total *= max(1, _int(d))
                except A2lError:
                    break
        m = Measurement(a[0], a[1], a[2], a[3], _num(a[6]), _num(a[7]), _int(addr[0]) if addr else 0, total,
                        _byte_order(n), _bit_mask(n))  # fmt: skip
        unit = n.keyword("PHYS_UNIT")
        m.unit = unit[0] if unit else out.unit(m.conversion)
        out.measurements[m.name] = m
    for n in mod.all("AXIS_PTS"):
        a = n.args
        if len(a) < 10:
            continue
        out.axis_pts[a[0]] = AxisPts(a[0], a[1], _int(a[2]), a[3], a[4], a[6], _int(a[7]), _num(a[8]), _num(a[9]),
                                     _byte_order(n))  # fmt: skip
    for n in mod.all("CHARACTERISTIC"):
        a = n.args
        if len(a) < 9:
            continue
        c = Characteristic(a[0], a[1], a[2], _int(a[3]), a[4], a[6], _num(a[7]), _num(a[8]),
                           byte_order=_byte_order(n), bit_mask=_bit_mask(n))  # fmt: skip
        number = n.keyword("NUMBER")
        dims = n.keyword("MATRIX_DIM", 3)
        if number:
            c.count = _int(number[0])
        elif dims:
            c.count = 1
            for d in dims:
                try:
                    c.count *= max(1, _int(d))
                except A2lError:
                    break
        ext = n.keyword("EXTENDED_LIMITS", 2)
        if ext:
            c.extended_limits = (_num(ext[0]), _num(ext[1]))
        for ad in n.all("AXIS_DESCR"):
            b = ad.args
            if len(b) < 6:
                continue
            ax = Axis(b[0], b[1], b[2], _int(b[3]), _num(b[4]), _num(b[5]))
            par_ = ad.keyword("FIX_AXIS_PAR", 3)
            dist = ad.keyword("FIX_AXIS_PAR_DIST", 3)
            lst = ad.first("FIX_AXIS_PAR_LIST")
            if par_:
                off, shift, cnt = _num(par_[0]), _int(par_[1]), _int(par_[2])
                ax.fixed = [off + i * 2**shift for i in range(cnt)]
            elif dist:
                off, step, cnt = _num(dist[0]), _num(dist[1]), _int(dist[2])
                ax.fixed = [off + i * step for i in range(cnt)]
            elif lst is not None:
                ax.fixed = [_num(x) for x in lst.args]
            ref = ad.keyword("AXIS_PTS_REF")
            if ref:
                ax.reference = ref[0]
            if ax.fixed is not None:
                ax.fixed = [out.compu(ax.conversion).to_physical(v) for v in ax.fixed]
            c.axes.append(ax)
        unit = n.keyword("PHYS_UNIT")
        c.unit = unit[0] if unit else out.unit(c.conversion)
        out.characteristics[c.name] = c
    for n in mod.walk():
        if n.tag == "XCP_ON_CAN":
            req, resp, baud = n.keyword("CAN_ID_MASTER"), n.keyword("CAN_ID_SLAVE"), n.keyword("BAUDRATE")
            if req and resp and out.xcp_can is None:
                out.xcp_can = XcpOnCan(_int(req[0]) & 0x1FFFFFFF, _int(resp[0]) & 0x1FFFFFFF,
                                       _int(baud[0]) if baud else None, n.first("CAN_FD") is not None)  # fmt: skip
        elif n.tag in ("XCP_ON_UDP_IP", "XCP_ON_TCP_IP") and len(n.args) >= 2:
            host = n.keyword("ADDRESS") or n.keyword("HOST_NAME") or [""]
            out.xcp_ip.append(XcpOnIp("UDP" if "UDP" in n.tag else "TCP", _int(n.args[1]), host[0]))
    return out
