"""XCP on CAN: protocol, A2L, and the XCP Slave block with a master on a (virtual) CAN bus."""

import struct
import threading
from pathlib import Path

import numpy as np
import pytest

from nervus import Diagram, simulate
from nervus.blocks import CanBusSetup, Gain, Outport, Product, Sine, XcpSlave
from nervus.core import Variable
from nervus.xcp import DirectTransport, Memory, Region, XcpError, XcpMaster, XcpServer, read_a2l


def _server() -> tuple[XcpServer, bytearray]:
    ram = bytearray(struct.pack("<dd", 1.5, -2.0))
    rom = struct.pack("<d", 42.0)

    def write(off: int, data: bytes) -> None:
        ram[off : off + len(data)] = data

    return XcpServer(Memory([Region(0x1000, 16, lambda: bytes(ram), write), Region(0x2000, 8, lambda: rom)])), ram


def test_protocol() -> None:
    server, ram = _server()
    m = XcpMaster(DirectTransport(server))
    with pytest.raises(XcpError, match="not connected"):
        m.upload(0x1000, 8)
    info = m.connect()
    assert info["max_cto"] == 8 and info["comm_mode"] & 1 == 0  # Intel byte order
    assert struct.unpack("<d", m.upload(0x1008, 8))[0] == -2.0
    m.download(0x1000, struct.pack("<d", 3.25))
    assert struct.unpack("<d", ram[:8])[0] == 3.25
    assert (
        server.handle(bytes([0xF4, 4, 0, 0, *0x2000.to_bytes(4, "little")]))[:5]
        == b"\xff" + struct.pack("<d", 42.0)[:4]
    )
    with pytest.raises(XcpError, match="access denied"):
        m.download(0x2000, b"\0" * 8)  # a measurement cannot be written
    with pytest.raises(XcpError, match="out of range"):
        m.upload(0x3000, 4)
    with pytest.raises(XcpError, match="unknown command"):
        m._cmd(bytes([0xC0]))
    m.disconnect()


def _model() -> Diagram:
    d = Diagram("xcp")
    d.workspace.add(Variable("Kp", 2.0, "Controller gain", "-", 0.0, 10.0))
    src = d.add(Sine(amplitude=1.0, frequency=2.0), name="sensor")
    slave = d.add(XcpSlave(measurements="sensor, output", calibrations="Kp"), name="xcp")
    g = d.add(Gain(gain=1.0), name="times_kp")
    prod = d.add(Product(ops="**"), name="out")
    d.connect(src, (prod, 0))
    d.connect((slave, 0), (prod, 1))
    d.chain(prod, g)
    d.connect(src, (slave, 0))
    d.connect(g, (slave, 1))
    d.connect(g, d.add(Outport(port=1), name="y"))
    d.connect((slave, 0), d.add(Outport(port=2), name="kp"))
    return d


def test_a2l_roundtrip(tmp_path: Path) -> None:
    d = _model()
    simulate(d, 0.0)
    objs = read_a2l(d.block("xcp").write_a2l(tmp_path / "slave.a2l"))
    assert objs["Kp"].address == 0x10000 and objs["Kp"].kind == "CHARACTERISTIC"
    assert objs["output"].address == 0x20008 and objs["output"].datatype == "FLOAT64_IEEE"


def test_codegen_a2l_is_readable(tmp_path: Path) -> None:
    from nervus.blocks import Constant
    from nervus.codegen import generate_c

    d = Diagram("m")
    d.workspace.add(Variable("k", 3.0))
    from nervus.blocks import UnitDelay

    d.chain(d.add(Constant(value="k")), d.add(Gain(gain="k")), d.add(UnitDelay(sample_time=0.01)),
            d.add(Outport(port=1), name="y"))  # fmt: skip
    code = generate_c(d)
    a2l = next(n for n in code.files if n.endswith(".a2l"))
    (tmp_path / a2l).write_text(code.files[a2l])
    objs = read_a2l(tmp_path / a2l)
    assert any(o.kind == "CHARACTERISTIC" for o in objs.values())
    assert any(o.kind == "MEASUREMENT" for o in objs.values())


def test_live_calibration_over_virtual_can(tmp_path: Path) -> None:
    can = pytest.importorskip("can")
    from nervus.xcp import CanTransport

    d = _model()
    d.add(CanBusSetup(interface="virtual", channel="nervus_xcp_test"), name="can")
    d.settings.pacing = 1.0  # real time, so the tool can talk to the running model
    d.settings.solver = "rk4"
    d.settings.step = 0.001
    a2l = d.block("xcp")
    simulate(d, 0.0)
    path = a2l.write_a2l(tmp_path / "slave.a2l")
    tool_bus = can.Bus(interface="virtual", channel="nervus_xcp_test")
    seen: dict[str, object] = {}

    def tool() -> None:
        m = XcpMaster(CanTransport(tool_bus), a2l=path, timeout=0.5)
        threading.Event().wait(0.2)
        seen["connect"] = m.connect()
        seen["kp_before"] = m.read("Kp")
        m.write("Kp", 5.0)
        seen["kp_after"] = m.read("Kp")
        seen["sensor"] = m.read("sensor")

    th = threading.Thread(target=tool)
    th.start()
    try:
        r = simulate(d, 1.5)
    finally:
        th.join(5)
        tool_bus.shutdown()
        from nervus.canbus import bus

        bus("can0").disconnect_hardware()
    assert seen["kp_before"] == 2.0 and seen["kp_after"] == 5.0
    assert -1.0 <= float(seen["sensor"]) <= 1.0  # type: ignore[arg-type]
    kp = np.ravel(r["kp"])
    assert kp[0] == 2.0 and kp[-1] == 5.0  # the model ran with the new value from then on
    y, s = np.ravel(r["y"]), np.sin(2.0 * r.t)
    late = r.t > r.t[np.argmax(kp == 5.0)] + 0.01
    assert np.allclose(y[late], 5.0 * s[late], atol=1e-9)


def test_master_uses_the_byte_order_of_a_motorola_slave() -> None:
    # A big-endian ECU (BYTE_ORDER = 1 in CONNECT): the SET_MTA address and the values are Motorola.
    ram = bytearray(struct.pack(">dh", 1.5, -300))

    def write(off: int, data: bytes) -> None:
        ram[off : off + len(data)] = data

    class Motorola(XcpServer):
        def handle(self, req: bytes) -> bytes:
            if req[:1] == bytes([0xFF]):
                self.connected = True
                return bytes([0xFF, 0x01, 0x01, 8, 0x00, 0x08, 0x01, 0x01])
            if req[:1] == bytes([0xF6]):
                self.mta = int.from_bytes(req[4:8], "big")
                return bytes([0xFF])
            return super().handle(req)

    from nervus.xcp import A2lObject

    objects = {"x": A2lObject("x", 0x1000, "FLOAT64_IEEE"), "n": A2lObject("n", 0x1008, "SWORD")}
    m = XcpMaster(DirectTransport(Motorola(Memory([Region(0x1000, 10, lambda: bytes(ram), write)]))), objects)
    assert m.connect()["max_dto"] == 8
    assert m.read("x") == 1.5 and m.read("n") == -300
    m.write("n", 1234)
    assert struct.unpack(">h", ram[8:10])[0] == 1234
