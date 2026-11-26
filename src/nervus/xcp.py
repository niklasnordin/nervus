"""XCP on CAN: live measurement and calibration (ASAM MCD-1 XCP), the protocol of INCA and CANape.

A calibration tool (the XCP **master**) connects to an ECU (the **slave**) and reads and writes its memory by
address. The addresses and data types of the ECU's variables come from its A2L file. Each command is one CAN
frame from the master (``request_id``) and the answer one frame from the slave (``response_id``): byte 0 is
the command code, the answer starts with ``0xFF`` (ok) or ``0xFE`` and an error code.

This module implements the commands for measurement (polling and DAQ lists), calibration (with working and
reference pages) and access protection (seed and key):

=============== ====== ===============================================================
CONNECT         0xFF   start a session; the slave reports its resources and byte order
DISCONNECT      0xFE   end the session
GET_STATUS      0xFD   session status
SET_MTA         0xF6   set the memory transfer address
UPLOAD          0xF5   read *n* bytes from the MTA (the MTA moves on)
SHORT_UPLOAD    0xF4   read up to 6 bytes from an address
DOWNLOAD        0xF0   write up to 6 bytes at the MTA (the MTA moves on)
SHORT_DOWNLOAD  0xED   write up to 2 bytes at an address (fits in 8 bytes with the address)
GET_SEED        0xF8   ask for a seed to unlock calibration (CAL/PAG) or measurement (DAQ)
UNLOCK          0xF7   send the key computed from the seed
SET_CAL_PAGE    0xEB   switch between the working page (0, RAM) and the reference page (1)
GET_CAL_PAGE    0xEA   the active page
DAQ commands    0xE2,  FREE_DAQ, ALLOC_DAQ, ALLOC_ODT, ALLOC_ODT_ENTRY, SET_DAQ_PTR, WRITE_DAQ,
                0xE0,  SET_DAQ_LIST_MODE, START_STOP_DAQ_LIST, START_STOP_SYNCH, GET_DAQ_PROCESSOR_INFO:
                0xD3-  the slave sends the values of a list of addresses at every cycle of an event
                0xDE   (e.g. its 10 ms task), with a time stamp, without being asked
=============== ====== ===============================================================

Transports: CAN and CAN FD (:class:`CanTransport`, python-can), Ethernet (:class:`UdpTransport`,
:class:`TcpTransport`: each packet with a 4-byte header of length and counter), and :class:`DirectTransport` in
the same process. A slave can be served on them with :func:`serve_can` and :func:`serve_udp`.

* :class:`XcpServer` answers requests for a byte-addressed :class:`Memory`; the XCP Slave block uses it to make
  a simulated ECU reachable on a simulated or real CAN bus.
* :class:`XcpMaster` talks to a slave through a transport, e.g. :class:`CanTransport` (python-can), and reads
  and writes variables by name with an A2L file (:func:`read_a2l`).
"""

from __future__ import annotations

import re
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

CONNECT, DISCONNECT, GET_STATUS, SET_MTA, UPLOAD, SHORT_UPLOAD, DOWNLOAD, SHORT_DOWNLOAD = (
    0xFF, 0xFE, 0xFD, 0xF6, 0xF5, 0xF4, 0xF0, 0xED,
)  # fmt: skip
GET_SEED, UNLOCK, SET_CAL_PAGE, GET_CAL_PAGE = 0xF8, 0xF7, 0xEB, 0xEA
SET_DAQ_PTR, WRITE_DAQ, SET_DAQ_LIST_MODE, START_STOP_DAQ_LIST, START_STOP_SYNCH = 0xE2, 0xE1, 0xE0, 0xDE, 0xDD
GET_DAQ_PROCESSOR_INFO, FREE_DAQ, ALLOC_DAQ, ALLOC_ODT, ALLOC_ODT_ENTRY = 0xDA, 0xD6, 0xD5, 0xD4, 0xD3
OK, ERR = 0xFF, 0xFE
ERR_CMD_UNKNOWN, ERR_OUT_OF_RANGE, ERR_WRITE_PROTECTED, ERR_ACCESS_DENIED, ERR_ACCESS_LOCKED = (
    0x20,
    0x22,
    0x23,
    0x24,
    0x25,
)
ERR_PAGE_NOT_VALID, ERR_SEQUENCE, ERR_MEMORY_OVERFLOW = 0x26, 0x29, 0x30
ERRORS = {ERR_CMD_UNKNOWN: "unknown command", ERR_OUT_OF_RANGE: "out of range", ERR_ACCESS_DENIED: "access denied",
          ERR_SEQUENCE: "not connected", ERR_WRITE_PROTECTED: "write protected (the reference page is active)",
          ERR_ACCESS_LOCKED: "locked (unlock with seed and key)", ERR_PAGE_NOT_VALID: "no such page",
          ERR_MEMORY_OVERFLOW: "too many DAQ lists or entries"}  # fmt: skip
