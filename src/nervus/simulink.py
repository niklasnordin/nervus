"""Import a Simulink model (``.slx``) as a Nervus model.

    from nervus.simulink import read_slx
    model = read_slx("controller.slx")
    print(model.meta["simulink_report"])   # what was not imported

An ``.slx`` file is a zip archive of XML files: ``simulink/systems/system_root.xml`` is the top level, and every
subsystem refers to its own ``system_<n>.xml``. Each ``<Block>`` has a ``BlockType``, a ``Name``, an ``SID`` and
``<P Name="...">`` parameters (parameters at their default value are left out); each ``<Line>`` goes from
``<SID>#out:<port>`` to ``<SID>#in:<port>``, with ``<Branch>`` elements where it fans out.

The common blocks are translated (sources, math, continuous and discrete, logic, routing, lookup tables, PID
controllers, ports and subsystems, with masks). MATLAB expressions in parameters are kept with their workspace
variables (``K``, ``2*pi``, ``[1 2 3]``; ``^`` becomes ``**`` and ``0:0.5:2`` a list). A block that cannot be
translated becomes an empty subsystem with the same ports, named after the Simulink block, so the wiring stays; the
import report lists those blocks and the workspace variables the model uses (added with the value 1: give them their
values). Older ``.mdl`` files and Stateflow charts are not read.
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from nervus.blocks import (
    PID,
    Abs,
    Bias,
    Clock,
    CompareToConstant,
    Constant,
    DataTypeConversion,
    DeadZone,
    Demux,
    DiscreteIntegrator,
    DiscretePID,
    DiscreteTransferFunction,
    Gain,
    Ground,
    Inport,
    Integrator,
    LogicalOperator,
    Lookup1D,
    Lookup2D,
    MinMax,
    Mux,
    Outport,
    Product,
    Quantizer,
    Ramp,
    RateLimiter,
    RelationalOperator,
    Relay,
    Saturation,
    Scope,
    Sine,
    StateSpace,
    Step,
    Subsystem,
    Sum,
    Switch,
    Terminator,
    ToWorkspace,
    TransferFunction,
    UnitDelay,
    ZeroOrderHold,
)
from nervus.core import Diagram, Variable
from nervus.core.block import Block
from nervus.core.errors import NervusError
from nervus.core.expressions import parse_value

_KNOWN_NAMES = {"pi", "inf", "Inf", "eps", "e", "sqrt", "exp", "log", "sin", "cos", "tan", "abs", "min", "max",
                "true", "false", "floor", "ceil", "round", "nan", "NaN"}  # fmt: skip


@dataclass
class ImportReport:
    blocks: int = 0
    unsupported: list[str] = field(default_factory=list)
    """``path (BlockType)`` of the blocks that became empty placeholders."""
    variables: list[str] = field(default_factory=list)
    """Workspace variables the model uses (added with the value 1)."""
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"Imported {self.blocks} blocks."]
        if self.unsupported:
            lines.append(f"Not supported, imported as empty subsystems ({len(self.unsupported)}): "
                         + ", ".join(self.unsupported))  # fmt: skip
        if self.variables:
            lines.append("Workspace variables, set to 1 (give them their values in the Model Workspace): "
                         + ", ".join(self.variables))  # fmt: skip
        lines += self.notes
        return "\n".join(lines)


# ---------------------------------------------------------------------------- MATLAB expressions


def matlab_expr(text: str) -> str:
    """A MATLAB parameter expression in Nervus's syntax: ``^`` → ``**``, element-wise operators, ranges, ``~=``."""
    s = text.strip()
    s = s.replace(".^", "**").replace(".*", "*").replace("./", "/").replace("^", "**").replace("~=", "!=")

    def expand(m: re.Match[str]) -> str:
        parts = [float(x) for x in m.group(0).split(":")]
        start, step, stop = (parts[0], 1.0, parts[1]) if len(parts) == 2 else parts
        if step == 0 or (stop - start) / step > 10000:
            return m.group(0)
        n = int((stop - start) / step + 1e-9) + 1
        return " ".join(f"{start + k * step:.15g}" for k in range(max(n, 0)))  # all digits, no rounding noise

    number = r"-?\d+(?:\.\d*)?(?:e[-+]?\d+)?"
    return re.sub(rf"{number}:{number}(?::{number})?", expand, s)


