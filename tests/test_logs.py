"""Vehicle logs: CSV files of OBD-II scanner apps and loggers, and raw CAN logs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nervus.core.errors import NervusError
from nervus.logs import log_channels, read_log
from nervus.sim import SimResult

TORQUE = """\
GPS Time, Device Time, Longitude, Latitude,GPS Speed (Meters/second), Engine RPM(rpm),Speed (OBD)(km/h),\
Engine Coolant Temperature(°C)
Wed Nov 18 14:32:01 GMT+01:00 2020,18-Nov-2020 14:32:01.000,18.0686,59.3293,0.0,800,0,45
Wed Nov 18 14:32:02 GMT+01:00 2020,18-Nov-2020 14:32:01.500,18.0686,59.3293,0.0,-,0,45
Wed Nov 18 14:32:02 GMT+01:00 2020,18-Nov-2020 14:32:02.000,18.0687,59.3294,1.0,1200,4,46
GPS Time, Device Time, Longitude, Latitude,GPS Speed (Meters/second), Engine RPM(rpm),Speed (OBD)(km/h),\
Engine Coolant Temperature(°C)
Wed Nov 18 14:32:03 GMT+01:00 2020,18-Nov-2020 14:32:03.000,18.0688,59.3295,2.0,1600,8,46
"""

CAR_SCANNER = """\
"SECONDS";"PID";"VALUE";"UNITS"
"10.0";"Engine RPM";"800";"rpm"
"10.2";"Vehicle speed";"0";"km/h"
"10.4";"Engine RPM";"900";"rpm"
"10.6";"Vehicle speed";"2";"km/h"
"10.8";"Engine RPM";"1000";"rpm"
"""

EUROPEAN = """\
OBD log exported 2024-03-01
Vehicle: Volvo FH
Time (ms);Engine speed [rpm];Fuel rate [l/h];Status
0;600,5;2,25;OK
100;610,5;2,5;OK
200;620,5;2,75;OK
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_torque_wide_csv(tmp_path: Path) -> None:
    p = write(tmp_path, "torque.csv", TORQUE)
    chans = log_channels(p)
    assert chans["Engine RPM"] == "rpm" and chans["Speed (OBD)"] == "km/h"
    assert chans["Engine Coolant Temperature"] == "°C" and "GPS Time" not in chans and "Device Time" not in chans
    rec = read_log(p)
    assert rec.t[0] == 0.0 and rec.t[-1] == pytest.approx(2.0)  # from the device time, the repeated header skipped
    assert rec["Engine RPM"][1] == pytest.approx(1000.0)  # the '-' cell is interpolated
    assert rec.unit("Speed (OBD)") == "km/h"


def test_car_scanner_long_csv(tmp_path: Path) -> None:
    rec = read_log(write(tmp_path, "carscanner.csv", CAR_SCANNER))
    assert set(rec.names()) == {"Engine RPM", "Vehicle speed"}
    assert np.allclose(rec.t, [10.0, 10.4, 10.8])  # the value with the most samples
    assert np.allclose(rec["Vehicle speed"], [0.0, 1.0, 2.0])
    assert rec.unit("Engine RPM") == "rpm"
    assert len(read_log(write(tmp_path, "c.csv", CAR_SCANNER), raster=0.1).t) == 9


def test_preamble_semicolons_decimal_comma_and_milliseconds(tmp_path: Path) -> None:
    rec = read_log(write(tmp_path, "truck.csv", EUROPEAN))
    assert rec.names() == ["Engine speed", "Fuel rate"]  # the text column is left out
    assert np.allclose(rec.t, [0.0, 0.1, 0.2])
    assert np.allclose(rec["Fuel rate"], [2.25, 2.5, 2.75]) and rec.unit("Fuel rate") == "l/h"