RESOURCE_CAL_PAG, RESOURCE_DAQ = 0x01, 0x04
MAX_CTO = 8
WORKING_PAGE, REFERENCE_PAGE = 0, 1

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


@dataclass
class _DaqList:
    odts: list[list[tuple[int, int]]] = field(default_factory=list)
    """Per ODT: (address, size) entries."""
    event: int = 0
    prescaler: int = 1
    timestamp: bool = False
    selected: bool = False
    running: bool = False
    first_pid: int = 0
    cycles: int = 0


class XcpServer:
    """The slave's protocol handler: :meth:`handle` takes a request packet and returns the answer packet;
    :meth:`event` returns the DAQ packets (DTOs) to send when an event (a task cycle) happens.

    *seed_key* protects calibration and DAQ: ``seed_key(seed) -> key`` must match what the master computes.
    *cal_regions* (start, size) are the calibration memory: its contents when the server starts are the reference
    page; the working page is the RAM, which the master changes. *max_cto* is 8 on CAN, up to 64 on CAN FD."""

    def __init__(self, memory: Memory, seed_key: Callable[[bytes], bytes] | None = None,
                 cal_regions: list[tuple[int, int]] | None = None, max_cto: int = MAX_CTO,
                 max_dto: int | None = None) -> None:  # fmt: skip
        self.memory = memory
        self.connected = False
        self.mta = 0
        self.seed_key = seed_key
        self.locked = RESOURCE_CAL_PAG | RESOURCE_DAQ if seed_key is not None else 0
        self._seed = b""
        self.max_cto = max_cto
        self.max_dto = max_dto or max_cto
        self.page = WORKING_PAGE
        self.cal_regions = list(cal_regions or [])
        self._reference = [memory.read(a, n) for a, n in self.cal_regions]
        self._working: list[bytes] = []
        self.daq: list[_DaqList] = []
        self._ptr = (0, 0, 0)
        self.t0 = time.monotonic()

    def handle(self, req: bytes) -> bytes:
        if not req:
            return b""
        cmd = req[0]
        try:
            if cmd == CONNECT:
                self.connected = True
                # resources, comm mode basic (bit 0 = 0: Intel byte order), MAX_CTO, MAX_DTO (2), versions
                res = RESOURCE_CAL_PAG | RESOURCE_DAQ
                return bytes([OK, res, 0x00, self.max_cto, *self.max_dto.to_bytes(2, "little"), 0x01, 0x01])
            if not self.connected:
                return bytes([ERR, ERR_SEQUENCE])
            return self._command(cmd, req)
        except IndexError:
            return bytes([ERR, ERR_OUT_OF_RANGE])
        except PermissionError:
            return bytes([ERR, ERR_ACCESS_DENIED])

    def _locked(self, resource: int) -> bytes | None:
        return bytes([ERR, ERR_ACCESS_LOCKED]) if self.locked & resource else None

    def _command(self, cmd: int, req: bytes) -> bytes:
        n = req[1] if len(req) > 1 else 0
        if cmd == DISCONNECT:
            self.connected = False
            self.locked = RESOURCE_CAL_PAG | RESOURCE_DAQ if self.seed_key is not None else 0
            return bytes([OK])
        if cmd == GET_STATUS:
            running = 0x40 if any(d.running for d in self.daq) else 0
            return bytes([OK, running, self.locked, 0x00, 0x00, 0x00])
        if cmd == SET_MTA:
            self.mta = int.from_bytes(req[4:8], "little")
            return bytes([OK])
        if cmd in (UPLOAD, SHORT_UPLOAD):
            if n > self.max_cto - 1:
                return bytes([ERR, ERR_OUT_OF_RANGE])
            addr = self.mta if cmd == UPLOAD else int.from_bytes(req[4:8], "little")
            data = self.memory.read(addr, n)
            if cmd == UPLOAD:
                self.mta += n
            return bytes([OK]) + data
        if cmd in (DOWNLOAD, SHORT_DOWNLOAD):
            locked = self._locked(RESOURCE_CAL_PAG)
            if locked:
                return locked
            limit = self.max_cto - 2 if cmd == DOWNLOAD else self.max_cto - 8
            if n > max(limit, 2 if cmd == SHORT_DOWNLOAD else 0):
                return bytes([ERR, ERR_OUT_OF_RANGE])
            addr = self.mta if cmd == DOWNLOAD else int.from_bytes(req[4:8], "little")
            data = bytes(req[2 : 2 + n]) if cmd == DOWNLOAD else bytes(req[8 : 8 + n]) if self.max_cto > 8 else bytes(
                req[2 : 2 + n])  # fmt: skip
            if self.page == REFERENCE_PAGE and self._in_cal(addr, len(data)):
                return bytes([ERR, ERR_WRITE_PROTECTED])
            self.memory.write(addr, data)
            if cmd == DOWNLOAD:
                self.mta += n
            return bytes([OK])
        if cmd == GET_SEED:
            resource = req[2] if len(req) > 2 else RESOURCE_CAL_PAG
            if self.seed_key is None or not self.locked & resource:
                return bytes([OK, 0])  # not protected: no seed needed
            self._seed = bytes(int(time.monotonic() * 1e6 + k * 7919) & 0xFF for k in range(4))
            self._seed_for = resource
            return bytes([OK, len(self._seed), *self._seed])
        if cmd == UNLOCK:
            if self.seed_key is None or not self._seed:
                return bytes([ERR, ERR_SEQUENCE])
            if bytes(req[2 : 2 + n]) != self.seed_key(self._seed):
                self.connected = False  # a wrong key ends the session (as the standard says)
                return bytes([ERR, ERR_ACCESS_LOCKED])
            self.locked &= ~self._seed_for
            self._seed = b""
            return bytes([OK, self.locked])
        if cmd == GET_CAL_PAGE:
            return bytes([OK, 0, 0, self.page])
        if cmd == SET_CAL_PAGE:
            locked = self._locked(RESOURCE_CAL_PAG)
            if locked:
                return locked
            page = req[3] if len(req) > 3 else 0
            if page not in (WORKING_PAGE, REFERENCE_PAGE):
                return bytes([ERR, ERR_PAGE_NOT_VALID])
            self.set_page(page)
            return bytes([OK])
        if cmd in (FREE_DAQ, ALLOC_DAQ, ALLOC_ODT, ALLOC_ODT_ENTRY, SET_DAQ_PTR, WRITE_DAQ, SET_DAQ_LIST_MODE,
                   START_STOP_DAQ_LIST, START_STOP_SYNCH, GET_DAQ_PROCESSOR_INFO):  # fmt: skip
            locked = self._locked(RESOURCE_DAQ)
            return locked or self._daq_command(cmd, req)
        return bytes([ERR, ERR_CMD_UNKNOWN])

    def _in_cal(self, address: int, n: int) -> bool:
        return any(a <= address < a + size or address <= a < address + n for a, size in self.cal_regions)

    def set_page(self, page: int) -> None:
        """Switch the calibration memory to the working page (0) or the reference page (1)."""
        if page == self.page:
            return
        if page == REFERENCE_PAGE:
            self._working = [self.memory.read(a, n) for a, n in self.cal_regions]
            for (a, _n), data in zip(self.cal_regions, self._reference, strict=True):
                self.memory.write(a, data)
        else:
            for (a, _n), data in zip(self.cal_regions, self._working, strict=True):
                self.memory.write(a, data)
        self.page = page

    def _daq_command(self, cmd: int, req: bytes) -> bytes:
        def u16(k: int) -> int:
            return int.from_bytes(req[k : k + 2], "little")

        if cmd == GET_DAQ_PROCESSOR_INFO:  # dynamic DAQ, time stamps, no predefined lists, 1 event channel
            return bytes([OK, 0x11, 0xFF, 0xFF, 0, 0, 0, 0])
        if cmd == FREE_DAQ:
            self.daq = []
            return bytes([OK])
        if cmd == ALLOC_DAQ:
            self.daq = [_DaqList() for _ in range(u16(2))]
            return bytes([OK])
        if cmd == ALLOC_ODT:
            self.daq[u16(2)].odts = [[] for _ in range(req[4])]
            return bytes([OK])
        if cmd == ALLOC_ODT_ENTRY:
            self.daq[u16(2)].odts[req[4]] = [(0, 0)] * req[5]
            return bytes([OK])
        if cmd == SET_DAQ_PTR:
            self._ptr = (u16(2), req[4], req[5])
            return bytes([OK])
        if cmd == WRITE_DAQ:
            d, o, e = self._ptr
            self.daq[d].odts[o][e] = (int.from_bytes(req[4:8], "little"), req[2])
            self._ptr = (d, o, e + 1)
            return bytes([OK])
        if cmd == SET_DAQ_LIST_MODE:
            dl = self.daq[u16(2)]
            dl.timestamp = bool(req[1] & 0x10)
            dl.event, dl.prescaler = u16(4), max(1, req[6])
            return bytes([OK])
        if cmd == START_STOP_DAQ_LIST:
            k = u16(2)
            dl = self.daq[k]
            dl.first_pid = sum(len(x.odts) for x in self.daq[:k])
            if req[1] == 0:
                dl.running = dl.selected = False
            elif req[1] == 1:
                dl.running, dl.cycles = True, 0
            else:
                dl.selected = True
            return bytes([OK, dl.first_pid])
        if cmd == START_STOP_SYNCH:
            for dl in self.daq:
                if req[1] == 0 or (req[1] == 2 and dl.selected):
                    dl.running = False
                elif req[1] == 1 and dl.selected:
                    dl.running, dl.cycles = True, 0
                dl.selected = False
            return bytes([OK])
        return bytes([ERR, ERR_CMD_UNKNOWN])

    def event(self, channel: int = 0, timestamp: float | None = None) -> list[bytes]:
        """The DTO packets of the running DAQ lists on this event channel: PID, a 4-byte time stamp in
        microseconds (first ODT, if enabled), then the values."""
        out = []
        ts = int(((time.monotonic() - self.t0) if timestamp is None else timestamp) * 1e6) & 0xFFFFFFFF
        for dl in self.daq:
            if not dl.running or dl.event != channel:
                continue
            dl.cycles += 1
            if (dl.cycles - 1) % dl.prescaler:
                continue
            for k, odt in enumerate(dl.odts):
                head = bytes([dl.first_pid + k]) + (ts.to_bytes(4, "little") if dl.timestamp and k == 0 else b"")
                try:
                    out.append(head + b"".join(self.memory.read(a, n) for a, n in odt))
                except (IndexError, PermissionError):
                    continue
        return out


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
    """Calls a server in the same process (for tests and examples). :meth:`event` runs the server's DAQ."""

    def __init__(self, server: XcpServer) -> None:
        self.server = server
        self.dtos: deque[tuple[float, bytes]] = deque()

    def request(self, frame: bytes, timeout: float) -> bytes | None:
        return self.server.handle(frame)

    def event(self, channel: int = 0, timestamp: float | None = None) -> None:
        for dto in self.server.event(channel, timestamp):
            self.dtos.append((time.monotonic(), dto))

    def receive(self, timeout: float) -> list[tuple[float, bytes]]:
        out = list(self.dtos)
        self.dtos.clear()
        return out