def _names(expr: str) -> set[str]:
    return {n for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr) if n not in _KNOWN_NAMES} - {"e"}


# ---------------------------------------------------------------------------- reading the archive


class _Archive:
    def __init__(self, path: Path) -> None:
        try:
            self.zip = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise NervusError(
                f"{path.name} is not an .slx file (an .slx is a zip archive; .mdl files are not read)"
            ) from exc
        self.names = set(self.zip.namelist())
        self.open_refs: set[str] = set()
        """The systems being read (a subsystem that contains itself would never end)."""

    def system(self, ref: str) -> ET.Element:
        name = f"simulink/systems/{ref}.xml"
        if name not in self.names:
            raise NervusError(f"the .slx file has no {name}")
        try:
            return ET.fromstring(self.zip.read(name))
        except (ET.ParseError, zipfile.BadZipFile, OSError) as exc:
            raise NervusError(f"cannot read {name} in the .slx file: {exc}") from exc


def _params(el: ET.Element) -> dict[str, str]:
    return {p.get("Name", ""): (p.text or "") for p in el.findall("P")}


def _mask(el: ET.Element) -> dict[str, str]:
    """Mask parameters of a masked subsystem: name -> value expression."""
    out: dict[str, str] = {}
    mask = el.find("Mask")
    if mask is None:
        return out
    for mp in mask.findall("MaskParameter"):
        name = mp.get("Name", "")
        value = mp.findtext("Value") or ""
        if name:
            out[name] = value
    return out


def _ports(params: dict[str, str]) -> tuple[int, int]:
    nums = [int(x) for x in re.findall(r"\d+", params.get("Ports", "[1, 1]"))]
    return (nums[0] if nums else 1), (nums[1] if len(nums) > 1 else 1)


# ---------------------------------------------------------------------------- blocks


def _count(spec: str) -> int:
    """A number of ports written as ``3`` or as a list of widths ``[1, 2]``."""
    spec = spec.strip()
    return int(spec) if spec.isdigit() else max(1, len([x for x in spec.strip("[]").split(",") if x.strip()]))


def _signs(spec: str, plus: str) -> str:
    spec = spec.strip() or "2"
    if spec.isdigit():
        return plus * int(spec)
    return spec.replace("|", "")


