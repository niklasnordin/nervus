"""OBD-II over CAN (ISO 15765-4) with ``python-can``: no ELM327 needed.

Cars since about 2008 use CAN for OBD-II: 11-bit identifiers at 500 kbit/s. A tool sends a request to the
*functional* address 0x7DF (every ECU listens); the engine ECU answers from 0x7E8. Requests and answers are
packed into CAN frames with **ISO-TP** (ISO 15765-2): up to 7 bytes fit in one *single frame*; longer answers
(the VIN, many trouble codes) are split into a *first frame* and *consecutive frames*, after the receiver has
sent a *flow control* frame.

    from nervus.obd_can import CanObd
    obd = CanObd(interface="socketcan", channel="can0")    # or "pcan", "kvaser", "vector", ...
    obd.initialize()
    obd.read(PIDS["rpm"]), obd.trouble_codes("stored"), obd.vin()

:func:`serve_can` makes a :class:`nervus.obd_sim.SimulatedEcu` answer on a CAN bus, so a tool (or this
client, on the ``virtual`` interface) can be tested without a car. Needs ``python-can`` (``uv sync --extra can``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from nervus.core.errors import install_hint
from nervus.obd import ObdError, Pid, decode_dtc, supported_from_bitmask, vin_from_bytes
from nervus.obd_sim import SimulatedEcu

FUNCTIONAL_ID = 0x7DF
ECU_REQUEST_ID = 0x7E0
ECU_RESPONSE_ID = 0x7E8
PAD = 0xCC


def _bus(interface: str, channel: str | None, bitrate: int) -> Any:
    try:
        import can
    except ImportError as exc:
        raise ObdError(f"OBD over CAN needs python-can: {install_hint('can')}") from exc
    kwargs: dict[str, Any] = {"interface": interface}
    if channel is not None:
        kwargs["channel"] = channel
    if interface not in ("virtual",):
        kwargs["bitrate"] = bitrate
    return can.Bus(**kwargs)


def _message(arbitration_id: int, data: bytes) -> Any:
    import can

    return can.Message(arbitration_id=arbitration_id, data=data.ljust(8, bytes([PAD])), is_extended_id=False)


def segment(payload: bytes) -> list[bytes]:
    """ISO-TP frames (without padding) for ``payload``: one single frame, or a first frame and consecutive
    frames."""
    if len(payload) <= 7:
        return [bytes([len(payload)]) + payload]
    n = len(payload)
    frames = [bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6]]
    seq = 1
    for k in range(6, n, 7):
        frames.append(bytes([0x20 | (seq & 0xF)]) + payload[k : k + 7])
        seq += 1
    return frames


def _receive(bus: Any, rx_id: int, fc_id: int, timeout: float) -> bytes | None:
    """One ISO-TP message from ``rx_id``; sends the flow control frame to ``fc_id`` for long messages."""
    deadline = time.monotonic() + timeout
    data = b""
    length = 0
    expected = 1
    while time.monotonic() < deadline:
        msg = bus.recv(timeout=max(0.0, deadline - time.monotonic()))
        if msg is None or msg.arbitration_id != rx_id:
            continue
        frame = bytes(msg.data)
        kind = frame[0] >> 4
        if kind == 0:  # single frame
            return frame[1 : 1 + (frame[0] & 0xF)]
        if kind == 1:  # first frame: ask for the rest, all at once, without delay
            length = ((frame[0] & 0xF) << 8) | frame[1]
            data = frame[2:8]
            expected = 1
            bus.send(_message(fc_id, bytes([0x30, 0x00, 0x00])))
        elif kind == 2 and length:
            if frame[0] & 0xF != expected & 0xF:
                raise ObdError("ISO-TP: a consecutive frame was lost")
            data += frame[1:8]
            expected += 1
            if len(data) >= length:
                return data[:length]
    return None


class CanObd:
    """OBD-II client over CAN (ISO 15765-4, 11-bit, 500 kbit/s): the same reads as :class:`nervus.obd.Elm327`."""

    def __init__(
        self,
        bus: Any = None,
        interface: str = "socketcan",
        channel: str | None = None,
        bitrate: int = 500000,
        timeout: float = 1.0,
    ) -> None:
        self.bus = bus if bus is not None else _bus(interface, channel, bitrate)
        self.timeout = timeout
        self.supported: set[int] = set()

    def request(self, service: bytes) -> bytes | None:
        """Send one OBD request (e.g. ``b"\\x01\\x0c"``) and return the engine ECU's reply bytes, or ``None``."""
        self.bus.send(_message(FUNCTIONAL_ID, segment(bytes(service))[0]))
        reply = _receive(self.bus, ECU_RESPONSE_ID, ECU_REQUEST_ID, self.timeout)
        if reply is None or reply[0] != service[0] + 0x40:
            return None
        return reply

    def initialize(self) -> set[int]:
        self.supported = set()
        base = 0
        while True:
            reply = self.request(bytes([0x01, base]))
            if reply is None or len(reply) < 6:
                break
            found = supported_from_bitmask(base, reply[2:6])
            self.supported |= found
            if base + 0x20 not in found:
                break
            base += 0x20
        if not self.supported:
            raise ObdError("no answer on the CAN bus: is the ignition on and the bitrate right?")
        return self.supported

    def read(self, pid: Pid) -> float | None:
        reply = self.request(bytes([0x01, pid.pid]))
        if reply is None or len(reply) < 2 + pid.n_bytes:
            return None
        return float(pid.decode(reply[2 : 2 + pid.n_bytes]))

    def trouble_codes(self, kind: str = "stored") -> list[str]:
        mode = {"stored": 3, "pending": 7, "permanent": 0x0A}[kind]
        reply = self.request(bytes([mode]))
        if reply is None or len(reply) < 2:
            return []
        body = reply[2 : 2 + 2 * reply[1]]
        return [decode_dtc(body[k : k + 2]) for k in range(0, len(body) - 1, 2) if body[k : k + 2] != b"\x00\x00"]

    def freeze_frame(self, pid: Pid, frame: int = 0) -> float | None:
        reply = self.request(bytes([0x02, pid.pid, frame]))
        if reply is None or len(reply) < 3 + pid.n_bytes:
            return None
        return float(pid.decode(reply[3 : 3 + pid.n_bytes]))

    def vin(self) -> str | None:
        reply = self.request(bytes([0x09, 0x02]))
        if reply is None:
            return None
        return vin_from_bytes(reply[3:])

    def close(self) -> None:
        self.bus.shutdown()