class CanTransport:
    """XCP on CAN (and CAN FD: ``fd=True``, packets up to 64 bytes) through python-can: requests with
    ``request_id``, answers and DAQ packets on ``response_id``."""

    def __init__(self, bus: Any, request_id: int = 0x7F0, response_id: int = 0x7F1, fd: bool = False,
                 extended: bool | None = None) -> None:  # fmt: skip
        self.bus, self.request_id, self.response_id, self.fd = bus, request_id, response_id, fd
        self.extended = request_id > 0x7FF if extended is None else extended
        self.dtos: deque[tuple[float, bytes]] = deque()

    def _keep(self, msg: Any) -> bytes | None:
        """An answer (first byte 0xFF/0xFE/0xFD/0xFC); DAQ packets go to the DTO queue."""
        if msg is None or msg.arbitration_id != self.response_id:
            return None
        data = bytes(msg.data)
        if data and data[0] < 0xFC:
            self.dtos.append((float(msg.timestamp or time.monotonic()), data))
            return None
        return data

    def request(self, frame: bytes, timeout: float) -> bytes | None:
        import can

        while (msg := self.bus.recv(timeout=0.0)) is not None:  # late answers; keep DAQ packets
            self._keep(msg)
        length = len(frame) if self.fd else 8
        if self.fd and length > 8:
            length = next(n for n in (12, 16, 20, 24, 32, 48, 64) if n >= length)
        self.bus.send(can.Message(arbitration_id=self.request_id, data=frame.ljust(length, b"\0"),
                                  is_extended_id=self.extended, is_fd=self.fd and length > 8))  # fmt: skip
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            answer = self._keep(self.bus.recv(timeout=left))
            if answer is not None:
                return answer
        return None

    def receive(self, timeout: float) -> list[tuple[float, bytes]]:
        """DAQ packets received so far (waiting up to *timeout* for the first)."""
        msg = self.bus.recv(timeout=timeout) if not self.dtos else None
        while msg is not None:
            self._keep(msg)
            msg = self.bus.recv(timeout=0.0)
        out = list(self.dtos)
        self.dtos.clear()
        return out