def test_a_nervus_csv_export_reads_back(tmp_path: Path) -> None:
    from nervus import Diagram, simulate
    from nervus.blocks import Scope, Sine

    d = Diagram()
    d.connect(d.add(Sine(), name="wave"), d.add(Scope()))
    res = simulate(d, 1.0, solver="rk4", step=0.01)
    res.to_csv(tmp_path / "run.csv")
    back = read_log(tmp_path / "run.csv")
    assert np.allclose(back.t, res.t) and np.allclose(back["wave"], res["wave"])


def test_no_header_and_errors(tmp_path: Path) -> None:
    rec = read_log(write(tmp_path, "plain.csv", "0,1\n1,3\n2,5\n"))
    assert rec.names() == ["column 2"] and rec["column 2"][-1] == 5.0
    with pytest.raises(NervusError, match="no table"):
        read_log(write(tmp_path, "text.csv", "hello\nworld\n"))
    with pytest.raises(NervusError, match="no value 'rpm'"):
        read_log(write(tmp_path, "plain2.csv", "0,1\n1,3\n"), ["rpm"])


def test_units_are_saved(tmp_path: Path) -> None:
    rec = read_log(write(tmp_path, "c.csv", CAR_SCANNER))
    assert SimResult.load(rec.save(tmp_path / "r.nervres")).unit("Engine RPM") == "rpm"


can = pytest.importorskip("can")


def test_candump_with_obd_answers(tmp_path: Path) -> None:
    # A candump log of a scan tool asking for engine speed and coolant temperature, the ECU answering.
    p = write(
        tmp_path,
        "obd.log",
        """\
(1700000000.000000) can0 7DF#02010C0000000000
(1700000000.010000) can0 7E8#04410C0C80AAAAAA
(1700000000.020000) can0 7DF#0201050000000000
(1700000000.030000) can0 7E8#0341055AAAAAAAAA
(1700000000.110000) can0 7E8#04410C1900AAAAAA
(1700000000.130000) can0 7E9#0341055BAAAAAAAA
""",
    )
    rec = read_log(p)
    assert set(rec.names()) == {"rpm", "coolant_temp", "coolant_temp@7E9"}
    assert rec["rpm"][0] == 800.0 and rec["rpm"][-1] == 1600.0 and rec.unit("rpm") == "rpm"
    assert rec["coolant_temp"][0] == 50.0


def test_j1939_asc_with_a_dbc(tmp_path: Path) -> None:
    pytest.importorskip("cantools")
    dbc = write(
        tmp_path,
        "j1939.dbc",
        """\
VERSION ""
BS_:
BU_: Engine
BO_ 2364540158 EEC1: 8 Engine
 SG_ EngineSpeed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX
BO_ 2566843904 ET1: 8 Engine
 SG_ EngineCoolantTemp : 0|8@1+ (1,-40) [-40|210] "degC" Vector__XXX
""",
    )
    # The DBC has EEC1 from source address 0xFE; the log has it from 0x00 and 0x01: matched by PGN.
    p = write(
        tmp_path,
        "truck.asc",
        """\
date Thu Jan 1 00:00:00.000 am 1970
base hex  timestamps absolute
no internal events logged
   0.000000 1  CF00400x       Rx   d 8 FF FF FF 00 19 FF FF FF
   0.010000 1  CF00401x       Rx   d 8 FF FF FF 40 1F FF FF FF
   0.020000 1  18FEEE00x      Rx   d 8 5A FF FF FF FF FF FF FF
   0.030000 1  123            Rx   d 2 01 02
""",
    )
    rec = read_log(p, dbc=dbc)
    assert set(rec.names()) == {"EngineSpeed", "EngineCoolantTemp"}
    assert rec["EngineSpeed"][0] == 800.0 and rec["EngineSpeed"][-1] == 1000.0 and rec.unit("EngineSpeed") == "rpm"
    assert rec["EngineCoolantTemp"][0] == 50.0
    with pytest.raises(NervusError, match="no OBD-II answers"):
        read_log(p)