def serve_can(
    sim: SimulatedEcu, interface: str = "virtual", channel: str | None = None, bitrate: int = 500000, bus: Any = None
) -> Callable[[], None]:
    """Answer OBD requests from 0x7DF / 0x7E0 on a CAN bus with ``sim``, from 0x7E8, in a background thread.
    Returns a function that stops serving."""
    own = bus is None
    bus = bus if bus is not None else _bus(interface, channel, bitrate)
    stop = threading.Event()

    def send(reply: bytes) -> None:
        frames = segment(reply)
        bus.send(_message(ECU_RESPONSE_ID, frames[0]))
        if len(frames) == 1:
            return
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:  # wait for the tester's flow control
            msg = bus.recv(timeout=0.1)
            if msg is not None and msg.arbitration_id == ECU_REQUEST_ID and msg.data[0] >> 4 == 3:
                st_min = msg.data[2] / 1000 if msg.data[2] <= 0x7F else 0.0
                for frame in frames[1:]:
                    bus.send(_message(ECU_RESPONSE_ID, frame))
                    if st_min:
                        time.sleep(st_min)
                return

    def loop() -> None:
        while not stop.is_set():
            msg = bus.recv(timeout=0.05)
            if msg is None or msg.arbitration_id not in (FUNCTIONAL_ID, ECU_REQUEST_ID):
                continue
            data = bytes(msg.data)
            if data[0] >> 4 != 0:
                continue  # only single-frame requests (all OBD requests fit in one frame)
            reply = sim.request(data[1 : 1 + (data[0] & 0xF)])
            if reply is not None:
                send(reply)

    thread = threading.Thread(target=loop, name="obd-can", daemon=True)
    thread.start()

    def stop_serving() -> None:
        stop.set()
        thread.join(timeout=1.0)
        if own:
            bus.shutdown()

    return stop_serving
