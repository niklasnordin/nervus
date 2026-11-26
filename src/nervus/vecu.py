"""A virtual ECU: the generated C code of a model running in real time, measured and calibrated over XCP.

    uv run python -m nervus.vecu examples/throttle_ecu.nervus --subsystem ECU --udp 5555
    # writes ECU.a2l; connect INCA, CANape or Nervus's Calibration window to UDP port 5555

    ecu = VirtualEcu(model.block("ECU"))
    ecu.start(udp_port=5555)             # or can_bus=can.Bus(interface="virtual", channel="ecu")
    ...
    ecu.stop()

Nervus generates the code, compiles it into a shared library (as for software-in-the-loop tests), reads the
addresses of the variables from the library and writes them into the A2L file. A thread calls the step function
at the code's base period; an XCP slave serves the library's memory: reads of measurements, writes of
characteristics, DAQ lists on event channel 0 (every step), the working and the reference page, and optionally
seed and key. Only memory described in the A2L file can be read or written.

It behaves like a real ECU on a bench, so the same tools and windows work against it, the development boards
and production ECUs.
"""

from __future__ import annotations

import argparse
import ctypes
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from nervus.a2l import A2lFile, parse_a2l
from nervus.xcp import Memory, XcpServer, serve_can, serve_udp

Inputs = Callable[[float], dict[str, float]]


class ProcessMemory(Memory):
    """Memory of this process, at ``base + address``, limited to the given (address, size) ranges."""

    def __init__(self, base: int, ranges: list[tuple[int, int]], writable: list[tuple[int, int]]) -> None:
        super().__init__([])
        self.base = base
        self.ranges = _merge(ranges)
        self.writable = _merge(writable)

    @staticmethod
    def _inside(address: int, n: int, ranges: list[tuple[int, int]]) -> bool:
        return any(a <= address and address + n <= a + size for a, size in ranges)

    def read(self, address: int, n: int) -> bytes:
        if not self._inside(address, n, self.ranges):
            raise IndexError(address)
        return ctypes.string_at(self.base + address, n)

    def write(self, address: int, data: bytes) -> None:
        if not self._inside(address, len(data), self.ranges):
            raise IndexError(address)
        if not self._inside(address, len(data), self.writable):
            raise PermissionError(address)
        ctypes.memmove(self.base + address, data, len(data))


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, n in sorted(ranges):
        if out and a <= out[-1][0] + out[-1][1]:
            out[-1][1] = max(out[-1][1], a + n - out[-1][0])
        else:
            out.append([a, n])
    return [(a, n) for a, n in out]