def _make(btype: str, p: dict[str, str], mask_source: str) -> Block | None:
    """The Nervus block for a Simulink block, or ``None`` if there is none."""
    g = p.get
    e = matlab_expr
    if btype == "Reference":  # a library block, e.g. the PID controller
        source = mask_source
        if "PID Controller" in source:
            kw = {"P": e(g("P", "1")), "I": e(g("I", "1")), "D": e(g("D", "0")), "N": e(g("N", "100"))}
            if "Discrete" in source or g("TimeDomain", "") == "Discrete-time":
                return DiscretePID(sample_time=e(g("SampleTime", "-1")), **kw)
            return PID(**kw)
        return None
    if btype == "Gain":
        return Gain(gain=e(g("Gain", "1")))
    if btype == "Sum":
        return Sum(signs=_signs(g("Inputs", "++"), "+"))
    if btype == "Product":
        return Product(ops=_signs(g("Inputs", "2"), "*"))
    if btype == "Constant":
        return Constant(value=e(g("Value", "1")))
    if btype == "Step":
        return Step(time=e(g("Time", "1")), initial=e(g("Before", "0")), final=e(g("After", "1")))
    if btype == "Sin":
        return Sine(amplitude=e(g("Amplitude", "1")), frequency=e(g("Frequency", "1")),
                    phase=e(g("Phase", "0")), bias=e(g("Bias", "0")))  # fmt: skip
    if btype == "Ramp":
        return Ramp(slope=e(g("slope", "1")), start_time=e(g("start", "0")), initial=e(g("InitialOutput", "0")))
    if btype == "Clock":
        return Clock()
    if btype == "Ground":
        return Ground()
    if btype == "Integrator":
        return Integrator(initial=e(g("InitialCondition", "0")), upper=e(g("UpperSaturationLimit", "inf")),
                          lower=e(g("LowerSaturationLimit", "-inf")))  # fmt: skip
    if btype == "TransferFcn":
        return TransferFunction(numerator=e(g("Numerator", "[1]")), denominator=e(g("Denominator", "[1 1]")))
    if btype == "StateSpace":
        return StateSpace(A=e(g("A", "1")), B=e(g("B", "1")), C=e(g("C", "1")), D=e(g("D", "1")))
    if btype in ("UnitDelay", "Memory"):
        return UnitDelay(initial=e(g("InitialCondition", g("X0", "0"))), sample_time=e(g("SampleTime", "-1")))
    if btype == "ZeroOrderHold":
        return ZeroOrderHold(sample_time=e(g("SampleTime", "1")))
    if btype == "DiscreteIntegrator":
        return DiscreteIntegrator(gain=e(g("gainval", "1")), initial=e(g("InitialCondition", "0")),
                                  sample_time=e(g("SampleTime", "1")),
                                  method="backward_euler" if "Backward" in g("IntegratorMethod", "") else "forward_euler")  # fmt: skip
    if btype == "DiscreteTransferFcn":
        return DiscreteTransferFunction(numerator=e(g("Numerator", "[1]")), denominator=e(g("Denominator", "[1 0.5]")),
                                        sample_time=e(g("SampleTime", "1")))  # fmt: skip
    if btype == "Saturate":
        return Saturation(upper=e(g("UpperLimit", "0.5")), lower=e(g("LowerLimit", "-0.5")))
    if btype == "DeadZone":
        return DeadZone(start=e(g("LowerValue", "-0.5")), end=e(g("UpperValue", "0.5")))
    if btype == "RateLimiter":
        return RateLimiter(rising_rate=e(g("RisingSlewLimit", "1")), falling_rate=e(g("FallingSlewLimit", "-1")))
    if btype == "Relay":
        return Relay(on_point=e(g("OnSwitchValue", "eps")), off_point=e(g("OffSwitchValue", "eps")),
                     on_value=e(g("OnOutputValue", "1")), off_value=e(g("OffOutputValue", "0")))  # fmt: skip
    if btype == "Quantizer":
        return Quantizer(interval=e(g("QuantizationInterval", "0.5")))
    if btype == "Abs":
        return Abs()
    if btype == "Bias":
        return Bias(bias=e(g("Bias", "0")))
    if btype == "MinMax":
        return MinMax(function=g("Function", "min"), num_inputs=_count(g("Inputs", "1")))
    if btype == "RelationalOperator":
        return RelationalOperator(operator=g("Operator", ">=").replace("~=", "!="))
    if btype == "Logic":
        op = g("Operator", "AND").upper()
        return LogicalOperator(operator=op, num_inputs=1 if op == "NOT" else _count(g("Inputs", "2")))
    if btype == "Switch":
        crit = g("Criteria", "u2 >= Threshold")
        criterion = (
            "u1 != 0" if "~=" in crit else "u1 > threshold" if ">" in crit and ">=" not in crit else "u1 >= threshold"
        )
        return Switch(criterion=criterion, threshold=e(g("Threshold", "0")))
    if btype == "Mux":
        return Mux(num_inputs=_count(g("Inputs", "2")))
    if btype == "Demux":
        return Demux(num_outputs=_count(g("Outputs", "2")))
    if btype == "Lookup_n-D":
        dims = g("NumberOfTableDimensions", "2")
        if dims == "1":
            return Lookup1D(breakpoints=e(g("BreakpointsForDimension1", "[1:3]")), table=e(g("Table", "[4 5 6]")))
        if dims == "2":
            return Lookup2D(row_breakpoints=e(g("BreakpointsForDimension1", "[1:3]")),
                            column_breakpoints=e(g("BreakpointsForDimension2", "[1:3]")),
                            table=e(g("Table", "[4 5 6; 16 19 20; 10 18 23]")))  # fmt: skip
        return None
    if btype == "Lookup":  # the old 1-D lookup table
        return Lookup1D(breakpoints=e(g("InputValues", "[-5:5]")), table=e(g("Table", "tanh([-5:5])")))
    if btype == "DataTypeConversion":
        dtype = g("OutDataTypeStr", "Inherit: auto").strip()
        return DataTypeConversion(output_type="inherit" if dtype.startswith("Inherit") else dtype)
    if btype == "Scope":
        return Scope(num_inputs=int(g("NumInputPorts", "1") or 1))
    if btype == "Terminator":
        return Terminator()
    if btype == "ToWorkspace":
        return ToWorkspace(variable=g("VariableName", "simout"))
    if btype == "CompareToConstant":
        return CompareToConstant(operator=g("relop", "<=").replace("~=", "!="), constant=e(g("const", "3")))
    return None


