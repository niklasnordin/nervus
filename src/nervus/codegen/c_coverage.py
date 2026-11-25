"""Coverage of the generated C code: which lines and branches the test cases execute (gcov).

Model coverage (:mod:`nervus.coverage`) measures the model; ECU projects also measure the code that runs on the ECU.
:func:`coverage_of_tests` simulates every test case of the model, records the signals that enter the code (the In blocks
of the model, or of the subsystem the code is generated for) at every step of the code, and replays them into the
generated C, compiled with ``--coverage``. gcov then counts each line and branch::

    from nervus.codegen.c_coverage import coverage_of_tests
    cov = coverage_of_tests(model, model.block("ECU"))
    print(cov.summary())             # Lines 94 % (47/50), branches 75 % (12/16) in 3 test case(s)
    cov.save_html("ecu_coverage.html")

A test case's parameter overrides apply to the code as well (its calibration is generated into it). Needs a C
compiler and gcov (part of gcc; on macOS ``gcov`` comes with the Xcode command line tools).
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nervus.codegen.generator import CodegenError, GeneratedCode, generate_c
from nervus.codegen.sil import _input_table, find_compiler
from nervus.core.block import Block
from nervus.core.diagram import Diagram
from nervus.core.workspace import ParameterSet
from nervus.sim.result import SimResult


@dataclass
class FileCoverage:
    name: str
    source: list[str]
    hits: dict[int, int] = field(default_factory=dict)
    """Executable line number -> times executed."""
    branches: dict[tuple[int, int], int] = field(default_factory=dict)
    """(line, branch index) -> times taken."""

    def merge(self, other: FileCoverage) -> None:
        for k, v in other.hits.items():
            self.hits[k] = self.hits.get(k, 0) + v
        for kb, vb in other.branches.items():
            self.branches[kb] = self.branches.get(kb, 0) + vb


@dataclass
class CCoverage:
    files: dict[str, FileCoverage] = field(default_factory=dict)
    runs: int = 0

    def _count(self, what: str) -> tuple[int, int]:
        items = [v for f in self.files.values() for v in getattr(f, what).values()]
        return sum(1 for v in items if v > 0), len(items)

    @property
    def lines(self) -> tuple[int, int]:
        """(executed, executable) lines."""
        return self._count("hits")

    @property
    def branch_counts(self) -> tuple[int, int]:
        """(taken, all) branches."""
        return self._count("branches")

    @staticmethod
    def _pct(done: int, total: int) -> float:
        return 100.0 * done / total if total else 100.0

    @property
    def line_percent(self) -> float:
        return self._pct(*self.lines)

    @property
    def branch_percent(self) -> float:
        return self._pct(*self.branch_counts)

    def missed_lines(self) -> list[tuple[str, int, str]]:
        """(file, line, source) of every executable line that never ran."""
        return [(f.name, n, f.source[n - 1].strip() if n <= len(f.source) else "")
                for f in self.files.values() for n, c in sorted(f.hits.items()) if c == 0]  # fmt: skip

    def missed_branches(self) -> list[tuple[str, int, str, int]]:
        """(file, line, source, number of branches never taken) for every line with a branch that never ran."""
        out = []
        for f in self.files.values():
            per_line: dict[int, int] = {}
            for (line, _b), v in f.branches.items():
                if v == 0:
                    per_line[line] = per_line.get(line, 0) + 1
            for line, n in sorted(per_line.items()):
                out.append((f.name, line, f.source[line - 1].strip() if line <= len(f.source) else "", n))
        return out

    def summary(self) -> str:
        (lh, lt), (bh, bt) = self.lines, self.branch_counts
        lines = [f"C code coverage: lines {self.line_percent:.0f} % ({lh}/{lt}), branches "
                 f"{self.branch_percent:.0f} % ({bh}/{bt}) in {self.runs} run(s)"]  # fmt: skip
        lines += [f"  {f}:{n}: never ran: {src}" for f, n, src in self.missed_lines()]
        lines += [f"  {f}:{n}: {k} branch(es) never taken: {src}" for f, n, src, k in self.missed_branches()]
        return "\n".join(lines)

    def to_html(self, title: str = "C code coverage") -> str:
        parts = []
        for f in self.files.values():
            rows = []
            for n, src in enumerate(f.source, start=1):
                hits = f.hits.get(n)
                taken = [v for (ln, _b), v in f.branches.items() if ln == n]
                cls = "" if hits is None else ("hit" if hits > 0 else "miss")
                if cls == "hit" and taken and min(taken) == 0:
                    cls = "part"  # the line ran, but a branch of it never did
                count = "" if hits is None else str(hits)
                br = f" {sum(1 for v in taken if v)}/{len(taken)}" if taken else ""
                rows.append(f'<tr class="{cls}"><td class="n">{n}</td><td class="n">{count}</td>'
                            f'<td class="n">{br}</td><td><code>{html.escape(src)}</code></td></tr>')  # fmt: skip
            parts.append(f"<h2>{html.escape(f.name)}</h2><table>{''.join(rows)}</table>")
        (lh, lt), (bh, bt) = self.lines, self.branch_counts
        css = (
            "body{font-family:system-ui,sans-serif;margin:24px;color:#1d1d1b;background:#fcfcfb}"
            "table{border-collapse:collapse;font-size:12px}td{padding:0 8px;white-space:pre}"
            ".n{text-align:right;color:#86857f}.hit{background:#1baf7a18}.miss{background:#e3494833}"
            ".part{background:#eda10033}code{font-family:ui-monospace,monospace}"
            "@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#f2f2ef}}"
        )
        return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
                f"<style>{css}</style></head><body><h1>{html.escape(title)}</h1>"
                f"<p><b>{self.line_percent:.0f} %</b> of the lines ({lh}/{lt}) and <b>{self.branch_percent:.0f} %"
                f"</b> of the branches ({bh}/{bt}) ran in {self.runs} run(s). Green: ran; red: never ran; yellow: "
                "ran, but one of its branches never did. Columns: line, times executed, branches taken.</p>"
                + "".join(parts) + "</body></html>")  # fmt: skip

    def save_html(self, path: str | Path, title: str = "C code coverage") -> Path:
        p = Path(path)
        p.write_text(self.to_html(title), encoding="utf-8")
        return p


# ---------------------------------------------------------------------------- running the code


def gcov_path() -> str | None:
    return shutil.which("gcov")


def _runner(code: GeneratedCode, steps: int) -> str:
    widths = [w for _n, w in code.inputs]
    return f"""/* Steps the generated code with inputs from a file of doubles (written by Nervus), then exits. */