class VirtualEcu:
    """Generated code of *target* (a model or a subsystem) as a running ECU with an XCP slave."""

    def __init__(self, target: Any, folder: str | Path | None = None,
                 seed_key: Callable[[bytes], bytes] | None = None) -> None:  # fmt: skip
        import tempfile

        from nervus.codegen import generate_c
        from nervus.codegen.a2l import fill_addresses, symbol_addresses
        from nervus.codegen.sil import build_shared_library

        self.code = generate_c(target)
        self.folder = Path(folder) if folder is not None else Path(tempfile.mkdtemp(prefix="nervus_vecu_"))
        lib_path = build_shared_library(self.code, self.folder)
        symbols = symbol_addresses(lib_path)
        name = self.code.name
        a2l_name = next(f for f in self.code.files if f.endswith(".a2l"))
        self.a2l_text = fill_addresses(self.code.files[a2l_name], symbols)
        self.a2l_path = self.folder / a2l_name
        self.a2l_path.write_text(self.a2l_text, encoding="utf-8")
        self.a2l: A2lFile = parse_a2l(self.a2l_text)
        self.lib = ctypes.CDLL(str(lib_path))
        anchor = f"{name}_P" if f"{name}_P" in symbols else next(s for s in symbols if s.startswith(name))
        base = ctypes.addressof(ctypes.c_char.in_dll(self.lib, anchor)) - symbols[anchor]
        readable = [(m.address, m.size()) for m in self.a2l.measurements.values()]
        cal = [(c.address, c.size(self.a2l)) for c in self.a2l.characteristics.values()]
        self.memory = ProcessMemory(base, readable + cal, cal)
        self.server = XcpServer(self.memory, seed_key=seed_key, cal_regions=_merge(cal))
        self.period = self.code.base_period
        self.lock = threading.Lock()
        self.inputs: Inputs = lambda t: {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.steps = 0
        dbl = ctypes.POINTER(ctypes.c_double)
        self.lib.nervus_sil_set_input.argtypes = [ctypes.c_int, dbl]
        self.lib.nervus_sil_initialize()

    @property
    def input_names(self) -> list[str]:
        return [n for n, _w in self.code.inputs]

    def step(self, t: float) -> None:
        values = self.inputs(t)
        for j, (n, w) in enumerate(self.code.inputs):
            if n in values:
                arr = (ctypes.c_double * w)(*([float(values[n])] * w))
                self.lib.nervus_sil_set_input(j, arr)
        self.lib.nervus_sil_step()
        self.steps += 1

    def start(self, udp_port: int | None = None, can_bus: Any = None, request_id: int = 0x7F0,
              response_id: int = 0x7F1, inputs: Inputs | None = None, speed: float = 1.0) -> None:  # fmt: skip
        """Serve XCP (UDP and/or CAN) and run the code in real time (*speed* × real time) in threads."""
        if inputs is not None:
            self.inputs = inputs
        # Packet size: 8 bytes on CAN; Ethernet carries bigger packets (DAQ of 8-byte doubles needs > 8)
        self.server.max_cto = self.server.max_dto = 8 if can_bus is not None else 255
        server = _Locked(self.server, self.lock)
        senders = []
        if udp_port is not None:
            t = serve_udp(server, udp_port, stop=self._stop)  # type: ignore[arg-type]
            self.udp_port = t.port  # type: ignore[attr-defined]
            senders.append(t)
        if can_bus is not None:
            senders.append(serve_can(server, can_bus, request_id, response_id, stop=self._stop))  # type: ignore[arg-type]
        self._threads += senders

        def run() -> None:
            t0 = time.monotonic()
            k = 0
            while not self._stop.is_set():
                t = k * self.period
                with self.lock:
                    self.step(t)
                    dtos = self.server.event(0, t)
                for s in senders:
                    s.send_dtos(dtos)  # type: ignore[attr-defined]
                k += 1
                wait = t0 + k * self.period / speed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)

        runner = threading.Thread(target=run, daemon=True, name="vecu")
        runner.start()
        self._threads.append(runner)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads = []


class _Locked:
    """An XCP server whose requests wait for the running step (like an ECU's background task)."""

    def __init__(self, server: XcpServer, lock: threading.Lock) -> None:
        self.server, self.lock = server, lock

    def handle(self, req: bytes) -> bytes:
        with self.lock:
            return self.server.handle(req)


def _main(argv: Sequence[str] | None = None) -> int:
    from nervus import load

    ap = argparse.ArgumentParser(prog="python -m nervus.vecu", description=__doc__.split("\n\n")[0])
    ap.add_argument("model", help="a .nervus model")
    ap.add_argument("--subsystem", help="the ECU subsystem (default: the whole model)")
    ap.add_argument("--udp", type=int, default=5555, help="UDP port (default 5555)")
    ap.add_argument(
        "--can", help="also serve on a python-can bus: interface:channel, e.g. virtual:ecu or pcan:PCAN_USBBUS1"
    )
    ap.add_argument("--a2l", help="where to write the A2L file (default: <name>.a2l here)")
    args = ap.parse_args(argv)
    d = load(args.model)
    ecu = VirtualEcu(d.block(args.subsystem) if args.subsystem else d)
    out = Path(args.a2l or Path.cwd() / ecu.a2l_path.name)
    out.write_text(ecu.a2l_text, encoding="utf-8")
    bus = None
    if args.can:
        import can

        interface, _, channel = args.can.partition(":")
        bus = can.Bus(interface=interface, channel=channel or None)
    ecu.start(udp_port=args.udp, can_bus=bus)
    print(f"virtual ECU {ecu.code.name}: XCP on UDP port {ecu.udp_port}" + (f" and CAN {args.can}" if bus else ""))
    print(f"A2L file: {out}; base period {ecu.period * 1000:g} ms; Ctrl+C stops")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        ecu.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
