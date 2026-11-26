"""Measurement and calibration: A2L files of any ECU, XCP (seed and key, pages, DAQ, Ethernet), the virtual ECU."""

from __future__ import annotations

import struct
import time

import pytest

from nervus.a2l import A2lError, parse_a2l
from nervus.xcp import (
    REFERENCE_PAGE,
    WORKING_PAGE,
    DaqSignal,
    DirectTransport,
    Memory,
    Region,
    UdpTransport,
    XcpError,
    XcpMaster,
    XcpServer,
    serve_udp,
)  # fmt: skip

A2L = """
ASAP2_VERSION 1 71
/begin PROJECT demo ""
/begin MODULE ECU "a test ECU"
  /begin MOD_COMMON "" BYTE_ORDER MSB_FIRST ALIGNMENT_FLOAT32_IEEE 4 /end MOD_COMMON
  /begin MOD_PAR "" /begin MEMORY_SEGMENT cal "" DATA RAM INTERN 0x1000 0x100 -1 -1 -1 -1 -1 /end MEMORY_SEGMENT
  /end MOD_PAR
  /* conversions */
  /begin COMPU_METHOD rpm "engine speed" LINEAR "%6.0" "rpm" COEFFS_LINEAR 0.25 0 /end COMPU_METHOD
  /begin COMPU_METHOD temp "" RAT_FUNC "%5.1" "degC" COEFFS 0 1 40 0 0 1 /end COMPU_METHOD
  /begin COMPU_METHOD gear "" TAB_VERB "%d" "" COMPU_TAB_REF gears /end COMPU_METHOD
  /begin COMPU_VTAB gears "" TAB_VERB 3 0 "P" 1 "R" 2 "D" /end COMPU_VTAB
  /begin COMPU_METHOD pct "" TAB_INTP "%4.1" "%" COMPU_TAB_REF pct_tab /end COMPU_METHOD
  /begin COMPU_TAB pct_tab "" TAB_INTP 2 0 0 255 100 /end COMPU_TAB
  /begin RECORD_LAYOUT RL_U16 FNC_VALUES 1 UWORD ROW_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_F32 FNC_VALUES 1 FLOAT32_IEEE ROW_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_CURVE NO_AXIS_PTS_X 1 UBYTE AXIS_PTS_X 2 FLOAT32_IEEE INDEX_INCR DIRECT
    FNC_VALUES 3 SWORD ROW_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_AXIS NO_AXIS_PTS_X 1 UWORD AXIS_PTS_X 2 UWORD INDEX_INCR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_MAP FNC_VALUES 1 UBYTE COLUMN_DIR DIRECT /end RECORD_LAYOUT
  /begin MEASUREMENT n_eng "engine speed" UWORD rpm 0 0 0 16000 ECU_ADDRESS 0x2000 /end MEASUREMENT
  /begin MEASUREMENT t_cool "coolant" SBYTE temp 0 0 -40 150 ECU_ADDRESS 0x2002 /end MEASUREMENT
  /begin MEASUREMENT gear_sel "" UBYTE gear 0 0 0 2 ECU_ADDRESS 0x2003 /end MEASUREMENT
  /begin MEASUREMENT flags "" UBYTE NO_COMPU_METHOD 0 0 0 1 ECU_ADDRESS 0x2004 BIT_MASK 0x0C /end MEASUREMENT
  /begin CHARACTERISTIC idle_rpm "" VALUE 0x1000 RL_U16 0 rpm 500 1200 /end CHARACTERISTIC
  /begin CHARACTERISTIC gains "" VAL_BLK 0x1004 RL_F32 0 NO_COMPU_METHOD -10 10 NUMBER 3 /end CHARACTERISTIC
  /begin CHARACTERISTIC fan_curve "" CURVE 0x1010 RL_CURVE 0 NO_COMPU_METHOD -1000 1000
    /begin AXIS_DESCR STD_AXIS t_cool NO_COMPU_METHOD 4 -40 150 /end AXIS_DESCR /end CHARACTERISTIC
  /begin AXIS_PTS rpm_axis "" 0x1040 n_eng RL_AXIS 0 rpm 3 0 16000 /end AXIS_PTS
  /begin CHARACTERISTIC spark "" MAP 0x1050 RL_MAP 0 pct 0 100
    /begin AXIS_DESCR COM_AXIS n_eng rpm 3 0 16000 AXIS_PTS_REF rpm_axis /end AXIS_DESCR
    /begin AXIS_DESCR FIX_AXIS t_cool NO_COMPU_METHOD 2 -40 150 FIX_AXIS_PAR_DIST 0 50 2 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin IF_DATA XCP /begin XCP_ON_CAN 0x0100 CAN_ID_MASTER 0x7E0 CAN_ID_SLAVE 0x7E8 BAUDRATE 500000
    /end XCP_ON_CAN /begin XCP_ON_UDP_IP 0x0100 5555 ADDRESS "192.168.1.10" /end XCP_ON_UDP_IP /end IF_DATA
/end MODULE
/end PROJECT
"""