#include <stdio.h>
void nervus_sil_initialize(void);
void nervus_sil_step(void);
void nervus_sil_set_input(int k, const double *v);

int main(int argc, char **argv)
{{
    static const int widths[{max(1, len(widths))}] = {{{", ".join(map(str, widths)) or "0"}}};
    double v[64];
    long k;
    int j;
    FILE *f;
    if (argc < 2) {{ return 2; }}
    f = fopen(argv[1], "rb");
    if (f == NULL) {{ return 3; }}
    nervus_sil_initialize();
    for (k = 0; k < {steps}L; k++) {{
        for (j = 0; j < {len(widths)}; j++) {{
            if (fread(v, sizeof(double), (size_t)widths[j], f) != (size_t)widths[j]) {{ fclose(f); return 4; }}
            nervus_sil_set_input(j, v);
        }}
        nervus_sil_step();
    }}
    fclose(f);
    return 0;
}}
"""


def _parse_gcov(path: Path, name: str) -> FileCoverage:
    cov = FileCoverage(name, [])
    line_no = 0
    branch = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*([^:]+):\s*(\d+):(.*)$", raw)
        if m:
            count, line_no, src = m.group(1).strip(), int(m.group(2)), m.group(3)
            branch = 0
            if line_no == 0:
                continue
            while len(cov.source) < line_no:
                cov.source.append("")
            cov.source[line_no - 1] = src
            if count == "-":
                continue
            cov.hits[line_no] = 0 if count.startswith(("#####", "=====")) else int(count.rstrip("*").replace(",", ""))
            continue
        b = re.match(r"^branch\s+(\d+)\s+(taken\s+(\d+)|never executed)", raw.strip())
        if b and line_no:
            cov.branches[(line_no, branch)] = int(b.group(3)) if b.group(3) is not None else 0
            branch += 1
    return cov


def run_coverage(
    code: GeneratedCode, inputs: dict[str, Any], steps: int, folder: str | Path | None = None
) -> CCoverage:
    """Run ``code`` with ``inputs`` (as for :func:`nervus.codegen.sil.back_to_back`) under gcov."""
    cc, gcov = find_compiler(), gcov_path()
    if cc is None or gcov is None:
        raise CodegenError("C code coverage needs a C compiler and gcov")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(folder) if folder is not None else Path(tmp)
        out.mkdir(parents=True, exist_ok=True)
        code.write(out)
        (out / "nervus_cov_main.c").write_text(_runner(code, steps), encoding="utf-8")
        sources = [f"{code.name}.c", f"{code.name}_sil.c", *[f for f in code.extra_sources if f.endswith(".c")],
                   "nervus_cov_main.c"]  # fmt: skip
        for src in sources:
            run = subprocess.run([cc, "-std=c99", "-O0", "--coverage", "-c", src, "-o", f"{Path(src).stem}.o"],
                                 cwd=out, capture_output=True, text=True, check=False)  # fmt: skip
            if run.returncode != 0:
                raise CodegenError(f"the code does not compile for coverage:\n{run.stderr}")
        objs = [f"{Path(s).stem}.o" for s in sources]
        run = subprocess.run([cc, "--coverage", "-o", "nervus_cov", *objs, "-lm"], cwd=out, capture_output=True,
                             text=True, check=False)  # fmt: skip
        if run.returncode != 0:
            raise CodegenError(f"linking for coverage failed:\n{run.stderr}")
        tables = _input_table(code, inputs, steps)
        data = np.concatenate([t.reshape(steps, -1) for t in tables], axis=1) if tables else np.zeros((steps, 0))
        data.astype("<f8").tofile(out / "inputs.bin")
        ran = subprocess.run([str(out / "nervus_cov"), "inputs.bin"], cwd=out, capture_output=True, check=False)
        if ran.returncode != 0:
            raise CodegenError(f"the coverage run failed (exit code {ran.returncode})")
        ecu = [f"{code.name}.c", *[f for f in code.extra_sources if f.endswith(".c")]]
        run = subprocess.run([gcov, "-b", "-c", *ecu], cwd=out, capture_output=True, text=True, check=False)
        result = CCoverage(runs=1)
        for src in ecu:
            report = out / f"{src}.gcov"
            if not report.exists():
                raise CodegenError(f"gcov wrote no report for {src}:\n{run.stdout}{run.stderr}")
            result.files[src] = _parse_gcov(report, src)
    return result


# ---------------------------------------------------------------------------- from the test cases


def _code_inputs(diagram: Diagram, target: Diagram | Block, res: SimResult, code: GeneratedCode,
                 steps: int) -> dict[str, Any]:  # fmt: skip
    """The signals entering the code, sampled at its steps."""
    from nervus.blocks.ports import Inport

    times = np.arange(steps) * code.base_period
    if isinstance(target, Block):
        paths = {id(b): path for path, b in diagram.walk()}
        parent = target.diagram
        if parent is None:
            raise CodegenError(f"{target.name} is not in a model")
        signals = []
        for port in range(target.num_inputs()):
            conn = next((c for c in parent.connections if c.dst is target and c.dst_port == port), None)
            if conn is None:
                raise CodegenError(f"input {port + 1} of {target.name} is not connected")
            signals.append(res.signal(paths[id(conn.src)], conn.src_port))
    else:
        ins = sorted((b for b in diagram.blocks if isinstance(b, Inport)), key=lambda b: int(b.params["port"]))
        signals = [res[b.name] for b in ins]
    out = {}
    for (name, width), sig in zip(code.inputs, signals, strict=True):
        arr = np.asarray(sig, dtype=float).reshape(len(res.t), -1)
        out[name] = (
            np.column_stack([np.interp(times, res.t, arr[:, k]) for k in range(arr.shape[1])]).reshape(steps, -1)[
                :, :width
            ]
            if arr.shape[1] >= width
            else np.repeat(np.interp(times, res.t, arr[:, 0])[:, None], width, 1)
        )
    return out


def coverage_of_tests(diagram: Diagram, target: Diagram | Block | None = None, integer_only: bool = False,
                  tasks: bool = False) -> CCoverage:  # fmt: skip
    """Line and branch coverage of the code generated for ``target`` (default: the whole model), when the
    signals of every test case of ``diagram`` are replayed into it."""
    from nervus.testing import run_test

    target = diagram if target is None else target
    if not diagram.tests:
        raise CodegenError("the model has no test cases to measure the code with")
    total = CCoverage()
    ws = diagram.workspace
    source = getattr(diagram.root, "source_path", None)
    base = Path(source).parent if source else None
    for case in diagram.tests:
        result = run_test(diagram, case, base)
        if result.result is None:
            raise CodegenError(f"test case {case.name!r} could not run: {result.error}")
        params: dict[str, Any] = {}
        if case.parameter_set:  # the test's calibration goes into the code: its parameter set, then its overrides
            ps_path = Path(case.parameter_set)
            if base is not None and not ps_path.is_absolute():
                ps_path = base / ps_path
            params.update(ParameterSet.load(ps_path).values)
        params.update(case.parameters)
        saved = {k: ws[k] for k in params if k in ws}
        try:
            for k, v in params.items():
                if k in ws:
                    ws[k] = v
            code = generate_c(target, integer_only=integer_only, tasks=tasks)
        finally:
            for k, v in saved.items():
                ws[k] = v
        stop = case.stop_time if case.stop_time is not None else diagram.settings.t_final  # the model's, if not set
        steps = int(np.floor(float(stop) / code.base_period + 1e-9)) + 1
        run = run_coverage(code, _code_inputs(diagram, target, result.result, code, steps), steps)
        for name, f in run.files.items():
            if name in total.files:
                total.files[name].merge(f)
            else:
                total.files[name] = f
        total.runs += 1
    return total