_NOTHING = object()


class _IpTransport:
    """XCP on Ethernet: every packet has a header of its length and a counter (2 bytes each, Intel)."""

    def __init__(self) -> None:
        self.counter = 0
        self.dtos: deque[tuple[float, bytes]] = deque()

    def _frame(self, packet: bytes) -> bytes:
        self.counter = (self.counter + 1) & 0xFFFF
        return len(packet).to_bytes(2, "little") + self.counter.to_bytes(2, "little") + packet

    @staticmethod
    def _split(data: bytes) -> list[bytes]:
        out, k = [], 0
        while k + 4 <= len(data):
            n = int.from_bytes(data[k : k + 2], "little")
            out.append(data[k + 4 : k + 4 + n])
            k += 4 + n
        return out

    def _sort(self, packets: list[bytes]) -> bytes | None:
        answer = None
        for p in packets:
            if p and p[0] < 0xFC:
                self.dtos.append((time.monotonic(), p))
            elif answer is None:
                answer = p
        return answer

    def receive(self, timeout: float) -> list[tuple[float, bytes]]:
        if not self.dtos:
            self._read(timeout)
        while self._read(0.0) is not _NOTHING:  # everything that is waiting
            pass
        out = list(self.dtos)
        self.dtos.clear()
        return out

    def _read(self, timeout: float) -> Any:
        """Read what arrived: the answer in it, None (only DAQ packets) or _NOTHING (nothing arrived)."""
        raise NotImplementedError

    def request(self, frame: bytes, timeout: float) -> bytes | None:
        self._send(self._frame(frame))
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            answer = self._read(left)
            if answer is not None and answer is not _NOTHING:
                return answer
        return None

    def _send(self, data: bytes) -> None:
        raise NotImplementedError


