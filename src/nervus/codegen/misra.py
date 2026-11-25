"""Check generated code against MISRA C:2012 with cppcheck's MISRA addon.

MISRA C is the coding standard for safety-related C in the automotive industry (ISO 26262 asks for a coding
standard, and most projects pick MISRA). Nervus generates code that follows it; this module runs the free checker
cppcheck (``brew install cppcheck``, ``apt install cppcheck``) on it:

    from nervus.codegen import generate_c
    from nervus.codegen.misra import misra_check
    findings = misra_check(generate_c(model.block("ECU")))

Only the ECU files (``<name>.c`` and ``<name>.h``) are checked; ``main_example.c`` is analysed with them so the calls
of the step function count, but its own findings are not reported (it is an example, not ECU code). The
:data:`DEVIATIONS` are the rules the generated code does not follow on purpose, each with its reason, as a MISRA
compliance report records them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nervus.codegen.generator import GeneratedCode

DEVIATIONS: dict[str, str] = {
    "2.5": "advisory: <MODEL>_BASE_PERIOD is part of the interface, for the scheduler that calls the step function",
    "8.7": "advisory: <model>_P has external linkage so the calibration tool (XCP, A2L) and other ECU code can "
    "reach the calibration parameters by address",
    "7.3": "a false positive of cppcheck's addon, which takes the last letter of identifiers such as 'pedal' for a "
    "lowercase 'l' suffix; the generated code writes all integer suffixes as 'u'",
}

_LINE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\n]+):(?P<line>\d+):(?P<col>\d+): \w+: .*?\[misra-c2012-(?P<rule>[\d.]+)\]$", re.M
)


@dataclass(frozen=True)
class MisraFinding:
    file: str
    line: int
    rule: str
    source: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: rule {self.rule}: {self.source.strip()}"


def cppcheck_path() -> str | None:
    return shutil.which("cppcheck")


def _python() -> str:
    # The addon is a Python script; without an explicit interpreter cppcheck may skip it without a message.
    return shutil.which("python3") or sys.executable


def misra_check(code: GeneratedCode, deviations: dict[str, str] | None = None) -> list[MisraFinding]:
    """MISRA C:2012 findings in the generated code, except the ``deviations`` (default :data:`DEVIATIONS`)."""
    exe = cppcheck_path()
    if exe is None:
        raise RuntimeError("the MISRA check needs cppcheck: brew install cppcheck, or apt install cppcheck")
    skip = DEVIATIONS if deviations is None else deviations
    ecu_files = set(code.c_files)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for fname in (*ecu_files, "main_example.c"):
            if fname in code.files:
                (folder / fname).write_text(code.files[fname], encoding="utf-8")
        cmd = [exe, "-q", "--addon=misra", f"--addon-python={_python()}", "--enable=style", "--std=c99",
               *[f"--suppress=misra-c2012-{r}" for r in skip],
               *sorted(str(p) for p in folder.glob("*.c"))]  # fmt: skip
        run = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=folder)
        sources = {p.name: p.read_text(encoding="utf-8").splitlines() for p in folder.iterdir()}
    found = []
    for m in _LINE.finditer(run.stderr):
        name = Path(m.group("file")).name
        if name not in ecu_files:
            continue
        line = int(m.group("line"))
        text = sources.get(name, [])
        found.append(MisraFinding(name, line, m.group("rule"), text[line - 1] if 0 < line <= len(text) else ""))
    if run.returncode != 0 and not found:
        raise RuntimeError(f"cppcheck failed:\n{run.stderr}")
    return sorted(set(found), key=lambda f: (f.file, f.line, f.rule))