def _port_number(p: dict[str, str], where: str) -> int:
    text = p.get("Port", "1").strip()
    if not text.isdigit() or int(text) < 1:
        raise NervusError(f"{where}: port number {text!r} is not a positive integer; the .slx file is damaged")
    return int(text)


def _not_imported(btype: str, p: dict[str, str]) -> list[str]:
    """Settings of a translated block that the Nervus block does not have: notes for the report (the block keeps
    the Nervus behaviour, which may differ)."""
    notes = []
    if btype == "DiscreteIntegrator":
        method = p.get("IntegratorMethod", "")
        if method not in ("", "Integration: Forward Euler", "Integration: Backward Euler"):
            notes.append(f"integrator method {method!r} not imported: it integrates with Euler and the sample time "
                         "(Integration: Forward/Backward Euler); check the block")  # fmt: skip
        if p.get("LimitOutput", "off") == "on":
            notes.append("output limits not imported; set the block's upper and lower limits")
        if p.get("ExternalReset", "none") != "none":
            notes.append(f"external reset {p['ExternalReset']!r} not imported")
    if btype == "Lookup_n-D":
        extrap = p.get("ExtrapMethod", "")
        if extrap != "Clip":
            notes.append(f"extrapolation {extrap or '(Simulink default)'!r} not imported: the Nervus table clips at "
                         "its ends; check the block's extrapolation")  # fmt: skip
        interp = p.get("InterpMethod", "")
        if interp not in ("", "Linear", "Linear point-slope"):
            notes.append(f"interpolation {interp!r} not imported: the Nervus table interpolates linearly")
    if btype == "Lookup" and p.get("LookUpMeth", "") != "Interpolation-Use End Values":
        method = p.get("LookUpMeth", "") or "(Simulink default)"
        notes.append(f"lookup method {method!r} not imported: the Nervus table interpolates linearly and clips at "
                     "its ends; check the block")  # fmt: skip
    return notes


def _placeholder(n_in: int, n_out: int, btype: str) -> Subsystem:
    """An empty subsystem with the block's ports, for a block that is not translated (its outputs are 0)."""
    inner = Diagram(btype)
    for k in range(n_in):
        inner.connect(inner.add(Inport(port=k + 1), name=f"in{k + 1}"), inner.add(Terminator(), name=f"unused{k + 1}"))
    for k in range(n_out):
        inner.connect(inner.add(Ground(), name=f"zero{k + 1}"), inner.add(Outport(port=k + 1), name=f"out{k + 1}"))
    sub = Subsystem(system=inner)
    sub.mask_icon = f"Simulink\\n{btype}"
    sub.mask_description = f"A Simulink {btype} block: not translated by the import (its outputs are 0)."
    return sub


# ---------------------------------------------------------------------------- systems


def _parameters_used(block: Block) -> set[str]:
    names: set[str] = set()
    for value in block.expressions.values():
        names |= _names(str(value))
    return names