def ecu_memory() -> bytearray:
    mem = bytearray(0x3000)
    struct.pack_into(">H", mem, 0x1000, 3200)  # idle_rpm raw: 800 rpm
    struct.pack_into(">3f", mem, 0x1004, 1.5, -2.0, 0.25)
    # fan_curve: 3 of up to 4 points; axis floats at 0x1014 (aligned to 4), the values (SWORD) right after them
    struct.pack_into(">B", mem, 0x1010, 3)
    struct.pack_into(">3f", mem, 0x1014, -10.0, 60.0, 100.0)
    struct.pack_into(">3h", mem, 0x1020, 0, 50, -100)
    struct.pack_into(">H3H", mem, 0x1040, 3, 4000, 8000, 12000)  # rpm_axis: 1000, 2000, 3000 rpm
    mem[0x1050:0x1056] = bytes([0, 51, 102, 153, 204, 255])  # spark[x][y], y fastest (COLUMN_DIR)
    struct.pack_into(">Hbbb", mem, 0x2000, 3000, 50, 2, 0b1010)
    return mem


def test_a2l_of_any_ecu() -> None:
    a2l = parse_a2l(A2L)
    mem = ecu_memory()
    assert a2l.byte_order == "MSB_FIRST" and a2l.xcp_can.request_id == 0x7E0 and a2l.xcp_can.baudrate == 500000
    assert a2l.xcp_ip[0].port == 5555 and a2l.xcp_ip[0].host == "192.168.1.10"
    m = a2l.measurements
    assert m["n_eng"].decode(bytes(mem[0x2000:]), a2l) == [750.0] and m["n_eng"].unit == "rpm"
    assert m["t_cool"].decode(bytes(mem[0x2002:]), a2l) == [10.0]  # RAT_FUNC: raw = phys + 40
    assert a2l.compu(m["gear_sel"].conversion).label(2) == "D"
    assert m["flags"].decode(bytes(mem[0x2004:]), a2l) == [2.0]  # bits 2..3 of 0b1010
    c = a2l.characteristics
    idle = c["idle_rpm"]
    assert idle.decode(bytes(mem[0x1000:0x1002]), a2l)["values"] == [800.0]
    raw = idle.encode([900.0], a2l, bytes(mem[0x1000:0x1002]))
    assert struct.unpack(">H", raw)[0] == 3600
    assert c["gains"].size(a2l) == 12 and c["gains"].decode(bytes(mem[0x1004:0x1010]), a2l)["values"][1] == -2.0
    curve = c["fan_curve"]
    rec = bytes(mem[0x1010 : 0x1010 + curve.size(a2l)])
    dec = curve.decode(rec, a2l)
    assert dec["axes"] == [[-10.0, 60.0, 100.0]] and dec["values"] == [0.0, 50.0, -100.0]
    new = curve.encode([1.0, 2.0, 3.0], a2l, rec)
    assert curve.decode(new, a2l)["values"] == [1.0, 2.0, 3.0] and new[:16] == rec[:16]  # the axis stays
    static = parse_a2l(
        A2L.replace("FNC_VALUES 3 SWORD ROW_DIR DIRECT", "FNC_VALUES 3 SWORD ROW_DIR DIRECT STATIC_RECORD_LAYOUT")
    )
    srec = bytearray(rec.ljust(32, b"\0"))
    struct.pack_into(">3h", srec, 0x14, 7, 8, 9)  # values after the room for 4 axis points
    sc = static.characteristics["fan_curve"]
    assert sc.decode(bytes(srec), static)["values"] == [7.0, 8.0, 9.0]
    assert sc.decode(sc.encode([4.0, 5.0, 6.0], static, bytes(srec)), static)["values"] == [4.0, 5.0, 6.0]
    axis = a2l.axis_pts["rpm_axis"].decode(bytes(mem[0x1040:0x1048]), a2l)
    assert axis == [1000.0, 2000.0, 3000.0]
    spark = c["spark"]
    dec = spark.decode(bytes(mem[0x1050:0x1056]), a2l, {"rpm_axis": axis})
    assert dec["axes"] == [[1000.0, 2000.0, 3000.0], [0.0, 50.0]]
    assert dec["values"][0] == pytest.approx([0.0, 20.0]) and dec["values"][2] == pytest.approx([80.0, 100.0])
    assert a2l.writable(0x1000, 2) and not a2l.writable(0x2000, 2)
    with pytest.raises(A2lError, match="no MODULE"):
        parse_a2l("/begin PROJECT x /end PROJECT")