class UdpTransport(_IpTransport):
    """XCP on UDP/IP (the ECU's address and port, often 5555)."""

    def __init__(self, host: str, port: int = 5555) -> None:
        import socket

        super().__init__()
        self.address = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(self, data: bytes) -> None:
        self.sock.sendto(data, self.address)

    def _read(self, timeout: float) -> Any:

        self.sock.settimeout(timeout if timeout > 0 else 0.0)
        try:
            data, _addr = self.sock.recvfrom(65536)
        except (TimeoutError, BlockingIOError):
            return _NOTHING
        return self._sort(self._split(data))

    def close(self) -> None:
        self.sock.close()


class TcpTransport(_IpTransport):
    """XCP on TCP/IP."""

    def __init__(self, host: str, port: int = 5555) -> None:
        import socket

        super().__init__()
        self.sock = socket.create_connection((host, port), timeout=2.0)
        self.buffer = b""

    def _send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def _read(self, timeout: float) -> Any:

        self.sock.settimeout(timeout if timeout > 0 else 0.0)
        try:
            chunk = self.sock.recv(65536)
        except (TimeoutError, BlockingIOError):
            return _NOTHING
        self.buffer += chunk
        packets, k = [], 0
        while k + 4 <= len(self.buffer):
            n = int.from_bytes(self.buffer[k : k + 2], "little")
            if k + 4 + n > len(self.buffer):
                break
            packets.append(self.buffer[k + 4 : k + 4 + n])
            k += 4 + n
        self.buffer = self.buffer[k:]
        return self._sort(packets)

    def close(self) -> None:
        self.sock.close()


