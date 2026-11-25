import numpy as np
import pytest

from nervus.core import Signal
from nervus.obd import (
    PIDS,
    Elm327,
    ObdError,
    _main,
    decode_dtc,
    describe_dtc,
    parse_dtcs,
    parse_response,
    parse_vin,
    record,
    supported_from_bitmask,
)


class FakeCar:
    """Behaves like an ELM327 connected to a car: answers AT commands and mode 01 requests."""

    def __init__(self) -> None:
        self.buffer = b""
        self.t = 0.0

    def write(self, data: bytes) -> int:
        cmd = data.decode().strip().upper()
        if cmd.startswith("AT"):
            reply = "OK"
        elif cmd == "0100":
            reply = "SEARCHING...\r41 00 BE 3F A8 13"  # supports 0C, 0D, 05, 11, ... and 0x20 range
        elif cmd == "0120":
            reply = "41 20 80 00 00 00"
        elif cmd == "010C":
            rpm = 800 + 100 * self.t
            raw = int(rpm * 4)
            reply = f"41 0C {raw >> 8:02X} {raw & 0xFF:02X}"
        elif cmd == "010D":
            reply = "41 0D 32"
        elif cmd == "0105":
            reply = "41 05 7B"  # 123 - 40 = 83 degC
        elif cmd == "0111":
            reply = "NO DATA"
        else:
            reply = "NO DATA"
        self.buffer += (reply + "\r\r>").encode()
        return len(data)

    def read_until(self, expected: bytes) -> bytes:
        k = self.buffer.index(expected) + len(expected)
        out, self.buffer = self.buffer[:k], self.buffer[k:]
        return out


def test_decode_standard_pids() -> None:
    assert PIDS["rpm"].decode(bytes([0x1A, 0xF8])) == 1726.0
    assert PIDS["coolant_temp"].decode(bytes([0x7B])) == 83
    assert PIDS["throttle"].decode(bytes([0xFF])) == pytest.approx(100.0)
    assert PIDS["stft_b1"].decode(bytes([0x80])) == 0.0
    assert PIDS["maf"].decode(bytes([0x01, 0xF4])) == 5.0
    assert PIDS["lambda_cmd"].decode(bytes([0x80, 0x00])) == 1.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("41 0C 1A F8", b"\x1a\xf8"),
        ("410C1AF8", b"\x1a\xf8"),
        ("010C\rSEARCHING...\r41 0C 1A F8\r", b"\x1a\xf8"),
        ("7E8 04 41 0C 1A F8", b"\x1a\xf8"),
        ("NO DATA", None),
        ("CAN ERROR", None),
    ],
)
def test_parse_response(text: str, expected: bytes | None) -> None:
    assert parse_response(text, 1, 0x0C) == expected


def test_supported_bitmask() -> None:
    found = supported_from_bitmask(0, bytes.fromhex("BE3FA813"))
    assert {0x01, 0x05, 0x0C, 0x0D, 0x20} <= found


def test_elm_session_and_recording(tmp_path) -> None:  # type: ignore[no-untyped-def]
    car = FakeCar()
    elm = Elm327(car)
    supported = elm.initialize()
    assert 0x0C in supported and 0x21 in supported
    clock = iter(np.arange(0, 10, 0.5))

    def fake_clock() -> float:
        car.t = next(clock)
        return car.t

    res = record(elm, ["rpm", "speed", "coolant_temp"], duration=3.0, clock=fake_clock, sleep=lambda s: None)
    assert res["speed"][0] == 50.0 and res["coolant_temp"][0] == 83.0
    assert res["rpm"][-1] > res["rpm"][0]
    path = res.save(tmp_path / "drive")
    replay = Signal(res.t, res["rpm"], "linear")  # recorded data as a test input
    assert replay(res.t[1])[0] == pytest.approx(res["rpm"][1])
    assert path.exists()


def test_unsupported_and_no_answer() -> None:
    elm = Elm327(FakeCar())
    elm.initialize()
    assert elm.read(PIDS["throttle"]) is None  # NO DATA
    with pytest.raises(ObdError, match="does not report"):
        record(elm, ["oil_temp"], 1.0)


