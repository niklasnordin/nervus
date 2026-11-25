"""OBD-II: read live data from a car through its diagnostic port (read-only).

Every petrol car since 2001 (EU) / 1996 (US) and diesel since 2004 has an OBD-II port. With an **ELM327**
adapter (USB or Bluetooth, a few euros) you can read standard values such as engine speed, coolant temperature
or throttle position, defined by SAE J1979 as *mode 01 PIDs*. This module decodes those PIDs, talks to an
ELM327, and records a drive into a Nervus result file that you can plot in the Data Inspector or replay as a
test input::

    uv run python -m nervus.obd pids                                    # list the known PIDs
    uv run python -m nervus.obd record --port /dev/tty.usbserial-1110 \\
        --pids rpm,speed,coolant_temp,throttle --duration 120 --out drive.nervres

Nervus also reads **trouble codes** (stored, pending and permanent), the freeze frame and the VIN::

    uv run python -m nervus.obd dtc --port /dev/tty.usbserial-1110

Nervus only sends standard *read* requests (modes 01, 02, 03, 07, 09, 0A). It never clears codes (mode 04
also resets the emissions readiness monitors), writes to, or reprograms the car's ECUs.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from nervus.core.errors import install_hint
from nervus.sim.result import SimResult, SimStats


@dataclass(frozen=True)
class Pid:
    pid: int
    name: str
    description: str
    unit: str
    n_bytes: int
    decode: Callable[[bytes], float]
    scale: float = 1.0
    """Physical value = raw · scale + offset (raw: the data bytes as one unsigned number); used to encode."""
    offset: float = 0.0

    @property
    def request(self) -> str:
        return f"01{self.pid:02X}"

    def encode(self, value: float) -> bytes:
        """The data bytes for ``value`` (rounded, limited to what the bytes can hold): for a simulated ECU."""
        top = 256**self.n_bytes - 1
        raw = round((float(value) - self.offset) / self.scale) if np.isfinite(value) else 0
        return int(min(max(raw, 0), top)).to_bytes(self.n_bytes, "big")


def _a(d: bytes) -> int:
    return d[0]


def _ab(d: bytes) -> int:
    return 256 * d[0] + d[1]


def _linear(pid: int, name: str, description: str, unit: str, n: int, scale: float, offset: float = 0.0) -> Pid:
    """A PID whose value is (raw · scale + offset), raw being its ``n`` data bytes."""

    def decode(d: bytes) -> float:
        return int.from_bytes(d[:n], "big") * scale + offset

    return Pid(pid, name, description, unit, n, decode, scale, offset)


PIDS: dict[str, Pid] = {
    p.name: p
    for p in (
        _linear(0x04, "engine_load", "Calculated engine load", "%", 1, 100 / 255),
        _linear(0x05, "coolant_temp", "Engine coolant temperature", "°C", 1, 1.0, -40.0),
        _linear(0x06, "stft_b1", "Short-term fuel trim, bank 1", "%", 1, 100 / 128, -100.0),
        _linear(0x07, "ltft_b1", "Long-term fuel trim, bank 1", "%", 1, 100 / 128, -100.0),
        _linear(0x0B, "map", "Intake manifold absolute pressure", "kPa", 1, 1.0),
        _linear(0x0C, "rpm", "Engine speed", "rpm", 2, 0.25),
        _linear(0x0D, "speed", "Vehicle speed", "km/h", 1, 1.0),
        _linear(0x0E, "timing_advance", "Ignition timing advance before TDC", "°", 1, 0.5, -64.0),
        _linear(0x0F, "intake_temp", "Intake air temperature", "°C", 1, 1.0, -40.0),
        _linear(0x10, "maf", "Mass air flow", "g/s", 2, 0.01),
        _linear(0x11, "throttle", "Throttle position", "%", 1, 100 / 255),
        Pid(0x14, "o2_b1s1", "Oxygen sensor voltage, bank 1 sensor 1", "V", 2, lambda d: _a(d) / 200, 1 / 200 / 256),
        _linear(0x1F, "run_time", "Time since engine start", "s", 2, 1.0),
        _linear(0x2F, "fuel_level", "Fuel tank level", "%", 1, 100 / 255),
        _linear(0x33, "baro", "Barometric pressure", "kPa", 1, 1.0),
        _linear(0x42, "module_voltage", "Control module voltage", "V", 2, 0.001),
        _linear(0x44, "lambda_cmd", "Commanded air-fuel equivalence ratio (lambda)", "-", 2, 2 / 65536),
        _linear(0x46, "ambient_temp", "Ambient air temperature", "°C", 1, 1.0, -40.0),
        _linear(0x49, "pedal_d", "Accelerator pedal position D", "%", 1, 100 / 255),
        _linear(0x5C, "oil_temp", "Engine oil temperature", "°C", 1, 1.0, -40.0),
        _linear(0x5E, "fuel_rate", "Engine fuel rate", "L/h", 2, 0.05),
    )
}
BY_NUMBER = {p.pid: p for p in PIDS.values()}


class ObdError(Exception):
    pass


def parse_response(text: str, mode: int, pid: int) -> bytes | None:
    """Data bytes of an ELM327 reply such as ``"41 0C 1A F8"`` (``None`` for ``NO DATA``).

    Handles echo, spaces or no spaces, ``SEARCHING...``, several lines and several ECUs answering (the first
    matching answer is used).
    """
    want = f"{0x40 + mode:02X}{pid:02X}"
    for line in text.replace("\r", "\n").split("\n"):
        compact = line.strip().replace(" ", "").upper()
        if not compact or compact.startswith(("SEARCHING", ">")):
            continue
        if "NODATA" in compact or "ERROR" in compact or "UNABLETOCONNECT" in compact:
            return None
        k = compact.find(want)
        if k >= 0:
            hexdata = compact[k + len(want) :]
            try:
                return bytes.fromhex(hexdata[: (len(hexdata) // 2) * 2])
            except ValueError:
                continue
    return None


def supported_from_bitmask(base: int, data: bytes) -> set[int]:
    """PIDs announced as supported by a reply to PID 0x00, 0x20, 0x40, ... (4 bytes, MSB first)."""
    bits = int.from_bytes(data[:4], "big")
    return {base + k + 1 for k in range(32) if bits & (1 << (31 - k))}


# ---------------------------------------------------------------------------- trouble codes, VIN

DTC_LETTERS = "PCBU"  # powertrain, chassis, body, network
DTC_DESCRIPTIONS = {
    "P0101": "Mass air flow sensor range/performance",
    "P0113": "Intake air temperature sensor high input",
    "P0118": "Engine coolant temperature sensor high input",
    "P0128": "Coolant temperature below thermostat regulating temperature",
    "P0133": "O2 sensor slow response (bank 1 sensor 1)",
    "P0171": "System too lean (bank 1)",
    "P0172": "System too rich (bank 1)",
    "P0300": "Random/multiple cylinder misfire detected",
    "P0301": "Cylinder 1 misfire detected",
    "P0302": "Cylinder 2 misfire detected",
    "P0303": "Cylinder 3 misfire detected",
    "P0304": "Cylinder 4 misfire detected",
    "P0335": "Crankshaft position sensor circuit",
    "P0401": "Exhaust gas recirculation flow insufficient",
    "P0420": "Catalyst system efficiency below threshold (bank 1)",
    "P0442": "Evaporative emission system small leak",
    "P0455": "Evaporative emission system large leak",
    "P0500": "Vehicle speed sensor",
    "P0505": "Idle air control system",
    "P0562": "System voltage low",
}


def decode_dtc(two_bytes: bytes) -> str:
    """``b"\x01\x33"`` -> ``"P0133"`` (SAE J2012: 2 bits letter, 2 bits digit, 3 hex digits)."""
    a, b = two_bytes[0], two_bytes[1]
    return f"{DTC_LETTERS[a >> 6]}{(a >> 4) & 0x3}{a & 0xF:X}{b >> 4:X}{b & 0xF:X}"


def describe_dtc(code: str) -> str:
    """A short description for common generic codes; otherwise the code's category (SAE J2012: P0, P2 and the
    P34-P39 range are generic; for B, C and U only 0 is generic, 1 and 2 are manufacturer-specific)."""
    if code in DTC_DESCRIPTIONS:
        return DTC_DESCRIPTIONS[code]
    system = {"P": "powertrain", "C": "chassis", "B": "body", "U": "network"}[code[0]]
    generic = code[1] == "0" or (code[0] == "P" and (code[1] == "2" or (code[1] == "3" and code[2] in "456789")))
    return f"{'generic (SAE)' if generic else 'manufacturer-specific'} {system} code"


def _responses(text: str) -> list[bytes]:
    """The data bytes of each response in an ELM327 reply (headers off).

    A response is one line, or a CAN multi-frame message: a byte-count line (``014``) followed by numbered frames
    (``0: 49 02 01 ...``, ``1: ...``). Those are joined and cut to the byte count, which drops the padding of the
    last frame. Several ECUs answering give several responses.
    """
    out: list[bytes] = []
    multi: bytearray | None = None
    count: int | None = None

    def flush() -> None:
        nonlocal multi, count
        if multi is not None:
            out.append(bytes(multi[:count] if count is not None else multi))
        multi, count = None, None

    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip().upper()
        if not line or line.startswith(("SEARCHING", ">")) or "NO DATA" in line:
            continue
        compact = line.replace(" ", "")
        if len(line) >= 2 and line[1] == ":" and line[0] in "0123456789ABCDEF":  # a numbered CAN frame
            try:
                data = bytes.fromhex(compact[2 : 2 + ((len(compact) - 2) // 2) * 2])
            except ValueError:
                continue
            # frame 0 again: another ECU's message, unless the index wrapped (F -> 0) in a long message
            if multi is None or (line[0] == "0" and len(multi) > 0 and (count is None or len(multi) >= count)):
                flush()
                multi = bytearray()
            multi += data
            continue
        flush()
        if len(compact) <= 3:  # the byte count of the multi-frame message that follows (or an echo such as "03")
            try:
                count = int(compact, 16)
            except ValueError:
                count = None
            multi = bytearray() if count is not None else None
            continue
        try:
            out.append(bytes.fromhex(compact[: (len(compact) // 2) * 2]))
        except ValueError:
            continue
    flush()
    return [r for r in out if r]


def parse_dtcs(text: str, mode: int = 3) -> list[str]:
    """Trouble codes in a reply to mode 03 (stored), 07 (pending) or 0A (permanent).

    Non-CAN cars answer ``43 01 33 00 00 00 00`` (three codes per line, padded with 0000); CAN cars add the
    number of codes: ``43 02 01 33 04 20`` (with long lists as a multi-frame message). The codes of every ECU
    that answers are returned, in the order of the replies.
    """
    head = 0x40 + mode
    codes: list[str] = []
    for r in _responses(text):
        if r[0] != head:
            continue
        # CAN: header, count, two bytes per code (an even length); non-CAN: header and three codes (7 bytes)
        body = r[2 : 2 + 2 * r[1]] if len(r) % 2 == 0 else r[1:]
        for k in range(0, len(body) - 1, 2):
            if body[k : k + 2] != b"\x00\x00":
                codes.append(decode_dtc(body[k : k + 2]))
    return codes


VIN_CHARACTERS = frozenset(b"0123456789ABCDEFGHJKLMNPRSTUVWXYZ")
"""The characters of a VIN (ISO 3779): digits and capital letters except I, O and Q."""


def vin_from_bytes(data: bytes) -> str | None:
    """The VIN in the data bytes of a mode 09 PID 02 reply after the header (fill bytes are dropped)."""
    chars = bytes(c for c in data if c in VIN_CHARACTERS).decode("ascii")
    return chars[-17:] if len(chars) >= 17 else None


def parse_vin(text: str) -> str | None:
    """The 17-character vehicle identification number in a reply to mode 09 PID 02.

    CAN cars send one multi-frame message ``49 02 01`` + 17 characters; non-CAN cars five lines
    ``49 02 nn`` + 4 bytes (the first one starting with three fill bytes)."""
    parts = sorted((r[2], r[3:]) for r in _responses(text) if len(r) >= 3 and r[:2] == b"\x49\x02")
    if not parts:
        return None
    if len(parts) == 1 or parts[0][0] != 1 or len(parts[0][1]) >= 17:  # CAN: one message with every character
        return vin_from_bytes(parts[0][1])
    return vin_from_bytes(b"".join(data for _n, data in parts))


class Transport(Protocol):
    def write(self, data: bytes) -> object: ...

    def read_until(self, expected: bytes) -> bytes: ...


class SerialTransport:
    """A serial port (USB or Bluetooth SPP) to an ELM327; needs ``pyserial`` (``uv sync --extra obd``)."""

    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 5.0) -> None:
        try:
            import serial
        except ImportError as exc:
            raise ObdError(f"talking to an ELM327 needs pyserial: {install_hint('obd')}") from exc
        self.port = serial.Serial(port, baudrate=baudrate, timeout=timeout)

    def write(self, data: bytes) -> object:
        return self.port.write(data)

    def read_until(self, expected: bytes) -> bytes:
        return bytes(self.port.read_until(expected))


class Elm327:
    """Minimal ELM327 client: initialise, ask which PIDs the car supports, and read PIDs."""

    def __init__(self, transport: Transport) -> None:
        self.t = transport
        self.supported: set[int] = set()

    def command(self, cmd: str) -> str:
        self.t.write((cmd + "\r").encode("ascii"))
        return self.t.read_until(b">").decode("ascii", errors="replace").replace(">", "").strip()

    def initialize(self) -> set[int]:
        for cmd in ("ATZ", "ATE0", "ATL0", "ATS1", "ATH0", "ATSP0"):  # reset, no echo, auto protocol
            self.command(cmd)
        self.supported = set()
        base = 0
        while True:
            data = parse_response(self.command(f"01{base:02X}"), 1, base)
            if data is None or len(data) < 4:
                break
            found = supported_from_bitmask(base, data)
            self.supported |= found
            if base + 0x20 not in found:
                break
            base += 0x20
        if not self.supported:
            raise ObdError("no answer from the car: is the ignition on and the adapter connected?")
        return self.supported

    def read(self, pid: Pid) -> float | None:
        data = parse_response(self.command(pid.request), 1, pid.pid)
        if data is None or len(data) < pid.n_bytes:
            return None
        return float(pid.decode(data[: pid.n_bytes]))

    def trouble_codes(self, kind: str = "stored") -> list[str]:
        """Diagnostic trouble codes: ``stored`` (mode 03, these light the MIL), ``pending`` (07, seen once but
        not confirmed) or ``permanent`` (0A, cannot be cleared by a tool)."""
        mode = {"stored": 3, "pending": 7, "permanent": 0x0A}[kind]
        return parse_dtcs(self.command(f"{mode:02X}"), mode)

    def freeze_frame(self, pid: Pid, frame: int = 0) -> float | None:
        """The value of ``pid`` stored when the first trouble code was set (mode 02)."""
        data = parse_response(self.command(f"02{pid.pid:02X}{frame:02X}"), 2, pid.pid)
        if data is None or len(data) < pid.n_bytes + 1:
            return None
        return float(pid.decode(data[1 : 1 + pid.n_bytes]))  # the first byte is the frame number

    def vin(self) -> str | None:
        return parse_vin(self.command("0902"))


class ObdClient(Protocol):
    """What :func:`record` needs: :class:`Elm327` or :class:`nervus.obd_can.CanObd`."""

    supported: set[int]

    def read(self, pid: Pid) -> float | None: ...


def record(
    elm: ObdClient,
    pids: Sequence[str],
    duration: float,
    interval: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    on_sample: Callable[[float, dict[str, float]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> SimResult:
    """Poll ``pids`` (names from :data:`PIDS`) for ``duration`` seconds into a result.

    Each signal is named after its PID (``rpm``, ``speed``, ...). Missing answers are stored as NaN. The time
    axis is the time each sweep over all PIDs started. ``duration`` 0 records until ``should_stop()`` is true.
    """
    chosen = [PIDS[p] for p in pids]
    unsupported = [p.name for p in chosen if elm.supported and p.pid not in elm.supported]
    if unsupported:
        raise ObdError(f"the car does not report: {', '.join(unsupported)}")
    t0 = clock()
    times: list[float] = []
    rows: list[list[float]] = []
    while True:
        now = clock() - t0
        if (duration > 0 and now > duration) or (should_stop is not None and should_stop()):
            break
        values = {}
        for p in chosen:
            v = elm.read(p)
            values[p.name] = np.nan if v is None else v
        times.append(now)
        rows.append([values[p.name] for p in chosen])
        if on_sample is not None:
            on_sample(now, values)
        if interval > 0:
            sleep(max(0.0, interval - (clock() - t0 - now)))
    data = np.array(rows, dtype=float).reshape(len(times), len(chosen))
    signals = {f"{p.name}:0": data[:, k : k + 1] for k, p in enumerate(chosen)}
    stats = SimStats("obd", None, len(times), len(times), float(times[-1]) if times else 0.0)
    return SimResult(np.array(times), signals, {}, {}, stats)


def _simulate(args: argparse.Namespace) -> int:
    """``python -m nervus.obd simulate``: the model's OBD Responder answers on a pty (and optionally CAN)."""
    from nervus import load, simulate
    from nervus.obd_sim import ecu, serve_pty

    model = load(args.model)
    names = sorted({str(b.params["ecu"]) for _p, b in model.walk() if b.type_id == "diagnostics.obd_responder"})
    if not names:
        raise ObdError(f"{args.model} has no OBD Responder block")
    stops = []
    for name in names:
        sim = ecu(name)
        if not args.no_pty:
            path, _thread = serve_pty(sim)
            print(f"ECU {name!r}: ELM327 on {path}  (e.g. python -m nervus.obd dtc --port {path})")
        if args.can_interface:
            from nervus.obd_can import serve_can

            stops.append(serve_can(sim, args.can_interface, args.can_channel))
            print(f"ECU {name!r}: answering OBD requests on CAN ({args.can_interface} {args.can_channel or ''})")
    if not args.fast:
        model.settings.pacing = 1.0
    print(f"simulating {args.duration or model.settings.t_final:g} s{'' if args.fast else ' in real time'} ...")
    try:
        simulate(model, args.duration)
    finally:
        for stop in stops:
            stop()
    print("done")
    return 0