@dataclass
class DaqSignal:
    """A value in a DAQ list: where it is and how to decode it."""

    name: str
    address: int
    size: int
    decode: Callable[[bytes], float]


class XcpMaster:
    """A calibration tool in Python: connect, then read and write memory, or variables by A2L name.

    master = XcpMaster(CanTransport(can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)),
                       a2l="ecu.a2l")
    master.connect()
    master.read("engine_rpm")
    master.write("Kp_idle", 0.8)
    master.unlock(my_seed_key)                 # if the ECU is protected
    master.set_page(REFERENCE_PAGE)            # the safe calibration, in one step
    master.start_daq(["engine_rpm", "lambda"], event=0)
    master.read_daq()                          # {"engine_rpm": [(t, value), ...], ...}
    """

    def __init__(self, transport: Transport, a2l: str | Path | dict[str, A2lObject] | None = None,
                 timeout: float = 0.1) -> None:  # fmt: skip
        self.transport = transport
        self.timeout = timeout
        self.objects = read_a2l(a2l) if isinstance(a2l, str | Path) else dict(a2l or {})
        self.byte_order: Literal["little", "big"] = "little"
        """The slave's byte order (BYTE_ORDER in the CONNECT answer): addresses in commands and multi-byte values
        are sent in it."""
        self.max_cto = MAX_CTO
        self.max_dto = MAX_CTO
        self._daq: dict[int, tuple[bool, list[DaqSignal]]] = {}
        """PID -> (time stamp in the packet, signals in the ODT)."""

    def _cmd(self, frame: bytes) -> bytes:
        answer = self.transport.request(bytes(frame), self.timeout)
        if answer is None:
            raise XcpError(f"no answer to command 0x{frame[0]:02X}")
        if answer[0] != OK:
            code = answer[1] if len(answer) > 1 else 0
            raise XcpError(f"command 0x{frame[0]:02X}: {ERRORS.get(code, f'error 0x{code:02X}')}")
        return answer

    def _u16(self, v: int) -> bytes:
        return v.to_bytes(2, self.byte_order)

    def connect(self) -> dict[str, int]:
        a = self._cmd(bytes([CONNECT, 0]))
        if len(a) < 6:
            raise XcpError(f"CONNECT: answer too short ({len(a)} bytes)")
        self.byte_order = "big" if a[2] & 0x01 else "little"  # COMM_MODE_BASIC bit 0: BYTE_ORDER (1 = Motorola)
        self.max_cto = max(8, a[3])
        self.max_dto = int.from_bytes(a[4:6], self.byte_order)
        return {"resources": a[1], "comm_mode": a[2], "max_cto": a[3], "max_dto": self.max_dto}

    def disconnect(self) -> None:
        self._cmd(bytes([DISCONNECT]))

    def status(self) -> dict[str, int]:
        a = self._cmd(bytes([GET_STATUS]))
        return {"session": a[1], "protection": a[2]}

    def unlock(self, seed_key: Callable[[bytes], bytes], resources: int = RESOURCE_CAL_PAG | RESOURCE_DAQ) -> None:
        """Unlock protected resources: ask for a seed, send ``seed_key(seed)``. The function comes from the ECU
        supplier (often a DLL; wrap it in Python)."""
        for res in (RESOURCE_CAL_PAG, RESOURCE_DAQ):
            if not resources & res:
                continue
            a = self._cmd(bytes([GET_SEED, 0, res]))
            n = a[1]
            if n == 0:
                continue  # not protected
            key = seed_key(bytes(a[2 : 2 + n]))
            self._cmd(bytes([UNLOCK, len(key)]) + key)

    def upload(self, address: int, n: int) -> bytes:
        """Read ``n`` bytes from ``address`` (in pieces of up to MAX_CTO - 1 bytes)."""
        self._cmd(bytes([SET_MTA, 0, 0, 0]) + address.to_bytes(4, self.byte_order))
        out = b""
        while len(out) < n:
            k = min(self.max_cto - 1, n - len(out))
            out += self._cmd(bytes([UPLOAD, k]))[1 : 1 + k]
        return out

    def download(self, address: int, data: bytes) -> None:
        """Write ``data`` at ``address`` (in pieces of up to MAX_CTO - 2 bytes)."""
        self._cmd(bytes([SET_MTA, 0, 0, 0]) + address.to_bytes(4, self.byte_order))
        for k in range(0, len(data), self.max_cto - 2):
            piece = data[k : k + self.max_cto - 2]
            self._cmd(bytes([DOWNLOAD, len(piece)]) + piece)

    def page(self) -> int:
        return self._cmd(bytes([GET_CAL_PAGE, 0x01, 0]))[3]

    def set_page(self, page: int) -> None:
        """Switch the ECU and the tool to the working (0) or the reference page (1), all segments."""
        self._cmd(bytes([SET_CAL_PAGE, 0x83, 0, page]))

    # DAQ --------------------------------------------------------------------------------------------------------

    def start_daq(self, signals: list[str | DaqSignal], event: int = 0, prescaler: int = 1,
                  timestamps: bool = True) -> None:  # fmt: skip
        """Ask the slave to send these values at every cycle of *event* (names from the A2L file, or
        :class:`DaqSignal`). Read them with :meth:`read_daq`."""
        sigs = [self._daq_signal(s) for s in signals]
        room_first = self.max_dto - 1 - (4 if timestamps else 0)
        odts: list[list[DaqSignal]] = [[]]
        used = 0
        for s in sigs:
            room = room_first if len(odts) == 1 else self.max_dto - 1
            if s.size > self.max_dto - 1 - (4 if timestamps else 0):
                raise XcpError(f"{s.name}: {s.size} bytes do not fit in a DAQ packet")
            if used + s.size > room:
                odts.append([])
                used = 0
            odts[-1].append(s)
            used += s.size
        self._cmd(bytes([FREE_DAQ]))
        self._cmd(bytes([ALLOC_DAQ, 0]) + self._u16(1))
        self._cmd(bytes([ALLOC_ODT, 0]) + self._u16(0) + bytes([len(odts)]))
        for k, odt in enumerate(odts):
            self._cmd(bytes([ALLOC_ODT_ENTRY, 0]) + self._u16(0) + bytes([k, len(odt)]))
        for k, odt in enumerate(odts):
            self._cmd(bytes([SET_DAQ_PTR, 0]) + self._u16(0) + bytes([k, 0]))
            for s in odt:
                self._cmd(bytes([WRITE_DAQ, 0xFF, s.size, 0]) + s.address.to_bytes(4, self.byte_order))
        mode = 0x10 if timestamps else 0x00
        self._cmd(bytes([SET_DAQ_LIST_MODE, mode]) + self._u16(0) + self._u16(event) + bytes([prescaler, 0]))
        first = self._cmd(bytes([START_STOP_DAQ_LIST, 2]) + self._u16(0))[1]
        self._daq = {first + k: (timestamps and k == 0, odt) for k, odt in enumerate(odts)}
        self._cmd(bytes([START_STOP_SYNCH, 1]))

    def stop_daq(self) -> None:
        self._cmd(bytes([START_STOP_SYNCH, 0]))
        self._daq = {}

    def _daq_signal(self, s: str | DaqSignal) -> DaqSignal:
        if isinstance(s, DaqSignal):
            return s
        obj = self.objects[s]
        fmt = self._format(obj.datatype)

        def decode(raw: bytes) -> float:
            return float(struct.unpack(fmt, raw)[0])

        return DaqSignal(s, obj.address, struct.calcsize(fmt), decode)

    def read_daq(self, timeout: float = 0.05) -> dict[str, list[tuple[float, float]]]:
        """The DAQ values received since the last call: name -> [(time [s], value), ...]. The time is the
        slave's time stamp, else the time of arrival."""
        out: dict[str, list[tuple[float, float]]] = {}
        receive = getattr(self.transport, "receive", None)
        if receive is None:
            return out
        last_ts = None
        for arrival, packet in receive(timeout):
            entry = self._daq.get(packet[0])
            if entry is None:
                continue
            has_ts, sigs = entry
            k = 1
            if has_ts:
                last_ts = int.from_bytes(packet[1:5], "little") / 1e6
                k = 5
            t = last_ts if last_ts is not None else arrival
            for s in sigs:
                out.setdefault(s.name, []).append((t, s.decode(packet[k : k + s.size])))
                k += s.size
        return out

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