def test_cli_lists_pids(capsys) -> None:  # type: ignore[no-untyped-def]
    assert _main(["pids"]) == 0
    assert "rpm" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("43 01 33 00 00 00 00", ["P0133"]),  # non-CAN, one line
        ("43 01 33 04 20 C1 23\r43 00 00 00 00 00 00", ["P0133", "P0420", "U0123"]),  # non-CAN, two lines
        ("43 02 01 33 04 20", ["P0133", "P0420"]),  # CAN with count
        ("43 00", []),  # CAN, no codes
        ("NO DATA", []),
        ("47 01 01 71", ["P0171"]),
    ],
)
def test_parse_dtcs(text: str, codes: list[str]) -> None:
    mode = 7 if text.startswith("47") else 3
    assert parse_dtcs(text, mode) == codes


def test_decode_and_describe_dtc() -> None:
    assert decode_dtc(bytes([0x01, 0x33])) == "P0133"
    assert decode_dtc(bytes([0x41, 0x23])) == "C0123"
    assert decode_dtc(bytes([0x92, 0x34])) == "B1234"
    assert describe_dtc("P0420").startswith("Catalyst")
    assert describe_dtc("P1234") == "manufacturer-specific powertrain code"
    assert describe_dtc("U0100") == "generic (SAE) network code"


def test_parse_vin_can_multiframe() -> None:
    vin = "WVWZZZ1KZAW000001"
    data = bytes([0x49, 0x02, 0x01]) + vin.encode()
    chunks = [data[:6], data[6:13], data[13:]]
    text = "014\r" + "\r".join(f"{k}: " + " ".join(f"{b:02X}" for b in c) for k, c in enumerate(chunks))
    assert parse_vin(text) == vin
    assert parse_vin("NO DATA") is None


def test_elm_trouble_codes_and_freeze_frame() -> None:
    class Car(FakeCar):
        def write(self, data: bytes) -> int:
            cmd = data.decode().strip().upper()
            replies = {"03": "43 02 01 33 03 00", "07": "47 00", "0A": "4A 00", "020C00": "42 0C 00 1A F8"}
            if cmd in replies:
                self.buffer += (replies[cmd] + "\r\r>").encode()
                return len(data)
            return super().write(data)

    elm = Elm327(Car())
    assert elm.trouble_codes() == ["P0133", "P0300"]
    assert elm.trouble_codes("pending") == []
    assert elm.freeze_frame(PIDS["rpm"]) == 1726.0


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        # CAN multi-frame: the last frame is padded (00 or AA); the byte count says where the data ends.
        ("00A\r0: 43 04 01 33 04 20\r1: 01 01 01 02 00 00 00", ["P0133", "P0420", "P0101", "P0102"]),
        ("00A\r0: 43 04 01 33 04 20\r1: 01 01 01 02 AA AA AA", ["P0133", "P0420", "P0101", "P0102"]),
        # Two ECUs (engine and transmission) answering the functional request.
        ("43 01 01 33\r43 01 07 00", ["P0133", "P0700"]),
        ("43 02 01 33 04 20\r43 00", ["P0133", "P0420"]),
    ],
)
def test_parse_dtcs_multiframe_and_several_ecus(text: str, codes: list[str]) -> None:
    assert parse_dtcs(text, 3) == codes


def test_parse_vin_non_can() -> None:
    # ISO 9141 / KWP2000: five lines "49 02 nn" + 4 bytes, the first one starting with three fill bytes.
    vin = b"1G1JC5444R7252367"
    data = b"\x00\x00\x00" + vin
    lines = [bytes([0x49, 0x02, k + 1]) + data[4 * k : 4 * k + 4] for k in range(5)]
    assert parse_vin("\r".join(" ".join(f"{b:02X}" for b in x) for x in lines)) == vin.decode()


def test_parse_vin_drops_padding() -> None:
    vin = "WVWZZZ1KZAW000001"
    data = bytes([0x49, 0x02, 0x01]) + vin.encode() + b"\x55\x55"  # 0x55 ('U') padding in the last frame
    chunks = [data[:6], data[6:13], data[13:]]
    text = "014\r" + "\r".join(f"{k}: " + " ".join(f"{b:02X}" for b in c) for k, c in enumerate(chunks))
    assert parse_vin(text) == vin


def test_describe_dtc_generic_ranges() -> None:
    assert describe_dtc("P2101") == "generic (SAE) powertrain code"
    assert describe_dtc("P3400") == "generic (SAE) powertrain code"
    assert describe_dtc("P3000") == "manufacturer-specific powertrain code"
    assert describe_dtc("U2100") == "manufacturer-specific network code"
    assert describe_dtc("B2123") == "manufacturer-specific body code"
    assert describe_dtc("C0035") == "generic (SAE) chassis code"
