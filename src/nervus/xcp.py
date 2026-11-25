"""XCP on CAN: live measurement and calibration (ASAM MCD-1 XCP), the protocol of INCA and CANape.

A calibration tool (the XCP **master**) connects to an ECU (the **slave**) and reads and writes its memory by
address. The addresses and data types of the ECU's variables come from its A2L file. Each command is one CAN
frame from the master (``request_id``) and the answer one frame from the slave (``response_id``): byte 0 is
the command code, the answer starts with ``0xFF`` (ok) or ``0xFE`` and an error code.

This module implements the basic commands for polling measurement and calibration:

=============== ====== ===============================================================
CONNECT         0xFF   start a session; the slave reports its resources and byte order
DISCONNECT      0xFE   end the session
GET_STATUS      0xFD   session status
SET_MTA         0xF6   set the memory transfer address
UPLOAD          0xF5   read *n* bytes from the MTA (the MTA moves on)
SHORT_UPLOAD    0xF4   read up to 6 bytes from an address
DOWNLOAD        0xF0   write up to 6 bytes at the MTA (the MTA moves on)
SHORT_DOWNLOAD  0xED   write up to 2 bytes at an address (fits in 8 bytes with the address)
=============== ====== ===============================================================

Event-driven measurement (DAQ lists) is not implemented: values are polled.

* :class:`XcpServer` answers requests for a byte-addressed :class:`Memory`; the XCP Slave block uses it to make
  a simulated ECU reachable on a simulated or real CAN bus.
* :class:`XcpMaster` talks to a slave through a transport, e.g. :class:`CanTransport` (python-can), and reads
  and writes variables by name with an A2L file (:func:`read_a2l`).
"""

from __future__ import annotations

import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

CONNECT, DISCONNECT, GET_STATUS, SET_MTA, UPLOAD, SHORT_UPLOAD, DOWNLOAD, SHORT_DOWNLOAD = (
    0xFF, 0xFE, 0xFD, 0xF6, 0xF5, 0xF4, 0xF0, 0xED,
)  # fmt: skip
OK, ERR = 0xFF, 0xFE
ERR_CMD_UNKNOWN, ERR_OUT_OF_RANGE, ERR_ACCESS_DENIED, ERR_SEQUENCE = 0x20, 0x22, 0x24, 0x29
ERRORS = {ERR_CMD_UNKNOWN: "unknown command", ERR_OUT_OF_RANGE: "out of range", ERR_ACCESS_DENIED: "access denied",
          ERR_SEQUENCE: "not connected"}  # fmt: skip
RESOURCE_CAL_PAG = 0x01
MAX_CTO = 8

_FORMATS = {"FLOAT64_IEEE": "<d", "FLOAT32_IEEE": "<f", "UBYTE": "<B", "SBYTE": "<b", "UWORD": "<H",
            "SWORD": "<h", "ULONG": "<I", "SLONG": "<i"}  # fmt: skip


class XcpError(RuntimeError):
    """The slave answered with an error, or did not answer."""


# ---------------------------------------------------------------------------- memory and slave


@dataclass
class Region:
    """A block of slave memory: ``size`` bytes at ``address``, read with ``read()`` and (if writable) written
    with ``write(offset, data)``."""

    address: int
    size: int
    read: Callable[[], bytes]
    write: Callable[[int, bytes], None] | None = None


class Memory:
    """The slave's address space as a list of regions."""

    def __init__(self, regions: list[Region] | None = None) -> None:
        self.regions = list(regions or [])

    def _find(self, address: int, n: int) -> Region:
        for r in self.regions:
            if r.address <= address and address + n <= r.address + r.size:
                return r
        raise IndexError(address)

    def read(self, address: int, n: int) -> bytes:
        r = self._find(address, n)
        off = address - r.address
        return r.read()[off : off + n]

    def write(self, address: int, data: bytes) -> None:
        r = self._find(address, len(data))
        if r.write is None:
            raise PermissionError(address)
        r.write(address - r.address, data)


