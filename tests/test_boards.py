"""Board projects: the host board built with make, its C XCP slave driven by the Python XCP master."""

from __future__ import annotations

import ctypes
import shutil
from pathlib import Path

import pytest

from nervus import load
from nervus.codegen.boards import BOARDS, create_project, fill_a2l_addresses
from nervus.codegen.sil import find_compiler
from nervus.xcp import XcpError, XcpMaster

EXAMPLES = Path(__file__).parent.parent / "examples"


class HostCan:
    """An XCP transport into the host board: inject the request, run the main loop, take the answer."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib

    def request(self, frame: bytes, timeout: float) -> bytes | None:
        data = (ctypes.c_uint8 * 8)(*frame.ljust(8, b"\0"))
        self.lib.nv_host_inject(0x7F0, data, 8)
        self.lib.nv_loop()
        cid, out, n = ctypes.c_uint32(), (ctypes.c_uint8 * 8)(), ctypes.c_uint8()
        while self.lib.nv_host_sent(ctypes.byref(cid), out, ctypes.byref(n)):
            if cid.value == 0x7F1:
                return bytes(out[: n.value])
        return None


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_projects_have_their_files(tmp_path: Path, board: str) -> None:
    d = load(EXAMPLES / "throttle_ecu.nervus")
    out = create_project(d.block("ECU"), board, tmp_path / board, request_id=0x7E0, response_id=0x7E8)
    src = out / "main" if board == "esp32" else out
    for name in ("ECU.c", "ECU.h", "main.c", "board.c", "io.c", "nervus_xcp.c", "nervus_target.h"):
        assert (src / name).exists(), name
    a2l = (out / "ECU.a2l").read_text()
    assert "CAN_ID_MASTER 0x7E0" in a2l and "CAN_ID_SLAVE 0x7E8" in a2l
    (src / "io.c").write_text("/* mine */")
    create_project(d.block("ECU"), board, out)
    assert (src / "io.c").read_text() == "/* mine */"  # the user's file is kept


@pytest.mark.skipif(find_compiler() is None or shutil.which("make") is None, reason="needs cc and make")
def test_host_board_runs_and_answers_xcp(tmp_path: Path) -> None:
    import subprocess

    from nervus.codegen.a2l import symbol_addresses

    d = load(EXAMPLES / "throttle_ecu.nervus")
    out = create_project(d.block("ECU"), "host", tmp_path / "host")
    proc = subprocess.run(["make"], cwd=out, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    a2l = fill_a2l_addresses(out)
    lib = ctypes.CDLL(str(next(out.glob("*.so"))))
    lib.nv_setup()
    base = ctypes.addressof(ctypes.c_char.in_dll(lib, "ECU_P")) - symbol_addresses(next(out.glob("*.so")))["ECU_P"]
    lib.nv_xcp_set_base(ctypes.c_size_t(base))
    m = XcpMaster(HostCan(lib), a2l=a2l)
    m.connect()
    assert m.read("ECU_P.Kp") == 1.5
    m.write("ECU_P.Kp", 2.0)
    assert m.read("ECU_P.Kp") == 2.0
    with pytest.raises(XcpError, match="access denied"):
        m.write("ECU_B.error", 1.0)  # a signal: read only
    clock = ctypes.c_uint32.in_dll(lib, "nv_host_time_ms")
    period = round(1000 * d.block("ECU").sample_time().period) or 10
    steps = 0
    for _ in range(5):  # the main loop steps the model every base period
        clock.value += period
        lib.nv_loop()
        steps += 1
    assert steps == 5 and m.read("ECU_P.Kp") == 2.0