def _main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nervus.obd", description="Read live data from a car (read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pids", help="list the PIDs Nervus can decode")
    sp = sub.add_parser("supported", help="ask the car which PIDs it supports")
    dp = sub.add_parser("dtc", help="read trouble codes (stored, pending, permanent), freeze frame and VIN")
    rp = sub.add_parser("record", help="record PIDs to a .nervres or .csv file")
    for reader in (sp, dp, rp):
        reader.add_argument("--port", help="serial port of an ELM327 adapter")
        reader.add_argument("--can-interface", help="use a CAN interface instead (socketcan, pcan, kvaser, ...)")
        reader.add_argument("--can-channel", help="CAN channel, e.g. can0 or PCAN_USBBUS1")
    rp.add_argument("--pids", default="rpm,speed,coolant_temp,throttle,engine_load")
    rp.add_argument("--duration", type=float, default=60.0)
    rp.add_argument("--interval", type=float, default=0.0, help="seconds between sweeps (0 = as fast as possible)")
    rp.add_argument("--out", default="drive.nervres")
    sp = sub.add_parser("simulate", help="run a model with an OBD Responder as a simulated car (ELM327 on a pty)")
    sp.add_argument("model", help="a .nervus model with an OBD Responder block")
    sp.add_argument("--duration", type=float, default=None, help="seconds to simulate (default: the stop time)")
    sp.add_argument("--fast", action="store_true", help="as fast as possible instead of in real time")
    sp.add_argument("--no-pty", action="store_true", help="do not open a serial port (e.g. only CAN)")
    sp.add_argument("--can-interface", default=None, help="also answer on a CAN bus: socketcan, pcan, virtual, ...")
    sp.add_argument("--can-channel", default=None, help="CAN channel, e.g. can0")
    args = ap.parse_args(argv)
    if args.cmd == "simulate":
        return _simulate(args)
    if args.cmd == "pids":
        for p in PIDS.values():
            print(f"{p.name:16s} 01{p.pid:02X}  {p.unit:6s} {p.description}")
        return 0
    elm: Any
    if args.can_interface:
        from nervus.obd_can import CanObd

        elm = CanObd(interface=args.can_interface, channel=args.can_channel)
    elif args.port:
        elm = Elm327(SerialTransport(args.port))
    else:
        ap.error("give --port (ELM327) or --can-interface")
    supported = elm.initialize()
    if args.cmd == "dtc":
        print(f"VIN: {elm.vin() or 'not reported'}")
        for kind in ("stored", "pending", "permanent"):
            codes = elm.trouble_codes(kind)
            print(f"{kind} trouble codes: {len(codes) or 'none'}")
            for code in codes:
                print(f"  {code}  {describe_dtc(code)}")
        frame = {name: elm.freeze_frame(PIDS[name]) for name in ("rpm", "speed", "coolant_temp", "engine_load")}
        if any(v is not None for v in frame.values()):
            print("freeze frame: " + ", ".join(f"{k}={v:g}" for k, v in frame.items() if v is not None))
        return 0
    if args.cmd == "supported":
        for n in sorted(supported):
            known = BY_NUMBER.get(n)
            print(f"01{n:02X}  {known.name if known else '(not decoded by Nervus)'}")
        return 0
    names = [p.strip() for p in args.pids.split(",") if p.strip()]
    res = record(
        elm,
        names,
        args.duration,
        args.interval,
        on_sample=lambda t, v: print(f"{t:7.2f} s  " + "  ".join(f"{k}={x:.1f}" for k, x in v.items())),
    )
    if args.out.endswith(".csv"):
        res.to_csv(args.out)
    else:
        res.save(args.out)
    print(f"saved {len(res.t)} samples to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