def _build(archive: _Archive, system: ET.Element, d: Diagram, path: str, report: ImportReport,
           used: set[str]) -> None:  # fmt: skip
    by_sid: dict[str, Block] = {}
    for el in system.findall("Block"):
        btype, name, sid = el.get("BlockType", ""), el.get("Name", ""), el.get("SID", "")
        p = _params(el)
        block: Block | None
        if btype in ("EnablePort", "TriggerPort", "ActionPort"):
            report.notes.append(f"{path}{name}: {btype} — the subsystem's enable/trigger is not translated")
            continue
        if btype == "Inport":
            block = Inport(port=_port_number(p, f"{path}{name}"))
        elif btype == "Outport":
            block = Outport(port=_port_number(p, f"{path}{name}"))
        elif btype == "SubSystem":
            ref = el.find("System")
            if ref is None or ref.get("Ref") is None:
                block = _placeholder(*_ports(p), "SubSystem")
                report.unsupported.append(f"{path}{name} (SubSystem without contents)")
            else:
                inner = Diagram(name)
                sub = Subsystem(system=inner)
                for k, v in _mask(el).items():
                    expr = matlab_expr(v)
                    try:
                        sub.mask.add(Variable(k, float(parse_value(expr))))
                    except (ValueError, TypeError, NervusError):
                        sub.mask.add(Variable(k, 1.0, f"from Simulink: {v}"))
                        used |= _names(expr)
                block = sub
                ref_name = str(ref.get("Ref"))
                if ref_name in archive.open_refs:
                    raise NervusError(
                        f"{path}{name}: the subsystem contains itself ({ref_name}); the .slx file is damaged"
                    )
                archive.open_refs.add(ref_name)
                _build(archive, archive.system(ref_name), inner, f"{path}{name}/", report, used)
                archive.open_refs.discard(ref_name)
                used -= set(sub.mask)  # the mask's own parameters
        else:
            try:
                block = _make(btype, p, p.get("SourceBlock", ""))
            except (ValueError, NervusError) as exc:
                report.notes.append(f"{path}{name}: {exc}")
                block = None
            if block is None:
                block = _placeholder(
                    *_ports(p), p.get("SourceBlock", btype).split("/")[-1] if btype == "Reference" else btype
                )
                report.unsupported.append(f"{path}{name} ({p.get('SourceBlock', btype)})")
            else:
                used |= _parameters_used(block)
                report.notes += [f"{path}{name}: {note}" for note in _not_imported(btype, p)]
        report.blocks += 1
        pos = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", p.get("Position", ""))]
        if len(pos) == 4:
            block.meta["position"] = [math.floor(pos[0] / 10 + 0.5) * 10.0, math.floor(pos[1] / 10 + 0.5) * 10.0]
        if p.get("Orientation", "right") == "left":
            block.meta["flipped"] = True
        d.add(block, name=_unique(d, name))
        by_sid[sid] = block
    for line in system.findall("Line"):
        _connect(d, by_sid, line, None, path, report)


def _unique(d: Diagram, name: str) -> str:
    base = re.sub(r"\s+", "_", name.strip()) or "block"
    base = base.replace("/", "_")
    out, k = base, 1
    names = {b.name for b in d.blocks}
    while out in names:
        k += 1
        out = f"{base}_{k}"
    return out


def _end(text: str) -> tuple[str, str, int] | None:
    m = re.match(r"^(\d+(?::\d+)*)#(in|out|enable|trigger):?(\d*)$", text.strip())
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3) or 1)


def _connect(d: Diagram, by_sid: dict[str, Block], line: ET.Element, src: tuple[Block, int] | None, path: str,
             report: ImportReport) -> None:  # fmt: skip
    p = _params(line)
    if "Src" in p:
        end = _end(p["Src"])
        if end is None or end[0] not in by_sid:
            return
        src = (by_sid[end[0]], end[2] - 1)
    if src is None:
        return
    if "Dst" in p:
        end = _end(p["Dst"])
        if end is not None and end[0] in by_sid and end[1] == "in":
            try:
                d.connect(src, (by_sid[end[0]], end[2] - 1))
            except (IndexError, NervusError) as exc:
                report.notes.append(f"{path}: line {p.get('Src', '')} → {p['Dst']} not connected ({exc})")
    for branch in line.findall("Branch"):
        _connect(d, by_sid, branch, src, path, report)


def read_slx(path: str | Path) -> Diagram:
    """Read a Simulink ``.slx`` model. The import report is in ``model.meta["simulink_report"]``."""
    p = Path(path)
    archive = _Archive(p)
    root = archive.system("system_root")
    d = Diagram(re.sub(r"\W", "_", p.stem))
    report = ImportReport()
    used: set[str] = set()
    _build(archive, root, d, "", report, used)
    for name in sorted(used):
        if name not in d.workspace:
            d.workspace.add(Variable(name, 1.0, "from the Simulink model: give it its value"))
            report.variables.append(name)
    d.meta["simulink_report"] = str(report)
    d.meta["simulink_unsupported"] = list(report.unsupported)
    return d