def protected_server() -> tuple[XcpServer, bytearray]:
    ram = bytearray(struct.pack("<ff", 1.0, 2.0))

    def write(off: int, data: bytes) -> None:
        ram[off : off + len(data)] = data

    mem = Memory([Region(0x100, 8, lambda: bytes(ram), write)])
    return XcpServer(
        mem, seed_key=lambda seed: bytes(b ^ 0x5A for b in seed), cal_regions=[(0x100, 8)], max_cto=64
    ), ram


def test_seed_key_pages_and_daq() -> None:
    server, ram = protected_server()
    transport = DirectTransport(server)
    m = XcpMaster(transport)
    assert m.connect()["max_cto"] == 64
    with pytest.raises(XcpError, match="locked"):
        m.download(0x100, struct.pack("<f", 5.0))
    with pytest.raises(XcpError, match="locked"):
        m.unlock(lambda seed: b"\0\0\0\0")  # wrong key
    m.connect()
    m.unlock(lambda seed: bytes(b ^ 0x5A for b in seed))
    m.download(0x100, struct.pack("<f", 5.0))
    assert struct.unpack_from("<f", ram)[0] == 5.0
    m.set_page(REFERENCE_PAGE)  # the safe values come back, and cannot be changed
    assert m.page() == REFERENCE_PAGE and struct.unpack_from("<f", ram)[0] == 1.0
    with pytest.raises(XcpError, match="write protected"):
        m.download(0x100, struct.pack("<f", 7.0))
    m.set_page(WORKING_PAGE)
    assert struct.unpack_from("<f", ram)[0] == 5.0
    dec = lambda raw: struct.unpack("<f", raw)[0]  # noqa: E731
    m.start_daq([DaqSignal("a", 0x100, 4, dec), DaqSignal("b", 0x104, 4, dec)], event=0, prescaler=2)
    for k in range(6):
        ram[4:8] = struct.pack("<f", float(k))
        transport.event(0, timestamp=k * 0.01)
    got = m.read_daq()
    assert [t for t, _v in got["b"]] == pytest.approx([0.0, 0.02, 0.04]) and [v for _t, v in got["b"]] == [
        0.0,
        2.0,
        4.0,
    ]
    m.stop_daq()
    transport.event(0)
    assert m.read_daq() == {}


def test_xcp_on_udp() -> None:
    server, ram = protected_server()
    server.seed_key, server.locked = None, 0
    thread = serve_udp(server, 0)
    try:
        m = XcpMaster(UdpTransport("127.0.0.1", thread.port))  # type: ignore[attr-defined]
        m.connect()
        assert struct.unpack("<f", m.upload(0x104, 4))[0] == 2.0
        m.download(0x104, struct.pack("<f", 3.0))
        assert struct.unpack_from("<f", ram, 4)[0] == 3.0
    finally:
        thread.stop.set()  # type: ignore[attr-defined]


@pytest.mark.skipif(__import__("nervus.codegen.sil", fromlist=["x"]).find_compiler() is None, reason="needs cc")
def test_virtual_ecu() -> None:
    from pathlib import Path

    from nervus import load
    from nervus.vecu import VirtualEcu

    d = load(Path(__file__).parent.parent / "examples" / "throttle_ecu.nervus")
    ecu = VirtualEcu(d.block("ECU"))
    assert ecu.a2l.characteristics["ECU_P.Kp"].address != 0  # real addresses from the library
    ecu.start(udp_port=0, inputs=lambda t: {"target_deg": 30.0, "tps_deg": 20.0}, speed=4.0)
    try:
        m = XcpMaster(UdpTransport("127.0.0.1", ecu.udp_port), a2l=ecu.a2l_path)
        m.connect()
        m.write("ECU_P.Kp", 2.5)
        assert m.read("ECU_P.Kp") == 2.5
        m.start_daq(["ECU_B.error"])
        deadline = time.monotonic() + 2.0
        got: list[tuple[float, float]] = []
        while len(got) < 5 and time.monotonic() < deadline:
            got += m.read_daq(0.05).get("ECU_B.error", [])
        assert len(got) >= 5 and got[-1][1] == pytest.approx(10.0)
        with pytest.raises(XcpError, match="out of range"):
            m.upload(0x10, 4)  # only memory in the A2L file
        m.stop_daq()
    finally:
        ecu.stop()