# ---------------------------------------------------------------------------- serving a slave


def serve_can(server: XcpServer, bus: Any, request_id: int = 0x7F0, response_id: int = 0x7F1,
              stop: threading.Event | None = None, fd: bool = False) -> threading.Thread:  # fmt: skip
    """Answer requests on a python-can bus in a thread (until *stop* is set). DAQ packets are sent by
    :meth:`XcpServer.event` through the returned thread's ``send_dtos(packets)``."""
    import can

    stop = stop or threading.Event()

    def send(data: bytes) -> None:
        n = 8 if not fd or len(data) <= 8 else next(k for k in (12, 16, 20, 24, 32, 48, 64) if k >= len(data))
        bus.send(can.Message(arbitration_id=response_id, data=data.ljust(n, b"\0"), is_extended_id=response_id > 0x7FF,
                             is_fd=fd and n > 8))  # fmt: skip

    def loop() -> None:
        while not stop.is_set():
            msg = bus.recv(timeout=0.05)
            if msg is not None and msg.arbitration_id == request_id:
                answer = server.handle(bytes(msg.data))
                if answer:
                    send(answer)

    thread = threading.Thread(target=loop, daemon=True, name="xcp-can")

    def send_dtos(packets: list[bytes]) -> None:
        for p in packets:
            send(p)

    thread.send_dtos = send_dtos  # type: ignore[attr-defined]
    thread.stop = stop  # type: ignore[attr-defined]
    thread.start()
    return thread