class XcpServer:
    """The slave's protocol handler: :meth:`handle` takes a request frame and returns the answer frame."""

    def __init__(self, memory: Memory) -> None:
        self.memory = memory
        self.connected = False
        self.mta = 0

    def handle(self, req: bytes) -> bytes:
        if not req:
            return b""
        cmd = req[0]
        try:
            if cmd == CONNECT:
                self.connected = True
                # resources, comm mode basic (bit 0 = 0: Intel byte order), MAX_CTO, MAX_DTO (2), versions
                return bytes([OK, RESOURCE_CAL_PAG, 0x00, MAX_CTO, MAX_CTO, 0x00, 0x01, 0x01])
            if not self.connected:
                return bytes([ERR, ERR_SEQUENCE])
            if cmd == DISCONNECT:
                self.connected = False
                return bytes([OK])
            if cmd == GET_STATUS:
                return bytes([OK, 0x00, 0x00, 0x00, 0x00, 0x00])
            if cmd == SET_MTA:
                self.mta = int.from_bytes(req[4:8], "little")
                return bytes([OK])
            if cmd == UPLOAD:
                n = req[1]
                if n > MAX_CTO - 1:
                    return bytes([ERR, ERR_OUT_OF_RANGE])
                data = self.memory.read(self.mta, n)
                self.mta += n
                return bytes([OK]) + data
            if cmd == SHORT_UPLOAD:
                n = req[1]
                if n > MAX_CTO - 1:
                    return bytes([ERR, ERR_OUT_OF_RANGE])
                return bytes([OK]) + self.memory.read(int.from_bytes(req[4:8], "little"), n)
            if cmd == DOWNLOAD:
                n = req[1]
                if n > MAX_CTO - 2:
                    return bytes([ERR, ERR_OUT_OF_RANGE])
                self.memory.write(self.mta, bytes(req[2 : 2 + n]))
                self.mta += n
                return bytes([OK])
            if cmd == SHORT_DOWNLOAD:
                n = req[1]
                if n > 2:
                    return bytes([ERR, ERR_OUT_OF_RANGE])
                self.memory.write(int.from_bytes(req[4:8], "little"), bytes(req[2 : 2 + n]))
                return bytes([OK])
        except IndexError:
            return bytes([ERR, ERR_OUT_OF_RANGE])
        except PermissionError:
            return bytes([ERR, ERR_ACCESS_DENIED])
        return bytes([ERR, ERR_CMD_UNKNOWN])


# ---------------------------------------------------------------------------- A2L


@dataclass(frozen=True)
class A2lObject:
    name: str
    address: int
    datatype: str
    count: int = 1
    kind: str = "MEASUREMENT"
    unit: str = ""

    @property
    def size(self) -> int:
        return struct.calcsize(_FORMATS[self.datatype]) * self.count


_BLOCK = re.compile(r"/begin\s+(MEASUREMENT|CHARACTERISTIC)\s+(\S+)(.*?)/end\s+\1", re.S)


def read_a2l(path: str | Path) -> dict[str, A2lObject]:
    """The measurements and characteristics of an A2L file with their address and data type (a subset of
    ASAM MCD-2 MC: enough for the files Nervus writes and simple ECU files)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    layouts = dict(re.findall(r"/begin\s+RECORD_LAYOUT\s+(\S+)\s+FNC_VALUES\s+\d+\s+(\S+)", text))
    out = {}
    for kind, name, body in _BLOCK.findall(re.sub(r"/\*.*?\*/", " ", text, flags=re.S)):
        tokens = re.sub(r'"[^"]*"', '""', body).split()
        if kind == "MEASUREMENT":  # "desc" datatype conversion resolution accuracy lower upper ... ECU_ADDRESS a
            addr = re.search(r"ECU_ADDRESS\s+(0x[0-9A-Fa-f]+|\d+)", body)
            if addr is None or len(tokens) < 2:
                continue
            address, datatype = int(addr.group(1), 0), tokens[1]
            count_m = re.search(r"ARRAY_SIZE\s+(\d+)", body)
        else:  # "desc" VALUE|VAL_BLK|... address record_layout ...
            if len(tokens) < 4:
                continue
            address, datatype = int(tokens[2], 0), layouts.get(tokens[3], "FLOAT64_IEEE")
            count_m = re.search(r"(?:NUMBER|MATRIX_DIM)\s+(\d+)", body)
        unit = re.search(r'PHYS_UNIT\s+"([^"]*)"', body)
        out[name] = A2lObject(name, address, datatype, int(count_m.group(1)) if count_m else 1, kind,
                              unit.group(1) if unit else "")  # fmt: skip
    return out


# ---------------------------------------------------------------------------- master


class Transport(Protocol):
    def request(self, frame: bytes, timeout: float) -> bytes | None: ...


class DirectTransport:
    """Calls a server in the same process (for tests and examples)."""

    def __init__(self, server: XcpServer) -> None:
        self.server = server

    def request(self, frame: bytes, timeout: float) -> bytes | None:
        return self.server.handle(frame)


class CanTransport:
    """XCP on CAN through python-can: requests with ``request_id``, answers on ``response_id``."""

    def __init__(self, bus: Any, request_id: int = 0x7F0, response_id: int = 0x7F1) -> None:
        self.bus, self.request_id, self.response_id = bus, request_id, response_id

    def request(self, frame: bytes, timeout: float) -> bytes | None:
        import time

        import can

        while self.bus.recv(timeout=0.0) is not None:  # late answers to earlier (timed-out) commands
            pass
        self.bus.send(can.Message(arbitration_id=self.request_id, data=frame.ljust(8, b"\0"), is_extended_id=False))
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            msg = self.bus.recv(timeout=left)
            if msg is not None and msg.arbitration_id == self.response_id:
                return bytes(msg.data)
        return None


class XcpMaster:
    """A calibration tool in Python: connect, then read and write memory, or variables by A2L name.

    master = XcpMaster(CanTransport(can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)),
                       a2l="ecu.a2l")
    master.connect()
    master.read("engine_rpm")
    master.write("Kp_idle", 0.8)
    """

    def __init__(self, transport: Transport, a2l: str | Path | dict[str, A2lObject] | None = None,
                 timeout: float = 0.1) -> None:  # fmt: skip
        self.transport = transport
        self.timeout = timeout
        self.objects = read_a2l(a2l) if isinstance(a2l, str | Path) else dict(a2l or {})
        self.byte_order: Literal["little", "big"] = "little"
        """The slave's byte order (BYTE_ORDER in the CONNECT answer): addresses in commands and multi-byte values
        are sent in it."""

    def _cmd(self, frame: bytes) -> bytes:
        answer = self.transport.request(bytes(frame), self.timeout)
        if answer is None:
            raise XcpError(f"no answer to command 0x{frame[0]:02X}")
        if answer[0] != OK:
            code = answer[1] if len(answer) > 1 else 0
            raise XcpError(f"command 0x{frame[0]:02X}: {ERRORS.get(code, f'error 0x{code:02X}')}")
        return answer

    def connect(self) -> dict[str, int]:
        a = self._cmd(bytes([CONNECT, 0]))
        if len(a) < 6:
            raise XcpError(f"CONNECT: answer too short ({len(a)} bytes)")
        self.byte_order = "big" if a[2] & 0x01 else "little"  # COMM_MODE_BASIC bit 0: BYTE_ORDER (1 = Motorola)
        max_dto = int.from_bytes(a[4:6], self.byte_order)
        return {"resources": a[1], "comm_mode": a[2], "max_cto": a[3], "max_dto": max_dto}

    def disconnect(self) -> None:
        self._cmd(bytes([DISCONNECT]))

    def upload(self, address: int, n: int) -> bytes:
        """Read ``n`` bytes from ``address`` (in pieces of up to 7 bytes)."""
        self._cmd(bytes([SET_MTA, 0, 0, 0]) + address.to_bytes(4, self.byte_order))
        out = b""
        while len(out) < n:
            k = min(MAX_CTO - 1, n - len(out))
            out += self._cmd(bytes([UPLOAD, k]))[1 : 1 + k]
        return out

    def download(self, address: int, data: bytes) -> None:
        """Write ``data`` at ``address`` (in pieces of up to 6 bytes)."""
        self._cmd(bytes([SET_MTA, 0, 0, 0]) + address.to_bytes(4, self.byte_order))
        for k in range(0, len(data), MAX_CTO - 2):
            piece = data[k : k + MAX_CTO - 2]
            self._cmd(bytes([DOWNLOAD, len(piece)]) + piece)

    def _format(self, datatype: str) -> str:
        """The struct format of an A2L data type in the slave's byte order."""
        return (">" if self.byte_order == "big" else "<") + _FORMATS[datatype][1:]

    def read(self, name: str) -> float | list[float]:
        """The value of a measurement or characteristic from the A2L file."""
        obj = self.objects[name]
        fmt = self._format(obj.datatype)
        raw = self.upload(obj.address, obj.size)
        values = [v[0] for v in struct.iter_unpack(fmt, raw)]
        return values[0] if obj.count == 1 else values

    def write(self, name: str, value: float | list[float]) -> None:
        """Calibrate: write a characteristic (scalar or array)."""
        obj = self.objects[name]
        values = value if isinstance(value, list) else [value]
        fmt = self._format(obj.datatype)
        if fmt[-1] not in "fd":
            values = [round(v) for v in values]
        self.download(obj.address, b"".join(struct.pack(fmt, v) for v in values))