def serve_udp(server: XcpServer, port: int = 5555, host: str = "127.0.0.1",
              stop: threading.Event | None = None) -> threading.Thread:  # fmt: skip
    """Answer XCP-on-UDP requests in a thread. DAQ packets go to the last master that sent a request, through
    the returned thread's ``send_dtos(packets)``."""
    import socket

    stop = stop or threading.Event()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(0.05)
    peer: list[Any] = [None]
    counter = [0]

    def frame(p: bytes) -> bytes:
        counter[0] = (counter[0] + 1) & 0xFFFF
        return len(p).to_bytes(2, "little") + counter[0].to_bytes(2, "little") + p

    def loop() -> None:
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            peer[0] = addr
            for packet in _IpTransport._split(data):
                answer = server.handle(packet)
                if answer:
                    sock.sendto(frame(answer), addr)
        sock.close()

    def send_dtos(packets: list[bytes]) -> None:
        if peer[0] is not None and packets:
            sock.sendto(b"".join(frame(p) for p in packets), peer[0])

    thread = threading.Thread(target=loop, daemon=True, name="xcp-udp")
    thread.send_dtos = send_dtos  # type: ignore[attr-defined]
    thread.stop = stop  # type: ignore[attr-defined]
    thread.port = sock.getsockname()[1]  # type: ignore[attr-defined]
    thread.start()
    return thread
